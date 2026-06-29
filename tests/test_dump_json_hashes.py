"""Tests for `lawvm dump --json` / `--hashes` machine-readable read surface."""
from __future__ import annotations

from argparse import Namespace
import json
from types import SimpleNamespace

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import dump
from lawvm.tools.provision_state import DUMP_SCHEMA, build_statute_dump_response


def _empty_master() -> SimpleNamespace:
    """Return a SimpleNamespace master with the minimal attrs dump.py now reads.

    The corrigenda-session dump.py change accesses ``master.ir`` and
    ``getattr(getattr(master, 'ctx', None), 'attachment_supplements', ())``
    on the apply path. Without these attrs the dump crashes before the
    JSON/hashes code path is reached.
    """
    return SimpleNamespace(
        ir=IRNode(kind=IRNodeKind.BODY, text="", children=()),
        title="",
        ctx=SimpleNamespace(attachment_supplements=()),
    )


def _section(text: str, *, label: str, heading: str | None = None) -> IRNode:
    children: list[IRNode] = []
    if heading is not None:
        children.append(IRNode(kind=IRNodeKind.HEADING, text=heading))
    children.append(IRNode(kind=IRNodeKind.CONTENT, text=text))
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _two_section_timelines() -> dict[LegalAddress, ProvisionTimeline]:
    addr1 = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content1 = _section("First provision duty.", label="1", heading="Soveltamisala")
    v1 = ProvisionVersion(
        effective="2020-01-01",
        enacted="2019-12-01",
        content=content1,
        source=OperationSource(
            statute_id="2019/1",
            title="Amending Act One",
            enacted="2019-12-01",
            effective="2020-01-01",
            raw_text="Section 1 is replaced.",
        ),
        content_hash=irnode_content_hash(content1),
    )
    addr2 = LegalAddress(path=(("chapter", "1"), ("section", "2")))
    content2 = _section("Second provision duty.", label="2")
    v2 = ProvisionVersion(
        effective="2021-06-01",
        enacted="2021-05-01",
        content=content2,
        source=OperationSource(
            statute_id="2021/55",
            title="Amending Act Two",
            enacted="2021-05-01",
            effective="2021-06-01",
        ),
        content_hash=irnode_content_hash(content2),
    )
    return {
        addr1: ProvisionTimeline(address=addr1, versions=[v1]),
        addr2: ProvisionTimeline(address=addr2, versions=[v2]),
    }


def test_dump_response_schema_and_section_shape() -> None:
    payload = build_statute_dump_response(
        timelines=_two_section_timelines(),
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2022-01-01",
        title="Test Statute",
    )

    assert payload["schema"] == DUMP_SCHEMA == "lawvm.dump.v1"
    assert payload["statute_id"] == "2000/1"
    assert payload["title"] == "Test Statute"
    assert payload["as_of"] == "2022-01-01"
    assert payload["section_count"] == 2
    assert payload["engine"]["producer"] == "lawvm"
    # engine fields are present but excluded from any per-section hash
    assert {"build_id", "git_commit", "git_dirty", "repository"} <= set(payload["engine"])

    sec1, sec2 = payload["sections"]
    assert sec1["address"]["text"] == "chapter:1/section:1"
    assert sec1["label"] == "1"
    assert sec1["heading"] == "Soveltamisala"
    assert sec1["text"] == "Soveltamisala First provision duty."
    assert sec1["version"]["effective"] == "2020-01-01"
    assert sec1["version"]["enacted"] == "2019-12-01"
    assert sec2["heading"] is None


def test_dump_response_content_hash_matches_independent_recompute() -> None:
    timelines = _two_section_timelines()
    payload = build_statute_dump_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2022-01-01",
    )

    for address, timeline in timelines.items():
        version = timeline.versions[0]
        expected = irnode_content_hash(version.content)
        section = next(
            s for s in payload["sections"] if s["address"]["text"] == str(address)
        )
        # independent recompute in the test must match the emitted hash
        assert section["content_hash"] == expected
        assert len(section["content_hash"]) == 64


def test_dump_response_source_attribution_is_amending_act() -> None:
    payload = build_statute_dump_response(
        timelines=_two_section_timelines(),
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2022-01-01",
    )
    by_addr = {s["address"]["text"]: s for s in payload["sections"]}
    assert by_addr["chapter:1/section:1"]["source"]["statute_id"] == "2019/1"
    assert by_addr["chapter:1/section:2"]["source"]["statute_id"] == "2021/55"


def test_dump_response_excludes_not_yet_effective_section_at_as_of() -> None:
    # as_of before section 2's effective date: only section 1 governs.
    payload = build_statute_dump_response(
        timelines=_two_section_timelines(),
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2020-06-01",
    )
    addrs = {s["address"]["text"] for s in payload["sections"]}
    assert addrs == {"chapter:1/section:1"}
    assert payload["section_count"] == 1


def test_dump_response_excludes_repeal_placeholder_sections() -> None:
    live_timelines = _two_section_timelines()
    placeholder_addr = LegalAddress(path=(("chapter", "1"), ("section", "3")))
    placeholder = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        attrs={"lawvm_repeal_placeholder": "1"},
    )
    live_timelines[placeholder_addr] = ProvisionTimeline(
        address=placeholder_addr,
        versions=[
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2020-01-01",
                content=placeholder,
                source=OperationSource(
                    statute_id="2020/10",
                    title="Repeal Act",
                    enacted="2020-01-01",
                    effective="2020-01-01",
                    raw_text="3 § kumotaan.",
                ),
            )
        ],
    )

    payload = build_statute_dump_response(
        timelines=live_timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2022-01-01",
    )

    addrs = {section["address"]["text"] for section in payload["sections"]}
    assert addrs == {"chapter:1/section:1", "chapter:1/section:2"}
    assert payload["section_count"] == 2


def test_dump_response_address_filter_selects_one_section() -> None:
    payload = build_statute_dump_response(
        timelines=_two_section_timelines(),
        statute_id="2000/1",
        jurisdiction="fi",
        as_of="2022-01-01",
        address_filter="section:2",
    )
    assert payload["section_count"] == 1
    assert payload["sections"][0]["address"]["text"] == "chapter:1/section:2"
    assert payload["query"]["address_filter"] == "section:2"


def test_dump_main_json_flag_emits_lawvm_dump_v1(monkeypatch, capsys) -> None:
    timelines = _two_section_timelines()

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        master = _empty_master()
        master.timelines = timelines
        master.title = "Replayed Title"
        return master

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="2000/1",
            after=None,
            source=None,
            address=None,
            before="",
            jurisdiction="fi",
            db=None,
            json=True,
            hashes=False,
            as_of="2022-01-01",
        )
    )

    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["schema"] == "lawvm.dump.v1"
    assert doc["statute_id"] == "2000/1"
    assert doc["title"] == "Replayed Title"
    assert doc["as_of"] == "2022-01-01"
    assert doc["flags"]["after"] == "apply"
    assert doc["section_count"] == 2
    sec1 = next(s for s in doc["sections"] if s["address"]["text"] == "chapter:1/section:1")
    assert sec1["content_hash"] == irnode_content_hash(timelines[
        LegalAddress(path=(("chapter", "1"), ("section", "1")))
    ].versions[0].content)


def test_dump_main_json_defaults_as_of_to_horizon(monkeypatch, capsys) -> None:
    timelines = _two_section_timelines()

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        master = _empty_master()
        master.timelines = timelines
        master.title = ""
        return master

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="2000/1",
            after=None,
            source=None,
            address=None,
            before="",
            jurisdiction="fi",
            db=None,
            json=True,
            hashes=False,
            as_of=None,
        )
    )

    doc = json.loads(capsys.readouterr().out)
    assert doc["as_of"] == "9999-12-31"
    assert doc["flags"]["as_of_defaulted"] is True
    assert doc["section_count"] == 2


def test_dump_main_hashes_flag_appends_short_hash(monkeypatch, capsys) -> None:
    timelines = _two_section_timelines()

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        master = _empty_master()
        master.timelines = timelines
        master.title = "Replayed Title"
        return master

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="2000/1",
            after=None,
            source=None,
            address=None,
            before="",
            jurisdiction="fi",
            db=None,
            json=False,
            hashes=True,
            as_of="2022-01-01",
        )
    )

    out = capsys.readouterr().out
    full = irnode_content_hash(timelines[
        LegalAddress(path=(("chapter", "1"), ("section", "1")))
    ].versions[0].content)
    assert full[:12] in out
    assert "chapter:1/section:1" in out
    assert "Soveltamisala" in out
    assert "Sections: 2" in out


def test_dump_main_json_rejected_for_non_apply_stage(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        dump.main(
            Namespace(
                statute_id="2000/1",
                after="parse",
                source=None,
                address=None,
                before="",
                jurisdiction="fi",
                db=None,
                json=True,
                hashes=False,
                as_of=None,
            )
        )
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "apply" in err
    assert "--json" in err


def test_dump_main_default_output_unchanged_without_flags(monkeypatch, capsys) -> None:
    # Default human apply read must remain stable when no new flag is set:
    # it goes through ``format_statute_with_attachments`` on the rewritten
    # master.ir, never the JSON/hashes path.
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        called["statute_id"] = statute_id
        return SimpleNamespace(
            serialize_text=lambda: "UNCHANGED BODY TEXT",
            ir=IRNode(kind=IRNodeKind.BODY, text="", children=()),
            title="",
            ctx=SimpleNamespace(attachment_supplements=()),
        )

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="2000/1",
            after=None,
            source=None,
            address=None,
            before="",
            jurisdiction="fi",
            db=None,
            json=False,
            hashes=False,
            as_of=None,
        )
    )

    out = capsys.readouterr().out
    # The apply path emits the statute header followed by the rendered tree
    # (which is empty for the empty BODY IR we supplied, plus a trailing
    # newline). The byte-exact value is pinned here as a stable-output regression.
    assert out == "Statute: 2000/1\nStage  : APPLY (full replay)\n\n\n"
    assert called["statute_id"] == "2000/1"


def test_dump_main_human_as_of_uses_materialized_ir(monkeypatch, capsys) -> None:
    fold_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="EXPIRED"),))
    materialized_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="LIVE"),))
    called: dict[str, object] = {}

    def fake_replay_xml(
        statute_id: str,
        *,
        stop_before: str = "",
        quiet: bool = False,
        as_of: str = "",
    ):
        called["as_of"] = as_of
        return SimpleNamespace(
            ir=fold_ir,
            materialized_state=SimpleNamespace(ir=materialized_ir),
            serialize_text=lambda: "EXPIRED",
        )

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="2000/1",
            after=None,
            source=None,
            address=None,
            before="",
            jurisdiction="fi",
            db=None,
            json=False,
            hashes=False,
            as_of="2022-01-01",
        )
    )

    out = capsys.readouterr().out
    assert called["as_of"] == "2022-01-01"
    assert out == "Statute: 2000/1\nStage  : APPLY (full replay)\n\nLIVE\n"
