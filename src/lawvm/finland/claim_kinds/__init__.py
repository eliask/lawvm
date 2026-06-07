"""Finland-specific claim kinds.

Registers all fi.v1.* ClaimKinds into the core registry at import time.
Import this module to activate Finland claim kinds.

Currently registered:
  fi.v1.INLINE_STATUTE_RESOLUTION — surface-grammar statute citation resolution
  fi.v1.CORRIGENDUM_SOURCE_CORRECTION — XML/source correction evidence
  fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION — partial broad-payload proof boundary
  fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION — sparse slot-binding proof boundary
  fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION — container payload ownership boundary
  fi.v1.SOURCE_CHAIN_RESOLUTION — source-chain/recodification proof boundary
  fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION — temporal base-selection boundary
  fi.v1.MUTATION_BOUNDARY_RESOLUTION — mutation-boundary proof boundary
"""
from lawvm.finland.claim_kinds import inline_statute_resolution  # noqa: F401
from lawvm.finland.claim_kinds import xml_manual_frontier  # noqa: F401
