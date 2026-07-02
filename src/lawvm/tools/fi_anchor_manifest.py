"""fi_anchor_manifest.py — frozen content-addressed anchor manifests + the
touch-relation attribution engine (#183).

This is the drift-robust metric successor described in
``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4. It is ADDITIVE: it
never mutates the corpus, replay, or the existing bench scoring; the default
bench modes stay byte-identical.

Three capabilities, layered:

1. **Content-addressed anchor manifest** (§3.1). For each statute, the set of
   published consolidation snapshots ("anchors") is pinned to a deterministic,
   diffable manifest keyed by ``version_tag`` and carrying, per anchor:
   ``as_of`` (derived exactly as :mod:`fi_aux_pit_probe` derives it), an
   ``artifact_hash`` (sha256 of the raw published XML bytes — detects ANY
   Finlex re-edit, the immutability guarantee), and a ``cnf_hash`` (sha256 over
   the normative-projected section map — an editorial-only refresh moves
   ``artifact_hash`` but NOT ``cnf_hash``).

2. **Freeze / predict-then-compare gate** (§3.1.2). A frozen manifest is
   persisted once; a later run recomputes anchors and diffs them against the
   frozen set, emitting a typed :class:`AnchorManifestDelta`. A ``cnf_hash``
   move on an anchor with an unchanged amendment pin is exactly the #137
   johtolause silent-oracle-drift failure mode, now surfaced as a preregistered
   event rather than a silently-moved baseline.

3. **Touch relation + attribution engine** (§3.3). Over each inter-anchor
   window ``W_k = (t_{k-1}, t_k]`` the *touch set* is the set of section keys
   whose REPLAY output changed across the window — replay's own notion of what
   an amendment touched, derived from the per-anchor replay section maps with
   no op-extraction plumbing dependency. Divergences are then attributed:
   spontaneous appearance / healing in an UNTOUCHED unit convicts the oracle
   (``oracle_suspect``); a divergence that PERSISTS after a touch localizes a
   candidate replay bug to that 1-2 amendment window. Observations are emitted
   as :class:`~lawvm.core.agreement_residual.AgreementResidual` rows.

Everything reuses the existing all_pit machinery: ``plan_snapshots`` for anchor
enumeration/date derivation, ``compute_statute_section_diffs`` +
``_section_diff_is_bench_neutralized`` for the bench-identical per-section
verdict, and ``get_consolidated_oracle_suspect`` for per-anchor
commensurability gating.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Optional, cast

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    AgreementResidualStatus,
)


MANIFEST_SCHEMA = "lawvm.fi_anchor_manifest.v1"


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed published consolidation snapshot.

    ``artifact_hash`` pins the RAW published XML bytes (immutability check —
    any Finlex re-edit changes it). ``cnf_hash`` pins the normative-projected
    section map (label + grammar-normalized wording of every non-editorial
    section), so an editorial-only refresh moves ``artifact_hash`` but leaves
    ``cnf_hash`` stable — separating "the world moved" (cnf) from "the
    consolidator re-rendered" (artifact only).
    """

    sid: str
    version_tag: str
    amendment_id: str
    as_of: Optional[str]  # ISO date; None ⇒ unplaceable
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""  # non-empty ⇒ could not be placed on the timeline

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_tag": self.version_tag,
            "amendment_id": self.amendment_id,
            "as_of": self.as_of,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    sid: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"sid": self.sid, "anchors": [a.to_dict() for a in self.anchors]}

    def by_version(self) -> dict[str, Anchor]:
        return {a.version_tag: a for a in self.anchors}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cnf_section_map(oracle_root: Any) -> dict[str, str]:
    """Project one oracle tree into a normative-content section map.

    Keys are section keys (the bench's own key space); values are the
    grammar-normalized wording of each section (via the bench's
    ``_clean_oracle_section_text`` — the FI quoted-span-matcher normalization,
    which is definitionally the FI editorial quotient, per FABLE §1.1). Only
    the addressable normative fields survive; whitespace-pad, dot-leaders, and
    other editorial noise are quotiented away, so the resulting hash tracks
    NORMATIVE content only.
    """
    from lawvm.tools.bench import _clean_oracle_section_text
    from lawvm.tools.section_keys import extract_oracle_sections
    from lawvm.xml_ingest import xml_element_to_text

    sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}
    out: dict[str, str] = {}
    for key, node in sections.items():
        # Oracle sections are lxml elements, so the oracle-side text extractor
        # (matching bench's own oracle path, ``xml_element_to_text``) is what
        # feeds the FI editorial-quotient normalizer — NOT the IRNode extractor.
        try:
            text = _clean_oracle_section_text(xml_element_to_text(node))
        except Exception:  # noqa: BLE001 — a mangled node must not sink the manifest
            text = ""
        out[str(key)] = text
    return out


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    """Deterministic hash over the normalized section map (order-independent)."""
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


def build_statute_manifest(sid: str, *, corpus: Any = None) -> StatuteManifest:
    """Enumerate + content-address every published anchor of *sid*.

    Reuses :func:`fi_aux_pit_probe.plan_snapshots` for enumeration and as-of
    derivation, and :func:`list_cached_consolidated_artifacts` for the raw
    published bytes. Never raises for per-anchor problems — an unreadable
    artifact yields an empty-hash anchor carrying a ``reason``.
    """
    from lawvm.finland.consolidated_store import list_cached_consolidated_artifacts
    from lawvm.finland.corpus import (
        _archive_from_source,
        get_corpus,
        get_ground_truth_tree,
    )
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lawvm.tools.fi_aux_pit_probe import plan_snapshots

    if corpus is None:
        corpus = get_corpus()
    archive = _archive_from_source(corpus)
    if archive is None:
        raise RuntimeError("corpus store exposes no archive backend")

    raw_by_version: dict[str, bytes] = {}
    for art in list_cached_consolidated_artifacts(cast(Any, archive), sid):
        raw_by_version[art.version_tag] = (
            art.xml if isinstance(art.xml, bytes) else str(art.xml).encode("utf-8")
        )

    anchors: list[Anchor] = []
    for plan in plan_snapshots(archive, sid):
        raw = raw_by_version.get(plan.version_tag)
        artifact_hash = _sha256_hex(raw) if raw is not None else ""
        cnf_hash = ""
        n_sections = 0
        reason = plan.reason
        if raw is not None:
            try:
                oracle_root = get_ground_truth_tree(
                    sid,
                    corpus=corpus,
                    selector=ConsolidatedArtifactSelector.exact_embedded_version(
                        plan.version_tag
                    ),
                )
                cnf_map = _cnf_section_map(oracle_root)
                cnf_hash = _cnf_hash_of_map(cnf_map)
                n_sections = len(cnf_map)
            except Exception as exc:  # noqa: BLE001
                reason = (reason + f"; cnf-error:{exc}").strip("; ")
        else:
            reason = (reason + "; no cached raw artifact").strip("; ")
        anchors.append(
            Anchor(
                sid=sid,
                version_tag=plan.version_tag,
                amendment_id=plan.amendment_id,
                as_of=plan.as_of.isoformat() if plan.as_of else None,
                artifact_hash=artifact_hash,
                cnf_hash=cnf_hash,
                n_sections=n_sections,
                reason=reason,
            )
        )
    return StatuteManifest(sid=sid, anchors=tuple(anchors))


def build_manifest(sids: list[str], *, corpus: Any = None) -> dict[str, Any]:
    """Build a deterministic, diffable manifest document over *sids*."""
    from lawvm.finland.corpus import get_corpus

    if corpus is None:
        corpus = get_corpus()
    statutes = {
        sid: build_statute_manifest(sid, corpus=corpus).to_dict()
        for sid in sorted(sids)
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "jurisdiction": "finland",
        "statute_count": len(statutes),
        "statutes": statutes,
    }


def write_manifest(manifest: dict[str, Any], path: str) -> None:
    """Persist a manifest deterministically (sorted keys, trailing newline)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, ensure_ascii=False, indent=2)
        fh.write("\n")


def read_manifest(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Freeze / predict-then-compare gate (§3.1.2)
# ---------------------------------------------------------------------------


AnchorDeltaKind = Literal[
    "anchor_added",
    "anchor_removed",
    "artifact_drift_editorial_only",  # raw bytes moved, normative CNF stable
    "cnf_drift",                      # normative content moved (the #137 mode)
    "as_of_moved",                    # placement date moved under a fixed tag
]


@dataclass(frozen=True)
class AnchorDelta:
    sid: str
    version_tag: str
    kind: AnchorDeltaKind
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "version_tag": self.version_tag,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AnchorManifestDelta:
    deltas: tuple[AnchorDelta, ...]

    @property
    def is_empty(self) -> bool:
        return not self.deltas

    @property
    def cnf_drifts(self) -> tuple[AnchorDelta, ...]:
        return tuple(d for d in self.deltas if d.kind == "cnf_drift")

    def to_dict(self) -> dict[str, Any]:
        return {"deltas": [d.to_dict() for d in self.deltas]}


def diff_manifest(frozen: dict[str, Any], fresh: dict[str, Any]) -> AnchorManifestDelta:
    """Compare a fresh manifest against a frozen one → typed anchor deltas.

    A ``cnf_drift`` on an anchor whose ``amendment_id`` is unchanged is the
    baseline-moved-under-you failure mode (#137): the published normative
    content of a HISTORICAL snapshot changed without a new amendment. It is
    surfaced, never silently absorbed.
    """
    deltas: list[AnchorDelta] = []
    frozen_statutes = frozen.get("statutes", {})
    fresh_statutes = fresh.get("statutes", {})
    for sid in sorted(set(frozen_statutes) | set(fresh_statutes)):
        f_anchors = {
            a["version_tag"]: a
            for a in frozen_statutes.get(sid, {}).get("anchors", [])
        }
        n_anchors = {
            a["version_tag"]: a
            for a in fresh_statutes.get(sid, {}).get("anchors", [])
        }
        for vt in sorted(set(f_anchors) | set(n_anchors)):
            fa = f_anchors.get(vt)
            na = n_anchors.get(vt)
            if fa is None:
                deltas.append(
                    AnchorDelta(sid, vt, "anchor_added", f"new anchor {vt}")
                )
                continue
            if na is None:
                deltas.append(
                    AnchorDelta(sid, vt, "anchor_removed", f"anchor {vt} gone")
                )
                continue
            if fa.get("cnf_hash") != na.get("cnf_hash"):
                deltas.append(
                    AnchorDelta(
                        sid,
                        vt,
                        "cnf_drift",
                        f"cnf {fa.get('cnf_hash', '')[:12]}→{na.get('cnf_hash', '')[:12]}"
                        f" (amend {fa.get('amendment_id')}→{na.get('amendment_id')})",
                    )
                )
            elif fa.get("artifact_hash") != na.get("artifact_hash"):
                deltas.append(
                    AnchorDelta(
                        sid,
                        vt,
                        "artifact_drift_editorial_only",
                        f"artifact {fa.get('artifact_hash', '')[:12]}→"
                        f"{na.get('artifact_hash', '')[:12]} (cnf stable)",
                    )
                )
            if fa.get("as_of") != na.get("as_of"):
                deltas.append(
                    AnchorDelta(
                        sid,
                        vt,
                        "as_of_moved",
                        f"as_of {fa.get('as_of')}→{na.get('as_of')}",
                    )
                )
    return AnchorManifestDelta(deltas=tuple(deltas))


# ---------------------------------------------------------------------------
# Per-anchor scoring with penalized-key + replay-text detail (touch relation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorObservation:
    """One anchor scored, with the detail needed by the attribution engine."""

    version_tag: str
    amendment_id: str
    as_of: Optional[str]
    struct_sim: float
    n_sections: int
    n_penalized: int
    penalized_keys: frozenset[str]
    replay_text: dict[str, str]  # section_key → grammar-normalized replay wording
    oracle_suspect: Optional[str]  # per-anchor commensurability witness, if any
    status: str


def _replay_section_text(replay_master: Any) -> dict[str, str]:
    """Grammar-normalized replay wording per section, for the touch relation."""
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.tools.bench import _clean, _comparison_ir
    from lawvm.tools.section_keys import extract_ir_sections

    try:
        replay_ir = _comparison_ir(replay_master)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for key, node in extract_ir_sections(replay_ir).items():
        try:
            out[str(key)] = _clean(irnode_to_text(node))
        except Exception:  # noqa: BLE001
            out[str(key)] = ""
    return out


def score_anchor(
    sid: str,
    version_tag: str,
    amendment_id: str,
    as_of: Optional[str],
    *,
    corpus: Any = None,
) -> AnchorObservation:
    """Score replay@as_of vs one anchor, returning penalized keys + replay text.

    Reuses ``compute_statute_section_diffs`` (bench-identical section verdict)
    and ``bench._section_diff_is_bench_neutralized`` so ``struct_sim`` /
    ``penalized_keys`` are commensurable with the headline number, then augments
    with the per-anchor commensurability witness
    (``get_consolidated_oracle_suspect`` at this exact version).
    """
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lawvm.finland.corpus import get_corpus, get_consolidated_oracle_suspect
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
    from lawvm.tools.bench import _section_diff_is_bench_neutralized
    from lawvm.tools.structural_review import (
        _sections_with_diffs,
        compute_statute_section_diffs,
    )

    if corpus is None:
        corpus = get_corpus()

    if as_of is None:
        return AnchorObservation(
            version_tag=version_tag,
            amendment_id=amendment_id,
            as_of=None,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            penalized_keys=frozenset(),
            replay_text={},
            oracle_suspect=None,
            status="UNPLACEABLE",
        )

    selector = ConsolidatedArtifactSelector.exact_embedded_version(version_tag)
    replay_master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=sid,
            mode="legal_pit",
            as_of=as_of,
            quiet=True,
            corpus=corpus,
            oracle_selector=selector,
        ),
    )
    sections, oracle_absent = compute_statute_section_diffs(
        sid,
        corpus=corpus,
        mode="legal_pit",
        oracle_selector=selector,
        as_of=as_of,
        replay_master=replay_master,
        support_mode="diff_only",
    )
    replay_text = _replay_section_text(replay_master)
    if oracle_absent:
        return AnchorObservation(
            version_tag=version_tag,
            amendment_id=amendment_id,
            as_of=as_of,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            penalized_keys=frozenset(),
            replay_text=replay_text,
            oracle_suspect=None,
            status="ORACLE_CONTENT_ABSENT",
        )

    non_editorial = {
        k: v
        for k, v in sections.items()
        if v.get("semantic_diff", {}).get("kind") != "editorial_only"
    }
    penalized: set[str] = set()
    for sec_key, sd, events in _sections_with_diffs({"sections": non_editorial}):
        if _section_diff_is_bench_neutralized(sd, events):
            continue
        if non_editorial.get(sec_key, {}).get("amb_alternate_match"):
            continue
        if non_editorial.get(sec_key, {}).get("seg_displacement_match"):
            continue
        penalized.add(sec_key)

    n_non_editorial = len(non_editorial)
    struct_sim = 1.0 if not n_non_editorial else 1.0 - len(penalized) / n_non_editorial

    try:
        suspect = get_consolidated_oracle_suspect(sid, corpus=corpus, selector=selector)
    except Exception:  # noqa: BLE001
        suspect = None

    return AnchorObservation(
        version_tag=version_tag,
        amendment_id=amendment_id,
        as_of=as_of,
        struct_sim=struct_sim,
        n_sections=n_non_editorial,
        n_penalized=len(penalized),
        penalized_keys=frozenset(penalized),
        replay_text=replay_text,
        oracle_suspect=suspect,
        status="OK",
    )


# ---------------------------------------------------------------------------
# Touch relation + attribution engine (§3.3)
# ---------------------------------------------------------------------------


def touch_set(prev: AnchorObservation, cur: AnchorObservation) -> frozenset[str]:
    """Section keys REPLAY changed across the window ``(prev.as_of, cur.as_of]``.

    A key is *touched* iff its grammar-normalized replay wording differs between
    the two anchors, OR the key appears / disappears in the replay output across
    the window (an amendment added or repealed the unit). This is replay's own
    notion of what the intervening amendments touched — the touch relation of
    FABLE §3.3, derived without op-extraction plumbing.
    """
    keys = set(prev.replay_text) | set(cur.replay_text)
    touched: set[str] = set()
    for k in keys:
        if prev.replay_text.get(k) != cur.replay_text.get(k):
            touched.add(k)
    return frozenset(touched)


TouchVerdict = Literal[
    "oracle_suspect_spontaneous_appearance",
    "oracle_suspect_spontaneous_healing",
    "oracle_suspect_standing_untouched",
    "candidate_replay_bug_persistent_post_touch",
    "temporal_mismatch_commensurability",
    "untyped",
]


@dataclass(frozen=True)
class TouchObservation:
    """One typed attribution over a (window, unit) or (anchor, unit)."""

    sid: str
    section_key: str
    verdict: TouchVerdict
    window: str            # "t_{k-1}..t_k"
    touching_amendments: tuple[str, ...]
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "section_key": self.section_key,
            "verdict": self.verdict,
            "window": self.window,
            "touching_amendments": list(self.touching_amendments),
            "evidence": self.evidence,
        }


def attribute_divergences(
    sid: str, anchors: list[AnchorObservation]
) -> list[TouchObservation]:
    """Run the §3.3 attribution calculus over a statute's scored anchor list.

    Anchors must be in chronological (as-of) order. Only scored anchors
    participate. For each unit that diverges at some anchor, the touch relation
    across adjacent windows decides:

    - matched at k-1, untouched in ``W_k``, diverges at k → oracle spontaneous
      APPEARANCE (oracle changed a unit nothing legal touched) → oracle_suspect.
    - diverges at k, untouched in ``W_{k+1}``, matches at k+1 → oracle
      spontaneous HEALING (anchor k was wrong at u) → oracle_suspect.
    - touched in ``W_k`` and diverges at k (and, if a later anchor exists,
      stays diverged) → candidate replay bug localized to ``W_k``.
    - present at the BASE anchor (no prior) and NEVER touched by replay across
      the statute's whole observed life → standing untouched divergence: replay
      never changed the unit, so no amendment localizes a bug and the oracle
      renders it differently → oracle_suspect (standing). This mirrors the
      spontaneous-appearance logic for the no-prior case.
    - anchor is per-anchor commensurability-suspect → temporal_mismatch.
    """
    scored = [a for a in anchors if a.struct_sim >= 0.0]
    # Per-key union of every replay-touch across the whole life: a key that
    # replay never once changed cannot carry a replay bug — the source-anchored
    # unit is stable, so a persisting divergence is oracle-side (FABLE §3.3 /
    # §5.3: source-anchored strata where the oracle can only corroborate).
    ever_touched: set[str] = set()
    for i in range(1, len(scored)):
        ever_touched |= touch_set(scored[i - 1], scored[i])
    observations: list[TouchObservation] = []
    for i, cur in enumerate(scored):
        prev = scored[i - 1] if i > 0 else None
        nxt = scored[i + 1] if i + 1 < len(scored) else None
        window = f"{(prev.as_of if prev else '-')}..{cur.as_of}"
        touched_in = touch_set(prev, cur) if prev is not None else frozenset()
        touched_out = touch_set(cur, nxt) if nxt is not None else frozenset()
        for key in sorted(cur.penalized_keys):
            # Commensurability convicts the anchor first (cheapest, doc-level).
            if cur.oracle_suspect:
                observations.append(
                    TouchObservation(
                        sid=sid,
                        section_key=key,
                        verdict="temporal_mismatch_commensurability",
                        window=window,
                        touching_amendments=(cur.amendment_id,),
                        evidence=f"anchor commensurability-suspect: {cur.oracle_suspect}",
                    )
                )
                continue
            matched_prev = prev is not None and key not in prev.penalized_keys
            matched_next = nxt is not None and key not in nxt.penalized_keys
            touched_now = key in touched_in
            # Spontaneous APPEARANCE: matched before, nothing touched it, now diverges.
            if matched_prev and not touched_now:
                observations.append(
                    TouchObservation(
                        sid=sid,
                        section_key=key,
                        verdict="oracle_suspect_spontaneous_appearance",
                        window=window,
                        touching_amendments=(),
                        evidence=(
                            f"matched at {prev.as_of}; no replay touch in window; "
                            f"diverges at {cur.as_of} ⇒ oracle changed an untouched unit"
                        ),
                    )
                )
                continue
            # Spontaneous HEALING: diverges now, nothing touches it next, matches next.
            if matched_next and key not in touched_out:
                observations.append(
                    TouchObservation(
                        sid=sid,
                        section_key=key,
                        verdict="oracle_suspect_spontaneous_healing",
                        window=f"{cur.as_of}..{nxt.as_of}",
                        touching_amendments=(),
                        evidence=(
                            f"diverges at {cur.as_of}; no replay touch in next window; "
                            f"matches at {nxt.as_of} ⇒ anchor {cur.version_tag} was wrong at unit"
                        ),
                    )
                )
                continue
            # Persistent post-touch divergence: a touch in this window changed the
            # unit and it stays diverged (or is the last anchor) ⇒ candidate bug.
            if touched_now:
                observations.append(
                    TouchObservation(
                        sid=sid,
                        section_key=key,
                        verdict="candidate_replay_bug_persistent_post_touch",
                        window=window,
                        touching_amendments=(cur.amendment_id,),
                        evidence=(
                            f"replay touched unit in window; diverges at {cur.as_of} "
                            f"and is not healed ⇒ candidate bug localized to window"
                        ),
                    )
                )
                continue
            # Standing untouched divergence: replay never once changed this unit
            # across its whole life, yet it diverges (and no adjacent window
            # healed/introduced it). The unit is source-anchored-stable; the
            # oracle simply renders it differently ⇒ oracle-side, not a bug.
            if key not in ever_touched:
                observations.append(
                    TouchObservation(
                        sid=sid,
                        section_key=key,
                        verdict="oracle_suspect_standing_untouched",
                        window=window,
                        touching_amendments=(),
                        evidence=(
                            f"replay never touched unit across life; diverges at "
                            f"{cur.as_of} ⇒ oracle renders a stable unit differently"
                        ),
                    )
                )
                continue
            observations.append(
                TouchObservation(
                    sid=sid,
                    section_key=key,
                    verdict="untyped",
                    window=window,
                    touching_amendments=(cur.amendment_id,),
                    evidence="divergence not resolved by touch relation",
                )
            )
    return observations


# ---------------------------------------------------------------------------
# Statute-level driver: score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    sid: str
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
        """True when every anchor-divergence is oracle/temporal-attributed.

        This is the §5.4 per-anchor gate: a min-over-life<latest dip is only a
        *candidate bug* if the touch relation did not convict the oracle and
        the anchor is commensurable. A statute is gated-clean iff it has no
        candidate-bug and no untyped observations.
        """
        return not self.candidate_bug_observations and not self.untyped_observations


def attribute_statute(sid: str, *, corpus: Any = None) -> StatuteAttribution:
    """Score every anchor of *sid*, then run the attribution engine + gate."""
    from lawvm.finland.corpus import _archive_from_source, get_corpus
    from lawvm.tools.fi_aux_pit_probe import plan_snapshots

    if corpus is None:
        corpus = get_corpus()
    archive = _archive_from_source(corpus)
    if archive is None:
        return StatuteAttribution(sid=sid, anchors=(), observations=(), status="ERROR:no-archive")
    try:
        plans = plan_snapshots(archive, sid)
    except Exception as exc:  # noqa: BLE001
        return StatuteAttribution(sid=sid, anchors=(), observations=(), status=f"ERROR:{exc}")

    anchors: list[AnchorObservation] = []
    for plan in plans:
        try:
            obs = score_anchor(
                sid,
                plan.version_tag,
                plan.amendment_id,
                plan.as_of.isoformat() if plan.as_of else None,
                corpus=corpus,
            )
        except Exception as exc:  # noqa: BLE001
            obs = AnchorObservation(
                version_tag=plan.version_tag,
                amendment_id=plan.amendment_id,
                as_of=plan.as_of.isoformat() if plan.as_of else None,
                struct_sim=-1.0,
                n_sections=0,
                n_penalized=0,
                penalized_keys=frozenset(),
                replay_text={},
                oracle_suspect=None,
                status=f"ERROR:{exc}",
            )
        anchors.append(obs)
    observations = attribute_divergences(sid, anchors)
    return StatuteAttribution(
        sid=sid, anchors=tuple(anchors), observations=tuple(observations)
    )


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the existing taxonomy)
# ---------------------------------------------------------------------------


_VERDICT_TO_FAMILY: dict[str, AgreementResidualFamily] = {
    "oracle_suspect_spontaneous_appearance": "oracle_editorial_pathology",
    "oracle_suspect_spontaneous_healing": "oracle_editorial_pathology",
    "oracle_suspect_standing_untouched": "oracle_editorial_pathology",
    "candidate_replay_bug_persistent_post_touch": "replay_bug",
    "temporal_mismatch_commensurability": "temporal_mismatch",
    "untyped": "unknown",
}

_VERDICT_TO_STATUS: dict[str, AgreementResidualStatus] = {
    "oracle_suspect_spontaneous_appearance": "blocked",
    "oracle_suspect_spontaneous_healing": "blocked",
    "oracle_suspect_standing_untouched": "blocked",
    "candidate_replay_bug_persistent_post_touch": "residual",
    "temporal_mismatch_commensurability": "blocked",
    "untyped": "residual",
}


def observation_to_residual(obs: TouchObservation) -> AgreementResidual:
    """Project one touch observation into the shared AgreementResidual taxonomy."""
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"fi:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="finland",
        agreement_surface="all_pit_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="bench.all_pit.touch_relation",
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
# CLI: build / diff a frozen manifest, or attribute statutes
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.fi_anchor_manifest",
        description="Build/freeze content-addressed anchor manifests, gate a "
        "fresh manifest against a frozen one, or run the touch-relation "
        "attribution engine (#183).",
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_build = sub.add_parser("build", help="build a frozen manifest and persist it")
    p_build.add_argument("statute_ids", nargs="+")
    p_build.add_argument("--out", required=True, help="manifest JSON output path")

    p_diff = sub.add_parser(
        "diff", help="gate: diff a fresh manifest (recomputed) vs a frozen one"
    )
    p_diff.add_argument("statute_ids", nargs="+")
    p_diff.add_argument("--frozen", required=True, help="frozen manifest JSON path")

    p_attr = sub.add_parser(
        "attribute", help="run the touch-relation attribution engine on statutes"
    )
    p_attr.add_argument("statute_ids", nargs="+")

    args = parser.parse_args(argv)

    if args.verb == "build":
        manifest = build_manifest(list(args.statute_ids))
        write_manifest(manifest, args.out)
        n = sum(len(s["anchors"]) for s in manifest["statutes"].values())
        print(f"wrote {args.out}: {manifest['statute_count']} statutes, {n} anchors")
        return 0

    if args.verb == "diff":
        frozen = read_manifest(args.frozen)
        fresh = build_manifest(list(args.statute_ids))
        delta = diff_manifest(frozen, fresh)
        if delta.is_empty:
            print("MANIFEST STABLE: no anchor deltas vs frozen set")
            return 0
        print(f"MANIFEST MOVED: {len(delta.deltas)} anchor delta(s)")
        for d in delta.deltas:
            print(f"  {d.sid:12s} {d.version_tag:<10} {d.kind}: {d.detail}")
        if delta.cnf_drifts:
            print(
                f"\n  WARNING: {len(delta.cnf_drifts)} cnf_drift(s) — a historical "
                "anchor's NORMATIVE content moved (the #137 silent-baseline-drift "
                "failure mode). Treat as a preregistered predict-then-compare event."
            )
        return 1

    if args.verb == "attribute":
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
                f"latest={100 * (attr.latest_scored or 0):.2f}%  "
                f"hidden-mid-life={'YES' if attr.has_hidden_mid_life_divergence else 'no'}"
            )
            for o in attr.observations:
                print(f"  {o.section_key:<12} {o.verdict}  window={o.window}")
                print(f"    {o.evidence}")
            if not attr.is_gated_clean:
                rc = 1
        return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
