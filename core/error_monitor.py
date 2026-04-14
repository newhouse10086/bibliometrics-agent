"""Error Monitor - Captures and detects errors from logs and subprocesses.

Ensures that ALL errors trigger the Guardian system, including:
- Exceptions that propagate to orchestrator
- Errors in multiprocessing workers
- Errors caught by libraries internally
- Import/dependency errors

This solves the problem where errors in tmtoolkit's multiprocessing workers
don't propagate to the main process but should still trigger Guardian intervention.
"""

import logging
import re
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class DetectedError:
    """An error detected from logs or exceptions."""

    timestamp: str
    source: str  # "exception", "log_stderr", "log_error"
    error_type: str
    error_message: str
    module_name: Optional[str] = None
    traceback: Optional[str] = None
    raw_log: str = ""


class ErrorMonitor:
    """Monitors logs and captures errors for Guardian intervention.

    Usage:
        monitor = ErrorMonitor()

        # Start capturing logs
        monitor.start()

        # ... run pipeline ...

        # Check for detected errors
        errors = monitor.get_detected_errors()

        # Stop monitoring
        monitor.stop()
    """

    # Error patterns to detect in logs
    ERROR_PATTERNS = [
        # Python exceptions
        (r"(\w+Error): (.+)", "exception"),
        (r"(\w+Exception): (.+)", "exception"),
        (r"Traceback \(most recent call last\):", "traceback_start"),

        # Import/dependency errors
        (r"ModuleNotFoundError: No module named '([^']+)'", "dependency_missing"),
        (r"ImportError: (.+)", "import_error"),
        (r"cannot import name '([^']+)'", "import_error"),

        # Multiprocessing errors
        (r"Process SpawnPoolWorker-\d+:", "multiprocessing"),
        (r"AttributeError: (.+)", "attribute_error"),

        # Library-specific errors
        (r"lda\.tmtoolkit failed: (.+)", "tmtoolkit_error"),
        (r"RuntimeError: (.+)", "runtime_error"),
        (r"ValueError: (.+)", "value_error"),
    ]

    def __init__(self, max_logs: int = 1000):
        """Initialize error monitor.

        Args:
            max_logs: Maximum number of log lines to retain
        """
        self.max_logs = max_logs
        self.log_buffer: deque = deque(maxlen=max_logs)
        self.detected_errors: list[DetectedError] = []
        self.lock = threading.Lock()
        self.handler: Optional[logging.Handler] = None
        self.original_stderr = None
        self._active = False

    def start(self):
        """Start monitoring logs and stderr."""
        if self._active:
            logger.warning("ErrorMonitor already active")
            return

        # Add custom log handler
        self.handler = logging.StreamHandler(self)
        self.handler.setLevel(logging.WARNING)
        self.handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
        logging.root.addHandler(self.handler)

        # Capture stderr
        self.original_stderr = sys.stderr
        sys.stderr = self

        self._active = True
        logger.info("ErrorMonitor started - capturing all errors for Guardian intervention")

    def stop(self):
        """Stop monitoring."""
        if not self._active:
            return

        # Remove log handler
        if self.handler:
            logging.root.removeHandler(self.handler)

        # Restore stderr
        if self.original_stderr:
            sys.stderr = self.original_stderr

        self._active = False
        logger.info(f"ErrorMonitor stopped - detected {len(self.detected_errors)} errors")

    def write(self, message: str):
        """Write to stderr (capture stderr output)."""
        # Forward to original stderr
        if self.original_stderr:
            self.original_stderr.write(message)

        # Process for errors
        if message.strip():
            self._process_log_line(message, source="stderr")

    def flush(self):
        """Flush stderr."""
        if self.original_stderr:
            self.original_stderr.flush()

    def emit(self, record: logging.LogRecord):
        """Handle log record from logging.StreamHandler."""
        try:
            message = self.handler.format(record)
            self._process_log_line(message, source=f"log:{record.levelname}")
        except Exception:
            pass

    def _process_log_line(self, line: str, source: str):
        """Process a log line and detect errors."""
        with self.lock:
            self.log_buffer.append({
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "line": line.strip()
            })

        # Check for error patterns
        for pattern, error_type in self.ERROR_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                detected = DetectedError(
                    timestamp=datetime.now().isoformat(),
                    source=source,
                    error_type=error_type,
                    error_message=match.group(0),
                    raw_log=line.strip(),
                )

                # Try to extract module name from log context
                module_match = re.search(r"modules\.(\w+)", line)
                if module_match:
                    detected.module_name = module_match.group(1)

                # Check if this is a new error (avoid duplicates)
                with self.lock:
                    # Don't add if same error already detected recently
                    recent = [e for e in self.detected_errors[-10:]
                              if e.error_message == detected.error_message]
                    if not recent:
                        self.detected_errors.append(detected)
                        logger.warning(
                            f"ErrorMonitor detected [{error_type}]: {match.group(0)[:100]}"
                        )

    def get_detected_errors(self, clear: bool = False) -> list[DetectedError]:
        """Get all detected errors.

        Args:
            clear: If True, clear the error list after retrieving

        Returns:
            List of DetectedError objects
        """
        with self.lock:
            errors = list(self.detected_errors)
            if clear:
                self.detected_errors.clear()
            return errors

    def get_recent_logs(self, n: int = 100) -> list[dict]:
        """Get recent log lines.

        Args:
            n: Number of recent lines to retrieve

        Returns:
            List of log dicts with timestamp, source, line
        """
        with self.lock:
            return list(self.log_buffer)[-n:]

    def has_errors(self) -> bool:
        """Check if any errors have been detected."""
        with self.lock:
            return len(self.detected_errors) > 0

    def get_error_summary(self) -> str:
        """Get a summary of detected errors for Guardian context."""
        with self.lock:
            if not self.detected_errors:
                return "No errors detected"

            summary_lines = ["Detected errors:"]
            for i, err in enumerate(self.detected_errors[-5:], 1):  # Last 5 errors
                summary_lines.append(
                    f"{i}. [{err.error_type}] {err.error_message[:100]}"
                )

            return "\n".join(summary_lines)


# Global error monitor instance
_global_monitor: Optional[ErrorMonitor] = None


def get_error_monitor() -> ErrorMonitor:
    """Get or create global error monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = ErrorMonitor()
    return _global_monitor


def start_error_monitoring():
    """Start global error monitoring."""
    monitor = get_error_monitor()
    monitor.start()
    return monitor


def stop_error_monitoring():
    """Stop global error monitoring."""
    global _global_monitor
    if _global_monitor:
        _global_monitor.stop()


def get_detected_errors(clear: bool = False) -> list[DetectedError]:
    """Get detected errors from global monitor."""
    monitor = get_error_monitor()
    return monitor.get_detected_errors(clear=clear)
