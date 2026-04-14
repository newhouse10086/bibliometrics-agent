"""Test script to verify error monitoring and Guardian intervention."""

import logging
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from core.error_monitor import ErrorMonitor, DetectedError

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)


def test_error_monitor():
    """Test error monitor captures various error types."""
    monitor = ErrorMonitor()
    monitor.start()

    # Simulate various error scenarios
    logger.error("ModuleNotFoundError: No module named 'lda'")
    logger.error("ImportError: cannot import name 'LdaModel'")
    logger.error("RuntimeError: LDA model failed to converge")

    # Check detected errors
    errors = monitor.get_detected_errors()
    print(f"\nDetected {len(errors)} errors:")
    for i, err in enumerate(errors, 1):
        print(f"{i}. [{err.error_type}] {err.error_message}")

    monitor.stop()

    assert len(errors) > 0, "Should detect at least some errors"
    assert any(e.error_type == "dependency_missing" for e in errors), \
        "Should detect dependency_missing error"

    print("\n✓ Error monitor test passed!")


def test_multiprocessing_error_capture():
    """Test that errors from multiprocessing would be captured."""
    monitor = ErrorMonitor()
    monitor.start()

    # Simulate a multiprocessing error message (like from tmtoolkit)
    stderr_msg = """Process SpawnPoolWorker-1:
Traceback (most recent call last):
  File "/usr/lib/python3.8/multiprocessing/process.py", line 315, in _bootstrap
    self.run()
  File "/usr/lib/python3.8/multiprocessing/process.py", line 108, in run
    self._target(*self._target_args, **self._target_kwargs)
ModuleNotFoundError: No module named 'lda'
"""

    # Write to stderr (which monitor captures)
    import sys
    sys.stderr.write(stderr_msg)

    errors = monitor.get_detected_errors()
    print(f"\nDetected {len(errors)} errors from stderr:")
    for i, err in enumerate(errors, 1):
        print(f"{i}. [{err.error_type}] {err.error_message[:100]}")

    monitor.stop()

    assert len(errors) > 0, "Should detect errors from stderr"
    print("\n✓ Multiprocessing error capture test passed!")


if __name__ == "__main__":
    test_error_monitor()
    test_multiprocessing_error_capture()
    print("\n✅ All tests passed!")
