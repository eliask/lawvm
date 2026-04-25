from __future__ import annotations

import io
import json
import tarfile

from lawvm.norway.commencement import (
    apply_no_commencement_overrides,
    build_no_blocked_law_report,
    build_no_commencement_report,
    build_no_commencement_phrase_report,
    build_no_law_report,
    build_no_override_impact_report,
    build_no_source_report,
    build_no_work_queue,
    export_no_work_queue_packets,
    load_no_current_law_ids,
    load_no_commencement_overrides,
    no_override_state,
    normalize_no_commencement_phrase,
    validate_no_commencement_overrides,
)
from lawvm.norway.index import NOAmendmentIndex, NOAmendmentIndexEntry


def test_no_override_state_distinguishes_untracked_blank_and_resolved() -> None:
    assert no_override_state("no/lovtid/2025-02-02-5", None)["override_state"] == "untracked"
    assert no_override_state("no/lovtid/2025-02-02-5", None)["override_has_evidence"] is False
    assert no_override_state(
        "no/lovtid/2025-02-02-5",
        {"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}},
    )["override_state"] == "blank"
    resolved = no_override_state(
        "no/lovtid/2025-02-02-5",
        {"no/lovtid/2025-02-02-5": {"effective_date": "2025-02-10", "note": "manual"}},
    )
    assert resolved["override_state"] == "resolved"
    assert resolved["override_has_note"] is True
    assert resolved["override_has_evidence"] is False


def test_no_override_state_reports_evidence_fields() -> None:
    resolved = no_override_state(
        "no/lovtid/2025-02-02-5",
        {
            "no/lovtid/2025-02-02-5": {
                "effective_date": "2025-02-10",
                "resolution_kind": "force_setting_source",
                "evidence_source_id": "no/lovtid/2025-02-15-7",
                "evidence_excerpt": "Denne loven trer i kraft 10. februar 2025.",
            }
        },
    )
    assert resolved["override_state"] == "resolved"
    assert resolved["override_has_evidence"] is True
    assert resolved["override_resolution_kind"] == "force_setting_source"
    assert resolved["override_evidence_source_id"] == "no/lovtid/2025-02-15-7"


def test_load_no_commencement_overrides_supports_shorthand_and_structured(tmp_path) -> None:
    path = tmp_path / "commencement.json"
    path.write_text(
        json.dumps(
            {
                "overrides": {
                    "no/lovtid/2025-02-02-5": "2025-02-10",
                    "no/lovtid/2025-03-03-6": {
                        "effective_date": "2025-03-15",
                        "note": "manual resolution",
                        "resolution_kind": "force_setting_source",
                        "evidence_source_id": "no/lovtid/2025-03-10-7",
                        "evidence_excerpt": "Trer i kraft 15. mars 2025.",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    overrides = load_no_commencement_overrides(path)

    assert overrides["no/lovtid/2025-02-02-5"]["effective_date"] == "2025-02-10"
    assert overrides["no/lovtid/2025-03-03-6"]["note"] == "manual resolution"
    assert overrides["no/lovtid/2025-03-03-6"]["resolution_kind"] == "force_setting_source"
    assert overrides["no/lovtid/2025-03-03-6"]["evidence_source_id"] == "no/lovtid/2025-03-10-7"


def test_apply_no_commencement_overrides_marks_entries_override() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=["lovtidend-avd1-2025.tar.bz2"],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="Endringslov",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )

    updated = apply_no_commencement_overrides(
        index,
        {"no/lovtid/2025-02-02-5": {"effective_date": "2025-02-10", "note": "manual"}},
    )

    assert updated.entries[0].effective_status == "override"
    assert updated.entries[0].effective_date == "2025-02-10"
    assert "override:2025-02-10" in updated.entries[0].raw_date_in_force


def test_build_no_commencement_report_lists_unresolved_entries() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="Endringslov",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250303-006.xml",
                effective_status="override",
                effective_date="2025-03-15",
                title="Resolved",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_report(index)

    assert report["unresolved_count"] == 1
    assert report["unresolved_by_status"] == {"contingent": 1}
    assert report["entries"][0]["source_id"] == "no/lovtid/2025-02-02-5"


def test_report_after_override_has_no_unresolved_entries() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="Endringslov",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )
    updated = apply_no_commencement_overrides(
        index,
        {"no/lovtid/2025-02-02-5": {"effective_date": "2025-02-10"}},
    )

    report = build_no_commencement_report(updated)

    assert report["unresolved_count"] == 0
    assert report["entries"] == []


def test_build_no_commencement_report_can_filter_and_sort_by_impact(tmp_path) -> None:
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_report(
        index,
        base_id="no/lov/2025-01-01-1",
        phrase=None,
        current_law_ids={"no/lov/2025-01-01-1"},
        current_laws_only=True,
        sort_mode="impact",
    )

    assert report["unresolved_count"] == 2
    assert report["entries"][0]["source_id"] == "no/lovtid/2025-02-02-5"
    assert report["entries"][0]["current_law_base_ids"] == ["no/lov/2025-01-01-1"]
    assert report["entries"][0]["current_law_count"] == 1
    assert report["entries"][0]["sole_blocker_current_laws"] == []


def test_build_no_commencement_report_can_sort_by_unlock_potential(tmp_path) -> None:
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        for member_name in (
            "lti/2025/nl-20250101-001.xml",
            "lti/2025/nl-20250101-002.xml",
            "lti/2025/nl-20250101-003.xml",
        ):
            payload = b"<html/>"
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-04-04-7",
                archive="c.tar.bz2",
                member_name="c.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="C",
                base_ids=("no/lov/2025-01-01-3",),
                n_ops=2,
            ),
        ],
    )

    report = build_no_commencement_report(
        index,
        current_law_ids={
            "no/lov/2025-01-01-1",
            "no/lov/2025-01-01-2",
            "no/lov/2025-01-01-3",
        },
        phrase=None,
        sort_mode="unlock",
    )

    assert [item["source_id"] for item in report["entries"]] == [
        "no/lovtid/2025-02-02-5",
        "no/lovtid/2025-04-04-7",
        "no/lovtid/2025-03-03-6",
    ]
    assert report["entries"][0]["sole_blocker_current_law_count"] == 1


def test_build_no_commencement_report_can_filter_by_phrase(tmp_path) -> None:
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen fastsetter",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_report(
        index,
        phrase="Kongen fastset",
        overrides={"no/lovtid/2025-03-03-6": {"effective_date": "", "note": ""}},
    )

    assert report["phrase_filter"] == "kongen fastsetter"
    assert report["unresolved_count"] == 1
    assert report["entries"][0]["source_id"] == "no/lovtid/2025-03-03-6"
    assert report["entries"][0]["override_state"] == "blank"
    assert report["override_state_counts"] == {"blank": 1}


def test_build_no_commencement_report_can_filter_by_override_state(tmp_path) -> None:
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_report(
        index,
        override_state="blank",
        overrides={"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}},
    )

    assert report["override_state_filter"] == "blank"
    assert report["unresolved_count"] == 1
    assert report["entries"][0]["source_id"] == "no/lovtid/2025-02-02-5"


def test_build_no_source_report_marks_executable_and_sole_blocker_laws(tmp_path) -> None:
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        for member_name in (
            "lti/2025/nl-20250101-001.xml",
            "lti/2025/nl-20250101-002.xml",
        ):
            payload = b"<html/>"
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-3"),
                n_ops=10,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_source_report(
        index,
        source_id="no/lovtid/2025-02-02-5",
        current_law_ids={
            "no/lov/2025-01-01-1",
            "no/lov/2025-01-01-2",
            "no/lov/2025-01-01-3",
        },
        current_law_titles={
            "no/lov/2025-01-01-1": "One",
            "no/lov/2025-01-01-3": "Three",
        },
    )

    assert report["current_law_count"] == 2
    assert report["executable_current_law_count"] == 1
    assert report["sole_blocker_current_law_count"] == 1
    assert report["sole_blocker_executable_current_law_count"] == 0
    assert report["laws"][0]["base_id"] == "no/lov/2025-01-01-1"
    assert report["laws"][0]["has_local_base_source"] is True
    assert report["laws"][0]["sole_blocker"] is False


def test_build_no_law_report_marks_missing_local_base_source(tmp_path) -> None:
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                title="A",
                base_ids=("no/lov/1946-12-13-21",),
                n_ops=1,
            )
        ],
    )

    report = build_no_law_report(
        index,
        base_id="no/lov/1946-12-13-21",
        current_law_ids={"no/lov/1946-12-13-21"},
        executable_current_law_ids=set(),
        current_law_titles={"no/lov/1946-12-13-21": "Legacy"},
    )

    assert report["replay_status"] == "fully_replayable"
    assert report["executable_replay_status"] == "missing_local_base_source"
    assert report["has_local_base_source"] is False
    assert report["amendment_count"] == 1


def test_build_no_work_queue_embeds_top_laws(tmp_path) -> None:
    with tarfile.open(tmp_path / "gjeldende-lover.tar.bz2", "w:bz2") as tf:
        for member_name in (
            "nl/nl-20250101-001.xml",
            "nl/nl-20250101-002.xml",
        ):
            payload = b"<html/>"
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        for member_name in ("lti/2025/nl-20250101-001.xml", "lti/2025/nl-20250101-002.xml"):
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            ),
        ],
    )

    report = build_no_work_queue(
        index,
        laws_per_source=2,
        overrides={"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}},
    )

    assert report["unresolved_count"] == 1
    assert report["work_items"][0]["source_id"] == "no/lovtid/2025-02-02-5"
    assert report["work_items"][0]["override_state"] == "blank"
    assert report["work_items"][0]["top_laws"][0]["base_id"] == "no/lov/2025-01-01-1"
    assert report["work_items"][0]["top_laws"][0]["has_local_base_source"] is True
    assert report["work_items"][0]["sole_blocker_current_laws"] == [
        "no/lov/2025-01-01-1",
        "no/lov/2025-01-01-2",
    ]
    assert report["work_items"][0]["sole_blocker_executable_current_laws"] == [
        "no/lov/2025-01-01-1",
        "no/lov/2025-01-01-2",
    ]
    assert report["work_items"][0]["current_law_count"] == 2
    assert report["override_state_counts"] == {"blank": 1}


def test_build_no_work_queue_can_filter_by_override_state(tmp_path) -> None:
    with tarfile.open(tmp_path / "gjeldende-lover.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        for member_name in ("nl/nl-20250101-001.xml", "nl/nl-20250101-002.xml"):
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        for member_name in ("lti/2025/nl-20250101-001.xml", "lti/2025/nl-20250101-002.xml"):
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-2",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_work_queue(
        index,
        override_state="blank",
        overrides={"no/lovtid/2025-03-03-6": {"effective_date": "", "note": ""}},
    )

    assert report["override_state_filter"] == "blank"
    assert [item["source_id"] for item in report["work_items"]] == ["no/lovtid/2025-03-03-6"]


def test_normalize_no_commencement_phrase_collapses_common_variants() -> None:
    assert normalize_no_commencement_phrase("Kongen bestemmer.") == "kongen bestemmer"
    assert normalize_no_commencement_phrase("Kongen fastset") == "kongen fastsetter"
    assert normalize_no_commencement_phrase("Kongen fastsetter") == "kongen fastsetter"
    assert normalize_no_commencement_phrase("Fra den tid Avtalen trer i kraft for Norge") == "fra den tid ..."


def test_build_no_commencement_phrase_report_groups_by_normalized_phrase(tmp_path) -> None:
    current_payload = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§1">
            <h4 class="legalArticleHeader">§ 1 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    with tarfile.open(tmp_path / "gjeldende-lover.tar.bz2", "w:bz2") as tf:
        for member_name, payload in (
            ("nl/nl-20250101-001.xml", current_payload),
            ("nl/nl-20250101-002.xml", current_payload),
        ):
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        info = tarfile.TarInfo("lti/2025/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen fastset",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=2,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen fastsetter",
                title="B",
                base_ids=("no/lov/2025-01-01-2",),
                n_ops=3,
            ),
        ],
    )

    report = build_no_commencement_phrase_report(
        index,
        current_law_ids={"no/lov/2025-01-01-1", "no/lov/2025-01-01-2"},
        executable_current_law_ids={"no/lov/2025-01-01-1"},
        current_law_titles={},
    )

    assert report["phrase_count"] == 1
    assert report["groups"][0]["phrase"] == "kongen fastsetter"
    assert report["groups"][0]["source_count"] == 2
    assert report["groups"][0]["executable_current_law_count"] == 1
    assert report["groups"][0]["top_sources"][0]["source_id"] == "no/lovtid/2025-02-02-5"


def test_build_no_commencement_phrase_report_can_filter_by_phrase(tmp_path) -> None:
    payload = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§1">
            <h4 class="legalArticleHeader">§ 1 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    with tarfile.open(tmp_path / "gjeldende-lover.tar.bz2", "w:bz2") as tf:
        info = tarfile.TarInfo("nl/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        info = tarfile.TarInfo("lti/2025/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen fastsetter",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_phrase_report(
        index,
        phrase="Kongen bestemmer",
        overrides={"no/lovtid/2025-02-02-5": {"effective_date": "2025-03-01", "note": "done"}},
        current_law_ids={"no/lov/2025-01-01-1"},
        executable_current_law_ids={"no/lov/2025-01-01-1"},
        current_law_titles={},
    )

    assert report["phrase_filter"] == "kongen bestemmer"
    assert report["phrase_count"] == 1
    assert report["groups"][0]["phrase"] == "kongen bestemmer"
    assert report["groups"][0]["override_state_counts"] == {"resolved": 1}


def test_build_no_commencement_phrase_report_can_filter_by_override_state(tmp_path) -> None:
    payload = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§1">
            <h4 class="legalArticleHeader">§ 1 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    with tarfile.open(tmp_path / "gjeldende-lover.tar.bz2", "w:bz2") as tf:
        info = tarfile.TarInfo("nl/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    with tarfile.open(tmp_path / "lovtidend-avd1-2025.tar.bz2", "w:bz2") as tf:
        payload = b"<html/>"
        info = tarfile.TarInfo("lti/2025/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen fastsetter",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = build_no_commencement_phrase_report(
        index,
        override_state="blank",
        overrides={"no/lovtid/2025-03-03-6": {"effective_date": "", "note": ""}},
        current_law_ids={"no/lov/2025-01-01-1"},
        executable_current_law_ids={"no/lov/2025-01-01-1"},
        current_law_titles={},
    )

    assert report["override_state_filter"] == "blank"
    assert report["phrase_count"] == 1
    assert report["groups"][0]["phrase"] == "kongen fastsetter"


def test_export_no_work_queue_packets_writes_summary_and_items(tmp_path) -> None:
    report = {
        "work_items": [
            {"source_id": "no/lovtid/2025-02-02-5", "title": "A"},
            {"source_id": "no/lovtid/2025-03-03-6", "title": "B"},
        ]
    }

    written = export_no_work_queue_packets(report, tmp_path / "packets")

    assert [path.name for path in written] == [
        "summary.json",
        "001_no__lovtid__2025-02-02-5.json",
        "002_no__lovtid__2025-03-03-6.json",
    ]
    assert (tmp_path / "packets" / "summary.json").exists()


def test_load_no_current_law_ids_reads_current_archive(tmp_path) -> None:
    import io
    import tarfile

    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    payload = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§1">
            <h4 class="legalArticleHeader">§ 1 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    with tarfile.open(archive_path, "w:bz2") as tf:
        info = tarfile.TarInfo("nl/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    current_ids = load_no_current_law_ids(tmp_path)

    assert current_ids == {"no/lov/2025-01-01-1"}


def test_load_no_current_law_ids_filters_empty_current_shells(tmp_path) -> None:
    import io
    import tarfile

    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    empty_shell = b"<html><head><title>Lov om endringer i testloven</title></head><body><main></main></body></html>"
    substantive = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§2">
            <h4 class="legalArticleHeader">§ 2 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    with tarfile.open(archive_path, "w:bz2") as tf:
        for name, payload in [
            ("nl/nl-20250101-001.xml", empty_shell),
            ("nl/nl-20250101-002.xml", substantive),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    current_ids = load_no_current_law_ids(tmp_path)

    assert current_ids == {"no/lov/2025-01-01-2"}


def test_build_no_blocked_law_report_groups_by_current_law() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=2,
            ),
        ],
    )

    report = build_no_blocked_law_report(index, current_law_ids={"no/lov/2025-01-01-1"})

    assert report["blocked_law_count"] == 1
    assert report["laws"][0]["base_id"] == "no/lov/2025-01-01-1"
    assert report["laws"][0]["title"] == ""
    assert report["laws"][0]["blocking_count"] == 2
    assert report["laws"][0]["blocking_ops"] == 12


def test_build_no_blocked_law_report_can_filter_base_id() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            )
        ],
    )

    report = build_no_blocked_law_report(
        index,
        current_law_ids={"no/lov/2025-01-01-1", "no/lov/2025-01-01-2"},
        base_id="no/lov/2025-01-01-2",
    )

    assert report["blocked_law_count"] == 1
    assert report["base_id_filter"] == "no/lov/2025-01-01-2"
    assert report["laws"][0]["base_id"] == "no/lov/2025-01-01-2"


def test_build_no_blocked_law_report_without_filter_keeps_all_laws() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1", "no/lov/2025-01-01-2"),
                n_ops=10,
            )
        ],
    )

    report = build_no_blocked_law_report(
        index,
        current_law_ids={"no/lov/2025-01-01-1", "no/lov/2025-01-01-2"},
    )

    assert report["base_id_filter"] == ""
    assert [item["base_id"] for item in report["laws"]] == [
        "no/lov/2025-01-01-1",
        "no/lov/2025-01-01-2",
    ]


def test_validate_no_commencement_overrides_reports_issues() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-03-03-6",
                archive="b.tar.bz2",
                member_name="b.xml",
                effective_status="dated",
                effective_date="2025-03-15",
                title="B",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-04-04-7",
                archive="c.tar.bz2",
                member_name="c.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="C",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = validate_no_commencement_overrides(
        index,
        {
            "no/lovtid/2025-02-02-5": {"effective_date": "2025-02-10"},
            "no/lovtid/2025-03-03-6": {"effective_date": "2025-03-15"},
            "no/lovtid/2025-04-04-7": {"effective_date": "bad"},
            "no/lovtid/2099-01-01-1": {"effective_date": "2025-01-01"},
            "no/lovtid/2025-05-05-8": {"effective_date": ""},
        },
    )

    assert report["resolvable_sources"] == ["no/lovtid/2025-02-02-5"]
    assert report["redundant_sources"] == ["no/lovtid/2025-03-03-6"]
    assert "no/lovtid/2099-01-01-1" in report["unknown_source_ids"]
    assert report["invalid_date_format"] == ["no/lovtid/2025-04-04-7"]
    assert report["blank_effective_date"] == ["no/lovtid/2025-05-05-8"]
    assert report["resolved_with_evidence"] == []
    assert report["resolved_missing_evidence"] == ["no/lovtid/2025-02-02-5", "no/lovtid/2025-03-03-6"]
    assert report["missing_contingent_sources"] == []


def test_validate_no_commencement_overrides_tracks_evidence_presence() -> None:
    index = NOAmendmentIndex(
        data_dir="/tmp/no",
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="a.tar.bz2",
                member_name="a.xml",
                effective_status="contingent",
                raw_date_in_force="Kongen bestemmer",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            ),
        ],
    )

    report = validate_no_commencement_overrides(
        index,
        {
            "no/lovtid/2025-02-02-5": {
                "effective_date": "2025-02-10",
                "resolution_kind": "force_setting_source",
                "evidence_source_id": "no/lovtid/2025-02-09-9",
                "evidence_excerpt": "Trer i kraft 10. februar 2025.",
            }
        },
    )

    assert report["resolved_with_evidence"] == ["no/lovtid/2025-02-02-5"]
    assert report["resolved_missing_evidence"] == []


def test_build_no_override_impact_report_computes_deltas() -> None:
    before = {
        "current_laws_fully_replayable": 28,
        "current_laws_blocked_contingent": 260,
        "current_laws_with_amendments_fully_replayable_executable": 19,
        "current_laws_with_amendments_blocked_contingent_executable": 260,
        "amendment_documents_by_status": {"dated": 91, "contingent": 167},
    }
    after = {
        "current_laws_fully_replayable": 35,
        "current_laws_blocked_contingent": 252,
        "current_laws_with_amendments_fully_replayable_executable": 26,
        "current_laws_with_amendments_blocked_contingent_executable": 252,
        "amendment_documents_by_status": {"dated": 91, "contingent": 159, "override": 8},
    }

    report = build_no_override_impact_report(before, after)

    assert report["current_laws_fully_replayable_delta"] == 7
    assert report["current_laws_blocked_contingent_delta"] == -8
    assert report["current_laws_with_amendments_fully_replayable_executable_delta"] == 7
    assert report["current_laws_with_amendments_blocked_contingent_executable_delta"] == -8
