#!/usr/bin/env python3
"""
Extract amendment-to-parent relationships from Finlex consolidated statutes.

This tool scans the Finlex consolidated ZIP file and builds a mapping between
amendment acts (muutoslait) and their parent statutes. This is a critical
discovery step for the LawVM Finland frontend, as it allows the grafter to
identify the full chain of patches required to reconstruct a Point-In-Time state.

In LawVM terms, this is part of the 'Source Fact Extraction' layer for Finland.
"""

import argparse
import csv
import datetime as dt
import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, cast

import lxml.etree as etree
from functools import lru_cache

from lawvm.corpus_store import (
    CorpusStore,
    get_corpus_store,
)
from lawvm.core.phase_result import Finding
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.citation_routing import johtolause_cited_target_ids
from lawvm.finland.fi_dates import parse_fi_day_month_year
from lawvm.finland.metadata import _statute_issue_date, get_johtolause
from lawvm.finland.vts import extract_voimaantulo_repeals
from lawvm.core.quirks_disposition import QuirksDisposition

# Pattern for /akn/fi/act/statute-consolidated/YEAR/NUMBER...
# or /akn/fi/act/statute/YEAR/NUMBER...
REF_PATTERN = re.compile(r'/akn/fi/act/statute(?:-consolidated)?/(\d{4})/(\d+(?:-\d+)?)')

# Fallback cache path when no canonical data checkout is configured.
_DEFAULT_CACHE_CSV = Path(".cache/finland/amendment_parents.csv")
_DEFAULT_CACHE_META = _DEFAULT_CACHE_CSV.with_suffix(".meta.json")
_INDEX_SCHEMA_VERSION = "source_vts_title_date_v2"
_CSV_HEADER = ["amendment_id", "parent_id", "edge_kind", "index_schema_version"]
_DEFAULT_CACHE_ENV = "LAWVM_FINLAND_AMENDMENT_INDEX_CACHE"


@dataclass(frozen=True, slots=True)
class _AmendmentIndexSourceKey:
    corpus_path: str
    corpus_size: int
    corpus_mtime_ns: int
    cache_csv_path: str


@dataclass(frozen=True, slots=True)
class _ParentTitleDateCandidate:
    statute_id: str
    title: str
    issue_date: dt.date


_VTS_DATED_PARENT_TITLE_RE = re.compile(
    # lawvm-regex: owning_parser source-VTS parent-title DATE FRAME locator; date
    # is parsed by parse_fi_day_month_year and parent authority is validated by
    # extract_voimaantulo_repeals(parent_id, parent_title), so this only proposes
    # issue-date-index candidates for the typed VTS extractor.
    r"\b(?P<day>\d{1,2})\.?\s+päivänä\s+"
    r"(?P<month>[a-zäöå]+)\s+"
    r"(?P<year>\d{4})\s+annetun\s+"
    r"(?P<instrument>lain|asetuksen|päätöksen)\b",
    re.IGNORECASE,
)


def _default_cache_csv() -> Path:
    override = os.environ.get(_DEFAULT_CACHE_ENV)
    if override:
        return Path(override)
    canonical_root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if canonical_root:
        return Path(canonical_root) / ".cache" / "finland" / "amendment_parents.csv"
    return _DEFAULT_CACHE_CSV


def _append_amendment_index_diagnostic(
    diagnostics_out: list[dict[str, object]] | None,
    *,
    rule_id: str,
    phase: str,
    family: str,
    reason: str,
    detail: dict[str, object],
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": rule_id,
            "phase": phase,
            "family": family,
            "reason": reason,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.RECORD,
            **detail,
        }
    )


def _make_statute_id(year: str, num_raw: str) -> str:
    """Normalize statute ID to YYYY/NUMBER format."""
    if '-' in num_raw:
        # Preserve sub-numbering for older statutes (e.g., 1889/39-001)
        return f"{year}/{num_raw}"
    return f"{year}/{int(num_raw)}"


def _normalize_source_citation_id(raw: str, source_year: int) -> str | None:
    """Normalize textual source citations like ``506/86`` or ``506/1986``."""
    raw = re.sub(r"\s+", "", (raw or ""))
    m = re.fullmatch(r"(\d{1,4})/(\d{2,4})", raw)
    if not m:
        return None
    left, right = m.groups()
    num = int(left)
    if len(right) == 4:
        return f"{right}/{num}"
    year_two = int(right)
    source_century = (source_year // 100) * 100
    full_year = source_century + year_two
    if full_year > source_year:
        full_year -= 100
    return f"{full_year}/{num}"


def _extract_explicit_cross_statute_vts_parents(
    xml_data: bytes,
    amendment_id: str,
    *,
    parent_title_date_candidates: dict[dt.date, tuple[_ParentTitleDateCandidate, ...]] | None = None,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> Set[str]:
    """Extract explicit parent statute IDs mentioned in VTS cross-statute clauses.

    This supplements the direct ``amendedBy``-based index with explicit source
    citations from entry-into-force / voimaantulo clauses such as:

      "Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan ..."
      "Tällä lailla kumotaan ... (785/1992) 11 § ..."

    The extractor is intentionally conservative: explicit statute citations only.
    """
    try:
        tree = parse_corpus_xml(xml_data)
    except etree.XMLSyntaxError as exc:
        _append_amendment_index_diagnostic(
            diagnostics_out,
            rule_id="fi_amendment_index_source_vts_xml_parse_failed",
            phase="parse",
            family="source_pathology",
            reason="Finland amendment index skipped source VTS extraction because source XML was not well-formed.",
            detail={
                "amendment_id": amendment_id,
                "edge_kind": "source_vts_explicit",
                "exception_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return set()

    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (ValueError, IndexError):
        return set()

    cited_ids: Set[str] = set()
    dated_parent_candidates: dict[str, _ParentTitleDateCandidate] = {}
    elements = tree.findall(".//{*}section") + tree.findall('.//{*}hcontainer[@eId="entryIntoForce"]')
    seen_texts: Set[str] = set()
    for el in elements:
        text = re.sub(r"\s+", " ", etree.tostring(el, method="text", encoding="unicode")).strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        lower = text.lower()
        is_relevant = (
            "kumotaan" in lower
            or ("jää" in lower and "lukuun ottamatta" in lower and "voimaan" in lower)
        )
        if not is_relevant:
            continue

        cut = re.search(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b", text, re.IGNORECASE)
        target_zone = text[:cut.start()] if cut else text
        for raw_citation in re.findall(r"\(\s*(\d{1,4}\s*/\s*\d{2,4})\s*\)", target_zone):
            norm = _normalize_source_citation_id(raw_citation, source_year)
            if norm and norm != amendment_id:
                cited_ids.add(norm)
        if parent_title_date_candidates:
            for match in _VTS_DATED_PARENT_TITLE_RE.finditer(target_zone):
                issue_date = parse_fi_day_month_year(
                    match.group("day"),
                    match.group("month"),
                    match.group("year"),
                )
                if issue_date is None:
                    continue
                for candidate in parent_title_date_candidates.get(issue_date, ()):
                    if candidate.statute_id != amendment_id:
                        dated_parent_candidates.setdefault(candidate.statute_id, candidate)

    candidates: Set[str] = set()
    for parent_id in cited_ids:
        try:
            if extract_voimaantulo_repeals(xml_data, parent_id):
                candidates.add(parent_id)
        except Exception as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_parent_extraction_failed",
                phase="parse",
                family="source_pathology",
                reason="Finland amendment index skipped a candidate source VTS parent because extraction failed.",
                detail={
                    "amendment_id": amendment_id,
                    "parent_id": parent_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue
    if dated_parent_candidates:
        for parent_id, candidate in sorted(dated_parent_candidates.items()):
            try:
                if extract_voimaantulo_repeals(
                    xml_data,
                    parent_id,
                    parent_title=candidate.title,
                ):
                    candidates.add(parent_id)
            except Exception as exc:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_source_vts_parent_extraction_failed",
                    phase="parse",
                    family="source_pathology",
                    reason="Finland amendment index skipped a candidate source VTS parent because extraction failed.",
                    detail={
                        "amendment_id": amendment_id,
                        "parent_id": parent_id,
                        "edge_kind": "source_vts_explicit",
                        "exception_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                continue
    return candidates


def _parent_title_date_candidate(
    parent_id: str,
    root: etree._Element,
) -> _ParentTitleDateCandidate | None:
    title_el = root.find(".//{*}docTitle")
    if title_el is None:
        return None
    title = " ".join(etree.tostring(title_el, method="text", encoding="unicode").split())
    if not title:
        return None
    issue_date = _statute_issue_date(root)
    if issue_date is None:
        return None
    return _ParentTitleDateCandidate(
        statute_id=parent_id,
        title=title,
        issue_date=issue_date,
    )

def _johtolause_cited_parents(xml_data: bytes, amendment_id: str) -> Set[str]:
    """Target statute IDs named by an amendment's johtolause (enacting clause).

    An amendment's enacting clause names the statute it operates on, e.g.
    ``muutetaan ... annetun avioliittolain (234/29) 55 §``. The consolidated
    ``amendedBy`` metadata is occasionally over-broad — it attributes an
    amendment to a statute its enacting clause never names — so this set is used
    to reject contradicting oracle edges.

    The cited set is the *target-zone* citation set from
    :func:`johtolause_cited_target_ids`: only statute numbers cited as the
    operation's target, excluding ``sellaisena kuin ne ovat ... (NNN/YY)``
    provenance citations (which name prior amendments, not the parent). A naive
    "all parenthesised numbers" scan would mistake those provenance numbers for
    the parent and wrongly drop legitimate edges whose parent is named by name
    or date (``13 päivänä kesäkuuta 1929 annetun avioliittolain``) rather than
    number.

    Returns an empty set when the johtolause names no target statute *number*
    (sparse clause, or a parent named only by name/date) so the caller falls
    back to the oracle edge rather than dropping it.
    """
    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (ValueError, IndexError):
        return set()
    try:
        johtolause = get_johtolause(xml_data)
    except etree.XMLSyntaxError:
        return set()
    cited: Set[str] = set()
    for target_id in johtolause_cited_target_ids(johtolause, source_year):
        if target_id and target_id != amendment_id:
            cited.add(target_id)
    return cited


def _johtolause_contradicts_parent(
    cited_parents: Set[str],
    parent_id: str,
    candidate_parents: Set[str],
) -> bool:
    """True when a johtolause names another corroborated parent, not ``parent_id``.

    The edge ``amendment → parent_id`` is rejected only when ALL hold:

    * the johtolause names at least one target statute *number*
      (``cited_parents`` non-empty);
    * ``parent_id`` is not among those cited numbers; and
    * at least one cited number is *itself* an oracle ``amendedBy`` candidate
      parent of the same amendment (``candidate_parents``), i.e. the citation is
      corroborated by the consolidated metadata.

    The corroboration requirement guards against unreliable citation extraction
    — e.g. a malformed ``sellaisna kuin`` (typo for ``sellaisina``) provenance
    clause whose prior-amendment numbers get mistaken for targets. In that case
    the "cited" numbers are not real candidate parents, so the edge is kept
    rather than a legitimate amendment (whose parent is named by name/date only)
    being dropped. An empty cited set is never a contradiction.
    """
    if not cited_parents or parent_id in cited_parents:
        return False
    return bool(cited_parents & candidate_parents)


def build_amendment_index(
    cs: CorpusStore | None = None,
    consolidated_zip_path: Path | None = None,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> List[Tuple[str, str, str]]:
    """Scan consolidated statutes and extract (amendment_id, parent_id) pairs.

    ``cs`` may be a CorpusStore or None (auto-detects via get_corpus_store()).
    ``consolidated_zip_path`` is accepted for backward-compat CLI callers but
    is ignored — the Finland pipeline uses Farchive exclusively.

    Returns:
        List of sorted (amendment_id, parent_id) tuples.
    """
    if cs is None:
        cs = get_corpus_store()

    edges: Set[Tuple[str, str, str]] = set()
    # Candidate oracle edges, gated below by the amendment's johtolause-cited
    # parent so an over-broad ``amendedBy`` entry cannot attach an amendment to
    # a statute its enacting clause never names.
    oracle_candidates: Set[Tuple[str, str]] = set()
    parent_title_date_candidates: dict[dt.date, list[_ParentTitleDateCandidate]] = {}

    # Use oracle_path_index() to enumerate sids, then read each statute via
    # its already-selected locator. Calling read_oracle(sid) here instead
    # would re-scan the locator table per statute, making this loop quadratic
    # over the corpus (~hours on the full Finlex corpus).
    oracle_index = cs.oracle_path_index()
    # Normalized-id → locator map so the johtolause gate can resolve an
    # amendment by its REF_PATTERN-normalized id (e.g. ``1889/39-001``) even when
    # the oracle index key uses a different spelling.
    normalized_locator_index: Dict[str, str] = {}
    for raw_sid, locator in oracle_index.items():
        sid_parts = raw_sid.split("/", 1)
        if len(sid_parts) != 2:
            continue
        normalized_locator_index.setdefault(
            _make_statute_id(sid_parts[0], sid_parts[1]), locator
        )
    print(f"Scanning {len(oracle_index)} consolidated statutes for amendment metadata...")

    for n, sid in enumerate(sorted(oracle_index), start=1):
        if n % 5000 == 0:
            print(f"[amendment_index] consolidated scan {n}/{len(oracle_index)}")
        parts = sid.split("/", 1)
        if len(parts) != 2:
            continue
        year, num_raw = parts
        parent_id = _make_statute_id(year, num_raw)
        try:
            xml_data = cs.read_locator(oracle_index[sid])
            if xml_data is None:
                xml_data = cs.read_oracle(sid)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_oracle_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped consolidated oracle metadata because oracle XML bytes were missing.",
                    detail={
                        "statute_id": sid,
                        "parent_id": parent_id,
                        "edge_kind": "oracle_amendedBy",
                    },
                )
                continue
            root = parse_corpus_xml(xml_data)
            title_date_candidate = _parent_title_date_candidate(parent_id, root)
            if title_date_candidate is not None:
                parent_title_date_candidates.setdefault(
                    title_date_candidate.issue_date,
                    [],
                ).append(title_date_candidate)
            for ref_elem in cast(list[etree._Element], root.xpath('.//*[local-name()="amendedBy"]//*[local-name()="ref"]')):
                href = ref_elem.get('href', '')
                m = REF_PATTERN.search(href)
                if m:
                    amend_id = _make_statute_id(m.group(1), m.group(2))
                    if amend_id != parent_id:
                        oracle_candidates.add((amend_id, parent_id))
        except (KeyError, OSError, etree.XMLSyntaxError, etree.XPathError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_oracle_artifact_skipped",
                phase="parse" if isinstance(exc, (etree.XMLSyntaxError, etree.XPathError)) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped consolidated oracle metadata because the artifact could not be read or parsed.",
                detail={
                    "statute_id": sid,
                    "parent_id": parent_id,
                    "edge_kind": "oracle_amendedBy",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    # Gate oracle amendedBy edges by the amendment's johtolause-cited parent.
    # When an amendment's enacting clause names a different target statute (and
    # that target is corroborated by another oracle candidate edge), the
    # consolidated amendedBy entry for this parent is over-broad and the edge is
    # dropped — it would otherwise apply the amendment's ops to a parent the
    # clause never names. The johtolause is read once per distinct amendment and
    # the cited target set is reused across that amendment's candidate parents.
    # An empty/uncorroborated cited set falls back to the oracle edge so a
    # correct edge is never dropped merely because the clause is sparse or its
    # parent is named only by name/date.
    candidate_parents_by_amend: Dict[str, Set[str]] = {}
    for amend_id, parent_id in oracle_candidates:
        candidate_parents_by_amend.setdefault(amend_id, set()).add(parent_id)

    johtolause_cited_cache: Dict[str, Set[str]] = {}
    for amend_id, parent_id in oracle_candidates:
        cited = johtolause_cited_cache.get(amend_id)
        if cited is None:
            amend_xml: bytes | None = None
            try:
                # Prefer the already-selected oracle locator (read_locator) over
                # a per-sid read_oracle() to keep this pass linear; fall back to
                # the source artifact only when the amendment is absent from the
                # consolidated oracle index.
                locator = normalized_locator_index.get(amend_id)
                if locator is not None:
                    amend_xml = cs.read_locator(locator)
                if amend_xml is None:
                    amend_xml = cs.read_source(amend_id)
            except (KeyError, OSError, etree.XMLSyntaxError):
                amend_xml = None
            cited = (
                _johtolause_cited_parents(amend_xml, amend_id)
                if amend_xml is not None
                else set()
            )
            johtolause_cited_cache[amend_id] = cited
        if _johtolause_contradicts_parent(
            cited, parent_id, candidate_parents_by_amend.get(amend_id, set())
        ):
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_oracle_edge_rejected_by_johtolause",
                phase="resolution",
                family="cross_statute_misattribution",
                reason=(
                    "Finland amendment index rejected an oracle amendedBy edge "
                    "because the amendment's johtolause names other parent "
                    "statute(s) and not this one."
                ),
                detail={
                    "amendment_id": amend_id,
                    "parent_id": parent_id,
                    "edge_kind": "oracle_amendedBy",
                    "johtolause_cited_parents": sorted(cited),
                },
            )
            continue
        edges.add((amend_id, parent_id, "oracle_amendedBy"))

    # Supplement from source VTS clauses. These are not represented by
    # consolidated amendedBy metadata when an amendment touches another statute
    # only via entry-into-force prose.
    parent_title_date_index = {
        key: tuple(value)
        for key, value in parent_title_date_candidates.items()
    }
    statute_ids = sorted(cs.list_statute_ids())
    for n, amendment_id in enumerate(statute_ids, start=1):
        if n % 5000 == 0:
            print(f"[amendment_index] source VTS scan {n}/{len(statute_ids)}")
        try:
            xml_data = cs.read_source(amendment_id)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_source_vts_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped source VTS extraction because source XML bytes were missing.",
                    detail={
                        "amendment_id": amendment_id,
                        "edge_kind": "source_vts_explicit",
                    },
                )
                continue
            for parent_id in _extract_explicit_cross_statute_vts_parents(
                xml_data,
                amendment_id,
                parent_title_date_candidates=parent_title_date_index,
                diagnostics_out=diagnostics_out,
            ):
                edges.add((amendment_id, parent_id, "source_vts_explicit"))
        except (KeyError, OSError, etree.XMLSyntaxError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_artifact_skipped",
                phase="parse" if isinstance(exc, etree.XMLSyntaxError) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped source VTS extraction because the source artifact could not be read or parsed.",
                detail={
                    "amendment_id": amendment_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    return sorted(list(edges))

def _path_from_pathlike(value: object) -> Path | None:
    if isinstance(value, str):
        return Path(value)
    if isinstance(value, os.PathLike):
        return Path(cast(os.PathLike[str], value))
    return None


def _fingerprint_int(value: object) -> int:
    if isinstance(value, int | float | str | bytes | bytearray):
        return int(value)
    raise TypeError(f"Expected integer-like fingerprint field, got {type(value).__name__}")


def _corpus_source_fingerprint(
    cs: CorpusStore | None,
    *,
    findings_out: Optional[List[Finding]] = None,
) -> dict[str, object] | None:
    """Return the backing farchive DB fingerprint, or None for unknown stores.

    The fingerprint probe may fail across path/format/IO axes. Previously
    ``return None`` silently swallowed (AGENTS.md §1.10 silent-fallback). Now
    the swallow is witnessed via a typed ``Finding(kind=UNEXPECTED_PHASE_FAILURE)``
    carrying ``rule_id`` / ``clause_text`` (the corpus-store type for triage) /
    ``jurisdiction``. When the caller plumbs ``findings_out`` the Finding is
    appended there (per-statute audit-trail sink, threaded from the caller);
    otherwise ``log_emitter`` keeps stderr WARNING visibility — never silent.
    """
    try:
        archive = getattr(cs, "_archive", None) if cs is not None else None
        candidates: list[object] = []
        if archive is not None:
            candidates.append(archive)
        if cs is not None:
            candidates.append(cs)

        path: Path | None = None
        for candidate in candidates:
            for attr in ("path", "_path", "db_path", "_db_path", "filename", "_filename"):
                value = getattr(candidate, attr, None)
                path = _path_from_pathlike(value)
                if path is not None:
                    break
            if path is not None:
                break

        if path is None:
            path = Path(os.environ.get("LAWVM_FARCHIVE_DB", "data/finlex.farchive"))
        if not path.exists():
            return None
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    except Exception as exc:
        # Unexpected fingerprint failure: previously ``return None`` silently
        # swallowed; now route through ``named_swallow`` so a typed Finding is
        # constructed with the corpus-store type as ``clause_text`` (AGENTS.md
        # §1.10 — never silent). Sink dispatch mirrors corpus.py:122 precedent:
        # when ``findings_out`` is plumbed, the Finding lands in that audit-trail
        # list; when not, ``log_emitter`` keeps stderr WARNING visibility.
        from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

        finding = build_named_swallow_finding(
            rule_id="fi_amendment_index_corpus_source_fingerprint",
            exception=exc,
            op_id=None,
            clause_text=f"cs_type={type(cs).__name__ if cs is not None else 'None'}",
            jurisdiction="fi",
            source_artifact=None,
        )
        if findings_out is not None:
            findings_out.append(finding)
        else:
            log_emitter()(finding)
        return None


def _fingerprints_equivalent(
    stored: object,
    current: dict[str, object] | None,
) -> bool:
    """True when two source fingerprints identify the same archive state.

    Path strings are compared after resolution so a representation change
    (relative vs absolute, symlink vs real path) alone never invalidates the
    cache — only an actual content change (size/mtime) forces the expensive
    full-corpus rebuild.
    """
    if stored == current:
        return True
    if not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    stored_map = cast(Dict[str, object], stored)
    if stored_map.get("size") != current.get("size"):
        return False
    if stored_map.get("mtime_ns") != current.get("mtime_ns"):
        return False
    stored_path = stored_map.get("path")
    current_path = current.get("path")
    if not isinstance(stored_path, str) or not isinstance(current_path, str):
        return False
    try:
        return Path(stored_path).resolve() == Path(current_path).resolve()
    except OSError:
        return False


def _read_csv_header(csv_path: Path) -> list[str]:
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            return next(csv.reader(f), [])
    except (OSError, StopIteration, csv.Error):
        return []


def _read_cache_meta(meta_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _cache_meta_payload(source_fingerprint: dict[str, object] | None) -> dict[str, object]:
    return {
        "schema": list(_CSV_HEADER),
        "source": source_fingerprint,
    }


def _atomic_write_tmp_path(path: Path) -> Path:
    """Return a per-writer temp path for atomic cache replacement."""
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _write_cache_meta_atomic(
    meta_path: Path,
    source_fingerprint: dict[str, object] | None,
) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _atomic_write_tmp_path(meta_path)
    tmp_path.write_text(
        json.dumps(_cache_meta_payload(source_fingerprint), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, meta_path)


def _write_amendment_index_cache(
    csv_path: Path,
    edges: List[Tuple[str, str, str]],
    source_fingerprint: dict[str, object] | None,
) -> None:
    meta_path = csv_path.with_suffix(".meta.json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_tmp_path = _atomic_write_tmp_path(csv_path)
    with open(csv_tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        writer.writerows((*edge, _INDEX_SCHEMA_VERSION) for edge in edges)
    os.replace(csv_tmp_path, csv_path)
    _write_cache_meta_atomic(meta_path, source_fingerprint)


@contextmanager
def _amendment_index_cache_lock(csv_path: Path):
    """Serialize expensive amendment-index rebuilds across test workers."""
    lock_path = csv_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _amendment_index_cache_is_current(
    csv_path: Path,
    source_fingerprint: dict[str, object] | None,
) -> bool:
    """Return True when the cache can be used without rebuilding."""
    if not csv_path.exists():
        return False
    header = _read_csv_header(csv_path)
    header_is_current = header == _CSV_HEADER
    if not header_is_current:
        return False
    if source_fingerprint is None:
        return True
    meta_path = csv_path.with_suffix(".meta.json")
    if not meta_path.exists():
        # Adopt an existing current-schema CSV as fresh on first sidecar
        # rollout. The written fingerprint catches subsequent DB changes.
        _write_cache_meta_atomic(meta_path, source_fingerprint)
        return True
    meta = _read_cache_meta(meta_path)
    return bool(
        meta is not None
        and _fingerprints_equivalent(meta.get("source"), source_fingerprint)
    )


def _amendment_index_rebuild_reason(
    csv_path: Path,
    source_fingerprint: dict[str, object] | None,
) -> str:
    """Return the user-facing rebuild reason for a stale or missing cache."""
    if not csv_path.exists():
        return f"[amendment_index] Building {csv_path}..."
    header = _read_csv_header(csv_path)
    if header != _CSV_HEADER:
        return f"[amendment_index] {csv_path} schema is stale — rebuilding"
    if source_fingerprint is None:
        return f"[amendment_index] Building {csv_path}..."
    return f"[amendment_index] {csv_path} source fingerprint is stale — rebuilding"


def ensure_amendment_index(
    cs: CorpusStore | None = None,
    csv_path: Path | None = None,
) -> None:
    """Ensure amendment_parents.csv exists and is fresh for the source farchive.

    Transparent caching: rebuilds automatically when the farchive fingerprint
    differs from the sidecar metadata, when the CSV is missing, or when the CSV
    schema is stale.

    ``cs`` may be a CorpusStore (preferred) or None (auto-detects via
    get_corpus_store()).  For unknown backends the source staleness check is
    skipped and the CSV is used as long as it exists with the current schema.
    """
    if csv_path is None:
        csv_path = _default_cache_csv()

    should_close_cs = False
    if cs is None:
        cs = get_corpus_store()
        should_close_cs = True

    try:
        # findings_out=None sanctioned: ``ensure_amendment_index`` is a
        # cache-management utility boundary — no per-statute audit-trail
        # ``list[Finding]`` is in scope here (the §3.2 ledger lives at the
        # replay/PIT compile caller, not at this sidecar-rebuild lane). The
        # swallow at ``_corpus_source_fingerprint`` falls through to
        # ``log_emitter`` stderr WARNING (IO/utility carve-out per
        # ``core/named_swallow.py`` docstring) — never silent.
        source_fingerprint = _corpus_source_fingerprint(cs)
        if _amendment_index_cache_is_current(csv_path, source_fingerprint):
            return

        with _amendment_index_cache_lock(csv_path):
            if _amendment_index_cache_is_current(csv_path, source_fingerprint):
                return

            print(_amendment_index_rebuild_reason(csv_path, source_fingerprint))
            edges = build_amendment_index(cs=cs)
            _write_amendment_index_cache(csv_path, edges, source_fingerprint)
            print(f"[amendment_index] Wrote {len(edges)} mappings to {csv_path}")
    finally:
        if should_close_cs:
            cs.close()


def _default_source_cache_key() -> _AmendmentIndexSourceKey | None:
    """Return an lru_cache key for the default corpus store source file."""
    try:
        cs = get_corpus_store()
    except (OSError, RuntimeError):
        return None
    try:
        # findings_out=None sanctioned: ``_default_source_cache_key`` is a pure
        # lru_cache-key-probe utility — no per-statute audit-trail
        # ``list[Finding]`` is in scope at this IO-boundary swallow site
        # (the §3.2 evidence ledger lives at the replay caller, not here).
        # The swallow falls through to ``log_emitter`` stderr WARNING
        # (core/named_swallow.py docstring's IO/utility carve-out) — never silent.
        fingerprint = _corpus_source_fingerprint(cs)
        if fingerprint is None:
            return None
        return _AmendmentIndexSourceKey(
            corpus_path=str(fingerprint["path"]),
            corpus_size=_fingerprint_int(fingerprint["size"]),
            corpus_mtime_ns=_fingerprint_int(fingerprint["mtime_ns"]),
            cache_csv_path=str(_default_cache_csv()),
        )
    finally:
        cs.close()


def _cache_csv_for_source_key(source_key: _AmendmentIndexSourceKey | None) -> Path:
    if source_key is not None:
        return Path(source_key.cache_csv_path)
    return _default_cache_csv()


@lru_cache(maxsize=256)
def _get_amendment_children_for_source_key(
    source_key: _AmendmentIndexSourceKey | None,
) -> Dict[str, List[str]]:
    """Inner impl keyed on farchive fingerprint so DB changes invalidate in-process."""
    csv_path = _cache_csv_for_source_key(source_key)
    ensure_amendment_index(cs=None, csv_path=csv_path)
    mapping: Dict[str, List[str]] = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            mapping.setdefault(row[1], []).append(row[0])
    return mapping


@lru_cache(maxsize=256)
def _get_amendment_child_edges_for_source_key(
    source_key: _AmendmentIndexSourceKey | None,
) -> Dict[str, List[Tuple[str, str]]]:
    """Return cached {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    csv_path = _cache_csv_for_source_key(source_key)
    ensure_amendment_index(cs=None, csv_path=csv_path)
    mapping: Dict[str, List[Tuple[str, str]]] = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            edge_kind = row[2] if len(row) >= 3 and row[2] else "oracle_amendedBy"
            mapping.setdefault(row[1], []).append((row[0], edge_kind))
    return mapping


def get_amendment_children() -> Dict[str, List[str]]:
    """Return {parent_statute_id: [amendment_id, ...]} mapping.

    Transparent caching: on-disk CSV rebuilt from the corpus store's farchive
    fingerprint when the DB changes or the cache is missing. In-process result
    is keyed on the same fingerprint, so callers never need to know about the
    backing CSV.
    """
    return _get_amendment_children_for_source_key(_default_source_cache_key())


def get_amendment_child_edges() -> Dict[str, List[Tuple[str, str]]]:
    """Return {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    return _get_amendment_child_edges_for_source_key(_default_source_cache_key())


def main():
    parser = argparse.ArgumentParser(description="Extract amendment-to-parent mapping from Finlex.")
    parser.add_argument("--out", type=Path, default=Path("data/finland/amendment_parents.csv"),
                        help="Output CSV path")
    args = parser.parse_args()

    try:
        edges = build_amendment_index()

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADER)
            writer.writerows((*edge, _INDEX_SCHEMA_VERSION) for edge in edges)

        print(f"Successfully wrote {len(edges)} mappings to {args.out}")
    except (FileNotFoundError, OSError, etree.XMLSyntaxError) as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
