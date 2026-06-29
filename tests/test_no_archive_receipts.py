"""Norway archive typed RejectedItem receipts (iter2 W7 M9).

§1.8 receipt contract compliance gap: Norway archive skip receipts previously
went through ``log_archive_member_too_large(exc)`` writing a formatted line to
``sys.stderr`` (``core/archive_safety.py:160-170``) — greppable but not part
of the typed-accounting plane that downstream tooling reads. These tests
drive the upgraded sink-threaded generators and the production
``replay_no_to_pit`` lane to assert the typed ``RejectedItem(item=member_name,
reason_code="no_archive_member_too_large", blocking=False)`` receipt lands
in the accumulator surface (per AGENTS.md §1.8 + §2.9 guard-liveness: every
new guard needs a test that drives a known-violating input through the FULL
production path, not just a unit test of the guard function).

Pattern (B) sink-threading was chosen over pattern (A) union yield because
the latter would break the destructuring ``for sid, html_bytes in
open_lovdata_archive(...)`` unpacking at out-of-scope consumers
(``tools/build.py:449``, ``tools/build.py:480``) — see the §1.8 partial
compliance design note in ``norway/sources.py::_no_record_archive_skip``.
When ``rejected_items`` is ``None`` (a destructuring consumer that did not
thread a sink) the structured stderr receipt via
``log_archive_member_too_large`` is preserved as the §1.8 minimum so the skip
stays greppable — that fallback is exercised by the negative tests below.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from lawvm.core.filter_result import RejectedItem
from lawvm.norway.grafter import (
    open_lovdata_amendment_archive,
    open_lovdata_archive,
)
from lawvm.norway.index import NOAmendmentIndex, NOAmendmentIndexEntry
from lawvm.norway.replay import replay_no_to_pit
from lawvm.norway.sources import (
    NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE,
    _iter_current_artifacts_from_dir,
    _iter_lovtidend_members_from_dir,
    iter_no_unmapped_current_xml_members,
    load_no_amendment_artifact_bytes,
)


# Small base statute XML used for the e2e replay fire-drill. Fits under the
# 4 KB cap set in the test so the base-XML load succeeds while the oversized
# amendment XML (~10 KB) is rejected by the cap.
_BASE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head>
    <title>Testlov om data</title>
  </head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="LTI/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="\xc2\xa71" data-lovdata-URL="LTI/lov/2025-01-01-1/\xc2\xa71">
          <h3 class="legalArticleHeader">\xc2\xa7 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
      </section>
    </main>
  </body>
</html>
"""


def _write_archive(archive_path: Path, members: list[tuple[str, bytes]]) -> Path:
    """Write a single bz2-compressed tar archive to ``archive_path``."""
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return archive_path


def _oversized_payload(cap: int) -> bytes:
    """Build an XML-shaped payload larger than ``cap`` to trip the bomb guard."""
    return b"<xml/>" + b"x" * (cap * 10)


def _assert_receipt_matches_cap_bypass(
    receipt: RejectedItem,
    *,
    expected_member_name: str,
    expected_archive_name: str,
    expected_cap: int,
) -> None:
    assert receipt.reason_code == NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE
    assert receipt.blocking is False
    assert receipt.item == expected_member_name
    assert expected_archive_name in receipt.reason
    assert str(expected_cap) in receipt.reason
    # §1.10 fail-loud: the diagnostic MUST name the concrete fix
    # (``LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` env var) so triage does not have
    # to re-derive the cause from a bare message.
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in receipt.reason


# ---------------------------------------------------------------------------
# Per-generator sink-threaded receipt tests (synthetic; AGENTS.md §2.9 layer 1)
# ---------------------------------------------------------------------------


def test_iter_current_artifacts_from_dir_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized ``gjeldende-lover.tar.bz2`` member lands in ``rejected_items``.

    Drive a synthesized NO archive with a malicious oversized member through
    ``_iter_current_artifacts_from_dir`` (the legacy tar-directory lane),
    threading a ``rejected_items`` sink. The typed ``RejectedItem`` receipt
    must be appended with ``reason_code=no_archive_member_too_large`` and
    ``item=member_name``; members within the cap pass through unchanged.
    """
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "nl/nl-20250101-001.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    artifacts = list(
        _iter_current_artifacts_from_dir(tmp_path, rejected_items=rejected)
    )

    assert artifacts == []
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


def test_iter_lovtidend_members_from_dir_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized ``lovtidend-avd1-*.tar.bz2`` member lands in ``rejected_items``."""
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "lti/2025/nl-20250202-005.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    members = list(
        _iter_lovtidend_members_from_dir(tmp_path, rejected_items=rejected)
    )

    assert members == []
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


def test_iter_no_unmapped_current_xml_members_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized unmapped ``gjeldende-lover.tar.bz2`` member lands in ``rejected_items``.

    The unmapped iterator filters members whose filename maps to a legal id
    BEFORE the cap check, so this test uses a non-mappable member name
    (``unknown/garbage.xml``) to drive the ``safe_tar_read`` site and assert
    the receipt fires on the path that would otherwise silently skip.
    """
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "unknown/garbage.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    artifacts = list(
        iter_no_unmapped_current_xml_members(tmp_path, rejected_items=rejected)
    )

    assert artifacts == []
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


def test_load_no_amendment_artifact_bytes_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized amendment member returns ``None`` AND records a typed receipt.

    The loader's contract is ``bytes | None`` (no accumulator surface): the
    §1.8 receipt is threaded via the optional ``rejected_items`` sink; when
    no sink is threaded, the stderr fallback stays (negative test below).
    The ``None`` return is the over-retention principle (§0 — never
    fabricate) that lets the replay lane classify the skip as
    ``amendments_skipped_missing_source`` while the typed receipt explains
    WHY the source was unavailable (oversize cap).
    """
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "lti/2025/nl-20250202-005.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    result = load_no_amendment_artifact_bytes(
        source_id="no/lovtid/2025-02-02-5",
        archive_name=archive_path.name,
        member_name=member_name,
        source_path=tmp_path,
        rejected_items=rejected,
    )

    assert result is None
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


def test_open_lovdata_archive_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized ``gjeldende-lover.tar.bz2`` member lands in ``rejected_items``.

    The grafter's ``open_lovdata_archive`` helper yields ``(statute_id, bytes)``
    tuples — pattern (A) union yield would break the ``for sid, html_bytes in
    open_lovdata_archive(...)`` destructuring at out-of-scope
    ``tools/build.py:449``, so sink-threading (pattern B) is the upgrade.
    """
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "nl/nl-20250101-001.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    items = list(open_lovdata_archive(str(archive_path), rejected_items=rejected))

    assert items == []
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


def test_open_lovdata_amendment_archive_records_oversize_in_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An oversized ``lovtidend-avd1-*.tar.bz2`` member lands in ``rejected_items``."""
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "lti/2025/nl-20250202-005.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    rejected: list[RejectedItem[str]] = []
    items = list(
        open_lovdata_amendment_archive(str(archive_path), rejected_items=rejected)
    )

    assert items == []
    assert len(rejected) == 1
    _assert_receipt_matches_cap_bypass(
        rejected[0],
        expected_member_name=member_name,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )


# ---------------------------------------------------------------------------
# Negative / fallback tests
# ---------------------------------------------------------------------------


def test_iter_current_artifacts_from_dir_falls_back_to_stderr_when_no_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When no sink is threaded, the prior stderr receipt is preserved (§1.8 minimum).

    Generator consumers that did not upgrade to thread a sink (out-of-scope
    destructuring callers in ``tools/build.py``) continue to see the
    structured stderr receipt via ``log_archive_member_too_large`` — the
    §1.8 "skip stays visible, never silent" minimum that predates the typed
    sink. The fallback is auditable via capsys so a regression that drops
    it would fail here.
    """
    cap = 1024
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "nl/nl-20250101-001.xml"
    oversized = _oversized_payload(cap)
    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    _write_archive(archive_path, [(member_name, oversized)])

    artifacts = list(_iter_current_artifacts_from_dir(tmp_path))

    assert artifacts == []
    captured = capsys.readouterr().err
    assert "ARCHIVE_MEMBER_TOO_LARGE" in captured
    assert member_name in captured
    assert archive_path.name in captured
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in captured


def test_small_member_passes_through_unchanged_with_typed_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Negative: a small ``gjeldende-lover.tar.bz2`` member does not emit a receipt.

    The cap must not over-reject legitimate input; a small base-XML member
    flows through ``_iter_current_artifacts_from_dir`` unchanged and the
    ``rejected_items`` sink stays empty.
    """
    cap = 4096
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    member_name = "nl/nl-20250101-001.xml"
    payload = b"<xml/>small"
    archive_path = tmp_path / "gjeldende-lover.tar.bz2"
    _write_archive(archive_path, [(member_name, payload)])

    rejected: list[RejectedItem[str]] = []
    artifacts = list(
        _iter_current_artifacts_from_dir(tmp_path, rejected_items=rejected)
    )

    assert len(artifacts) == 1
    assert artifacts[0].logical_id == "no/lov/2025-01-01-1"
    assert artifacts[0].payload == payload
    assert rejected == []


# ---------------------------------------------------------------------------
# §2.9 guard-liveness production fire-drill (e2e)
# ---------------------------------------------------------------------------


def test_replay_no_to_pit_fires_typed_archive_receipt_in_production_lane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end fire-drill: ``replay_no_to_pit`` carries the typed receipt.

    Drives a synthesized NO amendment archive with a malicious oversized
    member through the FULL production replay lane (``replay_no_to_pit``) —
    not just a unit test of the loader. The base statute XML is small
    enough to load under the cap; the amendment XML is oversized and the
    loader returns ``None`` while the typed ``RejectedItem`` receipt lands
    on ``result.archive_rejected_items``.

    Without routing production through the sink-threaded loader call, the
    typed-receipt upgrade at the generator level would be unreachable from
    production — a §2.9 worst-class silent failure (a guard that exists
    but is unreachable from the production lane looks real, passes review,
    and creates false confidence). The e2e assertion closes that hole.
    """
    cap = 4096  # Big enough for the small base XML, small enough for the test.
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    amendment_member = "lti/2025/nl-20250202-005.xml"
    oversized_amendment = _oversized_payload(cap)
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            (amendment_member, oversized_amendment),
        ],
    )

    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        source_kind="dir",
        generated_at_utc="2025-01-01T00:00:00+00:00",
        archive_names=[archive_path.name],
        archive_metadata={},
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive=archive_path.name,
                member_name=amendment_member,
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                title="Oversized amendment",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=0,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        index=index,
    )

    # The base statute loads and parses successfully under the cap.
    assert result.error is None
    assert result.replayed is not None

    # The loader returns None for the oversized amendment, so the
    # prior §1.8 missing-source surface stays populated (the two §1.8
    # receipts are complementary: the adjudication records the replay-side
    # SKIP, the typed receipt explains the loader-side CAUSE — oversize cap).
    assert result.amendments_skipped_missing_source == ["no/lovtid/2025-02-02-5"]

    # The §2.9 guard-liveness assertion: the typed sink fires in the
    # production replay lane. Without this thread, the loader-level upgrade
    # would be unreachable from production.
    assert len(result.archive_rejected_items) == 1
    receipt = result.archive_rejected_items[0]
    _assert_receipt_matches_cap_bypass(
        receipt,
        expected_member_name=amendment_member,
        expected_archive_name=archive_path.name,
        expected_cap=cap,
    )
    assert isinstance(receipt, RejectedItem)
    assert receipt.reason_code == NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE
