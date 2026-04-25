from lawvm.tools.divergence_heuristics import (
    blame_title_indicates_temporary_amendment,
    is_probable_repeal_stale_oracle,
    looks_like_bare_section_stub,
    oracle_text_reduces_to_bare_section_stub,
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
