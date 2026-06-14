"""Real-effect source-binding via the extracted-affecting-source seam.

The 7 text-binding UK manual-compilation claim validators read the family phrasing
from the bound effect's source surface. On REAL feed effects the effect attributes
(``source_text`` / ``raw_text`` / ``comments`` / ``extracted_text``) are EMPTY —
the instruction prose lives in the extracted affecting XML. ``compile_ops_for_statute``
now threads that extracted source into each validator's source-binding stage as the
optional ``extracted_source_text`` parameter (the SAME surface the manual-frontier
classifier binds), with effect-attribute fallback for synthetic fixtures.

These tests pin the seam against REAL production effects drawn from the farchive:
each claim PASSES source-binding with the extracted source and FAILS it without —
proving the validators are no longer inert on real data — and a synthetic effect
carrying the prose in ``comments`` still binds via the fallback path.
"""

from __future__ import annotations

from lawvm.uk_legislation.savings_omission_claim import (
    BASIS_TEMPORAL_WINDOW,
    SavingsScopedOmissionClaim,
    validate_savings_scoped_omission_claim,
)
from lawvm.uk_legislation.source_feed_reconciliation_claim import (
    BASIS_GENUINELY_AMBIGUOUS,
    SourceFeedReconciliationClaim,
    validate_source_feed_reconciliation_claim,
)
from lawvm.uk_legislation.appropriate_place_claim import (
    POSITION_FOLLOWING_SIBLING,
    AppropriatePlaceInsertClaim,
    validate_appropriate_place_claim,
)

# Real extracted affecting-source strings (verified via select_source_for_effect
# against the production farchive). The effect attributes are empty for these rows;
# the prose below is what the extractor yields and the classifier classifies.
_SAVINGS_EXTRACTED = (
    "27 In Schedule 1 to the Judicial Pensions Act 1981 (c. 20) "
    "(pensions of Supreme Court officers, etc.), in paragraph 1, omit the "
    "reference to a Master of the Court of Protection except in the case of a "
    "person holding that office immediately before the commencement of this "
    "paragraph or who had previously retired from that office or died."
)
_N5_EXTRACTED = "a omit  “and” at the end of paragraph (d);"
_APPROPRIATE_PLACE_EXTRACTED = (
    "b in the appropriate place insert— Registered society Section 275"
)


class _EmptyEffect:
    """A real feed effect's shape: ids + verb populated, prose surfaces EMPTY."""

    def __init__(self, effect_id: str, effect_type: str) -> None:
        self.effect_id = effect_id
        self.effect_type = effect_type
        self.source_text = ""
        self.raw_text = ""
        self.comments = ""
        self.extracted_text = ""


def _savings_claim() -> SavingsScopedOmissionClaim:
    return SavingsScopedOmissionClaim(
        claim_id="sv-1981-20-sch1-mcp",
        claim_kind="savings_scoped_omission",
        statute_id="ukpga/1981/20",
        effect_id="key-9a180c89a3854e6607217be14136ae6e",
        affected_target="Sch. 1 para. 1",
        omitted_text="the reference to a Master of the Court of Protection",
        omission_anchor="Sch. 1 para. 1",
        saving_basis=BASIS_TEMPORAL_WINDOW,
        saving_scope="immediately before the commencement of this paragraph",
        saving_snippet=(
            "except in the case of a person holding that office immediately "
            "before the commencement of this paragraph or who had previously "
            "retired from that office or died"
        ),
        source_snippet=_SAVINGS_EXTRACTED,
    )


def _n5_claim() -> SourceFeedReconciliationClaim:
    return SourceFeedReconciliationClaim(
        claim_id="n5-2018-12-s205-and",
        claim_kind="source_feed_target_reconciliation",
        statute_id="ukpga/2018/12",
        effect_id="key-f66d9a5b3f893f3e97a604ffb950b0a7",
        effect_type="word omitted",
        source_named_target="s. 205(1) para. (d)",
        feed_named_target="s. 205(1)",
        resolved_target_eid="s. 205(1)",
        reconciliation_basis=BASIS_GENUINELY_AMBIGUOUS,
        source_snippet=_N5_EXTRACTED,
    )


def _appropriate_place_claim() -> AppropriatePlaceInsertClaim:
    return AppropriatePlaceInsertClaim(
        claim_id="ap-2008-17-s276-regsoc",
        claim_kind="appropriate_place_insert",
        statute_id="ukpga/2008/17",
        effect_id="key-006071d4bbac345161c87a6c2756e2c6",
        target_list_eid="section-276",
        entry_label="Registered society",
        entry_text="Section 275",
        source_snippet=_APPROPRIATE_PLACE_EXTRACTED,
        position_kind=POSITION_FOLLOWING_SIBLING,
        following_sibling_eid="some-eid",
    )


def test_savings_binds_with_extracted_source_fails_without():
    claim = _savings_claim()
    effect = _EmptyEffect(claim.effect_id, "words omitted")
    # Real feed effect: prose only in extracted XML. Without the seam the
    # effect-source binding stage has nothing to bind and REJECTS.
    without = validate_savings_scoped_omission_claim(
        claim, effect=effect, extracted_source_text=None
    )
    assert without.validated is False
    assert without.rule_id == "uk_savings_scoped_omission_claim_rejected_source_mismatch"
    # With the extracted source threaded in, the SAME classifier recognizer binds.
    with_seam = validate_savings_scoped_omission_claim(
        claim, effect=effect, extracted_source_text=_SAVINGS_EXTRACTED
    )
    assert with_seam.validated is True
    assert with_seam.rule_id == "uk_savings_scoped_omission_claim_validated"


def test_n5_binds_with_extracted_source_fails_without():
    claim = _n5_claim()
    effect = _EmptyEffect(claim.effect_id, "word omitted")
    without = validate_source_feed_reconciliation_claim(
        claim, effect=effect, extracted_source_text=None
    )
    assert without.validated is False
    assert (
        without.rule_id
        == "uk_source_feed_reconciliation_claim_rejected_source_mismatch"
    )
    with_seam = validate_source_feed_reconciliation_claim(
        claim, effect=effect, extracted_source_text=_N5_EXTRACTED
    )
    assert with_seam.validated is True
    assert with_seam.rule_id == "uk_source_feed_reconciliation_claim_validated"


def test_appropriate_place_binds_with_extracted_source_fails_without():
    claim = _appropriate_place_claim()
    effect = _EmptyEffect(claim.effect_id, "words inserted")
    without = validate_appropriate_place_claim(
        claim, effect=effect, extracted_source_text=None
    )
    assert without.validated is False
    assert without.rule_id == "uk_appropriate_place_claim_rejected_source_mismatch"
    with_seam = validate_appropriate_place_claim(
        claim, effect=effect, extracted_source_text=_APPROPRIATE_PLACE_EXTRACTED
    )
    assert with_seam.validated is True
    assert with_seam.rule_id == "uk_appropriate_place_claim_validated"


def test_fallback_path_still_binds_synthetic_comments_effect():
    """The effect-attribute fallback keeps synthetic-fixture effects binding.

    A synthetic effect carrying the prose in ``comments`` (no extracted source
    supplied) must still bind — the seam is additive, not a replacement.
    """
    claim = _savings_claim()

    class _SyntheticEffect:
        effect_id = claim.effect_id
        effect_type = "words omitted"
        source_text = ""
        raw_text = ""
        comments = _SAVINGS_EXTRACTED

    result = validate_savings_scoped_omission_claim(
        claim, effect=_SyntheticEffect(), extracted_source_text=None
    )
    assert result.validated is True
