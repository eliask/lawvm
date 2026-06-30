"""Tests for the deterministic Table III act-section -> USC classification resolver.

Synthetic-only: a handful of :class:`Table3Record` rows exercise every resolver
behaviour (older-act chapter+PL form and modern-PL ``num`` form; range expansion;
sub-section root peel; ``nt`` uncodified holdout; repealed-status surfacing;
ambiguity refusal) plus the Table-III-vs-href agreement/disagreement adjudication
wired into :func:`resolve_nonpositive_target`.
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.import_table3 import Table3Record
from lawvm.us_federal.nonpositive import (
    NonPositiveResolveStatus,
    RULE_TABLE3,
    RULE_TABLE3_HREF_AGREE,
    RULE_TABLE3_HREF_DISAGREE,
    resolve_nonpositive_target,
)
from lawvm.us_federal.table3 import (
    AgreementVerdict,
    Table3ResolveStatus,
    Table3Resolver,
    adjudicate,
    normalize_act_key,
    section_root,
)


def _rec(
    act_num: str,
    act_section: str,
    usc_title: str,
    usc_section: str,
    *,
    congress: str = "",
    public_law: str = "",
    status: str = "",
    usckey: str = "k",
) -> Table3Record:
    return Table3Record(
        act_num=act_num,
        act_congress=congress,
        act_section=act_section,
        usc_title=usc_title,
        usc_section=usc_section,
        status=status,
        public_law=public_law,
        usckey=usckey,
    )


# A synthetic Table III: the Securities Act of 1933 (chapter 38 / 73rd Congress /
# public-law 22) sec 5 -> 15 U.S.C. 77e; a modern PL 117-2 range and a repealed
# sub-section row; and a 117-9 ``nt`` uncodified note target.
_RECORDS = [
    _rec("38", "5", "15", "77e", congress="73", public_law="22", usckey="kSec5"),
    _rec("117-2", "2001-2004", "42", "300", usckey="kRange"),
    _rec("117-2", "1101(a)", "26", "461", status="Rep.", usckey="kRep"),
    _rec("117-9", "1", "15", "9001 nt", usckey="knt_nt"),
    # An ambiguous pair: two classified rows for the same act-section disagree.
    _rec("118-5", "7", "20", "100", usckey="kAmbA"),
    _rec("118-5", "7", "20", "200", usckey="kAmbB"),
]


def _resolver() -> Table3Resolver:
    return Table3Resolver(_RECORDS)


# --- normalization ---------------------------------------------------------


def test_normalize_act_key_forms() -> None:
    assert normalize_act_key("117-2") == "117-2"
    assert normalize_act_key("PL 117-2") == "117-2"
    assert normalize_act_key("P.L. 117–2") == "117-2"  # en-dash
    assert normalize_act_key("531") == "531"
    assert normalize_act_key("") == ""


def test_section_root() -> None:
    assert section_root("1101(a)") == "1101"
    assert section_root("78o-10") == "78o"
    assert section_root("5") == "5"


# --- resolver ---------------------------------------------------------------


def test_older_act_chapter_and_congress_pl_forms_resolve() -> None:
    r = _resolver()
    want = LegalAddress(path=(("title", "15"), ("section", "77e")))
    # Older-act chapter <num> form.
    res_chapter = r.resolve("38", "5")
    assert res_chapter.status is Table3ResolveStatus.CLASSIFIED
    assert res_chapter.address == want
    assert res_chapter.usckey == "kSec5"
    # Synthetic {congress}-{public_law} form indexes the same record.
    assert r.resolve("73-22", "5").address == want
    # PL prose form normalizes too.
    assert r.resolve("PL 73-22", "5").address == want


def test_modern_pl_num_form_resolves() -> None:
    r = _resolver()
    res = r.resolve("117-2", "2002")
    assert res.status is Table3ResolveStatus.CLASSIFIED
    assert res.address == LegalAddress(path=(("title", "42"), ("section", "300")))


def test_range_expansion() -> None:
    r = _resolver()
    want = LegalAddress(path=(("title", "42"), ("section", "300")))
    # Every member of "2001-2004" resolves to the range target...
    for n in ("2001", "2002", "2003", "2004"):
        assert r.resolve("117-2", n).address == want, n
    # ...and the literal range key resolves too.
    assert r.resolve("117-2", "2001-2004").address == want
    # Outside the range: no match.
    assert r.resolve("117-2", "2005").status is Table3ResolveStatus.UNMAPPED


def test_subsection_root_peel() -> None:
    r = _resolver()
    want = LegalAddress(path=(("title", "26"), ("section", "461")))
    # "1101(a)" indexes under root 1101; deeper "1101(a)(1)" peels to it.
    assert r.resolve("117-2", "1101(a)").address == want
    assert r.resolve("117-2", "1101(a)(1)").address == want
    assert r.resolve("117-2", "1101").address == want


def test_repealed_status_surfaced_but_still_resolved() -> None:
    r = _resolver()
    res = r.resolve("117-2", "1101(a)")
    assert res.status is Table3ResolveStatus.CLASSIFIED
    assert res.address is not None  # a repealed classification is still a mapping
    assert res.usc_status == "Rep."
    assert res.is_repealed


def test_nt_uncodified_held_out() -> None:
    r = _resolver()
    res = r.resolve("117-9", "1")
    assert res.status is Table3ResolveStatus.UNCODIFIED
    assert res.address is None  # NEVER guessed onto a codified section


def test_ambiguous_classification_refused() -> None:
    r = _resolver()
    res = r.resolve("118-5", "7")
    assert res.status is Table3ResolveStatus.AMBIGUOUS
    assert res.address is None
    assert set(res.candidates) == {"20:100", "20:200"}


def test_unmapped_when_no_match() -> None:
    r = _resolver()
    assert r.resolve("999-1", "1").status is Table3ResolveStatus.UNMAPPED


def test_classification_compatible_adapter() -> None:
    r = _resolver()
    # Drops into the ClassificationIndex.resolve (statute_id, pl_section) contract.
    assert r.resolve_classification("PL 117-2", "2002") == LegalAddress(
        path=(("title", "42"), ("section", "300"))
    )
    # Uncodified / ambiguous -> None (no guess).
    assert r.resolve_classification("PL 117-9", "1") is None
    assert r.resolve_classification("PL 118-5", "7") is None


# --- agreement adjudication (§1.7) -----------------------------------------


def test_adjudicate_verdicts() -> None:
    a = LegalAddress(path=(("title", "15"), ("section", "77e")))
    b = LegalAddress(path=(("title", "15"), ("section", "77f")))
    assert adjudicate(a, a).verdict is AgreementVerdict.AGREE
    assert adjudicate(a, a).chosen == a
    dis = adjudicate(a, b)
    assert dis.verdict is AgreementVerdict.DISAGREE
    assert dis.chosen == b  # existing witness kept; never silently overwritten
    assert dis.table3_address == a
    assert adjudicate(a, None).verdict is AgreementVerdict.TABLE3_ONLY
    assert adjudicate(None, b).verdict is AgreementVerdict.EXISTING_ONLY
    assert adjudicate(None, None).verdict is AgreementVerdict.NEITHER


# --- nonpositive wiring -----------------------------------------------------


def test_nonpositive_resolves_via_table3_when_no_govinfo_channel() -> None:
    r = _resolver()
    # No href, no parenthetical: Table III is the resolver (the residual gap this
    # capability dissolves).
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933",
        table3=r,
        act_key="38",
        act_section="5",
    )
    assert w.resolve_status is NonPositiveResolveStatus.TABLE3
    assert w.rule_id == RULE_TABLE3
    assert w.address == LegalAddress(path=(("title", "15"), ("section", "77e")))
    assert w.table3_usckey == "kSec5"


def test_nonpositive_table3_holds_out_nt() -> None:
    r = _resolver()
    w = resolve_nonpositive_target(
        target_phrase="Section 1 of the Foo Act",
        table3=r,
        act_key="117-9",
        act_section="1",
    )
    # nt is held out: no Table III address, falls through to unmapped (not guessed).
    assert w.address is None
    assert w.resolve_status is NonPositiveResolveStatus.UNMAPPED


def test_nonpositive_table3_href_agreement() -> None:
    r = _resolver()
    # Both Table III and the structural href resolve to 15 U.S.C. 77e -> AGREE.
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)",
        target_href="/us/usc/t15/s77e",
        table3=r,
        act_key="38",
        act_section="5",
    )
    assert w.address == LegalAddress(path=(("title", "15"), ("section", "77e")))
    assert w.rule_id == RULE_TABLE3_HREF_AGREE
    assert w.table3_usckey == "kSec5"


def test_nonpositive_table3_href_disagreement_keeps_existing() -> None:
    r = _resolver()
    # Table III says 15:77e but the href lands on 15:77f -> DISAGREE. The existing
    # href witness is kept (never overwritten); divergence flagged as evidence.
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933",
        target_href="/us/usc/t15/s77f",
        table3=r,
        act_key="38",
        act_section="5",
    )
    assert w.address == LegalAddress(path=(("title", "15"), ("section", "77f")))
    assert w.rule_id == RULE_TABLE3_HREF_DISAGREE
    assert w.table3_usckey == "kSec5"


def test_nonpositive_without_table3_is_unchanged() -> None:
    # No resolver, no act key: behaviour is byte-for-byte the prior baseline.
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)",
        target_href="/us/usc/t15/s77e",
    )
    assert w.rule_id == "us_nonpositive_target_paren_href_agree"
    assert w.table3_usckey == ""


# --- corpus assertion (real Table III over the farchive) --------------------


def test_corpus_real_nonpositive_target_resolves_via_table3() -> None:
    """A real non-positive (title 42) act-section newly resolves from Table III.

    Skips when the Table III bulk XML is not in the farchive (build hosts without
    the canonical data root). PL 111-148 (the ACA) §1311 was previously a
    classification-table data gap; it now resolves deterministically to 42 U.S.C.
    18031, while an ambiguous §1001 stays refused (never guessed).
    """
    import pytest

    from lawvm.us_federal.table3 import (
        load_default_table3_resolver,
        reset_default_table3_resolver,
    )

    reset_default_table3_resolver()
    resolver = load_default_table3_resolver()
    if resolver is None:
        pytest.skip("Table III bulk XML not present in farchive")

    # Newly-resolved target: ACA §1311 -> 42 U.S.C. 18031 (title 42, non-positive).
    res = resolver.resolve("111-148", "1311")
    assert res.status is Table3ResolveStatus.CLASSIFIED
    assert res.address == LegalAddress(path=(("title", "42"), ("section", "18031")))

    # End-to-end through the non-positive resolver with NO govinfo href: Table III
    # is the sole resolver and stamps the auditable rule id.
    w = resolve_nonpositive_target(
        target_phrase="Section 1311 of the Patient Protection and Affordable Care Act",
        table3=resolver,
        act_key="111-148",
        act_section="1311",
    )
    assert w.resolve_status is NonPositiveResolveStatus.TABLE3
    assert w.rule_id == RULE_TABLE3
    assert w.address == LegalAddress(path=(("title", "42"), ("section", "18031")))
    assert w.table3_usckey  # the resolving Table III record is witnessed

    # An ambiguous act-section stays refused — never guessed onto a section.
    assert resolver.resolve("111-148", "1001").status is Table3ResolveStatus.AMBIGUOUS
