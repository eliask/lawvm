"""Tests for the `lawvm fi-parse` visualization renderers.

The bulk of the assertions run on pure-text inputs (johtolause + morph), which
need no corpus. One forest test is corpus-gated and skips when the archive is
unavailable.
"""

from __future__ import annotations

import pytest

from lawvm.tools import fi_parse_view
from lawvm.corpus_store import get_corpus_store


def _corpus_available() -> bool:
    try:
        return get_corpus_store().read_source("1987/1250") is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JOHTOLAUSE view (pure text)
# ---------------------------------------------------------------------------


def test_johtolause_range_expands_to_four_section_targets() -> None:
    text = (
        "Muutetaan ulkomaalaislain (301/2004) 69 d– 69 g §, "
        "sellaisina kuin ne ovat laissa 1218/2013, seuraavasti:"
    )
    view = fi_parse_view.build_johtolause_view(text)
    assert view["section_target_count"] == 4
    labels = [
        nd["label"]
        for vg in view["verb_groups"]
        for nd in vg["nodes"]
        if nd["node_type"] == "SurfaceTargetRef"
    ]
    assert labels == ["69d", "69e", "69f", "69g"]
    # Every expanded target is a SECTION.
    kinds = {
        nd["kind"]
        for vg in view["verb_groups"]
        for nd in vg["nodes"]
        if nd["node_type"] == "SurfaceTargetRef"
    }
    assert kinds == {"SECTION"}


def test_johtolause_repeal_renders_targets_and_ops() -> None:
    view = fi_parse_view.build_johtolause_view(
        "Kumotaan lain 5 §:n 2 momentti ja 7 §."
    )
    assert view["parse_error"] is None
    assert len(view["parsed_ops"]) == 2
    rendered = fi_parse_view.render_johtolause_view(view)
    assert "VERB GROUP: KUMOTA" in rendered
    assert "SECTION '5'" in rendered
    assert "mom=2" in rendered
    assert "SECTION targets: 2" in rendered


# ---------------------------------------------------------------------------
# MORPH view (pure text)
# ---------------------------------------------------------------------------


def test_morph_generates_inessive_of_laki() -> None:
    view = fi_parse_view.build_morph_view("laki")
    surfaces = {
        (f["case"], f["number"]): f["surface"]
        for para in view["paradigms"]
        for f in para["forms"]
    }
    assert surfaces[("inessive", "singular")] == "laissa"


def test_morph_analysis_inverts_laissa_to_laki() -> None:
    view = fi_parse_view.build_morph_view("laissa")
    assert view["analysis"] == ["laki"]
    assert view["analysis_status"] == "unique"
    # Because laissa inverts to laki, the laki paradigm is generated and
    # contains laissa as its inessive.
    surfaces = {
        f["surface"] for para in view["paradigms"] for f in para["forms"]
    }
    assert "laissa" in surfaces


def test_morph_analysis_seam_inverts_directly() -> None:
    assert fi_parse_view.analyze_surface("laissa") == ("laki",)


def test_morph_unknown_word_is_honest_unknown() -> None:
    view = fi_parse_view.build_morph_view("zzqqxx")
    assert view["analysis"] == []
    assert view["analysis_status"] == "unknown"
    rendered = fi_parse_view.render_morph_view(view)
    assert "unknown" in rendered.lower()


# ---------------------------------------------------------------------------
# CLAUSES view (pure text)
# ---------------------------------------------------------------------------


def test_clause_view_segments_raw_text() -> None:
    text = "Tämä on ensimmäinen virke. Tämä on toinen virke."
    view = fi_parse_view.build_clause_view("text#inline", text, 0, len(text))
    assert len(view["sentences"]) == 2
    rendered = fi_parse_view.render_clause_view(view)
    assert "sentence(s)" in rendered


# ---------------------------------------------------------------------------
# FOREST view (corpus-gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _corpus_available(), reason="corpus archive not available")
def test_forest_reference_np_and_modal_leaves() -> None:
    bundle, unit = fi_parse_view._load_statute_body("2004/301")
    body = unit.raw_text
    forest = fi_parse_view._build_forest(bundle, unit)
    lo, hi = fi_parse_view._resolve_span(
        body, grep="Palvelumaksua alentavana", provision=None, unit=unit
    )
    view = fi_parse_view.build_forest_view(forest, body, lo, hi)

    def _leaves(trees: list[dict]) -> list[dict]:
        out: list[dict] = []
        for node in trees:
            out.append(node)
            out.extend(_leaves(node["children"]))
        return out

    leaves = _leaves(view["trees"])
    ref_texts = [n["text"].strip() for n in leaves if n["kind"] == "reference_np"]
    modal_texts = [n["text"].strip() for n in leaves if n["kind"] == "modal_predicate"]
    # The 69 d–69 g range reference is whole (post-fix), not truncated.
    assert any("69 d–69 g §:ssä" == t for t in ref_texts)
    assert "säädetään" in modal_texts
    assert view["coverage"]["is_partition"] is True
