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
  fi.v1.SOURCE_PATHOLOGY_RESOLUTION — generic source-pathology proof boundary
  fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE — passive source-unit coverage certificate
  fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE — passive operation-cue coverage certificate
  fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION — temporal base-selection boundary
  fi.v1.MUTATION_BOUNDARY_RESOLUTION — mutation-boundary proof boundary
  fi.v1.FAILED_OPERATION_RESOLUTION — failed-operation proof boundary
  fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION — unsupported corrigendum patch boundary
  fi.v1.ORACLE_OVERRIDE — projection-plane override: oracle (consolidated
      comparison surface) is wrong in any way and the proof is in the cited
      witness. NOT a replay-authorising claim — mutates the comparison/
      projection plane only (AGENTS.md §2.10).
"""
import importlib

importlib.import_module("lawvm.finland.claim_kinds.inline_statute_resolution")
importlib.import_module("lawvm.finland.claim_kinds.xml_manual_frontier")
importlib.import_module("lawvm.finland.claim_kinds.oracle_override")
