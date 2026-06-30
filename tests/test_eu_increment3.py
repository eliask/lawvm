"""Increment 3: harder sub-article shapes + separate-annex payloads + corpus-scale divergence.

Increment 2 closed the dominant real EU sanctions-amender long-tail (the indirect
annex amendment) but DEFERRED three shapes (its findings §"DEFERRED (Increment 3)"):

  (a) sub-paragraph / list-item (point/indent) / renumber instruction shapes —
      Increment 2 left these as typed ``uncovered_instruction`` residuals because
      no acquired ACT-root sample carried them;
  (b) materialising the SEPARATE-manifestation replacement annex (Increment 2
      recorded it as a typed payload gap);
  (c) corpus-scale divergence (drive the consolidation-PIT oracle over a larger
      slice and report a typed divergence account with a real denominator).

This module covers all three end-to-end on small fixed fixtures (parse → lower →
apply → oracle-compare), plus the typed-residual ownership of what still does not
parse / does not yet materialise.

Honesty discipline (total accounting): every instruction is an op or a typed
diagnostic; every applied op lands or is a typed RejectedItem; every compared
article is owned by exactly one corpus divergence class (denominator == sum).
"""

from __future__ import annotations

import typing
from pathlib import Path

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.eu.eu_consolidation_oracle import build_consolidation_oracle
from lawvm.eu.eu_oracle_divergence import (
    CORPUS_DIVERGENCE_CLASSES,
    CorpusDivergenceAccount,
)
from lawvm.eu.eu_ordering import order_eu_ops
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
from lawvm.eu.grafter import parse_eu_regulation_ir
from lawvm.eu.pipeline import apply_eu_ops_conserved

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0044"

_SUBP = typing.cast(IRNodeKind, "subparagraph")
_ITEM = typing.cast(IRNodeKind, "item")
_PARAG = typing.cast(IRNodeKind, "paragraph")
_ANNEX = typing.cast(IRNodeKind, "annex")


# --------------------------------------------------------------------------- #
# Goal 1 — the harder sub-article shapes now PARSE (parse → lower)             #
# --------------------------------------------------------------------------- #


def _subart_shapes_result():
    return lower_amending_act(
        (FIXTURES / "amending_subart_shapes_excerpt.fmx4.xml").read_bytes(),
        "32016R9003",
        base_celex=BASE_CELEX,
        effective="2099-03-01",
    )


def test_subart_shapes_lower_to_typed_ops() -> None:
    """The 4 sub-article shapes Increment 2 left as residuals now lower: a
    subparagraph REPLACE, a point INSERT, an indent REPEAL, and an article
    RENUMBER. Article 5 (entry-into-force) is the only typed residual."""
    r = _subart_shapes_result()
    assert r.instruction_count == 5
    assert r.covered_count == 4
    by_rule = {op.witness_rule_id: op for op in r.ops}

    sub = by_rule["EU_FMX4.SUBART_SUBPARAGRAPH_REPLACE"]
    assert sub.action == StructuralAction.REPLACE
    assert str(sub.target) == "article:8/paragraph:2/subparagraph:2"
    assert sub.payload is not None and "ten working days" in sub.payload.text

    pt = by_rule["EU_FMX4.SUBART_POINT_INSERT"]
    assert pt.action == StructuralAction.INSERT
    assert str(pt.target) == "article:11/point:d"
    assert pt.payload is not None and "humanitarian goods" in pt.payload.text

    ind = by_rule["EU_FMX4.INDENT_REPEAL"]
    assert ind.action == StructuralAction.REPEAL
    assert str(ind.target) == "article:13/item:2"

    rn = by_rule["EU_FMX4.ARTICLE_RENUMBER"]
    assert rn.action == StructuralAction.RENUMBER
    assert str(rn.target) == "article:21"
    assert "renumber_to=article:21a" in rn.provenance_tags

    # Conservation: the single residual is the out-of-scope entry-into-force clause.
    uncovered = [
        d for d in r.diagnostics if d.rule_id == "eu_fmx4_grammar_uncovered_instruction"
    ]
    assert len(uncovered) == 1
    assert "enter into force" in uncovered[0].source_excerpt.lower()


def test_ordinal_normalisation_first_second_arabic() -> None:
    """Spelled and arabic ordinals normalise to a 1-based subparagraph index."""
    from lawvm.eu.fmx4_amendment_grammar import _ordinal_to_index

    assert _ordinal_to_index("first") == "1"
    assert _ordinal_to_index("second") == "2"
    assert _ordinal_to_index("tenth") == "10"
    assert _ordinal_to_index("3rd") == "3"
    assert _ordinal_to_index("12") == "12"


# --------------------------------------------------------------------------- #
# Goal 1 — the shapes APPLY end-to-end (lower → order → apply)                 #
# --------------------------------------------------------------------------- #


def test_subart_shapes_apply_resolvable_targets_and_own_renumber() -> None:
    """The structural sub-article edits land against a base carrying their
    targets; RENUMBER is OWNED by the apply seam as a typed
    ``eu_replay_unsupported_action`` skip — never a silent drop."""
    r = _subart_shapes_result()
    base = IRStatute(
        statute_id=BASE_CELEX,
        title="base",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="8",
                    children=(
                        IRNode(
                            kind=_PARAG,
                            label="2",
                            children=(
                                IRNode(kind=_SUBP, label="1", text="first sub"),
                                IRNode(kind=_SUBP, label="2", text="OLD second sub"),
                            ),
                        ),
                    ),
                ),
                IRNode(kind=IRNodeKind.SECTION, label="11", text="Article 11 body."),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="13",
                    children=(
                        IRNode(kind=_ITEM, label="1", text="indent 1"),
                        IRNode(kind=_ITEM, label="2", text="OLD indent 2"),
                    ),
                ),
                IRNode(kind=IRNodeKind.SECTION, label="21", text="Article 21 body."),
            ),
        ),
    )
    ordered = order_eu_ops(list(r.ops))
    res = apply_eu_ops_conserved(base, list(ordered.ops))

    # Conservation: every op applied or a typed RejectedItem.
    assert len(res.applied_ops) + len(res.skipped_items) == len(ordered.ops)
    # 3 structural edits land; renumber is the single typed skip.
    assert len(res.applied_ops) == 3
    assert len(res.skipped_items) == 1
    skipped = res.skipped_items[0]
    assert skipped.item.witness_rule_id == "EU_FMX4.ARTICLE_RENUMBER"
    assert skipped.reason_code == "eu_replay_unsupported_action"


# --------------------------------------------------------------------------- #
# Goal 2 — separate-annex payload threading                                    #
# --------------------------------------------------------------------------- #

_INDIRECT_NO_ANNEX = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART>
  <ALINEA>Annex II to Regulation (EU) 2016/44 is replaced by the list set out in the Annex to this Regulation.</ALINEA>
</ARTICLE></ENACTING.TERMS></ACT>"""


def test_separate_annex_resolver_materialises_payload() -> None:
    """When the replacement annex ships as a SEPARATE manifestation, a caller
    resolver materialises it: the op carries the real body and a
    ``separate_resolved`` provenance tag — the Increment-2 gap is closed."""
    seen: list[tuple[str, str]] = []

    def resolver(celex: str, label: str) -> str | None:
        seen.append((celex, label))
        return "List replacing Annex II: Entry X; Entry Y."

    r = lower_amending_act(
        _INDIRECT_NO_ANNEX,
        "32018R0870",
        base_celex=BASE_CELEX,
        resolve_separate_annex=resolver,
    )
    assert seen == [("32018R0870", "II")]
    op = r.ops[0]
    assert op.witness_rule_id == "EU_FMX4.ANNEX_AMENDED_AS_SET_OUT"
    assert str(op.target) == "annex:II"
    assert op.payload is not None and "Entry X" in op.payload.text
    assert "annex_payload=separate_resolved" in op.provenance_tags
    # No payload-gap diagnostic: the gap is materialised, not recorded.
    assert not any(
        d.rule_id == "eu_fmx4_grammar_annex_as_set_out_payload_separate"
        for d in r.diagnostics
    )


def test_separate_annex_resolver_none_preserves_typed_gap() -> None:
    """A resolver that cannot materialise the annex (returns None) preserves the
    Increment-2 typed gap — honest recorded gap, never a fabricated payload."""
    r = lower_amending_act(
        _INDIRECT_NO_ANNEX,
        "32018R0870",
        base_celex=BASE_CELEX,
        resolve_separate_annex=lambda c, lbl: None,
    )
    op = r.ops[0]
    assert "annex_payload=separate_manifestation" in op.provenance_tags
    assert (op.payload.text if op.payload else "") == ""
    assert any(
        d.rule_id == "eu_fmx4_grammar_annex_as_set_out_payload_separate"
        for d in r.diagnostics
    )


def test_separate_annex_no_resolver_is_increment2_behaviour() -> None:
    """Absent a resolver, the Increment-2 behaviour is unchanged (typed gap)."""
    r = lower_amending_act(_INDIRECT_NO_ANNEX, "32018R0870", base_celex=BASE_CELEX)
    assert "annex_payload=separate_manifestation" in r.ops[0].provenance_tags
    assert any(
        d.rule_id == "eu_fmx4_grammar_annex_as_set_out_payload_separate"
        for d in r.diagnostics
    )


# --------------------------------------------------------------------------- #
# Goal 3 — corpus-scale divergence account (parse → lower → apply → oracle)    #
# --------------------------------------------------------------------------- #


def _fetch_fixture(name: str):
    def _fetch(_celex: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _fetch


def _replay_corpus_base() -> IRStatute:
    base = parse_eu_regulation_ir(
        FIXTURES / "corpus_base_act.fmx4.xml", celex="32099R0001"
    )
    r = lower_amending_act(
        (FIXTURES / "corpus_amender_a.fmx4.xml").read_bytes(),
        "32099R9001",
        base_celex="32099R0001",
        effective="2099-02-01",
    )
    ordered = order_eu_ops(list(r.ops))
    return apply_eu_ops_conserved(base, list(ordered.ops)).statute


def test_corpus_divergence_account_typed_classes_and_denominator() -> None:
    """Drive the consolidation-PIT oracle over a 2-PIT corpus and produce a typed
    divergence account. PIT-1 agrees on every article; PIT-2 exercises each typed
    class. The denominator (article_total) equals the sum of all class counts —
    total accounting, no silent loss. NEVER repairs the replay toward the oracle."""
    replayed = _replay_corpus_base()
    acct = CorpusDivergenceAccount()

    cmp1 = build_consolidation_oracle(
        replayed,
        base_celex="32099R0001",
        as_of="2099-02-01",
        fetch_consolidation=_fetch_fixture("corpus_cons_pit1.fmx4.xml"),
    )
    acct.add(cmp1)
    assert cmp1.divergences_by_kind() == {"agreement": 3}

    cmp2 = build_consolidation_oracle(
        replayed,
        base_celex="32099R0001",
        as_of="2099-06-01",
        fetch_consolidation=_fetch_fixture("corpus_cons_pit2.fmx4.xml"),
    )
    acct.add(cmp2)

    counts = acct.class_counts
    assert acct.act_count == 2
    assert counts["agreement"] == 4
    assert counts["text_diff"] == 1
    assert counts["deterministic_gap"] == 1
    assert counts["manual_frontier"] == 1
    assert counts["oracle_suspect"] == 0
    # The denominator and conservation.
    assert acct.article_total == 7
    assert acct.article_total == sum(counts[c] for c in CORPUS_DIVERGENCE_CLASSES)
    assert acct.divergence_total == 3
    assert acct.to_dict()["conserved"] is True


def test_corpus_oracle_suspect_is_caller_asserted_not_synthesised() -> None:
    """``oracle_suspect`` is the ``authoritative oracle ≠ correct`` class: an
    article a caller KNOWS is an editorial artifact. The comparator never
    synthesises it (it never repairs) — it is 0 unless the caller asserts a label,
    and asserting one moves that article out of its mechanical class."""
    replayed = _replay_corpus_base()
    acct = CorpusDivergenceAccount()
    cmp2 = build_consolidation_oracle(
        replayed,
        base_celex="32099R0001",
        as_of="2099-06-01",
        fetch_consolidation=_fetch_fixture("corpus_cons_pit2.fmx4.xml"),
    )
    # Caller asserts Article 4 (the editorial-only addition) is an oracle artifact.
    acct.add(cmp2, oracle_suspect_labels=frozenset({"4"}))
    assert acct.class_counts["oracle_suspect"] == 1
    # Article 4 moved OUT of manual_frontier into oracle_suspect.
    assert acct.class_counts["manual_frontier"] == 0
    assert acct.article_total == sum(
        acct.class_counts[c] for c in CORPUS_DIVERGENCE_CLASSES
    )
    assert acct.suspect_labels == {"32099R0001@2099-06-01": {"4"}}
