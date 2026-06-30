"""Shared feature-typed ``EnvGatedProbe`` harness — the jurisdiction-neutral
home of the per-finding ``CompileAdjudication`` envelope builder consumed by
the UK fold-exit dormant-checker probes AND the per-op mutation-boundary
seam-observation projectors of UK, Norway, and Sweden.

WHY THIS EXISTS / WHY CORE-LEVEL (§2.3, §2.6)
This harness was born UK-local as ``lawvm.uk_legislation.probe_base`` (the
9 fold-exit dormant-checker probes share it). The per-op mutation-boundary
projector then landed the SAME envelope shape a THIRD time across frontends:
UK migrated onto the harness (task #65), while NO and SE hand-built the
byte-identical ``CompileAdjudication`` by inlining the same detail dict. Per
§2.6 (rule-of-three: the third landing of one shape is a missing abstraction)
and §2.3 (an idiom proven across frontends leaves the frontend), the harness
is promoted here, to ``lawvm.core``: it builds frontend-agnostic
``CompileAdjudication`` envelopes and depends on nothing jurisdiction-specific.

``lawvm.uk_legislation.probe_base`` is now a thin re-export shim of this
module, so the UK's existing ~9 fold-exit probes + the UK boundary projector
keep importing ``from lawvm.uk_legislation.probe_base import ...`` UNCHANGED.

PUBLIC SURFACE (small, deliberate)
* :class:`ProbeSpec` — frozen dataclass capturing the per-probe immutable
  shape (env_flag, kind, family, witness_class, witness_prior_art,
  core_registry_finding_kind).
* :func:`probe_env_enabled` — pure helper that returns ``True`` when the
  named env flag is set to "1".
* :func:`make_probe_skip_adjudication` — build a non-blocking probe-skipped
  diagnostic CompileAdjudication from a ProbeSpec (uniform shape).
* :func:`make_probe_observed_adjudication` — build a non-blocking per-
  finding CompileAdjudication from a ProbeSpec + the per-finding detail
  extension (uniform envelope; per-finding tail goes in extra_detail).
* :func:`detail_mapping_to_json_safe_dict` — JSON-safe conversion helper
  (same shape that 3+ probes already wrote inline).

Each probe module instantiates a :class:`ProbeSpec` at module scope once
+ calls the helpers, dropping the per-module boilerplate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication


# Standard owner phase for env-gated observation-only probes wired at the
# ``uk_amendment_replay.apply_ops`` fold-exit. Hard-coded so a future
# reader can grep ``EnvGatedProbe`` producers and see the uniform shape.
# The per-op apply-site consumers (the UK/NO/SE mutation-boundary projectors)
# emit at ``phase="replay"`` instead, via the ``phase=`` override on
# ``make_probe_observed_adjudication`` — the default keeps the fold-exit
# probes byte-identical.
_PROBE_PHASE = "replay_products"
_PROBE_MODE = "observation_only"
_PROBE_STRICT_DISPOSITION = "record"
_PROBE_QUIRKS_DISPOSITION = QuirksDisposition.RECORD


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """The per-probe immutable shape consumed by the probe-base helpers.

    Frozen + slots because probe specs are module-scope constants;
    downstream readers (probe-base helpers, fire-drill tests) MUST be
    able to trust immutability — a mid-run field bump would break audit
    consumers that read the spec via getattr (per §2.6 synthesis).

    Fields:
      * env_flag: the ``LAWVM_<JURIS>_<NAME>_PROBE`` constant each probe
        defines for its default-off opt-in env gate.
      * kind: the jurisdiction-scoped adjudication kind (e.g.
        ``uk_replay_observation_promoted_to_authority_observed``).
      * skipped_kind: the jurisdiction-scoped kind for the probe-skipped
        diagnostic (e.g.
        ``uk_replay_observation_promoted_to_authority_probe_skipped``).
        Conventionally ``kind.replace("_observed", "_probe_skipped")``;
        made explicit so a future rename does not silently break it.
      * family: the audit-family tag (e.g. ``"lineage"``,
        ``"commencement_totality"``, ``"unknown_attestation_policy"``).
      * audit_module_path: the dotted-path identifier of the underlying
        core audit module (e.g.
        ``"core.timeline_invariants.check_all_timeline_invariants_typed"``).
        For probes that compose multiple inputs (e.g. the timeline_
        invariants probe composes ``compile_timelines`` AND
        ``check_all_timeline_invariants_typed``), use a ``+``-joined
        dotted path.
      * witness_prior_art: short stable identifier of the canonical
        prior-art witness (the FI analogue when present, or the registry-
        row identifier).
      * core_registry_finding_kind: the underlying audit's registered
        finding code (e.g. ``"LINEAGE.CYCLE"``); empty string for probes
        that don't translate to a single registered finding code (e.g. the
        timeline_invariants probe emits a family of invariant-kind codes).
    """

    env_flag: str
    kind: str
    skipped_kind: str
    family: str
    audit_module_path: str
    witness_prior_art: str
    core_registry_finding_kind: str = ""

    def __post_init__(self) -> None:
        # Empty-field validation (fail-loud per AGENTS.md §1.10): a probe
        # spec with empty env_flag or kind drops its discipline silently —
        # the spec must be a load-bearing named carrier.
        for field_name in (
            "env_flag",
            "kind",
            "skipped_kind",
            "family",
            "audit_module_path",
            "witness_prior_art",
        ):
            value = getattr(self, field_name)
            if not value:
                raise ValueError(
                    f"ProbeSpec.{field_name} must be a non-empty string "
                    f"(got {value!r}); probe specs are module-scope constants "
                    "and load-bearing — empty fields drop the discipline "
                    "silently (AGENTS.md §1.10 fail-loud, §2.6 synthesis)."
                )


def probe_env_enabled(env_flag: str) -> bool:
    """True when the named env flag is set to ``"1"``.

    Per the dormat-checker-probe pattern: default-off preserves byte-stable
    production bench replay output. Probe harness users call this at the
    probe's entry point to early-return when the flag is disabled.
    """
    return os.environ.get(env_flag, "") == "1"


def make_probe_skip_adjudication(
    spec: ProbeSpec,
    *,
    statute_id: str,
    reason: str,
    op_id: str = "",
) -> CompileAdjudication:
    """Build a non-blocking probe-skipped diagnostic CompileAdjudication.

    Mirrors the uniform ``_build_probe_skip_adjudication`` shape that 9
    probes defined inline. The skipped_kind, family, and rule_id fields
    are sourced from the ProbeSpec — per-probe locals.
    """
    return CompileAdjudication(
        kind=spec.skipped_kind,
        message=(
            f"UK probe for {spec.family} could not run the audit. Recorded "
            "as a named diagnostic so the silence is itself audible — the "
            "alternative is silently dropping the probe check itself, which "
            "would recreate the §2.9 false confidence we are fixing."
        ),
        source_statute=str(statute_id or ""),
        op_id=str(op_id or ""),
        blocking=False,
        phase=_PROBE_PHASE,
        detail={
            "rule_id": spec.skipped_kind,
            "family": spec.family,
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": _PROBE_STRICT_DISPOSITION,
            "quirks_disposition": _PROBE_QUIRKS_DISPOSITION,
        },
    )


def make_probe_observed_adjudication(
    spec: ProbeSpec,
    *,
    statute_id: str,
    message: str,
    extra_detail: Optional[Mapping[str, Any]] = None,
    op_id: str = "",
    phase: str = _PROBE_PHASE,
    blocking: bool = False,
) -> CompileAdjudication:
    """Build a per-finding observation CompileAdjudication from a ProbeSpec.

    The detail payload carries the uniform harness fields (rule_id, family,
    probe_mode, strict_disposition, quirks_disposition, witness_class,
    witness_prior_art, core_registry_finding_kind) plus the per-finding
    extension fields passed via ``extra_detail``. The per-probe tail owns
    the audit-specific evidence (cited_policy_id, addr_path, etc.) so a
    triager can read both the harness-describing envelope and the per-
    finding evidence in one record.

    ``phase`` and ``blocking`` default to the fold-exit observation shape
    (``phase="replay_products"``, ``blocking=False``) that the original
    8 fold-exit probes use — so their calls stay byte-identical. The two
    overrides exist for the structurally-distinct per-op apply-site
    consumers (the UK/NO/SE mutation-boundary projectors), which emit at
    ``phase="replay"`` and pass the core finding's ``blocking`` through
    (observation-only today — the core audit runs ``is_strict=False`` so the
    pass-through is ``False`` — but ready for a future strict lane). A
    caller that overrides neither gets exactly the prior behaviour.
    """
    detail: dict[str, Any] = {
        "rule_id": spec.kind,
        "family": spec.family,
        "probe_mode": _PROBE_MODE,
        "strict_disposition": _PROBE_STRICT_DISPOSITION,
        "quirks_disposition": _PROBE_QUIRKS_DISPOSITION,
        "witness_class": spec.audit_module_path,
        "witness_prior_art": spec.witness_prior_art,
    }
    if spec.core_registry_finding_kind:
        detail["core_registry_finding_kind"] = spec.core_registry_finding_kind
    if extra_detail:
        for k, v in extra_detail.items():
            detail[str(k)] = v
    return CompileAdjudication(
        kind=spec.kind,
        message=str(message),
        source_statute=str(statute_id or ""),
        op_id=str(op_id or ""),
        blocking=bool(blocking),
        phase=str(phase),
        detail=detail,
    )


def detail_mapping_to_json_safe_dict(detail: Any) -> dict:
    """Convert a frozen Mapping[str, Any] (e.g. ``Observation.detail``,
    ``Finding.detail``, ``TimelineInvariantViolation.detail``) to a plain
    JSON-safe dict for the adjudication detail payload.

    Mirrors the per-probe ``_observation_detail_to_dict`` /
    ``_finding_detail_to_dict`` / ``_violation_detail_to_dict`` helpers
    already written three times in the 9-probe suite. Sub-mappings are
    recursed; non-JSON-shaped values are stringified defensively so the
    adjudication payload never fails serialisation. The empty-input case
    returns an empty dict (mirrors the per-probe helpers' behaviour).
    """
    out: dict = {}
    if not detail:
        return out
    try:
        for key, value in detail.items():
            # Sub-mapping (a Mapping instance with .items) — recurse.
            if hasattr(value, "items") and callable(getattr(value, "items", None)):
                out[str(key)] = detail_mapping_to_json_safe_dict(value)
            elif isinstance(value, (str, int, float, bool, type(None))):
                out[str(key)] = value
            else:
                # Unknown non-JSON shape — stringify defensively so
                # nothing is silently skipped (§1.10 fail-loud: triager
                # should see the unexpected value, not assume None).
                out[str(key)] = str(value)
    except Exception:  # noqa: BLE001 — best-effort stringification, never strict
        return {"detail_render_failed": str(detail)[:200]}
    return out


__all__ = [
    "ProbeSpec",
    "probe_env_enabled",
    "make_probe_skip_adjudication",
    "make_probe_observed_adjudication",
    "detail_mapping_to_json_safe_dict",
]
