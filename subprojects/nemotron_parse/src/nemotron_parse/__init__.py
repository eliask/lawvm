"""Process-isolated Nemotron-Parse page-parse service for LawVM.

This package NEVER gets imported by the main ``lawvm`` package — the only
coupling is the subprocess wire contract documented in the README. Keep this
``__init__`` free of heavy imports so the hermetic wire-contract test (and the
``probe``/``parse`` CLI's argument errors) work without torch installed.
"""
from __future__ import annotations

__all__ = ["wire"]
