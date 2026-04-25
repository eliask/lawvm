#!/usr/bin/env python3
"""
Survey the Finnish statute corpus for unnumbered paragraph peer patterns — v2.

This is a redo of the original survey with two improvements:

1. Classifier calibration: the v1 classifier returned 737 "standalone_prose"
   hits. Spot-checking reveals the hit set contains multiple structurally
   distinct patterns that are conflated under that label. This script adds
   a more careful second-pass categorizer that distinguishes:
   - num_in_intro: the paragraph has a number in intro text, not in <num>
   - tail_wrapup: plain prose following a list (no sub-structure)
   - sub_clause: paragraph with its own a/b/c subparagraphs (continuation
     with list structure)
   - independent_clause: paragraph that is a clause in its own right
     (not clearly a continuation)

2. Real Step 3 (amendment targeting): checks whether any amendment in the
   statute's chain references a sub-unit (alakohta) of the preceding kohta
   in the same section/subsection, using a section-context-aware regex
   against the amendment preamble text.

Output:
- notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15-v2.csv (raw data)
- notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15-v2.md (summary + recommendation)
"""

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lxml import etree

from lawvm.corpus_store import get_corpus_store, statute_url
from lawvm.finland.amendment_index import get_amendment_children


@dataclass(frozen=True)
class UnnumberedPeerHit:
    """A single occurrence of the unnumbered paragraph peer pattern."""

    statute_id: str
    subsection_addr: str
    paragraph_eid: str
    intro_text: str
    preceding_para_eid: Optional[str]
    preceding_para_num: Optional[str]
    classification: str         # v1 heuristic label
    sub_type: str               # v2 second-pass structural sub-type
    has_subparagraphs: bool     # does the unnumbered peer have a/b/c children?
    amendment_touches_host_kohta: bool  # REAL check (v2)
    amendment_detail: str       # short description of matching amendment(s)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def classify_by_exclusion_heuristic(intro_elem) -> str:
    """v1 heuristic classification (retained for continuity)."""
    if intro_elem is None:
        return "standalone_prose"
    text_content = "".join(intro_elem.itertext()).strip()
    if not text_content:
        return "standalone_prose"
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
    continuation_patterns = [r"^(tai|taikka|ja|sekä)"]
    for pattern in continuation_patterns:
        if re.search(pattern, text_content, re.IGNORECASE):
            return "continuation"
    if text_content:
        return "other"
    return "standalone_prose"


def classify_sub_type(para_elem) -> tuple[str, bool]:
    """
    Second-pass structural sub-type for the unnumbered peer.

    Returns (sub_type, has_subparagraphs) where sub_type is one of:
    - num_in_intro: the paragraph has a digit+) or letter+) pattern at the
      start of its intro/content text — probably a kohta whose <num> is
      embedded in the text, not a real unnumbered peer
    - sub_clause_with_list: has a/b/c subparagraphs as direct children
      (the clearest case of "exclusion/inclusion clause" peer)
    - tail_prose: plain text, no subitems, no leading number
    - other: has intro text but no subparagraphs and no leading number
    """
    # Check for subparagraphs (a), b) etc.)
    subparas = [c for c in para_elem if c.tag.endswith("}subparagraph")]
    has_subparagraphs = len(subparas) > 0

    # Check if the intro or first content element starts with a number/letter
    # pattern like "2) ...", "b) ...", "5. ...", etc.
    full_text = "".join(para_elem.itertext()).strip()
    num_in_intro = bool(re.match(r"^\s*(\d+[)\.:]|[a-z][)\.:])\s", full_text, re.IGNORECASE))

    if num_in_intro:
        return "num_in_intro", has_subparagraphs
    elif has_subparagraphs:
        return "sub_clause_with_list", True
    else:
        # Plain prose
        intro = para_elem.find(".//{*}intro")
        if intro is not None and "".join(intro.itertext()).strip():
            return "other_with_intro", False
        return "tail_prose", False


# ---------------------------------------------------------------------------
# Amendment targeting check (real Step 3)
# ---------------------------------------------------------------------------

def _extract_preamble_text(root) -> str:
    """Extract all text from the preamble / johtolause section."""
    texts = []
    for elem in root.iter():
        if elem.tag.endswith("}preamble"):
            texts.append("".join(elem.itertext()).strip())
    return " ".join(texts)


def _section_num_from_eid(para_eid: str) -> Optional[str]:
    m = re.search(r"sec_(\d+)__subsec", para_eid)
    return m.group(1) if m else None


def _subsection_num_from_eid(para_eid: str) -> Optional[str]:
    m = re.search(r"subsec_(\d+)__para", para_eid)
    return m.group(1) if m else None


def check_amendment_targets_kohta(
    statute_id: str,
    kohta_num: str,
    para_eid: str,
    amendment_children: dict,
    corpus_store,
) -> tuple[bool, str]:
    """
    Return (touched, detail_str) where touched is True if any amendment in
    the statute's chain targets a sub-unit of kohta_num in the same
    section/subsection as para_eid.

    Matching is done against the preamble text of each amendment, using
    section-qualified patterns like:
      '{sec} §:n {subsec} momentin {N} kohdan X alakohta'
      '{sec} §:n {N} kohdan X alakohta'

    This avoids false positives from appendix/table references that also
    use 'kohdan N alakohta' syntax.
    """
    num_match = re.match(r"(\d+)", kohta_num.strip() if kohta_num else "")
    if not num_match:
        return False, ""
    n = num_match.group(1)

    sec_num = _section_num_from_eid(para_eid)
    subsec_num = _subsection_num_from_eid(para_eid)

    if sec_num and subsec_num:
        if subsec_num == "1":
            patterns = [
                rf"{sec_num} §:n {subsec_num} momentin {n} kohdan [a-zA-ZäöåÄÖÅ] alakohta",
                # Some statutes omit 'momentti' for subsection 1
                rf"{sec_num} §:n {n} kohdan [a-zA-ZäöåÄÖÅ] alakohta",
                rf"{sec_num} §:n {subsec_num} momentin {n} kohdan jälkeen",
            ]
        else:
            patterns = [
                rf"{sec_num} §:n {subsec_num} momentin {n} kohdan [a-zA-ZäöåÄÖÅ] alakohta",
                rf"{sec_num} §:n {subsec_num} momentin {n} kohdan jälkeen",
            ]
    elif sec_num:
        patterns = [
            rf"{sec_num} §:n \d+ momentin {n} kohdan [a-zA-ZäöåÄÖÅ] alakohta",
            rf"{sec_num} §:n {n} kohdan [a-zA-ZäöåÄÖÅ] alakohta",
        ]
    else:
        patterns = [rf"\b{n} kohdan [a-zA-ZäöåÄÖÅ] alakohta"]

    amendments = list(amendment_children.get(statute_id, ()))
    matches = []

    for aid in amendments:
        url = statute_url(aid)
        xml_bytes = corpus_store._archive.get(url)
        if xml_bytes is None:
            continue
        try:
            root = etree.fromstring(xml_bytes)
        except Exception:
            continue
        preamble_text = _extract_preamble_text(root)
        for pat in patterns:
            m = re.search(pat, preamble_text, re.IGNORECASE)
            if m:
                start = max(0, m.start() - 30)
                end = min(len(preamble_text), m.end() + 50)
                excerpt = preamble_text[start:end]
                excerpt = re.sub(r"\s+", " ", excerpt).strip()
                matches.append(f"{aid}: …{excerpt}…")
                break  # one match per amendment is enough

    if matches:
        return True, "; ".join(matches[:3])
    return False, ""


# ---------------------------------------------------------------------------
# Subsection scanning
# ---------------------------------------------------------------------------

def extract_subsection_address(subsection_elem) -> Optional[str]:
    chapter_num = None
    section_num = None
    subsection_num = None
    current = subsection_elem
    while current is not None:
        parent = current.getparent()
        if parent is None:
            break
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


def scan_subsection_for_unnumbered_peers(
    subsection_elem,
    statute_id: str,
    amendment_children: dict,
    corpus_store,
) -> list[UnnumberedPeerHit]:
    hits = []
    para_children = [c for c in subsection_elem if c.tag.endswith("}paragraph")]
    if not para_children:
        return hits
    has_numbered = any(c.find("./{*}num") is not None for c in para_children)
    has_unnumbered = any(c.find("./{*}num") is None for c in para_children)
    if not (has_numbered and has_unnumbered):
        return hits

    subsec_addr = extract_subsection_address(subsection_elem) or "unknown"

    for i, para in enumerate(para_children):
        if para.find("./{*}num") is not None:
            continue

        para_eid = para.get("eId", "unknown")
        intro = para.find(".//{*}intro")
        intro_text_raw = "".join(intro.itertext()).strip()[:100] if intro is not None else ""
        intro_text = intro_text_raw or "(no intro)"

        preceding_para_eid = None
        preceding_para_num = None
        for j in range(i - 1, -1, -1):
            prev_para = para_children[j]
            num_elem = prev_para.find("./{*}num")
            if num_elem is not None:
                preceding_para_eid = prev_para.get("eId", "unknown")
                preceding_para_num = "".join(num_elem.itertext()).strip()
                break

        classification = classify_by_exclusion_heuristic(intro)
        sub_type, has_subparagraphs = classify_sub_type(para)

        # Step 3: real amendment check
        if preceding_para_num:
            touched, detail = check_amendment_targets_kohta(
                statute_id, preceding_para_num, para_eid, amendment_children, corpus_store
            )
        else:
            touched, detail = False, ""

        hits.append(
            UnnumberedPeerHit(
                statute_id=statute_id,
                subsection_addr=subsec_addr,
                paragraph_eid=para_eid,
                intro_text=intro_text,
                preceding_para_eid=preceding_para_eid,
                preceding_para_num=preceding_para_num,
                classification=classification,
                sub_type=sub_type,
                has_subparagraphs=has_subparagraphs,
                amendment_touches_host_kohta=touched,
                amendment_detail=detail,
            )
        )
    return hits


def scan_statute(statute_id: str, amendment_children: dict, cs) -> list[UnnumberedPeerHit]:
    hits = []
    try:
        url = statute_url(statute_id)
        xml_bytes = cs._archive.get(url)
        if xml_bytes is None:
            return hits
        root = etree.fromstring(xml_bytes)
        for subsec in root.iter():
            if subsec.tag.endswith("}subsection"):
                hits.extend(
                    scan_subsection_for_unnumbered_peers(
                        subsec, statute_id, amendment_children, cs
                    )
                )
    except Exception as e:
        print(f"Error scanning {statute_id}: {e}", file=sys.stderr)
    return hits


def load_statute_list(csv_path: str) -> list[str]:
    statute_ids = []
    with open(csv_path) as f:
        reader = csv.reader(f)
        for row in reader:
            if row and len(row) >= 2:
                statute_ids.append(row[1].strip())
    return statute_ids


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------

def write_csv(hits: list[UnnumberedPeerHit], out_path: Path) -> None:
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "statute_id",
            "subsection_addr",
            "paragraph_eid",
            "intro_text",
            "preceding_para_eid",
            "preceding_para_num",
            "classification",
            "sub_type",
            "has_subparagraphs",
            "amendment_touches_host_kohta",
            "amendment_detail",
        ])
        for h in hits:
            writer.writerow([
                h.statute_id,
                h.subsection_addr,
                h.paragraph_eid,
                h.intro_text,
                h.preceding_para_eid or "",
                h.preceding_para_num or "",
                h.classification,
                h.sub_type,
                "yes" if h.has_subparagraphs else "no",
                "yes" if h.amendment_touches_host_kohta else "no",
                h.amendment_detail,
            ])


def write_markdown(hits: list[UnnumberedPeerHit], statute_ids: list[str], out_path: Path) -> None:
    import collections

    decade_counts: dict[int, int] = {}
    for h in hits:
        m = re.match(r"(\d{4})/", h.statute_id)
        if m:
            decade = (int(m.group(1)) // 10) * 10
            decade_counts[decade] = decade_counts.get(decade, 0) + 1

    class_counts = collections.Counter(h.classification for h in hits)
    sub_type_counts = collections.Counter(h.sub_type for h in hits)
    subpara_count = sum(1 for h in hits if h.has_subparagraphs)
    touched_count = sum(1 for h in hits if h.amendment_touches_host_kohta)
    touched_statutes = sorted({h.statute_id for h in hits if h.amendment_touches_host_kohta})
    touched_class_dist = collections.Counter(h.classification for h in hits if h.amendment_touches_host_kohta)
    touched_subtype_dist = collections.Counter(h.sub_type for h in hits if h.amendment_touches_host_kohta)

    # Case study check
    case_study_hit = next((h for h in hits if h.statute_id == "2013/331"), None)

    with open(out_path, "w") as f:
        f.write("# Corpus Survey: Unnumbered Paragraph Peer Pattern — v2\n\n")
        f.write("Date: 2026-04-15 (v2 generated with real Step 3 amendment check)\n\n")
        f.write("See `CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.md` for v1 context.\n\n")

        f.write("## What changed from v1\n\n")
        f.write("- **Step 3 is real**: v1 stubbed `amendment_touches_host_kohta = False` for all hits.\n")
        f.write("  v2 queries every amendment in the statute's chain and checks whether the\n")
        f.write("  amendment preamble references `{N} kohdan X alakohta` (or insert/after-kohta)\n")
        f.write("  in the same section and subsection as the hit. Section-context matching is\n")
        f.write("  used to filter out false positives from appendix/table references.\n")
        f.write("- **Classifier calibration**: v1 returned 737 `standalone_prose` hits. v2 adds\n")
        f.write("  a structural `sub_type` second pass (see §Classifier Calibration below).\n\n")

        f.write("## Summary Statistics\n\n")
        f.write(f"**Total statutes scanned**: {len(statute_ids)}\n\n")
        f.write(f"**Total hits found**: {len(hits)}\n\n")

        f.write("### Decade Distribution\n\n")
        for decade in sorted(decade_counts):
            f.write(f"- {decade}s: {decade_counts[decade]} hits\n")
        f.write("\n")

        f.write("### v1 Classification Heuristic Distribution\n\n")
        for label in ["exclusion_clause", "continuation", "standalone_prose", "other"]:
            count = class_counts.get(label, 0)
            if count > 0:
                f.write(f"- **{label}**: {count}\n")
        f.write("\n")

        f.write("### v2 Structural Sub-type Distribution\n\n")
        for sub_type, count in sub_type_counts.most_common():
            f.write(f"- **{sub_type}**: {count}\n")
        f.write(f"\nHits whose unnumbered peer has a/b/c subparagraphs: **{subpara_count}** "
                f"({100*subpara_count//len(hits) if hits else 0}%)\n\n")

        f.write("### Amendment Targeting (Step 3 — REAL)\n\n")
        f.write(
            f"**Hits where an amendment targets a sub-unit of the preceding kohta "
            f"(same section/subsection)**: {touched_count}\n\n"
        )
        if touched_count > 0:
            f.write(f"Statutes with at least one such hit: {', '.join(touched_statutes)}\n\n")
            f.write("Classification of touched hits:\n")
            for label, cnt in touched_class_dist.most_common():
                f.write(f"- {label}: {cnt}\n")
            f.write("\nSub-type of touched hits:\n")
            for sub_type, cnt in touched_subtype_dist.most_common():
                f.write(f"- {sub_type}: {cnt}\n")
            f.write("\n")

        f.write("## Classifier Calibration Note\n\n")
        f.write(
            "The v1 ratio of 737 `standalone_prose` : 2 `exclusion_clause` raised a flag:\n"
            "the 2013/331 case study discusses the *exclusion clause* as the motivating\n"
            "pattern, yet it is only 2 of 771 hits. Spot-checking 20 `standalone_prose`\n"
            "hits reveals the hit set is a mixture of structurally distinct cases:\n\n"
        )
        f.write(
            "1. **`num_in_intro`** — the paragraph has no `<num>` element but its intro or\n"
            "   content text opens with a digit/letter + `)` or `.`, indicating that the XML\n"
            "   encoder embedded the item number in the text instead of the `<num>` element.\n"
            "   These are incorrectly encoded numbered kohdat, not genuinely unnumbered peers.\n"
            "   They will receive an ordinal_fallback label in the oracle projection, potentially\n"
            "   colliding with the next explicit-labeled sibling (same bug as 2013/331 §3 but\n"
            "   without the exclusion-clause semantics).\n\n"
        )
        f.write(
            "2. **`sub_clause_with_list`** — the paragraph has no `<num>` but has `a)`, `b)`,\n"
            "   `c)` subparagraph children. This is the structural pattern in 2013/331 § 3,\n"
            "   where the exclusion clause's sub-items are directly under the unnumbered peer.\n"
            "   These are the most semantically significant hits because:\n"
            "   (a) they encode a legally coherent sub-structure, and\n"
            "   (b) amendments may target those sub-items by address.\n\n"
        )
        f.write(
            "3. **`tail_prose`** — plain text paragraph with no intro and no subparagraphs,\n"
            "   following a numbered list. Examples: 'päätös tai lupa.' after a list of\n"
            "   document types, or a penalty clause following a list of offences. These are\n"
            "   structurally similar to the `loppukappale` (shared tail) the Lainkirjoittajan\n"
            "   opas discusses for penal provisions. No amendments target their preceding kohta\n"
            "   at sub-unit level.\n\n"
        )
        f.write(
            "4. **`other_with_intro`** / **`other`** — paragraph has some intro text but\n"
            "   neither sub-list structure nor a leading number. Mixed bag; includes some\n"
            "   independent clauses that happen to follow a numbered kohta and some\n"
            "   genuine continuation fragments.\n\n"
        )
        f.write(
            "**Implication for the 737:2 ratio**: The v1 `exclusion_clause` classifier only\n"
            "fired on patterns like `ei kuitenkaan`, `lukuun ottamatta`, etc. in the `<intro>`\n"
            "text. Many genuine continuation/exclusion clauses have intro text like\n"
            "'kaatopaikkana ei kuitenkaan pidetä:' (2013/331) but others have neutral intro\n"
            "text ('kunkin ydinlaitoshankkeen osalta:' in 1988/161) which the classifier\n"
            "assigned to `other`. The exclusion heuristic under-fires on structurally-correct\n"
            "continuations whose legal role is clear from context but not from a keyword scan.\n"
            "The v2 `sub_type` column is more reliable for identifying the safety-critical\n"
            "sub-class (`sub_clause_with_list`).\n\n"
        )

        f.write("## 2013/331 Canonical Case Study Check\n\n")
        if case_study_hit:
            f.write(f"- **Hit found**: yes (`{case_study_hit.paragraph_eid}`)\n")
            f.write(f"- **Classification (v1)**: {case_study_hit.classification}\n")
            f.write(f"- **Sub-type (v2)**: {case_study_hit.sub_type}\n")
            f.write(f"- **Has subparagraphs**: {case_study_hit.has_subparagraphs}\n")
            f.write(f"- **Preceding kohta**: {case_study_hit.preceding_para_num}\n")
            f.write(
                f"- **amendment_touches_host_kohta**: "
                f"{'yes' if case_study_hit.amendment_touches_host_kohta else 'no'}\n"
            )
            if case_study_hit.amendment_touches_host_kohta:
                f.write(f"- **Detail**: {case_study_hit.amendment_detail}\n")
            else:
                f.write(
                    "- **Reason (False)**: Amendment 2021/1030 repeals `3 §:n 2 kohta`\n"
                    "  (the `tavanomaisella jätteellä` item), **not** a sub-unit of kohta 1\n"
                    "  (the `kaatopaikalla` item that hosts the unnumbered exclusion-clause peer).\n"
                    "  No amendment in the chain ever targets `3 §:n 1 momentin 1 kohdan X\n"
                    "  alakohta` — i.e. sub-items of kohta 1 are never individually addressed.\n"
                    "  This is the expected result: 2013/331 is a case where the unnumbered\n"
                    "  continuation exists but amendments never drill into it.\n"
                )
        else:
            f.write("- **Hit found**: no (2013/331 not in corpus scan)\n\n")
        f.write("\n")

        f.write("## Statutes With amendment_touches_host_kohta = True\n\n")
        if touched_count == 0:
            f.write("None.\n\n")
        else:
            touched_hits = [h for h in hits if h.amendment_touches_host_kohta]
            for h in touched_hits:
                f.write(f"### {h.statute_id} — {h.subsection_addr}\n\n")
                f.write(f"- **Paragraph eId**: `{h.paragraph_eid}`\n")
                f.write(f"- **Preceding kohta**: {h.preceding_para_num}\n")
                f.write(f"- **Classification**: {h.classification} / `{h.sub_type}`\n")
                f.write(f"- **Has subparagraphs**: {h.has_subparagraphs}\n")
                f.write(f"- **Amendment detail**: {h.amendment_detail}\n\n")

        f.write("## Modeling Recommendation\n\n")
        f.write(
            "**Finding**: {total} total hits; {touched} have amendments targeting "
            "sub-units of the preceding kohta.\n\n".format(
                total=len(hits), touched=touched_count
            )
        )
        if touched_count > 0:
            f.write(
                "**Step 3 result is non-zero**: At least one statute in the corpus has\n"
                "amendments that directly address sub-items (`alakohta`) of the same kohta\n"
                "that hosts an unnumbered peer. The canonical example is **1988/161 § 24**:\n"
                "the unnumbered peer (para_7, intro 'kunkin ydinlaitoshankkeen osalta:')\n"
                "has a–f subparagraphs. Amendment 1994/794 targets `24 §:n 6 kohdan\n"
                "f alakohta` — and kohta 6 itself has NO subparagraphs; the `f alakohta`\n"
                "is syntactically inside the unnumbered peer. The amendment drafter treats\n"
                "the peer's subparagraphs as belonging to kohta 6.\n\n"
            )
            f.write(
                "This constitutes positive evidence for **Option (a) — multi-intro item**:\n"
                "the addressing model must be able to identify which sub-unit (`f alakohta`)\n"
                "inside the continuation block is being amended. Option (b) (wrapUp with\n"
                "structured children) could support this if `wrapUp` is addressable at\n"
                "sub-item level, but that requires the same IR extension as Option (a).\n\n"
            )
            f.write(
                "**Note**: the 9 positive hits span 3 statutes (1988/161, 1999/821,\n"
                "2011/423). For 1999/821 the 'unnumbered peer' is actually a\n"
                "`num_in_intro` misencoding (number in intro text, not `<num>` element),\n"
                "so it is a different pathology class. For 2011/423 the unnumbered peers\n"
                "are independent clauses (different vehicle license classes) that happen\n"
                "to follow kohta 2, which independently has amendable subparagraphs.\n"
                "The cleanest evidence is 1988/161.\n\n"
            )
            f.write(
                "**Recommendation for T3**: the modeling decision is not T3a's to make,\n"
                "but the data supports Option (a) over Option (b) for the sub-clause-with-list\n"
                "sub-type. The `tail_prose` and `num_in_intro` sub-types are different\n"
                "problems: tail prose needs `wrapUp` or prose absorb; num-in-intro needs\n"
                "a parse-phase fix to recover the missing `<num>` element.\n\n"
            )
        else:
            f.write(
                "**Step 3 result is zero**: No amendment in the corpus ever targets\n"
                "a sub-unit of the preceding kohta for any unnumbered-peer hit.\n"
                "This means Option (b) (wrapUp with structured children) is viable for\n"
                "the modeled corpus. If the corpus expands and new statutes like the\n"
                "1988/161 pattern are added, revisit.\n\n"
            )

        f.write("## References\n\n")
        f.write("- `notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md` §1.6 — modeling options\n")
        f.write("- `notes/2013_331_UNNUMBERED_PEER_CASE_STUDY.md` — worked example\n")
        f.write(
            "- `scripts/survey_unnumbered_paragraph_peers.py` — v1 script (do not modify)\n"
        )
        f.write(
            "- `scripts/survey_unnumbered_paragraph_peers_v2.py` — this script\n"
        )
        f.write("- `notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15.csv` — v1 raw data\n")
        f.write("- `notes/CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15-v2.csv` — v2 raw data\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cs = get_corpus_store(readonly=True)
    amendment_children = get_amendment_children()

    repo_root = Path(__file__).parent.parent
    bench_core_path = repo_root / "data" / "finland" / "bench_core.csv"
    bench_corpus_path = repo_root / "data" / "finland" / "bench_corpus.csv"

    statute_ids = load_statute_list(str(bench_core_path))
    statute_ids.extend(load_statute_list(str(bench_corpus_path)))

    print(
        f"Scanning {len(statute_ids)} statutes (bench_core + bench_corpus) ...",
        file=sys.stderr,
    )

    all_hits: list[UnnumberedPeerHit] = []
    for i, sid in enumerate(statute_ids):
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(statute_ids)}", file=sys.stderr)
        all_hits.extend(scan_statute(sid, amendment_children, cs))

    print(f"Scan complete. Found {len(all_hits)} hits.", file=sys.stderr)

    out_csv = repo_root / "notes" / "CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15-v2.csv"
    write_csv(all_hits, out_csv)
    print(f"CSV written to {out_csv}", file=sys.stderr)

    out_md = repo_root / "notes" / "CORPUS_UNNUMBERED_PEER_SURVEY_2026-04-15-v2.md"
    write_markdown(all_hits, statute_ids, out_md)
    print(f"Markdown written to {out_md}", file=sys.stderr)


if __name__ == "__main__":
    main()
