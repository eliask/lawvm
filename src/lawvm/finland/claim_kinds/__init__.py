"""Finland-specific claim kinds.

Registers all fi.v1.* ClaimKinds into the core registry at import time.
Import this module to activate Finland claim kinds.

Currently registered:
  fi.v1.INLINE_STATUTE_RESOLUTION — surface-grammar statute citation resolution
"""
from lawvm.finland.claim_kinds import inline_statute_resolution  # noqa: F401
