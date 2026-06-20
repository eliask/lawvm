"""Per-family grammar-vs-annotation reliability census (grammar7 §13-C).

This is the MEASUREMENT the grammar7 §13-C step called for: quantify ``<ref>``
reliability PER reference family by contrasting the GRAMMAR-induced reference set
against the ANNOTATION-witness surface, over a real-corpus sample, with the SEVEN
NEUTRAL comparison statuses.

It is distinct from :mod:`annotation_independence_census` (which runs the
extractor WITH vs WITHOUT the ``<ref>`` lane and measures collapse). Here the two
surfaces are:

  GRAMMAR     : the text-derived reference mentions —
                ``extract_all_reference_mentions(ignore_annotations=True)`` (the
                grammar/text lanes alone, NO ``<ref>`` consumption).
  ANNOTATION  : the raw ``<ref>`` elements —
                :func:`lawvm.finland.references.cross_refs.iter_body_annotation_refs`
                (the unmodified markup surface, one record per element).

For each statute, grammar mentions are matched to annotation witnesses by
overlapping BYTE SPAN (the shared coordinate into ``xml_bytes``), one-to-one and
greedy, exactly as the graph-level ``GrammarAnnotationComparePass`` does. The
per-pair verdict and the one-sided leftovers are tallied PER FAMILY (the family
is taken from the GRAMMAR mention for a matched pair / a grammar-only mention,
and inferred from the witness target for an annotation-only witness).

THE SEVEN NEUTRAL STATUSES (grammar7 §13-B / §14):
  both_same_target / both_same_span_diff_target / both_same_target_diff_span /
  grammar_only / annotation_only / both_present_noncomparable.

CRUCIAL (§14): the statuses stay NEUTRAL. grammar_only is NOT an "annotation
bug"; annotation_only is NOT a "parser miss". The census reports the delta; which
side is right is a downstream per-case act.

A THIRD recall surface (grammar7 §4): the cheap-signal anchor proxy — spans
carrying a cheap legal signal (``§``, ``NNN/YYYY``, ``artikla``, ``CELEX``,
``HE``, ``SopS``, ``-lain``/``-laissa``, directive/regulation markers) that yield
NO grammar mention → candidate residual (corroboration, not an oracle).

Measure-only: changes no production behaviour, off the replay/apply path. Run via
``python -m lawvm.finland.references.annotation_witness_census --limit N``.
Requires the canonical corpus (``LAWVM_CANONICAL_DATA_ROOT`` / ``LAWVM_FARCHIVE_DB``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lawvm.core.reference_mention import ReferenceMention
from lawvm.finland.references.annotation_independence_census import (
    FAMILIES,
    _cheap_signal_spans,
    _signal_family,
    family_of,
)
from lawvm.finland.references.cross_refs import (
    AnnotationRefRecord,
    iter_body_annotation_refs,
)
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

# Report order: the grammar7 reference families, plus the noncomparable bucket.
CENSUS_FAMILIES: tuple[str, ...] = FAMILIES


def _grammar_byte_span(m: ReferenceMention) -> Optional[tuple[int, int]]:
    """The grammar mention's authoritative byte span (offset, end), or None."""
    sp = m.source_span
    if sp is None:
        return None
    return (sp.byte_offset, sp.byte_offset + sp.byte_len)


def _witness_byte_span(rec: AnnotationRefRecord) -> Optional[tuple[int, int]]:
    if rec.source_byte_offset is None:
        return None
    return (rec.source_byte_offset, rec.source_byte_offset + rec.source_byte_len)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _grammar_target_key(m: ReferenceMention) -> Optional[str]:
    tgt = m.target_provision_ref
    if tgt is None or not tgt.statute_id:
        return None
    if tgt.provision_path:
        return f"{tgt.statute_id}#{tgt.provision_path}"
    return tgt.statute_id


def _witness_target_key(rec: AnnotationRefRecord) -> Optional[str]:
    if not rec.parsed_ok or not rec.target_statute_id:
        return None
    if rec.target_section:
        return f"{rec.target_statute_id}#{rec.target_section}"
    return rec.target_statute_id


def _witness_family(rec: AnnotationRefRecord) -> str:
    """Coarse family for an annotation-only witness (no grammar mention to type).

    An AKN <ref> body CITES is typed by where it points — the same convention as
    the <ref> lane in :func:`family_of`. An HE government-proposal target is a
    preparatory ref; an internal same-statute target is internal; otherwise it is
    an explicit-id cite. An unparseable href lands in ``other``.
    """
    if not rec.parsed_ok:
        return "other"
    tid = rec.target_statute_id
    if tid.startswith("he/"):
        return "preparatory"
    return "explicit_id"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class FamilyComparison:
    """Per-family 7-way grammar-vs-annotation tally."""

    family: str
    grammar_mentions: int = 0
    annotation_witnesses: int = 0
    both_same_target: int = 0
    both_same_span_diff_target: int = 0
    both_same_target_diff_span: int = 0
    grammar_only: int = 0
    annotation_only: int = 0
    both_present_noncomparable: int = 0
    cheap_signal_residual: int = 0

    @property
    def matched(self) -> int:
        return (
            self.both_same_target
            + self.both_same_span_diff_target
            + self.both_same_target_diff_span
            + self.both_present_noncomparable
        )

    @property
    def target_agree(self) -> int:
        """Matched pairs whose TARGETS agree (span exact OR span-not-comparable).

        both_same_target requires an exact byte-span match too; explicit-id text
        mentions in measurement mode carry no recoverable span, so a genuine
        target agreement lands in both_same_target_diff_span. Reliability is about
        the target, so the agreement metric sums both.
        """
        return self.both_same_target + self.both_same_target_diff_span

    @property
    def agree_pct(self) -> float:
        """Target agreement as a fraction of all annotation witnesses in family.

        High = whenever a <ref> witness is present the grammar agrees on its
        target (the explicit_id signature). Low = the markup and grammar disagree,
        or the grammar does not see what <ref> annotates.
        """
        denom = self.annotation_witnesses
        return self.target_agree / denom if denom else 0.0

    @property
    def annotation_recall_pct(self) -> float:
        """Fraction of grammar mentions that have ANY overlapping <ref> witness.

        Low = the family's references are largely UN-annotated (the grammar must
        carry them); high = <ref> covers the grammar's mentions.
        """
        denom = self.grammar_mentions
        return self.matched / denom if denom else 0.0


@dataclass
class WitnessCensus:
    """Full grammar-vs-annotation census over a corpus sample."""

    statutes_scanned: int = 0
    families: dict[str, FamilyComparison] = field(default_factory=dict)

    def family(self, name: str) -> FamilyComparison:
        fc = self.families.get(name)
        if fc is None:
            fc = FamilyComparison(family=name)
            self.families[name] = fc
        return fc


# ---------------------------------------------------------------------------
# Core per-statute comparison
# ---------------------------------------------------------------------------


def census_one_statute(xml_bytes: bytes, statute_id: str) -> dict[str, FamilyComparison]:
    """Per-family grammar-vs-annotation tally for ONE statute body.

    GRAMMAR = text-only mentions (ignore_annotations=True). ANNOTATION = raw
    body ``<ref>`` records. Matched one-to-one by overlapping byte span, greedy,
    lowest-offset first (deterministic), exactly as the graph compare pass.
    """
    out: dict[str, FamilyComparison] = {}

    def fc(fam: str) -> FamilyComparison:
        f = out.get(fam)
        if f is None:
            f = FamilyComparison(family=fam)
            out[fam] = f
        return f

    grammar_res = extract_all_reference_mentions(
        xml_bytes, statute_id, ignore_annotations=True
    )
    witnesses = iter_body_annotation_refs(xml_bytes)

    # Sorted grammar/witness lists, deterministic. A grammar mention's byte span
    # may be None: in measurement mode (include_ref_text) the explicit-id text
    # lane reads the <ref> INNER text but does not recover a byte offset, so the
    # span coordinate is unavailable even though the TARGET is. We therefore match
    # in TWO phases (grammar7 §13-C reliability is about the TARGET):
    #   phase 1 — byte-span overlap (the precise, span-aware match);
    #   phase 2 — target-key fallback for spanless grammar mentions (same target,
    #             span not comparable → both_same_target_diff_span).
    g_items: list[tuple[Optional[tuple[int, int]], ReferenceMention]] = []
    for m in grammar_res.mentions:
        fc(family_of(m)).grammar_mentions += 1
        g_items.append((_grammar_byte_span(m), m))
    w_items: list[tuple[Optional[tuple[int, int]], AnnotationRefRecord]] = []
    for rec in witnesses:
        fc(_witness_family(rec)).annotation_witnesses += 1
        w_items.append((_witness_byte_span(rec), rec))

    g_items.sort(
        key=lambda kv: ((kv[0][0] if kv[0] else -1), kv[1].surface_text or "")
    )
    w_items.sort(
        key=lambda kv: ((kv[0][0] if kv[0] else -1), kv[1].displayed_text or "")
    )

    used_w: set[int] = set()
    matched_grammar: set[int] = set()

    def _record_pair(fam: str, gkey: Optional[str], wkey: Optional[str], same_span: bool) -> None:
        if gkey is None or wkey is None:
            fc(fam).both_present_noncomparable += 1
        elif gkey == wkey:
            if same_span:
                fc(fam).both_same_target += 1
            else:
                fc(fam).both_same_target_diff_span += 1
        else:
            fc(fam).both_same_span_diff_target += 1

    # Phase 1: byte-span overlap.
    for gi, (g_span, m) in enumerate(g_items):
        if g_span is None:
            continue
        for wi, (w_span, rec) in enumerate(w_items):
            if wi in used_w or w_span is None:
                continue
            if _overlap(g_span, w_span):
                used_w.add(wi)
                matched_grammar.add(gi)
                _record_pair(
                    family_of(m),
                    _grammar_target_key(m),
                    _witness_target_key(rec),
                    same_span=(g_span == w_span),
                )
                break

    # Phase 2: target-key fallback for STILL-unmatched grammar mentions (spanless
    # or span-unmatched) against STILL-unmatched witnesses with the same target.
    w_by_target: dict[str, list[int]] = {}
    for wi, (_w_span, rec) in enumerate(w_items):
        if wi in used_w:
            continue
        wkey = _witness_target_key(rec)
        if wkey is not None:
            w_by_target.setdefault(wkey, []).append(wi)
    for gi, (_g_span, m) in enumerate(g_items):
        if gi in matched_grammar:
            continue
        gkey = _grammar_target_key(m)
        if gkey is None:
            continue
        pool = w_by_target.get(gkey)
        if not pool:
            continue
        wi = pool.pop(0)
        used_w.add(wi)
        matched_grammar.add(gi)
        # Same target, span not comparable → both_same_target_diff_span.
        fc(family_of(m)).both_same_target_diff_span += 1

    # Grammar-only: grammar mentions with no matched witness (NEUTRAL).
    for gi, (_g_span, m) in enumerate(g_items):
        if gi not in matched_grammar:
            fc(family_of(m)).grammar_only += 1
    # Annotation-only: witnesses with no grammar match (NEUTRAL).
    for wi, (_w_span, rec) in enumerate(w_items):
        if wi not in used_w:
            fc(_witness_family(rec)).annotation_only += 1

    return out


def add_cheap_signal(
    per_family: dict[str, FamilyComparison],
    *,
    body_text: str,
    grammar_surfaces: list[str],
) -> None:
    """Add the cheap-signal anchor proxy (grammar7 §4, the third recall surface).

    A span carrying a cheap legal signal whose surrounding window contains NO
    recovered grammar surface → a candidate residual (the grammar AND <ref> may
    both have missed it). Attributed to the family the signal most looks like.
    """
    if not body_text:
        return
    signals = _cheap_signal_spans(body_text)
    for s, e in signals:
        window = body_text[max(0, s - 4): e + 8]
        signal_surface = body_text[s:e]
        covered = any(
            (surf in window) or (signal_surface and signal_surface in surf)
            for surf in grammar_surfaces
            if surf
        )
        if not covered:
            fam = _signal_family(body_text[s:e])
            f = per_family.get(fam)
            if f is None:
                f = FamilyComparison(family=fam)
                per_family[fam] = f
            f.cheap_signal_residual += 1


# ---------------------------------------------------------------------------
# Corpus run
# ---------------------------------------------------------------------------


def run_annotation_witness_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    cheap_signal: bool = True,
) -> WitnessCensus:
    """Run the grammar-vs-annotation census over a corpus sample (oracle bodies).

    ``min_year`` restricts to statutes enacted in/after that year; ``limit`` caps
    the count (after the year filter). Reads consolidated/oracle bytes — the
    annotation-RICH surface (grammar7 §3: enacted bodies are annotation-poor, so
    the oracle bodies are where ``<ref>`` reliability is best measured).
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]

    census = WitnessCensus()
    scanned = 0
    for sid in ids:
        if limit and scanned >= limit:
            break
        xb = store.read_oracle(sid)
        if not xb:
            continue
        scanned += 1
        try:
            per_family = census_one_statute(xb, sid)
        except Exception:
            continue
        if cheap_signal:
            try:
                body_text = decode_body_text(xb)
                grammar_surfaces = [
                    (m.surface_text or "")
                    for m in extract_all_reference_mentions(
                        xb, sid, ignore_annotations=True
                    ).mentions
                    if m.surface_text
                ]
                add_cheap_signal(
                    per_family, body_text=body_text, grammar_surfaces=grammar_surfaces
                )
            except Exception:
                pass
        for fam, fc in per_family.items():
            agg = census.family(fam)
            agg.grammar_mentions += fc.grammar_mentions
            agg.annotation_witnesses += fc.annotation_witnesses
            agg.both_same_target += fc.both_same_target
            agg.both_same_span_diff_target += fc.both_same_span_diff_target
            agg.both_same_target_diff_span += fc.both_same_target_diff_span
            agg.grammar_only += fc.grammar_only
            agg.annotation_only += fc.annotation_only
            agg.both_present_noncomparable += fc.both_present_noncomparable
            agg.cheap_signal_residual += fc.cheap_signal_residual
    census.statutes_scanned = scanned
    return census


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def format_witness_census_report(census: WitnessCensus) -> str:
    """Render the per-family grammar-vs-annotation comparison table."""
    lines: list[str] = []
    lines.append("=" * 120)
    lines.append(
        "GRAMMAR-vs-ANNOTATION RELIABILITY CENSUS — oracle bodies  "
        f"(statutes scanned: {census.statutes_scanned})"
    )
    lines.append("=" * 120)
    header = (
        f"  {'family':<13} {'gram':>7} {'annot':>7} {'b_same_t':>9} "
        f"{'b_diff_t':>9} {'b_diff_sp':>10} {'gram_only':>10} {'annot_only':>11} "
        f"{'noncmp':>7} {'agree%':>8} {'cheap':>7}"
    )
    lines.append(header)
    lines.append("-" * 120)
    order = [f for f in CENSUS_FAMILIES if f in census.families] + [
        f for f in census.families if f not in CENSUS_FAMILIES
    ]
    tot = FamilyComparison(family="TOTAL")
    for fam in order:
        fc = census.families[fam]
        for attr in (
            "grammar_mentions",
            "annotation_witnesses",
            "both_same_target",
            "both_same_span_diff_target",
            "both_same_target_diff_span",
            "grammar_only",
            "annotation_only",
            "both_present_noncomparable",
            "cheap_signal_residual",
        ):
            setattr(tot, attr, getattr(tot, attr) + getattr(fc, attr))
        lines.append(
            f"  {fam:<13} {fc.grammar_mentions:>7} {fc.annotation_witnesses:>7} "
            f"{fc.both_same_target:>9} {fc.both_same_span_diff_target:>9} "
            f"{fc.both_same_target_diff_span:>10} {fc.grammar_only:>10} "
            f"{fc.annotation_only:>11} {fc.both_present_noncomparable:>7} "
            f"{100 * fc.agree_pct:>7.1f}% {fc.cheap_signal_residual:>7}"
        )
    lines.append("-" * 120)
    lines.append(
        f"  {'TOTAL':<13} {tot.grammar_mentions:>7} {tot.annotation_witnesses:>7} "
        f"{tot.both_same_target:>9} {tot.both_same_span_diff_target:>9} "
        f"{tot.both_same_target_diff_span:>10} {tot.grammar_only:>10} "
        f"{tot.annotation_only:>11} {tot.both_present_noncomparable:>7} "
        f"{100 * tot.agree_pct:>7.1f}% {tot.cheap_signal_residual:>7}"
    )
    lines.append("")
    lines.append(
        "  agree% = (both_same_target + both_same_target_diff_span) / "
        "annotation_witnesses: TARGET agreement when <ref> is present (span exact "
        "OR span not comparable). b_diff_sp dominates explicit_id because its text "
        "lane carries no recoverable byte span in measurement mode — the target "
        "still agrees."
    )
    lines.append(
        "  STATUSES ARE NEUTRAL (grammar7 §14): grammar_only is NOT an 'annotation "
        "bug'; annotation_only is NOT a 'parser miss'. The census reports the "
        "delta; adjudication is a downstream per-case act."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Per-family grammar-vs-annotation reliability census (grammar7 §13-C)."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-year", type=int, default=0)
    parser.add_argument("--no-cheap-signal", action="store_true")
    args = parser.parse_args(argv)

    census = run_annotation_witness_census(
        limit=args.limit,
        min_year=args.min_year,
        cheap_signal=not args.no_cheap_signal,
    )
    print(format_witness_census_report(census))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
