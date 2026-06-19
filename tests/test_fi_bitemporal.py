"""Unit tests for the corpus bitemporal broken-reference scan.

These tests INJECT synthetic ``tree_as_of`` / ``provision_present`` adapters —
NO real ``legal_pit`` replay, no corpus. They pin the three outcomes the scan
must distinguish:

  * a ref to a present provision           -> no finding
  * a ref to a repealed provision          -> repealed_since finding
  * a target whose tree won't materialize  -> BrokenCheckUnavailable (NOT broken)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from lawvm.core.ir import IRNode
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.legal_surface.bitemporal import (
    BrokenRefReport,
    CurrentStateFinding,
    CurrentStateReport,
    CurrentStateUnavailable,
    PitCache,
    cached_tree_as_of,
    citation_anchor_for_statute,
    scan_current_state,
    scan_one_statute,
    scan_one_statute_current_state,
    scan_broken_references,
)
from lawvm.finland.references.broken_detection import (
    BrokenCheckUnavailable,
    BrokenReason,
    BrokenReferenceFinding,
    detect_broken,
)


# ---------------------------------------------------------------------------
# Synthetic tree builders + injected adapters
# ---------------------------------------------------------------------------


def _statute_tree(*section_labels: str) -> IRNode:
    """A minimal statute tree carrying the named SECTION children."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=tuple(
            IRNode(kind=IRNodeKind.SECTION, label=lbl) for lbl in section_labels
        ),
    )


def _present(tree: IRNode, ref: ProvisionRef) -> bool:
    """Injected presence test: section_label resolves to a SECTION child."""
    if not ref.section_label:
        return True
    return any(
        c.kind is IRNodeKind.SECTION and c.label == ref.section_label
        for c in tree.children
    )


def _cross_statute_mention(
    *,
    source_statute: str,
    target_statute: str,
    target_section: str,
    cited_on: Optional[date],
) -> ReferenceMention:
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id=source_statute, section_label="1"),
        target_provision_ref=ProvisionRef(
            statute_id=target_statute, section_label=target_section
        ),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="plain_text",
        source_span=None,
        valid_at_interval=(cited_on, None),
        edge_subtype="CITES",
    )


# ---------------------------------------------------------------------------
# detect_broken outcomes (the three the scan aggregates)
# ---------------------------------------------------------------------------


def test_present_provision_yields_no_finding() -> None:
    mention = _cross_statute_mention(
        source_statute="711/2022",
        target_statute="500/2010",
        target_section="5",
        cited_on=date(2022, 1, 1),
    )

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        # Target still carries § 5 at every date.
        return _statute_tree("1", "5", "9")

    results = detect_broken(
        [mention],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert results == []


def test_repealed_provision_yields_repealed_since() -> None:
    mention = _cross_statute_mention(
        source_statute="711/2022",
        target_statute="500/2010",
        target_section="5",
        cited_on=date(2012, 1, 1),
    )

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        # § 5 existed when cited (2012) but is gone in the current tree, and the
        # statute is now effectively empty -> repealed, not renumbered.
        if on <= date(2015, 1, 1):
            return _statute_tree("1", "5", "9")
        return _statute_tree()  # repeal placeholder (no provision content)

    results = detect_broken(
        [mention],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert len(results) == 1
    finding = results[0]
    assert isinstance(finding, BrokenReferenceFinding)
    assert finding.reason is BrokenReason.REPEALED_SINCE
    assert finding.target.statute_id == "500/2010"
    assert finding.detected_interval == (date(2012, 1, 1), date(2026, 1, 1))


def test_moved_provision_yields_renumbered_since() -> None:
    mention = _cross_statute_mention(
        source_statute="711/2022",
        target_statute="500/2010",
        target_section="5",
        cited_on=date(2012, 1, 1),
    )

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        # § 5 existed when cited; gone now, but the statute still carries other
        # sections -> renumbered/moved, not a whole repeal.
        if on <= date(2015, 1, 1):
            return _statute_tree("1", "5", "9")
        return _statute_tree("1", "9", "12")

    results = detect_broken(
        [mention],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert len(results) == 1
    finding = results[0]
    assert isinstance(finding, BrokenReferenceFinding)
    assert finding.reason is BrokenReason.RENUMBERED_SINCE


def test_unmaterializable_tree_yields_unavailable_not_broken() -> None:
    mention = _cross_statute_mention(
        source_statute="711/2022",
        target_statute="999/9999",
        target_section="5",
        cited_on=date(2022, 1, 1),
    )

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return None  # cannot materialize — fail-loud

    results = detect_broken(
        [mention],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert len(results) == 1
    unavail = results[0]
    assert isinstance(unavail, BrokenCheckUnavailable)
    assert not isinstance(unavail, BrokenReferenceFinding)
    assert unavail.unavailable_for == "current"
    assert unavail.target.statute_id == "999/9999"


# ---------------------------------------------------------------------------
# citation anchor
# ---------------------------------------------------------------------------


def test_citation_anchor_from_statute_year() -> None:
    assert citation_anchor_for_statute("711/2022") == date(2022, 1, 1)
    # tail after the last "/" is the number, not the year -> implausible -> None.
    assert citation_anchor_for_statute("eu/dir/2019/790") is None
    assert citation_anchor_for_statute("garbage") is None


# ---------------------------------------------------------------------------
# scan_one_statute via injected store + extractor-free path
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal store exposing only what scan_one_statute reads."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies

    def read_oracle(self, sid: str) -> Optional[bytes]:
        return self._bodies.get(sid)

    def read_source(self, sid: str) -> Optional[bytes]:
        return None

    def read_amendment(self, sid: str) -> Optional[bytes]:
        return None


# An AKN body with one <ref> to another statute's § 5.
_BODY_WITH_REF = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<body><section eId=\"sec_1\"><num>1 \xc2\xa7</num>"
    b'<p>Viitataan <ref href="/akn/fi/act/statute/2010/500/!main#sec_5">'
    b"toiseen lakiin</ref>.</p>"
    b"</section></body></akomaNtoso>"
)


def test_scan_one_statute_reports_unavailable_when_target_tree_missing() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return None  # target 500/2010 cannot be materialized

    result = scan_one_statute(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert result.error is None
    # The ref to 500/2010 §5 is a resolved cross-statute target -> checked.
    assert result.mentions_checked >= 1
    assert result.findings == ()
    assert len(result.unavailable) >= 1
    assert all(isinstance(u, BrokenCheckUnavailable) for u in result.unavailable)


def test_scan_one_statute_no_body_is_not_an_error() -> None:
    store = _FakeStore({})  # 711/2022 has no body

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return _statute_tree("5")

    result = scan_one_statute(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=tree_as_of,
        provision_present=_present,
    )
    assert result.error is None
    assert result.mentions_checked == 0
    assert result.findings == ()
    assert result.unavailable == ()


def test_scan_one_statute_repealed_target_is_a_finding() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        # The citing statute is 711/2022 -> anchor 2022-01-01. § 5 of 500/2010
        # was present at the anchor but is gone (and statute empty) now.
        if on <= date(2022, 6, 1):
            return _statute_tree("1", "5")
        return _statute_tree()

    result = scan_one_statute(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0].reason is BrokenReason.REPEALED_SINCE
    assert result.unavailable == ()


# ---------------------------------------------------------------------------
# scan_broken_references aggregation
# ---------------------------------------------------------------------------


def test_scan_broken_references_aggregates_by_reason() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        if on <= date(2022, 6, 1):
            return _statute_tree("1", "5")
        return _statute_tree()

    report: BrokenRefReport = scan_broken_references(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert report.statutes_scanned == 1
    assert report.statutes_with_findings == 1
    assert report.statutes_errored == []
    assert report.total_findings == 1
    assert report.reason_counts == {"repealed_since": 1}
    assert report.unavailable_count == 0
    assert report.top_statutes(5)[0].sid == "711/2022"


# ---------------------------------------------------------------------------
# PIT materialization cache — repeat targets materialize once, results unchanged
# ---------------------------------------------------------------------------


class _CountingTreeAsOf:
    """A fake ``TreeAsOf`` that records every (statute_id, as_of) it computes.

    Lets a test assert that a repeated (statute, as_of) materialization is
    served from cache (the underlying compute runs once) WITHOUT touching real
    legal_pit replay.
    """

    def __init__(self, trees: dict[str, IRNode]) -> None:
        self._trees = trees
        self.calls: list[tuple[str, date]] = []

    def __call__(self, statute_id: str, on: date) -> Optional[IRNode]:
        self.calls.append((statute_id, on))
        return self._trees.get(statute_id)  # None when "cannot materialize"


def test_cache_materializes_each_target_as_of_once() -> None:
    inner = _CountingTreeAsOf({"500/2010": _statute_tree("1", "5")})
    cache = PitCache()
    cached = cached_tree_as_of(inner, cache=cache)

    d = date(2022, 1, 1)
    # Three lookups of the SAME (statute, as_of): only the first computes.
    assert cached("500/2010", d) is not None
    assert cached("500/2010", d) is not None
    assert cached("500/2010", d) is not None
    assert inner.calls == [("500/2010", d)]
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hits"] == 2

    # A different as_of for the same statute is a distinct key -> one more compute.
    cached("500/2010", date(2026, 1, 1))
    assert len(inner.calls) == 2


def test_cache_remembers_misses_no_re_replay() -> None:
    inner = _CountingTreeAsOf({})  # every target fails to materialize -> None
    cache = PitCache()
    cached = cached_tree_as_of(inner, cache=cache)

    d = date(2022, 1, 1)
    assert cached("999/9999", d) is None
    assert cached("999/9999", d) is None  # served from the cached MISS
    assert inner.calls == [("999/9999", d)]  # underlying compute ran only once
    assert cache.stats()["hits"] == 1


def test_cache_does_not_change_findings() -> None:
    """Same scan, cached vs uncached adapter -> byte-identical findings."""
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def base_tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        if on <= date(2022, 6, 1):
            return _statute_tree("1", "5")
        return _statute_tree()

    uncached = scan_broken_references(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=base_tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    cached = scan_broken_references(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=cached_tree_as_of(base_tree_as_of, cache=PitCache()),
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert cached.reason_counts == uncached.reason_counts
    assert cached.total_findings == uncached.total_findings
    assert cached.unavailable_count == uncached.unavailable_count


def test_cache_eviction_bounds_size() -> None:
    inner = _CountingTreeAsOf({})
    cache = PitCache(cap=2)
    cached = cached_tree_as_of(inner, cache=cache)

    cached("a/1", date(2020, 1, 1))
    cached("b/2", date(2020, 1, 1))
    cached("c/3", date(2020, 1, 1))  # evicts LRU ("a/1")
    assert cache.stats()["size"] == 2
    assert cache.stats()["evictions"] == 1
    # "a/1" was evicted -> re-lookup recomputes (a second underlying call).
    cached("a/1", date(2020, 1, 1))
    assert inner.calls.count(("a/1", date(2020, 1, 1))) == 2


def test_scan_broken_references_unavailable_counted_separately() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return None

    report = scan_broken_references(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=date(2026, 1, 1),
    )
    assert report.total_findings == 0
    assert report.reason_counts == {}
    assert report.unavailable_count >= 1
    assert report.unavailable_by_kind.get("current", 0) >= 1
    assert report.statutes_with_findings == 0


# ---------------------------------------------------------------------------
# DEFAULT MODE: current-state scan (no replay) via injected body_for
# ---------------------------------------------------------------------------
#
# These inject a fake current-body accessor (``body_for``) — NO real
# legal_pit replay, no corpus. They pin the three default-mode outcomes:
#   * target whose current body CONTAINS the cited section -> no finding
#   * target whose current body LACKS the cited section    -> absent finding
#   * target whose current body is unavailable             -> Unavailable

# A target body consolidated to CURRENTLY carry §5 (so the cite resolves).
_TARGET_HAS_SEC5 = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b'<body><section eId="sec_5"><num>5 \xc2\xa7</num>'
    b"<p>Sis\xc3\xa4lt\xc3\xb6.</p></section></body></akomaNtoso>"
)

# A target body whose CURRENT consolidated text-state no longer carries §5
# (it now carries only §7) -> the cited §5 is absent NOW.
_TARGET_NO_SEC5 = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b'<body><section eId="sec_7"><num>7 \xc2\xa7</num>'
    b"<p>Sis\xc3\xa4lt\xc3\xb6.</p></section></body></akomaNtoso>"
)


def test_current_state_present_section_yields_no_finding() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_HAS_SEC5 if statute_id == "2010/500" else None

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
    )
    assert result.error is None
    assert result.mentions_checked >= 1
    assert result.findings == ()
    assert result.unavailable == ()


def test_current_state_absent_section_yields_finding() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5 if statute_id == "2010/500" else None

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
    )
    assert result.error is None
    assert result.mentions_checked >= 1
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert isinstance(finding, CurrentStateFinding)
    assert finding.kind == "reference.target_provision_absent"
    assert finding.target.statute_id == "2010/500"
    assert finding.target.section_label == "5"
    # Surface fact, not a legal conclusion.
    assert "absent" in finding.message
    assert result.unavailable == ()


def test_current_state_unavailable_target_is_not_called_absent() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return None  # target current body unavailable -> fail-loud

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
    )
    assert result.error is None
    assert result.mentions_checked >= 1
    assert result.findings == ()  # never called absent
    assert len(result.unavailable) == 1
    assert isinstance(result.unavailable[0], CurrentStateUnavailable)
    assert result.unavailable[0].target.statute_id == "2010/500"


def test_current_state_unparseable_target_is_unavailable_not_absent() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return b"<<<not xml"  # parse fails -> presence undetermined

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
    )
    assert result.error is None
    assert result.findings == ()
    assert len(result.unavailable) == 1


def test_scan_current_state_aggregates_by_kind() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5 if statute_id == "2010/500" else None

    report: CurrentStateReport = scan_current_state(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
    )
    assert report.statutes_scanned == 1
    assert report.statutes_with_findings == 1
    assert report.statutes_errored == []
    assert report.total_findings == 1
    assert report.kind_counts == {"reference.target_provision_absent": 1}
    assert report.unavailable_count == 0
    assert report.top_statutes(5)[0].sid == "711/2022"


def test_scan_current_state_no_body_is_not_an_error() -> None:
    store = _FakeStore({})  # citing statute has no body

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_HAS_SEC5

    # A citer with no oracle has no consolidated text-state -> out of scope, so
    # it is skipped (not checked), but never an error.
    report = scan_current_state(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
        in_scope=lambda sid: True,  # force in-scope so "no body" path is exercised
    )
    assert report.statutes_scanned == 1
    assert report.statutes_errored == []
    assert report.mentions_checked == 0
    assert report.total_findings == 0


# ---------------------------------------------------------------------------
# CITER SCOPE GATE: amendment-act / source-only citers are skipped, not checked
# ---------------------------------------------------------------------------
#
# These pin the characterized false-positive guard: a citer with no consolidated
# text-state (an amendment act whose body is amended-law-relative payload) is
# SKIPPED as out of scope — surfaced as a count, never silently dropped, never
# checked (so its amended-law-relative internal refs never produce findings).


def test_out_of_scope_citer_is_skipped_not_checked() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5  # would yield a finding IF the citer were checked

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
        in_scope=lambda sid: False,  # no consolidated text-state -> out of scope
    )
    assert result.error is None
    assert result.skipped is not None
    assert result.skipped.sid == "711/2022"
    assert "consolidated text-state" in result.skipped.reason
    # Skipped citers are NOT checked: no findings manufactured.
    assert result.findings == ()
    assert result.unavailable == ()
    assert result.mentions_checked == 0


def test_scan_current_state_surfaces_skipped_count() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF, "712/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5 if statute_id == "2010/500" else None

    # 711/2022 in scope (checked -> a finding); 712/2022 out of scope (skipped).
    report = scan_current_state(
        ["711/2022", "712/2022"],
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
        in_scope=lambda sid: sid == "711/2022",
    )
    assert report.statutes_scanned == 2
    assert report.skipped_count == 1
    assert report.statutes_with_findings == 1
    assert report.total_findings == 1
    assert report.statutes_errored == []


def test_in_scope_citer_is_still_checked() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_REF})

    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5 if statute_id == "2010/500" else None

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
        in_scope=lambda sid: True,
    )
    assert result.skipped is None
    assert len(result.findings) == 1
    assert result.findings[0].target.statute_id == "2010/500"


# A citer body whose ref is an INTERNAL self-reference: a bare "9 §:ssä" that the
# extractor resolves to SELF (target statute == the citing statute 711/2022). The
# product scopes to CROSS-statute citations, so this must be excluded (surfaced),
# never checked against the citer's own parsed body and never made a finding.
_BODY_WITH_SELF_REF = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    '<body><section eId="sec_1"><num>1 §</num>'
    "<p>Mitä 9 §:ssä säädetään, sovelletaan tässä.</p>"
    "</section></body></akomaNtoso>"
).encode("utf-8")


def test_self_reference_is_excluded_not_checked() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_SELF_REF})

    # body_for must never be consulted for an excluded self-ref; if it were and
    # returned a body lacking §5, that would (wrongly) become a finding.
    def body_for(statute_id: str) -> Optional[bytes]:
        return _TARGET_NO_SEC5

    result = scan_one_statute_current_state(
        "711/2022",
        store,  # ty: ignore[invalid-argument-type]
        body_for=body_for,
        in_scope=lambda sid: True,
    )
    assert result.error is None
    assert result.skipped is None
    # The self-ref is excluded (surfaced), NOT checked -> no finding manufactured.
    assert result.self_refs_excluded >= 1
    assert result.findings == ()
    assert result.mentions_checked == 0


def test_scan_current_state_surfaces_self_refs_excluded() -> None:
    store = _FakeStore({"711/2022": _BODY_WITH_SELF_REF})

    report = scan_current_state(
        ["711/2022"],
        store,  # ty: ignore[invalid-argument-type]
        body_for=lambda sid: _TARGET_NO_SEC5,
        in_scope=lambda sid: True,
    )
    assert report.self_refs_excluded >= 1
    assert report.total_findings == 0
