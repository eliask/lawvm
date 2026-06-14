from lawvm.tools.divergence_heuristics import (
    blame_title_indicates_temporary_amendment,
    is_probable_repeal_stale_oracle,
    looks_like_bare_section_stub,
    oracle_text_reduces_to_bare_section_stub,
    parse_oracle_repeal_stub,
)


def test_looks_like_bare_section_stub_accepts_heading_only_section() -> None:
    assert looks_like_bare_section_stub("28 §")


def test_looks_like_bare_section_stub_rejects_substantive_section_text() -> None:
    assert not looks_like_bare_section_stub("28 § Tulliviranomaisella on oikeus saada tietoja.")


def test_oracle_text_reduces_to_bare_section_stub_strips_temporary_residue() -> None:
    assert oracle_text_reduces_to_bare_section_stub(
        "21 b § oli väliaikaisesti voimassa 24.11.2021–30.1.2022 L 984/2021."
    )


def test_is_probable_repeal_stale_oracle_accepts_stubbed_post_repeal_shape() -> None:
    replay = "28 §"
    oracle = (
        "28 § Tulliviranomaisella on oikeus saada tietoja. "
        "Tulliviranomaisella on lisäksi oikeus saada yhteystiedot."
    )
    pre = (
        "28 § Tulliviranomaisella on oikeus saada tietoja teknisen käyttöyhteyden avulla. "
        "Tulliviranomaisella on lisäksi oikeus saada yhteystiedot."
    )
    assert is_probable_repeal_stale_oracle(replay, oracle, pre)


def test_is_probable_repeal_stale_oracle_rejects_non_stubbed_replay_tail() -> None:
    replay = "28 § Uusi sisältö korvaa aiemman tekstin kokonaan."
    oracle = "28 § Vanha sisältö jää tähän."
    pre = "28 § Vanha sisältö jää tähän."
    assert not is_probable_repeal_stale_oracle(replay, oracle, pre)


def test_blame_title_indicates_temporary_amendment_accepts_valiaikainen() -> None:
    assert blame_title_indicates_temporary_amendment(
        "Laki saatavien perinnästä annetun lain väliaikaisesta muuttamisesta"
    )


def test_blame_title_indicates_temporary_amendment_rejects_normal_title() -> None:
    assert not blame_title_indicates_temporary_amendment(
        "Laki saatavien perinnästä annetun lain muuttamisesta"
    )


def test_parse_oracle_repeal_stub_extracts_repealing_statute_id() -> None:
    # Duplicated num is how etree text-serializes <num>47 §</num> + body label.
    assert (
        parse_oracle_repeal_stub("47 § 47 § on kumottu L:lla 16.12.1994/1218.")
        == "1994/1218"
    )


def test_parse_oracle_repeal_stub_handles_lettered_section_and_whitespace() -> None:
    assert (
        parse_oracle_repeal_stub("68 a §\n  68 a § on kumottu L:lla \n 13.11.2009/886.")
        == "2009/886"
    )


def test_parse_oracle_repeal_stub_normalises_zero_padded_number() -> None:
    assert (
        parse_oracle_repeal_stub("46 § 46 § on kumottu L:lla 11.12.2002/0071.")
        == "2002/71"
    )


def test_parse_oracle_repeal_stub_rejects_substantive_section() -> None:
    assert (
        parse_oracle_repeal_stub(
            "151 § Palautushakemus on tehtävä Verohallinnon vahvistamalla lomakkeella."
        )
        is None
    )


def test_parse_oracle_repeal_stub_rejects_partial_momentti_repeal() -> None:
    # Only one momentti repealed, section still carries other live text.
    assert (
        parse_oracle_repeal_stub(
            "1 § Veroa suoritetaan. 3 momentti on kumottu L:lla 9.12.2016/1064. Lisää tekstiä."
        )
        is None
    )
