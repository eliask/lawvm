"""US federal temporary-provision sunset detection (F2).

No network. Two layers:

1. Synthetic Title 99 sections with crafted note blocks exercise every honest
   outcome of :mod:`lawvm.us_federal.sunset`:
   - a temporary provision with a sunset note + a prior-permanent edition match
     -> ``sunset_reversion`` (channels a + b);
   - a sunset note whose reversion target is only a quoted prior text (channel b
     alone) -> ``sunset_reversion``;
   - a genuinely un-lowered amendment (a changed section with NO reversion
     evidence) -> NO reversion claim (stays missing_source);
   - an in-window effective date with no reversion language -> typed finding, not
     a reversion (do not over-claim sunsets).

2. The real Title 11 / 2023->2024 window from the canonical archive (skipped when
   absent): §109 and §1182 reclassify from missing_source to sunset_reversion
   with the SBRA June 21, 2024 sunset cited.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.us_federal.source_tree import UscSection, UscSectionNote, usc_section_address
from lawvm.us_federal.sunset import (
    DISPOSITION_SUNSET_REVERSION,
    US_SUNSET_AMBIGUOUS_RULE_ID,
    US_SUNSET_REVERSION_RULE_ID,
    classify_sunset_reversion,
)


def _section(
    section: str,
    statutory_text: str,
    notes: tuple[UscSectionNote, ...] = (),
) -> UscSection:
    return UscSection(
        title=99,
        section=section,
        heading="Synthetic",
        address=usc_section_address(99, section),
        statutory_text=statutory_text,
        source_credit_raw="(Pub. L. 99-1.)",
        repealed=False,
        paragraphs=(),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Synthetic: channels a + b -> sunset_reversion
# ---------------------------------------------------------------------------


def test_temporary_provision_with_sunset_note_and_prior_edition_is_sunset_reversion() -> None:
    # before (temporary, expanded) text differs from after (reverted) text, the
    # after-text matches a prior permanent edition, and a sunset note dates the
    # expiry inside the window.
    permanent_text = "The applicable debt limit is $250,000."
    temporary_text = "The applicable debt limit is $2,750,000."
    after = _section(
        "10",
        permanent_text,
        notes=(
            UscSectionNote(
                head="Effective Date of 2022 Amendment",
                bodies=(
                    "Pub. L. 117-151, the amendment made by section 2(i)(1)(A) is "
                    "effective on the date that is 2 years after June 21, 2022.",
                ),
            ),
            UscSectionNote(
                head="Amendments",
                bodies=(
                    "2022 — Pub. L. 117-151 amended this section to read as it read "
                    "on the day before June 21, 2022.",
                ),
            ),
        ),
    )
    result = classify_sunset_reversion(
        title=99,
        section="10",
        before_year="2023",
        after_year="2024",
        before_text=temporary_text,
        after_text=permanent_text,
        after_section=after,
        prior_edition_texts={"2018": permanent_text},
    )
    assert result.is_reversion
    cls = result.classification
    assert cls is not None
    assert cls.rule_id == US_SUNSET_REVERSION_RULE_ID
    assert cls.disposition == DISPOSITION_SUNSET_REVERSION
    w = cls.witness
    assert w.sunset_date == "2024-06-21"
    assert w.reverts_to_edition_year == "2018"
    # The temporal model is built from the shared core ProvisionVersion types.
    assert w.temporary_version is not None
    assert w.temporary_version.variant_kind == "temporary"
    assert w.temporary_version.expires == "2024-06-21"
    assert w.permanent_version is not None
    assert w.permanent_version.variant_kind == "permanent"


def test_sunset_reversion_via_quoted_prior_text_without_prior_edition() -> None:
    # Channel (b) alone: no earlier edition matches, but the note quotes the prior
    # text the section reverts to AND dates the expiry inside the window.
    after = _section(
        "20",
        'The term "debtor" means a small business debtor.',
        notes=(
            UscSectionNote(
                head="Amendments",
                bodies=(
                    "2022 — Pub. L. 117-151 amended par. (1) generally. Prior to "
                    'amendment, text read as follows: "The term \'debtor\' means a '
                    'small business debtor."',
                ),
            ),
            UscSectionNote(
                head="Effective Date of 2022 Amendment",
                bodies=(
                    "Pub. L. 117-151, the amendment made by section 2(i)(1)(B) is "
                    "effective on the date that is 2 years after June 21, 2022.",
                ),
            ),
        ),
    )
    result = classify_sunset_reversion(
        title=99,
        section="20",
        before_year="2023",
        after_year="2024",
        before_text="The term debtor means a person engaged in commercial activities.",
        after_text='The term "debtor" means a small business debtor.',
        after_section=after,
        prior_edition_texts={},
    )
    assert result.is_reversion
    assert result.classification is not None
    assert result.classification.witness.quoted_prior_text_matches is True
    assert result.classification.witness.sunset_date == "2024-06-21"


# ---------------------------------------------------------------------------
# Synthetic: do NOT over-claim sunsets
# ---------------------------------------------------------------------------


def test_un_lowered_amendment_without_reversion_evidence_is_not_a_sunset() -> None:
    # A genuine textual amendment: the section changed but there is no reversion
    # language, no in-window sunset date, and no earlier edition matches. The
    # detector makes NO claim (the dry-run keeps missing_source).
    after = _section(
        "30",
        "The new substantive rule applies.",
        notes=(
            UscSectionNote(
                head="Amendments",
                bodies=("2024 — Pub. L. 118-99 substituted 'new' for 'old'.",),
            ),
        ),
    )
    result = classify_sunset_reversion(
        title=99,
        section="30",
        before_year="2023",
        after_year="2024",
        before_text="The old substantive rule applies.",
        after_text="The new substantive rule applies.",
        after_section=after,
        prior_edition_texts={"2018": "Something completely different."},
    )
    assert not result.is_reversion
    assert result.classification is None
    assert result.finding is None


def test_in_window_effective_date_without_reversion_language_is_a_finding_not_a_sunset() -> None:
    # An ordinary amendment with an in-window effective date but NO reversion
    # semantics and no prior-permanent match: emit a typed finding (visible, self-
    # evidencing), never a reversion claim.
    after = _section(
        "40",
        "The amended rule applies broadly.",
        notes=(
            UscSectionNote(
                head="Effective Date of 2024 Amendment",
                bodies=("Amendment by Pub. L. 118-99 effective March 1, 2024.",),
            ),
        ),
    )
    result = classify_sunset_reversion(
        title=99,
        section="40",
        before_year="2023",
        after_year="2024",
        before_text="The prior rule applies narrowly.",
        after_text="The amended rule applies broadly.",
        after_section=after,
        prior_edition_texts={},
    )
    assert not result.is_reversion
    assert result.classification is None
    assert result.finding is not None
    assert result.finding.rule_id == US_SUNSET_AMBIGUOUS_RULE_ID
    # Self-evidencing: the offending note text is carried.
    assert "effective March 1, 2024" in result.finding.note_text


def test_no_text_change_yields_no_temporal_claim() -> None:
    same = _section(
        "50",
        "Unchanged text.",
        notes=(
            UscSectionNote(
                head="Effective Date of 2022 Amendment",
                bodies=("effective on the date that is 2 years after June 21, 2022.",),
            ),
        ),
    )
    result = classify_sunset_reversion(
        title=99,
        section="50",
        before_year="2023",
        after_year="2024",
        before_text="Unchanged text.",
        after_text="Unchanged text.",
        after_section=same,
        prior_edition_texts={},
    )
    assert not result.is_reversion
    assert result.classification is None
    assert result.finding is None


# ---------------------------------------------------------------------------
# Real Title 11 / 2023->2024 window (archive-gated, no network)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_title11_109_and_1182_reclassify_as_sunset_reversion() -> None:
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive
    from lawvm.us_federal.sources import open_us_federal_farchive, plaw_locator

    archive = open_us_federal_farchive(readonly=True)
    try:
        report = build_us_dry_run_from_archive(
            archive,
            title=11,
            before_year=2023,
            after_year=2024,
            plaw_locators={"PL 118-42": plaw_locator(118, 42)},
            enacted="2024-03-09",
            # Prior editions establish the pre-sunset permanent text §109 reverts to.
            prior_edition_years=(2018, 2022),
        )
    finally:
        archive.close()

    # §109 and §1182 move from missing_source to sunset_reversion (F2).
    ns = report.north_star()
    assert set(ns["sunset_reversion_sections"]) == {"11:109", "11:1182"}
    assert set(ns["missing_source_sections"]) == set()

    rev = {c.section: c for c in report.sunset_reversions}
    assert set(rev) == {"109", "1182"}

    # §109 reverts to the 2018 permanent edition exactly (channel a) AND carries
    # the June 21, 2024 SBRA sunset note (channel b).
    w109 = rev["109"].witness
    assert rev["109"].disposition == DISPOSITION_SUNSET_REVERSION
    assert w109.sunset_date == "2024-06-21"
    assert w109.reverts_to_edition_year == "2018"
    assert w109.temporary_version is not None
    assert w109.temporary_version.variant_kind == "temporary"
    assert w109.permanent_version is not None
    assert w109.permanent_version.variant_kind == "permanent"

    # §1182 reverts to "means a small business debtor"; no prior full-section
    # edition matches (it was added by SBRA in 2019), so the evidence is the
    # quoted prior text + the same June 21, 2024 sunset note (channel b).
    w1182 = rev["1182"].witness
    assert rev["1182"].disposition == DISPOSITION_SUNSET_REVERSION
    assert w1182.sunset_date == "2024-06-21"
    assert w1182.quoted_prior_text_matches is True

    # Never repaired to the oracle; replay stays blocked.
    assert report.replay_authorized is False
