"""Per-family annotation-dependence census (grammar7 §13-C/E, §4, §14).

This is the MEASUREMENT half of the annotation-independence experiment. The
toggle lives in :mod:`lawvm.finland.references.ref_mention_extractor`
(``LAWVM_IGNORE_SEMANTIC_ANNOTATIONS`` / the ``ignore_annotations`` parameter
on :func:`extract_all_reference_mentions`): when ON, the AKN ``<ref>``-element
semantic-annotation lane is suppressed and ONLY the text-derived lanes run.

This module QUANTIFIES, per reference family, how much extraction collapses
without ``<ref>``. For each statute it runs ``extract_all_reference_mentions``
TWICE on the SAME bytes — once WITH annotations (toggle OFF = production) and
once WITHOUT (toggle ON) — and, per family:

  with        : mentions produced WITH annotations (production today)
  without     : mentions produced WITHOUT annotations (text lanes only)
  annotation_only : with-targets the text lanes did NOT recover (lost if <ref>
                    were dropped)
  text_recovers   : with-targets the text lanes DID recover (annotation-
                    independent already)

and the grammar7 NEUTRAL per-target classification (§4 / §14):

  both_same_target : the target appears WITH and WITHOUT annotations
  grammar_only     : the target appears only WITHOUT (text lanes found it,
                     <ref> did not) — over-trust of the parser is as wrong as
                     over-trust of <ref>, so this is NEUTRAL, not "annotation
                     bug": some grammar_only is genuine recall, some is parser
                     overreach
  annotation_only  : the target appears only WITH (the <ref> lane found it, the
                     text lanes did not) — NEUTRAL, not "parser miss": some is a
                     real ref the text lanes missed, some is an annotation the
                     text correctly declines

CRUCIAL framing rule (grammar7 §14): statuses stay NEUTRAL. We NEVER label a
delta "annotation bug" or "parser miss". The census reports the delta; judging
which side is right is a downstream, per-case act.

The census runs on BOTH body sources to expose the stratification the session
measured (consolidated/oracle bodies are annotation-RICH; enacted/source bodies
are annotation-POOR):

  ``oracle``  — consolidated / oracle bytes (the live replay pipeline input)
  ``source``  — enacted source bytes

Bonus cheap-signal anchor proxy (grammar7 §4, "the third recall surface"): per
family, count spans carrying a cheap legal signal (``§``, ``NNN/YYYY``,
``artikla``, ``CELEX``, ``HE``, ``SopS``, ``-lain``/``-laissa``, directive/
regulation markers) that yield NO typed mention in the toggle-ON (text-only)
run → candidate residual.

The module is PURE measure-only: it changes no production behaviour and is off
the replay/apply path. It requires the canonical corpus
(``LAWVM_CANONICAL_DATA_ROOT`` / ``LAWVM_FARCHIVE_DB``); the corpus is imported
lazily.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from lawvm.core.reference_mention import CiteKind, ReferenceMention
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

# ---------------------------------------------------------------------------
# Family taxonomy
# ---------------------------------------------------------------------------
#
# A reference FAMILY is the grammar7 unit of the per-family table. We derive it
# from the mention's syntactic class (``phrase_lemma``) and ``cite_kind`` — the
# closed provenance the extractor stamps on every mention. The mapping is
# deterministic and total: an unrecognised lemma lands in ``other`` (fail-loud
# by visibility, never silently dropped).

#: Report order for families.
FAMILIES: tuple[str, ...] = (
    "explicit_id",
    "internal",
    "by_name",
    "eu",
    "treaty",
    "preparatory",
    "vague",
    "metadata",
    "other",
)


def family_of(mention: ReferenceMention) -> str:
    """Classify a ReferenceMention into a grammar7 reference family.

    Deterministic, total: every mention maps to exactly one family; unknown
    lemmas land in ``other`` so nothing disappears.
    """
    lemma = mention.phrase_lemma
    subtype = (mention.edge_subtype or "").upper()

    # The <ref>-element lane stamps phrase_lemma="ref_element" for CITES edges
    # and the edge_type name (REPEALS / ISSUED_UNDER / ISSUES) for metadata
    # edges. Metadata edges are PURE annotation facts (no body surface), so they
    # form their own family — they vanish entirely without <ref> by construction.
    if lemma in ("REPEALS", "ISSUED_UNDER", "ISSUES"):
        return "metadata"
    if lemma == "ref_element":
        # An AKN <ref> CITES edge — typed by where it points.
        if mention.cite_kind is CiteKind.INTERNAL:
            return "internal"
        if mention.cite_kind is CiteKind.EU:
            return "eu"
        return "explicit_id"

    # Text-derived lanes. The inline-(id) plain-text family is, post citation-flip,
    # produced primarily by the construction parse (``citation_construction``); the
    # demoted regex lane survives as a typed residue fallback
    # (``plain_text_fallback``) and the measurement (``ignore_annotations``) path
    # still uses the original ``plain_text``. All three are the SAME explicit-id
    # text family.
    if lemma in ("plain_text", "citation_construction", "plain_text_fallback"):
        return "explicit_id"
    if lemma == "internal_section_ref":
        return "internal"
    if lemma.startswith("statute_name"):
        return "by_name"
    if lemma in ("eu_text_pattern", "eu_directive_nickname_article"):
        return "eu"
    if lemma in ("treaty_sops", "treaty_article"):
        return "treaty"
    if lemma == "preparatory":
        return "preparatory"
    if lemma == "vague_open_catchall":
        return "vague"

    # Metadata edges can also reach here if a metadata edge_subtype leaked into
    # an unexpected lemma; keep them out of the body families.
    if subtype in ("REPEALS", "ISSUED_UNDER", "ISSUES"):
        return "metadata"
    return "other"


def target_key(mention: ReferenceMention) -> str:
    """A comparable target identity for one mention (for the neutral diff).

    The key is the target the citation points AT, at provision granularity, so
    a with-run mention and a without-run mention that name the SAME target
    compare equal regardless of which lane produced them (the grammar7
    ``both_same_target`` notion). Vague/open mentions carry no concrete target;
    they key on their lemma + surface so they still partition without colliding.
    """
    tgt = mention.target_provision_ref
    if tgt is None:
        return f"{mention.phrase_lemma}::{(mention.surface_text or '').strip()}"
    parts = [
        tgt.statute_id or "",
        tgt.provision_path or "",
        tgt.section_label or "",
        str(tgt.subsection_num) if tgt.subsection_num is not None else "",
        tgt.item_label or "",
    ]
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Cheap-signal anchor proxy (grammar7 §4 — the third recall surface)
# ---------------------------------------------------------------------------
#
# A span carrying any of these cheap legal signals is "obviously a reference"
# to a human; if the toggle-ON (text-only) run yields NO typed mention overlapping
# it, it is a candidate residual the text lanes (and very possibly <ref> too)
# missed. This is corroboration, not an oracle.

_CHEAP_SIGNAL_RE = re.compile(
    r"""
      (\b\d{1,6}\s*/\s*(?:19|20)\d{2}\b)   # NNN/YYYY statute id
    | (§)                              # § section mark
    | (\bartikla\w*)                        # EU article
    | (\bCELEX\b)                           # CELEX id
    | (\bHE\s+\d)                           # HE government proposal
    | (\bSopS\b)                            # treaty series
    | (\w+lai(?:n|ssa|sta|ksi|lle|lla|lta))  # -lain / -laissa inflections
    | (\bdirektiivi\w*|\basetuks\w*|\bdirective\b|\bregulation\b)  # directive/regulation markers
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _cheap_signal_spans(text: str) -> list[tuple[int, int]]:
    """Char (start, end) of every cheap-legal-signal hit in ``text``."""
    return [(m.start(), m.end()) for m in _CHEAP_SIGNAL_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class FamilyDependence:
    """Per-family annotation-dependence tally on ONE body source."""

    family: str
    #: Mentions produced WITH annotations (toggle OFF = production today).
    with_count: int = 0
    #: Mentions produced WITHOUT annotations (toggle ON = text lanes only).
    without_count: int = 0
    #: Distinct with-targets the text lanes did NOT recover (lost without <ref>).
    annotation_only: int = 0
    #: Distinct with-targets the text lanes DID recover (annotation-independent).
    text_recovers: int = 0
    #: Distinct targets present WITH and WITHOUT annotations.
    both_same_target: int = 0
    #: Distinct targets present only WITHOUT annotations (text found, <ref> did
    #: not). NEUTRAL — some genuine recall, some parser overreach.
    grammar_only: int = 0
    #: Cheap-signal spans with no overlapping typed mention in the text-only run.
    cheap_signal_residual: int = 0

    @property
    def dependence_ratio(self) -> float:
        """Fraction of distinct with-targets LOST when <ref> is dropped.

        1.0 = fully <ref>-dependent (text lanes recover nothing); 0.0 = fully
        annotation-independent (text lanes recover every with-target).
        """
        denom = self.annotation_only + self.text_recovers
        return self.annotation_only / denom if denom else 0.0


@dataclass
class BodySourceCensus:
    """Annotation-dependence census over one body source (oracle | source)."""

    body_source: str
    statutes_scanned: int = 0
    families: dict[str, FamilyDependence] = field(default_factory=dict)

    def family(self, name: str) -> FamilyDependence:
        fd = self.families.get(name)
        if fd is None:
            fd = FamilyDependence(family=name)
            self.families[name] = fd
        return fd


@dataclass
class AnnotationIndependenceResult:
    """Full result: a census per body source."""

    by_source: dict[str, BodySourceCensus] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core per-statute differential
# ---------------------------------------------------------------------------


def census_one_statute(
    xml_bytes: bytes,
    statute_id: str,
    *,
    body_text: Optional[str] = None,
) -> dict[str, FamilyDependence]:
    """Per-family annotation-dependence for ONE statute body.

    Runs ``extract_all_reference_mentions`` twice on the SAME ``xml_bytes`` —
    WITH annotations (``ignore_annotations=False``) and WITHOUT
    (``ignore_annotations=True``) — and computes the per-family tally and the
    neutral target diff. The toggle is passed explicitly (not via the
    environment) so the two runs are deterministic and side-effect-free.

    ``body_text`` (optional) is the decoded plain-text body used for the
    cheap-signal proxy; when None the proxy is skipped (counts stay 0).
    """
    with_res = extract_all_reference_mentions(
        xml_bytes, statute_id, ignore_annotations=False
    )
    without_res = extract_all_reference_mentions(
        xml_bytes, statute_id, ignore_annotations=True
    )

    out: dict[str, FamilyDependence] = {}

    def fd(fam: str) -> FamilyDependence:
        f = out.get(fam)
        if f is None:
            f = FamilyDependence(family=fam)
            out[fam] = f
        return f

    # Per-family distinct target sets, WITH and WITHOUT.
    with_targets: dict[str, set[str]] = {}
    without_targets: dict[str, set[str]] = {}

    for m in with_res.mentions:
        fam = family_of(m)
        fd(fam).with_count += 1
        with_targets.setdefault(fam, set()).add(target_key(m))
    for m in without_res.mentions:
        fam = family_of(m)
        fd(fam).without_count += 1
        without_targets.setdefault(fam, set()).add(target_key(m))

    # Neutral per-target diff, per family.
    all_fams = set(with_targets) | set(without_targets)
    for fam in all_fams:
        w = with_targets.get(fam, set())
        wo = without_targets.get(fam, set())
        f = fd(fam)
        both = w & wo
        f.both_same_target += len(both)
        f.text_recovers += len(both)           # with-target the text lanes recovered
        f.annotation_only += len(w - wo)       # with-target the text lanes lost
        f.grammar_only += len(wo - w)          # text found it, <ref> did not (NEUTRAL)

    # Cheap-signal proxy: spans with a cheap legal signal but no overlapping
    # typed mention in the TEXT-ONLY (toggle-ON) run → candidate residual.
    if body_text:
        signals = _cheap_signal_spans(body_text)
        if signals:
            # Build covered byte/char windows from the text-only run's surfaces.
            # We approximate coverage at the body-text level by surface-substring
            # presence: a signal whose surrounding window contains a recovered
            # surface counts as covered. (Spans are byte-offset into xml_bytes,
            # not body_text, so a direct overlap test is unavailable here; the
            # surface-substring proxy is the cheap, honest approximation.)
            recovered_surfaces = [
                (m.surface_text or "") for m in without_res.mentions if m.surface_text
            ]
            for s, e in signals:
                window = body_text[max(0, s - 4): e + 8]
                signal_surface = body_text[s:e]
                covered = any(
                    (surf in window) or (signal_surface and signal_surface in surf)
                    for surf in recovered_surfaces
                    if surf
                )
                if not covered:
                    # Attribute the residual to the family the signal most looks
                    # like (a coarse cue), so the proxy is per-family.
                    fd(_signal_family(body_text[s:e])).cheap_signal_residual += 1

    return out


def _signal_family(signal_text: str) -> str:
    """Coarse family attribution for a cheap-signal residual span."""
    t = signal_text.lower()
    if "celex" in t or "artikla" in t or "direktiiv" in t or "directive" in t or "regulation" in t:
        return "eu"
    if "sops" in t:
        return "treaty"
    if t.startswith("he ") or t == "he":
        return "preparatory"
    if "/" in t:
        return "explicit_id"
    if "lai" in t:
        return "by_name"
    if "§" in signal_text:
        return "internal"
    return "other"


# ---------------------------------------------------------------------------
# Corpus run
# ---------------------------------------------------------------------------


def run_annotation_independence_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    body_sources: tuple[str, ...] = ("oracle", "source"),
    cheap_signal: bool = True,
) -> AnnotationIndependenceResult:
    """Run the per-family annotation-dependence census over a corpus sample.

    For each statute in the (optionally sampled) corpus, and for each requested
    body source, run the WITH/WITHOUT differential and accumulate the per-family
    tally. ``oracle`` reads the consolidated/oracle bytes; ``source`` reads the
    enacted source bytes — exposing the annotation richness stratification.

    Sampling: ``min_year`` restricts to statutes enacted in/after that year;
    ``limit`` caps the count. With both 0 the whole corpus is scanned.

    Requires the canonical corpus (``LAWVM_CANONICAL_DATA_ROOT`` /
    ``LAWVM_FARCHIVE_DB``); imports the corpus lazily.
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit:
        ids = ids[:limit]

    readers = {
        "oracle": store.read_oracle,
        "source": store.read_source,
    }

    result = AnnotationIndependenceResult()
    for src_name in body_sources:
        result.by_source[src_name] = BodySourceCensus(body_source=src_name)

    for sid in ids:
        for src_name in body_sources:
            reader = readers.get(src_name)
            if reader is None:
                continue
            xb = reader(sid)
            if not xb:
                continue
            census = result.by_source[src_name]
            census.statutes_scanned += 1

            body_text: Optional[str] = None
            if cheap_signal:
                try:
                    body_text = decode_body_text(xb)
                except Exception as exc:
                    # Unexpected body-decode failure: previously set
                    # ``body_text = None`` silently swallowed; now route
                    # through ``named_swallow`` so a typed Finding is logged
                    # at WARNING with the statute id + source name as
                    # ``clause_text`` (AGENTS.md §1.10 — never silent).
                    from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

                    log_emitter()(
                        build_named_swallow_finding(
                            rule_id="fi_annotation_independence_census_decode_body_text",
                            exception=exc,
                            op_id=None,
                            clause_text=f"sid={sid} src={src_name}",
                            jurisdiction="fi",
                            source_artifact=sid,
                        )
                    )
                    body_text = None

            try:
                per_family = census_one_statute(
                    xb, sid, body_text=body_text
                )
            except Exception as exc:
                # Unexpected census-one-statute failure: previously
                # ``continue`` silently swallowed; now route through
                # ``named_swallow`` so a typed Finding is logged at WARNING
                # with the statute id + source name as ``clause_text``
                # (AGENTS.md §1.10 — never silent).
                from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

                log_emitter()(
                    build_named_swallow_finding(
                        rule_id="fi_annotation_independence_census_one_statute",
                        exception=exc,
                        op_id=None,
                        clause_text=f"sid={sid} src={src_name}",
                        jurisdiction="fi",
                        source_artifact=sid,
                    )
                )
                continue

            for fam, fd in per_family.items():
                agg = census.family(fam)
                agg.with_count += fd.with_count
                agg.without_count += fd.without_count
                agg.annotation_only += fd.annotation_only
                agg.text_recovers += fd.text_recovers
                agg.both_same_target += fd.both_same_target
                agg.grammar_only += fd.grammar_only
                agg.cheap_signal_residual += fd.cheap_signal_residual

    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def format_annotation_independence_report(
    result: AnnotationIndependenceResult,
) -> str:
    """Render the per-family annotation-dependence table for each body source."""
    lines: list[str] = []
    for src_name, census in result.by_source.items():
        lines.append("=" * 96)
        lines.append(
            f"ANNOTATION-DEPENDENCE CENSUS — body source: {src_name}  "
            f"(statutes scanned: {census.statutes_scanned})"
        )
        lines.append("=" * 96)
        header = (
            f"  {'family':<14} {'with':>8} {'without':>8} {'annot_only':>11} "
            f"{'text_rec':>9} {'grammar_only':>13} {'dep%':>7} {'cheap_resid':>12}"
        )
        lines.append(header)
        lines.append("-" * 96)
        # Report in canonical order, then any extra families seen.
        seen = list(census.families)
        order = [f for f in FAMILIES if f in census.families] + [
            f for f in seen if f not in FAMILIES
        ]
        tot = FamilyDependence(family="TOTAL")
        for fam in order:
            fd = census.families[fam]
            tot.with_count += fd.with_count
            tot.without_count += fd.without_count
            tot.annotation_only += fd.annotation_only
            tot.text_recovers += fd.text_recovers
            tot.grammar_only += fd.grammar_only
            tot.cheap_signal_residual += fd.cheap_signal_residual
            lines.append(
                f"  {fam:<14} {fd.with_count:>8} {fd.without_count:>8} "
                f"{fd.annotation_only:>11} {fd.text_recovers:>9} "
                f"{fd.grammar_only:>13} {100 * fd.dependence_ratio:>6.1f}% "
                f"{fd.cheap_signal_residual:>12}"
            )
        lines.append("-" * 96)
        lines.append(
            f"  {'TOTAL':<14} {tot.with_count:>8} {tot.without_count:>8} "
            f"{tot.annotation_only:>11} {tot.text_recovers:>9} "
            f"{tot.grammar_only:>13} {100 * tot.dependence_ratio:>6.1f}% "
            f"{tot.cheap_signal_residual:>12}"
        )
        lines.append("")
        lines.append(
            "  dep% = annotation_only / (annotation_only + text_recovers): "
            "fraction of with-targets LOST without <ref>."
        )
        lines.append(
            "  STATUSES ARE NEUTRAL (grammar7 §14): grammar_only is NOT an "
            "'annotation bug', annotation_only is NOT a 'parser miss'."
        )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry (optional convenience)
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Per-family annotation-dependence census (grammar7 §13-C/E)."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-year", type=int, default=0)
    parser.add_argument(
        "--body-sources",
        default="oracle,source",
        help="comma list of body sources (oracle,source)",
    )
    parser.add_argument("--no-cheap-signal", action="store_true")
    args = parser.parse_args(argv)

    result = run_annotation_independence_census(
        limit=args.limit,
        min_year=args.min_year,
        body_sources=tuple(s.strip() for s in args.body_sources.split(",") if s.strip()),
        cheap_signal=not args.no_cheap_signal,
    )
    print(format_annotation_independence_report(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
