"""nz_anchor_manifest.py — New Zealand's frozen content-addressed anchor +
touch-relation attribution engine (#183/#205, FOURTH jurisdiction).

This is the NZ analogue of :mod:`lawvm.tools.ee_anchor_manifest` (Estonia),
:mod:`lawvm.tools.uk_anchor_manifest` (United Kingdom), and
:mod:`lawvm.tools.fi_anchor_manifest` (Finland), extending the drift-robust #183
metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to a fourth
jurisdiction. It is ADDITIVE: it never mutates the NZ corpus, replay, or the
existing ``nz-bench`` scoring; the default NZ bench + chain replay stay
byte-identical (it consumes them read-only).

WHAT A NZ ANCHOR IS (the jurisdiction adaptation — and why it is the RICHEST
anchor surface of any jurisdiction). Finland enumerates the published
*consolidation snapshots* of one statute over its life (``plan_snapshots``);
Estonia enumerates the Riigi Teataja *terviktekst* chain per ``grupi_id`` (each
carrying a ``kehtivuseAlgus`` effective date); the UK has only a 2-node
enacted→current window (it publishes no dated PIT chain). New Zealand is the
RICHEST: legislation.govt.nz archives a dense chain of DATED point-in-time
consolidated XML versions per work (``archived_xml_versions_for_work`` —
2007-09-03 onward, often many dozens of dated snapshots). EACH dated consolidated
snapshot is a genuine content-addressed ANCHOR (the consolidated text of the act
at that publication date), exactly analogous to FI's consolidation snapshots and
EE's terviktekst chain — the cleanest multi-anchor legal-time surface of any
frontend.

    * anchor[0] = the EARLIEST archived version (the replay BASE — NZ archives PIT
      XML from 2007 onward, so pre-2007 amendments are already baked into it).
    * anchor[k] = the k-th archived version (its ``as_of`` = the version date). The
      NZ chain replay (:mod:`lawvm.new_zealand.chain_replay`) carries a SINGLE
      evolving tree base→latest applying every authorized amendment op forward; at
      each archived version the evolving (replayed) tree is materialized and its
      per-node cleaned text is scored against that version's archived oracle. This
      is the ``base → as_of`` replay EE/FI score, evaluated at every dated anchor.

    The touch relation ``touch_set(anchor_{k-1}, anchor_k)`` is then, faithfully,
    the set of units REPLAY changed applying the amendments effective in that
    window — replay's own notion of "what the intervening amendments touched",
    derived from the two materialized trees exactly as FI derives it from adjacent
    snapshots. This makes the SAME-DIMENSION-TOUCH principle bind on NZ: a
    divergence at ``anchor_k`` over a unit REPLAY touched (changed/added/removed in
    the window) that stays diverged is a candidate REPLAY BUG (billable); a
    divergence over a unit replay NEVER touched is a standing untouched divergence
    → oracle-side (non-billable). The convicting touch is in the same (per-node
    text) dimension as the divergence — the engine's invariant.

WHAT REPLAY IS SCORED AGAINST. For each act we run the NZ chain replay pipeline
(:func:`build_archived_work_chain_replay`), which materializes the evolving tree at
each archived version and parses that version's archived consolidated oracle. We
compare the replayed per-node cleaned similarity text against the oracle's on the
STABLE-PATH surface (``_stable_path`` — collapsing positional/identity path churn),
the same surface the chain replay's own ``combined_similarity_stable`` headline
runs over, so a penalized unit is commensurable with the nz-bench chain headline.

ORACLE-SUSPECT DISCIPLINE (reused, first-class — and the honest floor of NZ's
EXPERIMENTAL PARTIAL-COVERAGE surface). The NZ chain replay is explicitly a
DRY-RUN, PARTIAL-COVERAGE surface (``replay_claims == False``,
``NZ_CHAIN_REPLAY_TRUTH_CLAIM``): most oracle units are NEVER touched by the
partial op set, so the vast majority of non-reproduced units are standing-untouched
and type to oracle_suspect by the touch relation itself (never a forced replay
bug). We do NOT convict a divergence unless REPLAY touched the unit in the same
dimension — this is precisely the governing principle (project memory
``reference_authoritative_oracle_not_correct``: authoritative oracle ≠ correct; a
touch-free divergence is oracle-side). NZ's own op-LOCAL divergence guard
(``NZChainDivergence`` / ``_op_local_divergence``: a content-producing op that
yields a node the on-or-after oracle contradicts) is NZ's authoritative "wrong op"
signal; a work carrying such a divergence is a candidate real replay bug and is
EXCLUDED from the curated corpus (like EE #208 / UK #209), never frozen green.

STRUCTURE-SIGNATURE ADAPTATION (documented, per task — and WHY NZ differs). EE's
section comparison is byte-exact; FI's is structure-aware over
``extract_ir_sections``; UK's per-key surface is eId presence. NZ is DIFFERENT and
the adaptation is deliberate:

    NZ's IR is the flat ``NZSourceDocument.nodes`` tuple of ``NZSourceNode`` (each
    a ``(kind, path, heading, text, deletion_status)`` unit), not a nested
    LawVM-IR ``extract_ir_sections`` tree. So we adapt the structure signature to
    NZ's IR directly (per task ``_walk_source_nodes`` — NZ's own tree walk): a NZ
    unit's structure signature is the ordered ``(depth, kind, label)`` of the unit
    plus every node nested under its stable path, i.e. the wording-independent
    SHAPE of the subtree rooted at that unit. Two renderings with identical nesting
    but different wording share a signature; a re-nesting (a paragraph split/merge,
    an item added/removed) changes it. This is the exact FI/EE
    ``_ir_structure_signature`` semantics (ordered depth/kind/label, no wording),
    re-expressed over NZ's flat-node model, so the structural touch relation
    behaves identically.

    We do NOT populate ``structural_only_penalized_keys`` — NZ's per-node
    divergence surface is a continuous text-similarity verdict (there is no
    byte-exact structure-only sub-verdict), so the wording-level (per-node text)
    touch relation governs, mirroring EE's and UK's documented choice. This
    PRESERVES the same-dimension-touch principle: the convicting touch (a unit
    whose replayed text moved) is in the same dimension as the divergence (a unit
    whose replayed text disagrees with the oracle).

REUSED NEUTRAL CORE. The touch relation itself is jurisdiction-neutral: this module
imports :class:`fi_anchor_manifest.AnchorObservation`, :class:`TouchObservation`,
:func:`attribute_divergences`, :func:`touch_set`, :func:`structure_touch_set`, and
the ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` maps unchanged (they take a
``sid`` string and operate on generic replay text/structure maps — nothing
Finland-specific). The shared taxonomy
(:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused as-is. Only
the anchor *enumeration* (the dated archived-version chain), *scoring* (NZ chain
replay + NZ per-node cleaned-text comparison), and the content-addressing are
NZ-specific.
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
# These operate on a ``sid`` string + generic replay text/structure maps; none of
# them import ``lawvm.finland``. Re-exported here so NZ callers have a single
# surface (the parent ctsf_gate integration imports ``_VERDICT_TO_FAMILY`` +
# ``attribute_statute`` from here, exactly as it does for EE/UK).
from lawvm.tools.fi_anchor_manifest import (
    _VERDICT_TO_FAMILY,
    _VERDICT_TO_STATUS,
    AnchorObservation,
    TouchObservation,
    attribute_divergences,
)


MANIFEST_SCHEMA = "lawvm.nz_anchor_manifest.v1"

_DEFAULT_DB = Path("data/nz_legislation.farchive")

# A unit's replayed text must reproduce the oracle unit's text to at least this
# cleaned-similarity ratio to count as MATCHED at that anchor. Below it (or an
# oracle unit absent from the replay's stable-path set) the unit is PENALIZED.
# Set to the chain-replay's own per-op oracle-agreement threshold so the anchor
# penalty is commensurable with the nz-bench chain agreement signal.
_UNIT_MATCH_THRESHOLD = 0.9


def _default_db() -> Path:
    return _DEFAULT_DB


# ---------------------------------------------------------------------------
# NZ anchor enumeration (the dated archived-version chain of one work)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NZAnchorRef:
    """One dated archived consolidated version of a NZ work (a chain node).

    ``version_id`` is the archived version id (the anchor's content id);
    ``as_of`` is its ``version_date`` (the publication date the anchor renders);
    ``xml_locator`` is the archive locator; ``has_body`` is False when the archive
    lacks parseable body bytes (not scorable).
    """

    work_id: str
    version_id: str
    as_of: str
    xml_locator: str
    has_body: bool

    @property
    def scorable(self) -> bool:
        return self.has_body and bool(self.as_of)


def enumerate_nz_anchors(work_id: str, *, archive: Any) -> list[NZAnchorRef]:
    """Enumerate the dated archived-version chain of *work_id*, oldest-first.

    Returns every archived consolidated XML version of the work, chronologically
    (the order the touch relation requires). This is the NZ analogue of FI's
    ``plan_snapshots`` / EE's terviktekst chain: the published dated consolidation
    chain of one act. ``archived_xml_versions_for_work`` is newest-first; we
    reverse into ascending (base-first) order.
    """
    from lawvm.new_zealand.version_diff import archived_xml_versions_for_work

    versions = archived_xml_versions_for_work(archive, work_id)
    refs: list[NZAnchorRef] = []
    for version in reversed(versions):  # newest-first → oldest-first
        data = archive.get(version.xml_locator) if version.xml_locator else None
        refs.append(
            NZAnchorRef(
                work_id=work_id,
                version_id=version.version_id,
                as_of=version.version_date,
                xml_locator=version.xml_locator,
                has_body=bool(data and len(data) > 100),
            )
        )
    return refs


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest (mirrors ee_anchor_manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed dated NZ consolidated snapshot.

    ``artifact_hash`` pins the RAW published legislation.govt.nz XML bytes
    (immutability check — any re-edit changes it). ``cnf_hash`` pins the
    per-unit cleaned-text map, so an editorial-only refresh moves ``artifact_hash``
    but leaves ``cnf_hash`` stable.
    """

    work_id: str
    version_id: str
    as_of: Optional[str]
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "as_of": self.as_of,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    work_id: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "anchors": [a.to_dict() for a in self.anchors],
        }


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# NZ IR structure signature (task: adapt to NZ's flat-node IR via _walk_source_nodes)
# ---------------------------------------------------------------------------


def _stable_key(path: tuple[str, ...]) -> tuple[str, ...]:
    """Collapse positional/identity path churn to bare kinds (the stable surface).

    Reuses the chain replay's own ``_stable_path`` semantics: NZ's parser falls
    back to ``kind#ordinal`` (positional) or ``kind@xml_id`` (identity) when a node
    has no stable label, and those segments churn across consolidations even with
    zero real change. Collapsing them lets the anchor keys compare on the same
    stable surface the chain replay's ``combined_similarity_stable`` headline uses.
    """
    from lawvm.new_zealand.chain_replay import _stable_path

    return _stable_path(path)


def _cleaned_unit_text(node: Any) -> str:
    """The cleaned per-unit legal-text surface (heading + body + deletion marker).

    Reuses the chain replay's own ``_cleaned_node_similarity_text`` so a penalized
    unit is scored on the byte-identical text the chain headline scores.
    """
    from lawvm.new_zealand.chain_replay import _cleaned_node_similarity_text

    return _cleaned_node_similarity_text(node)


def _document_text_map(document: Any) -> dict[str, str]:
    """Per-stable-key cleaned-text map of a NZ source document.

    Keys are stable paths (``_stable_key``); the value is the cleaned text of the
    first node at that stable key (document order preserved). Multiple anonymous
    siblings under one stable key collapse to the first — the same slight
    under-count the chain replay's stable track accepts, with the exact per-node
    signal kept below in the structure map.
    """
    out: dict[str, str] = {}
    for node in getattr(document, "nodes", ()):
        key = "/".join(_stable_key(node.path))
        out.setdefault(key, _cleaned_unit_text(node))
    return out


def _nz_unit_structure_signature(document: Any, unit_stable_key: str) -> str:
    """Wording-independent nesting signature of the subtree rooted at a NZ unit.

    Adapts FI/EE's ``_ir_structure_signature`` (ordered ``(depth, kind, label)`` of
    every descendant, NO wording) to NZ's flat ``NZSourceDocument.nodes`` model: we
    walk the document's nodes (``_walk_source_nodes``' output — the flat tuple),
    select the unit's own node plus every node whose stable path is nested under
    it, and encode each as ``depth:kind:label`` (depth relative to the unit). Two
    renderings with identical nesting but different wording share a signature; a
    paragraph split/merge or item add/remove changes it — the exact structural
    touch semantics the neutral core expects, re-expressed over NZ's IR.
    """
    unit_segments = tuple(unit_stable_key.split("/")) if unit_stable_key else ()
    depth0 = len(unit_segments)
    parts: list[str] = []
    for node in getattr(document, "nodes", ()):
        skey = _stable_key(node.path)
        if skey[:depth0] != unit_segments:
            continue
        depth = len(skey) - depth0
        label = node.label or ""
        parts.append(f"{depth}:{node.kind}:{label}")
    return "|".join(parts)


def _document_structure_map(document: Any) -> dict[str, str]:
    """Per-stable-key structure signature of a NZ source document."""
    keys = {"/".join(_stable_key(node.path)) for node in getattr(document, "nodes", ())}
    return {key: _nz_unit_structure_signature(document, key) for key in sorted(keys)}


# ---------------------------------------------------------------------------
# Per-anchor scoring (NZ chain replay materialization → AnchorObservation chain)
# ---------------------------------------------------------------------------


def _unit_matches(replay_text: str, oracle_text: str) -> bool:
    """A replayed unit matches the oracle unit iff their cleaned texts are close.

    Reuses the chain replay's continuous ``section_similarity_cleaned`` verdict
    (already-cleaned strings) at :data:`_UNIT_MATCH_THRESHOLD`, so the anchor
    penalty is commensurable with the nz-bench chain agreement signal. Two empty
    (both-absent) texts trivially match.
    """
    from lawvm.core.evidence_support import section_similarity_cleaned

    if replay_text == oracle_text:
        return True
    if not oracle_text:
        # Oracle carries no text at this unit; a replay unit with text is not a
        # penalty against the oracle (the oracle simply lacks it — untouched-side).
        return True
    return section_similarity_cleaned(replay_text, oracle_text) >= _UNIT_MATCH_THRESHOLD


@dataclass(frozen=True)
class _NZAnchorScore:
    """The materialized replay tree + parsed oracle at one archived version."""

    version_id: str
    as_of: str
    replay_text: dict[str, str]
    replay_structure: dict[str, str]
    oracle_text: dict[str, str]
    # The stable-keys NZ's OWN authoritative op-LOCAL divergence detector
    # (:func:`_op_local_divergence`) has convicted at or before this version — a
    # content-producing op that yielded a node the on-or-after oracle contradicts.
    # This is NZ's canonical "replay got this unit WRONG" witness (same-dimension),
    # distinct from partial-coverage lag; the anchor's billable divergence set is
    # gated on it (see ``score_nz_anchors``).
    op_local_divergent_keys: frozenset[str]


def score_nz_anchors(work_id: str, *, archive: Any = None) -> list[AnchorObservation]:
    """Score the dated archived-version chain of *work_id* into anchor observations.

    Runs the NZ chain replay (all families) with a single evolving tree, capturing
    the materialized replayed tree + the parsed oracle at EACH archived version.
    Each version becomes an :class:`AnchorObservation` (base first): the base
    anchor seeds the touch relation (its ``replay_text`` = the base replayed tree's
    per-unit text), and each later anchor is scored (``penalized_keys`` = oracle
    units the replay did not reproduce on the stable-path surface).

    ``touch_set(anchor_{k-1}, anchor_k)`` is thus the units REPLAY changed applying
    the window's amendments — replay's own notion of what the amendments touched, in
    the same (per-node text) dimension as the divergence.
    """
    scores = _materialize_anchor_scores(work_id, archive=archive)
    if len(scores) < 2:
        return [
            AnchorObservation(
                version_tag=(scores[0].version_id if scores else work_id),
                amendment_id=work_id,
                as_of=None,
                struct_sim=-1.0,
                n_sections=0,
                n_penalized=0,
                penalized_keys=frozenset(),
                replay_text={},
                oracle_suspect=None,
                status="ERROR:fewer-than-2-archived-versions",
            )
        ]

    anchors: list[AnchorObservation] = []
    for idx, sc in enumerate(scores):
        is_base = idx == 0
        # Penalized: an oracle unit the replay did not reproduce (absent or below
        # the match threshold) on the stable-path surface. The base anchor is
        # replay's own source (replay@base = the earliest snapshot itself), so — as
        # EE/FI do — it is not re-scored against the oracle; it only seeds the touch
        # relation. struct_sim=1.0 so it participates as a scored chain node.
        if is_base:
            anchors.append(
                AnchorObservation(
                    version_tag=sc.version_id,
                    amendment_id=work_id,
                    as_of=sc.as_of,
                    struct_sim=1.0,
                    n_sections=len(sc.replay_text),
                    n_penalized=0,
                    penalized_keys=frozenset(),
                    replay_text=dict(sc.replay_text),
                    oracle_suspect=None,
                    status="BASE",
                    replay_structure=dict(sc.replay_structure),
                    structural_only_penalized_keys=frozenset(),
                )
            )
            continue

        # COMMENSURABILITY (the crux of NZ's EXPERIMENTAL PARTIAL-COVERAGE surface,
        # per project memory ``reference_authoritative_oracle_not_correct`` +
        # ``reference_consolidation_editorial_1d_artifacts``). NZ chain replay is a
        # dry-run PARTIAL-COVERAGE surface (``replay_claims == False``): the replay
        # tree is systematically BEHIND the oracle wherever an op was skipped /
        # pre-2007-baked / out of the covered families. Two kinds of oracle-vs-replay
        # per-unit disagreement result, and ONLY one is a replay bug:
        #
        #   (a) COVERAGE LAG — replay produced a unit but never applied the amendment
        #       that would have updated it (the op is a typed skip / uncovered), so
        #       replay's older text lags the oracle's amended text. This is the
        #       partial-coverage FLOOR, an oracle-vs-coverage gap, NOT a wrong op.
        #   (b) A WRONG OP — a content-producing op replay DID apply produced a node
        #       the on-or-after oracle contradicts. This is NZ's authoritative,
        #       same-dimension "replay got this unit wrong" witness, detected by
        #       :func:`_op_local_divergence` (``op_local_divergent_keys``).
        #
        # A faithful metric must convict (b) and never (a). The penalized set is the
        # units replay PRODUCED that disagree with the oracle (a real, checkable
        # claim mismatch — an oracle-absent unit is oracle-side, dropped). These
        # feed the touch relation as divergences to be TYPED. What keeps coverage
        # lag from forging a replay bug is the per-ANCHOR commensurability witness
        # (``oracle_suspect``, mirroring FI's ``get_consolidated_oracle_suspect`` /
        # EE's ``source_adjudication.oracle_suspect``): an anchor whose divergences
        # are pure coverage lag — i.e. it carries NO oracle-present, op-local-
        # convicted unit — is commensurability-suspect for THIS partial-coverage
        # surface, so ALL its divergences type to ``temporal_mismatch_commensurability``
        # (non-billable). An anchor that DOES carry an oracle-present op-local-
        # convicted unit (NZ's authoritative same-dimension wrong-op witness) is NOT
        # suspect, so the touch relation is free to convict a genuine replay bug
        # there. This is the same-dimension-touch principle: a divergence is billable
        # only when NZ's own wrong-op detector localizes a produced unit at the
        # anchor AND the touch relation attributes it to a replay touch.
        penalized: set[str] = set()
        for key, replay_unit_text in sc.replay_text.items():
            oracle_unit_text = sc.oracle_text.get(key)
            if oracle_unit_text is None:
                # Replay produced a unit the oracle lacks at this stable key. The
                # oracle routinely drops repealed/renumbered units (editorial
                # consolidation), so an absent oracle unit is oracle-side, not a
                # replay claim mismatch — mirror the chain replay's tombstone-
                # agreement rule (oracle absence is agreement, not divergence).
                continue
            if not _unit_matches(replay_unit_text, oracle_unit_text):
                penalized.add(key)

        # The per-anchor commensurability witness. A penalized unit is COVERAGE LAG
        # unless NZ's authoritative op-local detector convicted it (an oracle-present
        # produced unit the on-or-after oracle contradicts). An anchor carrying any
        # coverage-lag divergence is commensurability-limited for this partial-
        # coverage dry-run surface: its divergences are dominated by skipped/uncovered
        # ops the oracle already reflects, not by wrong ops, so they type to
        # ``temporal_mismatch_commensurability`` (non-billable), never a forced replay
        # bug. An anchor with NO coverage-lag divergence (either fully clean, or its
        # only divergences are op-local-convicted genuine wrong-ops) is commensurable:
        # it is NOT suspect, so the neutral touch relation runs and can convict a real
        # replay bug (op-local-convicted, touched, persistent) or type a genuine
        # spontaneous oracle change as ``oracle_editorial_pathology``.
        coverage_lag = penalized - set(sc.op_local_divergent_keys)
        oracle_suspect = (
            "nz_partial_coverage_dry_run_commensurability_limited"
            if coverage_lag
            else None
        )

        # struct_sim is over the COMMENSURABLE surface (units replay produced that the
        # oracle also carries) — the surface on which replay makes a checkable claim.
        commensurable = sum(
            1 for k in sc.replay_text if sc.oracle_text.get(k) is not None
        )
        struct_sim = 1.0 if not commensurable else 1.0 - len(penalized) / commensurable
        n_sections = commensurable

        anchors.append(
            AnchorObservation(
                version_tag=sc.version_id,
                amendment_id=work_id,
                as_of=sc.as_of,
                struct_sim=struct_sim,
                n_sections=n_sections,
                n_penalized=len(penalized),
                penalized_keys=frozenset(penalized),
                replay_text=dict(sc.replay_text),
                oracle_suspect=oracle_suspect,
                status="OK",
                replay_structure=dict(sc.replay_structure),
                # NZ's per-unit surface is a continuous text-similarity verdict (no
                # byte-exact structure-only sub-verdict), so the wording-level
                # (per-node text) touch relation governs — mirrors EE/UK.
                structural_only_penalized_keys=frozenset(),
            )
        )
    return anchors


def _materialize_anchor_scores(
    work_id: str, *, archive: Any = None
) -> list[_NZAnchorScore]:
    """Run the NZ chain replay and capture the materialized tree + oracle per version.

    This re-uses the chain replay's exact driver primitives (enumerate transitions,
    carry a single evolving tree, apply each transition, parse each archived
    version's oracle) but, instead of only recording the similarity curve, it
    snapshots the per-unit cleaned-text + structure maps of BOTH the materialized
    replayed tree and the parsed oracle at each archived version — the raw material
    for the anchor-touch attribution. Byte-identical replay to the chain headline
    (same op ordering, same apply kernel, same version walk).
    """
    from lawvm.new_zealand.acquisition import open_farchive
    from lawvm.new_zealand.chain_replay import (
        _ALL_FAMILIES,
        _apply_transition,
        _base_work_year_number,
        _EvolvingTree,
        _op_local_divergence,
        _parse_archived_version,
        build_nz_chain,
    )
    from lawvm.new_zealand.corpus_cache import corpus_run_cache
    from lawvm.new_zealand.effect_candidates import (
        build_archived_work_effect_candidate_preflight,
    )
    from lawvm.new_zealand.operation_surface import (
        build_archived_work_operation_surface,
    )
    from lawvm.new_zealand.version_diff import archived_xml_versions_for_work

    close_after = False
    db_path = _default_db()
    if archive is None:
        archive = open_farchive(db_path)
        close_after = True
    try:
        with corpus_run_cache():
            surface = build_archived_work_operation_surface(db_path, work_id)
            preflight = build_archived_work_effect_candidate_preflight(
                db_path, work_id, operation_surface=surface
            )
            transitions = build_nz_chain(preflight, surface, families=_ALL_FAMILIES)

            versions = archived_xml_versions_for_work(archive, work_id)
            versions_asc = tuple(reversed(versions))
            if not versions_asc:
                return []

            parsed_cache: dict[str, Any] = {}
            amending_root_cache: dict[str, Any] = {}
            base_work_year, base_work_number = _base_work_year_number(work_id)
            base_doc = _parse_archived_version(archive, versions_asc[0], parsed_cache)
            if base_doc is None:
                return []
            latest_version_date = versions_asc[-1].version_date

            tree = _EvolvingTree(document=base_doc)
            # Accumulated authoritative op-local divergent stable-keys (NZ's own
            # wrong-op witness). A content-producing op that yields a node the
            # on-or-after oracle contradicts adds that node's stable key here; it
            # then gates the anchor billable set (only op-local-convicted produced
            # units are scored divergences — coverage lag is not).
            op_local_divergent_keys: set[str] = set()

            def _run_transition(transition: Any) -> None:
                _applied, _skips, applied_ops = _apply_transition(
                    tree,
                    transition,
                    latest_version_date=latest_version_date,
                    archive=archive,
                    amending_root_cache=amending_root_cache,
                    base_work_year=base_work_year,
                    base_work_number=base_work_number,
                )
                for applied_op in applied_ops:
                    if applied_op.family == "repeal":
                        continue
                    divergence = _op_local_divergence(
                        tree.document, applied_op, versions_asc, archive, parsed_cache
                    )
                    if divergence is not None:
                        op_local_divergent_keys.add(
                            "/".join(_stable_key(divergence.target_path))
                        )

            scores: list[_NZAnchorScore] = []
            transition_cursor = 0
            for version in versions_asc:
                while (
                    transition_cursor < len(transitions)
                    and transitions[transition_cursor].amendment_date_iso
                    <= version.version_date
                ):
                    _run_transition(transitions[transition_cursor])
                    transition_cursor += 1

                oracle_doc = _parse_archived_version(archive, version, parsed_cache)
                if oracle_doc is None:
                    continue
                scores.append(
                    _NZAnchorScore(
                        version_id=version.version_id,
                        as_of=version.version_date,
                        replay_text=_document_text_map(tree.document),
                        replay_structure=_document_structure_map(tree.document),
                        oracle_text=_document_text_map(oracle_doc),
                        op_local_divergent_keys=frozenset(op_local_divergent_keys),
                    )
                )
            return scores
    finally:
        if close_after:
            archive.close()


# ---------------------------------------------------------------------------
# Statute-level driver: enumerate + score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    work_id: str
    base_version_id: str
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
    def has_hidden_mid_life_divergence(self) -> bool:
        mn, latest = self.min_over_life, self.latest_scored
        return mn is not None and latest is not None and mn < latest - 1e-9

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


def attribute_statute(work_id: str, *, archive: Any = None) -> StatuteAttribution:
    """Score the dated archived-version chain of *work_id*, then attribute + gate.

    The base anchor (earliest archived version) is replay's own source; the chain of
    scored windows starts at the first NON-base anchor. The attribution calculus
    (Finland's neutral ``attribute_divergences``) runs over the chronological scored
    anchor list. A divergence over a unit replay TOUCHED (its per-node text moved)
    that stays diverged is a candidate replay bug; a divergence over an untouched
    unit is oracle-side (standing untouched).
    """
    from lawvm.new_zealand.acquisition import open_farchive

    close_after = False
    if archive is None:
        archive = open_farchive(_default_db())
        close_after = True
    try:
        anchors = score_nz_anchors(work_id, archive=archive)
        scored = [a for a in anchors if a.struct_sim >= 0.0]
        if len(scored) < 2:
            return StatuteAttribution(
                work_id=work_id,
                base_version_id=anchors[0].version_tag if anchors else "",
                anchors=tuple(anchors),
                observations=(),
                status="ERROR:fewer-than-2-scorable-anchors",
            )
        observations = attribute_divergences(work_id, anchors)
        return StatuteAttribution(
            work_id=work_id,
            base_version_id=anchors[0].version_tag,
            anchors=tuple(anchors),
            observations=tuple(observations),
        )
    finally:
        if close_after:
            archive.close()


# ---------------------------------------------------------------------------
# The REAL #183 touch-relation corpus — NEW ZEALAND (task #205, the RICHEST anchor
# surface). Each is a real legislation.govt.nz act with a genuine dated-snapshot
# amendment chain that replays 0-BILLABLE (no replay_bug/unknown) — the honest
# steady state, mirroring FI/EE/UK. NZ acts whose chain replay surfaces a GENUINE
# op-local wrong-op (an oracle-present produced unit the on-or-after oracle
# contradicts, NZ's authoritative same-dimension witness) are DELIBERATELY EXCLUDED:
# those are real defects to fix, not to freeze — leaving them convicting is the point
# of the metric. See the deliverable report for the itemized excluded/found list.
# ---------------------------------------------------------------------------

REAL_ANCHOR_NZ_JURISDICTION = "new_zealand"

# The frozen, content-pinned NZ work-id corpus (sorted, explicit — membership is part
# of the frozen input). Chosen for dated-snapshot diversity (2–23 archived versions)
# across the repeal/text_replace/replace/insert families; annotated with the residual
# family it contributes at freeze time so the coverage is auditable.
REAL_ANCHOR_NZ_CORPUS_SIDS: tuple[str, ...] = (
    "act_public_1871_23",  # temporal_mismatch(3) — 2 dated snapshots, repeal chain
    "act_public_1872_13",  # scored clean (0 obs) — 2 dated snapshots
    "act_public_1875_34",  # temporal_mismatch(3) — 2 dated snapshots
    "act_public_1955_37",  # temporal_mismatch(266) — 14 dated snapshots (rich chain)
    "act_public_1981_23",  # temporal_mismatch(112) — 23 dated snapshots (richest chain;
    #                        exercises the op-local wrong-op witness path — its 2
    #                        op-local divergences are oracle-ABSENT path-shape artefacts,
    #                        not genuine wrong-ops, so it stays 0-billable)
    "act_public_1993_110",  # temporal_mismatch(1) — 2 dated snapshots
    "act_public_2005_87",  # temporal_mismatch(13) — 4 dated snapshots
    "act_public_2009_13",  # temporal_mismatch(124) — 5 dated snapshots
    "act_public_2009_38",  # temporal_mismatch(1) — 2 dated snapshots
)

# The committed NZ baseline artifact (frozen, sibling of the FI/EE/UK ones).
GATE_NZ_BASELINE_PATH = Path("tests/data/ctsf_gate_nz_residual_baseline.json")


def nz_anchor_corpus_available() -> bool:
    """True iff the NZ legislation Farchive backing the real corpus is present.

    Scoring the real corpus runs the NZ chain replay per act, which reads the NZ
    Farchive. When it is absent (a corpus-free CI checkout) the real-corpus tests
    SKIP; the unit surface (diff logic, synthetic injection, baseline round-trip) is
    corpus-free and always runs.
    """
    return _default_db().exists()


@memoize_default_corpus
def score_nz_real_corpus(
    sids: Any = None,
) -> dict[str, dict[str, int]]:
    """Score the NZ #183 touch-relation corpus into its typed-residual set.

    For each work_id, run the ``nz_anchor_manifest`` attribution engine over its dated
    archived-version anchors and project each ``TouchObservation`` into its CTSF
    residual family (the shared ``_VERDICT_TO_FAMILY``). Returns the same diffable
    ``{sid: {family: count}}`` shape as the FI/EE/UK real-corpus scorers — only
    non-zero families retained, a clean-but-scored work present with an empty family
    map. Deterministic in sid order.

    Reads the NZ Farchive (per-anchor chain replay). Deterministic given the frozen
    corpus bytes; NOT the wall-clock-free path — same as the FI/EE/UK real corpora.
    """
    from lawvm.new_zealand.acquisition import open_farchive

    corpus_sids = tuple(sids) if sids is not None else REAL_ANCHOR_NZ_CORPUS_SIDS
    # Open ONE archive handle for the whole corpus so each act's chain replay reuses
    # a single farchive handle (its own run cache is activated per act inside).
    archive = open_farchive(_default_db())
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
# AgreementResidual projection (reuse the shared taxonomy + FI verdict maps)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: TouchObservation) -> AgreementResidual:
    """Project one NZ touch observation into the shared AgreementResidual taxonomy.

    Reuses Finland's ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` (the verdict→
    family mapping is jurisdiction-neutral), stamped with the NZ jurisdiction.
    """
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"nz:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="new_zealand",
        agreement_surface="nz_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="nz_bench.anchor.touch_relation",
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
# CLI: attribute NZ acts
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.nz_anchor_manifest",
        description="Run the NZ touch-relation attribution engine over one or more "
        "legislation.govt.nz acts (e.g. act_public_1957_87), the #183/#205 metric.",
    )
    parser.add_argument("work_ids", nargs="+")
    args = parser.parse_args(argv)

    rc = 0
    for work_id in args.work_ids:
        attr = attribute_statute(work_id)
        if attr.status != "OK":
            print(f"\n=== {work_id} === {attr.status}", file=sys.stderr)
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(
            f"\n=== {work_id}  ({len(attr.scored)} scored anchors)  [{gate}] ==="
        )
        print(
            f"  min-over-life={100 * (attr.min_over_life or 0):.2f}%  "
            f"latest={100 * (attr.latest_scored or 0):.2f}%  "
            f"hidden-mid-life={'YES' if attr.has_hidden_mid_life_divergence else 'no'}"
        )
        for o in attr.observations:
            print(f"  {o.section_key:<28} {o.verdict}  window={o.window}")
        if not attr.is_gated_clean:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
