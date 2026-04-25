from __future__ import annotations

from types import SimpleNamespace

import lawvm.tools.ee_inspect_source as ee_inspect_source
from lawvm.core.semantic_types import StructuralAction
from lawvm.tools.ee_inspect_source import _build_ee_inspect_source_payload


class _Target:
    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


def test_build_ee_inspect_source_payload_resolves_target_title_from_base(monkeypatch) -> None:
    source_xml = b"<source/>"
    base_xml = b"<base/>"
    ops = [
        SimpleNamespace(
            sequence=1,
            action="replace",
            target=_Target("section:135"),
            payload=SimpleNamespace(text="new text", attrs={"old_text": "old text"}),
        )
    ]

    monkeypatch.setattr(
        "lawvm.estonia.fetch.open_rt_archive",
        lambda: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda akt_viide, archive=None: source_xml if akt_viide == "source" else base_xml,
    )
    monkeypatch.setattr(
        "lawvm.estonia.grafter.parse_ee_statute",
        lambda xml: SimpleNamespace(title="Avaliku teenistuse seadus"),
    )
    monkeypatch.setattr(
        "lawvm.estonia.grafter.parse_ee_amendment_ops",
        lambda xml, source_id, target_title: ops,
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_tekstiliik",
        lambda xml: "algtekst",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2012-01-01",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_grupi_id",
        lambda xml: "gid-1",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_amendment_refs",
        lambda xml: [SimpleNamespace(aktViide="123", passed="2011-01-27", joustumine="2012-01-01")],
    )
    monkeypatch.setattr(ee_inspect_source, "_extract_rt_title", lambda xml: "Halduskohtumenetluse seadustiku muutmise seadus")
    monkeypatch.setattr(
        ee_inspect_source,
        "_build_ee_source_sections",
        lambda xml, target_title="": [
            {
                "paragrahv_nr": "296",
                "paragrahv_title": "",
                "first_tavatekst": "Avaliku teenistuse seaduses tehakse järgmised muudatused",
                "intro_target_fragment": "Avaliku teenistuse seaduses",
                "html_block_count": 1,
                "matches_target_title": True,
            }
        ],
    )

    payload = _build_ee_inspect_source_payload(
        source_id="source",
        base_id="base",
    )

    assert payload["target_title"] == "Avaliku teenistuse seadus"
    assert payload["parsed_op_count"] == 1
    assert payload["parsed_ops"][0]["action"] == "replace"
    assert payload["parsed_ops"][0]["old_text"] == "old text"
    assert payload["section_summaries"][0]["matches_target_title"] is True


def test_build_ee_inspect_source_payload_truncates_ops(monkeypatch) -> None:
    source_xml = b"<source/>"
    ops = [
        SimpleNamespace(
            sequence=idx + 1,
            action="replace",
            target=_Target(f"section:{idx + 1}"),
            payload=SimpleNamespace(text=f"text {idx + 1}", attrs={}),
        )
        for idx in range(3)
    ]

    monkeypatch.setattr(
        "lawvm.estonia.fetch.open_rt_archive",
        lambda: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda akt_viide, archive=None: source_xml,
    )
    monkeypatch.setattr(
        "lawvm.estonia.grafter.parse_ee_amendment_ops",
        lambda xml, source_id, target_title: ops,
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_tekstiliik",
        lambda xml: "algtekst",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_grupi_id",
        lambda xml: None,
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_amendment_refs",
        lambda xml: [],
    )
    monkeypatch.setattr(ee_inspect_source, "_extract_rt_title", lambda xml: "Source title")
    monkeypatch.setattr(ee_inspect_source, "_build_ee_source_sections", lambda xml, target_title="": [])

    payload = _build_ee_inspect_source_payload(
        source_id="source",
        op_limit=2,
    )

    assert payload["parsed_op_count"] == 3
    assert len(payload["parsed_ops"]) == 2
    assert payload["truncated_ops"] == 1


def test_build_ee_inspect_source_payload_stringifies_structural_action(monkeypatch) -> None:
    source_xml = b"<source/>"
    ops = [
        SimpleNamespace(
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=_Target("section:23/subsection:2/item:3"),
            payload=SimpleNamespace(text="töötlev üksus", attrs={"old_text": "teabevaldaja"}),
        )
    ]

    monkeypatch.setattr(
        "lawvm.estonia.fetch.open_rt_archive",
        lambda: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda akt_viide, archive=None: source_xml,
    )
    monkeypatch.setattr(
        "lawvm.estonia.grafter.parse_ee_amendment_ops",
        lambda xml, source_id, target_title: ops,
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_tekstiliik",
        lambda xml: "algtekst",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_grupi_id",
        lambda xml: None,
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_amendment_refs",
        lambda xml: [],
    )
    monkeypatch.setattr(ee_inspect_source, "_extract_rt_title", lambda xml: "Source title")
    monkeypatch.setattr(ee_inspect_source, "_build_ee_source_sections", lambda xml, target_title="": [])

    payload = _build_ee_inspect_source_payload(source_id="source")

    assert payload["parsed_ops"][0]["action"] == "text_replace"
