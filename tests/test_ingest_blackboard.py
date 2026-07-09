"""Hermetic M3 tests: the stigmergic (blackboard) de-facsimile composer.

No network / no model / no real ``visual.py`` — synthetic ``PageSimulacrum``
stacks built by hand + a FAKE knowledge source / adjudicator (canned per-region
replies) + an injected FAKE crop function. Covers the §7 spec's acceptance list:
  * deterministic mark pre-seeding from the §3 metadata (FURNITURE? / GARBLE / OPEN)
  * affordance dispatch (VIEW / EXPAND / PAGE / NOTES / NOTE / PREFIX / DEFER) with
    fakes + budget bounding
  * controller scheduling on the highest-value live region
  * stigmergic fixpoint termination + budget-exhaustion fallback → context_exhausted
  * journal round-trip (byte-identical) + determinism (twice ⇒ identical journal+ledger)
  * verify_ledger still gates the emitted ledger; the M1 single-pass path is unchanged
"""
from __future__ import annotations

import pytest

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.blackboard import (
    AFFORDANCE_DISPATCH,
    AffordanceKind,
    AffordanceRequest,
    BlackboardBudget,
    BlackboardController,
    BudgetLedger,
    DispatchContext,
    KnowledgeSource,
    Mark,
    MarkKind,
    SeamAdjudicatorSource,
    SourceOutput,
    Workspace,
    defacsimile_blackboard,
    deserialize_workspace,
    dispatch_affordance,
    parse_affordance_line,
    preseed_workspace,
    serialize_workspace,
)
from lawvm.ingest.defacsimile import (
    DeFacsimileClaim,
    DeFacsimileOp,
    apply_ledger,
    defacsimile,
    verify_ledger,
)
from lawvm.ingest.metadata import NodeMetadata, encode_metadata
from lawvm.ingest.simulacrum import ConvergenceInfo, PageSimulacrum, SpanRef

_DIGEST = "b" * 64
_ROOT_ANCHOR = SourceAnchor(artifact_digest=_DIGEST, locator="manifestation")


def _anchor(page: int) -> SourceAnchor:
    return SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page)


def _node(
    kind: SourceDocumentNodeKind,
    text: str,
    page: int,
    *,
    meta: NodeMetadata | None = None,
) -> SourceDocumentNode:
    attrs = encode_metadata(meta) if meta is not None else {}
    return SourceDocumentNode(
        kind=kind,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        text=text,
        attrs=attrs,
    )


def _page(page_num: int, nodes: tuple[SourceDocumentNode, ...]) -> PageSimulacrum:
    return PageSimulacrum(
        page_num=page_num,
        nodes=nodes,
        freeform=(),
        convergence=ConvergenceInfo(
            rounds=1, round_hashes=("h",), termination="empty_patch", gate_reasons=(), patches_total=0
        ),
        assurance=AssuranceTier.SINGLE_WITNESS,
        raw_wire_digests=("d",),
    )


def _seam_pages() -> list[PageSimulacrum]:
    """A 2-page stack: a recurring furniture header + a mid-sentence seam split."""
    p1 = _page(
        1,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "HE 1/2015 vp", 1, meta=NodeMetadata(band="top", furniture=True, band_count=2, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "The provision states that", 1, meta=NodeMetadata(band="body", y_order=1, ends_terminal=False)),
        ),
    )
    p2 = _page(
        2,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "HE 1/2015 vp", 2, meta=NodeMetadata(band="top", furniture=True, band_count=2, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "the deadline is extended.", 2, meta=NodeMetadata(band="body", y_order=1, starts_lower=True)),
        ),
    )
    return [p1, p2]


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class _RegionResult:
    def __init__(self, claims, affordances=(), truncated=False):
        self.claims = tuple(claims)
        self.affordances = tuple(affordances)
        self.truncated = truncated


class _FakeAdjudicator:
    """A blackboard-aware adjudicator: canned claims/affordances per region-id set."""

    def __init__(self, plan):
        # plan: dict[frozenset[node-id-str]] -> (claims, affordances)
        self._plan = plan

    def is_available(self) -> bool:
        return True

    def adjudicate_region(self, window, region, region_marks, region_notes):
        key = frozenset(f"p{r.page_num}n{r.node_path[0]}" for r in region if r.node_path)
        claims, affordances = self._plan.get(key, ((), ()))
        return _RegionResult(claims=claims, affordances=affordances)


def _drop_claim(page: int, idx: int) -> DeFacsimileClaim:
    return DeFacsimileClaim(
        op=DeFacsimileOp.DROP_FURNITURE,
        targets=(SpanRef(page, (idx,)),),
        tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
        corroborating_producers=("defacsimile_adjudicator", "affordance:recurrence"),
        rationale="fake drop",
    )


def _rejoin_claim(a: SpanRef, b: SpanRef) -> DeFacsimileClaim:
    return DeFacsimileClaim(
        op=DeFacsimileOp.REJOIN,
        targets=(a, b),
        tier=AssuranceTier.SINGLE_WITNESS,
        corroborating_producers=("defacsimile_adjudicator",),
        rationale="fake rejoin",
    )


# --------------------------------------------------------------------------- #
# 1. Pre-seeding                                                             #
# --------------------------------------------------------------------------- #


def test_preseed_furniture_from_band_count() -> None:
    ws = preseed_workspace(_seam_pages())
    furn = [m for m in ws.marks if m.kind is MarkKind.FURNITURE_Q]
    # Two recurring headers (band_count=2) → two FURNITURE? marks.
    assert {m.region[0] for m in furn} == {SpanRef(1, (0,)), SpanRef(2, (0,))}
    assert all(m.producer_id == "preseed:metadata" for m in furn)


def test_preseed_garble_from_freeform_reason() -> None:
    p = _page(
        1,
        (_node(SourceDocumentNodeKind.MATH_REGION, "sum", 1, meta=NodeMetadata(freeform_reason="image_baked")),),
    )
    ws = preseed_workspace([p])
    garble = [m for m in ws.marks if m.kind is MarkKind.GARBLE]
    assert len(garble) == 1
    assert garble[0].region == (SpanRef(1, (0,)),)


def test_preseed_open_continuation_crosses_edge() -> None:
    ws = preseed_workspace(_seam_pages())
    opens = [m for m in ws.marks if m.kind is MarkKind.OPEN]
    assert len(opens) == 1
    # tail = p1 body, head = p2 body
    assert opens[0].region == (SpanRef(1, (1,)), SpanRef(2, (1,)))


def test_preseed_no_open_when_terminal_punctuation() -> None:
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "A complete sentence.", 1, meta=NodeMetadata(y_order=0, ends_terminal=True)),))
    p2 = _page(2, (_node(SourceDocumentNodeKind.PARAGRAPH, "Another one.", 2, meta=NodeMetadata(y_order=0, starts_lower=False)),))
    ws = preseed_workspace([p1, p2])
    assert not [m for m in ws.marks if m.kind is MarkKind.OPEN]


# --------------------------------------------------------------------------- #
# 2. Affordance dispatch                                                     #
# --------------------------------------------------------------------------- #


def _ctx(**kw) -> DispatchContext:
    return DispatchContext(
        workspace=kw.get("workspace", Workspace()),
        region=kw.get("region", (SpanRef(1, (0,)),)),
        simulacra=kw.get("simulacra", _seam_pages()),
        budget=kw.get("budget", BlackboardBudget()),
        used=kw.get("used", BudgetLedger()),
        crop_fn=kw.get("crop_fn"),
    )


def test_parse_affordance_lines() -> None:
    assert parse_affordance_line("PAGE 3").kind is AffordanceKind.PAGE
    assert parse_affordance_line("PAGE 3").page_num == 3
    ex = parse_affordance_line("EXPAND 2 5")
    assert ex.kind is AffordanceKind.EXPAND and ex.args == ("2", "5")
    v = parse_affordance_line("VIEW 4 0.1 0.2 0.3 0.4")
    assert v.kind is AffordanceKind.VIEW and v.page_num == 4 and v.bbox == (0.1, 0.2, 0.3, 0.4)
    note = parse_affordance_line("NOTE this is furniture")
    assert note.kind is AffordanceKind.NOTE and note.note_text == "this is furniture"
    assert parse_affordance_line("DROP p1n0") is None  # an op line, not an affordance


def test_dispatch_view_uses_injected_fake_crop() -> None:
    calls = []

    def fake_crop(manifestation, page_num, bbox, dpi):
        calls.append((page_num, bbox, dpi))
        return b"PNGBYTES"

    ctx = _ctx(crop_fn=fake_crop)
    dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.VIEW, page_num=2, bbox=(0.0, 0.0, 1.0, 1.0)))
    assert calls == [(2, (0.0, 0.0, 1.0, 1.0), 200)]
    assert ctx.view_bytes == [b"PNGBYTES"]
    assert ctx.used.views_used == 1


def test_dispatch_view_bounded_by_max_views() -> None:
    def fake_crop(manifestation, page_num, bbox, dpi):
        return b"X"

    ctx = _ctx(crop_fn=fake_crop, budget=BlackboardBudget(max_views=1))
    for _ in range(3):
        dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.VIEW, page_num=1))
    assert ctx.used.views_used == 1  # budget capped at 1


def test_dispatch_view_no_crop_module_is_noop() -> None:
    # crop_fn None and the real lawvm.ingest.visual absent → a guarded no-op.
    ctx = _ctx(crop_fn=None)
    dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.VIEW, page_num=1))
    assert ctx.view_bytes == []


def test_dispatch_expand_and_note_and_defer() -> None:
    ws = Workspace()
    region = (SpanRef(1, (1,)),)
    ctx = _ctx(workspace=ws, region=region)
    dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.EXPAND, args=("1", "3")))
    assert ctx.expanded_pages == [1, 2, 3]
    dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.NOTE, note_text="a note"))
    assert ws.notes_for(region) == ("a note",)
    dispatch_affordance(ctx, AffordanceRequest(kind=AffordanceKind.DEFER, note_text="need more"))
    assert any(m.kind is MarkKind.DEFER for m in ws.marks)


def test_dispatch_table_is_total() -> None:
    # Every affordance kind has a handler (the table is total / extensible).
    assert set(AFFORDANCE_DISPATCH) == set(AffordanceKind)


# --------------------------------------------------------------------------- #
# 3. Controller scheduling + fixpoint                                        #
# --------------------------------------------------------------------------- #


def test_controller_schedules_and_reaches_fixpoint() -> None:
    pages = _seam_pages()
    ws = preseed_workspace(pages)
    plan = {
        frozenset({"p1n0"}): ((_drop_claim(1, 0),), ()),
        frozenset({"p2n0"}): ((_drop_claim(2, 0),), ()),
        frozenset({"p1n1", "p2n1"}): ((_rejoin_claim(SpanRef(1, (1,)), SpanRef(2, (1,))),), ()),
    }
    controller = BlackboardController([SeamAdjudicatorSource(_FakeAdjudicator(plan))])
    result = controller.run(pages, ws)
    assert result.termination == "fixpoint"
    reduced = apply_ledger(pages, result.ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, result.ledger, reduced) == []
    # furniture dropped, body rejoined
    assert [c.text for c in reduced.children] == [
        "The provision states that the deadline is extended."
    ]


def test_controller_contest_detected_and_scheduled_first() -> None:
    pages = _seam_pages()
    ws = Workspace()
    region = (SpanRef(1, (1,)),)
    # Two producers post conflicting decisions on the same region.
    ws.post(Mark(kind=MarkKind.DECIDE_DROP, region=region, producer_id="src_a", round=1,
                 claim=_drop_claim(1, 1)))
    ws.post(Mark(kind=MarkKind.DECIDE_KEEP, region=region, producer_id="src_b", round=1,
                 claim=DeFacsimileClaim(op=DeFacsimileOp.KEEP, targets=region,
                                        tier=AssuranceTier.SINGLE_WITNESS,
                                        corroborating_producers=("src_b",))))

    class _Resolver(KnowledgeSource):
        source_id = "resolver"

        def run(self, workspace, region, simulacra):
            # Resolve the contest by KEEPing (deterministic bias to retention).
            return SourceOutput(marks=(Mark(
                kind=MarkKind.DECIDE_KEEP, region=region, producer_id="resolver", round=workspace.round,
                claim=DeFacsimileClaim(op=DeFacsimileOp.KEEP, targets=tuple(region),
                                       tier=AssuranceTier.SINGLE_WITNESS,
                                       corroborating_producers=("resolver",))),))

    controller = BlackboardController([_Resolver()])
    result = controller.run(pages, ws)
    # The CONTESTED region was scheduled and the resolver's KEEP was recorded.
    assert any(m.kind is MarkKind.CONTESTED for m in result.workspace.marks)
    assert any(m.producer_id == "resolver" for m in result.workspace.marks)
    # The resolver's KEEP (posted LAST) supersedes the earlier DROP in the ledger.
    region_claims = [c for c in result.ledger.claims if c.targets == region]
    assert region_claims and all(c.op is DeFacsimileOp.KEEP for c in region_claims)


def test_controller_budget_exhaustion_falls_back_to_context_exhausted() -> None:
    pages = _seam_pages()
    ws = preseed_workspace(pages)

    class _NeverDecides(KnowledgeSource):
        source_id = "never"

        def run(self, workspace, region, simulacra):
            # Posts a NEW candidate every round (band-count regions stay live) but
            # never a decision → the loop must hit the round budget, then residue.
            return SourceOutput(marks=(Mark(
                kind=MarkKind.KEEP_Q, region=region, producer_id=f"never-{workspace.round}",
                round=workspace.round, rationale="stalling"),))

    controller = BlackboardController([_NeverDecides()], budget=BlackboardBudget(max_rounds=3))
    result = controller.run(pages, ws)
    assert result.termination == "budget_exhausted"
    assert result.context_exhausted_regions  # residue typed
    # The residue ledger is verified + the reduced doc keeps all body content.
    reduced = apply_ledger(pages, result.ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, result.ledger, reduced) == []
    # The OPEN body seam was decided by the deterministic fallback (context_exhausted).
    methods = {c.method for c in result.ledger.claims}
    assert "context_exhausted" in methods


# --------------------------------------------------------------------------- #
# 4. Journal round-trip + determinism                                        #
# --------------------------------------------------------------------------- #


def test_workspace_journal_roundtrip_byte_identical() -> None:
    pages = _seam_pages()
    ws = preseed_workspace(pages)
    ws.add_note((SpanRef(1, (1,)),), "carried note")
    ws.post(Mark(kind=MarkKind.DECIDE_REJOIN,
                 region=(SpanRef(1, (1,)), SpanRef(2, (1,))),
                 producer_id="seam_adjudicator", round=2,
                 claim=_rejoin_claim(SpanRef(1, (1,)), SpanRef(2, (1,)))))
    blob = serialize_workspace(ws)
    restored = deserialize_workspace(blob)
    assert serialize_workspace(restored) == blob
    assert restored.notes_for((SpanRef(1, (1,)),)) == ("carried note",)


def test_blackboard_determinism_twice_identical() -> None:
    pages = _seam_pages()
    plan = {
        frozenset({"p1n0"}): ((_drop_claim(1, 0),), ()),
        frozenset({"p2n0"}): ((_drop_claim(2, 0),), ()),
        frozenset({"p1n1", "p2n1"}): ((_rejoin_claim(SpanRef(1, (1,)), SpanRef(2, (1,))),), ()),
    }
    doc1, ws1 = defacsimile_blackboard(pages, _ROOT_ANCHOR, adjudicator=_FakeAdjudicator(plan))
    doc2, ws2 = defacsimile_blackboard(pages, _ROOT_ANCHOR, adjudicator=_FakeAdjudicator(plan))
    assert serialize_workspace(ws1) == serialize_workspace(ws2)
    assert ws1.digest() == ws2.digest()
    assert [c.text for c in doc1.root.children] == [c.text for c in doc2.root.children]


# --------------------------------------------------------------------------- #
# 5. verify_ledger gates + M1 unchanged                                      #
# --------------------------------------------------------------------------- #


def test_blackboard_verify_ledger_gates_bad_model_ledger() -> None:
    pages = _seam_pages()

    # A malicious plan: DROP a real body line (would violate NUMERIC/multiset when
    # combined). Use a phantom target so verify_ledger rejects → fallback path.
    bad = DeFacsimileClaim(
        op=DeFacsimileOp.DROP_FURNITURE,
        targets=(SpanRef(9, (9,)),),  # phantom
        tier=AssuranceTier.SINGLE_WITNESS,
        corroborating_producers=("x",),
    )
    plan = {frozenset({"p1n0"}): ((bad,), ())}
    doc, ws = defacsimile_blackboard(pages, _ROOT_ANCHOR, adjudicator=_FakeAdjudicator(plan))
    # The promoted ledger failed verification → deterministic fallback used; the
    # emitted ledger passes verify_ledger (never emitted unverified) and no phantom.
    reduced = apply_ledger(pages, doc.ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, doc.ledger, reduced) == []
    assert all(t.page_num != 9 for c in doc.ledger.claims for t in c.targets)


def test_blackboard_no_adjudicator_uses_deterministic_fallback() -> None:
    pages = _seam_pages()
    doc, ws = defacsimile_blackboard(pages, _ROOT_ANCHOR, adjudicator=None)
    reduced = apply_ledger(pages, doc.ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, doc.ledger, reduced) == []


def test_m1_single_pass_unchanged_by_mode_default() -> None:
    pages = _seam_pages()
    # The default mode is the M1 single-pass fold; it must not raise and must gate.
    doc = defacsimile(pages, _ROOT_ANCHOR, adjudicator=None)
    reduced = apply_ledger(pages, doc.ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, doc.ledger, reduced) == []


def test_defacsimile_blackboard_mode_matches_direct_entry() -> None:
    pages = _seam_pages()
    plan = {frozenset({"p1n0"}): ((_drop_claim(1, 0),), ())}
    via_mode = defacsimile(pages, _ROOT_ANCHOR, adjudicator=_FakeAdjudicator(plan), mode="blackboard")
    direct, _ = defacsimile_blackboard(pages, _ROOT_ANCHOR, adjudicator=_FakeAdjudicator(plan))
    assert [c.text for c in via_mode.root.children] == [c.text for c in direct.root.children]


def test_defacsimile_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        defacsimile(_seam_pages(), _ROOT_ANCHOR, mode="nonsense")


# --------------------------------------------------------------------------- #
# 6. Store round-trip (ParsedIrStore.put_workspace / get_workspace)          #
# --------------------------------------------------------------------------- #


def test_put_get_workspace_roundtrip_via_store() -> None:
    import tempfile
    from pathlib import Path

    from lawvm.ingest.parsed_store import (
        ParsedIrStore,
        defacsimile_workspace_locator,
    )

    ws = preseed_workspace(_seam_pages())
    ws.post(Mark(kind=MarkKind.DECIDE_REJOIN,
                 region=(SpanRef(1, (1,)), SpanRef(2, (1,))),
                 producer_id="seam_adjudicator", round=1,
                 claim=_rejoin_claim(SpanRef(1, (1,)), SpanRef(2, (1,)))))
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "parsed.farchive")
        store = ParsedIrStore(path)
        try:
            locator = defacsimile_workspace_locator(_DIGEST, "adjudicated_vision", "v1+compose=blackboard.v1")
            store.put_workspace(locator, ws, source_digest=_DIGEST)
            got = store.get_workspace(locator)
        finally:
            store.close()
    assert got is not None
    assert serialize_workspace(got) == serialize_workspace(ws)


def test_get_workspace_absent_is_none() -> None:
    import tempfile
    from pathlib import Path

    from lawvm.ingest.parsed_store import ParsedIrStore

    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "parsed.farchive")
        store = ParsedIrStore(path)
        try:
            assert store.get_workspace("parsed/nope/x@y/defacsimile_workspace.json") is None
        finally:
            store.close()
