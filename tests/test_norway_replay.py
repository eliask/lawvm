from __future__ import annotations

import io
import json
import tarfile

from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
from lawvm.norway.index import NOAmendmentIndex, NOAmendmentIndexEntry, build_no_amendment_index, save_no_amendment_index
from lawvm.norway.replay import _effective_date_from_amendment, _no_ref_kind_and_date, replay_no_to_pit
from lawvm.tools.replay_payloads import build_no_replay_payload


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head>
    <title>Testlov om data</title>
  </head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="LTI/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
        <article class="legalArticle" data-name="§2" data-lovdata-URL="LTI/lov/2025-01-01-1/§2">
          <h3 class="legalArticleHeader">§ 2. Krav</h3>
          <article class="legalP" id="ledd1">
            Kravene er:
            <ol>
              <li data-li-identifier="1." data-name="1.">ett krav</li>
              <li data-li-identifier="2." data-name="2.">to krav</li>
            </ol>
          </article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _amendment_xml(date_in_force: str | None) -> bytes:
    if date_in_force is None:
        date_block = ""
    else:
        date_block = f"<dd class=\"dateInForce\">{date_in_force}</dd>"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    {date_block}
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-change-part="lov/2025-01-01-1/§2/nummer/1"
               data-add-new-part="lov/2025-01-01-1/§2/nummer/3">
        <article class="defaultP">I loven skal nr. 1 endres og ny nr. 3 tilfoyes.</article>
        <li data-li-identifier="1." data-name="1.">oppdatert krav</li>
        <li data-li-identifier="3." data-name="3.">tredje krav</li>
      </article>
      <article class="change" data-repeal-part="lov/2025-01-01-1/§1">
        <article class="defaultP">Paragraf 1 oppheves.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _occupied_insert_amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-add-new-part="lov/2025-01-01-1/§2/nummer/1">
        <article class="defaultP">I loven skal ny nr. 1 tilfoyes.</article>
        <li data-li-identifier="1." data-name="1.">erstattet krav</li>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _replace_renumber_amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§2/nummer/3">
        <article class="defaultP">§ 2 nr. 3 skal lyde:</article>
        <li data-li-identifier="3." data-name="3.">tredje krav</li>
      </article>
      <article class="change" data-move-part="lov/2025-01-01-1/§2/nummer/3;;lov/2025-01-01-1/§2/nummer/4">
        <article class="defaultP">Nåværende § 2 nr. 3 blir nytt nr. 4.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _other_base_amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-2">
      <article class="change" data-change-part="lov/2025-01-01-2/§1">
        <article class="defaultP">§ 1 skal lyde:</article>
        <article class="legalP">Annen lov endres.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _write_archive(
    archive_path,
    members: list[tuple[str, bytes]],
) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def _chapter_sections(result):
    assert result.replayed is not None
    chapter = result.replayed.body.children[0]
    return chapter, [child for child in chapter.children if child.kind is IRNodeKind.SECTION]


def test_replay_no_to_pit_applies_effective_amendments(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.base_source_id == "no/LTI/lov/2025-01-01-1"
    assert result.amendments_scanned == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_skipped_future == []
    assert result.amendments_skipped_unknown_effective == []
    assert result.n_ops == 3

    chapter, sections = _chapter_sections(result)
    assert chapter.kind is IRNodeKind.CHAPTER
    assert [section.label for section in sections] == ["2"]
    subsection = sections[0].children[1]
    assert subsection.text == "Kravene er:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "oppdatert krav"),
        ("2", "to krav"),
        ("3", "tredje krav"),
    ]


def test_replay_no_to_pit_loads_exact_index_member_witness(tmp_path) -> None:
    first_archive = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    selected_archive = tmp_path / "lovtidend-avd1-2025-2026.tar.bz2"
    member_name = "lti/2025/nl-20250202-005.xml"
    _write_archive(
        first_archive,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            (member_name, _amendment_xml("2025-02-10").replace(b"oppdatert krav", b"wrong witness")),
        ],
    )
    _write_archive(
        selected_archive,
        [(member_name, _amendment_xml("2025-02-10").replace(b"oppdatert krav", b"selected witness"))],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[first_archive.name, selected_archive.name],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive=selected_archive.name,
                member_name=member_name,
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                title="A",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=3,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        index=index,
    )

    assert result.error is None
    _chapter, sections = _chapter_sections(result)
    subsection = sections[0].children[1]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "selected witness"),
        ("2", "to krav"),
        ("3", "tredje krav"),
    ]


def test_replay_no_to_pit_surfaces_action_family_adjudications(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _occupied_insert_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert [(item.kind, item.detail["rule_id"]) for item in result.adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudications_count"] == 1
    assert payload["adjudication_kind_counts"] == {
        "no_replay_insert_occupied_target_replaced": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "norway"
    assert evidence_row["rule_id"] == "no_insert_occupied_target_replace"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_strict_action_family_rejects_recovery(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _occupied_insert_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        strict_action_family=True,
    )

    assert result.error is not None
    # iter4 W1 (HIGH #2): widened from ``except ValueError`` to
    # ``except Exception`` matching the EE/EU/SE precedent (silent-failure review
    # HIGH #1-3). The gate-message convention now mirrors the EE/EU/SE pattern:
    # ``f"Failed to apply ops: {exc}"`` (the original ValueError's message — which
    # embeds the "action-family recovery" string — is preserved inside the
    # exception repr, so the substring assertion still holds).
    assert "action-family recovery" in result.error
    assert "Failed to apply ops" in result.error
    # iter4 W1 (HIGH #2): the strict_action_family raise IS an apply-raise event,
    # so the typed ``no_replay_apply_raise`` orchestration adjudication (per §1.10
    # — embedding exception_type / exception / clause_text) MUST be on the
    # adjudication ledger alongside the pre-raise per-op skip. Ex-assertion was a
    # fragile exact-count equality (pre-fix: 1 item); the structural-invariant
    # form (AGENTS.md §2.9 — do not pin exact counts a concurrent improvement
    # will break) asserts the pre-raise skip is the first item AND the
    # apply_raise orchestration is present with the correct shape.
    assert [(item.kind, item.detail["rule_id"]) for item in result.adjudications[:1]] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]
    apply_raise_adjudications = [
        a for a in result.adjudications if a.kind == "no_replay_apply_raise"
    ]
    assert len(apply_raise_adjudications) == 1, (
        f"expected exactly one no_replay_apply_raise orchestration adjudication "
        f"on the apply-raise catch; found {len(apply_raise_adjudications)}."
    )
    assert apply_raise_adjudications[0].detail["rule_id"] == "no_replay_apply_raise"
    assert apply_raise_adjudications[0].detail["family"] == "orchestration_failure"
    assert apply_raise_adjudications[0].detail["phase"] == "replay"
    assert apply_raise_adjudications[0].blocking is False  # WITNESS, not gate
    assert apply_raise_adjudications[0].detail["exception_type"] == "ValueError"
    assert "action-family recovery" in apply_raise_adjudications[0].detail["exception"]
    assert "action-family recovery" in apply_raise_adjudications[0].detail["clause_text"]


def test_replay_no_to_pit_propagates_partial_adjudications_on_apply_raise(
    tmp_path,
    monkeypatch,
) -> None:
    """§2.9 + §1.0/§1.8/§1.10 fire-drill (iter4 W1 HIGH #2 — mirror EE/EU/SE pattern):
    ``replay_no_to_pit`` production caller MUST propagate bare-apply's partial
    adjudication witnesses across an apply-fold raise (§1.0 evidence-not-
    silently-destroyed + §1.8 no-unsupported-lane-disappears) AND emit a typed
    ``no_replay_apply_raise`` orchestration adjudication per §1.10 embedding the
    exception (exception_type / exception / clause_text ~400 char snippet).

    Pre-fix (iter2 W4): the NO production caller caught only ``ValueError`` and
    set ``result.error = str(exc)`` — the broad-catch family of NO was the
    WEAKEST contract of the four (EE/EU/SE catch ``Exception``, mirroring
    silent-failure review HIGH #1-3). Any non-ValueError raise
    (``AssertionError`` / ``KeyError`` / ``TypeError`` / internal tree-invariant
    exception) escaped as a bare traceback in the production lane, and
    bare-apply's partial witnesses were silently discarded by the propagating
    exception (the §1.0 partial-loss hole). iter4 W1 (HIGH #2) widens the
    catch to ``Exception`` and emits the typed ``no_replay_apply_raise``
    orchestration adjudication mirroring the EE/EU/SE precedent.

    Drives the FULL ``replay_no_to_pit`` production path (§2.9 guard-liveness:
    the new ``except Exception as exc:`` + adjudication emission must fire from
    the production lane, not just from a unit test of the catch predicate) by
    monkeypatching ``apply_no_ops_conserved`` in the norway.replay module with a
    spy that appends a known pre-raise skip adjudication to
    ``adjudications_out`` (mirrors bare-apply's per-op skip emission BEFORE the
    §1.10 fail-loud raise) then raises ValueError. Mirrors the EE/AU precedent
    at ``tests/test_ee_apply_conserved.py::test_replay_ee_to_pit_propagates_partial_adjudications_on_apply_raise``
    and ``tests/test_eu_apply_conserved.py::test_replay_statute_propagates_partial_adjudications_on_apply_raise``.
    """
    from lawvm.replay_adjudication import CompileAdjudication

    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _occupied_insert_amendment_xml("2025-02-10")),
        ],
    )

    raise_message = "synthesized mid-apply raise (mirrors EE/EU/SE fire-drill pattern)"

    def spy_apply_no_ops_conserved(statute, ops, **kwargs):
        adjudications_out = kwargs.get("adjudications_out")
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind="no_replay_target_not_found_in_spy",
                    message=(
                        "Synthesized pre-raise adjudication — op target not in "
                        "the baseline body (mirrors bare-apply's per-op skip "
                        "emission BEFORE the §1.10 fail-loud raise)."
                    ),
                    source_statute="no/lovtid/2025-02-02-5",
                    blocking=False,
                    phase="replay",
                    op_id="no_spy_replace",
                    detail={
                        "rule_id": "no_replay_target_not_found_in_spy",
                        "phase": "replay",
                        "blocking": False,
                    },
                )
            )
        raise ValueError(raise_message)

    monkeypatch.setattr(
        "lawvm.norway.replay.apply_no_ops_conserved",
        spy_apply_no_ops_conserved,
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    # The blocking gate lives on ``result.error`` (the EE/NO convention for
    # apply-fold failure — ``classify_no_replayability`` and CLI tooling map a
    # non-None ``result.error`` to a blocking replay-failure). Pre-fix the gate
    # was ``result.error = str(exc)`` (only set for ValueError); post-fix it is
    # ``result.error = f"Failed to apply ops: {exc}"`` (matching the EE/EU/SE
    # convention so the gate is observable for ANY exception, not only
    # ValueError).
    assert result.error is not None, (
        "result.error is None — the apply-raise gate did not fire (§2.9 "
        "worst-class silent failure: guard unreachable from production)."
    )
    assert "Failed to apply ops" in result.error, (
        f"result.error={result.error!r} — expected to mirror the EE/EU/SE "
        f"'Failed to apply ops: ...' gate-message convention (iter4 W1 HIGH #2)."
    )
    assert raise_message in result.error
    # The apply did not produce a tree — ``result.replayed`` stays None.
    assert result.replayed is None, (
        f"result.replayed={result.replayed!r} — expected None (apply raised "
        f"mid-fold; the conserved wrapper never returned a statute)."
    )

    # §1.0 / §1.8 partial-witness preservation: the pre-raise skip adjudication
    # emitted by the spy IS on ``result.adjudications``. Pre-fix (iter2 W4) the
    # local list was discarded by the propagating ValueError exception (silent-
    # failure review HIGH #2: production caller was the WEAKEST contract of the
    # four, only catching ValueError and never threading the witness).
    pre_raise = [
        a for a in result.adjudications if a.kind == "no_replay_target_not_found_in_spy"
    ]
    assert pre_raise, (
        "result.adjudications does not carry the pre-raise "
        "no_replay_target_not_found_in_spy witness — the §1.0/§1.8 "
        "partial-loss failure (the broad-catch fix did not thread the "
        "conserved wrapper's ``adjudications_out`` accumulator through "
        "to the production result)."
    )
    assert pre_raise[0].op_id == "no_spy_replace"

    # §1.10 typed orchestration adjudication: ``no_replay_apply_raise`` IS on
    # ``result.adjudications`` with ``exception_type`` / ``exception`` /
    # ``clause_text`` fields embedded in its ``detail``. Pre-fix (iter2 W4) NO
    # had NO orchestration adjudication at all (only ``result.error = str(exc)``)
    # — a downstream consumer had to re-run extraction to diagnose the raise.
    orchestration = next(
        (a for a in result.adjudications if a.kind == "no_replay_apply_raise"),
        None,
    )
    assert orchestration is not None, (
        "result.adjudications does not carry the typed "
        "no_replay_apply_raise orchestration adjudication — the §1.10 "
        "embed-snippet contract is unmet (silent-failure review HIGH #2: "
        "NO was the weakest of the four apply-raise contracts)."
    )
    assert orchestration.detail["exception_type"] == "ValueError", (
        f"orchestration.detail[exception_type]={orchestration.detail.get('exception_type')!r}; "
        f"expected 'ValueError'."
    )
    assert orchestration.detail["exception"] == raise_message
    assert orchestration.detail["clause_text"] == raise_message  # ≤400 chars
    # The orchestration adjudication is non-blocking — it is a WITNESS, not the
    # gate (mirrors the EE/SE conserved-wrapper ``RejectedItem.blocking=False``
    # pattern). The blocking gate lives on ``result.error``.
    assert orchestration.blocking is False
    assert orchestration.phase == "replay"
    assert orchestration.source_statute == "no/lov/2025-01-01-1"
    assert orchestration.detail["rule_id"] == "no_replay_apply_raise"
    assert orchestration.detail["family"] == "orchestration_failure"


def test_replay_no_to_pit_surfaces_parse_action_family_promotion(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _replace_renumber_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    kinds = [item.kind for item in result.adjudications]
    assert "no_parse_replace_promoted_to_insert_for_same_target_renumber" in kinds
    promotion = next(
        item
        for item in result.adjudications
        if item.kind == "no_parse_replace_promoted_to_insert_for_same_target_renumber"
    )
    assert promotion.detail["phase"] == "parse"
    assert promotion.detail["family"] == "action_family_recovery"
    assert promotion.detail["blocking"] is False
    assert promotion.detail["strict_disposition"] == "record"
    assert promotion.detail["quirks_disposition"] == "record"
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"]["no_parse_replace_promoted_to_insert_for_same_target_renumber"] == 1
    evidence_row = next(
        row
        for row in payload["evidence"]["finding_rows"]
        if row["rule_id"] == "no_parse_replace_promoted_to_insert_for_same_target_renumber"
    )
    assert evidence_row["phase"] == "parse"
    assert evidence_row["blocking"] is False
    assert evidence_row["strict_disposition"] == "record"
    assert evidence_row["quirks_disposition"] == "record"


def test_replay_no_to_pit_records_no_matching_change_group(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _other_base_amendment_xml("2025-02-10")),
        ],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[archive_path.name],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive=archive_path.name,
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )
    index_path = tmp_path / "no_index.json"
    save_no_amendment_index(index, index_path)

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        index_path=index_path,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.n_ops == 0
    assert [(item.kind, item.detail["rule_id"]) for item in result.adjudications] == [
        ("no_replay_no_matching_change_group", "no_replay_no_matching_change_group")
    ]
    adjudication = result.adjudications[0]
    assert adjudication.detail["phase"] == "replay"
    assert adjudication.detail["base_id"] == "no/lov/2025-01-01-1"
    assert adjudication.detail["parsed_group_bases"] == ("no/lov/2025-01-01-2",)
    assert adjudication.detail["strict_disposition"] == "block"
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {"no_replay_no_matching_change_group": 1}
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_no_matching_change_group"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_skips_future_amendments(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-01",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_future == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 0
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_future_effective_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_future_effective_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_future_effective_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert evidence_row["evidence"]["detail"]["family"] == "temporal_resolution"
    assert evidence_row["evidence"]["detail"]["temporal_resolution_status"] == "future_effective_date"
    assert evidence_row["evidence"]["detail"]["effective_date"] == "2025-02-10"
    assert evidence_row["evidence"]["detail"]["as_of"] == "2025-02-01"
    assert evidence_row["blocking"] is False
    assert evidence_row["strict_disposition"] == "record"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()

    _chapter, sections = _chapter_sections(result)
    assert [section.label for section in sections] == ["1", "2"]
    subsection = sections[1].children[1]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "ett krav"),
        ("2", "to krav"),
    ]


def test_replay_no_to_pit_marks_unknown_effective_dates(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml(None)),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_unknown_effective == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 0
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_unknown_effective_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_unknown_effective_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_unknown_effective_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert evidence_row["evidence"]["detail"]["family"] == "temporal_resolution"
    assert evidence_row["evidence"]["detail"]["temporal_resolution_status"] == "unknown_effective_date"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_surfaces_contingent_commencement_skip(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
        ],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2001-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="contingent",
                effective_date=None,
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
        index=index,
    )

    assert result.error is None
    assert result.amendments_skipped_contingent == ["no/lovtid/2025-02-02-5"]
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_contingent_commencement_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_contingent_commencement_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_contingent_commencement_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert evidence_row["evidence"]["detail"]["family"] == "temporal_resolution"
    assert evidence_row["evidence"]["detail"]["temporal_resolution_status"] == "unresolved_contingent"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_marks_missing_source_separately(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
        ],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2001-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="date",
                effective_date="2025-02-10",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
        index=index,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_unknown_effective == []
    assert result.amendments_skipped_missing_source == ["no/lovtid/2025-02-02-5"]
    assert [(item.kind, item.source_statute, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_missing_amendment_source", "no/lovtid/2025-02-02-5", "acquisition")
    ]
    payload = build_no_replay_payload(result)
    assert payload["amendment_counts"]["unknown_effective"] == 0
    assert payload["amendment_counts"]["missing_source"] == 1
    assert payload["skipped_amendments"]["missing_source"] == ["no/lovtid/2025-02-02-5"]
    assert payload["adjudication_kind_counts"] == {
        "no_replay_missing_amendment_source": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_missing_amendment_source"
    assert evidence_row["phase"] == "acquisition"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_effective_date_from_amendment_marks_contingent_force() -> None:
    xml = b"""<html><body><dd class=\"dateInForce\">Kongen bestemmer</dd></body></html>"""

    effective = _effective_date_from_amendment(xml, source_date="2025-02-02")

    assert effective.effective_status == "contingent"
    assert effective.effective_date is None


def test_effective_date_from_amendment_uses_source_date_for_straks() -> None:
    xml = b"""<html><body><dd class=\"dateInForce\">Trer i kraft straks.</dd></body></html>"""

    effective = _effective_date_from_amendment(xml, source_date="2025-02-02")

    assert effective.effective_status == "immediate"
    assert effective.effective_date == "2025-02-02"


def test_replay_no_to_pit_accepts_prebuilt_index(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )
    index = build_no_amendment_index(tmp_path)
    index_path = tmp_path / "no_index.json"
    save_no_amendment_index(index, index_path)

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        index_path=index_path,
    )

    assert result.error is None
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 3


def test_replay_no_to_pit_accepts_commencement_override(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("Kongen bestemmer")),
        ],
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"no/lovtid/2025-02-02-5": "2025-02-10"}),
        encoding="utf-8",
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        commencement_path=commencement_path,
    )

    assert result.error is None
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_skipped_contingent == []
    assert result.n_ops == 3


# --- §1.10: malformed base_id surfaces as a typed NOReplayResult.error ---
#
# Before this fix, ``_no, ref_kind, date_part = norm_base_id.split("/", 2)``
# silently crashed with a bare ValueError when ``norm_base_id`` had fewer
# than 3 ``/``-segments (e.g. ``no/lov``), and ``year = int(date_part[:4])``
# crashed when the date segment did not begin with a 4-digit year. Both escaped
# the existing try/except around ``_normalize_base_id`` and surfaced to the
# CLI as a raw Python traceback — the §1.10 invisible-silent-failure smell,
# just the loud side of it. The ``_no_ref_kind_and_date`` helper narrows the
# accepted shape and the try-block in ``replay_no_to_pit`` now wraps the
# year-parse so any malformed shape produces a typed ``NOReplayResult.error``
# instead of a crash.


def test_no_ref_kind_and_date_extracts_canonical_segments() -> None:
    no, ref_kind, date_part = _no_ref_kind_and_date("no/lov/2024-01-12-1")

    assert no == "no"
    assert ref_kind == "lov"
    assert date_part == "2024-01-12-1"


def test_no_ref_kind_and_date_raises_typed_for_two_segment_id() -> None:
    # ``_normalize_base_id`` accepts any ``no/...`` prefix; the helper narrows
    # the accepted shape to ``no/<kind>/<date>`` and raises ValueError when the
    # 3-segment canonical form is missing. The error message names the offending
    # id so triage does not have to re-run replay to find the bad shape.
    try:
        _no_ref_kind_and_date("no/lov")
    except ValueError as exc:
        assert "no/lov" in str(exc)
        assert "expected no/<kind>/<date>" in str(exc)
    else:
        raise AssertionError("expected ValueError on two-segment base_id")


def test_replay_no_to_pit_surfaces_two_segment_base_id_as_typed_error() -> None:
    # Before the fix, the bare ``norm_base_id.split("/", 2)`` crash escaped
    # replay_no_to_pit and bubbled to the CLI as a raw traceback. The replay
    # contract is that malformed inputs return NOReplayResult(error=...) —
    # this test pins the contract end-to-end.
    result = replay_no_to_pit("no/lov", as_of="2026-03-29")

    assert result.error is not None
    assert "expected no/<kind>/<date>" in result.error
    assert "no/lov" in result.error
    # The replay status (derived from the error in verify_no_against_current)
    # collapses to "error"; the REPLAYED state is never reached.
    assert result.replayed is None


def test_replay_no_to_pit_surfaces_non_numeric_date_segment_as_typed_error() -> None:
    # ``year = int(date_part[:4])`` previously crashed with a bare ValueError
    # when ``date_part`` did not begin with a 4-digit year. The try-block now
    # wraps the int parse so any malformed date segment produces a typed
    # NOReplayResult.error carrying the offending id.
    result = replay_no_to_pit("no/lov/xyz-1", as_of="2026-03-29")

    assert result.error is not None
    assert "4-digit year" in result.error
    assert "xyz-1" in result.error
    assert "no/lov/xyz-1" in result.error


def test_replay_no_to_pit_surfaces_unsupported_ref_kind_as_typed_error() -> None:
    # Pre-existing behaviour for ``ref_kind != "lov"``: typed NOReplayResult.error.
    # Pinned here to ensure the new try-blocks above do not regress it.
    result = replay_no_to_pit("no/forordning/2024-01-12-1", as_of="2026-03-29")

    assert result.error is not None
    assert "unsupported Norway ref kind" in result.error
    assert "forordning" in result.error
