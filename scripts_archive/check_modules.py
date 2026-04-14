#!/usr/bin/env python
"""Check available modules."""

from modules.registry import ModuleRegistry

registry = ModuleRegistry()
registry.auto_discover()

print("Available modules:")
for module_name in sorted(registry.list_modules()):
    module = registry.get(module_name)
    print(f"  - {module_name} (v{module.version})")
