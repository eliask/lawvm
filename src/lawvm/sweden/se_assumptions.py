"""Sweden (SE) declared-assumption register — the hand-curated, root-committed set.

Sibling of :mod:`lawvm.finland.fi_assumptions`. The Sweden compiler's declared
NON-guarantees, as typed :class:`~lawvm.core.assumption_register.AssumptionRegister`
objects rather than prose scattered across ``notes/SWEDEN_LAWVM_STATUS.md`` § Limits.

WHY SE specifically. SE replays as **consistency verification** against an
authoritative-but-single-version SFS consolidated oracle (latest consolidation
only — no point-in-time historical snapshots). Three load-bearing data-shape
facts sit underneath every SE replay-vs-oracle agreement claim that the status
doc carries as prose; this register makes them checkable root-committed objects
so a missing or surplus declared non-guarantee is detectable, not folklore.

WHAT THIS DOES **NOT** YET DO (honesty boundary — see the core module
docstring): v0 is HAND-CURATED. It does not auto-discover assumptions from prose
or scan the suite. ``expires_when`` is human-readable not machine-evaluable.
The root is not yet wired into the pack manifest / compile dossier.

The entries below encode the three data-ceiling facts:

1. **Single-version oracle.** The archived `rk.current.json` is the *latest*
   consolidation only (stamped ``Ändring införd: t.o.m. SFS YYYY:N``). Replaying
   a historical amendment compares it against a much-later-dated oracle — the
   dominant residual bucket ``oracle_version_mismatch`` is correct replay
   measured against a wrong-dated oracle, not a fidelity failure.

2. **Reverse-patch non-invertibility.** `_invert_se_reversible_ops`
   (fetch.py:1937) can invert only additive structural ops (section/heading/
   appendix INSERT, section RENUMBER). REPLACE / REPEAL / TEXT_REPLACE /
   HEADING_REPLACE lack the pre-amendment text needed to invert and produce NO
   inverse. `notes_internal/SE_VERSION_AWARE_ORACLE_SCOPING.md` (2026-06-14)
   proved empirically that 94.8% of version-timing drift rows have ≥1
   non-invertible REPLACE/REPEAL in their later chain — reverse-patch is
   mathematically blocked at scale, not a layout oversight.

3. **Archaeic cached `official.act.json` rows.** ~5000 cached rows carry
   pre-fix parsed state (truncated SFS-citation lines, ghost duplicate-label
   provisions). The runtime coercion refresh trigger bridges the ops-level
   staleness at replay, but the oracle-side `official_provisions` dict still
   compares against the cached truncated text. A bulk ``fetch-official
   --force-reextract`` re-ingest would resolve the 8 remaining
   `official_oracle_match_current_surface_drift` cases by lifting cached acts
   to the post-cleaner-fix parsed shape; the re-ingest is pending explicit
   authorization (touches the shared `sweden.farchive`, ~6 GB).
"""

from __future__ import annotations

from lawvm.core.assumption_register import AssumptionRegister


def build_se_assumption_register() -> tuple[AssumptionRegister, ...]:
    """The Sweden declared non-guarantees, hand-curated for v0.

    Returned as a sorted-stable tuple so :func:`assumption_register_root`
    yields one deterministic checkable root for the SE declared-assumption set.
    Mirrors :func:`lawvm.finland.fi_assumptions.build_fi_assumption_register`.
    """
    return (
        AssumptionRegister(
            kind="source_unavailable",
            scope=(
                "SE oracle-version surface: the archived rk.current.json seeded "
                "from the sfst current page carries only the LATEST consolidation "
                "(Ändring införd: t.o.m. SFS YYYY:N). Replaying a historical "
                "amendment compares its post-state against a much-later-dated "
                "oracle. The dominant residual bucket "
                "`official_oracle_version_mismatch` is correct replay measured "
                "against a wrong-dated oracle, NOT a fidelity failure; closing it "
                "would require point-in-time historical consolidations that the "
                "public source (rättsbaser/sfst) does not expose."
            ),
            effect="qualifies",
            expires_when=(
                "point-in-time consolidated snapshots are acquired (a corpus "
                "acquisition project), OR the forward older-base rebuild "
                "(rebuild_se_older_base_from_official_chain) is run for bases "
                "where the forward chain is complete."
            ),
            public_message=(
                "LawVM does NOT guarantee that an SE replay-vs-oracle "
                "version_mismatch row is a replay defect. The replay is correct; "
                "the oracle is simply a different-date consolidation. The "
                "version-mismatch bucket is a typed frontier residual, not a "
                "mismatch."
            ),
            witness_rule_id="se_replay_base_surface_contains_post_amendment_targets",
            finding_refs=(
                "notes_internal/SE_CURRENT_SURFACE_DRIFT_RANKING.md",
                "notes_internal/SE_VERSION_AWARE_ORACLE_SCOPING.md",
                "notes/SWEDEN_LAWVM_STATUS.md::Limits",
            ),
        ),
        AssumptionRegister(
            kind="doctrine_unresolved",
            scope=(
                "SE reverse-patch invertibility: _invert_se_reversible_ops "
                "(fetch.py:1937) inverts only additive structural ops "
                "(section/heading/appendix INSERT, section RENUMBER). REPLACE / "
                "REPEAL / TEXT_REPLACE / HEADING_REPLACE lack the pre-amendment "
                "text needed to invert and produce NO inverse. A version-aware "
                "oracle built by reverse-patching the latest sfst surface back to "
                "an amendment's date is therefore mathematically blocked at scale "
                "(94.8% of version-timing rows carry a non-invertible op in the "
                "later chain)."
            ),
            effect="qualifies",
            expires_when=(
                "the reverse-patch engine grows an inverse for REPLACE / REPEAL "
                "/ TEXT_* / HEADING_REPLACE — which physically requires the "
                "pre-amendment text the forward op does not carry and the source "
                "does not expose."
            ),
            public_message=(
                "LawVM does NOT guarantee SE reverse-patch oracle selection. "
                "Reverse-patch resolves ≤0.7% of version-timing drift rows; the "
                "94.8% with a non-invertible REPLACE/REPEAL in the later chain "
                "carry post-D text the undo path cannot remove."
            ),
            witness_rule_id="se_later_chain_reverse_op_exception",
            finding_refs=(
                "notes_internal/SE_VERSION_AWARE_ORACLE_SCOPING.md::Q3",
                "tests/test_sweden_fetch.py::test_analyze_se_official_replay_feasibility_detects_available_later_reverse_chain",
            ),
        ),
        AssumptionRegister(
            kind="source_unavailable",
            scope=(
                "SE archaeic cached official.act.json rows: ~5000 archived rows "
                "carry pre-fix parsed state (truncated SFS-citation lines, ghost "
                "duplicate-label provisions, ghost companion inserted-headings). "
                "The runtime coercion refresh trigger bridges the ops-level "
                "staleness at replay, but the oracle-side official_provisions "
                "dict still compares against the cached truncated text — the 8 "
                "remaining `official_oracle_match_current_surface_drift` "
                "mismatches at corpus-N=500 trace to this. A bulk "
                "`lawvm sweden fetch-official --force-reextract` re-ingest would "
                "lift cached acts to the post-cleaner-fix parsed shape."
            ),
            effect="qualifies",
            expires_when=(
                "the bulk `fetch-official --force-reextract` re-ingest over the "
                "~5000 archaeic cached official.act.json rows is authorized and "
                "run, OR the 8 residual drift rows are demonstrated to be "
                "oracle-side editorials (re-classified as findings rather than "
                "fixed by re-ingest)."
            ),
            public_message=(
                "LawVM does NOT guarantee that the 8 residual "
                "`official_oracle_match_current_surface_drift` rows at corpus-N=500 "
                "indicate a replay defect. They trace to ~5000 archaeic cached "
                "official.act.json rows the runtime-coercion refresh bridges at "
                "the ops level but not at the oracle-side provision text. The "
                "cached text re-ingest is pending explicit authorization."
            ),
            witness_rule_id="se_official_act_payload_row_duplicate_label",
            finding_refs=(
                "notes/SWEDEN_LAWVM_STATUS.md::Limits::Stale cached archaeic "
                "official-act JSON",
                "notes_internal/SE_CURRENT_SURFACE_DRIFT_RANKING.md::(d) editorial/normalization",
            ),
        ),
    )


__all__ = ["build_se_assumption_register"]
