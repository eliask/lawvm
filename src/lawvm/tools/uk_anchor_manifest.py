"""uk_anchor_manifest.py — the United Kingdom frozen content-addressed anchor +
touch-relation attribution engine (#183/#205, THIRD jurisdiction).

This is the UK analogue of :mod:`lawvm.tools.ee_anchor_manifest` (Estonia) and
:mod:`lawvm.tools.fi_anchor_manifest` (Finland), extending the drift-robust #183
metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to a third
jurisdiction. It is ADDITIVE: it never mutates the UK corpus, replay, or the
existing ``uk-bench`` scoring; the default UK bench stays byte-identical.

WHAT A UK ANCHOR IS (the jurisdiction adaptation — DOCUMENTED, and why it preserves
the same-dimension-touch principle). Finland enumerates the published
*consolidation snapshots* of one statute over its life (``plan_snapshots``); Estonia
enumerates the Riigi Teataja *terviktekst* chain per ``grupi_id`` (each carrying a
``kehtivuseAlgus`` effective date). The UK is DIFFERENT and the difference is
material:

    legislation.gov.uk does NOT publish an enumerable, effective-date-addressed
    chain of consolidated versions per act. The Farchive holds, per act, exactly
    two content-addressed renderings: the ``.../enacted/data.xml`` (the statute as
    ORIGINALLY enacted — the replay BASE) and the ``.../data.xml`` (the SINGLE
    latest "revised"/consolidated ORACLE — the current in-force text). Dated
    point-in-time URLs (``.../<YYYY-MM-DD>/data.xml``) are fetchable on demand from
    the live site but are NOT crawled into the archive (verified: 0 dated PIT
    locators over 146k locator rows), and the multiple *observations* of a
    ``/data.xml`` locator are wall-clock CRAWL timestamps (a few days of re-fetch
    churn in 2026), NOT legal effective dates. So UK has no multi-anchor legal-time
    chain to mirror EE's terviktekst chain.

    What UK DOES have is one GENUINE content-addressed anchor WINDOW per act:
    enacted (base) → current (as_of, the single revised oracle). This is exactly
    the ``base → as_of`` replay EE/FI score — a real, published, content-pinned
    pair. We model each act as a 2-node replay chain:

      * anchor[0] = the ENACTED base. Its ``replay_text`` is the eId-PRESENCE map of
        the enacted IR (replay@base = the enacted statute, before any amendment op).
      * anchor[1] = the CURRENT as_of. Its ``replay_text`` is the eId-PRESENCE map of
        the REPLAYED IR (the enacted IR after the UK amendment-replay pipeline applies
        every amendment op forward to the current in-force state). It is SCORED
        against the current oracle's eId set.

    The touch relation ``touch_set(enacted, replayed)`` is then, faithfully, the set
    of eIds REPLAY added or removed applying the amendment chain — replay's own notion
    of "what the intervening amendments touched", derived without op-extraction
    plumbing, exactly as FI derives it from adjacent snapshots. This makes the
    SAME-DIMENSION-TOUCH principle bind on UK: a divergence at ``current`` over an
    eId REPLAY touched (added/removed in the window) that stays absent from the oracle
    is a candidate REPLAY BUG (billable); a divergence over an eId replay NEVER touched
    is a standing untouched divergence → oracle-side (non-billable). The convicting
    touch is in the same (presence) dimension as the divergence — the engine's
    invariant. See "STRUCTURE-SIGNATURE ADAPTATION" below for why UK's per-key surface
    is eId presence, not byte-exact text.

WHAT REPLAY IS SCORED AGAINST. For each act we run the UK amendment-replay pipeline
(``UKReplayPipeline.compile_ops_for_statute`` + ``replay_uk_ops`` +
``align_uk_replay_to_oracle_with_report``) from the enacted IR forward to the current
in-force state, then compare the replayed eId set to the current oracle's eId set on
UK's own commensurable compare-eId surface (``normalize_uk_replay_compare_eids`` +
``_canonical_compare_index`` — the exact surface ``uk-bench``'s ``replay_score`` runs
over), so a penalized eId is commensurable with the ``uk-bench`` headline.

STRUCTURE-SIGNATURE ADAPTATION (documented, per task — and WHY UK differs). EE's
section comparison is byte-exact; FI's is structure-aware over
``extract_ir_sections``. UK is DIFFERENT and the adaptation is deliberate:

    UK has NO commensurable per-eId byte/text verdict. The oracle's per-eId text
    (``extract_eid_map_bytes``'s ``text_map``) is built by a different normalizer
    (``_normalize_text_for_grounding``) and a different container aggregation than
    the replayed IR's ``_extract_eid_texts``, so even a near-perfect replay scores
    ~0 exact per-eId text matches (verified: 0/441 for a 99%-eId-overlap act). UK's
    own ``uk-bench`` never uses per-eId text as a binary verdict — only as an
    AVERAGED Levenshtein ratio scalar, precisely to tolerate that skew.

    UK's genuinely commensurable per-key surface is eId PRESENCE, canonicalized
    (Roman↔Arabic via ``_canonical_compare_index``) and noise-normalized (parent-path
    drift, display-number drift, non-legal fragment ids, collapsed-subtree shapes via
    ``normalize_uk_replay_compare_eids``). That is the exact surface ``uk-bench``'s
    ``replay_score`` runs over. So the UK anchor keys ``penalized_keys`` /
    ``replay_text`` by CANONICAL eId, with each anchor's ``replay_text`` a PRESENCE
    map (``{eid: eid}``): a penalized eId is an oracle eId ABSENT from the normalized
    replay, and a touch is an eId that APPEARED or DISAPPEARED across the window.

    We do NOT populate ``structural_only_penalized_keys`` — UK's divergence surface is
    presence/wording (there is no byte-exact structure-only sub-verdict), so the
    wording-level touch relation governs. This PRESERVES the same-dimension-touch
    principle: the convicting touch (an eId replay added/removed) is in the same
    (presence) dimension as the divergence (an eId absent from replay). A penalized
    eId that replay TOUCHED (added/removed in the window) and left diverged is a
    candidate replay bug; a penalized eId replay never touched is oracle-side.

ORACLE-SUSPECT DISCIPLINE (reused, first-class). UK already types oracle-side
commensurability defects: ``build_uk_source_adjudication`` maps a non-core
``comparison_class`` (anything outside ``UK_CORE_COMPARISON_CLASSES`` =
{``commensurable``, ``unapplied_oracle_expansion``}) to a non-empty
``oracle_suspect`` string (``source_adjudication.py``). That is the UK analogue of
Finland's ``get_consolidated_oracle_suspect`` / Estonia's
``source_adjudication.oracle_suspect``; the attribution calculus consumes it the same
way (an oracle-suspect anchor's divergences type to
``temporal_mismatch_commensurability``, never a replay bug).

REUSED NEUTRAL CORE. The touch relation itself is jurisdiction-neutral: this module
imports :class:`fi_anchor_manifest.AnchorObservation`, :func:`touch_set`,
:func:`structure_touch_set`, :class:`TouchObservation`,
:func:`attribute_divergences`, and the ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS``
maps unchanged (they take a ``sid`` string and operate on generic replay text maps —
nothing Finland-specific). The shared taxonomy
(:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused as-is. Only the
anchor *enumeration* (enacted+current per act), *scoring* (UK replay + UK eId text
comparison), and the content-addressing are UK-specific.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from lawvm.core.agreement_residual import AgreementResidual

# The touch relation is jurisdiction-neutral: reuse Finland's engine wholesale.
# These operate on a ``sid`` string + generic replay text maps; none of them import
# ``lawvm.finland``. Re-exported here so UK callers have a single surface.
from lawvm.tools.fi_anchor_manifest import (
    _VERDICT_TO_FAMILY,
    _VERDICT_TO_STATUS,
    AnchorObservation,
    TouchObservation,
    attribute_divergences,
)


MANIFEST_SCHEMA = "lawvm.uk_anchor_manifest.v1"

_LEG_BASE = "https://www.legislation.gov.uk"

# The two anchor version tags of the UK 2-node replay chain per act.
_ENACTED_TAG = "enacted"
_CURRENT_TAG = "current"


def _default_db() -> Path:
    from lawvm.tools.uk_bench import _DEFAULT_DB

    return _DEFAULT_DB


def _repo_root() -> Path:
    from lawvm.tools.uk_bench import _REPO_ROOT

    return _REPO_ROOT


def enacted_url(statute_id: str) -> str:
    return f"{_LEG_BASE}/{statute_id}/enacted/data.xml"


def current_url(statute_id: str) -> str:
    return f"{_LEG_BASE}/{statute_id}/data.xml"


# ---------------------------------------------------------------------------
# UK anchor enumeration (the enacted→current replay window of one act)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UKAnchorRef:
    """One content-addressed UK rendering of an act (a node of its 2-node chain).

    ``version_tag`` is ``"enacted"`` (the replay base) or ``"current"`` (the single
    revised oracle, the ``as_of`` anchor). ``locator`` is the archive URL (the
    anchor's content id); ``has_body`` is False when the archive lacks the bytes
    (not scorable).
    """

    statute_id: str
    version_tag: str
    locator: str
    has_body: bool

    @property
    def scorable(self) -> bool:
        return self.has_body


def enumerate_uk_anchors(statute_id: str, *, archive: Any) -> list[UKAnchorRef]:
    """Enumerate the enacted→current anchor chain of *statute_id* (base first).

    UK publishes exactly two content-addressed renderings per act — the enacted
    base and the single current revised oracle — so the chain is always the pair
    ``[enacted, current]`` (base first, chronological). This is the UK analogue of
    EE's ``enumerate_ee_anchors`` / FI's ``plan_snapshots``, adapted to UK's
    two-rendering surface (see the module docstring for why UK has no longer chain).
    """
    refs: list[UKAnchorRef] = []
    for tag, loc in ((_ENACTED_TAG, enacted_url(statute_id)), (_CURRENT_TAG, current_url(statute_id))):
        data = archive.get(loc)
        refs.append(
            UKAnchorRef(
                statute_id=statute_id,
                version_tag=tag,
                locator=loc,
                has_body=bool(data and len(data) > 100),
            )
        )
    return refs


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest (mirrors ee_anchor_manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed published UK rendering (enacted or current).

    ``artifact_hash`` pins the RAW published legislation.gov.uk XML bytes
    (immutability check — any re-edit changes it). ``cnf_hash`` pins the
    normative-projected per-eId text map, so an editorial-only refresh moves
    ``artifact_hash`` but leaves ``cnf_hash`` stable.
    """

    statute_id: str
    version_tag: str
    locator: str
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_tag": self.version_tag,
            "locator": self.locator,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    statute_id: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "anchors": [a.to_dict() for a in self.anchors],
        }


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


def _enacted_text_map(raw: bytes, statute_id: str, locator: str) -> dict[str, str]:
    """Per-eId normalized text of the enacted IR (the CNF projection of the base)."""
    from lawvm.tools.uk_bench import (
        _collect_eids,
        _extract_eid_texts,
        parse_uk_statute_ir_bytes,
    )

    ir = parse_uk_statute_ir_bytes(
        raw, statute_id=statute_id, version_label="enacted", source_path=locator
    )
    eids = _collect_eids(ir.body.children)
    for s in ir.supplements:
        eids.update(_collect_eids([s]))
    return _extract_eid_texts(ir, eids)


def _current_text_map(raw: bytes) -> dict[str, str]:
    """Per-eId normalized text of the current oracle (its CNF projection)."""
    from lawvm.tools.uk_bench import extract_eid_map_bytes

    data = extract_eid_map_bytes(raw)
    return dict(data.get("text_map", {}))


def build_statute_manifest(statute_id: str, *, archive: Any) -> StatuteManifest:
    """Content-address the enacted + current anchors of *statute_id*.

    Never raises for per-anchor problems — an unreadable/unparseable anchor yields
    an empty-hash anchor carrying a ``reason``.
    """
    anchors: list[Anchor] = []
    for ref in enumerate_uk_anchors(statute_id, archive=archive):
        reason = "" if ref.scorable else "artifact-absent"
        artifact_hash = ""
        cnf_hash = ""
        n_sections = 0
        try:
            raw = archive.get(ref.locator)
            if raw:
                artifact_hash = _sha256_hex(raw)
                if ref.scorable:
                    if ref.version_tag == _ENACTED_TAG:
                        cnf_map = _enacted_text_map(raw, statute_id, ref.locator)
                    else:
                        cnf_map = _current_text_map(raw)
                    cnf_hash = _cnf_hash_of_map(cnf_map)
                    n_sections = len(cnf_map)
        except Exception as exc:  # noqa: BLE001 — a bad anchor must not sink the manifest
            reason = (reason + f"; error:{exc}").strip("; ")
        anchors.append(
            Anchor(
                statute_id=statute_id,
                version_tag=ref.version_tag,
                locator=ref.locator,
                artifact_hash=artifact_hash,
                cnf_hash=cnf_hash,
                n_sections=n_sections,
                reason=reason,
            )
        )
    return StatuteManifest(statute_id=statute_id, anchors=tuple(anchors))


def build_manifest(statute_ids: list[str], *, archive: Any = None) -> dict[str, Any]:
    """Build a deterministic, diffable manifest document over *statute_ids*."""
    from farchive import Farchive

    close_after = False
    if archive is None:
        archive = Farchive(str(_default_db()))
        close_after = True
    try:
        statutes = {
            sid: build_statute_manifest(sid, archive=archive).to_dict()
            for sid in sorted(statute_ids)
        }
    finally:
        if close_after:
            archive.close()
    return {
        "schema": MANIFEST_SCHEMA,
        "jurisdiction": "united_kingdom",
        "statute_count": len(statutes),
        "statutes": statutes,
    }


# ---------------------------------------------------------------------------
# Per-act scoring (UK replay + UK eId text comparison → AnchorObservation chain)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _UKReplayScore:
    """The raw materials of one UK enacted→current replay comparison.

    The per-anchor maps are eId-PRESENCE maps (``{canonical_eid: canonical_eid}``) —
    UK's commensurable per-key surface is eId PRESENCE (normalized via
    ``normalize_uk_replay_compare_eids`` + the Roman↔Arabic ``_canonical_compare_index``),
    NOT the container-aggregated per-eId text (which is cross-normalizer-skewed and
    only ever an averaged scalar in ``uk-bench``, never a per-key binary verdict). See
    the module docstring "STRUCTURE-SIGNATURE ADAPTATION".
    """

    enacted_present: dict[str, str]
    replayed_present: dict[str, str]
    oracle_present: dict[str, str]
    comparison_class: str
    status: str


def _presence_map(compare_eids: set[str]) -> dict[str, str]:
    """A presence map ``{canonical_eid: canonical_eid}`` over a compare-eid set.

    Canonicalizes each compare-eId to its Roman↔Arabic-invariant form (UK's bench
    ``_canonical_compare_index``), so an old Act's ``section-II`` and the oracle's
    ``section-2`` are the SAME key. Value == key: the touch relation then fires iff
    an eId APPEARS or DISAPPEARS across a window (the wording/presence dimension),
    which is UK's commensurable per-key signal.
    """
    from lawvm.tools.uk_bench import _canonical_compare_index

    return {canon: canon for canon in _canonical_compare_index(compare_eids)}


def _score_uk_replay(statute_id: str, *, archive: Any) -> _UKReplayScore:
    """Replay *statute_id* enacted→current and gather the per-eId presence surfaces.

    Reuses the UK bench primitives exactly (parse → compile ops → replay ops → align
    to oracle → collect eIds → normalize compare eIds → canonical index), so the
    comparison is bench-identical. Returns the enacted, replayed, and oracle eId
    PRESENCE maps + the UK comparison_class (the oracle-suspect witness).
    """
    from lawvm.uk_legislation.oracle_align import align_uk_replay_to_oracle_with_report
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_bench_comparison,
        normalize_uk_replay_compare_eids,
        uk_prospective_only_presence_ambiguous_eids,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayPipeline,
        replay_uk_ops,
    )
    from lawvm.tools.uk_bench import (
        _collect_eids,
        _score_eids,
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )

    enacted_bytes = archive.get(enacted_url(statute_id))
    oracle_bytes = archive.get(current_url(statute_id))
    if not enacted_bytes:
        return _UKReplayScore({}, {}, {}, "no_enacted_eids", "NO_ENACTED")
    if not oracle_bytes:
        return _UKReplayScore({}, {}, {}, "no_oracle_eids", "ORACLE_CONTENT_ABSENT")

    enacted_ir = parse_uk_statute_ir_bytes(
        enacted_bytes,
        statute_id=statute_id,
        version_label="enacted",
        source_path=enacted_url(statute_id),
    )
    oracle_data = extract_eid_map_bytes(oracle_bytes)
    # The oracle eId set is the VALUES of eid_map (the actual eIds), NOT its keys
    # (which carry semantic-path + hash entries) — exactly as ``uk-bench`` derives it.
    oracle_eids = set(oracle_data.get("eid_map", {}).values())
    oracle_physical_eid_aliases = oracle_data.get("physical_eid_aliases", {})
    oracle_visible_number_eid_aliases = oracle_data.get("visible_number_eid_aliases", {})

    enacted_eids = _collect_eids(enacted_ir.body.children)
    for s in enacted_ir.supplements:
        enacted_eids.update(_collect_eids([s]))

    pipeline = UKReplayPipeline(_repo_root())
    effects = load_effects_for_statute_from_archive(statute_id, archive)
    ops = pipeline.compile_ops_for_statute(
        statute_id,
        archive=archive,
        preloaded_effects=effects or None,
    )
    replayed_ir = replay_uk_ops(
        enacted_ir,
        ops,
        eid_map=oracle_data.get("eid_map", {}),
        text_map=oracle_data.get("text_map", {}),
        allow_oracle_alignment=True,
    )
    alignment = align_uk_replay_to_oracle_with_report(
        replayed_ir,
        eid_map=oracle_data.get("eid_map", {}),
        text_map=oracle_data.get("text_map", {}),
    )
    replayed_ir = alignment.statute
    replayed_eids = _collect_eids(replayed_ir.body.children)
    for s in replayed_ir.supplements:
        replayed_eids.update(_collect_eids([s]))

    # Normalize each side's compare-eId set against the oracle for known compare-shape
    # noise (parent-path drift, display-number drift, non-legal fragment ids, ...) —
    # the same noise normalization ``uk-bench`` applies before scoring; plus the two
    # presence reconciliations (#211): whole-provision RetainText-repealed oracle
    # eIds accept the repeal-applied replay form, and eIds owned by prospective-only
    # effects accept either form (temporal application is PIT/editorial dependent).
    presence_ambiguous_eids = uk_prospective_only_presence_ambiguous_eids(
        effects,
        ops,
        enacted_statute=enacted_ir,
        replayed_statute=replayed_ir,
    )
    replay_compare, oracle_compare = normalize_uk_replay_compare_eids(
        replayed_eids,
        oracle_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
        oracle_retained_repeal_eids=oracle_data.get("retain_text_fully_repealed_eids", ()),
        presence_ambiguous_eids=presence_ambiguous_eids,
    )
    enacted_compare, _oracle_compare_b = normalize_uk_replay_compare_eids(
        enacted_eids,
        oracle_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )

    # UK comparison_class over the REPLAYED comparison (the oracle-suspect witness):
    # non-core ⇒ the anchor is commensurability-suspect (temporal_mismatch). This is
    # the exact class ``uk-bench`` computes over the same normalized sets.
    replay_score = _score_eids(replay_compare, oracle_compare)
    comparison_class = classify_uk_bench_comparison(
        n_enacted_eids=len(enacted_eids),
        n_oracle_eids=len(oracle_eids),
        n_effects=len(ops),
        raw_score=replay_score,
        effect_source_pathology_counts={},
    )
    return _UKReplayScore(
        enacted_present=_presence_map(enacted_compare),
        replayed_present=_presence_map(replay_compare),
        oracle_present=_presence_map(oracle_compare),
        comparison_class=comparison_class,
        status="OK",
    )


def _penalized_keys(replay_present: dict[str, str], oracle_present: dict[str, str]) -> set[str]:
    """The oracle eIds replay did not reproduce (the per-key presence divergence).

    An oracle eId is penalized iff it is absent from the normalized+canonical replay
    presence set. This is UK's own commensurable per-key predicate (the same
    normalized compare-eId surface ``uk-bench``'s ``replay_score`` runs over), so
    ``struct_sim`` is commensurable with the uk-bench headline.
    """
    return set(oracle_present) - set(replay_present)


def score_uk_anchors(statute_id: str, *, archive: Any) -> list[AnchorObservation]:
    """Score the enacted→current 2-node replay chain of *statute_id*.

    Returns two :class:`AnchorObservation`s (base first): the enacted base (its
    ``replay_text`` = enacted eId-presence map, seeding the touch relation) and the
    current anchor (``replay_text`` = replayed eId-presence map, scored against the
    oracle eId set). ``touch_set(enacted, current)`` is thus the eIds REPLAY added or
    removed applying the amendment chain — replay's own notion of what the
    amendments touched, in the same (presence) dimension as the divergence.
    """
    score = _score_uk_replay(statute_id, archive=archive)
    if score.status != "OK":
        # A single unscorable anchor; return one -1 anchor so the driver reports it.
        return [
            AnchorObservation(
                version_tag=_CURRENT_TAG,
                amendment_id=_CURRENT_TAG,
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

    # anchor[0] — the ENACTED base. It seeds the touch relation (its replay_text is
    # the enacted eId-presence map). It is NOT scored against the oracle in the chain
    # (base is replay's source, exactly like EE/FI skip re-scoring the base);
    # struct_sim = 1.0 so it participates as a scored chain node.
    base = AnchorObservation(
        version_tag=_ENACTED_TAG,
        amendment_id=_ENACTED_TAG,
        as_of=_ENACTED_TAG,
        struct_sim=1.0,
        n_sections=len(score.enacted_present),
        n_penalized=0,
        penalized_keys=frozenset(),
        replay_text=dict(score.enacted_present),
        oracle_suspect=None,
        status="BASE",
    )

    penalized = _penalized_keys(score.replayed_present, score.oracle_present)
    n_sections = len(score.oracle_present)
    struct_sim = 1.0 if not n_sections else 1.0 - len(penalized) / n_sections

    from lawvm.uk_legislation.source_adjudication import is_core_uk_comparison

    oracle_suspect = (
        None if is_core_uk_comparison(score.comparison_class) else score.comparison_class
    )

    current = AnchorObservation(
        version_tag=_CURRENT_TAG,
        amendment_id=_CURRENT_TAG,
        as_of=_CURRENT_TAG,
        struct_sim=struct_sim,
        n_sections=n_sections,
        n_penalized=len(penalized),
        penalized_keys=frozenset(penalized),
        replay_text=dict(score.replayed_present),
        oracle_suspect=oracle_suspect,
        status="OK",
        # UK's per-key surface is eId PRESENCE (there is no byte-exact structure-only
        # sub-verdict): every UK penalized eId is a presence/wording divergence, so the
        # wording-level (presence) touch relation governs (mirrors EE's documented
        # choice for its byte-exact model).
        structural_only_penalized_keys=frozenset(),
    )
    return [base, current]


# ---------------------------------------------------------------------------
# Statute-level driver: enumerate + score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    statute_id: str
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


def attribute_statute(statute_id: str, *, archive: Any = None) -> StatuteAttribution:
    """Score the enacted→current chain of *statute_id*, then attribute + gate.

    The attribution calculus (Finland's neutral ``attribute_divergences``) runs over
    the chronological scored anchor list ``[enacted, current]``. A ``current``
    divergence over an eId replay TOUCHED (its enacted→replayed text moved) that
    stays diverged is a candidate replay bug; a divergence over an untouched eId is
    oracle-side (standing untouched).
    """
    from farchive import Farchive

    close_after = False
    if archive is None:
        archive = Farchive(str(_default_db()))
        close_after = True
    try:
        anchors = score_uk_anchors(statute_id, archive=archive)
        scored = [a for a in anchors if a.struct_sim >= 0.0]
        if len(scored) < 2:
            return StatuteAttribution(
                statute_id=statute_id,
                anchors=tuple(anchors),
                observations=(),
                status="ERROR:fewer-than-2-scorable-anchors",
            )
        observations = attribute_divergences(statute_id, anchors)
        return StatuteAttribution(
            statute_id=statute_id,
            anchors=tuple(anchors),
            observations=tuple(observations),
        )
    finally:
        if close_after:
            archive.close()


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the shared taxonomy + FI verdict maps)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: TouchObservation) -> AgreementResidual:
    """Project one UK touch observation into the shared AgreementResidual taxonomy.

    Reuses Finland's ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` (the verdict→
    family mapping is jurisdiction-neutral), stamped with the UK jurisdiction.
    """
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"uk:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="united_kingdom",
        agreement_surface="uk_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="uk_bench.anchor.touch_relation",
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
# CLI: attribute UK acts
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.uk_anchor_manifest",
        description="Run the UK touch-relation attribution engine over one or more "
        "legislation.gov.uk acts (e.g. ukpga/2010/15), the #183/#205 metric.",
    )
    parser.add_argument("statute_ids", nargs="+")
    args = parser.parse_args(argv)

    rc = 0
    for sid in args.statute_ids:
        attr = attribute_statute(sid)
        if attr.status != "OK":
            print(f"\n=== {sid} === {attr.status}", file=sys.stderr)
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(
            f"\n=== {sid}  ({len(attr.scored)} scored anchors)  [{gate}] ==="
        )
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
