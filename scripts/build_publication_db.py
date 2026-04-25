"""build_publication_db.py — Build publication-ready SQLite database from
ready oracle artifact evidence bundles + corrigendum records.

Error families extracted:
  Phase 1 (original):
  - oracle_section_stale (section-level proof cards)
  - oracle_cutoff_version_drift (statute-level version mismatch)
  - xml_html_topology_drift (HTML vs XML structural diff)
  - same_chapter_oracle_range_drift (oracle uses merged range labels)

  Phase 2 (expansion):
  - cross_chapter_oracle_section_drift (oracle has section in wrong chapter)
  - corrigendum_applied (verified johtolause corrections from Säädöskokoelma)

Run from LawVM/ dir:
    uv run python scripts/build_publication_db.py [--cache-dir .tmp/evidence_bundle_cache/] [--output .tmp/finlex_errors_publication.db]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing as mp
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree
from farchive._compression import decompress_blob, decompress_delta
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.semantic.structure import (
    semantic_structure_from_ir,
    semantic_structure_from_oracle,
)
from lawvm.semantic.contracts import (
    semantic_support_projection,
)
from lawvm.tools.editorial_hygiene import (
    strip_editorial_annotations,
    strip_kumottu_attribution,
)
from lawvm.tools._section_debug import resolve_section_key, score_text_pair
from lawvm.tools.section_keys import section_key_sort_text


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS statutes (
    statute_id          TEXT PRIMARY KEY,
    title               TEXT,
    statute_sort_key    TEXT,
    primary_proof_tier  TEXT,
    error_count         INTEGER,
    ready_artifact_count INTEGER,
    error_families      TEXT,
    error_family_counts TEXT,
    consolidated_url    TEXT,
    is_repealed         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    statute_id      TEXT,
    error_family    TEXT,
    error_complexity TEXT,
    review_category TEXT,
    review_tags     TEXT,
    severity        TEXT,
    fixability      TEXT,
    lawvm_status    TEXT,
    evidence_quality TEXT,
    section         TEXT,
    section_display TEXT,
    section_sort_key TEXT,
    section_sort_rank INTEGER DEFAULT 0,
    blame_source    TEXT,
    blame_title     TEXT,
    oracle_version  TEXT,
    oracle_text     TEXT,
    replay_text     TEXT,
    oracle_display_text TEXT,
    replay_display_text TEXT,
    similarity      REAL,
    johtolause_text TEXT,
    suspect_detail  TEXT,
    is_last_touch   INTEGER,
    later_touches   TEXT,
    finlex_url      TEXT,
    section_url     TEXT,
    amendment_url   TEXT,
    semantic_contract_version TEXT,
    oracle_structure TEXT,
    replay_structure TEXT,
    aligned_structure TEXT,
    structure_diff_kind TEXT,
    structure_diff_summary TEXT,
    structure_diff_structural INTEGER DEFAULT NULL,
    structure_diff_label INTEGER DEFAULT NULL,
    structure_diff_text INTEGER DEFAULT NULL,
    structure_diff_events TEXT,
    ready_for_clean_v1 INTEGER DEFAULT 0,
    html_also_wrong INTEGER DEFAULT NULL,
    johtolause_char_span TEXT,
    FOREIGN KEY (statute_id) REFERENCES statutes(statute_id)
);

CREATE TABLE IF NOT EXISTS corpus_stats (
    total_statutes          INTEGER,
    total_ready_artifacts   INTEGER,
    total_section_stale     INTEGER,
    total_cutoff_drift      INTEGER,
    total_topology_drift    INTEGER,
    total_cross_chapter     INTEGER,
    total_corrigendum       INTEGER,
    total_payload_prefers   INTEGER,
    review_category_counts  TEXT,
    total_oracle_indexed    INTEGER,
    total_source_absent     INTEGER,
    generated_at            TEXT
);

-- Per-section amendment chain: all amendments that touched each section, in
-- chronological order.  Phase 1a: chain metadata only (no intermediate text).
CREATE TABLE IF NOT EXISTS section_amendment_chain (
    statute_id    TEXT NOT NULL,
    section_key   TEXT NOT NULL,
    amendment_id  TEXT NOT NULL,
    amendment_ord INTEGER NOT NULL,
    amendment_title TEXT,
    is_blame_source INTEGER DEFAULT 0,
    is_later_touch  INTEGER DEFAULT 0,
    PRIMARY KEY (statute_id, section_key, amendment_id)
);

-- Statutes present in Finlex consolidated oracle but with no AkomaNtoso source XML.
-- LawVM cannot replay these from first principles.
-- content_absent=1: Finlex itself carries <contentAbsent/> in the oracle body.
-- repealed=1: oracle carries <repealedBy/> — statute is definitively dead.
-- Statutes with repealed=0 are either active or have ambiguous status.
CREATE TABLE IF NOT EXISTS source_absent (
    statute_id          TEXT PRIMARY KEY,
    year                INTEGER,
    consolidated_url    TEXT,
    page_title          TEXT,
    page_status_label   TEXT,
    content_absent      INTEGER DEFAULT 1,
    repealed            INTEGER DEFAULT 0
);

-- Manual review annotations from verified_finlex_divergences/*.yaml
-- section can be empty string for statute-level verdicts
CREATE TABLE IF NOT EXISTS manual_reviews (
    statute_id          TEXT NOT NULL,
    section             TEXT NOT NULL DEFAULT '',
    verdict             TEXT NOT NULL,
    explanation         TEXT,
    reviewed_at         TEXT,
    tier                TEXT,
    confidence          TEXT,
    root_cause          TEXT,
    reviewer            TEXT,
    auditor             TEXT,
    audited_at          TEXT,
    PRIMARY KEY (statute_id, section)
);

CREATE INDEX IF NOT EXISTS errors_review_category_idx ON errors(review_category);
CREATE INDEX IF NOT EXISTS errors_fixability_idx ON errors(fixability);
CREATE INDEX IF NOT EXISTS errors_lawvm_status_idx ON errors(lawvm_status);
CREATE INDEX IF NOT EXISTS errors_severity_idx ON errors(severity);
CREATE INDEX IF NOT EXISTS errors_statute_sort_idx ON errors(statute_id, section_sort_rank, section_sort_key, error_family, section);
CREATE INDEX IF NOT EXISTS statutes_error_sort_idx ON statutes(statute_sort_key) WHERE error_count > 0;
CREATE INDEX IF NOT EXISTS statutes_error_count_sort_idx ON statutes(error_count DESC, statute_sort_key) WHERE error_count > 0;
CREATE INDEX IF NOT EXISTS section_amendment_chain_statute_ord_idx ON section_amendment_chain(statute_id, amendment_ord);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def section_display(section: str) -> str:
    """Convert internal section path to Finnish display: 'chapter:3/section:12' -> '3 luku 12 §'."""
    parts = section.split("/")
    result: list[str] = []
    for part in parts:
        if ":" in part:
            kind, val = part.split(":", 1)
            if kind == "part":
                result.append(f"{val.upper()} osa")
            elif kind == "chapter":
                result.append(f"{val} luku")
            elif kind == "section":
                result.append(f"{val} §")
            else:
                result.append(f"{kind}:{val}")
    return " ".join(result)


def _johtolause_section_char_span(
    johtolause_text: str,
    section_path: str,
) -> tuple[int, int] | None:
    """Find the char span of a section reference in johtolause text.

    Extracts the section number from the section path (e.g. "section:12" → "12",
    "chapter:3/section:12a" → "12a"), then locates the matching NUM token in the
    tokenized johtolause.  Returns the span from that NUM token through the
    immediately following PYKALA token (if adjacent), giving a range that covers
    the "12 §" substring.

    Returns (char_start, char_end) in the normalized johtolause text, or None
    if the section number is not found or has no char offset.
    """
    if not johtolause_text or not section_path:
        return None

    # Extract section number from path: "chapter:3/section:12a" → "12a"
    section_num: str = ""
    for part in section_path.split("/"):
        if part.startswith("section:"):
            section_num = part[len("section:") :]
            break
    if not section_num:
        return None

    # Digits portion and optional letter suffix (e.g. "12a" → digits="12", letter="a")
    digits = section_num.rstrip("abcdefghijklmnopqrstuvwxyzäöå")
    letter_suffix = section_num[len(digits) :]

    from lawvm.finland.johtolause.peg3 import tokenize as _tokenize

    tokens = _tokenize(johtolause_text)
    n = len(tokens)

    for i, tok in enumerate(tokens):
        if tok.cat != "NUM":
            continue
        if tok.lemma != digits and tok.text != digits:
            continue
        if tok.char_start < 0:
            continue

        # If there's a letter suffix, the next token should be a LETTER with that value
        check_idx = i
        if letter_suffix:
            if i + 1 < n and tokens[i + 1].cat == "LETTER" and tokens[i + 1].lemma == letter_suffix:
                check_idx = i + 1
            else:
                # Section number with suffix but no letter token — skip (wrong match)
                continue

        # Extend to include the following PYKALA token if immediately adjacent in token stream
        end_tok_idx = check_idx
        if check_idx + 1 < n and tokens[check_idx + 1].cat == "PYKALA" and tokens[check_idx + 1].char_end >= 0:
            end_tok_idx = check_idx + 1

        char_start = tokens[i].char_start
        char_end = tokens[end_tok_idx].char_end
        if char_end > char_start:
            return (char_start, char_end)

    return None


def _statute_base(statute_id: str) -> tuple[str, str] | None:
    """Return Finnish ``(year, num_str)`` after stripping chapter suffixes.

    Evidence bundles now contain mixed statute-id namespaces, including foreign
    ids such as ``eur/2019/555`` and opaque ids with no ``year/num`` structure.
    Finlex ajantasa/alkup URLs only make sense for native Finnish ids like
    ``2019/555`` or ``1917/42-003``. Unsupported ids return ``None`` so callers
    can degrade to an empty URL instead of crashing the whole DB build.
    """
    base = str(statute_id or "").strip()
    if not base:
        return None
    base = base.split("-")[0]
    parts = base.split("/")
    if len(parts) != 2:
        return None
    year, num = parts
    if not year.isdigit() or not num:
        return None
    return year, num


def _finlex_url(kind: str, statute_id: str) -> str:
    base = _statute_base(statute_id)
    if base is None:
        return ""
    year, num = base
    try:
        suffix = f"{year}{int(num):04d}"
    except ValueError:
        suffix = f"{year}{num}"
    return f"https://finlex.fi/fi/laki/{kind}/{year}/{suffix}"


def finlex_ajantasa_url(statute_id: str) -> str:
    return _finlex_url("ajantasa", statute_id)


def finlex_lainsaadanto_url(statute_id: str) -> str:
    raw = str(statute_id or "").strip()
    if not raw:
        return ""
    parts = raw.split("/")
    if len(parts) != 2:
        return ""
    year, num = parts
    if not year.isdigit() or not num:
        return ""
    return f"https://www.finlex.fi/fi/lainsaadanto/{year}/{num}"


def finlex_alkup_url(amendment_id: str) -> str:
    return _finlex_url("alkup", amendment_id)


def _version_sort_key(amendment_id: str) -> tuple[int, int]:
    try:
        base = amendment_id.split("-")[0]
        y, n = base.split("/", 1)
        return (int(y), int(n))
    except (ValueError, AttributeError):
        return (0, 0)


def _configure_publication_db(con: sqlite3.Connection) -> None:
    """Apply build-time SQLite settings for browser-friendly publication DBs."""
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA page_size = 32768")


def _statute_sort_key(statute_id: str) -> str:
    """Canonical lexical sort key for statute ids of the form YYYY/NNN."""
    raw = str(statute_id or "").strip().split("-", 1)[0]
    if "/" not in raw:
        return "~"
    year, num = raw.split("/", 1)
    if not year.isdigit():
        return "~"
    m = re.match(r"^(\d+)([a-zäöå]*)$", num)
    if m:
        return f"{int(year):04d}:{int(m.group(1)):010d}:{m.group(2)}"
    return f"{int(year):04d}:9999999999:{num.lower()}"


def _clean_title(raw: str) -> str:
    title = re.sub(r"&#\d+;", " ", raw or "")
    return re.sub(r"\s+", " ", title).strip()


def _strip_html_tags(text: str) -> str:
    return _clean_title(re.sub(r"<[^>]+>", " ", text or ""))


def _normalize_display_diff_text(text: str) -> str:
    raw = _clean_title(text or "")
    if not raw:
        return ""
    normalized = strip_kumottu_attribution(strip_editorial_annotations(raw))
    return _clean_title(normalized)


def _localize_public_text(text: Any) -> str:
    raw = _clean_title(text or "")
    if not raw:
        return ""
    replacements = {
        "Replay has a section not present in the oracle.": "LawVM:ssä on pykälä, jota ei ole Finlexissä.",
        "Oracle has a section missing from replay.": "Finlexissä on pykälä, jota LawVM:ssä ei ole.",
        "Oracle section looks stale relative to replay.": "Finlexin pykälä näyttää vanhentuneelta LawVM:ään verrattuna.",
        "Editorial convention / presentation noise.": "Toimituksellinen käytäntö / esitysmelu.",
        "Liite / appendix differs.": "Liite eroaa.",
    }
    if raw in replacements:
        return replacements[raw]
    if raw.startswith("Body pairing: "):
        return _clean_title("Johtolause-/body-analyysi: " + raw[len("Body pairing: "):])
    if raw.startswith("content_proof: "):
        return _clean_title("Sisältötodiste: " + raw[len("content_proof: "):])
    if raw.startswith("fetch/parse failed"):
        return _clean_title(raw.replace("fetch/parse failed", "haun/parsinnan epäonnistuminen", 1))
    return raw


def _publication_taxonomy(row: dict[str, Any]) -> dict[str, Any]:
    """Derive reviewer-facing taxonomy fields from a publication error row."""
    family = str(row.get("error_family") or "")
    complexity = str(row.get("error_complexity") or "")
    ready = bool(int(row.get("ready_for_clean_v1") or 0))
    html_also_wrong = bool(row.get("html_also_wrong"))

    review_category = "manual_review"
    severity = "significant"
    fixability = "manual_review"
    lawvm_status = "unknown"
    tags: list[str] = []

    if family == "xml_html_topology_drift":
        review_category = "structural_topology_drift"
        severity = "structural"
        fixability = "classification_fixable"
        tags = ["structural", "html_xml_mismatch"]
        if html_also_wrong:
            tags.append("html_also_wrong")
    elif family == "oracle_temporal_impossibility":
        review_category = "temporal_mismatch"
        severity = "temporal"
        fixability = "ingestion_fixable"
        lawvm_status = "likely_finlex_issue"
        tags = ["temporal", "oracle_version"]
    elif family == "oracle_metadata_inconsistency":
        review_category = "temporal_mismatch"
        severity = "temporal"
        fixability = "ingestion_fixable"
        lawvm_status = "not_lawvm"
        tags = ["metadata", "oracle_version"]
    elif family == "oracle_section_stale":
        review_category = "oracle_stale"
        severity = "significant"
        fixability = "lawvm_fixable"
        lawvm_status = "likely_lawvm_bug"
        tags = ["stale_oracle"]
        if complexity:
            tags.append(complexity)
    elif family == "replay_structural_diff":
        if complexity in {"extra", "missing"}:
            review_category = f"structural_{complexity}"
            severity = "structural"
            fixability = "lawvm_fixable"
            lawvm_status = "likely_lawvm_bug"
            tags = ["structural", complexity]
        elif complexity == "liite_diff":
            review_category = "attachment_diff"
            severity = "significant"
            fixability = "manual_review"
            lawvm_status = "unknown"
            tags = ["attachment", "liite"]
        elif complexity == "unknown":
            review_category = "manual_review"
            severity = "significant"
            fixability = "manual_review"
            lawvm_status = "unknown"
            tags = ["structural", "unknown"]
        else:
            review_category = "structural_diff"
            severity = "structural"
            fixability = "lawvm_fixable"
            lawvm_status = "likely_lawvm_bug"
            tags = ["structural"]
            if complexity:
                tags.append(complexity)
    elif family == "institutional_editorial_convention":
        review_category = "editorial_convention"
        severity = "editorial"
        fixability = "manual_review"
        lawvm_status = "not_lawvm"
        tags = ["editorial", "institutional"]
    elif family == "same_chapter_oracle_range_drift":
        review_category = "presentation_range"
        severity = "structural"
        fixability = "classification_fixable"
        tags = ["range_merge", "structural"]
    elif family == "cross_chapter_oracle_section_drift":
        review_category = "scope_projection"
        severity = "structural"
        fixability = "lawvm_fixable"
        lawvm_status = "likely_lawvm_bug"
        tags = ["structural", "scope_projection"]
    elif family == "source_pathology":
        review_category = "source_pathology"
        severity = "source_pathology"
        fixability = "ingestion_fixable"
        lawvm_status = "not_lawvm"
        tags = ["source_pathology"]
    elif family == "contingent_effective_date":
        review_category = "temporal_dependency"
        severity = "temporal"
        fixability = "manual_review"
        lawvm_status = "not_lawvm"
        tags = ["temporal", "contingent_effective_date"]
    elif family == "blamed_source_lacks_payload_support":
        review_category = "attribution_gap"
        severity = "significant"
        fixability = "lawvm_fixable"
        lawvm_status = "likely_lawvm_bug"
        tags = ["attribution_gap"]
    else:
        if family:
            tags.append(family)

    if not tags and family:
        tags.append(family)
    if not ready:
        tags.append("gapped")

    evidence_quality = "ready" if ready else "gapped"
    return {
        "review_category": review_category,
        "review_tags": json.dumps(sorted(set(tags)), ensure_ascii=False),
        "severity": severity,
        "fixability": fixability,
        "lawvm_status": lawvm_status,
        "evidence_quality": evidence_quality,
    }


def _section_sort_key(section: str) -> str:
    """Canonical lexical sort key for section paths."""
    return section_key_sort_text(str(section or ""))


def _is_finnish_statute_id(statute_id: str) -> bool:
    return _statute_base(statute_id) is not None


def _sid_raw_to_statute_id(sid_raw: str) -> str:
    return str(sid_raw or "").replace("_", "/", 1)


def _parse_verified_finlex_divergences_yaml(
    yaml_dir: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Parse verified_finlex_divergences/*.yaml into review records.

    Returns {(statute_id, section_path_or_empty) -> {verdict, explanation,
    reviewed_at, tier, confidence, root_cause, reviewer, auditor, audited_at}}.
    Files with ``_`` prefix are skipped (spec examples).
    """
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if not yaml_dir.is_dir():
        return result

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        print("  WARNING: pyyaml not installed — skipping verified_finlex_divergences YAML", file=sys.stderr)
        return result

    for fpath in sorted(yaml_dir.glob("[0-9]*.yaml")):
        try:
            data = yaml.safe_load(fpath.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  WARNING: skipping {fpath.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        sid = str(data.get("statute_id") or "")
        if not sid:
            continue
        verdict = str(data.get("verdict") or "")
        reviewed_at = str(data.get("reviewed_at") or "")
        summary = str(data.get("summary") or "")
        tier = str(data.get("tier") or "")
        root_cause = str(data.get("root_cause") or "")
        reviewer = str(data.get("reviewer") or "")
        auditor = str(data.get("auditor") or "")
        audited_at = str(data.get("audited_at") or "")
        statute_confidence = str(data.get("confidence") or "")

        # Statute-level entry
        if sid and verdict:
            result[(sid, "")] = {
                "verdict": verdict,
                "explanation": summary,
                "reviewed_at": reviewed_at,
                "tier": tier,
                "confidence": statute_confidence,
                "root_cause": root_cause,
                "reviewer": reviewer,
                "auditor": auditor,
                "audited_at": audited_at,
            }

        # Section-level entries
        for sec in data.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            path = str(sec.get("path") or "")
            if not path:
                continue
            sec_verdict = str(sec.get("verdict") or verdict)
            sec_explanation = str(sec.get("explanation") or "")
            sec_confidence = str(sec.get("confidence") or statute_confidence)
            if sec_verdict:
                result[(sid, path)] = {
                    "verdict": sec_verdict,
                    "explanation": sec_explanation,
                    "reviewed_at": reviewed_at,
                    "tier": tier,
                    "confidence": sec_confidence,
                    "root_cause": root_cause,
                    "reviewer": reviewer,
                    "auditor": auditor,
                    "audited_at": audited_at,
                }

    return result


def _enumerate_finnish_oracle_statute_ids() -> set[str]:
    """Mirror evidence.py's Finnish live-review statute enumeration.

    Scope must come from the Finnish oracle corpus, not from whatever bundles
    happen to exist in the cache directory.
    """
    from lawvm.corpus_store import get_corpus_store
    from lawvm.finland.transparent_store import is_known_missing_source

    corpus = get_corpus_store()
    oracle_index = corpus.oracle_path_index()
    return {
        str(sid)
        for sid in oracle_index.keys()
        if _is_finnish_statute_id(str(sid)) and not is_known_missing_source(str(sid))
    }


_STRUCTURE_CACHE_VERSION = "publication-section-structure-v11-table-aware"
_STRUCTURE_CHILD_KINDS = frozenset(
    {
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "intro",
        "heading",
        "content",
        "num",
        "p",
        "block",
    }
)
_TEXT_ONLY_KINDS = frozenset({"intro", "heading", "content", "num", "p", "block"})


@contextlib.contextmanager
def _cache_only_corpus_env():
    prior = os.environ.get("LAWVM_TRANSPARENT_CACHE_ONLY")
    os.environ["LAWVM_TRANSPARENT_CACHE_ONLY"] = "1"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("LAWVM_TRANSPARENT_CACHE_ONLY", None)
        else:
            os.environ["LAWVM_TRANSPARENT_CACHE_ONLY"] = prior


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _serialize_ir_section_node(node) -> dict[str, Any] | None:
    structure = semantic_structure_from_ir(node)
    return structure.to_dict() if structure is not None else None


def _serialize_oracle_section_node(node) -> dict[str, Any] | None:
    structure = semantic_structure_from_oracle(node)
    return structure.to_dict() if structure is not None else None


def _structure_cache_path(section_cache_dir: Path, statute_id: str, mode: str) -> Path:
    safe_statute = re.sub(r"[^A-Za-z0-9._-]+", "_", str(statute_id or "").strip()) or "statute"
    safe_mode = re.sub(r"[^A-Za-z0-9._-]+", "_", str(mode or "").strip()) or "mode"
    return section_cache_dir / f"{safe_statute}__{safe_mode}__{_STRUCTURE_CACHE_VERSION}.json"


def _try_read_section_cache(
    statute_id: str,
    mode: str,
    section_cache_dir: Path,
) -> dict[str, dict[str, Any]] | None:
    """Read a section structure cache file if it exists and is valid.

    Returns the sections dict on hit, None on miss.  Pure IO — never triggers
    replay.  Used by the main process to avoid dispatching cache-hit statutes
    to the multiprocessing pool.
    """
    cache_path = _structure_cache_path(section_cache_dir, statute_id, mode)
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        sections = payload.get("sections")
        if isinstance(sections, dict):
            return sections
    except (OSError, ValueError):
        pass
    return None


def _load_structured_section_map(
    statute_id: str,
    *,
    mode: str,
    section_cache_dir: Path,
) -> dict[str, dict[str, Any]]:
    cached = _try_read_section_cache(statute_id, mode, section_cache_dir)
    if cached is not None:
        return cached

    from lawvm.finland.corpus import get_corpus
    from lawvm.tools.structural_review import compute_statute_section_diffs

    with _cache_only_corpus_env():
        corpus = get_corpus()
        sections, _oracle_content_absent = compute_statute_section_diffs(
            statute_id,
            corpus=corpus,
            mode=mode,
        )

    cache_path = _structure_cache_path(section_cache_dir, statute_id, mode)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": _STRUCTURE_CACHE_VERSION,
                "statute_id": statute_id,
                "mode": mode,
                "sections": sections,
            },
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    return sections


def _recompute_structured_section_map(
    statute_id: str,
    *,
    mode: str,
    section_cache_dir: Path,
) -> dict[str, dict[str, Any]]:
    from lawvm.finland.corpus import get_corpus
    from lawvm.tools.structural_review import compute_statute_section_diffs

    with _cache_only_corpus_env():
        corpus = get_corpus()
        sections, _oracle_content_absent = compute_statute_section_diffs(
            statute_id,
            corpus=corpus,
            mode=mode,
        )

    cache_path = _structure_cache_path(section_cache_dir, statute_id, mode)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": _STRUCTURE_CACHE_VERSION,
                "statute_id": statute_id,
                "mode": mode,
                "sections": sections,
            },
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    return sections


def _compute_section_map_worker(
    args: tuple[str, str, str],
) -> tuple[str, dict[str, dict[str, Any]] | None]:
    """Worker for parallel section-map computation.

    Takes ``(statute_id, mode, section_cache_dir_str)`` and returns
    ``(statute_id, section_map_or_none)``.  Designed to be called via
    ``multiprocessing.Pool.imap_unordered``.
    """
    statute_id, mode, section_cache_dir_str = args
    try:
        section_map = _load_structured_section_map(
            statute_id,
            mode=mode,
            section_cache_dir=Path(section_cache_dir_str),
        )
        return (statute_id, section_map)
    except Exception as exc:
        print(
            f"WARNING: could not load structured section data for {statute_id}: {exc}",
            file=sys.stderr,
        )
        return (statute_id, None)


def _attach_section_structures(
    statute_errors: dict[str, list[dict]],
    statute_modes: dict[str, str],
    *,
    section_cache_dir: Path,
    workers: int = 0,
) -> None:
    # Collect statutes that actually have section-level rows.
    needed: list[tuple[str, str]] = []
    for statute_id, rows in statute_errors.items():
        section_rows = [row for row in rows if "section:" in str(row.get("section") or "")]
        if not section_rows:
            continue
        mode = statute_modes.get(statute_id, "legal_pit")
        needed.append((statute_id, mode))

    if not needed:
        return

    # --- Phase 1: satisfy as many statutes as possible from the on-disk
    # section structure cache without spawning any worker processes. ---
    section_maps: dict[str, dict[str, Any]] = {}
    uncached: list[tuple[str, str, str]] = []
    for statute_id, mode in needed:
        cached = _try_read_section_cache(statute_id, mode, section_cache_dir)
        if cached:
            section_maps[statute_id] = cached
        else:
            uncached.append((statute_id, mode, str(section_cache_dir)))

    cache_hits = len(section_maps)
    if cache_hits:
        print(f"  section structures: {cache_hits}/{len(needed)} from cache")

    # --- Phase 2: replay only the uncached statutes. ---
    # NOTE: Evidence bundles do NOT contain semantic structure data (they store
    # flat oracle_text/replay_text strings only).  Semantic structures require
    # parsed IR nodes and lxml oracle elements, so a full replay via
    # compute_statute_section_diffs() is unavoidable for uncached statutes.
    # TODO: To eliminate this replay entirely, the evidence pipeline
    # (evidence.py) would need to compute and persist semantic structures
    # inside the bundles during Stage 1.  That is a larger change.
    if uncached:
        effective_workers = workers if workers >= 1 else min(mp.cpu_count(), 8)

        if effective_workers == 1:
            for i, item in enumerate(uncached, 1):
                statute_id, smap = _compute_section_map_worker(item)
                if smap is not None:
                    section_maps[statute_id] = smap
                if i % 20 == 0:
                    print(f"  section structures (replay): {i}/{len(uncached)}")
        else:
            with mp.Pool(effective_workers) as pool:
                for i, (statute_id, smap) in enumerate(
                    pool.imap_unordered(_compute_section_map_worker, uncached, chunksize=8), 1
                ):
                    if smap is not None:
                        section_maps[statute_id] = smap
                    if i % 20 == 0:
                        print(f"  section structures (replay): {i}/{len(uncached)}")

    # Attach results back to the error rows.
    for statute_id, rows in statute_errors.items():
        section_map = section_maps.get(statute_id)
        if not section_map:
            continue
        for row in rows:
            if "section:" not in str(row.get("section") or ""):
                continue
            resolved_section = _resolve_section_row_key(section_map, row)
            if resolved_section and resolved_section != str(row.get("section") or ""):
                row["section"] = resolved_section
                row["section_display"] = section_display(resolved_section)
                row["section_sort_key"] = _section_sort_key(resolved_section)
            support = section_map.get(resolved_section)
            if not support:
                continue
            row.update(_structure_support_projection(support))

    repair_needed: list[tuple[str, str]] = []
    for statute_id, rows in statute_errors.items():
        mode = statute_modes.get(statute_id, "legal_pit")
        if any(
            "section:" in str(row.get("section") or "")
            and not (row.get("oracle_structure") or row.get("replay_structure"))
            for row in rows
        ):
            repair_needed.append((statute_id, mode))

    if repair_needed:
        print(f"  section structures: repairing {len(repair_needed)} statutes with incomplete cached trees")
        for statute_id, mode in repair_needed:
            section_maps[statute_id] = _recompute_structured_section_map(
                statute_id,
                mode=mode,
                section_cache_dir=section_cache_dir,
            )
        for statute_id, rows in statute_errors.items():
            section_map = section_maps.get(statute_id)
            if not section_map:
                continue
            for row in rows:
                if "section:" not in str(row.get("section") or ""):
                    continue
                if row.get("oracle_structure") or row.get("replay_structure"):
                    continue
                resolved_section = _resolve_section_row_key(section_map, row)
                if resolved_section and resolved_section != str(row.get("section") or ""):
                    row["section"] = resolved_section
                    row["section_display"] = section_display(resolved_section)
                    row["section_sort_key"] = _section_sort_key(resolved_section)
                support = section_map.get(resolved_section)
                if not support:
                    continue
                row.update(_structure_support_projection(support))


def _structure_support_projection(support: dict[str, Any]) -> dict[str, Any]:
    return semantic_support_projection(support)


def _require_section_structure_payload(
    statute_id: str,
    row: dict[str, Any],
) -> None:
    if "section:" not in str(row.get("section") or ""):
        return
    if row.get("oracle_structure") or row.get("replay_structure"):
        return
    raise RuntimeError(
        f"Section row without structured payload: {statute_id} {row.get('section')} {row.get('error_family')}"
    )


def _section_diff_row_is_real(row: dict[str, Any]) -> bool:
    """Return True when a section row should count as a real divergence."""
    diff_kind = str(row.get("structure_diff_kind") or "")
    return diff_kind not in {"identical", "editorial_only"}


# Families that already have specific classification — never reclassify these.
_ALREADY_CLASSIFIED_FAMILIES = frozenset(
    {
        "cross_chapter_oracle_section_drift",
        "corrigendum_applied",
        "oracle_cutoff_version_drift",
        "xml_html_topology_drift",
        "institutional_editorial_convention",
    }
)

# Event kinds that are purely editorial noise.
_EDITORIAL_EVENT_KINDS = frozenset(
    {
        "editorial_repeal_notice",
        "empty_oracle_shell",
        "wording_attribution_only",
    }
)


def _reclassify_error_family(row: dict[str, Any]) -> str:
    """Return the most specific error family for a section-level error row.

    Called after ``_attach_section_structures`` has populated semantic diff
    columns on the row.  Only reclassifies rows whose current family is
    ``oracle_section_stale``; all other families pass through unchanged.
    """
    current = row.get("error_family", "oracle_section_stale")
    if current in _ALREADY_CLASSIFIED_FAMILIES:
        return current

    # Check structure_diff_events JSON for specific signals.
    events_raw = row.get("structure_diff_events")
    events: list[dict[str, Any]] = []
    if events_raw:
        try:
            events = json.loads(events_raw) if isinstance(events_raw, str) else events_raw
        except (json.JSONDecodeError, TypeError):
            pass

    event_kinds = [e.get("kind", "") for e in events if isinstance(e, dict)]

    # Priority 0: deferred commencement (contingent effective date)
    # These are amendments passed but not yet in force — pending decree or
    # future fixed date.  Distinct from oracle_pending_amendment (Finlex just
    # hasn't applied a recent amendment yet).
    if any(k == "oracle_pending_amendment_suspect" for k in event_kinds):
        # Check if the underlying cause is contingent commencement
        for e in events:
            if isinstance(e, dict) and e.get("kind") == "oracle_pending_amendment_suspect":
                detail = e.get("detail", "")
                if "contingent" in str(detail).lower() or "asetuksella" in str(detail).lower():
                    return "deferred_commencement"
        return "oracle_pending_amendment"

    # Priority 2: all events are editorial (institutional convention, not a bug)
    # Per PRO_RESPONSE4_1.md: distinguish institutional_editorial_convention
    # from institutional_surface_defect, institutional_anticipatory_display,
    # and institutional_noncommensurable_surface.
    # Currently: editorial_repeal_notice / empty_oracle_shell / wording_attribution
    # are all institutional editorial conventions (not Finlex bugs).
    # deferred_commencement (above) = institutional_anticipatory_display.
    # xml_html_topology_drift (pre-classified) = institutional_noncommensurable_surface.
    # oracle_pending_amendment (above) = institutional_surface_defect (stale).
    if event_kinds and all(k in _EDITORIAL_EVENT_KINDS for k in event_kinds):
        return "institutional_editorial_convention"

    # Priority 3: identical diff (safety — should have been filtered already)
    if row.get("structure_diff_kind") == "identical":
        return current  # pass through; filtering happens elsewhere

    # Priority 4: structural differences
    structural = row.get("structure_diff_structural")
    if structural is not None and int(structural) > 0:
        return "replay_structural_diff"

    # Priority 5: text-only differences
    text_diff = row.get("structure_diff_text")
    if text_diff is not None and int(text_diff) > 0:
        return "replay_wording_diff"

    # Default: keep original
    return "oracle_section_stale"


# ---------------------------------------------------------------------------
# Bundle selection
# ---------------------------------------------------------------------------


def _select_best_bundles(cache_dir: Path, allowed_statute_ids: set[str]) -> dict[str, Path]:
    """Return {statute_id_raw: path} for the latest publication bundle per statute."""
    by_statute: dict[str, list[tuple[Path, float]]] = {}
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".json"):
            continue
        sid_raw = fname.split("__")[0]
        if _sid_raw_to_statute_id(sid_raw) not in allowed_statute_ids:
            continue
        path = cache_dir / fname
        mt = path.stat().st_mtime
        by_statute.setdefault(sid_raw, []).append((path, mt))

    best: dict[str, Path] = {}
    for sid_raw, entries in by_statute.items():
        entries.sort(key=lambda x: x[1], reverse=True)
        for path, _ in entries:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            statute_id = str(data.get("statute_id", _sid_raw_to_statute_id(sid_raw)))
            if statute_id not in allowed_statute_ids:
                continue
            if (
                "artifact_summary" in data
                or data.get("section_results")
                or data.get("proof_claims")
                or data.get("diagnosis_counts")
            ):
                best[sid_raw] = path
                break
    return best


def _select_all_bundles_latest(cache_dir: Path, allowed_statute_ids: set[str]) -> dict[str, Path]:
    """Return {statute_id_raw: path} for latest bundle per statute (any tier)."""
    by_statute: dict[str, list[tuple[Path, float]]] = {}
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".json"):
            continue
        sid_raw = fname.split("__")[0]
        if _sid_raw_to_statute_id(sid_raw) not in allowed_statute_ids:
            continue
        path = cache_dir / fname
        mt = path.stat().st_mtime
        by_statute.setdefault(sid_raw, []).append((path, mt))

    best: dict[str, Path] = {}
    for sid_raw, entries in by_statute.items():
        entries.sort(key=lambda x: x[1], reverse=True)
        best[sid_raw] = entries[0][0]
    return best


# ---------------------------------------------------------------------------
# Repealed detection
# ---------------------------------------------------------------------------


def _check_repealed(data: dict) -> bool:
    html_topo = data.get("html_topology", {})
    if isinstance(html_topo, dict):
        html_text = html_topo.get("html_snippet", "") or ""
        if "Kumottu säädöksillä" in html_text:
            return True
    return False


def _oracle_bool_flag(oracle_bytes: bytes | None, flag_name: str) -> bool | None:
    """Read a Finlex boolean metadata flag from oracle XML with a fast byte check."""
    if not oracle_bytes:
        return None
    true_token = f'{flag_name} value="true"'.encode("utf-8")
    if true_token in oracle_bytes:
        return True
    false_token = f'{flag_name} value="false"'.encode("utf-8")
    if false_token in oracle_bytes:
        return False
    return None


def _section_result_row(
    *,
    sr: dict[str, Any],
    consolidated_url: str,
) -> dict[str, Any] | None:
    """Project a raw section result into a publication row shell.

    This is the structured section-row path.  It preserves the section identity
    and decision metadata, but it does not invent any display-side fallback text
    or synthetic structure.  Structure is attached later from the semantic
    section map.
    """
    diagnosis = str(sr.get("diagnosis") or "").strip().upper()
    if not diagnosis or diagnosis == "MATCH":
        return None

    section = str(sr.get("section") or "")
    replay_text = str(sr.get("replay_text") or "")
    oracle_text = str(sr.get("oracle_text") or "")
    blame_source = str(sr.get("blame_source") or "")
    blame_title = str(sr.get("blame_title") or "")
    oracle_version = str(sr.get("oracle_version") or "")
    similarity = sr.get("similarity")

    family = "replay_structural_diff"
    complexity = diagnosis.lower()
    suspect_detail = diagnosis
    ready = 1

    if diagnosis in {"EXTRA", "REPLAY_EXTRA"}:
        family = "replay_structural_diff"
        complexity = "extra"
        suspect_detail = "Replay has a section not present in the oracle."
    elif diagnosis in {"MISSING", "REPLAY_MISSING"}:
        family = "replay_structural_diff"
        complexity = "missing"
        suspect_detail = "Oracle has a section missing from replay."
    elif diagnosis == "EDITORIAL_CONVENTION":
        family = "institutional_editorial_convention"
        complexity = "editorial_convention"
        suspect_detail = "Editorial convention / presentation noise."
    elif diagnosis == "ORACLE_STALE":
        family = "oracle_section_stale"
        complexity = "oracle_stale"
        suspect_detail = "Oracle section looks stale relative to replay."
    elif diagnosis == "LIITE_DIFF":
        family = "replay_structural_diff"
        complexity = "liite_diff"
        suspect_detail = "Liite / appendix differs."
    elif diagnosis == "CORRIGENDUM_APPLIED":
        family = "corrigendum_applied"
        complexity = "corrigendum_applied"
        ready = 1
        suspect_detail = "Verified corrigendum applies."
    else:
        family = "replay_structural_diff"
        complexity = diagnosis.lower()
        suspect_detail = f"Pykälädiagnoosi: {diagnosis}"

    return {
        "error_family": family,
        "error_complexity": complexity,
        "section": section,
        "section_display": section_display(section),
        "blame_source": blame_source,
        "blame_title": blame_title,
        "oracle_version": oracle_version,
        "oracle_text": oracle_text,
        "replay_text": replay_text,
        "similarity": similarity,
        "johtolause_text": "",
        "suspect_detail": suspect_detail,
        "is_last_touch": 1,
        "later_touches": None,
        "finlex_url": consolidated_url,
        "section_url": "",
        "amendment_url": finlex_alkup_url(blame_source) if blame_source else "",
        "ready_for_clean_v1": ready,
    }


def _semantic_node_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []
    text = str(node.get("text") or "").strip()
    if text:
        parts.append(text)
    facets = node.get("facets", {})
    if isinstance(facets, dict):
        for facet in facets.values():
            if not isinstance(facet, dict):
                continue
            facet_text = str(facet.get("text") or "").strip()
            if facet_text:
                parts.append(facet_text)
            tables = facet.get("tables", [])
            if isinstance(tables, list):
                for table in tables:
                    if not isinstance(table, dict):
                        continue
                    for row in table.get("rows", []):
                        if not isinstance(row, dict):
                            continue
                        for cell in row.get("cells", []):
                            if not isinstance(cell, dict):
                                continue
                            cell_text = str(cell.get("text") or "").strip()
                            if cell_text:
                                parts.append(cell_text)
    for child in node.get("children", []):
        parts.append(_semantic_node_text(child))
    left = node.get("left")
    right = node.get("right")
    if left is not None:
        parts.append(_semantic_node_text(left))
    if right is not None:
        parts.append(_semantic_node_text(right))
    return " ".join(part for part in parts if part).strip()


def _resolve_section_row_key(section_map: dict[str, Any], row: dict[str, Any]) -> str:
    """Resolve a row's section label to the canonical structured path if possible."""
    section = str(row.get("section") or "").strip()
    if not section:
        return ""
    try:
        resolved = resolve_section_key(section_map, section)
        matches = [resolved]
    except Exception:
        wanted = section.split(":", 1)[1] if ":" in section else section
        matches = [
            key
            for key in section_map
            if key == section or key.endswith(f"/section:{wanted}")
        ]
        if not matches:
            return section
    if len(matches) == 1:
        return matches[0]
    row_text = str(row.get("replay_text") or row.get("oracle_text") or "")
    if not row_text:
        row_text = str(row.get("suspect_detail") or "")
    best_key = matches[0]
    best_score = -1.0
    for key in matches:
        support = section_map.get(key) or {}
        candidate_text = _semantic_node_text(support.get("aligned")) or _semantic_node_text(support.get("oracle")) or _semantic_node_text(support.get("replay"))
        score = max(
            score_text_pair(row_text, candidate_text),
            score_text_pair(row_text, _semantic_node_text(support.get("oracle"))),
            score_text_pair(row_text, _semantic_node_text(support.get("replay"))),
        )
        if score > best_score:
            best_score = score
            best_key = key
    return best_key


def _exclude_from_publication_by_oracle(oracle_bytes: bytes | None) -> bool:
    """Return True when Finlex oracle explicitly says the statute is not in force."""
    is_in_force = _oracle_bool_flag(oracle_bytes, "isInForce")
    if is_in_force is False:
        return True
    is_repealed = _oracle_bool_flag(oracle_bytes, "isRepealed")
    if is_repealed is True:
        return True
    return bool(oracle_bytes and b"repealedBy" in oracle_bytes)


# ---------------------------------------------------------------------------
# First-principles amendment-only instrument classifier
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Verbs that appear exclusively in amendment johtolause.  The Finnish verb
# forms here cover the most common operative lead-in patterns.
_AMENDMENT_VERB_RE = re.compile(
    r"\b(muutetaan|kumotaan|lisätään|korvataan|siirretään|poistetaan)\b",
    re.IGNORECASE,
)

# A section is an amendment johtolause if its text:
#  - contains an amendment verb, AND
#  - references a section marker (§) or "luku", AND
#  - ends with "seuraavasti" (or is a repeal-only without body text)
# We detect the "seuraavasti:" pattern loosely — some johtolauseet may omit
# the colon or be split across elements.
_SEURAAVASTI_RE = re.compile(r"seuraavasti\s*:?\s*$", re.IGNORECASE)


def _all_text(elem: Any) -> str:
    """Concatenate all text nodes under an lxml element."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_all_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(parts)


def _section_is_amendment_johtolause(section_elem: Any) -> bool:
    """Return True if the section contains only amendment johtolause prose.

    A section is considered amendment-only when its text:
    1. Contains an amendment verb (muutetaan / kumotaan / lisätään / etc.), AND
    2. Contains a section reference ("§") or chapter reference ("luku"), AND
    3. Ends with "seuraavasti:" (the canonical johtolause terminal).

    Repeal-only johtolauseet ("kumotaan N §") may lack "seuraavasti:" — we
    also accept those when the verb is "kumotaan" and no substantive body text
    follows.
    """
    text = re.sub(r"\s+", " ", _all_text(section_elem)).strip()
    if not text:
        return True  # empty section → no substantive content
    if not _AMENDMENT_VERB_RE.search(text):
        return False
    has_section_ref = "§" in text or "luku" in text.lower()
    if not has_section_ref:
        return False
    # Classic johtolause ends with "seuraavasti:"
    if _SEURAAVASTI_RE.search(text):
        return True
    # Repeal-only: "kumotaan N §" — verb is kumotaan, no following body
    if re.search(r"\bkumotaan\b", text, re.IGNORECASE):
        return True
    return False


# Reason codes returned by _is_amendment_only_instrument_with_reason:
#   "content_absent_marker"     — hcontainer[@name="contentAbsent"] present
#   "no_sections_no_chapters"   — zero section/chapter elements in body
#   "all_sections_are_johtolause" — every section is an amendment johtolause
#   None                        — statute has substantive content


def _is_amendment_only_instrument_with_reason(
    oracle_bytes: bytes | None,
) -> tuple[bool, str | None]:
    """Classify oracle bytes; return (is_amendment_only, reason_or_None).

    Classification is first-principles: parse the oracle XML and inspect the
    structural content.  The ``contentAbsent`` marker is checked as a secondary
    corroborating signal, not as the primary criterion.

    Fast-path byte checks avoid XML parsing whenever possible:
    - No bytes → (True, "no_bytes")
    - Neither "section" nor "chapter" nor "hcontainer" in bytes → parse anyway
      (the absence of these tokens is a strong signal but not conclusive due to
      namespace variations)

    Conservative: only excludes *clear* cases.  Edge cases (e.g. one
    substantive section mixed with johtolause sections) are included.
    """
    if not oracle_bytes:
        return True, "no_bytes"

    # Secondary signal: contentAbsent marker (Finlex metadata, not authoritative)
    has_content_absent_marker = b"contentAbsent" in oracle_bytes

    # Fast path: if there are clearly no section/chapter elements AND the
    # contentAbsent marker is present, skip XML parse entirely.
    has_section_bytes = b"section" in oracle_bytes or b"chapter" in oracle_bytes
    if not has_section_bytes and has_content_absent_marker:
        return True, "content_absent_marker"

    # Parse the XML.
    try:
        root = etree.fromstring(oracle_bytes)
    except etree.XMLSyntaxError:
        # Unparseable XML: fall back to byte signal only
        if has_content_absent_marker:
            return True, "content_absent_marker"
        return False, None

    # Primary signal 1: explicit contentAbsent hcontainer anywhere in tree.
    for elem in root.iter():
        if callable(getattr(elem, "get", None)) and elem.get("name") == "contentAbsent":
            return True, "content_absent_marker"

    # Collect all section and chapter elements (namespace-wildcard).
    sections = root.findall(f".//{{{_AKN_NS}}}section")
    chapters = root.findall(f".//{{{_AKN_NS}}}chapter")

    # Also try without namespace (some documents lack it).
    if not sections:
        sections = root.findall(".//section")
    if not chapters:
        chapters = root.findall(".//chapter")

    # Primary signal 2: no section AND no chapter → empty body.
    if not sections and not chapters:
        return True, "no_sections_no_chapters"

    # Primary signal 3: ALL sections are amendment johtolause prose.
    if sections:
        if all(_section_is_amendment_johtolause(s) for s in sections):
            return True, "all_sections_are_johtolause"

    return False, None


def _is_amendment_only_instrument(oracle_bytes: bytes | None) -> bool:
    """Return True when the oracle represents an amendment-only instrument.

    A statute is amendment-only (has no independent operative content) when:
    - The oracle body carries a contentAbsent marker, OR
    - There are zero section/chapter elements, OR
    - Every section's text content is a pure amendment johtolause.

    This does NOT trust Finlex's contentAbsent metadata as the primary signal.
    It derives the classification from the actual XML structure.

    See _is_amendment_only_instrument_with_reason for the per-reason breakdown.
    """
    result, _ = _is_amendment_only_instrument_with_reason(oracle_bytes)
    return result


def _parse_finlex_page_meta(html_bytes: bytes | None) -> tuple[str, str]:
    """Extract readable title and status badge text from a Finlex HTML law page."""
    if not html_bytes:
        return "", ""
    text = html_bytes.decode("utf-8", errors="replace")

    title = ""
    title_match = re.search(
        r"<h2\b[^>]*>(.*?)</h2>\s*<span\b[^>]*>(.*?)</span>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if title_match:
        title = _strip_html_tags(title_match.group(1))
        status = _strip_html_tags(title_match.group(2))
        return title, status

    title_match = re.search(
        r"<meta\s+property=[\"']og:title[\"']\s+content=[\"']([^\"']+)[\"']",
        text,
        re.IGNORECASE,
    )
    if title_match:
        title = _clean_title(title_match.group(1).split("|", 1)[0])

    status_match = re.search(
        r"<span\b[^>]*styles_inForce[^>]*>(.*?)</span>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    status = _strip_html_tags(status_match.group(1)) if status_match else ""
    return title, status


def _html_oracle_locator(year: str, num: str) -> str:
    return f"finlex://html/ajantasa/{year}/{num}"


def _load_farchive_dict_ro(con: sqlite3.Connection, dict_id: int):
    import zstandard as zstd

    row = con.execute(
        "SELECT dict_bytes FROM dict WHERE dict_id=?",
        (dict_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"dict_id {dict_id} not found")
    return zstd.ZstdCompressionDict(bytes(row[0]))


def _read_farchive_blob_ro(con: sqlite3.Connection, digest: str) -> bytes | None:
    row = con.execute(
        "SELECT payload, codec, codec_dict_id, base_digest, raw_size FROM blob WHERE digest=?",
        (digest,),
    ).fetchone()
    if row is None:
        return None
    payload, codec, codec_dict_id, base_digest, raw_size = row
    payload = bytes(payload)

    if codec == "zstd_delta":
        if base_digest is None:
            raise ValueError(f"zstd_delta blob {digest[:16]}.. has no base_digest")
        base_raw = _read_farchive_blob_ro(con, str(base_digest))
        if base_raw is None:
            raise ValueError(f"Delta base {str(base_digest)[:16]}.. not found for blob {digest[:16]}..")
        return decompress_delta(payload, base_raw)

    if codec == "chunked":
        rows = con.execute(
            "SELECT bc.ordinal, c.payload, c.codec, c.codec_dict_id "
            "FROM blob_chunk bc JOIN chunk c ON bc.chunk_digest = c.chunk_digest "
            "WHERE bc.blob_digest = ? ORDER BY bc.ordinal",
            (digest,),
        ).fetchall()
        if not rows:
            raise ValueError(f"chunked blob {digest[:16]}.. has no chunk rows")
        parts: list[bytes] = []
        for i, chunk_row in enumerate(rows):
            ordinal, chunk_payload, chunk_codec, chunk_dict_id = chunk_row
            if ordinal != i:
                raise ValueError(
                    f"chunked blob {digest[:16]}.. has gap in chunk ordinals (expected {i}, got {ordinal})"
                )
            parts.append(
                decompress_blob(
                    bytes(chunk_payload),
                    str(chunk_codec),
                    codec_dict_id=chunk_dict_id,
                    load_dict=lambda did: _load_farchive_dict_ro(con, did),
                )
            )
        reconstructed = b"".join(parts)
        if raw_size is not None and len(reconstructed) != int(raw_size):
            raise ValueError(
                f"chunked blob {digest[:16]}.. size mismatch: expected {raw_size}, got {len(reconstructed)}"
            )
        return reconstructed

    return decompress_blob(
        payload,
        str(codec),
        codec_dict_id=codec_dict_id,
        load_dict=lambda did: _load_farchive_dict_ro(con, did),
    )


def _fetch_cached_html_oracle_ro(
    year: str,
    num: str,
    html_cache_path: Path,
) -> bytes | None:
    if not html_cache_path.exists():
        return None
    locator = _html_oracle_locator(year, num)
    con = sqlite3.connect(f"file:{html_cache_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT digest FROM locator_span WHERE locator=? AND observed_until IS NULL ORDER BY span_id DESC LIMIT 1",
            (locator,),
        ).fetchone()
        if row is None:
            return None
        return _read_farchive_blob_ro(con, str(row[0]))
    finally:
        con.close()


def _fetch_source_absent_page_meta(
    statute_id: str,
    html_cache_path: Path,
) -> tuple[str, str]:
    """Read cached Finlex HTML metadata for source-absent statute pages.

    Publication DB builds are archive-only. Cache misses degrade to empty
    metadata instead of triggering any live refresh.
    """

    raw = str(statute_id or "").strip()
    parts = raw.split("/")
    if len(parts) != 2:
        return "", ""
    year, num = parts
    if not year.isdigit() or not num:
        return "", ""
    html = _fetch_cached_html_oracle_ro(year, num, html_cache_path)
    return _parse_finlex_page_meta(html)


# ---------------------------------------------------------------------------
# Cross-chapter oracle section drift extraction (from ANY bundle)
# ---------------------------------------------------------------------------

def _roman_to_arabic_str(token: str) -> str | None:
    """Return the Arabic-string form of a Roman numeral, or None.

    Thin string-returning wrapper around ``lawvm.roman.roman_to_arabic``;
    callers in this module want a string for path-segment normalization.
    """
    value = _shared_roman_to_arabic(token)
    return None if value is None else str(value)


def _normalize_path(path: str) -> str:
    """Normalize a section path: Roman→Arabic, strip osasto→osa equivalences."""
    parts = []
    for seg in path.split("/"):
        if ":" in seg:
            kind, val = seg.split(":", 1)
            val = val.lower()
            # Strip osasto/osa suffixes for parts
            if kind == "part":
                for suf in ("osasto", "osa"):
                    if val.endswith(suf):
                        val = val[: -len(suf)]
                        break
            arabic = _roman_to_arabic_str(val)
            if arabic is not None:
                val = arabic
            parts.append(f"{kind}:{val}")
        else:
            parts.append(seg)
    return "/".join(parts)


def _extract_cross_chapter_errors(
    cache_dir: Path,
    allowed_statute_ids: set[str],
) -> dict[str, tuple[str, str, list[dict[str, Any]]]]:
    """Extract cross_chapter_oracle_section_drift from all bundles.

    Returns {statute_id: (title, url, [error_row, ...])}.
    Filters out false positives where paths are equivalent after
    Roman→Arabic normalization.
    """
    all_latest = _select_all_bundles_latest(cache_dir, allowed_statute_ids)
    result: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}

    for sid_raw, path in all_latest.items():
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        statute_id = str(data.get("statute_id", _sid_raw_to_statute_id(sid_raw)))
        if statute_id not in allowed_statute_ids:
            continue
        title = _clean_title(data.get("title") or "")
        vlinks = data.get("verification_links", {})
        consolidated_url = vlinks.get("consolidated_url") or finlex_ajantasa_url(statute_id)

        for pc in data.get("proof_claims", []):
            if pc.get("kind") != "cross_chapter_oracle_section_drift":
                continue

            sections = pc.get("support", {}).get("sections", [])
            if not sections:
                obs = pc.get("trigger_observations", [])
                if obs:
                    sections = obs[0].get("value", [])

            rows = []
            for s in sections:
                sec = s.get("section", "")
                oracle_sec = s.get("oracle_section", "")
                blame = s.get("blame_source", "")
                blame_title = s.get("blame_title", "")
                score = s.get("oracle_section_score", 0)

                # Skip false positives: Roman↔Arabic label aliases
                if _normalize_path(sec) == _normalize_path(oracle_sec):
                    continue

                detail = (
                    f"Finlexin ajantasatekstissä {section_display(sec)} "
                    f"löytyy väärästä luvusta: {section_display(oracle_sec)}. "
                    f"Sisältövastaavuus {score:.0%}."
                )

                rows.append(
                    {
                        "error_family": "cross_chapter_oracle_section_drift",
                        "error_complexity": "cross_chapter_section_mismatch",
                        "section": sec,
                        "section_display": section_display(sec),
                        "blame_source": blame,
                        "blame_title": blame_title,
                        "oracle_version": "",
                        "oracle_text": "",
                        "replay_text": "",
                        "similarity": score,
                        "johtolause_text": "",
                        "suspect_detail": detail,
                        "is_last_touch": 1,
                        "later_touches": None,
                        "finlex_url": consolidated_url,
                        "section_url": "",
                        "amendment_url": finlex_alkup_url(blame) if blame else "",
                        "ready_for_clean_v1": 1,
                    }
                )

            if rows:
                result[statute_id] = (title, consolidated_url, rows)

    return result


# ---------------------------------------------------------------------------
# Corrigendum extraction from JSONL records
# ---------------------------------------------------------------------------


def _extract_corrigendum_errors(
    allowed_statute_ids: set[str],
) -> dict[str, tuple[str, str, list[dict]]]:
    """Extract verified corrigendum records from the LawVM corrigendum pipeline."""
    try:
        from lawvm.finland.corrigendum_records import load_patch_records
    except ImportError:
        print("  WARNING: lawvm not importable, skipping corrigendum extraction")
        return {}

    records = load_patch_records()
    verified = [r for r in records if r.get("verified_in_source") == 1]

    by_statute: dict[str, list[dict]] = {}
    for rec in verified:
        sid = rec.get("statute_id", "")
        if not sid or not _is_finnish_statute_id(str(sid)) or str(sid) not in allowed_statute_ids:
            continue
        by_statute.setdefault(sid, []).append(rec)

    result: dict[str, tuple[str, str, list[dict]]] = {}
    for sid, recs in by_statute.items():
        consolidated_url = finlex_ajantasa_url(sid)
        rows = []
        for rec in recs:
            amendment = rec.get("amendment_id", "")
            wrong = rec.get("wrong_text", "")
            correct = rec.get("correct_text", "")
            corr_type = rec.get("correction_type", "")
            location = rec.get("location_desc", "")
            confidence = rec.get("llm_confidence", "")
            date_pub = rec.get("date_published", "")

            detail = f'Oikaisuilmoitus ({date_pub}): {location}. Virheellinen: "{wrong}" → Oikea: "{correct}"'

            rows.append(
                {
                    "error_family": "corrigendum_applied",
                    "error_complexity": f"corrigendum_{corr_type}",
                    "section": "",
                    "section_display": location or corr_type,
                    "blame_source": amendment,
                    "blame_title": "",
                    "oracle_version": "",
                    "oracle_text": wrong,
                    "replay_text": correct,
                    "similarity": None,
                    "johtolause_text": "",
                    "suspect_detail": detail,
                    "is_last_touch": 1,
                    "later_touches": None,
                    "finlex_url": consolidated_url,
                    "section_url": "",
                    "amendment_url": finlex_alkup_url(amendment) if amendment else "",
                    "ready_for_clean_v1": 1 if confidence == "high" else 0,
                }
            )

        if rows:
            # Use first amendment's statute as title placeholder
            result[sid] = ("", consolidated_url, rows)

    return result


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def _bulk_fetch_html_page_meta(
    statute_ids: list[str],
    html_cache_path: Path,
) -> dict[str, tuple[str, str]]:
    """Batch-read Finlex HTML page metadata for many statutes at once.

    Opens the HTML cache SQLite once and queries all locators, avoiding the
    per-statute connection overhead that was the main bottleneck.
    """
    if not html_cache_path.exists() or not statute_ids:
        return {}

    # Build locator → statute_id mapping
    locator_to_sid: dict[str, str] = {}
    for sid in statute_ids:
        parts = str(sid).split("/")
        if len(parts) == 2 and parts[0].isdigit() and parts[1]:
            locator = _html_oracle_locator(parts[0], parts[1])
            locator_to_sid[locator] = sid

    if not locator_to_sid:
        return {}

    result: dict[str, tuple[str, str]] = {}
    html_con = sqlite3.connect(f"file:{html_cache_path}?mode=ro", uri=True)
    try:
        for locator, sid in locator_to_sid.items():
            row = html_con.execute(
                "SELECT digest FROM locator_span "
                "WHERE locator=? AND observed_until IS NULL "
                "ORDER BY span_id DESC LIMIT 1",
                (locator,),
            ).fetchone()
            if row is None:
                result[sid] = ("", "")
                continue
            html_bytes = _read_farchive_blob_ro(html_con, str(row[0]))
            result[sid] = _parse_finlex_page_meta(html_bytes)
    finally:
        html_con.close()
    return result


def _load_classification_cache(
    cache_path: Path,
) -> dict[str, str | None]:
    """Load the persistent statute classification cache from disk.

    The cache maps statute_id -> reason string (or null for "substantive").
    Returns an empty dict if the cache does not exist or is unreadable.
    """
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError):
        pass
    return {}


def _save_classification_cache(
    cache_path: Path,
    cache: dict[str, str | None],
) -> None:
    """Persist the statute classification cache to disk."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, sort_keys=True)


# Default location for the amendment-only classification cache.
_CLASSIFICATION_CACHE_PATH = Path(".tmp/statute_classification_cache.json")


def _extract_title_from_oracle_xml(xml_bytes: bytes | None) -> str:
    """Extract docTitle from oracle XML bytes as a title fallback."""
    if not xml_bytes:
        return ""
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
    m = re.search(r"<docTitle>([^<]+)", text)
    if not m:
        return ""
    title = m.group(1)
    title = re.sub(r"&#\d+;", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _populate_source_absent(
    con: sqlite3.Connection,
    html_cache_path: Path,
    *,
    excluded_statute_ids: set[str] | None = None,
    oracle_bytes_by_sid: dict[str, bytes | None] | None = None,
    classification_cache_path: Path | None = None,
) -> tuple[int, int]:
    """Insert statutes that have a consolidated oracle but no source XML.

    Returns (total_oracle_indexed, total_source_absent) in publication scope.
    When ``oracle_bytes_by_sid`` is provided, avoids re-reading oracle bytes
    from farchive (they were already read during the exclusion pre-pass).

    Classification of content_absent uses first-principles XML analysis via
    ``_is_amendment_only_instrument_with_reason``.  Results are cached in
    ``classification_cache_path`` (default: ``.tmp/statute_classification_cache.json``)
    to speed up subsequent builds.
    """
    from lawvm.finland.transparent_store import is_known_missing_source
    from lawvm.finland.corpus import get_corpus

    corpus = get_corpus()
    total_oracle = 0

    # Load the persistent classification cache.
    cache_path = classification_cache_path or _CLASSIFICATION_CACHE_PATH
    classification_cache = _load_classification_cache(cache_path)
    cache_hits = 0

    # When oracle bytes were pre-cached by the caller, iterate only those —
    # the pre-pass already covers every statute in the oracle index (minus
    # known-missing-source, which are exactly the ones we want here).
    # For known-missing-source statutes we need a separate pass.
    source_absent_sids: list[str] = []
    oracle_bytes_cache: dict[str, bytes | None] = {}

    if oracle_bytes_by_sid is not None:
        # Count non-excluded from the pre-cached set (fast dict iteration).
        for sid, ob in oracle_bytes_by_sid.items():
            if excluded_statute_ids and sid in excluded_statute_ids:
                continue
            if _exclude_from_publication_by_oracle(ob):
                continue
            total_oracle += 1

        # Now find statutes that ARE known-missing-source (the point of this table).
        oracle_index = corpus.oracle_path_index()
        all_missing = sorted(
            str(sid)
            for sid in oracle_index.keys()
            if _is_finnish_statute_id(str(sid)) and is_known_missing_source(str(sid))
        )
        for sid in all_missing:
            ob = oracle_bytes_by_sid.get(sid) or corpus.read_oracle(sid)
            if _exclude_from_publication_by_oracle(ob):
                continue
            total_oracle += 1
            source_absent_sids.append(sid)
            oracle_bytes_cache[sid] = ob
    else:
        # Fallback: no pre-cached bytes — read everything (original slow path).
        oracle_index = corpus.oracle_path_index()
        all_oracle = sorted(str(sid) for sid in oracle_index.keys() if _is_finnish_statute_id(str(sid)))
        for sid in all_oracle:
            if excluded_statute_ids and sid in excluded_statute_ids:
                continue
            ob = corpus.read_oracle(sid)
            if _exclude_from_publication_by_oracle(ob):
                continue
            total_oracle += 1
            if not is_known_missing_source(sid):
                continue
            source_absent_sids.append(sid)
            oracle_bytes_cache[sid] = ob

    # Batch-read HTML page metadata (single SQLite connection).
    html_meta = _bulk_fetch_html_page_meta(source_absent_sids, html_cache_path)

    # Classification statistics: reason -> count
    reason_stats: dict[str, int] = {}
    cache_updated = False

    rows = []
    for sid in source_absent_sids:
        ob = oracle_bytes_cache[sid]
        try:
            year = int(sid.split("/")[0])
        except (ValueError, IndexError):
            year = 0
        url = finlex_lainsaadanto_url(sid)
        page_title, page_status_label = html_meta.get(sid, ("", ""))
        if not page_title:
            page_title = _extract_title_from_oracle_xml(ob)

        # First-principles classification: check cache first, then classify.
        if sid in classification_cache:
            cached_reason = classification_cache[sid]
            is_amendment_only = cached_reason is not None
            reason: str | None = cached_reason
            cache_hits += 1
        else:
            is_amendment_only, reason = _is_amendment_only_instrument_with_reason(ob)
            # Cache the result: None means "substantive" (not amendment-only).
            classification_cache[sid] = reason
            cache_updated = True

        reason_stats[reason or "substantive"] = reason_stats.get(reason or "substantive", 0) + 1
        content_absent = 1 if is_amendment_only else 0
        repealed = 1 if (ob and b"repealedBy" in ob) else 0
        rows.append((sid, year, url, page_title, page_status_label, content_absent, repealed))

    # Persist updated cache.
    if cache_updated:
        _save_classification_cache(cache_path, classification_cache)

    # Log classification statistics.
    if reason_stats:
        print(f"  Classification cache hits: {cache_hits}/{len(source_absent_sids)}")
        for reason_key, count in sorted(reason_stats.items(), key=lambda x: -x[1]):
            print(f"    {reason_key}: {count}")

    con.executemany(
        "INSERT OR REPLACE INTO source_absent "
        "(statute_id, year, consolidated_url, page_title, page_status_label, content_absent, repealed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return total_oracle, len(rows)


def _read_oracle_exclusion_worker(sid: str) -> tuple[str, bytes | None, bool]:
    """Worker: read oracle bytes and check exclusion. Each process gets its own corpus."""
    from lawvm.finland.corpus import get_corpus

    corpus = get_corpus()
    ob = corpus.read_oracle(sid)
    excluded = _exclude_from_publication_by_oracle(ob)
    return (sid, ob, excluded)


# ---------------------------------------------------------------------------
# Publication row quality gate
# ---------------------------------------------------------------------------

# Families whose viewer render branch relies on the section path + substance
# (blame / johtolause / diff text).  A row in one of these families that
# lacks both a structured section path AND all three substance fields is
# indistinguishable from a blank "?" card in the viewer.
_SECTION_CARD_FAMILIES = frozenset(
    {
        "oracle_section_stale",
        "replay_structural_diff",
        "replay_wording_diff",
        "institutional_editorial_convention",
    }
)


def _row_is_meaningful(e: dict[str, Any]) -> bool:
    """Return True when a row would produce a non-empty viewer card.

    Families with dedicated render branches (corrigendum_applied,
    oracle_cutoff_version_drift, xml_html_topology_drift,
    cross_chapter_oracle_section_drift) are always kept — they have their
    own card layouts that do not require a section path.

    For the default section-card families the rule is:
      section must contain ':' AND
      at least one of (blame_source, johtolause_text, oracle_text, replay_text)
      must be non-empty.
    """
    family = str(e.get("error_family") or "")
    if family not in _SECTION_CARD_FAMILIES:
        return True
    sec = str(e.get("section") or "")
    if ":" not in sec:
        return False
    return bool(
        e.get("blame_source")
        or e.get("johtolause_text")
        or e.get("oracle_text")
        or e.get("replay_text")
    )


def build(
    cache_dir: Path,
    output_path: Path,
    html_cache_path: Path,
    section_cache_dir: Path,
    *,
    workers: int = 0,
    classification_cache_path: Path | None = None,
) -> None:
    from lawvm.finland.corpus import get_corpus

    allowed_statute_ids = _enumerate_finnish_oracle_statute_ids()
    print(f"Enumerated Finnish oracle statutes: {len(allowed_statute_ids)}")
    corpus = get_corpus()

    # Pre-compute exclusion set and cache oracle bytes (parallel farchive reads).
    # Include known-missing-source statutes so _populate_source_absent can reuse.
    oracle_index = corpus.oracle_path_index()
    all_finnish_oracle_sids = sorted(str(sid) for sid in oracle_index.keys() if _is_finnish_statute_id(str(sid)))
    print(f"Pre-computing publication exclusions ({len(all_finnish_oracle_sids)} oracle statutes) …")
    excluded_statute_ids: set[str] = set()
    oracle_bytes_by_sid: dict[str, bytes | None] = {}
    sorted_sids = all_finnish_oracle_sids

    import multiprocessing as mp

    effective_workers = workers if workers >= 1 else min(mp.cpu_count(), 8)
    if effective_workers > 1 and len(sorted_sids) > 100:
        with mp.Pool(effective_workers) as pool:
            for i, (sid, ob, excluded) in enumerate(
                pool.imap(_read_oracle_exclusion_worker, sorted_sids, chunksize=64),
                1,
            ):
                oracle_bytes_by_sid[sid] = ob
                if excluded:
                    excluded_statute_ids.add(sid)
                if i % 2000 == 0:
                    print(f"  {i}/{len(sorted_sids)}", end="\r", flush=True)
    else:
        for i, sid in enumerate(sorted_sids):
            ob = corpus.read_oracle(sid)
            oracle_bytes_by_sid[sid] = ob
            if _exclude_from_publication_by_oracle(ob):
                excluded_statute_ids.add(sid)
            if (i + 1) % 2000 == 0:
                print(f"  {i + 1}/{len(sorted_sids)}", end="\r", flush=True)
    print(f"  Excluded: {len(excluded_statute_ids)} not-in-force statutes")

    def _exclude_statute_id(statute_id: str) -> bool:
        return statute_id in excluded_statute_ids

    print(f"Selecting best bundles from {cache_dir} …")
    best_bundles = _select_best_bundles(cache_dir, allowed_statute_ids)
    print(f"  Statutes with publication bundles: {len(best_bundles)}")

    # Phase 2 extractions
    print("Extracting cross-chapter oracle drift …")
    cross_chapter_data = _extract_cross_chapter_errors(cache_dir, allowed_statute_ids)
    print(f"  Cross-chapter statutes: {len(cross_chapter_data)}")

    print("Extracting verified corrigenda …")
    corrigendum_data = _extract_corrigendum_errors(allowed_statute_ids)
    print(f"  Corrigendum statutes: {len(corrigendum_data)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    con = sqlite3.connect(str(output_path))
    _configure_publication_db(con)
    con.executescript(_SCHEMA)

    stats = {
        "total_statutes": 0,
        "total_ready_artifacts": 0,
        "total_section_stale": 0,
        "total_cutoff_drift": 0,
        "total_topology_drift": 0,
        "total_cross_chapter": 0,
        "total_corrigendum": 0,
        "total_payload_prefers": 0,
        "total_oracle_indexed": 0,
        "total_source_absent": 0,
    }

    # Track per-statute error rows from all sources
    statute_errors: dict[str, list[dict]] = {}
    statute_meta: dict[str, dict] = {}  # statute_id -> {title, url, tier, families, ...}
    statute_modes: dict[str, str] = {}
    taxonomy_by_category: Counter[str] = Counter()
    taxonomy_by_fixability: Counter[str] = Counter()
    taxonomy_by_severity: Counter[str] = Counter()
    taxonomy_by_lawvm_status: Counter[str] = Counter()
    # Per-section amendment chains: (statute_id, section_key) -> [(amendment_id, title)]
    section_chains: dict[tuple[str, str], list[tuple[str, str]]] = {}
    # Per-error blame sources for marking chain entries
    section_blame: dict[tuple[str, str], str] = {}
    section_later: dict[tuple[str, str], set[str]] = {}

    # --- Pass 1: PROVED bundles (original families) ---
    for sid_raw, bundle_path in sorted(best_bundles.items()):
        with open(bundle_path, encoding="utf-8") as fh:
            data = json.load(fh)

        statute_id = str(data.get("statute_id", _sid_raw_to_statute_id(sid_raw)))
        if statute_id not in allowed_statute_ids:
            continue
        if _exclude_statute_id(statute_id):
            continue
        statute_modes[statute_id] = str(data.get("mode") or "legal_pit")
        title = _clean_title(data.get("title") or "")
        artifact_summary = data.get("artifact_summary", {})
        families = sorted(artifact_summary.get("by_family", {}).keys())
        ready_count = int(artifact_summary.get("ready_total_artifact_count", 0))
        vlinks = data.get("verification_links", {})
        consolidated_url = vlinks.get("consolidated_url") or finlex_ajantasa_url(statute_id)
        is_repealed = 1 if _check_repealed(data) else 0

        error_rows: list[dict] = []

        # --- Section-level structural errors ---
        for sr in data.get("section_results", []):
            if str(sr.get("diagnosis") or "").strip().upper() == "MATCH":
                continue
            if bool(sr.get("oracle_content_absent")):
                continue
            row = _section_result_row(sr=sr, consolidated_url=consolidated_url)
            if row is None:
                continue
            sec = row["section"]
            blame = row.get("blame_source") or ""
            # Skip rows with no usable section path and no supporting evidence.
            # An empty section or a non-structured label (e.g. 'liitteet') with
            # no blame attribution and no oracle/replay text has zero evidentiary
            # value and produces blank "?" cards in the viewer.
            if not sec:
                continue
            # Unconditionally exclude known non-provision spam labels.
            # 'liitteet' = annexes/attachments section (not a structured provision).
            # '?' = unresolvable address (no provenance, no actionable target).
            # These inflate error_count and produce blank cards regardless of blame.
            if sec in ("liitteet", "?"):
                continue
            if ":" not in sec and not blame and not (row.get("oracle_text") or row.get("replay_text")):
                continue

            bv = _version_sort_key(blame)
            later: list[str] = []
            if blame:
                for sa in data.get("supporting_amendments", []):
                    if sec in sa.get("blamed_sections", []):
                        if _version_sort_key(sa["amendment_id"]) > bv:
                            later.append(sa["amendment_id"])
            is_last = 0 if later else 1

            j_text = sr.get("blame_source_johtolause") or ""
            # Skip rows with no johtolause text and no blame attribution.
            # These produce proof cards with zero actionable content ("Johtolauseteksti ei saatavilla").
            if not j_text and not blame:
                continue
            j_span = _johtolause_section_char_span(j_text, sec) if j_text else None
            row.update(
                {
                    "blame_title": sr.get("blame_source_title") or sr.get("blame_title") or "",
                    "johtolause_text": j_text,
                    "johtolause_char_span": json.dumps(list(j_span)) if j_span else None,
                    "is_last_touch": is_last,
                    "later_touches": json.dumps(later) if later else None,
                    "section_url": sr.get("section_url") or "",
                    "amendment_url": sr.get("blame_source_url") or (finlex_alkup_url(blame) if blame else ""),
                }
            )
            error_rows.append(row)

        # --- Collect per-section amendment chains ---
        for sa in data.get("supporting_amendments", []):
            aid = str(sa.get("amendment_id") or "")
            atitle = str(sa.get("source_title") or "")
            if not aid:
                continue
            for sec in sa.get("blamed_sections", []):
                sec = str(sec)
                if not sec:
                    continue
                key = (statute_id, sec)
                section_chains.setdefault(key, []).append((aid, atitle))
        # Record blame sources for later marking
        for row in error_rows:
            sec = row.get("section") or ""
            blame = row.get("blame_source") or ""
            if sec and blame:
                key = (statute_id, sec)
                section_blame[key] = blame
                lt = row.get("later_touches")
                if lt:
                    section_later.setdefault(key, set()).update(json.loads(lt) if isinstance(lt, str) else lt)

        # --- Statute-level cutoff version drift (content-verified only) ---
        # oracle_metadata_inconsistency is a pure metadata heuristic — it fires
        # for statutes that don't even exist on Finlex consolidated.  Only emit
        # version drift findings backed by content evidence.
        for pc in data.get("proof_claims", []):
            pc_kind = pc.get("kind") or ""
            if pc_kind == "oracle_cutoff_version_drift":
                support = pc.get("support", {})
                behind_by = int(support.get("behind_by") or 0)
                unapplied = list(support.get("unapplied") or [])
                matched_at = str(support.get("matched_at") or "")
                suspect = (
                    f"content_proof: behind_by={behind_by} matched_at={matched_at} unapplied={','.join(unapplied)}"
                )
                family = "oracle_cutoff_version_drift"
                error_rows.append(
                    {
                        "error_family": family,
                        "error_complexity": "statute_cutoff_version_drift",
                        "section": "",
                        "section_display": "Koko säädös",
                        "blame_source": "",
                        "blame_title": "",
                        "oracle_version": "",
                        "oracle_text": "",
                        "replay_text": "",
                        "similarity": None,
                        "johtolause_text": "",
                        "suspect_detail": suspect,
                        "is_last_touch": 1,
                        "later_touches": None,
                        "finlex_url": consolidated_url,
                        "section_url": "",
                        "amendment_url": "",
                        "ready_for_clean_v1": 1,
                    }
                )
                stats["total_cutoff_drift"] += 1
                break

        # --- XML/HTML topology drift ---
        html_topology = data.get("html_topology", {})
        html_error = ""
        if isinstance(html_topology, dict):
            html_error = str(html_topology.get("html_error") or "").strip()
        for pc in data.get("proof_claims", []):
            if html_error:
                continue
            if pc.get("kind") == "xml_html_topology_drift":
                topo_support = pc.get("support") or {}
                missing_from_xml = [str(v) for v in topo_support.get("html_missing_from_xml", []) if str(v)]
                extra_in_xml = [str(v) for v in topo_support.get("html_extra_in_xml", []) if str(v)]
                error_rows.append(
                    {
                        "error_family": "xml_html_topology_drift",
                        "error_complexity": "xml_html_topology_drift",
                        "section": "",
                        "section_display": "XML/HTML-rakennevirhe",
                        "blame_source": "",
                        "blame_title": "",
                        "oracle_version": "",
                        # Reuse oracle_text/replay_text (unused for this family) to carry
                        # the section-label lists for the viewer triple-view panel.
                        "oracle_text": json.dumps(missing_from_xml, ensure_ascii=False) if missing_from_xml else "",
                        "replay_text": json.dumps(extra_in_xml, ensure_ascii=False) if extra_in_xml else "",
                        "similarity": None,
                        "johtolause_text": "",
                        "suspect_detail": "Finlexin verkkosivun HTML ja XML-rajapinnan rakenne eroavat toisistaan pykälätasolla.",
                        "is_last_touch": 1,
                        "later_touches": None,
                        "finlex_url": consolidated_url,
                        "section_url": "",
                        "amendment_url": "",
                        "ready_for_clean_v1": 1,
                    }
                )
                stats["total_topology_drift"] += 1
                break

        if error_rows:
            statute_errors.setdefault(statute_id, []).extend(error_rows)
            statute_meta[statute_id] = {
                "title": title,
                "tier": "PROVED_ORACLE_INCORRECT",
                "families": families,
                "ready_count": ready_count,
                "url": consolidated_url,
                "is_repealed": is_repealed,
            }

    # --- Pass 2: Cross-chapter oracle section drift ---
    for statute_id, (title, url, rows) in cross_chapter_data.items():
        if _exclude_statute_id(statute_id):
            continue
        statute_errors.setdefault(statute_id, []).extend(rows)
        statute_modes.setdefault(statute_id, "legal_pit")
        stats["total_cross_chapter"] += len(rows)
        if statute_id not in statute_meta:
            statute_meta[statute_id] = {
                "title": title,
                "tier": "CROSS_CHAPTER_DRIFT",
                "families": [],
                "ready_count": 0,
                "url": url,
                "is_repealed": 0,
            }
        meta = statute_meta[statute_id]
        if "cross_chapter_oracle_section_drift" not in meta["families"]:
            meta["families"] = sorted(set(meta["families"]) | {"cross_chapter_oracle_section_drift"})

    # --- Pass 3: Corrigendum ---
    for statute_id, (title, url, rows) in corrigendum_data.items():
        if _exclude_statute_id(statute_id):
            continue
        statute_errors.setdefault(statute_id, []).extend(rows)
        statute_modes.setdefault(statute_id, "legal_pit")
        stats["total_corrigendum"] += len(rows)
        if statute_id not in statute_meta:
            statute_meta[statute_id] = {
                "title": title,
                "tier": "CORRIGENDUM",
                "families": [],
                "ready_count": 0,
                "url": url,
                "is_repealed": 0,
            }
        meta = statute_meta[statute_id]
        if "corrigendum_applied" not in meta["families"]:
            meta["families"] = sorted(set(meta["families"]) | {"corrigendum_applied"})

    print("Attaching structured section trees …")
    _attach_section_structures(
        statute_errors,
        statute_modes,
        section_cache_dir=section_cache_dir,
        workers=workers,
    )

    # --- Reclassify section-level error families using semantic diff data ---
    reclassified = 0
    for _sid, rows in statute_errors.items():
        for row in rows:
            if "section:" not in str(row.get("section") or ""):
                continue
            old_family = row.get("error_family", "oracle_section_stale")
            new_family = _reclassify_error_family(row)
            if new_family != old_family:
                row["error_family"] = new_family
                reclassified += 1
    if reclassified:
        print(f"  Reclassified {reclassified} section errors into specific families")

    # --- Filter sections where semantic diff is not a real divergence ---
    # The structured section diff (computed fresh when available) is the
    # authoritative signal for whether there is any real divergence.  Drop
    # rows whose diff kind is identical or editorial-only tombstones.
    _FILTER_DIFF_KINDS = {"identical", "editorial_only"}
    filtered_count = 0
    for statute_id, rows in list(statute_errors.items()):
        original_len = len(rows)
        rows[:] = [
            r
            for r in rows
            if "section:" not in str(r.get("section") or "")
            or _section_diff_row_is_real(r)
        ]
        filtered_count += original_len - len(rows)
        if not rows:
            del statute_errors[statute_id]
    if filtered_count:
        print(f"  Filtered {filtered_count} section rows with identical or absent semantic diff")

    # --- Write all statutes and errors ---
    skipped_unstructured_sections = 0
    skipped_empty_cards = 0
    for statute_id, rows in sorted(statute_errors.items()):
        meta = statute_meta[statute_id]

        # Pre-insert statutes row with a placeholder count of 0.  We will
        # UPDATE it after the error loop with the real inserted count and the
        # real family breakdown.  This avoids the mismatch between pre-filter
        # len(rows) and the post-filter count that actually reaches the DB.
        # FK enforcement is off by default in SQLite, so errors can be inserted
        # before the final UPDATE without violating constraints.
        con.execute(
            "INSERT OR REPLACE INTO statutes "
            "(statute_id, title, statute_sort_key, primary_proof_tier, error_count, ready_artifact_count, "
            "error_families, error_family_counts, consolidated_url, is_repealed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                statute_id,
                meta["title"],
                _statute_sort_key(statute_id),
                meta["tier"],
                0,  # placeholder — updated below after error loop
                meta["ready_count"],
                json.dumps([]),  # placeholder — updated below
                json.dumps({}),  # placeholder — updated below
                meta["url"],
                meta["is_repealed"],
            ),
        )

        actual_inserted = 0
        actual_families: set[str] = set(meta["families"])
        actual_family_counts: Counter[str] = Counter()

        for e in rows:
            # Final safety net: skip rows that would render as blank cards.
            # This catches any path (reclassification, cross-chapter, etc.) that
            # produces a section-card-family row without usable content.
            if not _row_is_meaningful(e):
                skipped_empty_cards += 1
                continue

            taxonomy = _publication_taxonomy(e)
            suspect_detail = _localize_public_text(e.get("suspect_detail"))
            structure_diff_summary = _localize_public_text(e.get("structure_diff_summary"))
            try:
                _require_section_structure_payload(statute_id, e)
            except RuntimeError:
                if "section:" in str(e.get("section") or ""):
                    skipped_unstructured_sections += 1
                    continue
                raise

            error_columns = (
                "statute_id, error_family, error_complexity, review_category, review_tags, severity, fixability, lawvm_status, evidence_quality, "
                "section, section_display, section_sort_key, section_sort_rank, "
                "blame_source, blame_title, oracle_version, oracle_text, replay_text, "
                "oracle_display_text, replay_display_text, "
                "similarity, johtolause_text, suspect_detail, is_last_touch, later_touches, "
                "finlex_url, section_url, amendment_url, semantic_contract_version, oracle_structure, replay_structure, aligned_structure, "
                "structure_diff_kind, structure_diff_summary, structure_diff_structural, structure_diff_label, structure_diff_text, structure_diff_events, "
                "ready_for_clean_v1, html_also_wrong, johtolause_char_span"
            )
            con.execute(
                f"INSERT INTO errors ({error_columns}) "
                f"VALUES ({', '.join(['?'] * 41)})",
                (
                    statute_id,
                    e["error_family"],
                    e["error_complexity"],
                    taxonomy["review_category"],
                    taxonomy["review_tags"],
                    taxonomy["severity"],
                    taxonomy["fixability"],
                    taxonomy["lawvm_status"],
                    taxonomy["evidence_quality"],
                    e["section"],
                    e["section_display"],
                    _section_sort_key(e["section"]),
                    0 if str(e["section"] or '').strip() else 1,
                    e["blame_source"],
                    e["blame_title"],
                    e["oracle_version"],
                    e["oracle_text"],
                    e["replay_text"],
                    _normalize_display_diff_text(e["oracle_text"]),
                    _normalize_display_diff_text(e["replay_text"]),
                    e["similarity"],
                    e["johtolause_text"],
                    suspect_detail,
                    e["is_last_touch"],
                    e["later_touches"],
                    e["finlex_url"],
                    e["section_url"],
                    e["amendment_url"],
                    e.get("semantic_contract_version"),
                    e.get("oracle_structure"),
                    e.get("replay_structure"),
                    e.get("aligned_structure"),
                    e.get("structure_diff_kind"),
                    structure_diff_summary,
                    e.get("structure_diff_structural"),
                    e.get("structure_diff_label"),
                    e.get("structure_diff_text"),
                    e.get("structure_diff_events"),
                    e["ready_for_clean_v1"],
                    None,
                    e.get("johtolause_char_span"),
                ),
            )
            actual_inserted += 1
            actual_families.add(e["error_family"])
            actual_family_counts[e["error_family"]] += 1
            taxonomy_by_category[taxonomy["review_category"]] += 1
            taxonomy_by_fixability[taxonomy["fixability"]] += 1
            taxonomy_by_severity[taxonomy["severity"]] += 1
            taxonomy_by_lawvm_status[taxonomy["lawvm_status"]] += 1

        # Update the statutes row with actual post-filter counts.
        # If nothing was inserted (all rows were filtered), the statute row
        # stays with error_count=0 rather than an inflated pre-filter value.
        section_stale = actual_family_counts.get("oracle_section_stale", 0)
        stats["total_section_stale"] += section_stale
        stats["total_statutes"] += 1
        stats["total_ready_artifacts"] += actual_inserted
        con.execute(
            "UPDATE statutes SET error_count=?, error_families=?, error_family_counts=? "
            "WHERE statute_id=?",
            (
                actual_inserted,
                json.dumps(sorted(actual_families)),
                json.dumps(dict(sorted(actual_family_counts.items()))),
                statute_id,
            ),
        )

    if skipped_unstructured_sections:
        print(f"  Skipped {skipped_unstructured_sections} section rows without structured payload")
    if skipped_empty_cards:
        print(f"  Skipped {skipped_empty_cards} section rows that would produce empty viewer cards")

    # --- Write section amendment chains ---
    chain_count = 0
    for (sid, sec), entries in sorted(section_chains.items()):
        # Deduplicate and sort chronologically
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for aid, atitle in entries:
            if aid not in seen:
                seen.add(aid)
                unique.append((aid, atitle))
        unique.sort(key=lambda x: _version_sort_key(x[0]))
        blame = section_blame.get((sid, sec), "")
        later = list(section_later.get((sid, sec), set()))
        for ord_idx, (aid, atitle) in enumerate(unique, 1):
            con.execute(
                "INSERT OR REPLACE INTO section_amendment_chain "
                "(statute_id, section_key, amendment_id, amendment_ord, "
                "amendment_title, is_blame_source, is_later_touch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, sec, aid, ord_idx, atitle or None, 1 if aid == blame else 0, 1 if aid in later else 0),
            )
            chain_count += 1
    if chain_count:
        print(f"  Section amendment chains: {chain_count} entries")

    print("Populating source_absent table …")
    total_oracle, total_absent = _populate_source_absent(
        con,
        html_cache_path,
        excluded_statute_ids=excluded_statute_ids,
        oracle_bytes_by_sid=oracle_bytes_by_sid,
        classification_cache_path=classification_cache_path,
    )
    stats["total_oracle_indexed"] = total_oracle
    stats["total_source_absent"] = total_absent
    print(f"  Oracle indexed: {total_oracle}, source absent: {total_absent}")

    # Populate manual reviews from verified_finlex_divergences YAML
    print("Populating manual_reviews table …")
    yaml_dir = Path(__file__).parent.parent / "notes" / "verified_finlex_divergences"
    manual_reviews = _parse_verified_finlex_divergences_yaml(yaml_dir)
    if manual_reviews:
        print(f"  Verified YAML entries: {len(manual_reviews)}")

    review_count = 0
    for (statute_id, section), review_data in manual_reviews.items():
        con.execute(
            "INSERT OR REPLACE INTO manual_reviews "
            "(statute_id, section, verdict, explanation, reviewed_at, "
            " tier, confidence, root_cause, reviewer, auditor, audited_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                statute_id,
                section,
                review_data.get("verdict", ""),
                review_data.get("explanation", ""),
                review_data.get("reviewed_at", ""),
                review_data.get("tier", ""),
                review_data.get("confidence", ""),
                review_data.get("root_cause", ""),
                review_data.get("reviewer", ""),
                review_data.get("auditor", ""),
                review_data.get("audited_at", ""),
            ),
        )
        review_count += 1
    if review_count:
        print(f"  Manual reviews: {review_count} entries")

    if taxonomy_by_category:
        print("Review taxonomy:")
        print(
            "  categories: "
            + ", ".join(f"{name}={count}" for name, count in sorted(taxonomy_by_category.items()))
        )
        print(
            "  fixability: "
            + ", ".join(f"{name}={count}" for name, count in sorted(taxonomy_by_fixability.items()))
        )
        print(
            "  severity: "
            + ", ".join(f"{name}={count}" for name, count in sorted(taxonomy_by_severity.items()))
        )
        print(
            "  lawvm_status: "
            + ", ".join(f"{name}={count}" for name, count in sorted(taxonomy_by_lawvm_status.items()))
        )

    con.execute(
        "INSERT INTO corpus_stats "
        "(total_statutes, total_ready_artifacts, total_section_stale, "
        "total_cutoff_drift, total_topology_drift, total_cross_chapter, "
        "total_corrigendum, total_payload_prefers, review_category_counts, "
        "total_oracle_indexed, total_source_absent, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            stats["total_statutes"],
            stats["total_ready_artifacts"],
            stats["total_section_stale"],
            stats["total_cutoff_drift"],
            stats["total_topology_drift"],
            stats["total_cross_chapter"],
            stats["total_corrigendum"],
            stats["total_payload_prefers"],
            json.dumps(dict(sorted(taxonomy_by_category.items()))),
            stats["total_oracle_indexed"],
            stats["total_source_absent"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    con.commit()
    con.close()

    print()
    print("=== Publication DB built ===")
    print(f"  Output:              {output_path}")
    print(f"  Statutes:            {stats['total_statutes']}")
    print(f"  Total errors:        {stats['total_ready_artifacts']}")
    print(f"  Section stale:       {stats['total_section_stale']}")
    print(f"  Cutoff drift:        {stats['total_cutoff_drift']}")
    print(f"  Topology drift:      {stats['total_topology_drift']}")
    print(f"  Cross-chapter:       {stats['total_cross_chapter']}")
    print(f"  Corrigendum:         {stats['total_corrigendum']}")
    print(f"  Oracle indexed:      {stats['total_oracle_indexed']}")
    print(f"  Source absent:       {stats['total_source_absent']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_absent_ajantasa_scan(db_path: Path, farchive_path: Path) -> None:
    """Run the absent_ajantasa scan and append tables to the publication DB."""
    try:
        from scan_absent_ajantasa import scan_farchive, write_publication_db, _load_corrections
    except ImportError:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "scan_absent_ajantasa",
            Path(__file__).parent / "scan_absent_ajantasa.py",
        )
        if spec is None or spec.loader is None:
            print("WARNING: could not import scan_absent_ajantasa — skipping absent scan", file=sys.stderr)
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        scan_farchive = mod.scan_farchive
        write_publication_db = mod.write_publication_db
        _load_corrections = mod._load_corrections

    corrections_path = Path("data/finlex_metadata_corrections.yaml")
    corrections = _load_corrections(corrections_path) if corrections_path.exists() else {}
    if corrections:
        print(f"  Loaded {len(corrections)} metadata corrections")

    results = scan_farchive(farchive_path, corrections=corrections)
    if results:
        write_publication_db(results, db_path, corrections)
        amended_acts = sum(1 for r in results if r["type_statute"] == "act" and r["is_amended"])
        print(f"  Absent ajantasa: {len(results)} total, {amended_acts} amended acts")
    else:
        print("  No absent ajantasa statutes found")


def _print_preflight_instructions(cache_dir: Path, output_path: Path) -> None:
    print("=== build_publication_db.py preflight ===")
    print("Expected scope: Finnish oracle-corpus statutes only.")
    print(f"Expected cache dir: {cache_dir}")
    print("Expected cache contents: evidence-review bundles from evidence.py.")
    print("Populate or refresh that cache first with:")
    print(
        f"  uv run lawvm evidence-review -j fi --oracle-corpus --bundle-cache-dir {cache_dir} --workers 16 --cache-only"
    )
    print("Then run:")
    print(f"  uv run scripts/build_publication_db.py --cache-dir {cache_dir} --output {output_path}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build publication DB from evidence-review publication bundles.")
    parser.add_argument(
        "--cache-dir",
        default=".tmp/evidence_bundle_cache/",
        help="Evidence bundle cache dir (default: .tmp/evidence_bundle_cache/)",
    )
    parser.add_argument(
        "--output",
        default=".tmp/finlex_errors_publication.db",
        help="Output SQLite path (default: .tmp/finlex_errors_publication.db)",
    )
    parser.add_argument(
        "--html-cache",
        default=".tmp/finlex_publication_html_cache.farchive",
        help="Farchive cache for Finlex HTML enrichment (default: .tmp/finlex_publication_html_cache.farchive)",
    )
    parser.add_argument(
        "--section-cache-dir",
        default=".tmp/publication_section_structure_cache",
        help="Per-statute structured section cache dir (default: .tmp/publication_section_structure_cache)",
    )
    parser.add_argument(
        "--skip-absent-scan",
        action="store_true",
        help="Skip the absent ajantasa scan step",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Number of parallel workers for section structure computation "
            "(default: 0 = min(cpu_count, 8); use 1 to run sequentially)"
        ),
    )
    parser.add_argument(
        "--classification-cache",
        default=".tmp/statute_classification_cache.json",
        help=(
            "Persistent JSON cache for amendment-only instrument classification "
            "(default: .tmp/statute_classification_cache.json)"
        ),
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.exists():
        print(f"ERROR: cache dir does not exist: {cache_dir}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    html_cache_path = Path(args.html_cache)
    section_cache_dir = Path(args.section_cache_dir)
    classification_cache_path = Path(args.classification_cache)
    html_cache_path.parent.mkdir(parents=True, exist_ok=True)
    section_cache_dir.mkdir(parents=True, exist_ok=True)
    _print_preflight_instructions(cache_dir, output_path)
    build(
        cache_dir,
        output_path,
        html_cache_path,
        section_cache_dir,
        workers=args.workers,
        classification_cache_path=classification_cache_path,
    )

    # Step 2: absent ajantasa scan
    if not args.skip_absent_scan:
        farchive_path = Path("data/finlex.farchive")
        if farchive_path.exists():
            print("\n=== Absent ajantasa scan ===")
            _run_absent_ajantasa_scan(output_path, farchive_path)
        else:
            print(f"\nWARNING: farchive not found at {farchive_path} — skipping absent scan", file=sys.stderr)

    print(f"Built publication database at {output_path}")


if __name__ == "__main__":
    main()
