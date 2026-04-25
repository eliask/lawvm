"""lawvm peg-audit -- verify scan/filter pipeline preserves structural tokens.

Phase 4 of Proof Boundary Architecture (AUDITOR_SPECS.md).

Verifies that the PEG scan/filter pipeline does not silently destroy
structural tokens.  The scan pipeline converts raw johtolause text into
an annotated token stream, replacing provenance/citation/reinstatement
spans with sentinels.  If a structural token is inside an annotation
span, it is intentionally hidden.  But if a structural token is destroyed
by whitespace normalization, text cleaning, or annotation boundary errors,
that is information loss.

Usage:
    lawvm peg-audit <statute_id>                         # all amendments
    lawvm peg-audit <statute_id> --source <amendment_id> # one amendment
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from lawvm.finland.johtolause.peg3 import Token, tokenize
from lawvm.finland.johtolause.scan import (
    AnnotatedStream,
    annotate_statute_citations,
    annotate_statute_names,
    annotate_provenance,
    annotate_reinstatement,
    annotate_jolloin,
    annotate_end_sentinels,
    annotate_qualifiers,
    annotate_punct,
    _remap_annotation,
)


# ---------------------------------------------------------------------------
# Structural token categories
# ---------------------------------------------------------------------------

# These are the categories that define document structure positions in
# Finnish amendment clauses.  A token with one of these categories
# appearing in the raw tokenization MUST either survive into the
# structural view OR be covered by a named annotation span.
_STRUCTURAL_CATS = frozenset({
    "PYKALA", "LUKU", "OSA", "MOMENTTI", "KOHTA", "LIITE",
})


# ---------------------------------------------------------------------------
# Audit result
# ---------------------------------------------------------------------------

@dataclass
class ScanAuditResult:
    """Result of auditing one johtolause's scan/filter pipeline."""

    amendment_id: str
    raw_structural_count: int
    annotation_covered_count: int
    structural_view_count: int
    unaccounted_count: int
    unaccounted_tokens: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.unaccounted_count == 0


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------

def _count_structural(tokens: list[Token]) -> int:
    """Count structural tokens in a token list."""
    return sum(1 for t in tokens if t.cat in _STRUCTURAL_CATS)


def audit_scan_preservation(
    johto_text: str,
    amendment_id: str = "",
) -> ScanAuditResult:
    """Audit that scan/filter pipeline preserves all structural tokens.

    Pipeline:
    1. Tokenize raw text -> get raw token list
    2. Count structural tokens in raw list
    3. Run the full annotation pipeline (apply_annotations) to get the
       structural view
    4. Reconstruct the two-phase annotation architecture to determine
       which raw structural tokens are covered by annotations
    5. For each raw structural token, check: is it in structural view
       OR covered by a named annotation span?
    6. Any token that is neither = UNACCOUNTED (information loss)
    """
    # Step 1: Tokenize
    raw_tokens = tokenize(johto_text)
    if not raw_tokens:
        return ScanAuditResult(
            amendment_id=amendment_id,
            raw_structural_count=0,
            annotation_covered_count=0,
            structural_view_count=0,
            unaccounted_count=0,
        )

    # Step 2: Count structural tokens in raw list
    raw_structural_positions = [
        i for i, t in enumerate(raw_tokens) if t.cat in _STRUCTURAL_CATS
    ]
    raw_structural_count = len(raw_structural_positions)

    # Step 3: Reconstruct the two-phase annotation pipeline to get
    # annotations with raw-tape coordinates.  This mirrors apply_annotations()
    # but retains the annotations for inspection.

    # Phase 1: citation + names on raw tokens
    cite_anns = annotate_statute_citations(raw_tokens)
    name_anns = annotate_statute_names(raw_tokens, cite_anns)
    phase1_anns = cite_anns + name_anns
    phase1_stream = AnnotatedStream(tokens=raw_tokens, annotations=phase1_anns)
    view, view_to_raw = phase1_stream.structural_view_with_map()

    # Phase 2: remaining producers on the Phase 1 structural view
    prov_anns_v = annotate_provenance(view)
    reinst_anns_v = annotate_reinstatement(view)
    jolloin_anns_v = annotate_jolloin(view)
    qual_anns_v = annotate_qualifiers(view)
    end_anns_v = annotate_end_sentinels(view)
    punct_anns_v = annotate_punct(view)

    # Map Phase 2 annotations from view coordinates -> raw-tape coordinates
    phase2_view_anns = (
        prov_anns_v + reinst_anns_v + jolloin_anns_v
        + qual_anns_v + end_anns_v + punct_anns_v
    )
    phase2_raw_anns = [_remap_annotation(a, view_to_raw) for a in phase2_view_anns]

    # Combine all annotations on raw tape
    all_anns = phase1_anns + phase2_raw_anns

    # Step 4: Build a coverage mask — which raw positions are covered by
    # annotation spans?
    covered_by_annotation: set[int] = set()
    for ann in all_anns:
        for pos in range(ann.span.start, ann.span.end):
            covered_by_annotation.add(pos)

    # Step 5: Build the final structural view and find structural tokens in it.
    # We use the combined annotation stream for this.
    final_stream = AnnotatedStream(tokens=raw_tokens, annotations=all_anns)
    final_view = final_stream.structural_view()
    structural_view_count = _count_structural(final_view)

    # Step 6: For each raw structural token position, check if it is either:
    # (a) covered by an annotation, or
    # (b) present in the structural view (passed through).
    #
    # For (b), we need to know which raw positions passed through to the view.
    # The final_stream.structural_view_with_map() gives us this mapping.
    _, final_view_to_raw = final_stream.structural_view_with_map()
    passthrough_positions: set[int] = set()
    for raw_start, raw_end in final_view_to_raw:
        if raw_end - raw_start == 1:
            # Single raw token passed through (not a sentinel for a span)
            passthrough_positions.add(raw_start)

    annotation_covered_count = 0
    unaccounted_positions: list[int] = []

    for pos in raw_structural_positions:
        if pos in covered_by_annotation:
            annotation_covered_count += 1
        elif pos in passthrough_positions:
            pass  # counted in structural_view_count
        else:
            unaccounted_positions.append(pos)

    unaccounted_tokens = [raw_tokens[p].text for p in unaccounted_positions]

    return ScanAuditResult(
        amendment_id=amendment_id,
        raw_structural_count=raw_structural_count,
        annotation_covered_count=annotation_covered_count,
        structural_view_count=structural_view_count,
        unaccounted_count=len(unaccounted_positions),
        unaccounted_tokens=unaccounted_tokens,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    """Run peg-audit for one statute."""
    from lawvm.finland.corpus import get_corpus
    from lawvm.finland.grafter import (
        _amendment_children_by_parent,
        get_johtolause,
        OP_KEYWORDS,
    )
    from lawvm.finland.metadata import _normalize_johtolause_verbs
    from lxml import etree

    sid = args.statute_id
    source_filter: Optional[str] = getattr(args, "source", None)

    cs = get_corpus()

    if source_filter:
        # Audit a single amendment
        amendment_ids = [source_filter]
    else:
        # Get all amendments for this statute
        children = _amendment_children_by_parent()
        amendment_ids = list(children.get(sid, []))
        if not amendment_ids:
            print(f"No amendments found for {sid}", file=sys.stderr)
            sys.exit(1)

    any_unaccounted = False

    for amendment_id in amendment_ids:
        xml_bytes = cs.read_source(amendment_id)
        if xml_bytes is None:
            print(f"WARNING: {amendment_id} not in corpus, skipping", file=sys.stderr)
            continue

        johto = get_johtolause(xml_bytes)
        if not johto:
            # Try section 1 fallback (same as dump.py)
            tree = etree.fromstring(xml_bytes)
            sec1 = tree.find(".//{*}section[@eId='sec_1']")
            if sec1 is not None:
                johto = etree.tostring(sec1, method="text", encoding="unicode").strip()
                johto = re.sub(r'^\d+\s*[a-zäöå]?\s*§\s*', '', johto).strip()

        if not johto or len(johto) < 10:
            continue

        johto = _normalize_johtolause_verbs(johto)

        if not any(kw in johto.lower() for kw in OP_KEYWORDS):
            continue

        result = audit_scan_preservation(johto, amendment_id=amendment_id)

        status = "OK" if result.valid else "FAIL"
        print(f"{amendment_id}:")
        print(
            f"  raw_structural: {result.raw_structural_count}"
            f"  annotation_covered: {result.annotation_covered_count}"
            f"  structural_view: {result.structural_view_count}"
            f"  UNACCOUNTED: {result.unaccounted_count} {status}"
        )

        if not result.valid:
            any_unaccounted = True
            for tok_text in result.unaccounted_tokens:
                print(f"    lost: {tok_text!r}")

    if any_unaccounted:
        sys.exit(1)
