from __future__ import annotations

from types import SimpleNamespace

from lawvm.tools.no_commencement_candidates import build_no_commencement_candidate_report


def test_no_commencement_candidates_prefers_exact_source_id_with_commencement_marker(monkeypatch) -> None:
    class FakeArtifact(SimpleNamespace):
        pass

    fake_index = SimpleNamespace(
        entries=[
            SimpleNamespace(
                source_id="no/lovtid/2025-06-20-96",
                title="Lov om dokumentasjon og arkiv (arkivlova)",
                effective_status="contingent",
                raw_date_in_force="Kongen fastset",
                base_ids=["no/lov/2024-12-13-77"],
            )
        ]
    )

    artifacts = [
        FakeArtifact(
            logical_id="no/lovtid/2025-06-25-99",
            payload=(
                '<html><dd class="title">Lov om ikraftsetting av arkivlova</dd>'
                "<body>Denne lova trer i kraft straks. Det gjeld lov 2025-06-20-96.</body></html>"
            ).encode(),
            source_name="norway.farchive",
            member_name="no://lovtid/2025-06-25-99/amendment.xml",
        ),
        FakeArtifact(
            logical_id="no/lovtid/2025-06-25-100",
            payload=(
                '<html><dd class="title">Lov om endringar i arkivlova</dd>'
                "<body>Arkivlova får ei mindre retting.</body></html>"
            ).encode(),
            source_name="norway.farchive",
            member_name="no://lovtid/2025-06-25-100/amendment.xml",
        ),
    ]

    monkeypatch.setattr("lawvm.norway.index.load_no_amendment_index", lambda path: fake_index)
    monkeypatch.setattr("lawvm.norway.sources.resolve_no_source_path", lambda path=None: path)
    monkeypatch.setattr("lawvm.norway.sources.load_no_current_law_titles", lambda data_dir=None: {
        "no/lov/2024-12-13-77": "Arkivlova"
    })
    monkeypatch.setattr("lawvm.norway.sources.iter_no_amendment_artifacts", lambda data_dir=None: iter(artifacts))
    monkeypatch.setattr("lawvm.norway.statsrad.iter_no_statsrad_event_artifacts", lambda data_dir=None: [])

    report = build_no_commencement_candidate_report(
        source_id="no/lovtid/2025-06-20-96",
        data_dir=None,
        index_path=__import__("pathlib").Path(".tmp/no_index_farchive.json"),
        limit=10,
    )

    assert report["candidate_count"] == 2
    top = report["candidates"][0]
    assert top["source_id"] == "no/lovtid/2025-06-25-99"
    assert top["direct_match"] is True
    assert top["commencement_marker"] is True
    assert any(match["kind"] == "source_short_id" for match in top["matches"])


def test_no_commencement_candidates_skips_earlier_sources(monkeypatch) -> None:
    class FakeArtifact(SimpleNamespace):
        pass

    fake_index = SimpleNamespace(
        entries=[
            SimpleNamespace(
                source_id="no/lovtid/2025-04-25-12",
                title="Lov om innkreving av statlige krav mv. (innkrevingsloven)",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                base_ids=["no/lov/2022-06-17-57"],
            )
        ]
    )

    artifacts = [
        FakeArtifact(
            logical_id="no/lovtid/2025-04-20-10",
            payload=(
                '<html><dd class="title">Eldre lov</dd>'
                "<body>Denne loven trer i kraft. 2025-04-25-12.</body></html>"
            ).encode(),
            source_name="norway.farchive",
            member_name="no://lovtid/2025-04-20-10/amendment.xml",
        ),
        FakeArtifact(
            logical_id="no/lovtid/2025-04-26-13",
            payload=(
                '<html><dd class="title">Nyere lov</dd>'
                "<body>Denne loven trer i kraft. 2025-04-25-12.</body></html>"
            ).encode(),
            source_name="norway.farchive",
            member_name="no://lovtid/2025-04-26-13/amendment.xml",
        ),
    ]

    monkeypatch.setattr("lawvm.norway.index.load_no_amendment_index", lambda path: fake_index)
    monkeypatch.setattr("lawvm.norway.sources.resolve_no_source_path", lambda path=None: path)
    monkeypatch.setattr("lawvm.norway.sources.load_no_current_law_titles", lambda data_dir=None: {})
    monkeypatch.setattr("lawvm.norway.sources.iter_no_amendment_artifacts", lambda data_dir=None: iter(artifacts))
    monkeypatch.setattr("lawvm.norway.statsrad.iter_no_statsrad_event_artifacts", lambda data_dir=None: [])

    report = build_no_commencement_candidate_report(
        source_id="no/lovtid/2025-04-25-12",
        data_dir=None,
        index_path=__import__("pathlib").Path(".tmp/no_index_farchive.json"),
        limit=10,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["source_id"] == "no/lovtid/2025-04-26-13"


def test_no_commencement_candidates_direct_only_filters_indirect_base_overlap(monkeypatch) -> None:
    class FakeArtifact(SimpleNamespace):
        pass

    fake_index = SimpleNamespace(
        entries=[
            SimpleNamespace(
                source_id="no/lovtid/2025-06-20-96",
                title="Lov om dokumentasjon og arkiv (arkivlova)",
                effective_status="contingent",
                raw_date_in_force="Kongen fastset",
                base_ids=["no/lov/2024-12-13-77"],
            )
        ]
    )

    artifacts = [
        FakeArtifact(
            logical_id="no/lovtid/2025-06-25-99",
            payload=(
                '<html><dd class="title">Lov om noe annet</dd>'
                "<body>Denne lova trer i kraft straks. Endrer lov/2024-12-13-77.</body></html>"
            ).encode(),
            source_name="norway.farchive",
            member_name="no://lovtid/2025-06-25-99/amendment.xml",
        )
    ]

    monkeypatch.setattr("lawvm.norway.index.load_no_amendment_index", lambda path: fake_index)
    monkeypatch.setattr("lawvm.norway.sources.resolve_no_source_path", lambda path=None: path)
    monkeypatch.setattr("lawvm.norway.sources.load_no_current_law_titles", lambda data_dir=None: {
        "no/lov/2024-12-13-77": "Arkivlova"
    })
    monkeypatch.setattr("lawvm.norway.sources.iter_no_amendment_artifacts", lambda data_dir=None: iter(artifacts))
    monkeypatch.setattr("lawvm.norway.statsrad.iter_no_statsrad_event_artifacts", lambda data_dir=None: [])

    report = build_no_commencement_candidate_report(
        source_id="no/lovtid/2025-06-20-96",
        data_dir=None,
        index_path=__import__("pathlib").Path(".tmp/no_index_farchive.json"),
        limit=10,
        direct_only=True,
    )

    assert report["direct_only"] is True
    assert report["candidate_count"] == 0


def test_no_commencement_candidates_includes_statsrad_evidence(monkeypatch) -> None:
    fake_index = SimpleNamespace(
        entries=[
            SimpleNamespace(
                source_id="no/lovtid/2025-06-20-96",
                title="Lov om dokumentasjon og arkiv (arkivlova)",
                effective_status="contingent",
                raw_date_in_force="Kongen fastset",
                base_ids=["no/lov/2024-12-13-77"],
            )
        ]
    )

    monkeypatch.setattr("lawvm.norway.index.load_no_amendment_index", lambda path: fake_index)
    monkeypatch.setattr("lawvm.norway.sources.resolve_no_source_path", lambda path=None: path)
    monkeypatch.setattr(
        "lawvm.norway.sources.load_no_current_law_titles",
        lambda data_dir=None: {"no/lov/2024-12-13-77": "Arkivlova"},
    )
    monkeypatch.setattr("lawvm.norway.sources.iter_no_amendment_artifacts", lambda data_dir=None: iter(()))
    monkeypatch.setattr(
        "lawvm.norway.statsrad.iter_no_statsrad_event_artifacts",
        lambda data_dir=None: [
            {
                "bulletin_id": "id3103197",
                "event_kind": "commencement",
                "bulletin_date": "2025-05-27",
                "effective_date": "2025-07-01",
                "title": "Lov om dokumentasjon og arkiv (arkivlova)",
                "excerpt": "Sanksjon av Stortingets vedtak ... Lovvedtak 56 (2024-2025). Lov nr. 20. Loven trer i kraft 1. juli 2025. Det gjelder lov 2025-06-20-96.",
                "raw_text": "Loven trer i kraft 1. juli 2025. Det gjelder lov 2025-06-20-96.",
                "source_url": "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/",
                "_locator": "no://statsrad/article/id3103197/events.json",
            }
        ],
    )

    report = build_no_commencement_candidate_report(
        source_id="no/lovtid/2025-06-20-96",
        data_dir=None,
        index_path=__import__("pathlib").Path(".tmp/no_index_farchive.json"),
        limit=10,
        direct_only=True,
    )

    assert report["candidate_count"] == 1
    assert report["statsrad_candidate_count"] == 1
    assert report["local_candidate_count"] == 0
    assert report["candidate_source_counts"] == {"local_corpus": 0, "statsrad": 1}
    assert [group["candidate_source"] for group in report["candidate_groups"]] == ["local_corpus", "statsrad"]
    top = report["candidates"][0]
    assert top["candidate_source"] == "statsrad"
    assert top["source_id"] == "id3103197"
    assert top["evidence_source_id"] == "id3103197"
    assert top["commencement_marker"] is True
    assert report["statsrad_candidates"][0]["candidate_source"] == "statsrad"
