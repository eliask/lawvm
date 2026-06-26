"""Unit + e2e tests for the EU regulation ingest + FI→EU reference resolution.

The unit tests drive ``export_eu_regulation_pack`` with SYNTHETIC ``EuStructNode``
lists (the ``nodes=`` injection path) and synthetic relation edges, so they run in
milliseconds and never touch the real Formex / FI corpus. They assert the
load-bearing invariants:

* the consolidated Formex parse yields addressable nodes whose ids are derived
  from the ``IDENTIFIER`` (``entity:celex:32016R0679#006`` /
  ``...#006.001``) — and the ``grafter`` raises on the ``CONS.ACT`` root, so the
  direct parse is the consolidated path;
* the Work id is the language-NEUTRAL ``celex:32016R0679`` (NEVER a FI-specific
  work id) — the §25.8 identity discipline;
* a FI reference whose target carries a resolvable article window → a RESOLVED
  edge whose ``target_set`` is the ingested node id, ``status=resolved`` /
  ``registry_resolved`` on the ``surface`` plane (matrix-legal);
* a FI reference to a NON-ingested CELEX (or with no article window) STAYS opaque
  (honest typed residue — no fabricated resolution);
* the resolved edge is matrix-legal (``edge_authority_violation`` is None);
* the emitted EU work pack is ``check-pack`` VALID.

The e2e test (``-m slow``) uses the real ``.tmp/eulex`` Formex + the FI corpus to
prove the "effective Finnish law in one set" payoff on GDPR end to end.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from lawvm.substrate.checker import CheckMode, IntegrityVerdict, check_pack
from lawvm.substrate.eu_ingest import (
    EuStructNode,
    IngestedEuWork,
    export_eu_regulation_pack,
    load_eu_pack_for_check,
    parse_consolidated_formex,
    parse_fi_eu_target,
    resolve_fi_eu_edge,
    resolve_fi_eu_target,
)
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
)

_CELEX = "32016R0679"
_REAL_FORMEX = (
    Path(__file__).resolve().parents[2] / ".tmp" / "eulex" / "gdpr_fi_formex_plain.xml"
)


# --------------------------------------------------------------------------- #
# Synthetic node fixtures (a tiny 1-division / 2-article / 3-parag work)       #
# --------------------------------------------------------------------------- #


def _entity(identifier: str) -> str:
    return f"entity:celex:{_CELEX}#{identifier}"


def _synthetic_nodes() -> list[EuStructNode]:
    return [
        EuStructNode(
            structural_kind="division",
            identifier="001",
            label="1",
            title="I LUKU Yleiset säännökset",
            text="I LUKU Yleiset säännökset",
            address_path="division:001",
            entity_node_id=_entity("div.001"),
            article_number="",
            parag_number=None,
        ),
        EuStructNode(
            structural_kind="article",
            identifier="006",
            label="6",
            title="6 artikla",
            text="6 artikla",
            address_path="division:001/article:006",
            entity_node_id=_entity("006"),
            article_number="006",
            parag_number=None,
        ),
        EuStructNode(
            structural_kind="paragraph",
            identifier="006.001",
            label="1",
            title="",
            text="1. Kasittely on lainmukaista ainoastaan ...",
            address_path="division:001/article:006/paragraph:006.001",
            entity_node_id=_entity("006.001"),
            article_number="006",
            parag_number="001",
        ),
        EuStructNode(
            structural_kind="article",
            identifier="089",
            label="89",
            title="89 artikla",
            text="89 artikla",
            address_path="division:001/article:089",
            entity_node_id=_entity("089"),
            article_number="089",
            parag_number=None,
        ),
        EuStructNode(
            structural_kind="paragraph",
            identifier="089.002",
            label="2",
            title="",
            text="2. Kun henkilotietoja kasitellaan ...",
            address_path="division:001/article:089/paragraph:089.002",
            entity_node_id=_entity("089.002"),
            article_number="089",
            parag_number="002",
        ),
    ]


def _ingested_work() -> IngestedEuWork:
    return IngestedEuWork.from_nodes(celex=_CELEX, title="GDPR", nodes=_synthetic_nodes())


# --------------------------------------------------------------------------- #
# Target-string parsing (the FI serialized ProvisionRef form)                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "target,expected",
    [
        ("celex:32016R0679", ("32016R0679", None, None, None)),
        ("celex:32016R0679/6", ("32016R0679", "6", None, None)),
        ("celex:32016R0679/89/2", ("32016R0679", "89", "2", None)),
        ("celex:32016R0679/6/1/ka", ("32016R0679", "6", "1", "a")),
        ("celex:32016R0679/6/1/ke", ("32016R0679", "6", "1", "e")),
    ],
)
def test_parse_fi_eu_target(target: str, expected: tuple[str, str | None, str | None, str | None]) -> None:
    assert parse_fi_eu_target(target) == expected


def test_parse_fi_eu_target_rejects_non_celex() -> None:
    # An opaque ``eu/reg/...`` (eu_text_pattern) target is NOT a celex: target.
    assert parse_fi_eu_target("eu/reg/2016/679") is None
    assert parse_fi_eu_target("1050/2018/6") is None


# --------------------------------------------------------------------------- #
# Resolution against an ingested work (article / kohta / alakohta depth)       #
# --------------------------------------------------------------------------- #


def test_resolve_article_only_window() -> None:
    work = _ingested_work()
    res = resolve_fi_eu_target("celex:32016R0679/89", work)
    assert res.is_resolved
    assert res.resolved == _entity("089")
    assert res.resolved_depth == "article"


def test_resolve_kohta_window_to_parag_node() -> None:
    work = _ingested_work()
    res = resolve_fi_eu_target("celex:32016R0679/89/2", work)
    assert res.is_resolved
    assert res.resolved == _entity("089.002")
    assert res.resolved_depth == "paragraph"


def test_resolve_alakohta_window_degrades_to_kohta() -> None:
    # "6 artiklan 1 kohdan a alakohta" → kohta node (points are not separately
    # IDENTIFIER-addressable in the Formex). Honest depth degradation, real node.
    work = _ingested_work()
    res = resolve_fi_eu_target("celex:32016R0679/6/1/ka", work)
    assert res.is_resolved
    assert res.resolved == _entity("006.001")
    assert res.resolved_depth == "paragraph"
    assert res.alakohta == "a"


def test_resolve_absent_node_stays_opaque() -> None:
    work = _ingested_work()
    # Article 999 is not in the work — no fabrication.
    res = resolve_fi_eu_target("celex:32016R0679/999", work)
    assert not res.is_resolved
    assert res.reason == "node_absent"


def test_resolve_non_ingested_celex_stays_opaque() -> None:
    work = _ingested_work()
    res = resolve_fi_eu_target("celex:32019R0881/3", work)
    assert not res.is_resolved
    assert res.reason == "not_ingested"
    assert res.celex == "32019R0881"


def test_resolve_no_ingested_work_stays_opaque() -> None:
    res = resolve_fi_eu_target("celex:32016R0679/6", None)
    assert not res.is_resolved
    assert res.reason == "not_ingested"


# --------------------------------------------------------------------------- #
# Edge upgrade (opaque → resolved) + matrix legality                           #
# --------------------------------------------------------------------------- #


def _opaque_edge(target: str, *, semantics: TargetSetSemantics = TargetSetSemantics.SINGLE) -> dict:
    return build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="struct:fi-source#abc",
        target_set=(target,),
        target_set_semantics=semantics,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"branch_id": "actual"},
        corpus_version="fi:corpus:test",
    )


def test_edge_rewritten_to_resolved_node() -> None:
    work = _ingested_work()
    edge = _opaque_edge("celex:32016R0679/89/2")
    er = resolve_fi_eu_edge(edge, work, corpus_version="fi:corpus:test")
    assert er.rewritten
    assert er.edge["target_set"] == [_entity("089.002")]
    assert er.edge["edge_status"] == EdgeStatus.RESOLVED.value
    assert er.edge["verification_level"] == VerificationLevel.REGISTRY_RESOLVED.value
    assert er.edge["authority_plane"] == AuthorityPlane.SURFACE.value
    # The opaque CELEX string is GONE from the resolved target_set.
    assert not any(
        str(t).startswith("celex:") for t in cast("list[str]", er.edge["target_set"])
    )


def test_resolved_edge_is_matrix_legal() -> None:
    work = _ingested_work()
    edge = _opaque_edge("celex:32016R0679/6/1/ka")
    er = resolve_fi_eu_edge(edge, work, corpus_version="fi:corpus:test")
    assert er.rewritten
    assert edge_authority_violation(er.edge) is None


def test_edge_with_non_ingested_celex_stays_opaque() -> None:
    work = _ingested_work()
    edge = _opaque_edge("celex:32019R0881/3")
    er = resolve_fi_eu_edge(edge, work, corpus_version="fi:corpus:test")
    assert not er.rewritten
    # The original opaque target is preserved — no fabrication.
    assert er.edge["target_set"] == ["celex:32019R0881/3"]


def test_edge_with_no_work_stays_opaque() -> None:
    edge = _opaque_edge("celex:32016R0679/6")
    er = resolve_fi_eu_edge(edge, None, corpus_version="fi:corpus:test")
    assert not er.rewritten
    assert er.edge["target_set"] == ["celex:32016R0679/6"]


def test_coordination_edge_all_targets_resolved() -> None:
    # "6 ja 89 artiklassa" → a 2-target ALL_VALID set; both resolve.
    work = _ingested_work()
    edge = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="struct:fi-source#abc",
        target_set=("celex:32016R0679/6", "celex:32016R0679/89"),
        target_set_semantics=TargetSetSemantics.ALL_VALID,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"branch_id": "actual"},
        corpus_version="fi:corpus:test",
    )
    er = resolve_fi_eu_edge(edge, work, corpus_version="fi:corpus:test")
    assert er.rewritten
    assert sorted(cast("list[str]", er.edge["target_set"])) == sorted(
        [_entity("006"), _entity("089")]
    )
    assert er.edge["target_set_semantics"] == TargetSetSemantics.ALL_VALID.value


# --------------------------------------------------------------------------- #
# Pack emission (synthetic nodes) — identity discipline + check-pack VALID     #
# --------------------------------------------------------------------------- #


def test_ingest_pack_work_id_is_language_neutral(tmp_path: Path) -> None:
    res, work = export_eu_regulation_pack(
        "",
        celex=_CELEX,
        out_dir=tmp_path / "pack",
        nodes=_synthetic_nodes(),
        created_at="2026-06-22T00:00:00+00:00",
    )
    # §25.8: the Work id is the language-NEUTRAL CELEX canonical id, NEVER a
    # FI-specific work id.
    assert res.work_id == "celex:32016R0679"
    assert work.work_id == "celex:32016R0679"
    assert "fi" not in res.work_id
    assert res.n_articles == 2
    assert res.n_parags == 2
    assert res.n_divisions == 1


def test_ingest_pack_checks_valid(tmp_path: Path) -> None:
    res, _ = export_eu_regulation_pack(
        "",
        celex=_CELEX,
        out_dir=tmp_path / "pack",
        nodes=_synthetic_nodes(),
        created_at="2026-06-22T00:00:00+00:00",
    )
    pack = load_eu_pack_for_check(res.out_dir)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert verdict.certification.value == "VALID_CLEAN"


def test_ingest_address_nodes_carry_entity_id(tmp_path: Path) -> None:
    res, _ = export_eu_regulation_pack(
        "",
        celex=_CELEX,
        out_dir=tmp_path / "pack",
        nodes=_synthetic_nodes(),
        created_at="2026-06-22T00:00:00+00:00",
    )
    import json

    base_rows = [
        json.loads(line)
        for line in (Path(res.out_dir) / "base" / "base.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entity_ids = {
        r["object"]["eu_entity_node_id"]
        for r in base_rows
        if r["object"].get("schema") == "lawvm.address_node.v1"
    }
    assert _entity("006") in entity_ids
    assert _entity("006.001") in entity_ids


def test_ingest_pack_is_deterministic(tmp_path: Path) -> None:
    nodes = _synthetic_nodes()
    created_at = "2026-06-22T00:00:00+00:00"
    r1, _ = export_eu_regulation_pack(
        "", celex=_CELEX, nodes=nodes, created_at=created_at, out_dir=tmp_path / "p1"
    )
    r2, _ = export_eu_regulation_pack(
        "", celex=_CELEX, nodes=nodes, created_at=created_at, out_dir=tmp_path / "p2"
    )
    assert r1.pack_id == r2.pack_id


# --------------------------------------------------------------------------- #
# Real-Formex parse (skipped when the acquired Formex is absent)               #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _REAL_FORMEX.exists(), reason="acquired GDPR Formex not present")
def test_real_formex_parses_to_addressable_nodes() -> None:
    nodes = parse_consolidated_formex(_REAL_FORMEX, celex=_CELEX)
    articles = [n for n in nodes if n.structural_kind == "article"]
    parags = [n for n in nodes if n.structural_kind == "paragraph"]
    assert len(articles) == 99
    assert len(parags) == 372
    # IDENTIFIER-derived ids.
    art6 = next(n for n in articles if n.article_number == "006")
    assert art6.entity_node_id == "entity:celex:32016R0679#006"


@pytest.mark.skipif(not _REAL_FORMEX.exists(), reason="acquired GDPR Formex not present")
def test_grafter_raises_on_consolidated_root() -> None:
    # STEP 0: the FMX4 grafter expects an ``ACT`` root; a CONSLEG ``CONS.ACT``
    # manifestation is not an ACT descendant, so it RAISES — documenting why the
    # direct consolidated parse is required.
    from lawvm.eu.grafter import parse_eu_regulation_ir

    with pytest.raises(ValueError, match="CONS.ACT"):
        parse_eu_regulation_ir(_REAL_FORMEX, celex=_CELEX)


# --------------------------------------------------------------------------- #
# E2E — the "effective Finnish law in one set" payoff on GDPR                  #
# --------------------------------------------------------------------------- #


@pytest.mark.slow
@pytest.mark.skipif(not _REAL_FORMEX.exists(), reason="acquired GDPR Formex not present")
def test_e2e_tietosuojalaki_eu_refs_resolve(tmp_path: Path) -> None:
    """Ingest GDPR, resolve tietosuojalaki's GDPR cites into article nodes."""
    from lawvm.substrate.exporter import _build_fi_relation_edges

    res, work = export_eu_regulation_pack(
        _REAL_FORMEX,
        celex=_CELEX,
        out_dir=tmp_path / "gdpr_pack",
        title="GDPR",
        created_at="2026-06-22T00:00:00+00:00",
    )
    # The GDPR work pack is VALID.
    pack = load_eu_pack_for_check(res.out_dir)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]

    # tietosuojalaki 1050/2018 lives at engine id 2018/1050.
    cv = "fi:corpus:test"
    edges = _build_fi_relation_edges(engine_id="2018/1050", corpus_version=cv)
    eu_edges = [
        e
        for e in edges
        if any(str(t).startswith("celex:") for t in cast("list[str]", e.get("target_set", [])))
    ]
    assert eu_edges, "tietosuojalaki should carry FI→GDPR article cites"

    n_resolved = 0
    example = None
    for e in eu_edges:
        er = resolve_fi_eu_edge(e, work, corpus_version=cv)
        if er.rewritten:
            n_resolved += 1
            for pt in er.per_target:
                if pt.is_resolved and pt.article == "6" and example is None:
                    example = pt

    # At least one FI→GDPR cite resolves to a real article-6 node that EXISTS.
    assert n_resolved > 0
    assert example is not None
    assert example.resolved is not None
    assert example.resolved.startswith("entity:celex:32016R0679#006")
    assert example.resolved in work.entity_ids
