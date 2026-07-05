"""
app/engineering_exporter/__init__.py — REQ-W27-002

Public interface for the engineering export module.
All generators follow the same contract:
    generate_*(week: str, session: Session) -> str | dict

Timeline generation is pending Source Review #5 (reflex.py).
A safe placeholder is returned until that review is complete.
"""

from .exporter import (
    generate_engineering_index,
    generate_event_bundle,
    generate_runtime_statistics,
    generate_eo_register,
    generate_er_register,
    generate_engineering_summary,
    generate_timeline,          # placeholder — see exporter.py
    SCHEMA_VERSION,
    PACKAGE_VERSION_PREFIX,
    COMPATIBLE_RUNTIME,
)

__all__ = [
    "generate_engineering_index",
    "generate_event_bundle",
    "generate_runtime_statistics",
    "generate_eo_register",
    "generate_er_register",
    "generate_engineering_summary",
    "generate_timeline",
    "SCHEMA_VERSION",
    "PACKAGE_VERSION_PREFIX",
    "COMPATIBLE_RUNTIME",
]
