"""lawvm bench-triage — classify residual Finlex-oracle bench divergences.

Decision-support for burndown: the residual ~0.5% bench error is a mix of
genuinely-fixable parser gaps and divergences no recognizer fix can close
(the oracle is itself stale/wrong, or the source is irreducibly ambiguous).
This tool partitions a bounded sample of the worst divergent statutes into:

  A. real_parser_gap     — LawVM's reconstruction is wrong/incomplete; a
                           recognizer fix would close it. The ONLY category
                           worth codex burndown.
  B. oracle_error_or_desync — the consolidated Finlex oracle text is itself
                           behind the amendment chain / corrigendum-pending;
                           LawVM is arguably right. Always blame-cited.
  C. irreducibly_ambiguous — source genuinely admits multiple equivalent
                           renderings (editorial convention, OCR source
                           pathology, corpus-reachability limits) where
                           byte-parity is not a meaningful target.
  needs_human            — cannot be classified deterministically; carries the
                           specific question. NOT guessed.

The classification reuses ``oracle_check._classify_statute``, which already
performs blame-attribution of each divergent section to the amendment that
produced it (or to the oracle's own staleness). Each blame diagnosis maps
deterministically onto A/B/C; ambiguous residue is surfaced, never guessed.

Usage:
    lawvm bench-triage --label run_20260619T2232 --top 50
    lawvm bench-triage --label run_20260619T2232 --top 50 --json out.json
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple


# ---------------------------------------------------------------------------
# Diagnosis -> triage-class mapping
#
# The diagnosis vocabulary is produced by oracle_check._classify_statute. Each
# value is mapped to exactly one triage class with a one-line justification.
# Unknown diagnoses are NOT silently bucketed: they route to needs_human so a
# new oracle_check diagnosis can never be misattributed as "fixable" or "noise".
# ---------------------------------------------------------------------------

# B — oracle is wrong/behind; LawVM is arguably right. Always blame-cited.
_DIAG_ORACLE: Dict[str, str] = {
    "ORACLE_STALE": "oracle consolidated text predates the blamed amendment; "
    "LawVM applied it, Finlex has not",
    "CORRIGENDUM_APPLIED": "LawVM applied a corrigendum the oracle has not yet absorbed",
}

# C — equivalent rendering / source-limit; no recognizer fix closes byte-parity.
_DIAG_AMBIGUOUS: Dict[str, str] = {
    "EDITORIAL_CONVENTION": "Finlex editorial convention; both renderings equivalent",
    "SOURCE_PATHOLOGY": "OCR / source-defect; content equal modulo source noise",
    "SOURCE_INCOMPLETE": "repealing/amending act is unreachable, out-of-window, or "
    "contingent-effective — a corpus-reachability limit, not a recognizer gap",
    "RECODIFICATION_SOURCE_CHAIN_GAP": "recodification source chain has a gap; "
    "base text unreconstructable from available corpus",
    "RECODIFICATION_OMISSION_ONLY_SECTION_SHELL": "recodification left an "
    "omission-only shell; nothing to reconstruct",
}

# A — LawVM's reconstruction is wrong/incomplete; a recognizer fix would close it.
# These are the unblamed structural/text divergences that survive every
# oracle-side reclassification pass in _classify_statute.
_DIAG_PARSER_GAP: Dict[str, str] = {
    "REPLAY_MISSING": "LawVM dropped a section/unit the oracle has",
    "MISSING": "LawVM is missing oracle content",
    "REPLAY_EXTRA": "LawVM emitted a section/unit the oracle does not have",
    "EXTRA": "LawVM emitted content absent from the oracle",
    "UNKNOWN": "divergence with no oracle-side blame attribution",
    "REPLAY_UNREPEALED": "repealing act was reachable + in-window + datable, but "
    "LawVM kept the section — a genuine missed-repeal bug",
}

# Annex / liite divergences: structural but not classifiable without inspecting
# the annex body. Surface as needs_human with the concrete question.
_DIAG_NEEDS_HUMAN: Dict[str, str] = {
    "LIITE_DIFF": "annex (liite) count/structure mismatch — inspect annex body "
    "to decide parser-gap vs oracle-side",
    "LIITE_BODY_DIFF": "annex (liite) body text mismatch — inspect annex body "
    "to decide parser-gap vs oracle-side",
}

TriageClass = Literal["A", "B", "C", "needs_human"]

CLASS_LABELS: Dict[str, str] = {
    "A": "real_parser_gap",
    "B": "oracle_error_or_desync",
    "C": "irreducibly_ambiguous",
    "needs_human": "needs_human",
}


def classify_diagnosis(diagnosis: str, blame_source: str) -> Tuple[TriageClass, str]:
    """Map one section diagnosis to a triage class + justification.

    ``blame_source`` is the amendment id _classify_statute attributed the
    divergence to (empty when unblamed). A-family diagnoses that DO carry a
    blame source are ambiguous: oracle_check already promotes blamed cases to
    ORACLE_STALE where the evidence is conclusive, so an A-diagnosis that
    *retains* a blame source means the blame was inconclusive — route to
    needs_human with the cite, never guess.
    """
    if diagnosis in _DIAG_ORACLE:
        return "B", _DIAG_ORACLE[diagnosis]
    if diagnosis in _DIAG_AMBIGUOUS:
        return "C", _DIAG_AMBIGUOUS[diagnosis]
    if diagnosis in _DIAG_NEEDS_HUMAN:
        return "needs_human", _DIAG_NEEDS_HUMAN[diagnosis]
    if diagnosis in _DIAG_PARSER_GAP:
        if blame_source:
            return (
                "needs_human",
                f"{_DIAG_PARSER_GAP[diagnosis]}; but blamed to {blame_source} "
                f"without conclusive oracle-stale promotion — verify whether the "
                f"amendment was correctly applied",
            )
        return "A", _DIAG_PARSER_GAP[diagnosis]
    # Fail loud: a diagnosis the triage table does not know about must not be
    # silently treated as noise OR as fixable.
    return (
        "needs_human",
        f"unmapped oracle_check diagnosis {diagnosis!r} — extend the bench-triage "
        f"diagnosis table",
    )


# ---------------------------------------------------------------------------
# Per-statute triage
# ---------------------------------------------------------------------------


@dataclass
class SectionTriage:
    section: str
    diagnosis: str
    triage_class: TriageClass
    justification: str
    blame_source: str = ""
    blame_title: str = ""
    replay_text: str = ""
    oracle_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "diagnosis": self.diagnosis,
            "triage_class": self.triage_class,
            "class_label": CLASS_LABELS[self.triage_class],
            "justification": self.justification,
            "blame_source": self.blame_source,
            "blame_title": self.blame_title,
            "replay_text": self.replay_text[:240],
            "oracle_text": self.oracle_text[:240],
        }


@dataclass
class StatuteTriage:
    statute_id: str
    similarity: float
    amendments: int
    error: Optional[str] = None
    overall_score: float = 0.0
    sections: List[SectionTriage] = field(default_factory=list)

    @property
    def class_counts(self) -> Counter:
        c: Counter = Counter()
        for s in self.sections:
            c[s.triage_class] += 1
        return c

    @property
    def verdict(self) -> str:
        """Statute-level verdict.

        A statute is ``A`` (worth burndown) iff it has at least one genuine
        parser-gap section. Otherwise it is the dominant of the remaining
        classes. ``needs_human`` only when nothing is class A and the residue
        is undecidable.
        """
        if self.error:
            return "error"
        counts = self.class_counts
        if not counts:
            return "no_divergence"
        if counts.get("A", 0) > 0:
            return "A"
        # No fixable section. Pick the dominant remaining class; a tie or any
        # needs_human residue surfaces as needs_human (do not over-claim B/C).
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_class, top_n = ordered[0]
        if len(ordered) > 1 and ordered[1][1] == top_n:
            return "needs_human"
        if counts.get("needs_human", 0) > 0 and top_class != "needs_human":
            # mixed B/C with an undecidable residue
            return "needs_human"
        return top_class

    @property
    def fixable_sections(self) -> int:
        return self.class_counts.get("A", 0)

    @property
    def ev_score(self) -> float:
        """EV-ordering key for the A-list: fixable sections weighted by the
        structural error contribution (more divergent statutes first)."""
        return self.fixable_sections * (1.0 - self.similarity)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "similarity": self.similarity,
            "amendments": self.amendments,
            "overall_score": self.overall_score,
            "error": self.error,
            "verdict": self.verdict,
            "class_counts": dict(self.class_counts),
            "fixable_sections": self.fixable_sections,
            "ev_score": self.ev_score,
            "sections": [s.to_dict() for s in self.sections],
        }


def triage_statute(
    statute_id: str,
    similarity: float,
    amendments: int,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
) -> StatuteTriage:
    """Run oracle-check classification for one statute and triage each section."""
    from lawvm.tools.oracle_check import _classify_statute

    try:
        result = _classify_statute(statute_id, mode)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:  # noqa: BLE001 - record per-statute failure, keep going
        return StatuteTriage(
            statute_id=statute_id,
            similarity=similarity,
            amendments=amendments,
            error=str(e)[:160],
        )

    if result is None:
        return StatuteTriage(
            statute_id=statute_id,
            similarity=similarity,
            amendments=amendments,
            error="classify returned None (no oracle / content absent)",
        )
    if result.error:
        return StatuteTriage(
            statute_id=statute_id,
            similarity=similarity,
            amendments=amendments,
            error=result.error,
        )

    sections: List[SectionTriage] = []
    for sec in result.section_results:
        diagnosis = str(sec.get("diagnosis", "") or "")
        blame_source = str(sec.get("blame_source", "") or "")
        tclass, justification = classify_diagnosis(diagnosis, blame_source)
        sections.append(
            SectionTriage(
                section=str(sec.get("section", "") or ""),
                diagnosis=diagnosis,
                triage_class=tclass,
                justification=justification,
                blame_source=blame_source,
                blame_title=str(sec.get("blame_title", "") or ""),
                replay_text=str(sec.get("replay_text", "") or ""),
                oracle_text=str(sec.get("oracle_text", "") or ""),
            )
        )

    return StatuteTriage(
        statute_id=statute_id,
        similarity=similarity,
        amendments=amendments,
        overall_score=result.overall_score,
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Bench CSV loading + worst-N selection
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    here = Path(__file__).resolve()
    # src/lawvm/tools/bench_triage.py -> tools -> lawvm -> src -> LawVM
    return here.parent.parent.parent.parent / "data"


def _bench_runs_dir() -> Path:
    return _data_dir() / "bench_runs"


def load_worst_statutes(
    label: Optional[str],
    top: int,
    *,
    runs_dir: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Path]:
    """Load the worst-N divergent OK rows from a bench CSV.

    Ranked by structural error (ascending similarity), tie-broken by Levenshtein
    similarity. NO_TRUTH / SOURCE_UNAVAILABLE / sim==1.0 rows are excluded — a
    perfect statute has nothing to triage.
    """
    rd = runs_dir or _bench_runs_dir()
    if label:
        candidates = sorted(rd.glob(f"*{label}*.csv"))
        candidates = [c for c in candidates if "diagnostics" not in c.name]
    else:
        candidates = sorted(
            c for c in rd.glob("*_run_*.csv") if "diagnostics" not in c.name
        )
    if not candidates:
        raise FileNotFoundError(f"no bench CSV matching label={label!r} under {rd}")
    path = candidates[-1]

    rows: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "OK":
                continue
            try:
                sim = float(row["similarity"])
            except (ValueError, TypeError, KeyError):
                continue
            if sim >= 1.0 or sim < 0:
                continue
            try:
                lev = float(row.get("lev_similarity", 1.0))
            except (ValueError, TypeError):
                lev = 1.0
            try:
                amendments = int(row.get("amendments", 0))
            except (ValueError, TypeError):
                amendments = 0
            rows.append(
                {
                    "statute_id": row["statute_id"],
                    "similarity": sim,
                    "lev_similarity": lev,
                    "amendments": amendments,
                }
            )
    rows.sort(key=lambda r: (r["similarity"], r["lev_similarity"]))
    return rows[:top], path


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------


@dataclass
class TriageReport:
    source_csv: str
    mode: str
    sample_size: int
    statutes: List[StatuteTriage]

    @property
    def verdict_counts(self) -> Counter:
        c: Counter = Counter()
        for s in self.statutes:
            c[s.verdict] += 1
        return c

    @property
    def section_class_counts(self) -> Counter:
        c: Counter = Counter()
        for s in self.statutes:
            c.update(s.class_counts)
        return c

    @property
    def a_list(self) -> List[StatuteTriage]:
        """EV-ranked fixable statutes."""
        return sorted(
            (s for s in self.statutes if s.verdict == "A"),
            key=lambda s: s.ev_score,
            reverse=True,
        )

    def closeable_fraction(self) -> Dict[str, float]:
        """Estimate the closeable fraction of sampled divergent sections.

        Counts only sections (not statutes): the fraction of all divergent
        sections in the sample that are class A (genuinely fixable). B and C
        are NOT closeable by recognizer work; needs_human is undecided.
        """
        cc = self.section_class_counts
        total = sum(cc.values())
        if total == 0:
            return {"total_sections": 0, "closeable_fraction": 0.0}
        return {
            "total_sections": total,
            "A": cc.get("A", 0),
            "B": cc.get("B", 0),
            "C": cc.get("C", 0),
            "needs_human": cc.get("needs_human", 0),
            "closeable_fraction": cc.get("A", 0) / total,
            "closeable_fraction_lo": cc.get("A", 0) / total,
            "closeable_fraction_hi": (cc.get("A", 0) + cc.get("needs_human", 0)) / total,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_csv": self.source_csv,
            "mode": self.mode,
            "sample_size": self.sample_size,
            "verdict_counts": dict(self.verdict_counts),
            "section_class_counts": dict(self.section_class_counts),
            "closeable": self.closeable_fraction(),
            "a_list": [
                {
                    "statute_id": s.statute_id,
                    "similarity": s.similarity,
                    "amendments": s.amendments,
                    "fixable_sections": s.fixable_sections,
                    "ev_score": s.ev_score,
                    "fixable_detail": [
                        sec.to_dict() for sec in s.sections if sec.triage_class == "A"
                    ],
                }
                for s in self.a_list
            ],
            "statutes": [s.to_dict() for s in self.statutes],
        }


def build_report(
    label: Optional[str],
    top: int,
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    *,
    runs_dir: Optional[Path] = None,
    progress: bool = False,
) -> TriageReport:
    worst, path = load_worst_statutes(label, top, runs_dir=runs_dir)
    statutes: List[StatuteTriage] = []
    for i, row in enumerate(worst, 1):
        if progress:
            print(f"  triage [{i}/{len(worst)}] {row['statute_id']}...", flush=True)
        statutes.append(
            triage_statute(
                row["statute_id"],
                row["similarity"],
                row["amendments"],
                mode=mode,
            )
        )
    return TriageReport(
        source_csv=str(path),
        mode=mode,
        sample_size=len(statutes),
        statutes=statutes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_text_report(report: TriageReport) -> str:
    lines: List[str] = []
    lines.append(f"Bench divergence triage  (sample={report.sample_size})")
    lines.append(f"  source: {report.source_csv}")
    lines.append(f"  mode:   {report.mode}")
    lines.append("")
    lines.append("Statute verdicts:")
    for verdict, n in sorted(report.verdict_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {verdict:<14} {n:4}")
    lines.append("")
    lines.append("Divergent sections by class:")
    cc = report.section_class_counts
    total = sum(cc.values()) or 1
    for cls in ("A", "B", "C", "needs_human"):
        n = cc.get(cls, 0)
        lines.append(f"  {cls:<12} {CLASS_LABELS[cls]:<24} {n:4}  ({100*n/total:.1f}%)")
    closeable = report.closeable_fraction()
    lines.append("")
    lines.append(
        f"Closeable fraction of divergent sections: "
        f"{100*closeable.get('closeable_fraction',0):.1f}% "
        f"(A only) .. {100*closeable.get('closeable_fraction_hi',0):.1f}% "
        f"(A + needs_human upper bound)"
    )
    lines.append("")
    lines.append("A-list (EV-ranked fixable statutes — codex burndown targets):")
    for s in report.a_list:
        lines.append(
            f"  {s.statute_id:<14} err={100*(1-s.similarity):.1f}%  "
            f"amend={s.amendments:<3}  fixable_sections={s.fixable_sections}  "
            f"ev={s.ev_score:.3f}"
        )
        for sec in s.sections:
            if sec.triage_class == "A":
                lines.append(f"        {sec.section}: {sec.diagnosis} — {sec.justification}")
    return "\n".join(lines)


def main(args) -> None:
    runs_dir_arg = getattr(args, "runs_dir", None)
    report = build_report(
        getattr(args, "label", None),
        getattr(args, "top", 50),
        mode=getattr(args, "mode", "official_consolidation"),
        runs_dir=Path(runs_dir_arg) if runs_dir_arg else None,
        progress=not getattr(args, "json", None),
    )
    json_path = getattr(args, "json", None)
    if json_path:
        Path(json_path).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {json_path}")
    print(_format_text_report(report))


def register_cli(sub: Any, parents: Any) -> None:
    """Register the 'bench-triage' subcommand."""
    p = sub.add_parser(
        "bench-triage",
        parents=parents,
        help="classify residual bench divergences into A/B/C/needs_human",
        description=(
            "Triage the worst divergent statutes from a bench run into "
            "real_parser_gap (A, worth burndown), oracle_error_or_desync (B), "
            "irreducibly_ambiguous (C), or needs_human."
        ),
    )
    p.add_argument(
        "--label",
        metavar="LABEL",
        help="bench run label substring (default: latest *_run_*.csv)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=50,
        help="number of worst divergent statutes to triage (default: 50)",
    )
    p.add_argument(
        "--mode",
        default="official_consolidation",
        choices=["official_consolidation", "legal_pit"],
        help="replay mode (default: official_consolidation)",
    )
    p.add_argument(
        "--json",
        metavar="PATH",
        help="write the full triage report as JSON to PATH",
    )
    p.add_argument(
        "--runs-dir",
        dest="runs_dir",
        metavar="DIR",
        help="bench_runs directory (default: <repo>/data/bench_runs)",
    )
    p.set_defaults(func=main)
