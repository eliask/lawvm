"""Hermetic Track-C tests: Level-2 de-facsimile fold + verify_ledger + adjudicator.

No network / no model / no fixtures — synthetic ``PageSimulacrum`` stacks built by
hand (per-page node lists with metadata attrs) and a mocked adjudicator ``_chat``.
Covers (spec §2 / the Track-C gate list):
  * the PURE fold + idempotence (fold twice → byte-identical)
  * verify_ledger rejecting phantom-drop / invented-REJOIN-text / multiset-
    violation / claim-disjointness / NUMERIC-change
  * adjudicator parse of canned line replies → claims; repetition-loop → withheld;
    truncation → per-window deterministic fallback
  * the HE-2015/1 defect fixtures: furniture dropped, seam-header dedup'd, annex
    triplicates → one, paragraph rejoined, legit table header KEPT; the "14 §"
    body vs "14" page-number guardrail.
"""
from __future__ import annotations

import pytest

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.defacsimile import (
    DeFacsimileClaim,
    DeFacsimileLedger,
    DeFacsimileOp,
    DeFacsimiledDocument,
    LedgerVerificationError,
    apply_ledger,
    defacsimile,
    verify_ledger,
)
from lawvm.ingest.llm_backends.defacsimile_adjudicator import (
    DeFacsimileAdjudicator,
    parse_window_reply,
)
from lawvm.ingest.metadata import NodeMetadata, encode_metadata
from lawvm.ingest.simulacrum import (
    ConvergenceInfo,
    PageSimulacrum,
    SpanRef,
)

_DIGEST = "a" * 64
_ROOT_ANCHOR = SourceAnchor(artifact_digest=_DIGEST, locator="manifestation")


def _anchor(page: int) -> SourceAnchor:
    return SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page)


def _node(
    kind: SourceDocumentNodeKind,
    text: str,
    page: int,
    *,
    meta: NodeMetadata | None = None,
    children: tuple[SourceDocumentNode, ...] = (),
    label: str | None = None,
) -> SourceDocumentNode:
    attrs = encode_metadata(meta) if meta is not None else {}
    return SourceDocumentNode(
        kind=kind,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        text=text,
        children=children,
        label=label,
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


def _cell(text: str, page: int, *, header: bool = False) -> SourceDocumentNode:
    attrs = {"is_header": "1"} if header else {}
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_CELL,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        text=text,
        attrs=attrs,
    )


def _row(cells: tuple[SourceDocumentNode, ...], page: int) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_ROW,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        children=cells,
    )


def _reduced_text(root: SourceDocumentNode) -> str:
    parts: list[str] = []

    def _walk(n: SourceDocumentNode) -> None:
        if n.text:
            parts.append(n.text)
        for c in n.children:
            _walk(c)

    _walk(root)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# The PURE fold + idempotence                                                 #
# --------------------------------------------------------------------------- #


def test_apply_ledger_identity_when_empty() -> None:
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "Body one.", 1, meta=NodeMetadata(y_order=0)),))
    p2 = _page(2, (_node(SourceDocumentNodeKind.PARAGRAPH, "Body two.", 2, meta=NodeMetadata(y_order=0)),))
    root = apply_ledger([p1, p2], DeFacsimileLedger(), _ROOT_ANCHOR)
    assert [c.text for c in root.children] == ["Body one.", "Body two."]


def test_fold_is_idempotent_byte_identical() -> None:
    p1 = _page(
        1,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "Running header p1", 1, meta=NodeMetadata(band="top", furniture=True, band_count=3, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "A sentence that is split", 1, meta=NodeMetadata(band="body", y_order=1)),
        ),
    )
    p2 = _page(
        2,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "Running header p2", 2, meta=NodeMetadata(band="top", furniture=True, band_count=3, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "across the page break.", 2, meta=NodeMetadata(band="body", y_order=1)),
        ),
    )
    ledger = DeFacsimileLedger(
        claims=(
            DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (0,)),), AssuranceTier.MULTI_WITNESS_ADJUDICATED, ("defacsimile_adjudicator", "affordance:margin_band")),
            DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(2, (0,)),), AssuranceTier.MULTI_WITNESS_ADJUDICATED, ("defacsimile_adjudicator", "affordance:margin_band")),
            DeFacsimileClaim(DeFacsimileOp.REJOIN, (SpanRef(1, (1,)), SpanRef(2, (1,))), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),
        )
    )
    r1 = apply_ledger([p1, p2], ledger, _ROOT_ANCHOR)
    r2 = apply_ledger([p1, p2], ledger, _ROOT_ANCHOR)
    assert _reduced_text(r1) == _reduced_text(r2)
    # furniture gone, paragraph rejoined into one node.
    assert [c.text for c in r1.children] == ["A sentence that is split across the page break."]


# --------------------------------------------------------------------------- #
# verify_ledger rejections                                                    #
# --------------------------------------------------------------------------- #


def _simple_stack() -> list[PageSimulacrum]:
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "Alpha beta gamma.", 1, meta=NodeMetadata(y_order=0)),))
    p2 = _page(2, (_node(SourceDocumentNodeKind.PARAGRAPH, "Delta epsilon.", 2, meta=NodeMetadata(y_order=0)),))
    return [p1, p2]


def test_verify_rejects_phantom_drop() -> None:
    stack = _simple_stack()
    ledger = DeFacsimileLedger(
        claims=(DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (5,)),), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),)
    )
    reduced = apply_ledger(stack, ledger, _ROOT_ANCHOR)
    v = verify_ledger(stack, ledger, reduced)
    assert any("phantom-drop" in x for x in v)


def test_verify_rejects_claim_disjointness() -> None:
    stack = _simple_stack()
    ledger = DeFacsimileLedger(
        claims=(
            DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (0,)),), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),
            DeFacsimileClaim(DeFacsimileOp.DEDUP_SEAM, (SpanRef(1, (0,)),), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),
        )
    )
    reduced = apply_ledger(stack, ledger, _ROOT_ANCHOR)
    v = verify_ledger(stack, ledger, reduced)
    assert any("claim-disjointness" in x for x in v)


def test_verify_rejects_invented_rejoin_text() -> None:
    stack = _simple_stack()
    ledger = DeFacsimileLedger(
        claims=(DeFacsimileClaim(DeFacsimileOp.REJOIN, (SpanRef(1, (0,)), SpanRef(2, (0,))), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),)
    )
    reduced = apply_ledger(stack, ledger, _ROOT_ANCHOR)
    # honest rejoin passes; now hand a hand-forged reduced tree with invented text.
    forged = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_ROOT_ANCHOR,
        children=(_node(SourceDocumentNodeKind.PARAGRAPH, "Alpha beta gamma. Delta epsilon. INVENTED WORDS", 1),),
    )
    v = verify_ledger(stack, ledger, forged)
    assert any("multiset-violation" in x for x in v)
    # and the honest fold verifies clean.
    assert verify_ledger(stack, ledger, reduced) == []


def test_verify_rejects_multiset_violation_invented_content() -> None:
    stack = _simple_stack()
    ledger = DeFacsimileLedger()
    forged = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_ROOT_ANCHOR,
        children=(_node(SourceDocumentNodeKind.PARAGRAPH, "Alpha beta gamma delta HALLUCINATED.", 1),),
    )
    v = verify_ledger(stack, ledger, forged)
    assert any("multiset-violation" in x for x in v)


def test_verify_rejects_numeric_change() -> None:
    # A body "§ 14" must not silently become "§ 15" in the reduced tree.
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "Under § 14 the amount is 500 €.", 1, meta=NodeMetadata(numeric=True, y_order=0)),))
    stack = [p1]
    ledger = DeFacsimileLedger()
    forged = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_ROOT_ANCHOR,
        children=(_node(SourceDocumentNodeKind.PARAGRAPH, "Under § 15 the amount is 500 €.", 1),),
    )
    v = verify_ledger(stack, ledger, forged)
    assert any("numeric-change" in x for x in v)


def test_verify_page_number_14_droppable_but_body_14_paragraph_protected() -> None:
    # Guardrail: a page-number furniture "14" is droppable; a body "14 §" is not.
    page_number = _node(SourceDocumentNodeKind.PARAGRAPH, "14", 1, meta=NodeMetadata(band="bottom", furniture=True, band_count=5, y_order=9))
    body = _node(SourceDocumentNodeKind.PARAGRAPH, "This 14 § governs the case.", 1, meta=NodeMetadata(numeric=True, y_order=0))
    p1 = _page(1, (body, page_number))
    stack = [p1]
    # Dropping the page-number "14" is fine — the body "14" survives.
    drop_ledger = DeFacsimileLedger(
        claims=(DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (1,)),), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),)
    )
    reduced = apply_ledger(stack, drop_ledger, _ROOT_ANCHOR)
    assert verify_ledger(stack, drop_ledger, reduced) == []
    assert "14 §" in _reduced_text(reduced)
    # But dropping the BODY paragraph loses the "14" the page number can't cover.
    drop_body = DeFacsimileLedger(
        claims=(DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (0,)),), AssuranceTier.SINGLE_WITNESS, ("defacsimile_adjudicator",)),)
    )
    reduced2 = apply_ledger(stack, drop_body, _ROOT_ANCHOR)
    v = verify_ledger(stack, drop_body, reduced2)
    assert any("numeric-change" in x for x in v)


# --------------------------------------------------------------------------- #
# Adjudicator: parse of canned line replies → claims                          #
# --------------------------------------------------------------------------- #


class _MockAdjudicator(DeFacsimileAdjudicator):
    """A DeFacsimileAdjudicator whose ``_chat`` returns canned per-window replies."""

    def __init__(self, replies: dict[str, str] | str) -> None:
        super().__init__()
        self._replies = replies

    def is_available(self) -> bool:
        return True

    def _chat(self, system: str, user: str, *, window: str) -> str:
        if isinstance(self._replies, str):
            return self._replies
        return self._replies[window]


def _window_pages() -> list[PageSimulacrum]:
    p1 = _page(
        1,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "Government Proposal HE 1/2015", 1, meta=NodeMetadata(band="top", furniture=True, band_count=4, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "The provision states that", 1, meta=NodeMetadata(band="body", y_order=1)),
        ),
    )
    p2 = _page(
        2,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "Government Proposal HE 1/2015", 2, meta=NodeMetadata(band="top", furniture=True, band_count=4, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "the deadline is extended.", 2, meta=NodeMetadata(band="body", y_order=1)),
        ),
    )
    return [p1, p2]


def test_adjudicator_parses_canned_lines_into_claims() -> None:
    pages = _window_pages()
    reply = "DROP p1n0\nDROP p2n0\nREJOIN p1n1 p2n1\n"
    claims, pathological = parse_window_reply(reply, pages)
    assert not pathological
    ops = sorted(str(c.op) for c in claims)
    assert ops == ["drop_furniture", "drop_furniture", "rejoin"]
    rejoin = next(c for c in claims if c.op is DeFacsimileOp.REJOIN)
    assert rejoin.targets == (SpanRef(1, (1,)), SpanRef(2, (1,)))


def test_adjudicator_tier_multiwitness_when_affordance_fires() -> None:
    pages = _window_pages()
    claims, _ = parse_window_reply("DROP p1n0\n", pages)
    drop = claims[0]
    # p1n0 sits in the top band with recurrence 4 → both affordances fire.
    assert drop.tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    assert "affordance:margin_band" in drop.corroborating_producers
    assert "affordance:recurrence" in drop.corroborating_producers


def test_adjudicator_tier_single_witness_when_no_affordance() -> None:
    pages = _window_pages()
    # A REJOIN over body nodes with no margin-band / recurrence affordance.
    claims, _ = parse_window_reply("REJOIN p1n1 p2n1\n", pages)
    assert claims[0].tier is AssuranceTier.SINGLE_WITNESS
    assert claims[0].corroborating_producers == ("defacsimile_adjudicator",)


def test_adjudicator_ignores_invented_ids() -> None:
    pages = _window_pages()
    claims, _ = parse_window_reply("DROP p9n9\nDROP p1n0\n", pages)
    # p9n9 does not exist in this window — it cannot conjure a node.
    assert len(claims) == 1
    assert claims[0].targets == (SpanRef(1, (0,)),)


def test_adjudicator_repetition_loop_withholds_claims() -> None:
    pages = _window_pages()
    loop = "\n".join(["DROP p1n0"] * 20)
    claims, pathological = parse_window_reply(loop, pages)
    assert pathological
    assert claims == []


def test_adjudicator_document_merges_windows_disjoint() -> None:
    pages = _window_pages()
    adj = _MockAdjudicator("DROP p1n0\nDROP p2n0\nREJOIN p1n1 p2n1\n")
    ledger = adj.adjudicate_document(pages)
    # verify_ledger must pass on the produced ledger.
    reduced = apply_ledger(pages, ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, ledger, reduced) == []
    assert [c.text for c in reduced.children] == ["The provision states that the deadline is extended."]


def test_adjudicator_truncation_falls_back_per_window() -> None:
    pages = _window_pages()

    class _TruncAdj(DeFacsimileAdjudicator):
        def is_available(self) -> bool:
            return True

        def _chat(self, system: str, user: str, *, window: str) -> str:
            from lawvm.ingest.llm_backends.defacsimile_adjudicator import (
                AdjudicationTruncated,
            )

            raise AdjudicationTruncated(window=window, detail="truncated")

    result = _TruncAdj().adjudicate_window(pages)
    assert result.truncated
    # compose_pages would REJOIN the split body paragraph (unterminated → lower).
    assert all(c.method == "deterministic_fallback" for c in result.claims)


# --------------------------------------------------------------------------- #
# defacsimile() orchestrator: fallback adapter (Decision 8)                    #
# --------------------------------------------------------------------------- #


def test_defacsimile_none_adjudicator_uses_deterministic_fallback() -> None:
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "A sentence continuing", 1, meta=NodeMetadata(y_order=0)),))
    p2 = _page(2, (_node(SourceDocumentNodeKind.PARAGRAPH, "onto the next page.", 2, meta=NodeMetadata(y_order=0)),))
    doc = defacsimile([p1, p2], _ROOT_ANCHOR, adjudicator=None)
    assert isinstance(doc, DeFacsimiledDocument)
    assert doc.page_count == 2
    assert all(c.method == "deterministic_fallback" for c in doc.ledger.claims)
    assert [c.text for c in doc.root.children] == ["A sentence continuing onto the next page."]


def test_defacsimile_falls_back_when_model_ledger_fails_verification() -> None:
    pages = _window_pages()
    # A model that emits a phantom drop → verify fails → deterministic fallback.
    bad = _MockAdjudicator("DROP p1n5\n")
    doc = defacsimile(pages, _ROOT_ANCHOR, adjudicator=bad)
    assert all(c.method == "deterministic_fallback" for c in doc.ledger.claims)
    # verify passes on the emitted (fallback) ledger.
    assert verify_ledger(pages, doc.ledger, doc.root) == []


def test_defacsimile_uses_model_ledger_when_it_verifies() -> None:
    pages = _window_pages()
    good = _MockAdjudicator("DROP p1n0\nDROP p2n0\nREJOIN p1n1 p2n1\n")
    doc = defacsimile(pages, _ROOT_ANCHOR, adjudicator=good)
    assert any(c.method == "model_adjudicated" for c in doc.ledger.claims)


# --------------------------------------------------------------------------- #
# HE-2015/1 defect fixtures (spec §2 / §8)                                     #
# --------------------------------------------------------------------------- #


def test_he_defect_annex_triplicate_dedup_to_one() -> None:
    # The same annex boilerplate line recurs on 3 consecutive pages at the seam.
    def annex(page: int) -> SourceDocumentNode:
        return _node(SourceDocumentNodeKind.PARAGRAPH, "Annex I boilerplate clause.", page, meta=NodeMetadata(band="body", band_count=3, y_order=0))

    pages = [_page(1, (annex(1),)), _page(2, (annex(2),)), _page(3, (annex(3),))]
    adj = _MockAdjudicator(
        {
            "1+2": "DEDUP p1n0 p2n0\n",
            "2+3": "DEDUP p2n0 p3n0\n",
        }
    )
    ledger = adj.adjudicate_document(pages)
    reduced = apply_ledger(pages, ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, ledger, reduced) == []
    # triplicate collapsed to exactly one.
    assert [c.text for c in reduced.children] == ["Annex I boilerplate clause."]


def test_he_defect_legit_table_header_kept_not_dedup() -> None:
    # A printed table's per-page header is a legitimate repeat → KEEP, not DEDUP.
    def table(page: int) -> SourceDocumentNode:
        return _node(
            SourceDocumentNodeKind.TABLE,
            "",
            page,
            meta=NodeMetadata(band="body", y_order=0),
            children=(
                _row((_cell("Year", page, header=True), _cell("Sum", page, header=True)), page),
                _row((_cell(f"201{page}", page), _cell(f"{page}00", page)), page),
            ),
        )

    pages = [_page(1, (table(1),)), _page(2, (table(2),))]
    adj = _MockAdjudicator({"1+2": "KEEP p2n0\n"})
    ledger = adj.adjudicate_document(pages)
    reduced = apply_ledger(pages, ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, ledger, reduced) == []
    # both table blocks survive (a KEEP is non-destructive).
    assert sum(1 for c in reduced.children if c.kind is SourceDocumentNodeKind.TABLE) == 2


def test_he_defect_table_rejoin_absorbs_repeated_header() -> None:
    # A multi-page table: page-2 leading table continues page-1's; its repeated
    # header row is ABSORBED (Decision 3), not re-emitted.
    t1 = _node(
        SourceDocumentNodeKind.TABLE,
        "",
        1,
        meta=NodeMetadata(band="body", y_order=0),
        children=(
            _row((_cell("Year", 1, header=True), _cell("Sum", 1, header=True)), 1),
            _row((_cell("2011", 1), _cell("100", 1)), 1),
        ),
    )
    t2 = _node(
        SourceDocumentNodeKind.TABLE,
        "",
        2,
        meta=NodeMetadata(band="body", y_order=0),
        children=(
            _row((_cell("Year", 2, header=True), _cell("Sum", 2, header=True)), 2),
            _row((_cell("2012", 2), _cell("200", 2)), 2),
        ),
    )
    pages = [_page(1, (t1,)), _page(2, (t2,))]
    # REJOIN the two tables, absorbing page-2's repeated header row (p2n0's row 0).
    ledger = DeFacsimileLedger(
        claims=(
            DeFacsimileClaim(
                DeFacsimileOp.REJOIN,
                (SpanRef(1, (0,)), SpanRef(2, (0,))),
                AssuranceTier.SINGLE_WITNESS,
                ("defacsimile_adjudicator",),
                absorbed=(SpanRef(2, (0, 0)),),
            ),
        )
    )
    reduced = apply_ledger(pages, ledger, _ROOT_ANCHOR)
    assert verify_ledger(pages, ledger, reduced) == []
    tables = [c for c in reduced.children if c.kind is SourceDocumentNodeKind.TABLE]
    assert len(tables) == 1
    merged = tables[0]
    # data rows preserved; the second "Year"/"Sum" header row absorbed (one header).
    header_rows = sum(
        1 for r in merged.children if any(c.attrs.get("is_header") == "1" for c in r.children)
    )
    assert header_rows == 1
    txt = _reduced_text(merged)
    assert "2011" in txt and "2012" in txt


def test_he_defect_seam_header_dedup_and_furniture_drop_and_rejoin() -> None:
    # Combined: running header dropped both pages, body paragraph rejoined.
    p1 = _page(
        1,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "HE 1/2015 vp", 1, meta=NodeMetadata(band="top", furniture=True, band_count=6, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "Section 3 provides for the transfer of", 1, meta=NodeMetadata(band="body", y_order=1)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "1", 1, meta=NodeMetadata(band="bottom", furniture=True, band_count=6, y_order=9)),
        ),
    )
    p2 = _page(
        2,
        (
            _node(SourceDocumentNodeKind.PARAGRAPH, "HE 1/2015 vp", 2, meta=NodeMetadata(band="top", furniture=True, band_count=6, y_order=0)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "competence to the new authority.", 2, meta=NodeMetadata(band="body", y_order=1)),
            _node(SourceDocumentNodeKind.PARAGRAPH, "2", 2, meta=NodeMetadata(band="bottom", furniture=True, band_count=6, y_order=9)),
        ),
    )
    adj = _MockAdjudicator("DROP p1n0\nDROP p1n2\nDROP p2n0\nDROP p2n2\nREJOIN p1n1 p2n1\n")
    ledger = adj.adjudicate_document([p1, p2])
    reduced = apply_ledger([p1, p2], ledger, _ROOT_ANCHOR)
    assert verify_ledger([p1, p2], ledger, reduced) == []
    assert [c.text for c in reduced.children] == [
        "Section 3 provides for the transfer of competence to the new authority."
    ]


def test_verify_error_raised_only_via_orchestrator_guard() -> None:
    # The orchestrator NEVER emits an unverified ledger — even the fallback is
    # verified; a fabricated un-foldable state would raise. Here we assert the
    # public contract that a passing doc's ledger verifies clean.
    p1 = _page(1, (_node(SourceDocumentNodeKind.PARAGRAPH, "Only body.", 1, meta=NodeMetadata(y_order=0)),))
    doc = defacsimile([p1], _ROOT_ANCHOR, adjudicator=None)
    assert verify_ledger([p1], doc.ledger, doc.root) == []
    with pytest.raises(LedgerVerificationError):
        # A directly-forged ledger with a phantom drop, fed to a strict re-check,
        # is what the orchestrator guards against; simulate the raise path.
        bad = DeFacsimileLedger(
            claims=(DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (7,)),), AssuranceTier.SINGLE_WITNESS, ("x",)),)
        )
        reduced = apply_ledger([p1], bad, _ROOT_ANCHOR)
        if verify_ledger([p1], bad, reduced):
            raise LedgerVerificationError("phantom")
