"""se_anchor_manifest.py — Sweden's frozen content-addressed anchor + touch-relation
attribution engine (#183/#205, the SWEDEN jurisdiction).

This is the Sweden analogue of :mod:`lawvm.tools.uk_anchor_manifest` (United Kingdom),
:mod:`lawvm.tools.ee_anchor_manifest` (Estonia) and :mod:`lawvm.tools.fi_anchor_manifest`
(Finland), extending the drift-robust #183 metric
(``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to Sweden. It is ADDITIVE:
it never mutates the SE corpus, replay, or the existing ``se-bench`` scoring; the
default SE bench stays byte-identical.

WHAT A SWEDEN ANCHOR IS (the jurisdiction adaptation — DOCUMENTED, and why it differs).
Finland enumerates published *consolidation snapshots* per statute (``plan_snapshots``);
Estonia the Riigi Teataja *terviktekst* chain per grupi_id (each dated); the UK a single
enacted→current window per act. Sweden is DIFFERENT again, and the difference is material:

    Sweden's Regeringskansliet (RK) surface publishes, per base statute, exactly ONE
    consolidated ``rk.current.json`` — the LATEST consolidation only, carrying an
    "Ändring införd: t.o.m. SFS YYYY:N" stamp (the last amendment folded in). There is
    NO enumerable effective-date-addressed chain of consolidated versions per statute
    (the single-version-oracle data ceiling — the registered SE declared assumption
    ``se_data_ceiling_single_version_oracle``). So Sweden has no multi-anchor legal-time
    chain to mirror EE's terviktekst chain.

    What Sweden DOES have — and it is genuinely content-addressed and replays — is a
    per-AMENDING-ACT replay WINDOW. For each amending SFS act (a real SFS act carrying a
    compiled op set), :func:`sweden.fetch.check_se_official_replay` materializes the base
    statute at ``pre_date`` (the state BEFORE the amendment, the replay BASE), applies the
    amending act's ops, and compares the replayed post-state to the consolidated oracle
    (the RK current surface materialized as-of the amendment's ``effective_date``), with
    the amending act's own official-act text as a fallback oracle. So each SE anchor is a
    2-node replay chain per amending act:

      * anchor[0] = the PRE base (replay's source; its ``replay_text`` is the base's
        per-section text at ``pre_date``). It seeds the touch relation.
      * anchor[1] = the replayed POST state. Its ``replay_text`` is the per-section
        replayed text; it is SCORED against the consolidated oracle's post-state.

    This is exactly the ``base → as_of`` replay UK/EE/FI score — a real, published,
    content-pinned window — only the window is delimited by ONE amending act rather than a
    published-snapshot pair. It is the natural SE grain: the amending act IS the legal-time
    step whose effect the oracle records.

COMMENSURABLE-SURFACE ADAPTATION (documented, per task — and WHY SE differs). EE's per-
section verdict is byte-exact; FI's is structure-aware; UK's is eId PRESENCE. Sweden's own
``se-bench`` headline (``lawvm.tools.se_bench``) scores over the SE THREE-BUCKET split of
:func:`sweden.fetch.check_se_official_replay`'s per-op rows: ``genuine_match`` /
``oracle_version_mismatch`` / ``genuine_mismatch`` / ``unknown`` (see
``se_three_bucket_for_classification``). The SE bench's ``structural_err`` is exactly the
``genuine_mismatch`` fraction — a correct replay measured against a LATER consolidation
(``oracle_version_mismatch``) or an untrustworthy stamp (``unknown``) is NOT a correctness
gap. So the SE anchor's penalized surface is the SE three-bucket ``genuine_mismatch`` set
(the same surface ``se-bench`` scores), NOT byte-exact text: a section is penalized iff its
per-op row's three-bucket verdict is ``genuine_mismatch`` (the replay text genuinely
disagrees with the CONTEMPORANEOUS oracle/current surface, including the
``official_oracle_match_current_surface_drift`` real-drift case). That makes ``struct_sim``
commensurable with the ``se-bench`` headline.

WHY THE TOUCH RELATION IS PRESERVED (same-dimension-touch). Sweden's per-op rows are, by
CONSTRUCTION, the amending act's declared op targets — ``check_se_official_replay`` builds
one row PER OP (``for op in ops``). So every scored SE section is a section REPLAY touched
in this window (the amendment applied an op to it). We encode this faithfully: the PRE base
anchor carries NO keys and the POST anchor carries every op-target label, so
``touch_set(pre, post)`` is exactly the op-target set — replay's own notion of "what this
amending act touched", in the same (wording) dimension as the divergence. The neutral
attribution calculus then types a penalized (``genuine_mismatch``) op-target as
``candidate_replay_bug_persistent_post_touch`` (→ replay_bug, BILLABLE): a section replay
TOUCHED whose post-state genuinely diverges from the contemporaneous oracle and is not
healed. A section replay touched whose oracle is a strictly-LATER consolidation is instead
carried on the per-anchor ``oracle_suspect`` witness (see below) and types to
``temporal_mismatch`` (non-billable WARN), never a replay bug.

ORACLE-SUSPECT DISCIPLINE (reused, first-class). Sweden already types oracle-side
commensurability defects: a row whose three-bucket verdict is ``oracle_version_mismatch``
(correct replay vs a strictly-later consolidation) or ``unknown`` (missing/unparseable
consolidation stamp) is NOT a replay bug — it is the ``se_data_ceiling_single_version_oracle``
frontier. When an amending act's oracle is a strictly-later consolidation
(``oracle_version_relation == "later"``) OR its stamp is untrustworthy, we stamp the POST
anchor's ``oracle_suspect``, so the attribution calculus types the act's divergences to
``temporal_mismatch_commensurability`` (never a replay bug) — the SE analogue of UK's
``comparison_class``/EE's ``source_adjudication.oracle_suspect``. This is the exact
distinction ``se-bench`` already draws (its ``oracle_version_mismatch``/``unknown`` buckets
do NOT enter ``structural_err``).

REUSED NEUTRAL CORE. The touch relation itself is jurisdiction-neutral: this module imports
:class:`fi_anchor_manifest.AnchorObservation`, :class:`TouchObservation`,
:func:`attribute_divergences`, and the ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` maps
unchanged (they take a ``sid`` string and operate on generic replay text maps — nothing
Finland-specific). The shared taxonomy
(:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused as-is. Only the anchor
*enumeration* (the amending act's pre→post window), *scoring* (SE replay + the SE
three-bucket verdict), and the content-addressing are SE-specific. As in EE/UK, SE does not
populate ``structural_only_penalized_keys`` (the SE surface is wording/presence — the
three-bucket has no byte-exact structure-only sub-verdict), so the wording-level touch
relation governs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.ctsf_corpus_cache import memoize_default_corpus

# The touch relation is jurisdiction-neutral: reuse Finland's engine wholesale.
# These operate on a ``sid`` string + generic replay text maps; none of them import
# ``lawvm.finland``. Re-exported here so SE callers have a single surface.
from lawvm.tools.fi_anchor_manifest import (
    _VERDICT_TO_FAMILY,
    _VERDICT_TO_STATUS,
    AnchorObservation,
    TouchObservation,
    attribute_divergences,
)


MANIFEST_SCHEMA = "lawvm.se_anchor_manifest.v1"

# The two anchor version tags of the SE 2-node replay window per amending act.
_PRE_TAG = "pre"
_POST_TAG = "post"


def _default_db() -> Path:
    """The SE Farchive path (the same ``sweden.farchive`` ``se-bench`` reads)."""
    from lawvm.sweden.fetch import _DEFAULT_CACHE

    return _DEFAULT_CACHE


def _repo_root() -> Path:
    # src/lawvm/tools/se_anchor_manifest.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


def se_anchor_corpus_available() -> bool:
    """True iff the ``sweden.farchive`` backing the SE real corpus is present.

    Scoring the SE #183 corpus re-derives the touch relation per amending act via SE
    replay, which reads the SE Farchive. When it is absent (a corpus-free CI checkout)
    the SE real-corpus tests SKIP; the gate's unit surface stays corpus-free.
    """
    try:
        return _default_db().exists()
    # An availability PROBE: any archive-open failure legitimately means "corpus
    # absent" (tests skip; the CLI reports the frozen baseline).
    # lawvm-failloud: corpus-availability probe; absence is the answer, not an error
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest (mirrors uk/ee_anchor_manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed SE replay anchor (the pre base or the replayed post).

    ``artifact_hash`` pins the RAW published SFS artifact bytes (immutability check —
    any RK/official re-edit changes it). ``cnf_hash`` pins the normative-projected
    per-section text map, so an editorial-only refresh moves ``artifact_hash`` but
    leaves ``cnf_hash`` stable.
    """

    amending_sfs_id: str
    base_sfs_id: str
    version_tag: str
    as_of: Optional[str]
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_tag": self.version_tag,
            "base_sfs_id": self.base_sfs_id,
            "as_of": self.as_of,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    amending_sfs_id: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "amending_sfs_id": self.amending_sfs_id,
            "anchors": [a.to_dict() for a in self.anchors],
        }


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# Per-act scoring (SE replay + SE three-bucket verdict → AnchorObservation chain)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SEReplayScore:
    """The raw materials of one SE amending-act replay comparison.

    ``pre_text`` / ``post_text`` are per-op-target label → normalized text maps: the
    PRE base carries no keys (it seeds the touch relation) and the POST carries every
    op target (so ``touch_set`` is the op-target set — see the module docstring
    "WHY THE TOUCH RELATION IS PRESERVED"). ``penalized`` is the SE three-bucket
    ``genuine_mismatch`` label set (the surface ``se-bench``'s ``structural_err``
    scores). ``oracle_suspect`` is set when the act's oracle is a strictly-later /
    untrustworthy consolidation (the ``se_data_ceiling_single_version_oracle``
    frontier — a temporal, non-billable move).
    """

    pre_text: dict[str, str]
    post_text: dict[str, str]
    penalized: frozenset[str]
    oracle_suspect: Optional[str]
    status: str


def _row_label(row: dict[str, Any]) -> str:
    """The per-op-target label key of one SE replay row (kind-namespaced so a section
    and a heading / appendix sharing a label do not collide)."""
    kind = str(row.get("target_kind") or "")
    label = str(row.get("section") or row.get("appendix") or "")
    return f"{kind}:{label}"


def _score_se_replay(amending_sfs_id: str, *, archive: Any) -> _SEReplayScore:
    """Replay one amending SFS act pre→post and gather the per-op-target surfaces.

    Reuses ``check_se_official_replay`` exactly (the same replay + three-bucket verdict
    ``se-bench`` scores), so the comparison is bench-identical. Returns the PRE (empty)
    and POST (replayed) per-target text maps + the SE three-bucket ``genuine_mismatch``
    penalized set + the oracle-suspect witness (a strictly-later / untrustworthy
    consolidation).
    """
    from lawvm.sweden.fetch import (
        check_se_official_replay,
        se_three_bucket_for_classification,
        SE_THREE_BUCKET_GENUINE_MISMATCH,
        SE_THREE_BUCKET_ORACLE_VERSION_MISMATCH,
        SE_THREE_BUCKET_UNKNOWN,
    )

    try:
        result = check_se_official_replay(archive, amending_sfs_id)
    except (FileNotFoundError, ValueError, KeyError, AssertionError) as exc:
        return _SEReplayScore(
            pre_text={},
            post_text={},
            penalized=frozenset(),
            oracle_suspect=None,
            status=f"ERROR:{type(exc).__name__}",
        )

    outcome = str(result.get("outcome") or "")
    from lawvm.sweden.fetch import SE_REPLAY_OUTCOME_REPLAY_FEASIBLE

    if outcome != SE_REPLAY_OUTCOME_REPLAY_FEASIBLE:
        # A non-feasible outcome (older_base_required / apply_raise / precondition
        # issues) is not a scorable replay window — the source does not
        # deterministically specify the replay (frontier), so the act is UNSCORABLE.
        return _SEReplayScore(
            pre_text={},
            post_text={},
            penalized=frozenset(),
            oracle_suspect=None,
            status=f"UNSCORABLE:{outcome}",
        )

    rows = list(result.get("rows") or [])
    post_text: dict[str, str] = {}
    penalized: set[str] = set()
    saw_oracle_version_frontier = False
    for row in rows:
        key = _row_label(row)
        post_text[key] = str(row.get("replay_text") or "")
        classification = str(row.get("classification") or "")
        matched = bool(row.get("match"))
        bucket = se_three_bucket_for_classification(classification, matched=matched)
        if bucket == SE_THREE_BUCKET_GENUINE_MISMATCH:
            penalized.add(key)
        elif bucket in (
            SE_THREE_BUCKET_ORACLE_VERSION_MISMATCH,
            SE_THREE_BUCKET_UNKNOWN,
        ):
            saw_oracle_version_frontier = True

    # The act-level oracle-suspect witness: a strictly-later consolidation (the oracle
    # is a newer version than the replayed amendment) OR any row on the
    # oracle_version_mismatch / unknown frontier. This routes the act's divergences to
    # ``temporal_mismatch`` (never a replay bug) — the ``se_data_ceiling_single_version_oracle``
    # frontier ``se-bench`` already excludes from ``structural_err``.
    oracle_version_relation = str(result.get("oracle_version_relation") or "")
    oracle_suspect: Optional[str] = None
    if oracle_version_relation == "later" or saw_oracle_version_frontier:
        oracle_suspect = f"se_data_ceiling_single_version_oracle:{oracle_version_relation or 'frontier'}"

    return _SEReplayScore(
        pre_text={},  # the PRE base seeds the touch relation (all post keys are touched)
        post_text=post_text,
        penalized=frozenset(penalized),
        oracle_suspect=oracle_suspect,
        status="OK",
    )


def score_se_anchors(amending_sfs_id: str, *, archive: Any) -> list[AnchorObservation]:
    """Score the pre→post 2-node replay window of one amending SFS *amending_sfs_id*.

    Returns two :class:`AnchorObservation`s (base first): the PRE base (its
    ``replay_text`` is empty — it seeds the touch relation, so every op target is
    "touched") and the replayed POST (``replay_text`` = per-target replayed text, scored
    against the consolidated oracle). ``touch_set(pre, post)`` is thus exactly the amending
    act's op-target set — replay's own notion of what this amendment touched, in the same
    (wording) dimension as the divergence.
    """
    score = _score_se_replay(amending_sfs_id, archive=archive)
    if score.status != "OK":
        return [
            AnchorObservation(
                version_tag=_POST_TAG,
                amendment_id=amending_sfs_id,
                as_of=None,
                struct_sim=-1.0,
                n_sections=0,
                n_penalized=0,
                penalized_keys=frozenset(),
                replay_text={},
                oracle_suspect=None,
                status=score.status,
            )
        ]

    # anchor[0] — the PRE base. It seeds the touch relation (its replay_text is empty, so
    # every post op-target is a touch). It is NOT scored against the oracle in the chain
    # (base is replay's source, exactly like UK/EE/FI skip re-scoring the base);
    # struct_sim = 1.0 so it participates as a scored chain node.
    base = AnchorObservation(
        version_tag=_PRE_TAG,
        amendment_id=_PRE_TAG,
        as_of=_PRE_TAG,
        struct_sim=1.0,
        n_sections=0,
        n_penalized=0,
        penalized_keys=frozenset(),
        replay_text={},
        oracle_suspect=None,
        status="BASE",
    )

    n_sections = len(score.post_text)
    struct_sim = 1.0 if not n_sections else 1.0 - len(score.penalized) / n_sections

    post = AnchorObservation(
        version_tag=_POST_TAG,
        amendment_id=_POST_TAG,
        as_of=_POST_TAG,
        struct_sim=struct_sim,
        n_sections=n_sections,
        n_penalized=len(score.penalized),
        penalized_keys=frozenset(score.penalized),
        replay_text=dict(score.post_text),
        oracle_suspect=score.oracle_suspect,
        status="OK",
        # SE's surface is wording/presence (the three-bucket has no byte-exact
        # structure-only sub-verdict), so we do not populate
        # ``structural_only_penalized_keys`` — the wording-level touch relation governs
        # (mirrors EE/UK's documented choice).
        structural_only_penalized_keys=frozenset(),
    )
    return [base, post]


# ---------------------------------------------------------------------------
# Statute-level driver: enumerate + score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    amending_sfs_id: str
    anchors: tuple[AnchorObservation, ...]
    observations: tuple[TouchObservation, ...]
    status: str = "OK"

    @property
    def scored(self) -> list[AnchorObservation]:
        return [a for a in self.anchors if a.struct_sim >= 0.0]

    @property
    def min_over_life(self) -> Optional[float]:
        return min((a.struct_sim for a in self.scored), default=None)

    @property
    def latest_scored(self) -> Optional[float]:
        for a in reversed(self.anchors):
            if a.struct_sim >= 0.0:
                return a.struct_sim
        return None

    @property
    def candidate_bug_observations(self) -> tuple[TouchObservation, ...]:
        return tuple(
            o
            for o in self.observations
            if o.verdict == "candidate_replay_bug_persistent_post_touch"
        )

    @property
    def untyped_observations(self) -> tuple[TouchObservation, ...]:
        return tuple(o for o in self.observations if o.verdict == "untyped")

    @property
    def is_gated_clean(self) -> bool:
        return not self.candidate_bug_observations and not self.untyped_observations


def attribute_statute(
    amending_sfs_id: str, *, archive: Any = None
) -> StatuteAttribution:
    """Score the pre→post window of one amending SFS act, then attribute + gate.

    The attribution calculus (Finland's neutral ``attribute_divergences``) runs over the
    chronological scored anchor list ``[pre, post]``. A ``post`` divergence over an
    op-target replay TOUCHED (every op target is touched by construction) that is a SE
    three-bucket ``genuine_mismatch`` and stays diverged is a candidate replay bug; a
    divergence on an act whose oracle is a strictly-later consolidation is oracle-side
    (temporal, non-billable).
    """
    close_after = False
    if archive is None:
        from lawvm.sweden.fetch import open_se_archive

        archive = open_se_archive(_default_db(), readonly=True)
        close_after = True
    try:
        anchors = score_se_anchors(amending_sfs_id, archive=archive)
        scored = [a for a in anchors if a.struct_sim >= 0.0]
        if len(scored) < 2:
            return StatuteAttribution(
                amending_sfs_id=amending_sfs_id,
                anchors=tuple(anchors),
                observations=(),
                status=anchors[0].status if anchors else "ERROR:no-anchors",
            )
        observations = attribute_divergences(amending_sfs_id, anchors)
        return StatuteAttribution(
            amending_sfs_id=amending_sfs_id,
            anchors=tuple(anchors),
            observations=tuple(observations),
        )
    finally:
        if close_after:
            archive.close()


# ---------------------------------------------------------------------------
# The frozen REAL SE corpus + its scoring (the CTSF gate input the parent wires in)
# ---------------------------------------------------------------------------

REAL_ANCHOR_SE_JURISDICTION = "sweden"

# The frozen, content-pinned SE amending-SFS corpus (sorted, explicit — membership is
# part of the frozen input). Each is a real amending SFS act with a genuine compiled op
# set whose pre→post replay reproduces every op-target section against the
# contemporaneous oracle 0-billable; annotated with the residual family it contributes at
# freeze time so the coverage is auditable.
#
# This corpus is curated 0-BILLABLE (no replay_bug/unknown) — the honest steady state,
# mirroring FI/EE/UK/EU. SE amending acts whose pre→post replay surfaces a GENUINE
# ``genuine_mismatch`` divergence (a replay-touched op target the contemporaneous oracle
# carries that replay drops / mis-segments) are DELIBERATELY EXCLUDED — those are real
# defects to fix, not to freeze. See the deliverable report / notes_internal for the
# itemized excluded-bug list.
REAL_ANCHOR_SE_CORPUS_SIDS: tuple[str, ...] = (
    "2002:1006",  # scored clean (4 op targets, all exact) — base 1999:1229
    "2006:1320",  # scored clean (2 op targets: editorial_attribution_only + inline_numbering_only)
    "2009:538",   # PROMOTED #218 (5 op targets, all clean) — base 2007:346; was the
    #               truncated-inserted-clause / mid-body-footnote bug (§9a wrapped
    #               "4 a §" cross-reference split; §4b Prop./Ändringen footnote fold)
    "2011:1496",  # scored clean (4 op targets, editorial_attribution_only) — base 2007:757
    "2012:682",   # scored clean (2 op targets, exact) — base 2011:1244
    "2014:313",   # scored clean (2 op targets, exact) — base 2005:551
    "2015:1037",  # PROMOTED #218 (7 op targets, all clean) — base 2005:1057; was the
    #               Jfr-EU-directive / "Senaste lydelse" footnote-fold bug (§2 §4)
    "2015:838",   # scored clean (5 op targets, all exact) — base 2010:598
    "2016:1216",  # scored clean (3 op targets, exact) — base 2001:650
    "2018:243",   # scored clean (4 op targets, exact) — base 2011:1244
    "2018:328",   # scored clean (4 op targets, exact) — base 2007:528
    "2021:1035",  # PROMOTED #218 (1 op target, clean) — base 2010:299; was the
    #               "2 kap.\\n7 §" chapter-section cross-reference mis-segmentation
    #               (ghost §7 + truncated §9)
    "2022:1495",  # scored clean (3 op targets, exact) — base 2009:400
    "2022:543",   # scored clean (3 op targets: editorial + inline_numbering) — base 2009:93
    "2026:249",   # PROMOTED #218 (2 op targets, all clean) — base 2010:1846; was the
    #               "3 kap.\\n11 §" cross-reference drop + 2-level ghost §9a/heading
    #               mis-segmentation (§10a truncation) + trailing numbered Prop. footnote
)

# The committed SE baseline artifact (frozen, sibling of the FI/EE/UK/EU ones).
GATE_SE_BASELINE_PATH = Path("tests/data/ctsf_gate_se_residual_baseline.json")


@memoize_default_corpus
def score_se_real_corpus(
    sids: Any | None = None,
) -> dict[str, dict[str, int]]:
    """Score the SE #183 touch-relation corpus into its typed-residual set.

    For each amending SFS id, run the ``se_anchor_manifest`` attribution engine over its
    pre→post replay window and project each ``TouchObservation`` into its CTSF residual
    family (the shared ``_VERDICT_TO_FAMILY``). Returns the same diffable
    ``{sid: {family: count}}`` shape as the FI/EE/UK/EU corpora, only non-zero families
    retained, a clean-but-scored act present with an empty family map. Deterministic in
    sid order.

    Reads the SE Farchive (per-act SE replay). Deterministic given the frozen corpus
    bytes; NOT the wall-clock-free path — same as the FI/EE/UK/EU corpora.
    """
    from lawvm.sweden.fetch import open_se_archive

    corpus_sids = tuple(sids) if sids is not None else REAL_ANCHOR_SE_CORPUS_SIDS
    # Open ONE archive handle for the whole corpus so each act's replay reuses it.
    archive = open_se_archive(_default_db(), readonly=True)
    try:
        out: dict[str, dict[str, int]] = {}
        for sid in corpus_sids:
            attr = attribute_statute(sid, archive=archive)
            families: dict[str, int] = {}
            for obs in attr.observations:
                family = _VERDICT_TO_FAMILY[obs.verdict]
                families[family] = families.get(family, 0) + 1
            out[sid] = {fam: n for fam, n in sorted(families.items()) if n}
        return dict(sorted(out.items()))
    finally:
        archive.close()


# ---------------------------------------------------------------------------
# SE baseline artifact (frozen; the parent's ctsf_gate reads this via load_se_baseline)
# ---------------------------------------------------------------------------


def _se_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    from lawvm.core.ctsf_gate import FAIL_FAMILIES, GATE_VERSION
    from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — SWEDEN (#183/#205). Frozen typed-"
            "residual set of the REAL SE touch-relation anchor corpus "
            "(REAL_ANCHOR_SE_CORPUS_SIDS), keyed {sid: {family: count}} with only "
            "non-zero families retained. A SE anchor is the per-amending-act pre→post "
            "replay window (SE has no dated consolidation chain — a single-version "
            "RK oracle; the amending act is the legal-time step). The penalized surface "
            "is the SE three-bucket genuine_mismatch set (the exact surface se-bench's "
            "structural_err scores); every scored section is an op target replay touched "
            "by construction. The gate FAILs iff a NEW replay_bug/unknown residual "
            "appears vs this set; WARNs on a typed oracle/editorial/temporal move. This "
            "corpus is curated 0-BILLABLE (the honest steady state); SE amending acts "
            "whose pre→post replay surfaces genuine billable residuals are deliberately "
            "excluded (they are defects to fix, not to freeze). Regenerate with `uv run "
            "python -m lawvm.tools.se_anchor_manifest --update-baseline` (needs the SE "
            "Farchive) after a legitimate, reviewed corpus/projection change — a "
            "preregistered predict-then-compare event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_SE_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_SE_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_se_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen SE typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_SE_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_se_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen SE typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL SE corpus (``score_se_real_corpus()``). Reads the
    SE Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_SE_BASELINE_PATH
    payload = _se_baseline_payload(
        residuals if residuals is not None else score_se_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the shared taxonomy + FI verdict maps)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: TouchObservation) -> AgreementResidual:
    """Project one SE touch observation into the shared AgreementResidual taxonomy.

    Reuses Finland's ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` (the verdict→family
    mapping is jurisdiction-neutral), stamped with the SE jurisdiction.
    """
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"se:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="sweden",
        agreement_surface="se_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="se_bench.anchor.touch_relation",
        rule_id=obs.verdict,
        source_artifact_id=obs.sid,
        safe_default="classify_without_rewriting_replay_or_oracle",
        forbidden_shortcuts=(
            "touch_observation_as_replay_authorization",
            "oracle_conviction_as_source_truth",
        ),
        detail={
            "section_key": obs.section_key,
            "window": obs.window,
            "touching_amendments": list(obs.touching_amendments),
            "evidence": obs.evidence,
        },
    )


# ---------------------------------------------------------------------------
# CLI: attribute SE amending acts / regenerate the frozen baseline
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.se_anchor_manifest",
        description="Run the SE touch-relation attribution engine over one or more "
        "amending SFS acts (e.g. 2015:838), the #183/#205 metric; or regenerate the "
        "frozen SE CTSF baseline with --update-baseline.",
    )
    parser.add_argument("amending_sfs_ids", nargs="*")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate the frozen SE CTSF baseline from the real SE corpus.",
    )
    args = parser.parse_args(argv)

    if args.update_baseline:
        path = write_se_baseline()
        print(f"wrote SE CTSF baseline → {path}")
        return 0

    if not args.amending_sfs_ids:
        parser.error("provide one or more amending SFS ids, or --update-baseline")

    rc = 0
    for sid in args.amending_sfs_ids:
        attr = attribute_statute(sid)
        if attr.status != "OK":
            print(f"\n=== {sid} === {attr.status}", file=sys.stderr)
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(f"\n=== {sid}  ({len(attr.scored)} scored anchors)  [{gate}] ===")
        print(
            f"  min-over-life={100 * (attr.min_over_life or 0):.2f}%  "
            f"latest={100 * (attr.latest_scored or 0):.2f}%"
        )
        for o in attr.observations:
            print(f"  {o.section_key:<24} {o.verdict}  window={o.window}")
        if not attr.is_gated_clean:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
