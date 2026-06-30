"""UK re-export shim for the shared probe-adjudication harness.

WHY THIS IS NOW A SHIM (§2.3, §2.6)
This harness was born here, UK-local, as the extracted base for the UK's 9
fold-exit dormant-checker probes. The per-op mutation-boundary projector then
landed the SAME ``CompileAdjudication`` envelope a THIRD time across frontends
(UK migrated onto it; NO and SE hand-built the byte-identical shape). Per §2.6
(rule-of-three) and §2.3 (an idiom proven across frontends leaves the frontend),
the harness moved to its jurisdiction-neutral home,
:mod:`lawvm.core.probe_adjudication`.

This module stays as a thin re-export so the UK's existing ~9 fold-exit probe
modules + the UK boundary projector keep importing
``from lawvm.uk_legislation.probe_base import ...`` UNCHANGED (minimal blast
radius). The real implementation — :class:`ProbeSpec`, :func:`probe_env_enabled`,
:func:`make_probe_skip_adjudication`, :func:`make_probe_observed_adjudication`,
:func:`detail_mapping_to_json_safe_dict` — lives in
:mod:`lawvm.core.probe_adjudication`. NEW probe consumers (in any frontend)
should import from the core module directly.
"""
from __future__ import annotations

from lawvm.core.probe_adjudication import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

__all__ = [
    "ProbeSpec",
    "probe_env_enabled",
    "make_probe_skip_adjudication",
    "make_probe_observed_adjudication",
    "detail_mapping_to_json_safe_dict",
]
