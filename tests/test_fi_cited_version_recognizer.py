"""Tests for the item-scoped cited-version clause recognizer.

The recognizer (``references.cited_version``) owns the item-cited-version clause
grammar that the replay layer used to parse inline from amendment source
``raw_text``. These lock the typed-result contract: matched cue + cited statute
ids routed to the references statute-id constructor, a typed residual on an
unparsed cue, and a clean no-match.
"""
from __future__ import annotations

from lawvm.finland.references.cited_version import (
    CITED_VERSION_PARSE_RESIDUAL_RULE_ID,
    CitedVersionParseResidual,
    recognize_item_cited_version_clause,
)


def test_item_cited_version_clause_matches_and_routes_statute_id() -> None:
    clause = recognize_item_cited_version_clause(
        "muutetaan 5 §:n 2 kohta, sellaisena kuin se on laissa 123/2019", "5"
    )
    assert clause.matched is True
    # NUMBER/YEAR ``123/2019`` → canonical YEAR/NUMBER via the references
    # statute-id constructor.
    assert clause.cited_statute_ids == frozenset({"2019/123"})
    assert clause.residual is None


def test_item_cited_version_clause_asetus_head() -> None:
    clause = recognize_item_cited_version_clause(
        "muutetaan 7 §:n 1 kohta, sellaisena kuin se on asetuksessa 5/2020", "7"
    )
    assert clause.matched is True
    assert clause.cited_statute_ids == frozenset({"2020/5"})


def test_item_cited_version_clause_no_match_when_no_item_word() -> None:
    # No ``koht*`` item word in the target window → not an item-cited-version
    # clause; caller keeps the op.
    clause = recognize_item_cited_version_clause(
        "muutetaan 5 §:n 2 momentti, sellaisena kuin se on laissa 123/2019", "5"
    )
    assert clause.matched is False
    assert clause.cited_statute_ids == frozenset()
    assert clause.residual is None


def test_item_cited_version_clause_no_match_when_target_label_differs() -> None:
    clause = recognize_item_cited_version_clause(
        "muutetaan 5 §:n 2 kohta, sellaisena kuin se on laissa 123/2019", "9"
    )
    assert clause.matched is False


def test_item_cited_version_clause_residual_on_unparsed_id() -> None:
    # Cited-version cue present for the item target but no ``laissa/asetuksessa
    # N/YYYY`` id parses → typed residual, never a silent skip.
    clause = recognize_item_cited_version_clause(
        "muutetaan 5 §:n 2 kohta, sellaisena kuin se on aiemmin", "5"
    )
    assert clause.matched is True
    assert clause.cited_statute_ids == frozenset()
    assert isinstance(clause.residual, CitedVersionParseResidual)
    assert clause.residual.rule_id == CITED_VERSION_PARSE_RESIDUAL_RULE_ID
    assert clause.residual.target_label == "5"
    assert "sellaisena kuin" in clause.residual.clause_text
