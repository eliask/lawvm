"""Fire-drill tests for Norway RENUMBER migration event stamping (§1.6 + §2.9).

Mirrors the SE precedent
``tests/test_sweden_fetch.py::test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``,
adapted for NO's current state:

* op-side ``witness_rule_id`` stamping on every RENUMBER op mint site
  (mirrors EE's ``_EE_SECTION_SEQUENCE_RENUMBER_RULE`` on op construction at
  ``estonia/peg.py:1225``) — Step 2 of iter2 W5 H2;
* receipt-side ``migration_rule_ids`` stamping is PENDING — NO has no
  ``_no_emit_one_op_receipt``-style per-op WriteReceipt helper (the SE analog
  at ``sweden/grafter.py:4145``) yet, so the SE-style ``WriteReceipt`` assertions
  are NOT exercised here. This is the iter2 W5 H2 STOP-and-report condition.
  When the helper lands, extend this file withreceipt-divergence assertions
  mirroring the SE test's `migration_rule_ids == ["se_renumber_relabel"]` /
  `divergence_explained is True` shape.

The four RENUMBER op mint sites in ``src/lawvm/norway/grafter.py``:

* unstructured repeal+renumber combo (subsection-level, ~line 1583);
* unstructured single renumber (section-level, ~line 1858);
* unstructured plural renumber (section-level, ~line 1879);
* structured renumber XML attr (mixed granularity, ~line 2553).

Plus a guard-liveness test driving the production lane
``replay_no_to_pit`` → ``apply_no_ops_conserved`` so the ``witness_rule_id``
stamping is provably reachable from a real user invocation, not just from
``parse_no_amendment_ops`` in isolation (the §2.9 worst-class silent-failure
form: a guard that exists but is unreachable from production).
"""
from __future__ import annotations

import io
import tarfile

from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.norway.grafter import parse_no_amendment_ops
from lawvm.norway.replay import replay_no_to_pit


_RENUMBER_RULE_ID = "no_section_renumber_relabel"


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head>
    <title>Testlov om renumber-witness</title>
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
          <article class="legalP" id="ledd1">Kravene skal oppfylles.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _unstructured_single_renumber_xml() -> bytes:
    """One ``Nåværende § 2 blir ny § 3.`` lead in proper unstructured wrap."""
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2003-12-12-108</li></ul>
    </dd>
    <main>
      <section data-name="kap16">
        <article class="defaultP">I lov 12. desember 2003 nr. 108 om testlov gjøres følgende endringer:</article>
        <article class="defaultP">Nåværende § 2 blir ny § 3.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _unstructured_plural_renumber_xml() -> bytes:
    """``Nåværende §§ 2 og 3 blir §§ 3 og 4.`` (two renumber ops)."""
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2003-12-12-108</li></ul>
    </dd>
    <main>
      <section data-name="kap16">
        <article class="defaultP">I lov 12. desember 2003 nr. 108 om testlov gjøres følgende endringer:</article>
        <article class="defaultP">Nåværende §§ 2 og 3 blir §§ 3 og 4.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _unstructured_repeal_renumber_xml() -> bytes:
    """Section-level ``§ 2 første ledd oppheves. Nåværende annet ledd blir første ledd.``"""
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2003-12-12-108</li></ul>
    </dd>
    <main>
      <section data-name="kap16">
        <article class="defaultP">I lov 12. desember 2003 nr. 108 om testlov gjøres følgende endringer:</article>
        <article class="defaultP">§ 2 første ledd oppheves. Nåværende annet ledd blir første ledd.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _structured_renumber_xml() -> bytes:
    """``<article data-move-part="...§2;;...§3">``."""
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-move-part="lov/2025-01-01-1/§2;;lov/2025-01-01-1/§3">
        <article class="defaultP">Nåværende § 2 blir ny § 3.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _renumber_amendment_xml_for_replay(date_in_force: str) -> bytes:
    """Production-lane amendment: renumber §2 → §3 within lov/2025-01-01-1."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-move-part="lov/2025-01-01-1/§2;;lov/2025-01-01-1/§3">
        <article class="defaultP">Nåværende § 2 blir ny § 3.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _write_archive(archive_path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def _renumber_ops(ops: list[LegalOperation]) -> list[LegalOperation]:
    return [op for op in ops if op.action is StructuralAction.RENUMBER]


# ---- op-side witness_rule_id stamping on each mint site ---------------------


def test_no_unstructured_single_renumber_op_carries_witness_rule_id() -> None:
    """Fire-drill §2.9 guard-liveness: the unstructured single-renumber mint
    site (grafter.py:1858-1869, `Nåværende § A blir ny § B` regex path) stamps
    `witness_rule_id="no_section_renumber_relabel"` on the parse-time op.
    Mirrors EE's `witness_rule_id=_EE_SECTION_SEQUENCE_RENUMBER_RULE` on op
    construction at `estonia/peg.py:1225`.

    Pre-fix state: RENUMBER ops minted at this site carried no `witness_rule_id`
    (the §1.6 unstated-migration invariant's identity migration had no named
    owner at the parse→apply waist).
    """
    ops = parse_no_amendment_ops(
        _unstructured_single_renumber_xml(),
        "no/lovtid/2026-06-27-90",
    )

    renumber_ops = _renumber_ops(ops)
    assert len(renumber_ops) == 1, [
        (op.action, op.target.path, op.destination.path if op.destination else None)
        for op in ops
    ]
    op = renumber_ops[0]
    assert op.target.path == (("section", "2"),)
    assert op.destination is not None
    assert op.destination.path == (("section", "3"),)
    assert op.witness_rule_id == _RENUMBER_RULE_ID


def test_no_unstructured_plural_renumber_op_carries_witness_rule_id() -> None:
    """Fire-drill §2.9: the unstructured plural-renumber mint site
    (grafter.py:1879-1897, `Nåværende §§ A og B blir §§ C og D` regex path)."""
    ops = parse_no_amendment_ops(
        _unstructured_plural_renumber_xml(),
        "no/lovtid/2026-06-27-90",
    )

    renumber_ops = _renumber_ops(ops)
    assert len(renumber_ops) == 2
    dst_labels: list[str] = []
    for op in renumber_ops:
        assert op.destination is not None
        dst_labels.append(op.destination.path[0][1])
    assert dst_labels == ["3", "4"]
    for op in renumber_ops:
        assert op.witness_rule_id == _RENUMBER_RULE_ID


def test_no_unstructured_repeal_renumber_subsection_op_carries_witness_rule_id() -> None:
    """Fire-drill §2.9: the unstructured repeal+renumber combo mint site
    (grafter.py:1583-1594, subsection-level path). The rule id's ``section``
    qualifier is the broad family owner per the catalog entry (mirrors the
    SE one-rule-id-for-all-renumbers pattern at sweden/grafter.py:4145)."""
    ops = parse_no_amendment_ops(
        _unstructured_repeal_renumber_xml(),
        "no/lovtid/2026-06-27-90",
    )

    renumber_ops = _renumber_ops(ops)
    assert len(renumber_ops) == 1, [
        (op.action, op.target.path, op.destination.path if op.destination else None)
        for op in ops
    ]
    op = renumber_ops[0]
    assert op.target.path == (("section", "2"), ("subsection", "2"))
    assert op.destination is not None
    assert op.destination.path == (("section", "2"), ("subsection", "1"))
    assert op.witness_rule_id == _RENUMBER_RULE_ID


def test_no_structured_renumber_op_carries_witness_rule_id() -> None:
    """Fire-drill §2.9: the structured renumber XML-attr mint site
    (grafter.py:2553-2570, `data-move-part` parse path)."""
    ops = parse_no_amendment_ops(
        _structured_renumber_xml(),
        "no/lovtid/2026-06-27-90",
    )

    renumber_ops = _renumber_ops(ops)
    assert len(renumber_ops) == 1, [
        (op.action, op.target.path, op.destination.path if op.destination else None)
        for op in ops
    ]
    op = renumber_ops[0]
    assert op.target.path == (("section", "2"),)
    assert op.destination is not None
    assert op.destination.path == (("section", "3"),)
    assert op.witness_rule_id == _RENUMBER_RULE_ID


def test_no_renumber_op_witness_rule_id_reachable_from_production_lane(tmp_path) -> None:
    """Fire-drill §2.9 guard-liveness (the worst-class failure form):
    the `witness_rule_id` stamped on parse-time RENUMBER ops MUST be reachable
    from the production lane, not just from `parse_no_amendment_ops` in
    isolation. Drives:

      replay_no_to_pit (production entry)
        → parse_no_amendment_ops (minting site, stamps witness_rule_id)
        → apply_no_ops_conserved (production routing per iter2 W2)
        → NOApplyResult.filter_result.accepted_items (typed transport)

    Without this assertion, the `witness_rule_id` stamping would be a guard
    that exists but is unreachable from production — a §2.9 worst-class silent
    failure that passes review and creates false confidence.

    The §1.6 unstated-migration invariant: a RENUMBER op's bound target (source
    label) vs landed destination divergence is the typed migration event and
    MUST carry a named rule id. The op-side `witness_rule_id` is the parse-time
    stamping (iter2 W5 H2 Step 2); the receipt-side `migration_rule_ids`
    stamping requires the per-op WriteReceipt helper that does not yet exist
    in the NO frontend (STOP-and-report, see top-of-file docstring).
    """
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _renumber_amendment_xml_for_replay("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None, result.error
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.apply_filter_result is not None, (
        "apply_no_ops_conserved did not surface a typed FilterResult on the "
        "production lane — the iter2 W2 conserved-wrapper routing may have been "
        "bypassed."
    )
    accepted_renumber_ops = _renumber_ops(list(result.apply_filter_result.accepted_items))
    assert len(accepted_renumber_ops) == 1, [
        (op.action, op.target.path, op.destination.path if op.destination else None)
        for op in result.apply_filter_result.accepted_items
    ]
    op = accepted_renumber_ops[0]
    assert op.witness_rule_id == _RENUMBER_RULE_ID, (
        f"RENUMBER op minted on the production lane lacks the §1.6 witness rule id "
        f"(expected {_RENUMBER_RULE_ID!r}, got {op.witness_rule_id!r}). This is the "
        "§2.9 guard-liveness failure: the op-side stamping exists but is not "
        "reachable from `replay_no_to_pit`."
    )
    assert op.target.path == (("section", "2"),)
    assert op.destination is not None
    assert op.destination.path == (("section", "3"),)

    # The replayed statute actually reflects the renumber: §2 was removed and
    # §3 was inserted with §2's content.
    assert result.replayed is not None
    chapter = result.replayed.body.children[0]
    section_labels = [
        child.label
        for child in chapter.children
        if child.kind is IRNodeKind.SECTION
    ]
    assert "2" not in section_labels, section_labels
    assert "3" in section_labels, section_labels
