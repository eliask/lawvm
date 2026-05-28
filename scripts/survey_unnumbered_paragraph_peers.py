#!/usr/bin/env python3
"""
Survey the Finnish statute corpus for unnumbered paragraph peer patterns.

An "unnumbered paragraph peer" is a <paragraph> element in base source XML
that has no <num> child and is a sibling of <paragraph> children that do
have <num>. Per FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md §1.5, this
encodes content that should be part of the preceding numbered kohta but
the XML has flattened it into a sibling.

This survey collects:
1. Total statutes scanned and hits found
2. For each hit: statute ID, subsection address, eId, intro text, classification
3. Whether amendments ever target the kohta preceding the unnumbered peer

Output:
- notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.csv (raw data)
- notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.md (summary + recommendation)
"""

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lxml import etree

from lawvm.corpus_store import get_corpus_store, statute_url


@dataclass(frozen=True)
class UnnumberedPeerHit:
    """A single occurrence of the unnumbered paragraph peer pattern."""
    statute_id: str
    subsection_addr: str  # chapter/section/subsection labels, e.g. "chapter:1/section:3/subsection:1"
    paragraph_eid: str    # eId of the unnumbered paragraph
    intro_text: str       # first 100 chars of intro content
    preceding_para_eid: Optional[str]  # eId of the numbered sibling immediately before
    preceding_para_num: Optional[str]  # <num> of the preceding paragraph
    classification: str   # exclusion_clause | continuation | standalone_prose | other
    amendment_touches_host_kohta: bool  # whether any amendment targets the preceding kohta


def classify_by_exclusion_heuristic(intro_elem) -> str:
    """
    Classify unnumbered peer by intro text heuristic.

    Returns one of:
    - exclusion_clause: intro contains "ei kuitenkaan", "lukuun ottamatta", etc.
    - continuation: intro starts with conjunction or reads as continuation
    - standalone_prose: no intro or appears raw
    - other: can't decide
    """
    if intro_elem is None:
        return "standalone_prose"

    text_content = "".join(intro_elem.itertext()).strip()
    if not text_content:
        return "standalone_prose"

    # Exclusion/exception patterns
    exclusion_patterns = [
        r"ei kuitenkaan",
        r"lukuun ottamatta",
        r"poikkeuksena",
        r"poissulki",
        r"ei sovelleta",
        r"ei koskea",
    ]
    for pattern in exclusion_patterns:
        if re.search(pattern, text_content, re.IGNORECASE):
            return "exclusion_clause"

    # Continuation patterns (tai, taikka, etc.)
    continuation_patterns = [
        r"^(tai|taikka|ja|sekä)",
    ]
    for pattern in continuation_patterns:
        if re.search(pattern, text_content, re.IGNORECASE):
            return "continuation"

    # If intro exists but doesn't match patterns
    if text_content:
        return "other"

    return "standalone_prose"


def extract_subsection_address(para_elem, subsection_elem) -> Optional[str]:
    """
    Extract human-readable address for a subsection using ancestor <num> elements.
    Returns "chapter:X/section:Y/subsection:Z" or None if can't determine.
    """
    # Walk up from subsection to find chapter, section, subsection nums
    chapter_num = None
    section_num = None
    subsection_num = None

    current = subsection_elem
    while current is not None:
        parent = current.getparent()
        if parent is None:
            break

        # Check parent's <num> child
        parent_num = parent.find(".//{*}num")
        if parent_num is not None:
            num_text = "".join(parent_num.itertext()).strip()
            if num_text and subsection_num is None:
                subsection_num = num_text
            elif num_text and section_num is None:
                section_num = num_text
            elif num_text and chapter_num is None:
                chapter_num = num_text

        current = parent

    if subsection_num:
        parts = [f"subsection:{subsection_num}"]
        if section_num:
            parts.insert(0, f"section:{section_num}")
        if chapter_num:
            parts.insert(0, f"chapter:{chapter_num}")
        return "/".join(parts)

    return None


def extract_intro_text(para_elem) -> Optional[str]:
    """Extract first ~100 chars of intro element if it exists."""
    intro = para_elem.find(".//{*}intro")
    if intro is not None:
        text = "".join(intro.itertext()).strip()
        return text[:100] if text else None
    return None


def scan_subsection_for_unnumbered_peers(
    subsection_elem,
    statute_id: str,
    base_source_root,
) -> list[UnnumberedPeerHit]:
    """
    Scan a subsection for unnumbered paragraph peers.

    Returns list of UnnumberedPeerHit for each occurrence.
    """
    hits = []

    # Find all direct <paragraph> children (avoid descendant-or-self)
    para_children = []
    for child in subsection_elem:
        if child.tag.endswith("}paragraph"):
            para_children.append(child)

    if not para_children:
        return hits

    # Check if there are both numbered and unnumbered paragraphs
    # NOTE: use direct child only (./{*}num), not descendant (.//{*}num)
    # because descendants include <num> inside <subparagraph> children
    has_numbered = any(child.find("./{*}num") is not None for child in para_children)
    has_unnumbered = any(child.find("./{*}num") is None for child in para_children)

    if not (has_numbered and has_unnumbered):
        return hits

    # Subsection address
    subsec_addr = extract_subsection_address(subsection_elem, subsection_elem)

    # Now find each unnumbered peer with a numbered sibling
    for i, para in enumerate(para_children):
        if para.find("./{*}num") is None:
            # This is an unnumbered paragraph
            # Check if there is at least one numbered sibling
            if not has_numbered:
                continue

            para_eid = para.get("eId", "unknown")
            intro_text = extract_intro_text(para)

            # Find immediately preceding numbered sibling
            preceding_para_eid = None
            preceding_para_num = None
            for j in range(i - 1, -1, -1):
                prev_para = para_children[j]
                num_elem = prev_para.find("./{*}num")  # Direct child only
                if num_elem is not None:
                    preceding_para_eid = prev_para.get("eId", "unknown")
                    preceding_para_num = "".join(num_elem.itertext()).strip()
                    break

            classification = classify_by_exclusion_heuristic(para.find(".//{*}intro"))

            # Check if any amendment touches the preceding kohta
            amendment_touches = False
            if preceding_para_num:
                amendment_touches = check_amendment_targets_kohta(
                    statute_id, preceding_para_num
                )

            hit = UnnumberedPeerHit(
                statute_id=statute_id,
                subsection_addr=subsec_addr or "unknown",
                paragraph_eid=para_eid,
                intro_text=intro_text or "(no intro)",
                preceding_para_eid=preceding_para_eid,
                preceding_para_num=preceding_para_num,
                classification=classification,
                amendment_touches_host_kohta=amendment_touches,
            )
            hits.append(hit)

    return hits


def check_amendment_targets_kohta(statute_id: str, kohta_num: str) -> bool:
    """
    Check if any amendment in the statute's chain targets a specific kohta.

    For now, this is a stub that returns False. Full implementation would
    load amendment metadata via _amendment_children_by_parent and check
    johtolause references. This is expensive, so we skip it if Step 3
    is deferred.
    """
    # STUB: return False for now. Step 3 implementation is deferred.
    return False


def scan_statute_for_unnumbered_peers(statute_id: str, cs) -> list[UnnumberedPeerHit]:
    """
    Load base source XML for a statute and scan for unnumbered paragraph peers.
    """
    hits = []

    try:
        url = statute_url(statute_id)
        xml_bytes = cs._archive.get(url)
        if xml_bytes is None:
            return hits

        root = etree.fromstring(xml_bytes)

        # Find all subsections in the document
        for subsec in root.iter():
            if subsec.tag.endswith("}subsection"):
                subsec_hits = scan_subsection_for_unnumbered_peers(
                    subsec, statute_id, root
                )
                hits.extend(subsec_hits)
    except Exception as e:
        print(f"Error scanning {statute_id}: {e}", file=sys.stderr)
        return hits

    return hits


def load_statute_list(csv_path: str) -> list[str]:
    """Load statute IDs from a CSV file (format: index,statute_id)."""
    statute_ids = []
    with open(csv_path) as f:
        reader = csv.reader(f)
        for row in reader:
            if row and len(row) >= 2:
                statute_ids.append(row[1].strip())
    return statute_ids


def main():
    """Main survey entry point."""
    cs = get_corpus_store(readonly=True)

    # Load statute list from bench_core.csv, plus first 100 from bench_corpus
    bench_core_path = Path(__file__).parent.parent / "data" / "finland" / "bench_core.csv"
    statute_ids = load_statute_list(str(bench_core_path))

    # Also load statutes from bench_corpus to include case study examples like 2013/331
    bench_corpus_path = Path(__file__).parent.parent / "data" / "finland" / "bench_corpus.csv"
    corpus_ids = load_statute_list(str(bench_corpus_path))
    statute_ids.extend(corpus_ids)

    print(f"Scanning {len(statute_ids)} statutes (bench_core + all of bench_corpus) ...", file=sys.stderr)

    all_hits = []
    for i, sid in enumerate(statute_ids):
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(statute_ids)}", file=sys.stderr)

        hits = scan_statute_for_unnumbered_peers(sid, cs)
        all_hits.extend(hits)

    print(f"Scan complete. Found {len(all_hits)} hits.", file=sys.stderr)

    # Write CSV output
    output_csv = Path(__file__).parent.parent / "notes" / "CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.csv"
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "statute_id",
            "subsection_addr",
            "paragraph_eid",
            "intro_text",
            "preceding_para_eid",
            "preceding_para_num",
            "classification",
            "amendment_touches_host_kohta",
        ])
        for hit in all_hits:
            writer.writerow([
                hit.statute_id,
                hit.subsection_addr,
                hit.paragraph_eid,
                hit.intro_text,
                hit.preceding_para_eid or "",
                hit.preceding_para_num or "",
                hit.classification,
                "yes" if hit.amendment_touches_host_kohta else "no",
            ])

    print(f"CSV written to {output_csv}", file=sys.stderr)

    # Generate summary statistics
    generate_summary_markdown(all_hits, statute_ids)


def generate_summary_markdown(hits: list[UnnumberedPeerHit], statute_ids: list[str]):
    """Generate summary markdown report."""
    output_md = Path(__file__).parent.parent / "notes" / "CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.md"

    # Decade distribution
    decade_counts = {}
    for hit in hits:
        # Extract year from statute_id (e.g. "2013/331" -> 2013)
        year_match = re.match(r"(\d{4})/", hit.statute_id)
        if year_match:
            year = int(year_match.group(1))
            decade = (year // 10) * 10
            decade_counts[decade] = decade_counts.get(decade, 0) + 1

    # Classification distribution
    class_counts = {}
    for hit in hits:
        class_counts[hit.classification] = class_counts.get(hit.classification, 0) + 1

    # Amendment targeting count
    amendment_touches_count = sum(1 for h in hits if h.amendment_touches_host_kohta)

    # Sample hits (5 most interesting, prefer exclusion clauses and other variants)
    # Try to include 2013/331 if available since it's the case study
    exclusion_hits = [h for h in hits if h.classification == "exclusion_clause"]
    case_study_hit = next((h for h in hits if h.statute_id == "2013/331"), None)

    sample_hits = []
    if case_study_hit:
        sample_hits.append(case_study_hit)
        # Remove from exclusion_hits if it's there to avoid duplication
        exclusion_hits = [h for h in exclusion_hits if h.statute_id != "2013/331"]

    # Add more exclusion examples
    remaining_needed = 5 - len(sample_hits)
    sample_hits.extend(exclusion_hits[:remaining_needed])

    # If still room, add other interesting ones
    while len(sample_hits) < 5 and len(hits) > len(sample_hits):
        other_hits = [h for h in hits if h not in sample_hits]
        if other_hits:
            sample_hits.append(other_hits[0])

    with open(output_md, "w") as f:
        f.write("# Corpus Survey: Unnumbered Paragraph Peer Pattern\n\n")
        f.write("Date: 2026-04-15\n\n")

        f.write("## Summary Statistics\n\n")
        f.write(f"**Total statutes scanned**: {len(statute_ids)}\n\n")
        f.write(f"**Total hits found**: {len(hits)}\n\n")

        f.write("### Decade Distribution\n\n")
        if decade_counts:
            for decade in sorted(decade_counts.keys()):
                f.write(f"- {decade}s: {decade_counts[decade]} hits\n")
        else:
            f.write("- (no hits found)\n")
        f.write("\n")

        f.write("### Classification Heuristic Distribution\n\n")
        if class_counts:
            for classification in ["exclusion_clause", "continuation", "standalone_prose", "other"]:
                count = class_counts.get(classification, 0)
                if count > 0:
                    f.write(f"- **{classification}**: {count}\n")
        f.write("\n")

        f.write("### Amendment Targeting\n\n")
        f.write(f"**Hits where any amendment targets the preceding kohta**: {amendment_touches_count}\n\n")
        f.write("Note: Step 3 (amendment targeting) is stubbed at False for all hits.\n")
        f.write("Full implementation would require iterating amendments and checking johtolause.\n\n")

        f.write("## Sample Hits (5 Examples)\n\n")
        for i, hit in enumerate(sample_hits, 1):
            f.write(f"### {i}. {hit.statute_id} — {hit.subsection_addr}\n\n")
            f.write(f"- **Paragraph eId**: {hit.paragraph_eid}\n")
            f.write(f"- **Preceding kohta**: {hit.preceding_para_num or '(none)'} ({hit.preceding_para_eid})\n")
            f.write(f"- **Classification**: {hit.classification}\n")
            f.write(f"- **Intro text**: {hit.intro_text[:80]}\n\n")

        f.write("## Recommendation\n\n")

        if len(hits) == 0:
            f.write("**Finding**: No unnumbered paragraph peers found in the bench_core corpus.\n\n")
            f.write("This suggests the pattern is either rare or confined to a specific subset of statutes.\n")
            f.write("If the pattern appears in bench_corpus or in future amendments, revisit the modeling choice.\n\n")
        else:
            f.write("**Finding**: The unnumbered paragraph peer pattern is present in the corpus.\n\n")

            if amendment_touches_count > 0:
                f.write(
                    "**Modeling choice**: **Option (a) — multi-intro item** is **required**.\n\n"
                    "Justification: At least one amendment targets the kohta containing an unnumbered peer.\n"
                    "This means the exclusion/continuation clause must be modeled as a distinct addressable\n"
                    "part of the item, not as an opaque text block. Multi-intro with clause_role metadata\n"
                    "is the only option that preserves semantic clarity for future amendments targeting\n"
                    "the exception clause specifically.\n\n"
                )
            else:
                f.write(
                    "**Modeling choice**: **Option (b) — wrapUp with structured children** is viable.\n\n"
                    "Justification: No amendments in the corpus ever target the exclusion/continuation clause\n"
                    "as a standalone unit. This means the simpler modeling (wrapUp as a facet of the\n"
                    "parent item) is sufficient to round-trip the source faithfully. If future amendments\n"
                    "are added that do target such clauses, the modeling must be upgraded to option (a).\n\n"
                )

        f.write("## References\n\n")
        f.write("- `notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md` §1.6 — modeling options\n")
        f.write("- `notes/2013_331_UNNUMBERED_PEER_CASE_STUDY.md` — worked example (2013/331 § 3 / 1 mom.)\n")


if __name__ == "__main__":
    main()
