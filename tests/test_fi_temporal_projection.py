"""Differential: the SourceSyntaxGraph forest's temporal projection vs the H3 lens.

The temporal half of the L5 lens→forest projection strangle — following the L3
TEMPLATE (``test_fi_reference_projection``). It proves the forest can REPRODUCE
the shared-kind SUBSET (dated commencement / dated fixed-term expiry) of the H3
:class:`TemporalLens` (the differential ORACLE), and CHARACTERISES the temporal
families neither grammar shares.

OUTCOME (B): the forest's ``temporal_phrase`` leaf is sourced from the temporal /
applicability family (``parse_temporal_sentence``, mirroring the production
``meta_parse`` clause-role classifier). The H3 lens is a DIFFERENT grammar
(``recognize_temporal_exprs``). Their shared identity is the dated commencement /
dated expiry core; the differential is run on THAT canonical subset, and the
lens-only kinds (bare FIXED_DATE / duration / event-bound / undated validity) +
the forest-only roles (application / delegation) are explicit, surfaced residual
worklists.

The differential compares CANONICAL ``(kind, iso_date)`` identity keys, so it is
robust to the representational differences between the two grammars (the lens
splits a dated commencement into a dateless COMMENCEMENT row + a FIXED_DATE row;
the forest carries the date on the commencement clause).
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.source_syntax_graph import assemble_source_syntax_graph
from lawvm.finland.legal_surface.temporal_projection import (
    CANON_COMMENCEMENT,
    CANON_EXPIRY,
    FOREST_ONLY_TEMPORAL_ROLES,
    FOREST_UNOWNED_TEMPORAL_LENS_KINDS,
    diff_forest_vs_lens_temporal_subset,
    forest_temporal_keys,
    lens_temporal_keys_for_text,
    project_forest_temporal,
)

_SUBJECT = SurfaceGraphSubject(
    jurisdiction="fi",
    work_id="test/1",
    scope={},
    surface_time=None,
    source_bundle_hash="",
    language="fi",
)


def _forest_for(body: str, statute_id: str):
    return assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id=statute_id, body=body
    )


def _forest_keys_for(body: str, statute_id: str) -> set[str]:
    return forest_temporal_keys(_forest_for(body, statute_id), body)


# ── outcome characterisation ────────────────────────────────────────────────


def test_outcome_is_subset_plus_characterised_residual() -> None:
    """The forest shares the dated commencement/expiry kinds; the rest is surfaced.

    Documents the strangle's frontier: the shared canonical kinds are commencement
    and expiry; the lens-only kinds (bare date / duration / event-bound / undated
    validity) and the forest-only roles (application / delegation) are the surfaced
    residual worklists — never hidden.
    """
    assert CANON_COMMENCEMENT == "commencement"
    assert CANON_EXPIRY == "expiry"
    # The lens-only kinds the forest does not reproduce.
    assert "fixed_date" in FOREST_UNOWNED_TEMPORAL_LENS_KINDS
    assert "duration_from_commencement" in FOREST_UNOWNED_TEMPORAL_LENS_KINDS
    assert "event_bound" in FOREST_UNOWNED_TEMPORAL_LENS_KINDS
    assert "validity_open" in FOREST_UNOWNED_TEMPORAL_LENS_KINDS
    # The forest-only roles the H3 lens does not model.
    assert "application" in FOREST_ONLY_TEMPORAL_ROLES
    assert "delegation" in FOREST_ONLY_TEMPORAL_ROLES


# ── 0-delta on the shared canonical subset (the flip gate) ───────────────────


def test_zero_delta_on_dated_commencement_numeric() -> None:
    """A numeric-dated commencement: forest commencement clause == lens subset.

    ``tulee voimaan 1.1.2027`` — the forest's commencement clause carries the ISO
    date; the lens emits a dateless COMMENCEMENT + a FIXED_DATE, paired into the
    same ``commencement:<iso>`` key → 0 delta.
    """
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."
    statute_id = "2026/100"
    forest_keys = _forest_keys_for(body, statute_id)
    lens_keys = lens_temporal_keys_for_text(body)

    diff = diff_forest_vs_lens_temporal_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "commencement:2027-01-01" in forest_keys, sorted(forest_keys)


def test_zero_delta_on_dated_fixed_term_expiry() -> None:
    """A fixed-term expiry: forest validity clause == lens FIXED_TERM_EXPIRY.

    ``on voimassa 31 päivään joulukuuta 2027 saakka`` — the forest validity clause
    extracts the expiry ISO date; the lens emits a FIXED_TERM_EXPIRY carrying the
    same date → 0 delta on the shared ``expiry:<iso>`` key.
    """
    body = "Tämä laki on voimassa 31 päivään joulukuuta 2027 saakka."
    statute_id = "2026/200"
    forest_keys = _forest_keys_for(body, statute_id)
    lens_keys = lens_temporal_keys_for_text(body)

    diff = diff_forest_vs_lens_temporal_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "expiry:2027-12-31" in forest_keys, sorted(forest_keys)


# ── residual worklist: families neither grammar shares ───────────────────────


def test_forest_does_not_own_bare_fixed_date() -> None:
    """A bare calendar date with no temporal-operator cue is lens-only residual.

    ``Hakemus on tehtävä 1.1.2027`` carries a FIXED_DATE the H3 lens recognises,
    but NO commencement/expiry temporal-operator cue, so the forest temporal
    family produces no clause for it → the shared subset is empty on both sides
    (the bare date is the lens's own FIXED_DATE residual worklist, not a shared
    core).
    """
    body = "Hakemus on tehtävä 1.1.2027."
    statute_id = "2026/300"
    forest_keys = _forest_keys_for(body, statute_id)
    lens_keys = lens_temporal_keys_for_text(body)
    # No shared commencement/expiry core on either side.
    assert forest_keys == set(), sorted(forest_keys)
    assert lens_keys == set(), sorted(lens_keys)


def test_application_clause_is_forest_only_residual() -> None:
    """An application/transition clause is forest-only (the H3 lens has no kind).

    ``Tätä lakia sovelletaan ensimmäisen kerran …`` is a forest ``application``
    clause; it canonicalises to NO shared kind, so it never enters the
    differential (it is the forest-only residual worklist).
    """
    body = "Tätä lakia sovelletaan ensimmäisen kerran vuodelta 2027 toimitettavassa verotuksessa."
    statute_id = "2026/400"
    forest_keys = _forest_keys_for(body, statute_id)
    lens_keys = lens_temporal_keys_for_text(body)
    # The application clause does not produce a shared canonical key.
    assert forest_keys == set(), sorted(forest_keys)
    assert lens_keys == set(), sorted(lens_keys)


def test_undated_commencement_is_not_keyed() -> None:
    """A dateless commencement (placeholder) yields no shared key on either side.

    ``Tämä laki tulee voimaan päivänä kuuta 20 .`` — the forest commencement
    clause has no extractable date; the lens emits a dateless COMMENCEMENT with no
    FIXED_DATE to pair. Both sides produce the empty shared subset → 0 delta with
    nothing compared (the honest no-guess outcome).
    """
    body = "Tämä laki tulee voimaan päivänä kuuta 20 ."
    statute_id = "2026/500"
    forest_keys = _forest_keys_for(body, statute_id)
    lens_keys = lens_temporal_keys_for_text(body)
    assert forest_keys == set(), sorted(forest_keys)
    assert lens_keys == set(), sorted(lens_keys)
    diff = diff_forest_vs_lens_temporal_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta


# ── projection shape sanity ──────────────────────────────────────────────────


def test_projection_is_gated_by_temporal_family_membership() -> None:
    """The projection emits facts only for segments the temporal family gated.

    A pure-prose provision with no temporal cue carries no temporal family
    ownership on any leaf and therefore projects no temporal segment.
    """
    body = "Viranomaisen on tehtävä päätös viivytyksettä."
    statute_id = "2026/600"
    forest = _forest_for(body, statute_id)
    assert not any("temporal" in n.families for n in forest.syntax_nodes.values())
    assert project_forest_temporal(forest, body) == ()


def test_projected_temporal_anchors_to_enclosing_segment() -> None:
    """Each projected temporal segment anchors to a real structural segment node."""
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."
    statute_id = "2026/700"
    forest = _forest_for(body, statute_id)
    projected = project_forest_temporal(forest, body)
    assert projected, "expected one projected temporal segment"
    p = projected[0]
    assert p.segment_node_id in forest.syntax_nodes
    assert "tulee voimaan" in body[p.char_start : p.char_end]
    assert p.clauses


# ── the PRODUCTION node-seed flip projection (doc-6 partial strangle-flip) ────


_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(*paras: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paras)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


def _seed_fp(seed) -> tuple:
    """A node-identity fingerprint of a temporal_expr seed (span/discr/payload)."""
    ref = seed.source_ref
    payload = tuple(
        sorted(
            (k, tuple(v) if isinstance(v, list) else v)
            for k, v in dict(seed.payload).items()
        )
    )
    return (
        seed.node_kind,
        seed.local_discriminator,
        seed.rule_id,
        seed.node_status,
        seed.authority_role,
        None if ref is None else (ref.char_start, ref.char_end, ref.text_hash),
        payload,
    )


def test_forest_temporal_seed_projection_emits_only_shared_slice() -> None:
    """``project_forest_temporal_seeds`` emits ONLY the shared fixed-term-expiry slice.

    A body carrying a dated commencement, a dated fixed-term expiry, and a bare
    date: the forest projection emits the fixed-term-expiry node (the flippable
    shared slice) and NOTHING else — the commencement cue / its FIXED_DATE date /
    bare dates are residual kinds the projection does not own.
    """
    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.legal_surface.lenses.temporal import (
        FOREST_SHARED_TEMPORAL_KINDS,
    )
    from lawvm.finland.legal_surface.temporal_projection import (
        project_forest_temporal_seeds,
    )

    bundle = build_surface_bundle(
        _xml(
            "Tämä laki tulee voimaan 1.1.2027.",
            "Tämä laki on voimassa 31 päivään joulukuuta 2030 saakka.",
        ),
        "2026/710",
    )
    seeds = project_forest_temporal_seeds(bundle)
    assert seeds, "expected the fixed-term-expiry slice from the forest projection"
    kinds = {s.payload["temporal_kind"] for s in seeds}
    assert kinds == {k.value for k in FOREST_SHARED_TEMPORAL_KINDS}
    assert all(s.payload["temporal_kind"] == "fixed_term_expiry" for s in seeds)


def test_production_temporal_facts_derive_from_forest_and_total_is_identical() -> None:
    """The partial flip happened (shared slice from the forest) AND the total is 0-delta.

    (1) The production lens's fixed-term-expiry node(s) are minted by the cached
        forest projection (``project_forest_temporal_seeds``);
    (2) the TOTAL production node set is node-identical (span / discriminator /
        payload) to the pre-flip whole-unit scan, kept as the golden reference
        (``temporal_seeds_for_unit``) — the 0-delta flip gate.
    """
    from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.legal_surface.lenses.temporal import (
        TemporalLens,
        temporal_seeds_for_unit,
    )
    from lawvm.finland.legal_surface.temporal_projection import (
        project_forest_temporal_seeds,
    )

    bundle = build_surface_bundle(
        _xml(
            "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027.",
            "Tämä laki on voimassa 31 päivään joulukuuta 2030 saakka.",
            "Hakemus on tehtävä 1.6.2028.",
            "Tämä asetus on voimassa 31.12.2029 saakka.",
        ),
        "2026/720",
    )

    # (1) the shared slice is non-empty and comes from the forest projection.
    forest_slice = project_forest_temporal_seeds(bundle)
    assert forest_slice, "expected fixed-term-expiry nodes from the forest"
    assert {s.payload["temporal_kind"] for s in forest_slice} == {"fixed_term_expiry"}

    # (2) the production lens's TOTAL node set == the golden-reference whole-unit
    #     scan (the shared slice routed through the forest + residuals from scan).
    lens = TemporalLens()
    prod_seeds = lens.analyze(bundle, context=SurfaceAnalysisContext()).node_seeds
    golden = [s for u in bundle.units for s in temporal_seeds_for_unit(u)]
    assert {_seed_fp(s) for s in prod_seeds} == {_seed_fp(s) for s in golden}
    assert len(prod_seeds) == len(golden)

    # the forest slice's seeds are PRESENT in the production set (the flip routed
    # the shared slice through the forest, byte-identically).
    prod_fps = {_seed_fp(s) for s in prod_seeds}
    assert {_seed_fp(s) for s in forest_slice} <= prod_fps


# ── corpus gate: WHICH temporal kinds the forest gate reproduces 0-delta ──────
#
# The committed boundary of standing task #27 Lane T (the temporal non-shared
# flip). The forest temporal GATE keys on temporal-family OWNERSHIP (commencement
# / validity / application / delegation cue-bearing segments). The shared
# fixed-term-expiry slice is gate-reproducible 0-delta corpus-wide (the landed
# flip); every OTHER temporal kind has golden seeds in segments the temporal
# family does NOT gate (a bare ``fixed_date`` with no cue, the ``alkaen`` duration
# anchor, the ``kunnes`` event bound), so routing those kinds through the gate
# would SILENTLY DROP nodes — NOT 0-delta. This test LOCKS that boundary: the
# shared slice stays gate-reproduced, the non-shared kinds stay gate-UNreproduced,
# and the gate never over-produces. A future "flip the rest" attempt that breaks
# any of these assertions is, by construction, a non-0-delta producer change.


def _corpus_available() -> bool:
    try:
        from farchive import Farchive

        from lawvm.finland.transparent_store import TransparentCorpusStore
        from lawvm.tools.parse_bench import _archive_path

        store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
        return store.read_source("1999/731") is not None
    except Exception:
        return False


def _iter_corpus_bundles(limit: int, min_year: int):
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import (
        build_surface_bundle,
        decode_body_text,
    )
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit and limit < len(ids):
        step = len(ids) / limit
        ids = [ids[int(i * step)] for i in range(limit)]
    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            if not decode_body_text(xb):
                continue
            yield sid, build_surface_bundle(xb, sid)
        except Exception:
            # A statute whose substrate build fails loud (e.g. a provision-index
            # alignment refusal) is not part of THIS boundary measurement; skip it.
            continue


@pytest.mark.skipif(not _corpus_available(), reason="canonical corpus not available")
def test_corpus_forest_gate_reproduces_only_shared_expiry_slice() -> None:
    """Corpus gate: only fixed-term-expiry is forest-gate 0-delta; the rest miss.

    Aggregates :func:`classify_forest_temporal_gate_coverage` over a corpus slice.
    Asserts the committed Lane-T boundary:

      * ``fixed_term_expiry`` is REPRODUCED (gate == golden) on every statute that
        carries it — the landed 0-delta flip, proven corpus-wide here;
      * the lens-only kinds (``fixed_date`` / ``commencement`` /
        ``duration_from_commencement`` / ``event_bound`` / ``validity_open``)
        accumulate a NON-ZERO corpus miss — they are NOT gate-reproducible and so
        MUST stay lens-produced (flipping them would silently drop nodes);
      * the gate NEVER over-produces (corpus ``extra`` is empty for every kind).
    """
    from collections import Counter

    from lawvm.finland.legal_surface.temporal_projection import (
        classify_forest_temporal_gate_coverage,
    )

    total_missed: Counter[str] = Counter()
    total_extra: Counter[str] = Counter()
    expiry_seen = False
    expiry_ever_missed = False
    statutes = 0

    for _sid, bundle in _iter_corpus_bundles(limit=800, min_year=0):
        cov = classify_forest_temporal_gate_coverage(bundle)
        statutes += 1
        for kind, n in cov.missed.items():
            total_missed[kind] += n
        for kind, n in cov.extra.items():
            total_extra[kind] += n
        if "fixed_term_expiry" in cov.reproduced:
            expiry_seen = True
        if cov.missed.get("fixed_term_expiry", 0):
            expiry_ever_missed = True

    assert statutes > 100, f"corpus slice too small ({statutes})"

    # (1) the gate never over-produces — it is a span-local re-scan of a strict
    #     subset of the body.
    assert not total_extra, f"forest gate over-produced: {dict(total_extra)}"

    # (2) the shared fixed-term-expiry slice is gate-reproduced and NEVER missed.
    assert expiry_seen, "expected fixed_term_expiry in the corpus slice"
    assert not expiry_ever_missed, "fixed_term_expiry must be gate-0-delta corpus-wide"

    # (3) the lens-only kinds are NOT gate-reproducible — a non-zero corpus miss
    #     proves flipping them would silently drop nodes (the Lane-T NO-GO).
    for lens_only_kind in (
        "fixed_date",
        "commencement",
        "duration_from_commencement",
        "event_bound",
    ):
        assert total_missed.get(lens_only_kind, 0) > 0, (
            f"{lens_only_kind} is unexpectedly gate-reproducible — re-investigate "
            f"whether it became flippable (missed={dict(total_missed)})"
        )
