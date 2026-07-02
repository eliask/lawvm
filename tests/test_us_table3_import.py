"""Tests for the OLRC Table III bulk-XML importer + streaming parser.

Synthetic-only: a small rootless Table III fragment (matching the real OLRC
``table3_xml_bulk.xml`` shape — a bare ``<act>...</act>`` sequence with no
enclosing document element) is written to a loose file and ingested into a tmp
farchive. No zip, no network.
"""

from __future__ import annotations

from pathlib import Path

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.import_table3 import (
    _ConcatBytesReader,
    Table3Index,
    import_tables,
    iter_table3_records,
)
from lawvm.us_federal.table3 import Table3Resolver, section_root
from lawvm.us_federal.sources import (
    open_us_federal_farchive,
    usc_classification_table_locator,
)

# Rootless fragment: two acts. The 1935 ch531 (PL 74-271) act classifies
# act-section 1902 -> 42 U.S.C. 1396a (codified) and a 117-2 modern PL row that
# is a `... nt` note target (uncodified).
_TABLE3_FRAGMENT = (
    b"\r\n"
    b"<act id='a1' congress='74' statutes-at-large-volume='49' "
    b"date='1935-08-14' search-key='1935-08-14:531'>\n"
    b" <num>531</num>\n"
    b" <public-law>271</public-law>\n"
    b" <record id='r1' usckey='4200000000139600000000000a00000000'>\n"
    b"  <act-section>1902</act-section>\n"
    b"  <united-states-code-title>42</united-states-code-title>\n"
    b"  <united-states-code-section>1396a</united-states-code-section>\n"
    b" </record>\n"
    b"</act>"
    b"<act id='a2' congress='117' search-key='117-2'>\n"
    b" <num>117-2</num>\n"
    b" <record id='r2' usckey='1500000000900100000000000000000000nt'>\n"
    b"  <act-section>1001</act-section>\n"
    b"  <united-states-code-title>15</united-states-code-title>\n"
    b"  <united-states-code-section>9001 nt</united-states-code-section>\n"
    b" </record>\n"
    b"</act>\n"
)


def test_iter_records_parses_rootless_fragment() -> None:
    records = list(iter_table3_records(_TABLE3_FRAGMENT))
    assert len(records) == 2
    ssa, modern = records
    assert ssa.act_num == "531"
    assert ssa.public_law == "271"
    assert ssa.act_section == "1902"
    assert ssa.usc_title == "42"
    assert ssa.usc_section == "1396a"
    assert ssa.is_classified
    assert ssa.usc_address() == LegalAddress(
        path=(("title", "42"), ("section", "1396a"))
    )
    assert modern.act_num == "117-2"
    assert modern.is_note
    assert not modern.is_classified
    assert modern.usc_address() is None


def test_concat_bytes_reader_fast_path_preserves_cross_chunk_reads() -> None:
    reader = _ConcatBytesReader((b"abc", b"defgh", b"ij"))
    assert reader.read(2) == b"ab"
    assert reader.read(3) == b"cde"
    assert reader.read(4) == b"fghi"
    assert reader.read(10) == b"j"
    assert reader.read(1) == b""


# A Table III row whose USC title is the legacy "50 App." (Title 50 Appendix)
# label rather than a bare integer. It carries a title+section but is NOT a
# resolvable positive-law address on this surface.
_TABLE3_APP_FRAGMENT = (
    b"\r\n"
    b"<act id='a1' congress='113' search-key='113-291'>\n"
    b" <num>113-291</num>\n"
    b" <record id='r1' usckey='50A000000000123400000000000000000000'>\n"
    b"  <act-section>1234</act-section>\n"
    b"  <united-states-code-title>50 App.</united-states-code-title>\n"
    b"  <united-states-code-section>1234</united-states-code-section>\n"
    b" </record>\n"
    b"</act>\n"
)


def test_title_50_appendix_row_is_held_out_not_classified() -> None:
    # A "50 App." title carries text but no int-able positive-law title. It must
    # be treated as uncodified (not classified) so the downstream int(usc_title)
    # address build never crashes on it.
    (record,) = list(iter_table3_records(_TABLE3_APP_FRAGMENT))
    assert record.usc_title == "50 App."
    assert record.usc_section == "1234"
    assert not record.is_classified
    # Must not raise (previously int("50 App.") crashed the address build).
    assert record.usc_address() is None


def test_index_resolves_agreeing_classification() -> None:
    idx = Table3Index.from_bytes(_TABLE3_FRAGMENT, modern_pl_only=False)
    assert idx.record_count == 2
    addr = idx.resolve("531", "1902")
    assert addr == LegalAddress(path=(("title", "42"), ("section", "1396a")))
    # A sub-section act-section (1902(a)) indexes under the 1902 root.
    assert idx.resolve("531", "1902(a)") == addr
    # The note row does not resolve to a codified address.
    assert idx.resolve("117-2", "1001") is None


def test_table3_section_root_scanner_preserves_literal_nonnumeric_labels() -> None:
    assert section_root("1902(a)") == "1902"
    assert section_root("78o-10") == "78o"
    assert section_root("Art. 1") == "Art. 1"
    assert section_root("Sched. A, B") == "Sched. A, B"

    resolver = Table3Resolver(
        [
            next(iter_table3_records(
                b"<act congress='74'><num>531</num>"
                b"<record usckey='k'><act-section>2001-2002</act-section>"
                b"<united-states-code-title>42</united-states-code-title>"
                b"<united-states-code-section>1</united-states-code-section>"
                b"</record></act>"
            )),
            next(iter_table3_records(
                b"<act congress='66'><num>227</num>"
                b"<record usckey='war'><act-section>Art. 1</act-section>"
                b"<united-states-code-title>10</united-states-code-title>"
                b"<united-states-code-section>1471</united-states-code-section>"
                b"</record></act>"
            )),
        ]
    )

    assert resolver.lookup("531", "2001")
    assert resolver.lookup("531", "2002")
    assert resolver.lookup("227", "Art. 1")
    assert not resolver.lookup("227", "A")


def test_import_table3_and_tables_roundtrip(tmp_path: Path) -> None:
    t3 = tmp_path / "table3_xml_bulk.xml"
    t3.write_bytes(_TABLE3_FRAGMENT)
    tbl1 = tmp_path / "usctable1.htm"
    tbl1.write_bytes(b"<html><body>Table I</body></html>")
    db = tmp_path / "us_federal.farchive"

    report = import_tables(
        release_point="119-99",
        table3_path=t3,
        table_htm_paths=[tbl1],
        db_path=db,
    )
    assert report.total_imported == 2
    assert report.total_errors == 0

    ro = open_us_federal_farchive(db, readonly=True)
    try:
        t3_loc = usc_classification_table_locator("table3", "119-99", ext="xml")
        tbl1_loc = usc_classification_table_locator("table1", "119-99", ext="htm")
        t3_bytes = ro.get(t3_loc)
        assert t3_bytes == _TABLE3_FRAGMENT
        t3_span = ro.resolve(t3_loc)
        tbl1_span = ro.resolve(tbl1_loc)
        assert t3_span is not None and tbl1_span is not None
        assert t3_span.last_metadata is not None and tbl1_span.last_metadata is not None
        assert t3_span.last_metadata["table"] == "table3"
        assert tbl1_span.last_metadata["ext"] == "htm"
        # A sample lookup resolves directly off the archived bytes.
        assert t3_bytes is not None
        idx = Table3Index.from_bytes(t3_bytes, modern_pl_only=False)
        assert idx.resolve("531", "1902") == LegalAddress(
            path=(("title", "42"), ("section", "1396a"))
        )
    finally:
        ro.close()


def test_unrecognized_table_htm_is_typed_skip(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_table.htm"
    bad.write_bytes(b"<html/>")
    db = tmp_path / "us_federal.farchive"
    report = import_tables(
        release_point="119-99",
        table3_path=None,
        table_htm_paths=[bad],
        db_path=db,
    )
    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.skipped_entries[0]["rule_id"] == "us_classification_unrecognized_table"
