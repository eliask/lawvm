"""ee_anchor_manifest.py — Estonia's frozen content-addressed anchor manifests +
the touch-relation attribution engine (#183/#205).

This is the Estonia analogue of :mod:`lawvm.tools.fi_anchor_manifest`, extending
the drift-robust #183 metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 /
§5.4) from Finland to a second jurisdiction. It is ADDITIVE: it never mutates the
EE corpus, replay, or the existing ``ee-bench`` scoring; the default EE bench
stays byte-identical.

WHAT AN EE ANCHOR IS (the jurisdiction adaptation). Finland enumerates the
published *consolidation snapshots* of one statute over its life via
:func:`fi_aux_pit_probe.plan_snapshots`. Estonia's Riigi Teataja archive stores,
per ``terviktekstiGrupiID`` (the statute family), a chain of published
*terviktekst* (consolidated-text) versions, each carrying a ``kehtivuseAlgus``
effective-start date. THOSE tervikteksts are the EE anchors — exactly analogous to
Finland's consolidation snapshots: a content-addressed published rendering of the
statute at one point in time. The chain is genuine (many EE grupi_ids carry
20-66 body-carrying tervikteksts), so the FABLE §3.3 touch relation across
adjacent windows applies unchanged.

WHAT REPLAY IS SCORED AGAINST. For each anchor ``a`` (effective at ``as_of``) we
run :func:`estonia.replay.replay_ee_to_pit` from the statute's earliest
body-carrying terviktekst (the ``base_id``) forward to ``a.as_of`` with ``a`` as
the oracle, and compare the replayed section map to ``a``'s section map. The
per-section verdict reuses EE's OWN bench-identical comparison
(``irnode_to_ee_comparison_text`` + ``normalize_ee_comparison_text`` — EE's
editorial quotient, byte-exact section match), so ``struct_sim`` /
``penalized_keys`` are commensurable with the ``ee-bench`` headline.

ORACLE-SUSPECT DISCIPLINE (reused, first-class). Estonia already types oracle-side
commensurability defects: a replay whose ``comparison_class`` is not one of the
core classes (``base_is_oracle`` / ``commensurable_delta``) carries a non-empty
``source_adjudication.oracle_suspect`` (see ``estonia/source_adjudication.py``).
That is the EE analogue of Finland's ``get_consolidated_oracle_suspect`` per-anchor
witness; the attribution calculus consumes it exactly the same way (an oracle-
suspect anchor's divergences type to ``temporal_mismatch_commensurability``, never
a replay bug).

REUSED NEUTRAL CORE. The touch relation itself is jurisdiction-neutral: this module
imports :class:`fi_anchor_manifest.AnchorObservation`, :func:`touch_set`,
:func:`structure_touch_set`, :class:`TouchObservation`,
:func:`attribute_divergences`, :func:`_ir_structure_signature`, and the
``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` maps unchanged (they take a ``sid``
string and operate on generic replay section maps — nothing Finland-specific). Only
the anchor *enumeration*, *scoring* (EE replay + EE comparison text), and the
content-addressing are EE-specific. The shared taxonomy
(:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused as-is.

The convicting-touch principle is preserved byte-for-byte: a residual is only
``replay_bug`` / ``unknown`` (billable) if a SAME-DIMENSION replay touch attributes
it to the replay; oracle-side defects type non-billable. The structure signature
(wording-independent nesting shape) is computed identically to Finland, so a word
substitution is not a structural touch on the EE side either.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.agreement_residual import AgreementResidual

# The touch relation is jurisdiction-neutral: reuse Finland's engine wholesale.
# These operate on a ``sid`` string + generic replay section maps; none of them
# import ``lawvm.finland``. Re-exported here so EE callers have a single surface.
from lawvm.tools.fi_anchor_manifest import (
    _VERDICT_TO_FAMILY,
    _VERDICT_TO_STATUS,
    _ir_structure_signature,
    AnchorObservation,
    TouchObservation,
    attribute_divergences,
)


MANIFEST_SCHEMA = "lawvm.ee_anchor_manifest.v1"

# The RT tervikteksts that carry an actual statute body (a `<peatykk>` chapter or a
# `<paragrahv>` section) — a stub/repeal-only version is not a scorable anchor.
_EE_BODY_MARKERS = (b"<peatykk", b"<paragrahv")

# A terviktekst with a ``kehtivuseAlgus`` we cannot place on the timeline sorts
# last and is dropped from the scored chain (it is unplaceable, exactly like an
# ``as_of is None`` FI anchor).
_EE_UNPLACEABLE_ALGUS = "9999-99-99"


# ---------------------------------------------------------------------------
# EE anchor enumeration (the RT terviktekst chain of one grupi_id)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EEAnchorRef:
    """One published RT terviktekst version of a statute family (grupi_id).

    ``akt_viide`` is the RT act reference (the anchor's content id); ``as_of`` is
    its ``kehtivuseAlgus`` effective-start date (the point-in-time the anchor
    renders); ``has_body`` is False for a stub/repeal-only version (not scorable).
    """

    grupi_id: str
    akt_viide: str
    as_of: str
    has_body: bool

    @property
    def scorable(self) -> bool:
        return self.has_body and not self.as_of.startswith("9999")


def _rt_akt_locators(archive: Any) -> list[str]:
    """Every RT act-XML locator in the archive (the enumeration universe)."""
    conn = archive._conn
    rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span "
        "WHERE locator LIKE '%riigiteataja.ee/akt/%.xml'"
    ).fetchall()
    return [row[0] for row in rows]


# One archive pass builds the whole grupi_id → anchor-chain index; a per-statute
# enumeration is O(archive) otherwise (a 46k-locator scan each), so scoring a
# multi-statute corpus would re-scan the whole archive N times. The index is cached
# by the archive object's id so a corpus sweep pays the scan ONCE. (Deterministic:
# the archive bytes are frozen for the run; keyed on id(archive) which is stable for
# a live handle. A fresh handle rebuilds — correct, never stale.)
_ANCHOR_INDEX_CACHE: dict[int, dict[str, list[EEAnchorRef]]] = {}


def _build_anchor_index(archive: Any) -> dict[str, list[EEAnchorRef]]:
    """Scan the archive ONCE → {grupi_id: [EEAnchorRef, ...]} (as-of sorted)."""
    index: dict[str, list[EEAnchorRef]] = {}
    for locator in _rt_akt_locators(archive):
        aid = locator.split("/akt/")[-1].replace(".xml", "")
        data = archive.get(locator)
        if not data or len(data) < 100:
            continue
        prefix = data[:20000]
        # Raw-byte metadata triage over the RT archive to SELECT which acts are a
        # grupi_id's body-carrying tervikteksts (the anchor set); the selected bytes
        # are parsed properly downstream via parse_ee_statute. Mirrors
        # ee_bench._index_corpus's identical enumeration scan.
        # lawvm-regex: prefilter — grupi_id membership triage on raw archive bytes.
        m_g = re.search(rb"<[^>]*terviktekstiGrupiID[^>]*>([^<]+)<", prefix)
        gid = m_g.group(1).decode().strip() if m_g else None
        if not gid:
            continue
        # lawvm-regex: prefilter — terviktekst-vs-amendment discriminator on raw bytes.
        m_t = re.search(rb"<[^>]*tekstiliik[^>]*>([^<]+)<", prefix)
        tekstiliik = m_t.group(1).decode().strip() if m_t else ""
        if tekstiliik != "terviktekst":
            continue
        has_body = any(marker in data for marker in _EE_BODY_MARKERS)
        # lawvm-regex: prefilter — effective-start (as_of) extraction for anchor order.
        m_algus = re.search(rb"<[^>]*kehtivuseAlgus[^>]*>([^<]+)<", prefix)
        as_of = (
            m_algus.group(1).decode().strip()[:10]
            if m_algus
            else _EE_UNPLACEABLE_ALGUS
        )
        index.setdefault(gid, []).append(
            EEAnchorRef(
                grupi_id=gid, akt_viide=aid, as_of=as_of, has_body=has_body
            )
        )
    for gid in index:
        index[gid].sort(key=lambda a: (a.as_of, a.akt_viide))
    return index


def anchor_index(archive: Any) -> dict[str, list[EEAnchorRef]]:
    """The cached {grupi_id: anchor-chain} index for *archive* (built once)."""
    key = id(archive)
    cached = _ANCHOR_INDEX_CACHE.get(key)
    if cached is None:
        cached = _build_anchor_index(archive)
        _ANCHOR_INDEX_CACHE[key] = cached
    return cached


def enumerate_ee_anchors(grupi_id: str, *, archive: Any) -> list[EEAnchorRef]:
    """Enumerate the body-carrying terviktekst chain of *grupi_id*, as-of sorted.

    Returns every terviktekst (``tekstiliik == terviktekst``) whose
    ``terviktekstiGrupiID`` is *grupi_id*, sorted by (as_of, akt_viide) —
    chronological, the order the touch relation requires. This is the EE analogue of
    ``plan_snapshots``: the published consolidation chain of one statute family.
    Backed by the archive-wide :func:`anchor_index` (one scan amortized over a
    corpus sweep).
    """
    return list(anchor_index(archive).get(grupi_id, []))


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest (mirrors fi_anchor_manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed published EE terviktekst snapshot.

    ``artifact_hash`` pins the RAW published RT XML bytes (immutability check — any
    RT re-edit changes it). ``cnf_hash`` pins the normative-projected section map
    (EE's editorial-quotient comparison text of every section), so an editorial-only
    refresh moves ``artifact_hash`` but leaves ``cnf_hash`` stable.
    """

    grupi_id: str
    akt_viide: str
    as_of: Optional[str]
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "akt_viide": self.akt_viide,
            "as_of": self.as_of,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    grupi_id: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "grupi_id": self.grupi_id,
            "anchors": [a.to_dict() for a in self.anchors],
        }


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ee_cnf_section_map(body: Any) -> dict[str, str]:
    """Project one EE statute body into a normative-content section map.

    Keys are section keys (EE's bench key space); values are the EE editorial-
    quotient comparison text (``irnode_to_ee_comparison_text`` +
    ``normalize_ee_comparison_text`` — definitionally EE's editorial quotient, the
    same normalization ``ee-bench`` scores over). This is the EE analogue of FI's
    ``_cnf_section_map``.
    """
    from lawvm.tools.ee_bench import _get_sections

    return _get_sections(body) if body is not None else {}


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


def build_statute_manifest(grupi_id: str, *, archive: Any) -> StatuteManifest:
    """Enumerate + content-address every published terviktekst anchor of *grupi_id*.

    Never raises for per-anchor problems — an unreadable/unparseable anchor yields
    an empty-hash anchor carrying a ``reason``.
    """
    from lawvm.estonia.fetch import fetch_rt_xml
    from lawvm.estonia.grafter import parse_ee_statute

    anchors: list[Anchor] = []
    for ref in enumerate_ee_anchors(grupi_id, archive=archive):
        reason = "" if ref.scorable else "stub-or-unplaceable"
        artifact_hash = ""
        cnf_hash = ""
        n_sections = 0
        try:
            raw = fetch_rt_xml(ref.akt_viide, archive=archive)
            artifact_hash = _sha256_hex(raw)
            if ref.scorable:
                statute = parse_ee_statute(raw, f"ee/{ref.akt_viide}")
                cnf_map = _ee_cnf_section_map(statute.body if statute else None)
                cnf_hash = _cnf_hash_of_map(cnf_map)
                n_sections = len(cnf_map)
        except Exception as exc:  # noqa: BLE001 — a bad anchor must not sink the manifest
            reason = (reason + f"; error:{exc}").strip("; ")
        anchors.append(
            Anchor(
                grupi_id=grupi_id,
                akt_viide=ref.akt_viide,
                as_of=ref.as_of if not ref.as_of.startswith("9999") else None,
                artifact_hash=artifact_hash,
                cnf_hash=cnf_hash,
                n_sections=n_sections,
                reason=reason,
            )
        )
    return StatuteManifest(grupi_id=grupi_id, anchors=tuple(anchors))


def build_manifest(grupi_ids: list[str], *, archive: Any = None) -> dict[str, Any]:
    """Build a deterministic, diffable manifest document over *grupi_ids*."""
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.tools.ee_bench import _DEFAULT_DB

    close_after = False
    if archive is None:
        archive = open_rt_archive(_DEFAULT_DB, readonly=True)
        close_after = True
    try:
        statutes = {
            gid: build_statute_manifest(gid, archive=archive).to_dict()
            for gid in sorted(grupi_ids)
        }
    finally:
        if close_after:
            archive.close()
    return {
        "schema": MANIFEST_SCHEMA,
        "jurisdiction": "estonia",
        "statute_count": len(statutes),
        "statutes": statutes,
    }


# ---------------------------------------------------------------------------
# Per-anchor scoring (EE replay + EE comparison text → AnchorObservation)
# ---------------------------------------------------------------------------


def _replay_section_structure(body: Any) -> dict[str, str]:
    """Per-section replay STRUCTURE signature over an EE replay body.

    Reuses Finland's ``_ir_structure_signature`` (wording-independent nesting shape)
    — the IR node shape is jurisdiction-neutral, so the structural touch relation is
    identical for EE.
    """
    from lawvm.tools.section_keys import extract_ir_sections

    if body is None:
        return {}
    out: dict[str, str] = {}
    for key, node in extract_ir_sections(body).items():
        try:
            out[str(key)] = _ir_structure_signature(node)
        except Exception:  # noqa: BLE001
            out[str(key)] = ""
    return out


def score_anchor(
    grupi_id: str,
    base_id: str,
    ref: EEAnchorRef,
    *,
    archive: Any,
) -> AnchorObservation:
    """Score replay@as_of vs one EE terviktekst anchor.

    Runs ``replay_ee_to_pit(base_id, as_of=ref.as_of, oracle_id=ref.akt_viide)``,
    then computes the bench-identical per-section verdict (EE byte-exact section
    match over ``irnode_to_ee_comparison_text``/``normalize_ee_comparison_text``).
    ``penalized_keys`` are the oracle sections replay did not reproduce exactly.
    ``oracle_suspect`` carries EE's per-anchor commensurability witness
    (``source_adjudication.oracle_suspect``: non-empty iff the anchor's
    comparison_class is not a core class).
    """
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.tools.ee_bench import _get_sections

    if not ref.scorable:
        return AnchorObservation(
            version_tag=ref.akt_viide,
            amendment_id=ref.akt_viide,
            as_of=None,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            penalized_keys=frozenset(),
            replay_text={},
            oracle_suspect=None,
            status="UNPLACEABLE",
        )

    try:
        r = replay_ee_to_pit(
            base_id,
            as_of=ref.as_of,
            archive=archive,
            verbose=False,
            oracle_id=ref.akt_viide,
        )
    except Exception as exc:  # noqa: BLE001
        return AnchorObservation(
            version_tag=ref.akt_viide,
            amendment_id=ref.akt_viide,
            as_of=ref.as_of,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            penalized_keys=frozenset(),
            replay_text={},
            oracle_suspect=None,
            status=f"ERROR:{exc}",
        )

    r_secs = _get_sections(r.replayed.body) if r.replayed else {}
    o_secs = _get_sections(r.oracle.body) if r.oracle else {}

    if not o_secs:
        return AnchorObservation(
            version_tag=ref.akt_viide,
            amendment_id=ref.akt_viide,
            as_of=ref.as_of,
            struct_sim=-1.0,
            n_sections=0,
            n_penalized=0,
            penalized_keys=frozenset(),
            replay_text=r_secs,
            oracle_suspect=None,
            status="ORACLE_CONTENT_ABSENT",
        )

    # Penalized: an oracle section replay did not reproduce exactly. This is the
    # SAME byte-exact section-match predicate ``ee-bench`` uses (``_score_one_pair``:
    # ``r_secs[key] == oracle_text`` or absent-both), so struct_sim is commensurable
    # with the EE headline.
    penalized: set[str] = set()
    for key, oracle_text in o_secs.items():
        matched = (key in r_secs and r_secs[key] == oracle_text) or (
            key not in r_secs and oracle_text == ""
        )
        if not matched:
            penalized.add(key)

    n_sections = len(o_secs)
    struct_sim = 1.0 if not n_sections else 1.0 - len(penalized) / n_sections

    adj = r.source_adjudication
    oracle_suspect = (adj.oracle_suspect or None) if adj is not None else None

    return AnchorObservation(
        version_tag=ref.akt_viide,
        amendment_id=ref.akt_viide,
        as_of=ref.as_of,
        struct_sim=struct_sim,
        n_sections=n_sections,
        n_penalized=len(penalized),
        penalized_keys=frozenset(penalized),
        replay_text=dict(r_secs),
        oracle_suspect=oracle_suspect,
        status="OK",
        replay_structure=_replay_section_structure(r.replayed.body if r.replayed else None),
        # EE section comparison is byte-exact (no structure-only sub-verdict), so we
        # do not populate ``structural_only_penalized_keys`` — every EE penalized key
        # is a wording-level divergence, and the wording-level touch relation governs.
        structural_only_penalized_keys=frozenset(),
    )


# ---------------------------------------------------------------------------
# Statute-level driver: enumerate + score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    grupi_id: str
    base_id: str
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


def attribute_statute(
    grupi_id: str, *, archive: Any = None
) -> StatuteAttribution:
    """Enumerate every terviktekst anchor of *grupi_id*, score each, then attribute.

    The ``base_id`` is the earliest scorable (body-carrying, placeable) anchor — the
    statute's origin snapshot, from which replay applies amendments forward to each
    later anchor's as_of. The attribution calculus (Finland's neutral
    ``attribute_divergences``) runs over the chronological scored anchor list.
    """
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.tools.ee_bench import _DEFAULT_DB

    close_after = False
    if archive is None:
        archive = open_rt_archive(_DEFAULT_DB, readonly=True)
        close_after = True
    try:
        refs = enumerate_ee_anchors(grupi_id, archive=archive)
        scorable = [r for r in refs if r.scorable]
        if len(scorable) < 2:
            return StatuteAttribution(
                grupi_id=grupi_id,
                base_id=scorable[0].akt_viide if scorable else "",
                anchors=(),
                observations=(),
                status="ERROR:fewer-than-2-scorable-anchors",
            )
        base_id = scorable[0].akt_viide
        anchors: list[AnchorObservation] = []
        # The base anchor is replay's own source (replay from base to base is the
        # identity), so it is not re-scored against itself; the chain of scored
        # windows starts at the first NON-base anchor, exactly like FI where the
        # earliest snapshot seeds the touch relation.
        for ref in scorable[1:]:
            anchors.append(score_anchor(grupi_id, base_id, ref, archive=archive))
        observations = attribute_divergences(grupi_id, anchors)
        return StatuteAttribution(
            grupi_id=grupi_id,
            base_id=base_id,
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
    """Project one EE touch observation into the shared AgreementResidual taxonomy.

    Reuses Finland's ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` (the verdict→
    family mapping is jurisdiction-neutral), stamped with the EE jurisdiction.
    """
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"ee:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="estonia",
        agreement_surface="ee_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="ee_bench.anchor.touch_relation",
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
# CLI: attribute EE statute families
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.ee_anchor_manifest",
        description="Run the EE touch-relation attribution engine over one or more "
        "Riigi Teataja statute families (grupi_id), the #183/#205 metric.",
    )
    parser.add_argument("grupi_ids", nargs="+")
    args = parser.parse_args(argv)

    rc = 0
    for gid in args.grupi_ids:
        attr = attribute_statute(gid)
        if attr.status != "OK":
            print(f"\n=== {gid} === {attr.status}", file=sys.stderr)
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(
            f"\n=== {gid}  (base {attr.base_id}, {len(attr.scored)} scored anchors)"
            f"  [{gate}] ==="
        )
        print(
            f"  min-over-life={100 * (attr.min_over_life or 0):.2f}%  "
            f"latest={100 * (attr.latest_scored or 0):.2f}%  "
            f"hidden-mid-life={'YES' if attr.has_hidden_mid_life_divergence else 'no'}"
        )
        for o in attr.observations:
            print(f"  {o.section_key:<12} {o.verdict}  window={o.window}")
        if not attr.is_gated_clean:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
