from __future__ import annotations

from lawvm.core.payload_realization import (
    PayloadRealizationUnit,
    _NON_WORD_RE,
    _approx_tokens_realized_in_window,
    _display_text,
    _normalized_text,
    audit_payload_realization,
    drop_materialized_payload_realization_false_positives,
    payload_realization_gap_findings,
)
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE


def test_audit_payload_realization_reports_missing_substantive_chunk() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text that should land.",),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="The folded state still contains unrelated old text.",
    )

    assert len(gaps) == 1
    assert gaps[0].unit_id == "section_1"


def test_payload_normalized_text_matches_two_step_reference() -> None:
    cases = (
        "Substantive payload text, that should land.",
        "  two\n\nlines\tand punctuation -- here  ",
        "§ 1 momentin 2 kohdassa säädetään",
        ",,,",
        "A_B remains a word token",
    )

    for text in cases:
        reference = _NON_WORD_RE.sub(" ", _display_text(text).casefold()).strip()
        assert _normalized_text(text) == reference


def test_audit_payload_realization_accepts_normalized_text_match() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text, that should land.",),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="Substantive payload text that should land",
    )

    assert gaps == ()


def test_audit_payload_realization_exact_chunk_fast_path_accepts_all_chunks() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=(
                "First substantive payload text lands.",
                "Second substantive payload text lands.",
            ),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text=(
            "Before. First substantive payload text lands. Between. "
            "Second substantive payload text lands. After."
        ),
    )

    assert gaps == ()


def test_audit_payload_realization_exact_chunk_fast_path_falls_back_on_miss() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=(
                "Present substantive payload text lands.",
                "Absent substantive payload text should report.",
            ),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="Present substantive payload text lands.",
    )

    assert len(gaps) == 1
    assert gaps[0].chunk_text == "Absent substantive payload text should report."


def test_audit_payload_realization_accepts_bounded_ordered_interleaving() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="item_7",
            unit_kind="item",
            observed_label="7",
            text_chunks=(
                "kauneudenhoitoalan ammattikorkeakoulututkinto, "
                "johon liitetään koulutusohjelman mukaan tutkintonimike estenomi (AMK); sekä",
            ),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text=(
            "kauneudenhoitoalan ammattikorkeakoulututkinto, johon liitetään "
            "koulutusohjelman mukaan tutkintonimike estenomi (AMK), "
            "Bachelor of Beauty and Cosmetics; sekä"
        ),
    )

    assert gaps == ()


def test_audit_payload_realization_accepts_local_token_substitutions() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_2",
            unit_kind="section",
            observed_label="2",
            text_chunks=(
                "Liikenne- ja viestintävirasto avaa 1 §:n nojalla saamansa tiedot "
                "sekä niiden perusteella tekemänsä tilastot ja tutkimukset avoimen "
                "rajapinnan kautta koneluettavassa muodossa vapaasti käytettäväksi.",
            ),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text=(
            "Liikenne- ja viestintävirasto avaa 233 §:n nojalla saamansa tiedot "
            "sekä niiden perusteella tekemänsä tilastot ja tutkimukset avoimen "
            "rajapinnan kautta koneluettavassa muodossa vapaasti käytettäväksi."
        ),
    )

    assert gaps == ()


def test_audit_payload_realization_rejects_low_coverage_local_overlap() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_2",
            unit_kind="section",
            observed_label="2",
            text_chunks=(
                "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda",
            ),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="alpha beta gamma delta unrelated replacement text in one local window",
    )

    assert len(gaps) == 1


def test_audit_payload_realization_rejects_ordered_tokens_scattered_across_statute() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="item_7",
            unit_kind="item",
            observed_label="7",
            text_chunks=("alpha beta gamma delta epsilon",),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="alpha " + " filler" * 100 + " beta gamma delta epsilon",
    )

    assert len(gaps) == 1


def test_approx_token_window_matches_reference_lcs_with_repeated_tokens() -> None:
    cases = (
        (
            tuple("alpha alpha beta gamma delta epsilon zeta eta theta iota".split()),
            tuple("alpha filler alpha beta gamma delta epsilon zeta eta theta".split()),
        ),
        (
            tuple("alpha beta gamma delta epsilon zeta eta theta iota kappa".split()),
            tuple("alpha beta beta gamma unrelated unrelated unrelated".split()),
        ),
        (
            tuple("one two two three four five six seven eight nine ten".split()),
            tuple("one two three two four five six seven eight changed ten".split()),
        ),
    )

    for chunk_tokens, window_tokens in cases:
        assert _approx_tokens_realized_in_window(
            chunk_tokens,
            window_tokens,
        ) is _reference_approx_tokens_realized(chunk_tokens, window_tokens)


def _reference_approx_tokens_realized(
    chunk_tokens: tuple[str, ...],
    window_tokens: tuple[str, ...],
) -> bool:
    if len(chunk_tokens) < 8:
        return False
    previous = [0] * (len(window_tokens) + 1)
    for chunk_token in chunk_tokens:
        current = [0]
        for col, window_token in enumerate(window_tokens, start=1):
            if chunk_token == window_token:
                current.append(previous[col - 1] + 1)
            else:
                current.append(max(previous[col], current[-1]))
        previous = current
    return previous[-1] / len(chunk_tokens) >= 0.80


def test_payload_realization_gap_findings_use_shared_coverage_code() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text that should land.",),
        ),
    )
    gaps = audit_payload_realization(units=units, after_text="unrelated old text")

    findings = payload_realization_gap_findings(gaps, source_ref="2000/1")

    assert [finding.kind for finding in findings] == ["COVERAGE.PAYLOAD_REALIZATION_GAP"]
    assert findings[0].source_statute == "2000/1"


def test_materialized_payload_filter_drops_realized_gap_only() -> None:
    realized = Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_GAP",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute="2000/1",
        detail={"chunk_excerpt": "Owned materialized text."},
    )
    missing = Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_GAP",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute="2000/1",
        detail={"chunk_excerpt": "Still absent text."},
    )

    filtered = drop_materialized_payload_realization_false_positives(
        (realized, missing),
        materialized_text="The product contains owned materialized text.",
    )

    assert filtered == (missing,)


def test_materialized_payload_filter_accepts_bounded_ordered_interleaving() -> None:
    realized = Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_GAP",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute="2000/1",
        detail={
            "chunk_excerpt": (
                "merenkulun ammattikorkeakoulututkinto, johon liitetaan koulutusohjelman "
                "mukaan tutkintonimike merikapteeni (AMK), Bachelor of Marine Technology;"
            )
        },
    )

    filtered = drop_materialized_payload_realization_false_positives(
        (realized,),
        materialized_text=(
            "merenkulun ammattikorkeakoulututkinto, johon liitetaan koulutusohjelman "
            "mukaan tutkintonimike merikapteeni (AMK), merikapteeni (ylempi AMK), "
            "Bachelor of Marine Technology;"
        ),
    )

    assert filtered == ()
