"""Offline tests for the dated directed EU amendment DAG (eu_amendment_graph).

No network: a SPARQL JSON fixture mirroring the SHAPE of the live Cellar response
for the high-degree base 32016R0044 (resolved empirically — see the module
docstring). Verifies: the resolved CDM predicates produce typed dated edges, the
same-moment pair (two amenders sharing a date) survives, the closure is sorted
and de-duplicated, and a CELLAR-500/HTML body fails LOUD (never a silent empty
DAG).
"""

from __future__ import annotations

import json

import pytest

from lawvm.eu.eu_amendment_graph import (
    AmendmentEdge,
    AmendmentGraphError,
    amendment_graph_query,
    build_amendment_graph,
    parse_amendment_edges,
)

BASE = "32016R0044"


def _row(relkind: str, celex: str, eif_min: str = "", eif_max: str = "") -> dict:
    row = {
        "relkind": {"type": "literal", "value": relkind},
        "celex": {"type": "literal", "value": celex},
    }
    if eif_min:
        row["eif_min"] = {"type": "literal", "value": eif_min}
    if eif_max:
        row["eif_max"] = {"type": "literal", "value": eif_max}
    return row


# Mirrors the real 32016R0044 amenders sample (dates from the live endpoint),
# INCLUDING the same-moment pair 32017R0488 / 32017R0489 (both eif 2017-03-23)
# and a single-date act (eif_min == eif_max) and a two-date act (eif != dap).
SPARQL_RESULTS = json.dumps(
    {
        "head": {"vars": ["relkind", "celex", "eif_min", "eif_max"]},
        "results": {
            "bindings": [
                _row("amends", "32016R0466", "2016-04-01", "2016-04-01"),
                _row("amends", "32016R0819", "2016-05-25", "2016-05-25"),
                # two-date act: entry-into-force earlier, date-of-application later
                _row("amends", "32017R1325", "2017-07-19", "2018-01-01"),
                # same-moment pair (both apply 2017-03-23)
                _row("amends", "32017R0488", "2017-03-23", "2017-03-23"),
                _row("amends", "32017R0489", "2017-03-23", "2017-03-23"),
                # a corrigendum (correcting relation, no machine-readable date)
                _row("corrects", "32016R0044R(01)"),
                # self-loop must be dropped
                _row("amends", BASE, "2016-01-29", "2016-01-29"),
            ]
        },
    }
).encode("utf-8")

CELLAR_500 = (
    b"JDBC exception on Hibernate data access: SQLException for SQL [n/a]; "
    b"Unable to acquire JDBC Connection"
)
HTML_PAGE = b"<!DOCTYPE html><html><body>error</body></html>"


def _fetch_ok(_q: str, _e: str, _t: int) -> bytes:
    return SPARQL_RESULTS


def test_query_binds_base_as_object_and_uses_resolved_predicates() -> None:
    q = amendment_graph_query(BASE)
    assert "resource_legal_amends_resource_legal" in q
    assert "resource_legal_corrects_resource_legal" in q
    assert BASE in q
    # The base is the OBJECT of both relations (direction = amending -> base).
    assert "?act cdm:resource_legal_amends_resource_legal ?base" in q


def test_dated_edges_typed_and_directed() -> None:
    edges = parse_amendment_edges(SPARQL_RESULTS, BASE)
    by_celex = {e.amending_celex: e for e in edges}
    assert "32016R0466" in by_celex
    amend = by_celex["32016R0466"]
    assert amend.relation_kind == "amends"
    assert amend.base_celex == BASE
    assert amend.entry_into_force == "2016-04-01"
    assert amend.date_of_application == "2016-04-01"
    # corrigendum survives even though its CELEX is not a well-formed act id
    assert "32016R0044R(01)" in by_celex
    assert by_celex["32016R0044R(01)"].relation_kind == "corrects"


def test_self_loop_dropped() -> None:
    edges = parse_amendment_edges(SPARQL_RESULTS, BASE)
    assert all(e.amending_celex != BASE for e in edges)


def test_two_date_act_orders_by_date_of_application() -> None:
    edges = parse_amendment_edges(SPARQL_RESULTS, BASE)
    e = next(e for e in edges if e.amending_celex == "32017R1325")
    assert e.entry_into_force == "2017-07-19"
    assert e.date_of_application == "2018-01-01"
    # ordering_date is the date-of-application (the later one)
    assert e.ordering_date == "2018-01-01"


def test_same_moment_pair_shares_ordering_date() -> None:
    g = build_amendment_graph(BASE, _fetch=_fetch_ok)
    pair = [e for e in g.edges if e.amending_celex in ("32017R0488", "32017R0489")]
    assert len(pair) == 2
    assert pair[0].ordering_date == pair[1].ordering_date == "2017-03-23"


def test_ordered_edges_are_chronological() -> None:
    g = build_amendment_graph(BASE, _fetch=_fetch_ok)
    dates = [e.ordering_date for e in g.ordered_edges()]
    # Corrigendum has no date (sorts first under ""), then chronological.
    assert dates == sorted(dates)
    amenders = g.amenders()
    assert amenders == tuple(sorted(set(amenders)))  # sorted, de-duplicated


def test_cellar_500_fails_loud_not_empty_graph() -> None:
    with pytest.raises(AmendmentGraphError, match="JDBC|Hibernate|backend"):
        parse_amendment_edges(CELLAR_500, BASE)


def test_html_error_page_fails_loud() -> None:
    with pytest.raises(AmendmentGraphError, match="not JSON"):
        parse_amendment_edges(HTML_PAGE, BASE)


def test_empty_payload_fails_loud() -> None:
    with pytest.raises(AmendmentGraphError, match="empty"):
        parse_amendment_edges(b"", BASE)


def test_malformed_base_celex_refused() -> None:
    with pytest.raises(ValueError, match="well-formed base CELEX"):
        build_amendment_graph("not-a-celex", _fetch=_fetch_ok)


def test_edge_relation_kind_validated() -> None:
    with pytest.raises(ValueError, match="relation_kind"):
        AmendmentEdge(amending_celex="x", base_celex="y", relation_kind="bogus")
