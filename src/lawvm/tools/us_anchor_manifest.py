"""us_anchor_manifest.py — the US federal frozen content-addressed anchor +
replay-attribution engine (#205, the EIGHTH jurisdiction).

This is the US analogue of :mod:`lawvm.tools.eu_anchor_manifest` (European Union),
:mod:`lawvm.tools.se_anchor_manifest` (Sweden), :mod:`lawvm.tools.no_anchor_manifest`
(Norway) and the FI/EE/UK/NZ manifests, extending the drift-robust #183 CTSF metric
(``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to the US federal frontend.
It is ADDITIVE: it never mutates the US corpus, the US dry-run replay pipeline, or the
existing ``us_bench_unit_result`` scoring path; the US frontend stays byte-identical.

WHAT A US "ANCHOR" IS — and WHY US KEEPS THE SAME-DIMENSION-TOUCH PRINCIPLE WITHOUT
THE FI/EE/UK LABEL-ORDERED-TREE MODEL (documented, load-bearing). Finland enumerates
the published *consolidation snapshots* of one statute over its life; Estonia the Riigi
Teataja *terviktekst* chain per ``grupi_id``; the UK/NO each act's enacted/base→current
replay window. All of those are label-ordered tree grafters whose oracle is a
materialized consolidated TREE. The US federal frontend is architecturally DIFFERENT:
it is a TEXT/SPAN MATERIALIZER (``us_federal.apply_profile`` / ``us_federal.dry_run``
perform string surgery on located char spans, not label-ordered tree grafting). But the
US surface nonetheless carries EXACTLY the two things the CTSF anchor gate requires — a
real replay window with an oracle to diff, and a commensurable same-dimension touch
surface — so US is anchor-gatable, and this module builds that gate.

    THE REPLAY WINDOW (dated, content-pinned, offline). The US bench corpus
    (``us/bench/us_bench_corpus.csv``) enumerates, per USC title, adjacent-edition
    windows ``(title, before_year, after_year)`` — a genuine dated point-in-time
    chain of OLRC-published US Code annual editions (2006 … 2024). For each window,
    :func:`us_federal.bench.evaluate_window` derives the window's Public Laws from the
    editions' source-credit witness delta, replays each PL's amendatory instructions
    onto the before-edition base via the dry-run kernel
    (:func:`us_federal.dry_run.build_us_dry_run_from_archive`), and compares the
    materialized per-section statutory text to the OLRC after-edition oracle. The whole
    chain runs NETWORK-FREE from ``us_federal.farchive`` bytes (before/after edition
    ``.htm`` oracles + PL USLM ``.xml`` blobs + prior editions), and is deterministic
    given the frozen bytes. The window is content-addressed by
    ``(title, before_year, after_year)``.

    THE ORACLE + THE COMMENSURABLE TOUCH SURFACE. The oracle is the OLRC-published USC
    annual-edition section text at the after-year (``us://usc/{year}/title{N}.htm``,
    normalized). The touch surface is the CHANGED-SECTION SET: every scored section is a
    ``title:section`` the window's amendments TOUCHED — the oracle changed it and/or
    LawVM claimed it. This is the same-dimension touch relation (wording of the touched
    sections) the FI/EE/UK/NO calculus scores, only the touch set is delimited by an
    adjacent-edition window's Public Laws rather than a snapshot pair. Critically, the
    US oracle is a WITNESS, never repaired-to: the dry-run keeps every divergence
    visible as a typed residual with a disposition, and NEVER edits the materialization
    toward the oracle (the ``authoritative oracle ≠ correct`` discipline LawVM forbids).

WHAT IS SCORED (the US adaptation — its OWN disposition→family projection, like EU's).
US does NOT re-derive per-section replay text maps to feed Finland's neutral
``attribute_divergences`` (the US bench ALREADY computes the typed per-section
partition — re-deriving it would duplicate, not reuse). Instead — exactly as EU
projects its conserved-apply partition directly onto the CTSF families — US projects the
per-window disposition partition ``evaluate_window`` produces. The dry-run kernel
partitions every oracle-changed / claimed section into a CLOSED disposition set (see
``us_federal.dry_run`` ``DISPOSITION_*`` + ``us_federal.bench.us_bench_unit_result``):

    * ``lawvm_wrong``   → ``replay_bug``  (BILLABLE): LawVM materialized a section's
      text and it GENUINELY diverges from the oracle after-text — and LawVM owns the
      error (not an oracle-side editorial pathology, not a source-truncation). This is
      the exact "the replay is wrong" signal the honest metric exists to convict. A hard
      FAIL.
    * ``unclassified_non_agreement`` → ``unknown`` (BILLABLE): a non-agreement section
      captured by NO named disposition bucket — a divergence we cannot even classify.
      The ``us_bench_unit_result`` residue records it explicitly so the structural error
      is never silently unexplained; here it is the second FAIL lane. A hard FAIL.
    * ``oracle_suspect`` → ``oracle_editorial_pathology`` (non-billable): the OLRC
      consolidated after-text diverges from the enacted instruction for a NON-amendment,
      editorial reason (the generalized F1 class) — our materialization is faithful; the
      gap is oracle-side. A WARN-lane typed move, never a red gate.
    * ``sunset_reversion`` → ``temporal_mismatch`` (non-billable): a temporary provision
      expired and the section reverted to its prior permanent form (F2, temporal). The
      change is driven by time, not a PL amendatory instruction. A WARN-lane move.
    * ``deferred_op``   → ``temporal_mismatch`` (non-billable): LawVM lowered the right
      op but the after-edition cutoff precedes its statutory effective date; the OLRC
      pre-incorporated the future-effective text (F3, OLRC editorial pre-dating — a
      temporal commensurability gap). A WARN-lane move.
    * ``missing_source`` → ``cnf_unsupported`` (non-billable): the oracle changed a
      section for which LawVM never lowered an amendment source — a standing lowering
      CAPABILITY GAP (the amendment exists; we have not lowered it yet). This is the US
      analogue of EU's ``eu_replay_typed_op_skip`` typed capability-gap: a WARN-lane
      frontier telemetry move, NOT a replay bug. (Honest limit, mirroring the US bench's
      ``coverage_source_present``: ``missing_source`` is NOT a wrong materialization —
      it is an un-lowered one — so it never enters the billable FAIL lane.)

A fully-agreeing window (every oracle-changed section materialized in agreement, 0
non-agreement) emits NO observation — it is scored-clean, the honest 0-billable steady
state the frozen corpus is curated to hold.

THE FROZEN CORPUS IS CURATED 0-BILLABLE (the honest steady state, mirroring
FI/EE/UK/EU/NZ/SE/NO). US windows whose replay surfaces a GENUINE ``lawvm_wrong``
(a section LawVM materializes WRONG vs the contemporaneous oracle) are DELIBERATELY
EXCLUDED — those are real defects to fix, not to freeze. A rich set of such billable
windows exists in the corpus (e.g. title35:2010->2012, title23:2020->2022,
title10:2022->2023); ``test_us_excluded_billable_windows_convict`` proves the fail-red
mechanism fires on them, so the gate's FAIL lane is convicted by REAL bugs, not only
synthetic injection. See the deliverable report for the itemized excluded-billable list.

OBSERVATION RECORD. US emits its own :class:`USWindowObservation` (a small typed
per-observation record) rather than reusing Finland's ``TouchObservation``: the FI
record's ``verdict`` is a CLOSED ``Literal`` of oracle-divergence verdicts, which the US
disposition verdicts are not members of (exactly EU's reason for its own record). The
shared taxonomy (:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused
as-is for cross-jurisdiction residual reporting. The gate's diff/baseline machinery
(``lawvm.core.ctsf_gate``) consumes the projected ``{window: {family: count}}`` set
identically to FI/EE/UK/EU/NZ/SE/NO.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    AgreementResidualStatus,
)
from lawvm.core.ctsf_corpus_cache import memoize_default_corpus

MANIFEST_SCHEMA = "lawvm.us_anchor_manifest.v1"


# ---------------------------------------------------------------------------
# The US verdict vocabulary + its projection onto the CTSF residual families.
# US cannot reuse Finland's oracle-divergence verdicts (US has its own typed
# disposition partition from the dry-run kernel); these describe the US per-section
# disposition instead. Kept EXPLICIT + honest, mirroring EU's own verdict model.
# ---------------------------------------------------------------------------

#: LawVM materialized a section whose text genuinely diverges from the oracle and
#: LawVM owns the error → a genuine replay defect (BILLABLE).
VERDICT_LAWVM_WRONG = "us_dry_run_lawvm_wrong"
#: A non-agreement section captured by no named disposition — unclassifiable (BILLABLE).
VERDICT_UNCLASSIFIED = "us_dry_run_unclassified_non_agreement"
#: The OLRC oracle diverges for a non-amendment editorial reason (F1) — oracle-side.
VERDICT_ORACLE_SUSPECT = "us_dry_run_oracle_suspect"
#: A temporary provision expired / reverted (F2) — temporal, not an amendment op.
VERDICT_SUNSET_REVERSION = "us_dry_run_sunset_reversion"
#: OLRC pre-dated a future-effective op (F3) — temporal commensurability gap.
VERDICT_DEFERRED_OP = "us_dry_run_deferred_op"
#: The oracle changed a section we never lowered an amendment source for — a standing
#: lowering capability gap (non-billable frontier, the US analogue of EU typed-op-skip).
VERDICT_MISSING_SOURCE = "us_dry_run_missing_source"

#: US verdict → CTSF residual family (``lawvm.core.ctsf_residual_report``'s
#: ``RESIDUAL_VERDICT_FAMILIES`` — the family set the gate diffs). The two BILLABLE
#: verdicts map onto the two FAIL families (``replay_bug`` / ``unknown``); the typed
#: non-billable verdicts map onto the WARN-lane families (editorial / temporal /
#: capability-gap). This is the map the GATE consumes.
_VERDICT_TO_FAMILY: dict[str, str] = {
    VERDICT_LAWVM_WRONG: "replay_bug",
    VERDICT_UNCLASSIFIED: "unknown",
    VERDICT_ORACLE_SUSPECT: "oracle_editorial_pathology",
    VERDICT_SUNSET_REVERSION: "temporal_mismatch",
    VERDICT_DEFERRED_OP: "temporal_mismatch",
    VERDICT_MISSING_SOURCE: "cnf_unsupported",
}

#: US verdict → shared ``AgreementResidual`` family vocabulary (used only by
#: :func:`observation_to_residual` for cross-jurisdiction residual reporting; the gate
#: does NOT use this). The typed capability-gap becomes an
#: ``accepted_non_executable_frontier``; the others keep their family identity.
_VERDICT_TO_RESIDUAL_FAMILY: dict[str, str] = {
    VERDICT_LAWVM_WRONG: "replay_bug",
    VERDICT_UNCLASSIFIED: "unknown",
    VERDICT_ORACLE_SUSPECT: "oracle_editorial_pathology",
    VERDICT_SUNSET_REVERSION: "temporal_mismatch",
    VERDICT_DEFERRED_OP: "temporal_mismatch",
    VERDICT_MISSING_SOURCE: "accepted_non_executable_frontier",
}

#: US verdict → AgreementResidual status (residual = billable-lane, blocked = typed).
_VERDICT_TO_STATUS: dict[str, str] = {
    VERDICT_LAWVM_WRONG: "residual",
    VERDICT_UNCLASSIFIED: "residual",
    VERDICT_ORACLE_SUSPECT: "blocked",
    VERDICT_SUNSET_REVERSION: "blocked",
    VERDICT_DEFERRED_OP: "blocked",
    VERDICT_MISSING_SOURCE: "blocked",
}


@dataclass(frozen=True)
class USWindowObservation:
    """One typed US per-window disposition observation.

    The US analogue of Finland's ``TouchObservation`` — but US's ``verdict`` vocabulary
    is its own (the dry-run disposition verdicts above, NOT the oracle-divergence
    verdicts Finland types, whose ``Literal`` is closed), so it is a distinct record
    (exactly EU's rationale for ``EUReplayObservation``). ``count`` is the number of
    disposition-bucket sections it carries; the gate consumes the projected family
    counts, so an observation with ``count==0`` is not emitted.
    """

    window: str
    verdict: str
    count: int
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "verdict": self.verdict,
            "count": self.count,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Farchive access (offline, network-free) + corpus availability
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    # src/lawvm/tools/us_anchor_manifest.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


def us_anchor_corpus_available() -> bool:
    """True iff the ``us_federal.farchive`` backing the US real corpus is present.

    Scoring the US #205 corpus re-runs the dry-run replay per window (which reads the US
    Farchive). When it is absent (a corpus-free CI checkout) the US real-corpus tests
    SKIP; the gate's unit surface (diff logic over the committed baseline) stays
    corpus-free. Mirrors the sibling ``*_anchor_corpus_available`` probes.
    """
    try:
        from lawvm.us_federal.sources import resolve_us_federal_farchive_path

        path, _rule = resolve_us_federal_farchive_path()
        return path.exists()
    # An availability PROBE: any resolution/open failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline).
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error.
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Per-window scoring (US dry-run disposition partition → USWindowObservation set)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowAttribution:
    """The typed attribution of one US replay window (mirrors StatuteAttribution)."""

    window: str
    observations: tuple[USWindowObservation, ...]
    oracle_changed: int = 0
    agreements: int = 0
    status: str = "OK"

    @property
    def billable_observations(self) -> tuple[USWindowObservation, ...]:
        return tuple(
            o
            for o in self.observations
            if _VERDICT_TO_FAMILY[o.verdict] in ("replay_bug", "unknown")
        )

    @property
    def is_gated_clean(self) -> bool:
        return not self.billable_observations


def _window_by_key(window_key: str) -> Any:
    """Resolve one frozen ``title{N}:{before}->{after}`` key to its ``BenchWindow``.

    The corpus rows are the single source of truth for a window's
    ``prior_edition_years`` (the F2 sunset-reversion channel), so we look the key up in
    the committed CSV corpus rather than reconstructing it — the window stays identical
    to the ``us-bench`` window of the same key.
    """
    from lawvm.us_federal.bench import DEFAULT_CORPUS_PATH, load_corpus

    corpus = {w.key: w for w in load_corpus(_repo_root() / DEFAULT_CORPUS_PATH)}
    return corpus.get(window_key)


def attribute_window(window_key: str, *, archive: Any = None) -> WindowAttribution:
    """Score one adjacent-edition window's offline dry-run replay → attribution.

    Runs the network-free US dry-run replay (derive window PLs from the edition witness
    delta → replay onto the before-edition base → compare to the after-edition oracle)
    and projects the per-window disposition partition into typed
    :class:`USWindowObservation`s per the US verdict model. A fully-agreeing window
    emits NO observation (scored clean). A ``lawvm_wrong`` or unclassified non-agreement
    emits a BILLABLE observation; a typed editorial / temporal / capability-gap
    disposition emits a non-billable observation. Deterministic given the frozen
    Farchive bytes.
    """
    from lawvm.us_federal.bench import (
        WindowStatus,
        evaluate_window,
        open_us_federal_farchive,
    )

    window = _window_by_key(window_key)
    if window is None:
        return WindowAttribution(
            window=window_key, observations=(), status="ERROR:window-not-in-corpus"
        )

    close_after = False
    if archive is None:
        archive = open_us_federal_farchive(readonly=True)
        close_after = True
    try:
        result = evaluate_window(archive, window)
    finally:
        if close_after:
            archive.close()

    if result.window_status is not WindowStatus.EVALUATED:
        # A typed skip (edition missing / empty witness delta / missing PL blob) is not
        # a scorable replay window — the source does not deterministically specify the
        # replay, so the window is UNSCORABLE (never a silent zero-agreement pass).
        return WindowAttribution(
            window=window_key,
            observations=(),
            status=f"UNSCORABLE:{result.skip_rule_id or result.window_status}",
        )
    if result.oracle_changed <= 0:
        # No oracle-changed sections — nothing to score against (NO_TRUTH).
        return WindowAttribution(
            window=window_key,
            observations=(),
            oracle_changed=result.oracle_changed,
            agreements=result.agreements,
            status="NO_TRUTH",
        )

    non_agreement = max(0, result.oracle_changed - result.agreements)
    # The named disposition buckets, in the SAME order + accounting the US bench's
    # ``us_bench_unit_result`` residue uses (so the two projections reconcile exactly).
    named: list[tuple[str, int]] = [
        (VERDICT_LAWVM_WRONG, result.lawvm_wrong),
        (VERDICT_ORACLE_SUSPECT, result.oracle_suspect),
        (VERDICT_MISSING_SOURCE, result.missing_source),
        (VERDICT_SUNSET_REVERSION, result.sunset_reversion),
        (VERDICT_DEFERRED_OP, result.deferred_op),
    ]
    observations: list[USWindowObservation] = []
    accounted = 0
    for verdict, count in named:
        if count > 0:
            accounted += count
            observations.append(
                USWindowObservation(window=window_key, verdict=verdict, count=int(count))
            )
    # Any non-agreement section not captured by a named disposition is unclassified —
    # recorded explicitly (the exact ``unclassified_non_agreement`` residue the US bench
    # records so the structural error is never silently unexplained) → ``unknown``.
    remainder = non_agreement - accounted
    if remainder > 0:
        observations.append(
            USWindowObservation(
                window=window_key,
                verdict=VERDICT_UNCLASSIFIED,
                count=int(remainder),
                evidence=(
                    f"non_agreement={non_agreement} exceeds named dispositions "
                    f"(accounted={accounted})"
                ),
            )
        )

    return WindowAttribution(
        window=window_key,
        observations=tuple(observations),
        oracle_changed=result.oracle_changed,
        agreements=result.agreements,
        status="OK",
    )


# ---------------------------------------------------------------------------
# The frozen REAL US corpus + its scoring (the CTSF gate input the parent wires in)
# ---------------------------------------------------------------------------

REAL_ANCHOR_US_JURISDICTION = "us_federal"

# The frozen, content-pinned US adjacent-edition window corpus (sorted, explicit —
# membership is part of the frozen input). Each is a real
# ``title{N}:{before}->{after}`` window from ``us/bench/us_bench_corpus.csv`` whose
# offline dry-run replay materializes every oracle-changed section it lowers in
# agreement, with ZERO ``lawvm_wrong`` (no billable replay bug), annotated with the
# non-billable disposition families it contributes at freeze time so the coverage is
# auditable.
#
# This corpus is curated 0-BILLABLE (no replay_bug/unknown) — the honest steady state,
# mirroring FI/EE/UK/EU/NZ/SE/NO. US windows whose replay surfaces a GENUINE
# ``lawvm_wrong`` (a section LawVM materializes wrong vs the contemporaneous oracle) are
# DELIBERATELY EXCLUDED — those are real defects to fix, not to freeze. See the
# deliverable report / notes_internal for the itemized excluded-billable list; the
# exclusion is proven convicting by ``test_us_excluded_billable_windows_convict``.
#
# The corpus spans positive-law + non-positive titles and exercises every non-billable
# typed lane: oracle_suspect → oracle_editorial_pathology; sunset_reversion / deferred_op
# → temporal_mismatch; missing_source → cnf_unsupported.
REAL_ANCHOR_US_CORPUS_WINDOWS: tuple[str, ...] = (
    "title11:2023->2024",  # clean (agr=1) + temporal_mismatch (sunset=2) — bankruptcy
    "title20:2018->2020",  # pure clean (0 residual) — education (non-positive)
    "title21:2023->2024",  # clean (agr=1) + oracle_editorial(1) + cnf_unsupported(9) — food/drugs
    "title23:2016->2018",  # clean (agr=1) + oracle_editorial_pathology(3) — highways
    "title28:2014->2016",  # clean (agr=3) + oracle_editorial(2) + cnf_unsupported(1) + temporal(deferred=1) — judiciary
    "title28:2016->2018",  # clean (agr=4) + oracle_editorial(1) + cnf_unsupported(1) — judiciary
    "title35:2010->2012",  # clean (agr=33) + oracle_editorial(16) + cnf_unsupported(17) + temporal(deferred=42) — patents/AIA
    "title35:2020->2022",  # clean (agr=3) + oracle_editorial_pathology(1) — patents
    "title39:2022->2023",  # pure clean (0 residual) — postal service
    "title40:2018->2020",  # clean (agr=4) + oracle_editorial(1) + cnf_unsupported(4) — public buildings
    "title40:2022->2023",  # pure clean (agr=1, 0 residual) — public buildings
    "title47:2023->2024",  # pure clean (agr=1, 0 residual) — telecommunications (non-positive)
    "title48:2022->2023",  # clean (agr=1) + cnf_unsupported(1) — territories (non-positive)
    "title8:2016->2018",   # clean (agr=2) + oracle_editorial(2) + cnf_unsupported(3) + temporal(deferred=2) — aliens
)

# The committed US baseline artifact (frozen, sibling of the FI/EE/UK/EU/NZ/SE/NO ones).
GATE_US_BASELINE_PATH = Path("tests/data/ctsf_gate_us_residual_baseline.json")


@memoize_default_corpus
def score_us_real_corpus(
    windows: Any | None = None,
) -> dict[str, dict[str, int]]:
    """Score the US #205 disposition corpus into its typed-residual set.

    For each adjacent-edition window key, run the ``us_anchor_manifest`` attribution
    engine over its offline dry-run replay and project each ``USWindowObservation`` into
    its CTSF residual family (:data:`_VERDICT_TO_FAMILY`). Returns the same diffable
    ``{window: {family: count}}`` shape as the FI/EE/UK/EU/NZ/SE/NO corpora, only
    non-zero families retained, a clean-but-scored window present with an empty family
    map. Deterministic in window order.

    Reads the US Farchive (per-window dry-run replay). Deterministic given the frozen
    corpus bytes; NOT the wall-clock-free path — same as the sibling corpora.
    """
    from lawvm.us_federal.bench import open_us_federal_farchive

    corpus = tuple(windows) if windows is not None else REAL_ANCHOR_US_CORPUS_WINDOWS
    # Open ONE archive handle for the whole corpus so each window's replay reuses it.
    archive = open_us_federal_farchive(readonly=True)
    try:
        out: dict[str, dict[str, int]] = {}
        for window_key in corpus:
            attr = attribute_window(window_key, archive=archive)
            families: dict[str, int] = {}
            for obs in attr.observations:
                family = _VERDICT_TO_FAMILY[obs.verdict]
                families[family] = families.get(family, 0) + obs.count
            out[window_key] = {fam: n for fam, n in sorted(families.items()) if n}
        return dict(sorted(out.items()))
    finally:
        archive.close()


# ---------------------------------------------------------------------------
# US baseline artifact (frozen; the parent's ctsf_gate reads this via load_us_baseline)
# ---------------------------------------------------------------------------


def _us_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    from lawvm.core.ctsf_gate import FAIL_FAMILIES, GATE_VERSION
    from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — US FEDERAL (#205). Frozen typed-"
            "residual set of the REAL US adjacent-edition window anchor corpus "
            "(REAL_ANCHOR_US_CORPUS_WINDOWS), keyed {window: {family: count}} with only "
            "non-zero families retained. A US anchor is one (title, before_year, "
            "after_year) OLRC-published USC annual-edition window; the dry-run kernel "
            "replays the window's Public Laws onto the before edition and scores the "
            "materialized per-section text against the after-edition oracle. The touch "
            "surface is the changed-section set (every scored section is a section the "
            "window's amendments touched). The per-section disposition partition projects "
            "to CTSF families: lawvm_wrong -> replay_bug (BILLABLE), unclassified "
            "non-agreement -> unknown (BILLABLE), oracle_suspect -> "
            "oracle_editorial_pathology, sunset_reversion/deferred_op -> temporal_mismatch, "
            "missing_source -> cnf_unsupported (all WARN-lane). The gate FAILs iff a NEW "
            "replay_bug/unknown residual appears vs this set; WARNs on a typed "
            "editorial/temporal/capability-gap move. This corpus is curated 0-BILLABLE "
            "(the honest steady state); US windows whose replay surfaces a genuine "
            "lawvm_wrong are deliberately excluded (defects to fix, not to freeze) and "
            "proven convicting by test_us_excluded_billable_windows_convict. Regenerate "
            "with `uv run python -m lawvm.tools.us_anchor_manifest --update-baseline` "
            "(needs the US Farchive) after a legitimate, reviewed corpus/projection "
            "change — a preregistered predict-then-compare event, never a silent "
            "baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_US_JURISDICTION,
        "corpus_windows": list(REAL_ANCHOR_US_CORPUS_WINDOWS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_us_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen US typed-residual baseline ({window: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_US_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        window: {fam: int(cnt) for fam, cnt in families.items()}
        for window, families in sorted(residuals.items())
    }


def write_us_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen US typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL US corpus (``score_us_real_corpus()``). Reads the
    US Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_US_BASELINE_PATH
    payload = _us_baseline_payload(
        residuals if residuals is not None else score_us_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the shared taxonomy, US-stamped)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: USWindowObservation) -> AgreementResidual:
    """Project one US window observation into the shared AgreementResidual taxonomy.

    Uses :data:`_VERDICT_TO_RESIDUAL_FAMILY` (the wider AgreementResidual vocabulary),
    NOT the CTSF-family map the gate consumes — a missing-source capability gap is an
    ``accepted_non_executable_frontier`` here, ``cnf_unsupported`` in the gate.
    """
    family = cast(AgreementResidualFamily, _VERDICT_TO_RESIDUAL_FAMILY[obs.verdict])
    status = cast(AgreementResidualStatus, _VERDICT_TO_STATUS[obs.verdict])
    return AgreementResidual(
        residual_id=f"us:dry-run-disposition:{obs.window}:{obs.verdict}",
        jurisdiction="us_federal",
        agreement_surface="us_dry_run_changed_section_set",
        family=family,
        agreement_residual_status=status,
        owner_phase="us_bench.anchor.dry_run_disposition",
        rule_id=obs.verdict,
        source_artifact_id=obs.window,
        safe_default="classify_without_rewriting_replay_or_oracle",
        forbidden_shortcuts=(
            "disposition_observation_as_replay_authorization",
            "oracle_conviction_as_source_truth",
        ),
        detail={
            "window": obs.window,
            "count": obs.count,
            "evidence": obs.evidence,
        },
    )


# ---------------------------------------------------------------------------
# CLI: attribute US windows / regenerate the frozen baseline
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.us_anchor_manifest",
        description="Run the US dry-run disposition attribution engine over one or more "
        "adjacent-edition windows (e.g. title35:2020->2022), the #205 metric; or "
        "regenerate the frozen US CTSF baseline with --update-baseline.",
    )
    parser.add_argument("window_keys", nargs="*")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate the frozen US CTSF baseline from the real US corpus.",
    )
    args = parser.parse_args(argv)

    if args.update_baseline:
        path = write_us_baseline()
        print(f"wrote US CTSF baseline → {path}")
        return 0

    if not args.window_keys:
        parser.error("provide one or more window keys, or --update-baseline")

    rc = 0
    for window_key in args.window_keys:
        attr = attribute_window(window_key)
        if attr.status != "OK":
            print(f"\n=== {window_key} === {attr.status}", file=sys.stderr)
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(
            f"\n=== {window_key}  (oracle_changed={attr.oracle_changed} "
            f"agreements={attr.agreements})  [{gate}] ==="
        )
        for o in attr.observations:
            print(f"  {o.verdict:<36} count={o.count}  → {_VERDICT_TO_FAMILY[o.verdict]}")
        if not attr.is_gated_clean:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
