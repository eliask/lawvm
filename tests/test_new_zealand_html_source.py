"""HTML-manifestation source parsing: XML-equivalence + real-corpus smoke.

The NZ consume path stores HTML renditions when no XML manifestation exists
(scan-only Acts). ``html_source.parse_nz_html_source_document`` must lift that
HTML into the SAME ``NZSourceDocument`` IR the XML walker produces, so replay /
self-consistency / oracle logic works unchanged on HTML-sourced Acts.

The golden equivalence test hand-authors an Act in BOTH the PCO XML vocabulary
and the legislation.govt.nz HTML rendition template, parses each through its own
parser, and asserts the normative node structure (kind / path / label / heading
/ text / source_zone) is identical. HTML acts are disjoint from XML acts in the
archive (HTML is only fetched when XML 404s), so a same-blob corpus pair does not
exist; the hand-authored pair is the equivalence oracle, and a real-corpus smoke
test asserts the structural invariants over live archived HTML blobs.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import (
    NZ_SOURCE_FORMAT_HTML,
    NZ_SOURCE_FORMAT_XML,
    latest_source_locator_for_work,
)
from lawvm.new_zealand.html_source import (
    NZHtmlSourceError,
    parse_nz_html_source_document,
)
from lawvm.new_zealand.source_tree import (
    NZSourceDocument,
    parse_archived_work_latest,
    parse_nz_source_document,
    parse_nz_source_document_by_format,
)


# One Act, two manifestations. The XML is the PCO source vocabulary; the HTML is
# the legislation.govt.nz rendition template (structural ``class`` tokens,
# parenthesized sub-item labels, curly-quoted def terms, ``div#legislation``).
_ACT_XML = """\
<act id="LMS1">
  <cover><title>Example Act 1950</title></cover>
  <body>
    <prov id="S1">
      <label>1</label>
      <heading>Short Title</heading>
      <prov.body>
        <subprov id="S1-1"><label>1</label><para><text>This Act is the Example Act 1950.</text></para></subprov>
      </prov.body>
    </prov>
    <prov id="S2">
      <label>2</label>
      <heading>Interpretation</heading>
      <prov.body>
        <subprov id="S2-1">
          <label>1</label>
          <para>
            <text>In this Act,</text>
            <def-para id="D1"><para><text><def-term>Board</def-term> means the Survey Board.</text></para></def-para>
          </para>
        </subprov>
        <subprov id="S2-2">
          <label>2</label>
          <para>
            <text>The Board shall consist of—</text>
            <label-para id="LPa"><label>a</label><para><text>the Chairman; and</text></para></label-para>
            <label-para id="LPb"><label>b</label><para><text>two members.</text></para></label-para>
          </para>
        </subprov>
      </prov.body>
    </prov>
  </body>
  <schedule.group>
    <schedule id="SCH1">
      <label>1</label>
      <heading>Forms</heading>
      <schedule-misc><para><text>Schedule content here.</text></para></schedule-misc>
    </schedule>
  </schedule.group>
</act>
""".encode("utf-8")


_ACT_HTML = """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <title>Example Act 1950 | New Zealand Legislation</title>
    <meta name="iid" content="LMS1">
  </head>
  <body>
    <div id="legislation">
      <div class="act">
        <div class="actbodyfirstpage">
          <div class="cover"><h1 class="title">Example Act 1950</h1></div>
        </div>
        <div class="actbody">
          <div class="body">
            <div class="prov" id="S1">
              <h5 class="prov"><span class="label">1</span> Short Title</h5>
              <div class="prov-body">
                <div class="subprov">
                  <p class="subprov"><span class="label">(1)</span> </p>
                  <div class="para"><p class="text">This Act is the Example Act 1950.</p></div>
                </div>
              </div>
            </div>
            <div class="prov" id="S2">
              <h5 class="prov"><span class="label">2</span> Interpretation</h5>
              <div class="prov-body">
                <div class="subprov">
                  <p class="subprov"><span class="label">(1)</span> </p>
                  <div class="para">
                    <p class="text">In this Act,</p>
                    <div class="def-para" id="D1">
                      <div class="para"><p class="text">“Board” means the Survey Board.</p></div>
                    </div>
                  </div>
                </div>
                <div class="subprov">
                  <p class="subprov"><span class="label">(2)</span> </p>
                  <div class="para">
                    <p class="text">The Board shall consist of—</p>
                    <div class="label-para">
                      <h5 class="label-para"><span class="label">(a)</span> </h5>
                      <div class="para"><p class="text">the Chairman; and</p></div>
                    </div>
                    <div class="label-para">
                      <h5 class="label-para"><span class="label">(b)</span> </h5>
                      <div class="para"><p class="text">two members.</p></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="schedule-group">
            <div class="schedule" id="SCH1">
              <h2 class="schedule"><span class="label">Schedule 1</span> Forms</h2>
              <div class="schedule-misc">
                <div class="para"><p class="text">Schedule content here.</p></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
""".encode("utf-8")


def _normative_shape(document: NZSourceDocument) -> list[tuple[str, tuple[str, ...], str, str, str, str]]:
    """The replay-normative projection of a source document's nodes.

    (kind, path, label, heading, source_zone, text) — the fields downstream
    replay/oracle logic reads. ``xml_id`` / ``xml_path`` are source-witness
    identities that legitimately differ between manifestations, so they are not
    part of the equivalence.
    """
    return [
        (node.kind, node.path, node.label, node.heading, node.source_zone, node.text)
        for node in document.nodes
    ]


def test_html_and_xml_manifestations_yield_equivalent_source_tree() -> None:
    xml_doc = parse_nz_source_document(_ACT_XML, xml_locator="xml", version_id="v1")
    html_doc = parse_nz_html_source_document(_ACT_HTML, html_locator="html", version_id="v1")

    assert xml_doc.metadata["title"] == "Example Act 1950"
    assert html_doc.metadata["title"] == "Example Act 1950"

    assert _normative_shape(html_doc) == _normative_shape(xml_doc)


def test_html_source_recovers_full_hierarchy() -> None:
    html_doc = parse_nz_html_source_document(_ACT_HTML, html_locator="html")
    kinds = html_doc.summary()["node_kinds"]
    assert kinds == {"def-para": 1, "label-para": 2, "prov": 2, "schedule": 1, "subprov": 3}

    def_para = next(n for n in html_doc.nodes if n.kind == "def-para")
    assert def_para.label == "Board"
    assert def_para.text == "Board means the Survey Board."

    schedule = next(n for n in html_doc.nodes if n.kind == "schedule")
    assert schedule.label == "1"
    assert schedule.source_zone == "primary_schedule"


def test_html_source_strips_parenthesized_subitem_labels() -> None:
    html_doc = parse_nz_html_source_document(_ACT_HTML, html_locator="html")
    labels = {
        node.path[-1]
        for node in html_doc.nodes
        if node.kind in {"subprov", "label-para"}
    }
    # Bare labels, not the rendered "(1)"/"(a)" forms.
    assert "subprov:1" in labels
    assert "subprov:2" in labels
    assert "label-para:a" in labels
    assert "label-para:b" in labels


def test_html_source_rejects_page_without_legislation_body() -> None:
    with pytest.raises(NZHtmlSourceError):
        parse_nz_html_source_document(b"<html><body><p>no legislation here</p></body></html>")


def test_html_parse_is_pure_and_repeatable() -> None:
    first = parse_nz_html_source_document(_ACT_HTML, html_locator="html", version_id="v1")
    second = parse_nz_html_source_document(_ACT_HTML, html_locator="html", version_id="v1")
    assert _normative_shape(first) == _normative_shape(second)
    # Re-rooting one node must not affect the other (frozen, independent trees).
    assert replace(first.nodes[0], label="mutated").label == "mutated"
    assert second.nodes[0].label != "mutated"


def test_parse_by_format_routes_html_and_xml() -> None:
    html_doc = parse_nz_source_document_by_format(
        _ACT_HTML, source_format=NZ_SOURCE_FORMAT_HTML, locator="html"
    )
    xml_doc = parse_nz_source_document_by_format(
        _ACT_XML, source_format=NZ_SOURCE_FORMAT_XML, locator="xml"
    )
    assert _normative_shape(html_doc) == _normative_shape(xml_doc)
    # An unknown/empty format falls through to the XML path (never HTML), so an
    # XML-present work is byte-identical to a direct parse.
    fallthrough = parse_nz_source_document_by_format(_ACT_XML, source_format="", locator="xml")
    assert _normative_shape(fallthrough) == _normative_shape(
        parse_nz_source_document(_ACT_XML, xml_locator="xml")
    )


# --- Real-corpus smoke (archive-gated) --------------------------------------

_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


def _sample_html_only_works(archive, *, limit: int) -> list[str]:
    """A deterministic sample of works whose latest source is HTML (no XML).

    Enumerated from the archived ``www`` HTML-rendition locators (``…/en/latest/``)
    minus the works that also have an archived ``.xml`` locator — the exact set
    the XML-only consume path could not replay.
    """
    import re

    www = archive.locators("https://www.legislation.govt.nz/%")
    pattern = re.compile(
        r"https://www\.legislation\.govt\.nz/([a-z]+)/([a-z]+)/(\d+)/([0-9A-Za-z]+)/"
    )
    xml_works: set[tuple[str, ...]] = set()
    html_works: dict[tuple[str, ...], str] = {}
    for loc in www:
        match = pattern.match(loc)
        if match is None:
            continue
        key = match.groups()
        if loc.endswith(".xml"):
            xml_works.add(key)
        else:
            html_works.setdefault(key, key[3])
    html_only = sorted(k for k in html_works if k not in xml_works)
    work_ids: list[str] = []
    for kind, jur, year, number in html_only:
        work_ids.append(f"{kind}_{jur}_{year}_{number}")
        if len(work_ids) >= limit:
            break
    return work_ids


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_html_only_works_become_replayable_source_documents() -> None:
    archive = open_farchive(_REAL_DB)
    try:
        work_ids = _sample_html_only_works(archive, limit=25)
        assert work_ids, "expected HTML-only works in the archive"
        for work_id in work_ids:
            version_id, locator, source_format = latest_source_locator_for_work(archive, work_id)
            # These works have no archived XML, so the resolver must fall back to
            # the HTML rendition — the crux of the newly-replayable set.
            assert source_format == NZ_SOURCE_FORMAT_HTML, work_id
            assert locator.endswith("/"), locator
    finally:
        archive.close()

    for work_id in work_ids:
        document = parse_archived_work_latest(_REAL_DB, work_id)
        # A real Act must lower into a non-empty, well-formed node tree with the
        # normative fields populated on the structural leaves.
        assert document.nodes, work_id
        assert any(node.kind == "prov" for node in document.nodes), work_id
        paths = [node.path for node in document.nodes]
        assert len(paths) == len(set(paths)), f"duplicate node paths in {work_id}"
        for node in document.nodes:
            assert node.source_zone in {
                "primary_body",
                "primary_schedule",
                "front_history",
                "end_history",
                "end_skeleton",
                "unknown",
            }, (work_id, node.source_zone)
