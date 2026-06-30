"""Increment-0 existence proof: dated multi-act EU replay end-to-end (offline).

This ties the four new EU pieces into ONE vertical slice on pinned fixtures (no
network):

  DAG (eu_amendment_graph)
    -> grammar lowering (fmx4_amendment_grammar, quoted-block capture)
    -> date-of-application ordering (eu_ordering, NOT lexical CELEX)
    -> replay via apply_eu_ops_conserved (the Wave-4 core seam)
    -> oracle divergence vs a consolidation (eu_oracle_divergence, NEVER repaired)

It proves the four properties the design says EU lacked: ordering is
legal-chronological, same-moment cross-act conflict is LIVE (two acts share a
date-of-application touching the same provision), coverage is measured, and the
oracle divergence is a first-class finding.
"""

from __future__ import annotations

from pathlib import Path

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.eu.eu_ordering import eu_ordering_profile, order_eu_ops
from lawvm.eu.eu_oracle_divergence import compare_replay_to_consolidation
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
from lawvm.eu.grafter import parse_eu_regulation_ir
from lawvm.eu.pipeline import apply_eu_ops_conserved

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0679"


def _base() -> IRStatute:
    return parse_eu_regulation_ir(FIXTURES / "base_act_excerpt.fmx4.xml", celex=BASE_CELEX)


def _sections(statute: IRStatute) -> dict[str, str]:
    out: dict[str, str] = {}

    def _walk(node: IRNode) -> None:
        if str(node.kind) == "section" and node.label:
            out[str(node.label)] = node.text
        for child in node.children:
            _walk(child)

    _walk(statute.body)
    return out


# --------------------------------------------------------------------------- #
# Replay end-to-end                                                           #
# --------------------------------------------------------------------------- #


def test_replay_applies_lowered_ops_to_base() -> None:
    base = _base()
    lowered = lower_amending_act(
        (FIXTURES / "amending_act_excerpt.fmx4.xml").read_bytes(),
        "32017R0488",
        base_celex=BASE_CELEX,
        effective="2017-03-23",
    )
    ordered = order_eu_ops(lowered.ops)
    result = apply_eu_ops_conserved(base, list(ordered.ops))

    # Conservation: every op accounted for (applied or a typed RejectedItem).
    assert len(result.applied_ops) + len(result.skipped_items) == len(ordered.ops)
    assert len(result.applied_ops) == 4

    secs = _sections(result.statute)
    # Article 5 replaced (new text), 5a inserted, 7 repealed, 9 still present.
    assert "lawful only where consent" in secs["5"]
    assert "5a" in secs
    assert "7" not in secs
    assert "9" in secs


# --------------------------------------------------------------------------- #
# Ordering is legal-chronological, NOT lexical-by-CELEX                        #
# --------------------------------------------------------------------------- #


def _repeal(celex: str, effective: str, seq: int) -> LegalOperation:
    return LegalOperation(
        op_id=f"{celex}-{seq}",
        sequence=seq,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("article", "7"),)),
        source=OperationSource(statute_id=celex, effective=effective),
    )


def test_ordering_is_chronological_not_lexical() -> None:
    # 32017R0489 is lexically LATER but applies EARLIER would be false; here the
    # 2016 act applies first despite a higher input sequence — proving the sort
    # is by date-of-application, not discovery/lexical order.
    ops = [
        _repeal("32017R0489", "2017-03-23", seq=2),
        _repeal("32016R0819", "2016-05-25", seq=1),
    ]
    ordered = order_eu_ops(ops)
    got = [op.source.statute_id for op in ordered.ops if op.source]
    assert got == ["32016R0819", "32017R0489"]


# --------------------------------------------------------------------------- #
# Same-moment cross-act conflict is LIVE                                       #
# --------------------------------------------------------------------------- #


def _replace_art5(celex: str, effective: str, seq: int, text: str) -> LegalOperation:
    return LegalOperation(
        op_id=f"{celex}-{seq}",
        sequence=seq,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("article", "5"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="5", text=text),
        source=OperationSource(statute_id=celex, effective=effective),
    )


def test_same_moment_two_acts_touch_same_provision() -> None:
    # Two distinct amending acts BOTH replace Article 5 at the SAME
    # date-of-application (2017-03-23) with DIFFERENT text — a genuine
    # same-moment cross-act collision the shared detector must surface.
    ops = [
        _replace_art5("32017R0488", "2017-03-23", 1, "Text A from act 488."),
        _replace_art5("32017R0489", "2017-03-23", 1, "Text B from act 489."),
    ]
    ordered = order_eu_ops(ops)
    # The detector is ADDITIVE — it emits findings, does not drop ops. With a
    # shared instant + same target + incompatible payload, it produces an
    # eu-prefixed same-moment finding (vacuously impossible before dates).
    assert any(
        f.kind.startswith("eu") for f in ordered.findings
    ), f"expected an eu-prefixed same-moment finding, got {[f.kind for f in ordered.findings]}"


def test_profile_is_eu_prefixed_and_dated() -> None:
    profile = eu_ordering_profile()
    assert profile.finder_kind_prefix == "eu"
    # The temporal key is date-driven (not the sequence-identity default).
    op = _repeal("32017R0488", "2017-03-23", 1)
    assert profile.temporal_key(op)[0] == "2017-03-23"


# --------------------------------------------------------------------------- #
# Oracle divergence is a first-class finding, NEVER repaired                   #
# --------------------------------------------------------------------------- #


def test_oracle_agreement_when_replay_matches_consolidation() -> None:
    base = _base()
    lowered = lower_amending_act(
        (FIXTURES / "amending_act_excerpt.fmx4.xml").read_bytes(),
        "32017R0488",
        effective="2017-03-23",
    )
    ordered = order_eu_ops(lowered.ops)
    replayed = apply_eu_ops_conserved(base, list(ordered.ops)).statute

    # A consolidation that MATCHES the replay on the shared articles -> agreement.
    consolidated = replayed  # identical body -> perfect agreement
    cmp = compare_replay_to_consolidation(
        replayed, consolidated, as_of="2017-03-23", base_celex=BASE_CELEX
    )
    assert cmp.divergence_count == 0
    assert cmp.agreement_fraction == 1.0


def test_oracle_divergence_classified_not_repaired() -> None:
    base = _base()
    lowered = lower_amending_act(
        (FIXTURES / "amending_act_excerpt.fmx4.xml").read_bytes(),
        "32017R0488",
        effective="2017-03-23",
    )
    ordered = order_eu_ops(lowered.ops)
    replayed = apply_eu_ops_conserved(base, list(ordered.ops)).statute

    # An EDITORIAL consolidation that DIFFERS from the native replay on Article 5
    # AND drops Article 5a (a consolidation error / editorial choice). The
    # comparator must CLASSIFY, never mutate the replay toward the oracle.
    oracle_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="5", text="EDITORIALLY DIFFERENT Article 5."),
            IRNode(kind=IRNodeKind.SECTION, label="9", text=""),
            IRNode(kind=IRNodeKind.SECTION, label="12", text=""),
        ),
    )
    oracle = IRStatute(statute_id="01999R0679-20170323", title="consolidated", body=oracle_body)

    cmp = compare_replay_to_consolidation(
        replayed, oracle, as_of="2017-03-23", base_celex=BASE_CELEX
    )
    kinds = cmp.divergences_by_kind()
    # Article 5 text diverges; Article 5a is present in replay, absent in oracle.
    assert kinds.get("text_divergence", 0) >= 1
    assert kinds.get("present_in_replay_absent_in_oracle", 0) >= 1
    assert cmp.divergence_count >= 2

    # NEVER repaired: the replayed body is untouched after comparison.
    assert "lawful only where consent" in _sections(replayed)["5"]
