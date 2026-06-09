"""lawvm oracle-text — fetch oracle consolidated section text at a specific version.

Reads the Finnish consolidated oracle XML (sd-cons) from the archive at either
the current selected oracle version or at the version pinned to a specific
amendment, and prints the section text with optional subsection breakdown.

This covers the gap where farchive cat + hand-rolled regex was the only way to
inspect oracle section text at a specific consolidated version snapshot.

Usage:
    lawvm oracle-text 2017/530 --section section:2
    lawvm oracle-text 2017/530 --section section:2 --at-amendment 2020/959
    lawvm oracle-text 2017/530 --section section:2 --subsections
    lawvm oracle-text 2017/530 --section section:2 --at-amendment 2020/959 --json
    lawvm oracle-text 2017/530                          # list all section labels
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import lawvm.finland.section_resolver  # noqa: F401 — registers FI section resolver at import time

# Once-per-process gate for Task-N discovery hint (stateless: no files, no session state).
_HINT_EMITTED: bool = False

# Snapshots older than this many years trigger the coverage caveat on stderr.
# Uses the cutoff date of the last-included amendment in the ingested oracle chain.
_STALE_SNAPSHOT_YEARS = 2


def _amendment_id_to_version_tag(amendment_id: str) -> str:
    """Convert '2020/959' → '20200959' (YYYY + zero-padded 4-digit number)."""
    year, num = amendment_id.strip().split("/")
    return f"{year}{int(num):04d}"


def _el_to_text(el: Any) -> str:
    """Extract plain text from an lxml element."""
    from lxml import etree
    raw = etree.tostring(el, method="text", encoding="unicode")
    return re.sub(r"\s+", " ", raw).strip()


# --- Temporal labeling (opt-in, presentation-only) -------------------------
#
# Finlex consolidated (sd-cons) XML structurally marks amendment versions and
# editorial notes inside a <section>.  The default flattened `full_text` mixes
# in-force, soon-to-be-superseded, and future-in-force wording into one blob,
# which is ambiguous for a reader.  The `--temporal-labels` flag walks the
# section's direct children and emits labeled spans using ONLY structural
# signals already present in the source XML:
#
#   • <subsection finlex:originalVersion="@YYYYNNNN"
#                 finlex:originalVersionLabel="D.M.YYYY/NNN"> — a version-pinned
#     amended/added provision.  Whether it is already IN FORCE or ENTERS FORCE
#     in the future is decided by the "tulee voimaan <date>" date carried in the
#     adjacent editorial note (compared to today).
#   • <hcontainer name="noteAuthorial" finlex:outline="huomautus"> — an editorial
#     note (e.g. "L:lla 269/2026 muutettu 1 momentti tulee voimaan 1.6.2026.
#     Aiempi sanamuoto kuuluu:").
#   • a bare <subsection> (no version attrs) IMMEDIATELY following a note whose
#     text contains "Aiempi sanamuoto kuuluu" — the SUPERSEDED prior wording.
#
# This mirrors the structural model already used by
# tools/section_keys.py (_strip_inline_prior_wording_sibling /
# _is_oracle_version_shadow_section).  It is presentation-only: the default
# `full_text` output is untouched.

_FINLEX_NS = "http://data.finlex.fi/schema/finlex"
_FINLEX_ORIG_VERSION_ATTR = f"{{{_FINLEX_NS}}}originalVersion"
_FINLEX_ORIG_VERSION_LABEL_ATTR = f"{{{_FINLEX_NS}}}originalVersionLabel"
_AIEMPI_SANAMUOTO_RE = re.compile(r"\bAiempi sanamuoto kuuluu\b", re.IGNORECASE)
# "tulee voimaan 1.6.2026" — the entering-into-force date carried by the note.
_TULEE_VOIMAAN_DATE_RE = re.compile(
    r"tulee voimaan\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE
)


def _local_tag(el: Any) -> str:
    from lxml import etree
    return etree.QName(el).localname


def _is_authorial_note(el: Any) -> bool:
    return _local_tag(el) == "hcontainer" and el.get("name") == "noteAuthorial"


def _note_introduces_prior_wording(note_el: Any) -> bool:
    return bool(_AIEMPI_SANAMUOTO_RE.search(_el_to_text(note_el)))


def _note_enters_force_date(note_el: Any) -> Optional[datetime.date]:
    """Parse 'tulee voimaan D.M.YYYY' from a note, or None if absent/unparseable."""
    m = _TULEE_VOIMAAN_DATE_RE.search(_el_to_text(note_el))
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def build_temporal_spans(
    section_el: Any, today: Optional[datetime.date] = None
) -> List[Dict[str, Any]]:
    """Walk a section's direct children → ordered, labeled temporal spans.

    Each span is a dict with keys:
      - label:   one of "IN_FORCE", "ENTERS_FORCE", "SUPERSEDED", "NOTE",
                 "CURRENT" (a bare in-force subsection with no temporal marker).
      - text:    the flattened text of the span.
      - version: finlex:originalVersionLabel for versioned subsections, else "".
      - enters_force_date: ISO date string for ENTERS_FORCE spans, else None.

    Structural-only: no semantic interpretation beyond the markers Finlex
    already encodes.  The most-recent note's "tulee voimaan" date is carried
    forward to label the version-pinned subsection that the note refers to.
    """
    if today is None:
        today = datetime.date.today()

    spans: List[Dict[str, Any]] = []
    children = list(section_el)
    # In Finlex sd-cons, the editorial note ("... tulee voimaan <date>. Aiempi
    # sanamuoto kuuluu:") is placed AFTER the version-pinned subsection it
    # refers to and BEFORE the prior-wording subsection it introduces.  So a
    # versioned subsection's in-force/future status comes from the note that
    # immediately FOLLOWS it, while a SUPERSEDED bare subsection comes from the
    # note that immediately PRECEDES it.
    prior_wording_armed = False  # set by the immediately-preceding note

    def _following_note_enters_force(idx: int) -> Optional[datetime.date]:
        """Date from the next authorial note before the next subsection, if any."""
        for j in range(idx + 1, len(children)):
            nxt = children[j]
            ntag = _local_tag(nxt)
            if _is_authorial_note(nxt):
                return _note_enters_force_date(nxt)
            if ntag == "subsection":
                break
        return None

    for i, child in enumerate(children):
        tag = _local_tag(child)
        if tag in ("num", "heading"):
            continue

        if _is_authorial_note(child):
            enters = _note_enters_force_date(child)
            spans.append(
                {
                    "label": "NOTE",
                    "text": _el_to_text(child),
                    "version": "",
                    "enters_force_date": enters.isoformat() if enters else None,
                }
            )
            prior_wording_armed = _note_introduces_prior_wording(child)
            continue

        if tag == "subsection":
            version_label = child.get(_FINLEX_ORIG_VERSION_LABEL_ATTR) or ""
            has_version = bool(
                child.get(_FINLEX_ORIG_VERSION_ATTR) or version_label
            )
            text = _el_to_text(child)

            if not has_version and prior_wording_armed:
                # Bare subsection right after an "Aiempi sanamuoto kuuluu:" note.
                spans.append(
                    {
                        "label": "SUPERSEDED",
                        "text": text,
                        "version": "",
                        "enters_force_date": None,
                    }
                )
                # The prior wording is a single subsection; disarm afterwards.
                prior_wording_armed = False
                continue

            if has_version:
                # In force vs future depends on the FOLLOWING note's date
                # (the note that announces when this version takes effect).
                enters_force = _following_note_enters_force(i)
                if enters_force is not None and enters_force > today:
                    label = "ENTERS_FORCE"
                else:
                    label = "IN_FORCE"
                spans.append(
                    {
                        "label": label,
                        "text": text,
                        "version": version_label,
                        "enters_force_date": (
                            enters_force.isoformat()
                            if (label == "ENTERS_FORCE" and enters_force)
                            else None
                        ),
                    }
                )
            else:
                # Bare, in-force subsection with no temporal annotation.
                spans.append(
                    {
                        "label": "CURRENT",
                        "text": text,
                        "version": "",
                        "enters_force_date": None,
                    }
                )
            # A versioned/current subsection consumes any pending note context.
            prior_wording_armed = False
            continue

        # Any other child (rare): pass through unlabeled as CURRENT.
        spans.append(
            {
                "label": "CURRENT",
                "text": _el_to_text(child),
                "version": "",
                "enters_force_date": None,
            }
        )

    return spans


def _section_has_temporal_markers(spans: List[Dict[str, Any]]) -> bool:
    """True if any span carries a temporal label beyond plain CURRENT text."""
    return any(s["label"] in ("IN_FORCE", "ENTERS_FORCE", "SUPERSEDED", "NOTE") for s in spans)


def _normalize_section_label(label: str) -> str:
    return re.sub(r"[\s§.*]", "", label).lower()


def _num_text_to_canonical_selector(num_text: str) -> str:
    """Convert AKN num-text like '7 §' or '14 b §' to canonical 'section:7' / 'section:14 b'."""
    # Strip trailing § (with optional surrounding whitespace) and any trailing dots/spaces
    label = re.sub(r"\s*§\s*$", "", num_text).strip().rstrip(".")
    return f"section:{label}" if label else ""


def _find_section_el(oracle_root: Any, section_filter: str) -> Any | None:
    """Delegate to the registered Finnish section resolver.

    The CLI is Finland-only by virtue of its corpus imports; the resolver
    implementation lives in finland/ and the jurisdiction-agnostic locator
    format lives in core/. Kept as a thin wrapper here for test continuity.

    If the input parses as a hierarchical locator, the resolver's verdict
    is authoritative — we do NOT fall through to raw num-text matching,
    because that would silently widen `section:3` to a deeply-nested
    section and `subsection:1` to whatever has `<num>1 §</num>`.

    Additional pre-processing (CLI-edge only, no semantics change):
    - Accept an exact eId match (e.g. 'chp_2__sec_7') directly.
    - Strip one wrapping parenthesis pair so '(7 §)' resolves like '7 §'.
    """
    if not section_filter:
        return None

    # Pre-processing: strip one wrapping paren pair
    stripped = section_filter.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()
    else:
        stripped = stripped

    from lawvm.core.locator import get_section_resolver, parse_locator_string
    resolver = get_section_resolver("fi")

    # Accept an exact eId match first (e.g. 'chp_2__sec_7v20221023')
    exact_by_eid = oracle_root.find(f'.//*[@eId="{stripped}"]')
    if exact_by_eid is not None:
        return exact_by_eid

    locator = parse_locator_string(stripped)
    if locator is not None:
        return resolver.resolve(oracle_root, locator)
    return resolver.resolve_raw(oracle_root, stripped)


def _collect_section_info(oracle_root: Any) -> List[Dict[str, str]]:
    """Collect (canonical_selector, eid, num_text) for every <section> in the oracle XML.

    Returns list of dicts with keys: canonical, eid, num_text.
    canonical is the --section-accepted form, e.g. 'section:7 a'.
    """
    items = []
    for sec in oracle_root.findall(".//{*}section"):
        eid = sec.get("eId") or ""
        num_el = sec.find(".//{*}num")
        num_text = (num_el.text or "").strip() if num_el is not None else ""
        canonical = _num_text_to_canonical_selector(num_text) if num_text else ""
        items.append({"canonical": canonical, "eid": eid, "num_text": num_text})
    return items


def _find_nearby_sections(
    section_info: List[Dict[str, str]],
    section_filter: str,
    n: int = 4,
) -> List[str]:
    """Return up to n canonical selectors near section_filter (for teaching errors).

    Strategy: extract the numeric label from the filter and find sections with
    numerically nearby labels. Falls back to the first few sections.
    """
    # Extract a numeric stem from the filter for proximity search
    # e.g. 'section:127a' → 127, 'section:127' → 127, '127 a' → 127, 'chp_3__sec_5' → skip
    _NUM_STEM_RE = re.compile(r"(\d+)")
    m = _NUM_STEM_RE.search(section_filter)
    if m:
        target_num = int(m.group(1))
        scored: List[tuple] = []
        for info in section_info:
            canon = info["canonical"]
            if not canon:
                continue
            m2 = _NUM_STEM_RE.search(canon)
            if m2:
                dist = abs(int(m2.group(1)) - target_num)
                scored.append((dist, canon))
        if scored:
            scored.sort(key=lambda x: x[0])
            seen: List[str] = []
            for _, c in scored:
                if c not in seen:
                    seen.append(c)
                if len(seen) >= n:
                    break
            return seen

    # Fallback: return first n canonical selectors
    fallback = []
    for info in section_info:
        c = info["canonical"]
        if c and c not in fallback:
            fallback.append(c)
        if len(fallback) >= n:
            break
    return fallback


def build_oracle_text_bundle(
    statute_id: str,
    section_filter: str = "",
    at_amendment: str = "",
    lang: str = "fin",
    show_subsections: bool = False,
    temporal_labels: bool = False,
) -> Dict[str, Any]:
    """Fetch oracle section text from the consolidated archive.

    Parameters
    ----------
    statute_id:
        Statute identifier, e.g. '2017/530'.
    section_filter:
        Section address, e.g. 'section:2'. If empty, lists all section labels.
    at_amendment:
        If given (e.g. '2020/959'), read the oracle at the consolidated version
        pinned to that amendment (version_tag = YYYYNNNN format).
        If empty, use the current selected oracle.
    lang:
        Language code (default 'fin').
    show_subsections:
        If True, include per-subsection text breakdown.
    temporal_labels:
        If True, include a `temporal_spans` breakdown that labels each span of
        the section as IN_FORCE / ENTERS_FORCE / SUPERSEDED / NOTE / CURRENT
        using the structural amendment-version markers in the source XML. The
        default flattened `full_text` is unchanged.
    """
    from lawvm.finland.grafter import get_corpus
    from lawvm.finland.consolidated_artifacts import build_consolidated_main_locator
    from lawvm.finland.corpus import get_consolidated_oracle_context
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lxml import etree

    cs = get_corpus()

    # oracle_cutoff_date and oracle_version_amendment_id are populated for the
    # default/latest snapshot path; for --at-amendment they remain None/"" because
    # the user pinned the version deliberately and the coverage caveat does not apply.
    oracle_cutoff_date: Optional[datetime.date] = None
    oracle_version_amendment_id_resolved: str = ""

    if at_amendment:
        version_tag = _amendment_id_to_version_tag(at_amendment)
        locator = build_consolidated_main_locator(
            sid=statute_id, lang=lang, version_tag=version_tag
        )
    else:
        selector = ConsolidatedArtifactSelector.latest_cached_editorial()
        ctx = get_consolidated_oracle_context(statute_id, corpus=cs, selector=selector)
        locator = ctx.locator
        # Reuse the cutoff metadata already resolved by get_consolidated_oracle_context.
        # Source: src/lawvm/finland/corpus.py — ConsolidatedOracleContext.cutoff_date
        # and ConsolidatedOracleContext.oracle_version_amendment_id.
        oracle_cutoff_date = ctx.cutoff_date
        oracle_version_amendment_id_resolved = ctx.oracle_version_amendment_id

    oracle_bytes = cs.read_locator(locator)
    if oracle_bytes is None:
        raise SystemExit(f"oracle not found in archive: {locator!r}")

    oracle_root = etree.fromstring(oracle_bytes)

    # Coverage staleness: snapshot is stale if its cutoff date is more than
    # _STALE_SNAPSHOT_YEARS before today.  Only applies to the default/latest
    # snapshot (not when the caller pinned a version with --at-amendment).
    coverage_possibly_stale = False
    if oracle_cutoff_date is not None and not at_amendment:
        stale_threshold = datetime.date.today().replace(
            year=datetime.date.today().year - _STALE_SNAPSHOT_YEARS
        )
        coverage_possibly_stale = oracle_cutoff_date < stale_threshold

    # Shared coverage fields included in every bundle variant so machine consumers
    # can inspect them without special-casing per path.
    coverage_fields: Dict[str, Any] = {
        "oracle_cutoff_date": oracle_cutoff_date.isoformat() if oracle_cutoff_date else None,
        "oracle_version_amendment_id": oracle_version_amendment_id_resolved or None,
        "coverage_possibly_stale": coverage_possibly_stale,
    }

    # No section filter → list all section labels and return
    if not section_filter:
        section_info = _collect_section_info(oracle_root)
        # Print the canonical selector (--section-accepted form) as the primary token,
        # with eId and raw num_text as metadata so a user/agent can copy the token directly.
        labels: List[str] = []
        for info in section_info:
            canon = info["canonical"]
            eid = info["eid"]
            num_text = info["num_text"]
            if canon:
                labels.append(f"{canon}  (eId={eid}, num={num_text})")
            elif eid:
                labels.append(f"(no-num)  (eId={eid})")
            else:
                labels.append(f"(no-num, no-eId)  num={num_text}")
        return {
            "statute_id": statute_id,
            "locator": locator,
            "at_amendment": at_amendment,
            "section_filter": "(none — listing sections)",
            "found": True,
            "section_labels": labels,
            "section_count": len(labels),
            "total_section_count": len(labels),
            "full_text": "",
            "subsections": [],
            **coverage_fields,
        }

    # Count all sections in this oracle (used by Task-N hint gate).
    total_section_count = len(oracle_root.findall(".//{*}section"))

    section_el = _find_section_el(oracle_root, section_filter)

    if section_el is None:
        section_info = _collect_section_info(oracle_root)
        nearby = _find_nearby_sections(section_info, section_filter)
        return {
            "statute_id": statute_id,
            "locator": locator,
            "at_amendment": at_amendment,
            "section_filter": section_filter,
            "found": False,
            "error": f"section {section_filter!r} not found at this oracle version",
            "nearby_sections": nearby,
            "total_section_count": total_section_count,
            "full_text": "",
            "subsections": [],
            **coverage_fields,
        }

    full_text = _el_to_text(section_el)
    subsections: List[Dict[str, Any]] = []
    if show_subsections:
        for i, ss in enumerate(section_el.findall(".//{*}subsection"), start=1):
            ss_text = _el_to_text(ss)
            hcontainers = ss.findall(".//{*}hcontainer")
            subsections.append({
                "index": i,
                "text": ss_text,
                "text_length": len(ss_text),
                "hcontainer_count": len(hcontainers),
            })

    temporal_spans: List[Dict[str, Any]] = []
    section_has_temporal_markers = False
    if temporal_labels:
        temporal_spans = build_temporal_spans(section_el)
        section_has_temporal_markers = _section_has_temporal_markers(temporal_spans)

    return {
        "statute_id": statute_id,
        "locator": locator,
        "at_amendment": at_amendment,
        "section_filter": section_filter,
        "found": True,
        "full_text": full_text,
        "full_text_length": len(full_text),
        "subsection_count": len(section_el.findall(".//{*}subsection")),
        "total_section_count": total_section_count,
        "subsections": subsections,
        "temporal_spans": temporal_spans,
        "section_has_temporal_markers": section_has_temporal_markers,
        **coverage_fields,
    }


def _format_temporal_span(span: Dict[str, Any]) -> List[str]:
    """Render one temporal span as labeled, indented lines."""
    label = span.get("label", "")
    version = span.get("version") or ""
    enters = span.get("enters_force_date")
    text = span.get("text", "")

    if label == "IN_FORCE":
        header = f"[IN FORCE{f' — {version}' if version else ''}]"
    elif label == "ENTERS_FORCE":
        date_part = f" {enters}" if enters else ""
        header = f"[ENTERS FORCE{date_part}{f' — {version}' if version else ''}]"
    elif label == "SUPERSEDED":
        header = "[SUPERSEDED (aiempi sanamuoto)]"
    elif label == "NOTE":
        header = "[NOTE (editorial)]"
    else:  # CURRENT
        header = "[CURRENT]"

    return [f"  {header}", f"    {text}"]


def _format_text(bundle: Dict[str, Any]) -> str:
    lines = [
        f"Statute  : {bundle['statute_id']}",
        f"Locator  : {bundle['locator']}",
    ]
    if bundle.get("at_amendment"):
        lines.append(f"Version  : @{_amendment_id_to_version_tag(bundle['at_amendment'])} (amendment {bundle['at_amendment']})")
    lines.append(f"Section  : {bundle['section_filter']}")

    # Listing mode
    section_labels = bundle.get("section_labels")
    if section_labels is not None:
        lines.append(f"\n{bundle['section_count']} sections in this oracle version:")
        for lbl in section_labels:
            lines.append(f"  {lbl}")
        return "\n".join(lines)

    if not bundle.get("found"):
        lines.append(f"\nERROR: {bundle.get('error', 'not found')}")
        nearby = bundle.get("nearby_sections", [])
        if nearby:
            lines.append(f"  nearby: {', '.join(nearby)}")
        return "\n".join(lines)

    lines.append(f"Subsections: {bundle.get('subsection_count', 0)}")
    lines.append(f"Text length: {bundle.get('full_text_length', 0)} chars")
    lines.append("")
    lines.append("Full text:")
    lines.append(f"  {bundle.get('full_text', '')}")

    temporal_spans = bundle.get("temporal_spans") or []
    if temporal_spans:
        lines.append("")
        if bundle.get("section_has_temporal_markers"):
            lines.append("Temporal breakdown (structural amendment-version markers):")
        else:
            lines.append(
                "Temporal breakdown (no amendment-version markers in this section "
                "— all text is current):"
            )
        for span in temporal_spans:
            lines.extend(_format_temporal_span(span))

    for ss in bundle.get("subsections", []):
        lines.append(f"\nSubsection {ss['index']} ({ss['text_length']} chars):")
        lines.append(f"  {ss['text']}")

    return "\n".join(lines)


def main(args: Any) -> None:
    global _HINT_EMITTED

    bundle = build_oracle_text_bundle(
        statute_id=args.statute_id,
        section_filter=getattr(args, "section", "") or "",
        at_amendment=getattr(args, "at_amendment", "") or "",
        show_subsections=getattr(args, "subsections", False),
        temporal_labels=getattr(args, "temporal_labels", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    # On not-found, emit the concise teaching error to stderr for agent/pipe
    # consumers while _format_text includes it in the human-readable stdout output.
    if not bundle.get("found") and bundle.get("nearby_sections"):
        nearby = bundle["nearby_sections"]
        statute_id_str = bundle.get("statute_id", "")
        section_filter_str = bundle.get("section_filter", "")
        print(
            f"hint: --section {section_filter_str!r} not found in {statute_id_str} "
            f"@{bundle.get('at_amendment') or 'latest'} — "
            f"nearby: {', '.join(nearby)}",
            file=sys.stderr,
        )
    print(_format_text(bundle))

    # Suppress all stderr hints/caveats when --no-hints or LAWVM_NO_HINTS=1.
    _no_hints = (
        getattr(args, "no_hints", False)
        or bool(os.environ.get("LAWVM_NO_HINTS", ""))
    )

    # Coverage staleness caveat — emitted to stderr when:
    #   • the default/latest snapshot is being used (not --at-amendment)
    #   • its cutoff date is more than _STALE_SNAPSHOT_YEARS years ago
    #   • hints are not suppressed
    # This is a COVERAGE statement (not a repeal claim): LawVM cannot confirm
    # what happened after the ingested chain's cutoff.
    at_amendment_used = bool(getattr(args, "at_amendment", ""))
    if (
        not _no_hints
        and not at_amendment_used
        and bundle.get("coverage_possibly_stale")
    ):
        cutoff_str = bundle.get("oracle_cutoff_date") or "unknown"
        last_amendment = bundle.get("oracle_version_amendment_id") or "unknown"
        print(
            f"note: consolidated snapshot reflects amendments through {cutoff_str} "
            f"(last: {last_amendment}). "
            f"LawVM cannot confirm amendments or repeals enacted after that date "
            f"— verify currency in Finlex.",
            file=sys.stderr,
        )

    # Task N: point-of-use discovery nudge — stateless, once-per-process,
    # never on JSON (handled above), suppressible.
    # Gates: section filter set, total_section_count > 12, not suppressed.
    section_filter_set = bool(getattr(args, "section", ""))
    total_count = bundle.get("total_section_count", 0)
    if (
        not _HINT_EMITTED
        and not _no_hints
        and section_filter_set
        and total_count > 12
    ):
        _HINT_EMITTED = True
        statute_id_str = bundle.get("statute_id", "")
        print(
            f"hint: searching a statute? "
            f"'refs --to {statute_id_str}' (who cites it) · "
            f"'topic --topic <kw>' (text) · 'sgrep' (structural).",
            file=sys.stderr,
        )
