"""Module registry for discovering and managing pluggable analysis modules."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Type

from modules.base import BaseModule

logger = logging.getLogger(__name__)


class ModuleRegistry:
    """Central registry for all analysis modules.

    Supports:
    - Explicit registration of module classes
    - Auto-discovery from the modules/ package
    - Plugin loading from external directories
    """

    def __init__(self) -> None:
        self._modules: dict[str, BaseModule] = {}

    def register(self, module: BaseModule) -> None:
        """Register a module instance."""
        if module.name in self._modules:
            logger.warning("Overwriting existing module: %s", module.name)
        self._modules[module.name] = module
        logger.info("Registered module: %s v%s", module.name, module.version)

    def get(self, name: str) -> BaseModule:
        """Get a module by name. Raises KeyError if not found."""
        if name not in self._modules:
            raise KeyError(f"Module '{name}' not registered. Available: {list(self._modules)}")
        return self._modules[name]

    def list_modules(self) -> list[str]:
        """List all registered module names."""
        return list(self._modules.keys())

    def describe_all(self) -> dict[str, dict]:
        """Return descriptions of all registered modules."""
        return {name: mod.describe() for name, mod in self._modules.items()}

    def auto_discover(self) -> None:
        """Auto-discover and register modules from the modules/ package."""
        modules_dir = Path(__file__).parent
        for py_file in modules_dir.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name == "base.py":
                continue
            module_name = py_file.stem
            try:
                mod = importlib.import_module(f"modules.{module_name}")
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseModule)
                        and attr is not BaseModule
                    ):
                        try:
                            instance = attr()
                            self.register(instance)
                        except Exception as e:
                            logger.warning("Failed to instantiate %s: %s", attr_name, e)
            except ImportError as e:
                logger.warning("Failed to import module %s: %s", module_name, e)

    def load_plugins(self, plugin_dir: Path) -> None:
        """Load external plugin modules from a directory.

        Each plugin .py file should export a class inheriting from BaseModule.
        """
        if not plugin_dir.exists():
            return
        for py_file in plugin_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            import_name = f"_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(import_name, py_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseModule)
                        and attr is not BaseModule
                    ):
                        try:
                            instance = attr()
                            self.register(instance)
                            logger.info("Loaded plugin: %s from %s", instance.name, py_file)
                        except Exception as e:
                            logger.warning("Failed to load plugin %s: %s", py_file, e)
