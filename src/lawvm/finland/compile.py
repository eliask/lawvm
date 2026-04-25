"""Preferred Finland compile dossier APIs.

Public callers that want Finland compile products should import from this
module. The owned dossier assembly lives behind this surface in
``lawvm.finland._compile``.
"""

from lawvm.finland._compile import (
    compile_fi_facade,
    compile_fi_facade_from_replay,
)

__all__ = [
    "compile_fi_facade",
    "compile_fi_facade_from_replay",
]
