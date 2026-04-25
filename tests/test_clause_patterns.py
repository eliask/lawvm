from lawvm.finland.johtolause.clause_patterns import (
    _classify_named_row_segment,
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)


def test_parse_named_table_row_mixed_clauses_parses_basic_kohdat_family() -> None:
    got = parse_named_table_row_mixed_clauses(
        (
            "kumotaan käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista annetun "
            "päätöksen 1 §:n Iitin ja Juvan käräjäoikeuksia koskevat kohdat sekä muutetaan "
            "Kouvolan ja Mikkelin käräjäoikeuksia koskevat kohdat seuraavasti:"
        )
    )

    assert len(got) == 1
    clause = got[0]
    assert clause.section == "1"
    assert clause.repeal_rows.targets == ("iitin", "juvan")
    assert clause.replace_rows.targets == ("kouvolan", "mikkelin")
    assert clause.repeal_rows.modifiers == ()
    assert clause.replace_rows.modifiers == ()


def test_parse_named_table_row_mixed_clauses_preserves_modifier_segments() -> None:
    got = parse_named_table_row_mixed_clauses(
        (
            "kumotaan päätöksen 1 §:n Iitin, sellaisina kuin ne ovat 1 päivänä tammikuuta 2000 "
            "annetulla päätöksellä, ja Juvan käräjäoikeuksia koskevat kohdat sekä muutetaan "
            "Kouvolan, mainitulla päätöksellä muutetun, ja Mikkelin käräjäoikeuksia koskevat "
            "kohdat seuraavasti:"
        )
    )

    assert len(got) == 1
    clause = got[0]
    assert clause.repeal_rows.targets == ("iitin", "juvan")
    assert clause.replace_rows.targets == ("kouvolan", "mikkelin")
    assert [m.kind for m in clause.repeal_rows.modifiers] == ["version_qualifier"]
    assert [m.kind for m in clause.replace_rows.modifiers] == ["source_citation"]


def test_parse_named_table_row_mixed_clauses_parses_osalta_family() -> None:
    got = parse_named_table_row_mixed_clauses(
        (
            "kumota käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista annetun päätöksen "
            "1 §:n Pirkanmaan käräjäoikeuden osalta ja muuttaa 1 §:n Tampereen käräjäoikeuden "
            "osalta seuraavasti:"
        )
    )

    assert len(got) == 1
    clause = got[0]
    assert clause.section == "1"
    assert clause.pattern_kind == "osalta"
    assert clause.repeal_rows.targets == ("pirkanmaan",)
    assert clause.replace_rows.targets == ("tampereen",)


def test_parse_named_table_row_mixed_clauses_parses_singular_kohta_family() -> None:
    got = parse_named_table_row_mixed_clauses(
        (
            "kumotaan päätöksen 1 §:n Haapajärven käräjäoikeutta koskeva kohta sekä muutetaan "
            "Ylivieskan käräjäoikeutta kohta seuraavasti:"
        )
    )

    assert len(got) == 1
    clause = got[0]
    assert clause.section == "1"
    assert clause.pattern_kind == "kohta"
    assert clause.repeal_rows.targets == ("haapajärven",)
    assert clause.replace_rows.targets == ("ylivieskan",)


def test_parse_named_table_row_single_clauses_parses_single_replace_clause() -> None:
    got = parse_named_table_row_single_clauses(
        "muutetaan päätöksen 1 §:n Iisalmen käräjäoikeutta koskevan kohdan seuraavasti:"
    )

    assert len(got) == 1
    clause = got[0]
    assert clause.section == "1"
    assert clause.action == "replace"
    assert clause.rows.targets == ("iisalmen",)


def test_parse_named_table_row_single_clauses_skips_mixed_clauses() -> None:
    got = parse_named_table_row_single_clauses(
        (
            "kumotaan päätöksen 1 §:n Alavuden ja Lapuan käräjäoikeuksia koskevat kohdat "
            "sekä muutetaan Kauhavan, Seinäjoen ja Äänekosken käräjäoikeuksia koskevat "
            "kohdat seuraavasti:"
        )
    )

    assert got == []


# --- _classify_named_row_segment: numeric row identity handling ---


class TestClassifyNamedRowSegmentNumeric:
    """Pro audit #9: tariff/code-style row identities must be accepted."""

    def test_tariff_code_pure_numeric_accepted(self) -> None:
        """Pure numeric identifiers like '1234' are valid row names."""
        target, modifier = _classify_named_row_segment("1234")
        assert target == "1234"
        assert modifier is None

    def test_tariff_code_dotted_numeric_accepted(self) -> None:
        """Dotted tariff codes like '90.12' are valid row names."""
        target, modifier = _classify_named_row_segment("90.12")
        # _norm_row_anchor_text strips '.', so "90.12" becomes "90 12"
        assert target == "90 12"
        assert modifier is None

    def test_tariff_code_alphanumeric_accepted(self) -> None:
        """Alphanumeric codes like 'H 01' are valid row names."""
        target, modifier = _classify_named_row_segment("H 01")
        assert target == "h 01"
        assert modifier is None

    def test_section_reference_rejected(self) -> None:
        """True section references like '5 §' must NOT be treated as row names."""
        target, modifier = _classify_named_row_segment("5 §")
        assert target is None
        assert modifier is not None
        assert modifier.kind == "numeric_reference"

    def test_section_reference_with_suffix_rejected(self) -> None:
        """Section references with suffixes like '5a §:n' are rejected."""
        target, modifier = _classify_named_row_segment("5a §:n")
        assert target is None
        assert modifier is not None
        assert modifier.kind == "numeric_reference"

    def test_plain_text_still_works(self) -> None:
        """Plain text names (no digits) still work as before."""
        target, modifier = _classify_named_row_segment("Kouvolan")
        assert target == "kouvolan"
        assert modifier is None

    def test_mixed_text_with_digits_accepted(self) -> None:
        """Text mixing letters and digits without § is a valid row name."""
        target, modifier = _classify_named_row_segment("ryhmä 3")
        assert target == "ryhmä 3"
        assert modifier is None
