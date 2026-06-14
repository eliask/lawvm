from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from lawvm.tools.divergence_heuristics import (
    blame_title_indicates_temporary_amendment,
    is_probable_repeal_stale_oracle,
    looks_like_bare_section_stub,
    oracle_text_reduces_to_bare_section_stub,
    parse_oracle_repeal_stub,
    replay_section_has_future_effective_version,
)


@dataclass
class _FakeVersion:
    effective: str


@dataclass
class _FakeTimeline:
    versions: tuple[_FakeVersion, ...]


@dataclass
class _FakeReplayResult:
    timelines: dict[str, _FakeTimeline] = field(default_factory=dict)


def _result(versions_by_address: dict[str, list[str]]) -> _FakeReplayResult:
    return _FakeReplayResult(
        timelines={
            address: _FakeTimeline(tuple(_FakeVersion(e) for e in effectives))
            for address, effectives in versions_by_address.items()
        }
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


_CUTOFF = datetime.date(2026, 1, 16)


def test_future_effective_fires_on_section_level_future_version() -> None:
    result = _result({"chapter:3/section:13": ["2023-01-01", "2026-11-20"]})
    assert replay_section_has_future_effective_version(
        result, "chapter:3/section:13", _CUTOFF
    )


def test_future_effective_fires_on_descendant_subsection_future_version() -> None:
    # Split commencement: the section heading commenced at the base date, but one
    # momentti's wording only enters force later — only the descendant subsection
    # timeline carries the future-effective version (mirrors 2020/566 §13/2026/35).
    result = _result(
        {
            "chapter:3/section:13": ["2023-01-01", "2026-01-16"],
            "chapter:3/section:13/subsection:1": ["2023-01-01", "2026-11-20"],
        }
    )
    assert replay_section_has_future_effective_version(
        result, "chapter:3/section:13", _CUTOFF
    )


def test_future_effective_quiet_when_all_versions_at_or_before_cutoff() -> None:
    # In-force change at the cutoff with no future-effective descendant: the
    # divergence is a real topology/text bug, not a future-version residue, so the
    # heuristic must NOT mask it.
    result = _result(
        {
            "chapter:3/section:11": ["2023-01-01", "2025-01-01"],
            "chapter:3/section:11/subsection:5": ["2025-01-01"],
        }
    )
    assert not replay_section_has_future_effective_version(
        result, "chapter:3/section:11", _CUTOFF
    )


def test_future_effective_does_not_borrow_unrelated_section_future_version() -> None:
    # A sibling section's future-effective version must not bleed into another
    # section's classification (prefix match is anchored to "<key>/").
    result = _result(
        {
            "chapter:3/section:13": ["2023-01-01", "2026-01-16"],
            "chapter:3/section:130": ["2026-11-20"],
        }
    )
    assert not replay_section_has_future_effective_version(
        result, "chapter:3/section:13", _CUTOFF
    )
