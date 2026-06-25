"""Tests for the UK SI commencement-metadata audit (diagnostic, read-only)."""
from __future__ import annotations

from lawvm.uk_legislation.effect_temporal import (
    UKCommencementMetadata,
    UKCommencementMetadataStatus,
    _instrument_commencement_metadata,
)
from lawvm.uk_legislation.si_commencement_audit import (
    UK_SI_COMMENCEMENT_AUDIT_STATES,
    UK_SI_COMMENCEMENT_MADE_DATE_DEFAULT_CANDIDATE,
    UK_SI_COMMENCEMENT_MULTIPLE_DATES,
    UK_SI_COMMENCEMENT_NO_MADE_DATE,
    UK_SI_COMMENCEMENT_PROSPECTIVE_UNRESOLVED,
    UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE,
    UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR,
    UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE,
    UK_SI_COMMENCEMENT_TEXTUAL_ONLY,
    audit_affecting_si_commencement,
    classify_si_commencement_metadata,
    si_commencement_state_counts,
)

_UKM = "http://www.legislation.gov.uk/namespaces/metadata"


def _si_xml(*, cif_dates: tuple[str, ...] = (), made_dates: tuple[str, ...] = (), cif_textual: bool = False) -> bytes:
    cif = ""
    if cif_dates:
        inner = "".join(f'<ukm:DateTime Date="{d}"/>' for d in cif_dates)
        cif = f"<ukm:ComingIntoForce>{inner}</ukm:ComingIntoForce>"
    elif cif_textual:
        cif = "<ukm:ComingIntoForce><ukm:Text>see article 1</ukm:Text></ukm:ComingIntoForce>"
    made = "".join(f'<ukm:Made Date="{d}"/>' for d in made_dates)
    return (
        f'<Legislation xmlns:ukm="{_UKM}">'
        f"<ukm:Metadata>{made}{cif}</ukm:Metadata>"
        f"</Legislation>"
    ).encode("utf-8")


# ── State taxonomy on synthetic metadata carriers ────────────────────────────


def test_single_date_classifies_resolved_in_force() -> None:
    metadata = UKCommencementMetadata(
        effective_date="2020-01-01",
        source_locator="loc",
        status=UKCommencementMetadataStatus.SINGLE_DATE,
        dates=("2020-01-01",),
        made_dates=("2019-12-01",),
    )
    state = classify_si_commencement_metadata("uksi/2019/1", metadata)
    assert state.state == UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE
    assert state.reason_tags == ("single_commencement_date",)


def test_multiple_dates_classifies_multiple() -> None:
    metadata = UKCommencementMetadata(
        effective_date="",
        source_locator="loc",
        status=UKCommencementMetadataStatus.MULTIPLE_OR_TEXTUAL,
        dates=("2020-01-01", "2020-06-01"),
        made_dates=("2019-12-01",),
    )
    state = classify_si_commencement_metadata("uksi/2019/2", metadata)
    assert state.state == UK_SI_COMMENCEMENT_MULTIPLE_DATES
    assert state.commencement_dates == ("2020-01-01", "2020-06-01")


def test_made_date_default_candidate_is_unproved() -> None:
    metadata = UKCommencementMetadata(
        effective_date="",
        source_locator="loc",
        status=UKCommencementMetadataStatus.DEFAULT_COMMENCEMENT_MADE_DATE_CANDIDATE,
        dates=(),
        made_dates=("2019-12-01",),
    )
    state = classify_si_commencement_metadata("uksi/2019/3", metadata)
    assert state.state == UK_SI_COMMENCEMENT_MADE_DATE_DEFAULT_CANDIDATE
    assert "made_date_default_candidate_but_unproved" in state.reason_tags


def test_textual_only_when_cif_present_without_date() -> None:
    metadata = UKCommencementMetadata(
        effective_date="",
        source_locator="loc",
        status=UKCommencementMetadataStatus.TEXTUAL_OR_MISSING_DATE,
        dates=(),
        made_dates=(),
    )
    state = classify_si_commencement_metadata(
        "uksi/2019/4",
        metadata,
        coming_into_force_element_present=True,
    )
    assert state.state == UK_SI_COMMENCEMENT_TEXTUAL_ONLY
    assert "coming_into_force_element_present" in state.reason_tags
    assert "no_made_date" in state.reason_tags


def test_no_made_date_when_nothing_present() -> None:
    metadata = UKCommencementMetadata(
        effective_date="",
        source_locator="loc",
        status=UKCommencementMetadataStatus.TEXTUAL_OR_MISSING_DATE,
        dates=(),
        made_dates=(),
    )
    state = classify_si_commencement_metadata("uksi/2019/5", metadata)
    assert state.state == UK_SI_COMMENCEMENT_NO_MADE_DATE
    assert "no_made_date" in state.reason_tags


def test_prospective_unresolved_overrides_undated_states() -> None:
    metadata = UKCommencementMetadata(
        effective_date="",
        source_locator="loc",
        status=UKCommencementMetadataStatus.DEFAULT_COMMENCEMENT_MADE_DATE_CANDIDATE,
        dates=(),
        made_dates=("2019-12-01",),
    )
    state = classify_si_commencement_metadata(
        "uksi/2019/6",
        metadata,
        prospective_unresolved=True,
    )
    assert state.state == UK_SI_COMMENCEMENT_PROSPECTIVE_UNRESOLVED
    assert "prospective_affecting_provision_unresolved" in state.reason_tags


def test_single_date_beats_prospective_unresolved() -> None:
    # A proven commencement date must NOT be downgraded to prospective_unresolved.
    metadata = UKCommencementMetadata(
        effective_date="2020-01-01",
        source_locator="loc",
        status=UKCommencementMetadataStatus.SINGLE_DATE,
        dates=("2020-01-01",),
    )
    state = classify_si_commencement_metadata(
        "uksi/2019/7",
        metadata,
        prospective_unresolved=True,
    )
    assert state.state == UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE


def test_source_unavailable_and_parse_error_states() -> None:
    unavailable = classify_si_commencement_metadata(
        "uksi/2019/8",
        UKCommencementMetadata(
            effective_date="",
            source_locator="",
            status=UKCommencementMetadataStatus.SOURCE_XML_UNAVAILABLE,
        ),
    )
    assert unavailable.state == UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE
    parse_error = classify_si_commencement_metadata(
        "uksi/2019/9",
        UKCommencementMetadata(
            effective_date="",
            source_locator="loc",
            status=UKCommencementMetadataStatus.SOURCE_XML_PARSE_ERROR,
            parse_error="boom",
        ),
    )
    assert parse_error.state == UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR
    assert parse_error.parse_error == "boom"


# ── Totality: every metadata status maps to a known state ────────────────────


def test_classifier_is_total_over_metadata_statuses() -> None:
    # Iterate the whole enum so a newly added member that the classifier does
    # not map fails here (and, structurally, at the match/assert_never in
    # classify_si_commencement_metadata).
    for status in UKCommencementMetadataStatus:
        dates = (
            ("2020-01-01", "2020-06-01")
            if status is UKCommencementMetadataStatus.MULTIPLE_OR_TEXTUAL
            else (("2020-01-01",) if status is UKCommencementMetadataStatus.SINGLE_DATE else ())
        )
        made = (
            ("2019-12-01",)
            if status is UKCommencementMetadataStatus.DEFAULT_COMMENCEMENT_MADE_DATE_CANDIDATE
            else ()
        )
        metadata = UKCommencementMetadata(
            effective_date=(
                "2020-01-01" if status is UKCommencementMetadataStatus.SINGLE_DATE else ""
            ),
            source_locator="loc",
            status=status,
            dates=dates,
            made_dates=made,
        )
        state = classify_si_commencement_metadata("uksi/2000/1", metadata)
        assert state.state in UK_SI_COMMENCEMENT_AUDIT_STATES


# ── End-to-end from XML bytes (reusing the replay-path extractor) ────────────


def test_audit_from_xml_matches_replay_extractor() -> None:
    xml = _si_xml(cif_dates=("2021-03-01",), made_dates=("2021-02-01",))
    # The audit reuses the replay-path extractor verbatim.
    metadata = _instrument_commencement_metadata(xml, source_locator="loc")
    assert metadata.status == "single_date"
    state = audit_affecting_si_commencement("uksi/2021/100", xml, source_locator="loc")
    assert state.state == UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE
    assert state.commencement_dates == ("2021-03-01",)


def test_audit_from_xml_textual_only() -> None:
    xml = _si_xml(cif_textual=True, made_dates=("2021-02-01", "2021-02-02"))
    state = audit_affecting_si_commencement("uksi/2021/101", xml)
    assert state.state == UK_SI_COMMENCEMENT_TEXTUAL_ONLY
    assert state.coming_into_force_element_present is True


def test_audit_from_empty_bytes_is_source_unavailable() -> None:
    state = audit_affecting_si_commencement("uksi/2021/102", b"")
    assert state.state == UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE


def test_audit_from_unparseable_bytes_is_parse_error() -> None:
    state = audit_affecting_si_commencement("uksi/2021/103", b"<not-closed")
    assert state.state == UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR


# ── Determinism ──────────────────────────────────────────────────────────────


def test_to_dict_is_stable_and_repeatable() -> None:
    xml = _si_xml(cif_dates=("2021-03-01",), made_dates=("2021-02-01",))
    first = audit_affecting_si_commencement("uksi/2021/100", xml, source_locator="loc").to_dict()
    second = audit_affecting_si_commencement("uksi/2021/100", xml, source_locator="loc").to_dict()
    assert first == second
    assert list(first) == list(second)
    assert first["owner_phase"] == "effect_metadata_frontend"


def test_state_counts_are_sorted() -> None:
    states = (
        audit_affecting_si_commencement("uksi/2021/100", _si_xml(cif_dates=("2021-03-01",))),
        audit_affecting_si_commencement("uksi/2021/101", b""),
        audit_affecting_si_commencement("uksi/2021/102", _si_xml(cif_dates=("2022-01-01",))),
    )
    counts = si_commencement_state_counts(states)
    assert list(counts) == sorted(counts)
    assert counts[UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE] == 2
    assert counts[UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE] == 1
