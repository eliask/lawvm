"""UK feature-typed ``EnvGatedProbe`` harness — shared by all 9 (and counting)
dormant-checker probe modules wired at the UK replay fold-exit.

WHY THIS EXISTS
Per AGENTS.md §2.6: when the same fix shape lands for the third time, that
is a missing abstraction. The dormant audit-probe pattern — env-gated
default-off, observation-only, env-flag enabled, probe-skipped diagnostic
on failure, CompileAdjudication emission per finding with a fixed-shape
detail payload — has landed 9 times already (one per audit row LS-MAT-01/
LS-01/LS-12/LS-13/LS-11/LS-23/D8/D11/D12 + timeline_invariants). This
module is the extracted harness that future probe modules use to drop
their LOC cost from ~250 → ~80.

WHAT THIS DOES **NOT** YET DO (§2.6 incremental migration discipline):
The 9 existing probes are NOT migrated in the same commit as the harness
introduction. Per §2.6 rule-of-three-migration-discipline: introduce the
harness + migrate ONE probe + confirm CI green + leave the other 8 in
their current shape; the precedent migration can be reverse-applied to
the others in cohesive later commits. Rushing a 9-probe sweep in one
commit defeats the §2.6 synthesis.

WHY UK-LOCAL-NOT-CORE (§2.3)
The env-gated observation-only probe pattern is UK-specific. FI's
strict-mode mechanism is per-recovery-pattern gating INSIDE apply sites
(verified yesterday against the codebase — not the
"hallucinated-StrictModeViolationError" framing). Per §2.3 a jurisdiction-
local drafting idiom (or here, a jurisdiction-local probe pattern) stays
in the frontend until the shape is proven across frontends. So this is
the UK's own ``probe_base.py``, NOT in ``lawvm.core``.

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
      * env_flag: the ``LAWVM_UK_<NAME>_PROBE`` constant each probe defines
        for its default-off opt-in env gate.
      * kind: the UK-scoped adjudication kind (e.g.
        ``uk_replay_observation_promoted_to_authority_observed``).
      * skipped_kind: the UK-scoped kind for the probe-skipped diagnostic
        (e.g. ``uk_replay_observation_promoted_to_authority_probe_skipped``).
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
) -> CompileAdjudication:
    """Build a non-blocking per-finding CompileAdjudication from a ProbeSpec.

    The detail payload carries the uniform harness fields (rule_id, family,
    probe_mode, strict_disposition, quirks_disposition, witness_class,
    witness_prior_art, core_registry_finding_kind) plus the per-finding
    extension fields passed via ``extra_detail``. The per-probe tail owns
    the audit-specific evidence (cited_policy_id, addr_path, etc.) so a
    triager can read both the harness-describing envelope and the per-
    finding evidence in one record.
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
        blocking=False,
        phase=_PROBE_PHASE,
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
