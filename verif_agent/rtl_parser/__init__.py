"""RTL parser package. Public API: port_resolver.resolve."""
from .port_resolver import discover_rtl_files, resolve

__all__ = ["discover_rtl_files", "resolve"]
