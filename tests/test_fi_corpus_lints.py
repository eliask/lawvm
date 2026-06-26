"""Tests for the cross-statute structural type-mismatch lint (corpus_lints.py).

Synthetic statutes are built so the corpus graph asserts provision-level
``refers_to`` edges into shared ``legal_address_entity`` nodes; the lint then
loads each target body, parses its structure, and flags cited paths whose
structural TYPE or leaf-existence disagrees with the actual target structure.

Cases:
  (a) clean cite that matches the target structure        → no lint
  (b) cite naming a momentti the target lacks (out of range) → target_provision_absent
  (c) cite naming a momentti where the target is flat     → structural_type_mismatch
  (d) target body absent from the store                   → no false finding
  (e) firewall: every lint surface_only / not legal_conclusion / not replay
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_lints import run_lint_passes
from lawvm.finland.legal_surface.corpus_graph import build_corpus_surface_graph
from lawvm.finland.legal_surface.corpus_lints import (
    KIND_ABSENT,
    KIND_MISMATCH,
    CorpusTypeMismatchLintPass,
    lint_corpus_type_mismatches,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Target act B = 2022/711. Citers A/C/D/E cite provisions inside B.
_B_ID = "2022/711"
_A_ID = "2003/314"  # cites B 5 §:n 2 momentti  (exists → clean)
_C_ID = "2010/100"  # cites B 5 §:n 9 momentti  (out of range → absent)
_D_ID = "2015/500"  # cites B 7 §:n 1 momentti  (7 § is flat → type mismatch)
_E_ID = "2018/200"  # cites B 5 §:n 2 momentti — same as A (de-dup target)


def _citer_xml(citing_section: str, ref_href: str, surface: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
        f"<section><num>{citing_section} §</num><paragraph><content>"
        f'<p>Noudatetaan, mitä <ref href="{ref_href}">{surface}</ref> '
        "säädetään.</p>"
        "</content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


def _href(provision: str) -> str:
    # provision like "sec_5__subsec_2" — the resolver derives the address tail
    # from the citing TEXT (target_provision_ref), not the href, so the href
    # only needs to anchor the act; the tail comes from the by-name surface.
    return f"/akn/fi/act/statute-consolidated/2022/711#{provision}"


# B's body: 5 § has TWO momenttia (subsections); 7 § is FLAT (no subsections).
_B_XML = (
    f'<akomaNtoso xmlns="{_AKN}"><act><body>'
    "<section><num>5 §</num>"
    "<subsection><content><p>Ensimmäinen momentti.</p></content></subsection>"
    "<subsection><content><p>Toinen momentti.</p></content></subsection>"
    "</section>"
    "<section><num>7 §</num>"
    "<paragraph><content><p>Flat-section content, ei momentteja.</p></content></paragraph>"
    "</section>"
    "</body></act></akomaNtoso>"
).encode("utf-8")


class _StubCandidate:
    def __init__(self, statute_id: str) -> None:
        self.statute_id = statute_id


class _StubLookupResult:
    def __init__(self, candidates: list[str]) -> None:
        self.registry_status = {0: "none", 1: "single"}.get(len(candidates), "multiple")
        self.candidates = tuple(_StubCandidate(c) for c in candidates)


class _StubStatuteRegistry:
    def __init__(self, table: dict[str, list[str]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: object = None) -> _StubLookupResult:
        return _StubLookupResult(self._table.get(name, []))


class _DictStore:
    def __init__(self, xml: dict[str, bytes]) -> None:
        self._xml = xml

    def read_oracle(self, sid: str) -> bytes | None:
        return self._xml.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return None

    def read_amendment(self, sid: str) -> bytes | None:
        return None


# The by-name surface that names a concrete momentti inside lannoitelaki. The
# Finnish reference recognizers parse the structural tail from this text into the
# mention's target_provision_ref, which the corpus graph promotes to an address.
def _cite_text(tail_phrase: str) -> str:
    return f"lannoitelain {tail_phrase}"


_CITER_XML = {
    _A_ID: _citer_xml("3", _href("sec_5"), _cite_text("5 §:n 2 momentin")),
    _C_ID: _citer_xml("4", _href("sec_5"), _cite_text("5 §:n 9 momentin")),
    _D_ID: _citer_xml("2", _href("sec_7"), _cite_text("7 §:n 1 momentin")),
    _E_ID: _citer_xml("6", _href("sec_5"), _cite_text("5 §:n 2 momentin")),
}


def _registry() -> _StubStatuteRegistry:
    return _StubStatuteRegistry({"lannoitelaki": [_B_ID]})


# The by-name recognizer captures the FULL momentti tail (e.g. 5/2) but anchors
# the deep address under the by-name placeholder work_id (``fi-name:lannoitelaki``)
# rather than the registry-canonical id; the <ref> href lane carries the
# canonical id but only the section tail. To exercise the lint deterministically
# we discover the actual address-entity work_ids in the built graph and serve B's
# body under EVERY work_id that carries a 5/* or 7/* address (the lint reads the
# body keyed by the address entity's own work_id, which is exactly what it does
# in production over the real corpus).


def _address_work_ids(graph) -> set[str]:
    out: set[str] = set()
    for node in graph.nodes.values():
        if node.node_kind != "legal_address_entity":
            continue
        addr = node.payload.get("address")
        wid = node.payload.get("work_id")
        if isinstance(addr, str) and isinstance(wid, str) and (
            addr.startswith("5") or addr.startswith("7")
        ):
            out.add(wid)
    return out


def _build_graph(include_target: bool = True):
    store_xml = dict(_CITER_XML)
    if include_target:
        store_xml[_B_ID] = _B_XML
    # First pass: discover which work_id(s) the address entities are keyed under.
    probe = build_corpus_surface_graph(
        [_A_ID, _C_ID, _D_ID, _E_ID], _DictStore(dict(_CITER_XML)),
        statute_registry=_registry(),
    )
    body_keys = _address_work_ids(probe) if include_target else set()
    for wid in body_keys:
        store_xml.setdefault(wid, _B_XML)
    store = _DictStore(store_xml)
    ids = [_A_ID, _C_ID, _D_ID, _E_ID] + ([_B_ID] if include_target else [])
    graph = build_corpus_surface_graph(ids, store, statute_registry=_registry())
    return graph, store


def _addr_entities_with(graph, tail: str) -> list[str]:
    """All address-entity node ids whose address tail equals ``tail``."""
    return [
        nid
        for nid, n in graph.nodes.items()
        if n.node_kind == "legal_address_entity" and n.payload.get("address") == tail
    ]


def test_clean_cite_matches_no_lint() -> None:
    graph, store = _build_graph()
    addr_5_2 = _addr_entities_with(graph, "5/2")
    assert addr_5_2, "expected a 5/2 momentti address target to exist"
    lints = lint_corpus_type_mismatches(graph, store)
    # 5 §:n 2 momentti EXISTS in B → no lint targets it.
    bad = [lint for lint in lints if set(addr_5_2) & set(lint.support_node_ids)]
    assert bad == [], f"clean 5/2 cite should not be flagged: {bad}"


def test_out_of_range_momentti_is_absent() -> None:
    graph, store = _build_graph()
    addr_5_9 = _addr_entities_with(graph, "5/9")
    assert addr_5_9, "expected a 5/9 momentti address target to exist"
    lints = lint_corpus_type_mismatches(graph, store)
    flagged = [lint for lint in lints if set(addr_5_9) & set(lint.support_node_ids)]
    assert flagged, "5 §:n 9 momentti is out of range and must be flagged absent"
    assert all(lint.lint_kind == KIND_ABSENT for lint in flagged)
    # Self-evidencing: the message embeds the offending surface text + counts.
    assert any("9" in lint.message and "momentti" in lint.message for lint in flagged)


def test_flat_section_momentti_is_type_mismatch() -> None:
    graph, store = _build_graph()
    addr_7_1 = _addr_entities_with(graph, "7/1")
    assert addr_7_1, "expected a 7/1 momentti address target to exist"
    lints = lint_corpus_type_mismatches(graph, store)
    flagged = [lint for lint in lints if set(addr_7_1) & set(lint.support_node_ids)]
    assert flagged, "momentti cite into a flat section must be a type mismatch"
    assert all(lint.lint_kind == KIND_MISMATCH for lint in flagged)
    assert any("flat section" in lint.message for lint in flagged)


def test_target_body_absent_no_false_finding() -> None:
    # B's body is NOT in the store: every cited path is unverifiable, so the lint
    # must emit NOTHING (tag-don't-guess), never a fabricated mismatch.
    graph, store = _build_graph(include_target=False)
    lints = lint_corpus_type_mismatches(graph, store)
    assert lints == [], f"absent target body must yield no findings, got {lints}"


def test_firewall_every_lint_is_surface_only() -> None:
    graph, store = _build_graph()
    lints = lint_corpus_type_mismatches(graph, store)
    for lint in lints:
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.replay_authorized is False
        assert lint.forbidden_overclaims  # non-empty, as the validator requires


def test_lint_pass_runs_through_runner_and_validates() -> None:
    # The pass-shaped wrapper must satisfy run_lint_passes (which enforces the
    # firewall and node-membership) without raising.
    graph, store = _build_graph()
    report = run_lint_passes(graph, (CorpusTypeMismatchLintPass(store=store),))
    # Every returned lint subject/support node is in the graph (runner-validated).
    node_ids = set(graph.nodes)
    for lint in report.lints:
        assert lint.subject_node_id in node_ids
        assert all(s in node_ids for s in lint.support_node_ids)
        assert lint.lint_kind in (KIND_ABSENT, KIND_MISMATCH)


# ── unit-level checks (no recognizer dependency) ─────────────────────────────


def test_parse_address_tail_rejects_unmodeled_paths() -> None:
    from lawvm.finland.legal_surface.corpus_lints import _parse_address_tail

    assert _parse_address_tail("") is None
    assert _parse_address_tail("5/2/3/4") is None  # deeper than kohta
    assert _parse_address_tail("5/x") is None  # non-integer momentti
    assert _parse_address_tail("5/0") is None  # ordinal < 1
    p = _parse_address_tail("5/2/3")
    assert p is not None and p.depth == 3 and p.subsection == 2 and p.item == 3


def test_check_citation_is_silent_when_section_absent() -> None:
    from lawvm.finland.legal_surface.corpus_lints import (
        _check_citation,
        _parse_address_tail,
        _parse_target_sections,
    )

    sections = _parse_target_sections(_B_XML)
    assert sections is not None
    # cite into 99 § (not in B) → silent (broken-detection territory, not ours).
    path = _parse_address_tail("99/1")
    assert path is not None
    assert _check_citation(path, sections) is None


def test_check_citation_unit_cases() -> None:
    from lawvm.finland.legal_surface.corpus_lints import (
        _check_citation,
        _parse_address_tail,
        _parse_target_sections,
    )

    sections = _parse_target_sections(_B_XML)
    assert sections is not None

    path_5_2 = _parse_address_tail("5/2")
    path_5_9 = _parse_address_tail("5/9")
    path_7_1 = _parse_address_tail("7/1")
    path_5 = _parse_address_tail("5")
    assert path_5_2 is not None
    assert path_5_9 is not None
    assert path_7_1 is not None
    assert path_5 is not None

    # 5/2 exists (5 § has 2 momenttia) → no finding.
    assert _check_citation(path_5_2, sections) is None
    # 5/9 out of range → absent.
    f_absent = _check_citation(path_5_9, sections)
    assert f_absent is not None and f_absent.kind == KIND_ABSENT
    # 7/1 into flat 7 § → type mismatch.
    f_mismatch = _check_citation(path_7_1, sections)
    assert f_mismatch is not None and f_mismatch.kind == KIND_MISMATCH
    # bare 5 § → nothing deeper to disagree about.
    assert _check_citation(path_5, sections) is None


def test_unparseable_target_body_is_silent() -> None:
    from lawvm.finland.legal_surface.corpus_lints import _parse_target_sections

    assert _parse_target_sections(b"<not-xml<<<") is None


# ── real-corpus smoke ────────────────────────────────────────────────────────


def _real_store_or_skip():
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        pytest.skip("LAWVM_CANONICAL_DATA_ROOT not set; real-corpus smoke skipped")
    archive = os.path.join(root, "data", "finlex.farchive")
    if not os.path.exists(archive):
        pytest.skip(f"farchive absent at {archive}; real-corpus smoke skipped")
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    return TransparentCorpusStore(Farchive(archive))


def test_real_corpus_smoke_produces_well_formed_lints() -> None:
    store = _real_store_or_skip()
    from lawvm.finland.references.registries import eu_nickname
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
        load_statute_name_registry,
    )

    artifact = default_artifact_path()
    if not artifact.exists():
        pytest.skip(f"statute-name registry artifact absent at {artifact}")
    statute_registry = load_statute_name_registry(artifact)

    ids = store.list_statute_ids()[:40]
    assert ids, "expected at least some statute ids in the corpus"

    graph = build_corpus_surface_graph(
        ids,
        store,
        statute_registry=statute_registry,
        eu_registry=eu_nickname,
    )
    lints = lint_corpus_type_mismatches(graph, store)

    # Every produced lint must be well-formed and firewall-clean; the runner
    # additionally validates node membership.
    report = run_lint_passes(graph, (CorpusTypeMismatchLintPass(store=store),))
    assert {lint.lint_id for lint in report.lints} == {lint.lint_id for lint in lints}
    for lint in lints:
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.lint_kind in (KIND_ABSENT, KIND_MISMATCH)
        assert lint.message  # self-evidencing, non-empty
