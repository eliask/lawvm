"""Tests for the popular-act-name -> originating-act-key registry and its lane.

Two layers:

- SYNTHETIC: a handful of registry entries exercise every behaviour — name
  normalization (article/`, as amended` tail/diacritics/curly quotes), the
  single-act RESOLVED case, the multi-act AMBIGUOUS refusal (§1.7), the UNMAPPED
  refusal, the ``Section <sec> of the <Act Name>`` citation parser, and the
  end-to-end act-name -> Table III lane wired into
  :func:`resolve_nonpositive_target` (which only fires when the GPO href/paren and
  direct-Table III channels miss).
- REAL CORPUS: when the packaged registry and the farchive Table III are present,
  a real named-act citation (the Social Security Act) resolves through the whole
  lane to its codified USC address, and a known-ambiguous name is refused.
"""

from __future__ import annotations

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.act_name_registry import (
    ActNameStatus,
    PopularNameRegistry,
    normalize_act_name,
    reset_default_act_name_registry,
)
from lawvm.us_federal.import_table3 import Table3Record
from lawvm.us_federal.nonpositive import (
    ACT_NAME_AMBIGUOUS_FINDING_RULE_ID,
    RULE_ACT_NAME_TABLE3,
    NonPositiveResolveStatus,
    parse_named_act_citation,
    resolve_nonpositive_target,
)
from lawvm.us_federal.table3 import Table3Resolver


def _entries() -> list[dict]:
    """A tiny grounded registry: one clean name, one ambiguous name."""
    return [
        {
            "name": "securities act of 1933",
            "acts": [
                {
                    "raw_name": "Securities Act of 1933",
                    "act_key": "38",
                    "usc_title": "15",
                    "usc_node": "/us/usc/t15/s77a",
                    "origin_ref": "/us/act/1933-05-27/ch38",
                }
            ],
        },
        {
            "name": "controlled substances act",
            "acts": [
                {"raw_name": "Controlled Substances Act", "act_key": "675",
                 "usc_title": "21", "usc_node": "/us/usc/t21/s801", "origin_ref": "x"},
                {"raw_name": "Controlled Substances Act", "act_key": "91-513",
                 "usc_title": "21", "usc_node": "/us/usc/t21/s801", "origin_ref": "y"},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def test_normalize_folds_article_tail_quotes_and_case() -> None:
    base = "securities act of 1933"
    assert normalize_act_name("The Securities Act of 1933") == base
    assert normalize_act_name("Securities Act of 1933, as amended") == base
    assert normalize_act_name("Securities Act of 1933 (as amended)") == base
    # Curly apostrophe + extra whitespace fold to the same key.
    assert normalize_act_name("  Workers’  Comp Act ") == normalize_act_name("Workers' Comp Act")
    assert normalize_act_name("") == ""


# ---------------------------------------------------------------------------
# Registry resolution discipline
# ---------------------------------------------------------------------------


def test_registry_resolves_single_grounded_name_with_witness() -> None:
    reg = PopularNameRegistry(_entries())
    res = reg.resolve("the Securities Act of 1933")
    assert res.status is ActNameStatus.RESOLVED
    assert res.resolved
    assert res.act_key == "38"
    assert res.witness is not None
    assert res.witness.usc_node == "/us/usc/t15/s77a"
    assert res.witness.origin_ref == "/us/act/1933-05-27/ch38"
    assert reg.resolve_act_key("Securities Act of 1933") == "38"


def test_registry_refuses_ambiguous_name() -> None:
    reg = PopularNameRegistry(_entries())
    res = reg.resolve("Controlled Substances Act")
    assert res.status is ActNameStatus.AMBIGUOUS
    assert not res.resolved
    assert res.act_key == ""
    assert res.candidates == ("675", "91-513")
    # The convenience accessor also refuses (never guesses one of the two).
    assert reg.resolve_act_key("Controlled Substances Act") == ""


def test_registry_refuses_unknown_name() -> None:
    reg = PopularNameRegistry(_entries())
    res = reg.resolve("Nonexistent Fictional Act of 2099")
    assert res.status is ActNameStatus.UNMAPPED
    assert not res.resolved
    assert res.candidates == ()


def test_registry_reports_counts() -> None:
    reg = PopularNameRegistry(_entries())
    assert reg.name_count == 2
    assert reg.ambiguous_count == 1


# ---------------------------------------------------------------------------
# Named-act citation parser
# ---------------------------------------------------------------------------


def test_parse_named_act_citation_extracts_name_and_section() -> None:
    cite = parse_named_act_citation(
        "Section 1902(a) of the Social Security Act is amended"
    )
    assert cite is not None
    assert cite.act_name == "Social Security Act"
    assert cite.act_section == "1902(a)"


def test_parse_named_act_citation_stops_name_at_act_head() -> None:
    # A trailing "(N U.S.C. M)" parenthetical is NOT part of the act name.
    cite = parse_named_act_citation(
        "Section 5 of the Securities Act of 1933 (15 U.S.C. 77e) is amended"
    )
    assert cite is not None
    assert cite.act_name == "Securities Act of 1933"
    assert cite.act_section == "5"


def test_parse_named_act_citation_none_without_named_act() -> None:
    assert parse_named_act_citation("Section 5 of title 15 is amended") is None
    assert parse_named_act_citation("") is None


# ---------------------------------------------------------------------------
# End-to-end: act name -> registry -> Table III lane
# ---------------------------------------------------------------------------


def _t3_resolver() -> Table3Resolver:
    """A synthetic Table III with the Securities Act (chapter 38) classified."""
    return Table3Resolver(
        [
            Table3Record(
                act_num="38", act_congress="73", act_section="5",
                usc_title="15", usc_section="77e", status="",
                public_law="22", usckey="k77e",
            )
        ]
    )


def test_act_name_lane_resolves_when_other_channels_miss() -> None:
    reg = PopularNameRegistry(_entries())
    # No href, no parenthetical: the act-name lane is the sole resolver.
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933",
        raw_text="Section 5 of the Securities Act of 1933 is amended",
        table3=_t3_resolver(),
        act_name_registry=reg,
    )
    assert w.resolve_status is NonPositiveResolveStatus.ACT_NAME_TABLE3
    assert w.rule_id == RULE_ACT_NAME_TABLE3
    assert w.address == LegalAddress(path=(("title", "15"), ("section", "77e")))
    assert w.act_name == "Securities Act of 1933"
    assert w.act_name_key == "38"
    assert w.table3_usckey == "k77e"


def test_act_name_lane_does_not_preempt_existing_paren_channel() -> None:
    reg = PopularNameRegistry(_entries())
    # A parenthetical USC cite is present: the existing paren lane wins; the
    # act-name lane is purely additional and must NOT fire here.
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)",
        table3=_t3_resolver(),
        act_name_registry=reg,
    )
    assert w.resolve_status is NonPositiveResolveStatus.PAREN
    assert w.address == LegalAddress(path=(("title", "15"), ("section", "77e")))


def test_act_name_lane_refuses_ambiguous_name_distinctly() -> None:
    reg = PopularNameRegistry(_entries())
    w = resolve_nonpositive_target(
        target_phrase="Section 401 of the Controlled Substances Act",
        table3=_t3_resolver(),
        act_name_registry=reg,
    )
    # Refused (no address) but surfaced distinctly as an act-name ambiguity, with
    # the name auditable — never guessed onto one of the two acts (§1.7).
    assert w.address is None
    assert w.rule_id == ACT_NAME_AMBIGUOUS_FINDING_RULE_ID
    assert w.act_name == "Controlled Substances Act"


def test_act_name_lane_refuses_unknown_name() -> None:
    reg = PopularNameRegistry(_entries())
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Nonexistent Fictional Act of 2099",
        table3=_t3_resolver(),
        act_name_registry=reg,
    )
    assert w.address is None
    assert w.resolve_status is NonPositiveResolveStatus.UNMAPPED


# ---------------------------------------------------------------------------
# Real corpus (skips when the packaged registry / farchive Table III is absent)
# ---------------------------------------------------------------------------


def test_real_corpus_social_security_act_resolves_end_to_end() -> None:
    """The Social Security Act citation resolves through the whole lane.

    Skips on a build host without the packaged registry or the farchive Table III.
    'Section 1902(a) of the Social Security Act' -> originating act key 531 ->
    42 U.S.C. 1396a, with NO govinfo href/parenthetical (the structural miss this
    lane closes). A known-ambiguous popular name stays refused.
    """
    from lawvm.us_federal.act_name_registry import load_default_act_name_registry
    from lawvm.us_federal.table3 import (
        load_default_table3_resolver,
        reset_default_table3_resolver,
    )

    reset_default_act_name_registry()
    reset_default_table3_resolver()
    registry = load_default_act_name_registry()
    table3 = load_default_table3_resolver()
    if registry is None:
        pytest.skip("popular-name registry data not packaged")
    if table3 is None:
        pytest.skip("Table III bulk XML not present in farchive")

    # Registry: a marquee old act grounds to its chapter key; ambiguity refused.
    assert registry.resolve("Social Security Act").act_key == "531"
    assert registry.resolve("Controlled Substances Act").status is ActNameStatus.AMBIGUOUS

    # End-to-end through the lane (no href, no paren): Social Security Act §1902
    # classifies to 42 U.S.C. 1396a.
    w = resolve_nonpositive_target(
        target_phrase="Section 1902(a) of the Social Security Act",
        raw_text="Section 1902(a) of the Social Security Act is amended",
        table3=table3,
        act_name_registry=registry,
    )
    assert w.resolve_status is NonPositiveResolveStatus.ACT_NAME_TABLE3
    assert w.rule_id == RULE_ACT_NAME_TABLE3
    assert w.address is not None
    assert w.address.path[0] == ("title", "42")
    assert w.address.path[1] == ("section", "1396a")
    assert w.act_name_key == "531"
