"""``lawvm fi-he-ir-corpus`` — corpus driver for the HE proposed-effect IR op-diff.

:mod:`lawvm.tools.fi_he_ir_compare` answers the phase-2 accuracy question for ONE HE:
does the HE PDF→proposed-ops path reproduce the trusted HE XML→proposed-ops path EXACTLY?
This module DRIVES that comparison over a sample of HE XML/PDF pairs from
``data/fi_government_proposal.farchive`` and folds the per-HE results into the first
corpus-wide proposed-effect divergence distribution.

The lane is the FREE geom born-digital lane (HEs are born-digital prose — no vision, zero
image tokens), so unlike the phase-1 amendment corpus there is no vision cap: every HE in
the sample is read for free.  Two design commitments carry the driver:

  1. **Clean-gold split.**  Only HEs whose trusted XML carries real proposed AMENDMENT
     ops enter the exact-equivalence comparison.  Wrapper-XML HEs (PDF-only content),
     new-statute-only HEs, treaty/budget HEs, and XML parse gaps are TYPED and counted,
     never diffed against an untrustworthy reference.

  2. **Persist the residual queue.**  Every HE's result is written as one JSONL row
     (he_id, ``compare_status``, ``exact_equivalent``, ``typed_divergence_count``,
     ``counts``, per-divergence ``{kind, target_ref, detail}``) — the residual queue the
     downstream adjudicator consumes.

The orchestration (:func:`run_he_corpus`) is DEPENDENCY-INJECTED: it takes a ``comparer``
(``HEUnit`` → :class:`HECompareResult`), so CI exercises the driver HERMETICALLY with a
fake comparer (no farchive, no geom) while the CLI binds the farchive-backed one.
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.finland.he_johtolause_tagger import FI_HE_JOHTOLAUSE_TAG_STORE
from lawvm.ingest.corroboration import (
    CorroborationReceipt,
    EscalationPending,
    VisionReader,
)
from lawvm.tools.fi_he_ir_compare import (
    _BENIGN_NONCOMPARED_STATUSES,
    _ESCALATION_PENDING_STATUSES,
    HECompareResult,
    OpDivergence,
    compare_he_from_farchive,
)

_DEFAULT_FARCHIVE = "data/fi_government_proposal.farchive"
_AKN_PATH_PREFIX = "akn/fi/doc/government-proposal/"


@dataclass(frozen=True, slots=True)
class HEUnit:
    """One HE XML/PDF pair to compare, identified by year+number."""

    he_year: int
    he_number: int
    he_id: str


@dataclass(frozen=True, slots=True)
class HEDiffRow:
    """One HE's corpus-driver result."""

    he_id: str
    branch_id: str
    compare_status: str
    exact_equivalent: bool
    typed_divergence_count: int
    counts: Dict[str, int]
    xml_op_count: int
    pdf_op_count: int
    detail: str
    divergences: Tuple[OpDivergence, ...]
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0
    #: The CORROBORATE edge, populated ONLY when a vision witness was injected (the free
    #: offline sweep leaves both ``None``). Emitted in ``to_json`` ONLY when present, so a
    #: witness-less row is byte-identical to the pre-corroboration JSONL.
    escalation_pending: Optional[EscalationPending] = None
    corroboration_receipt: Optional[CorroborationReceipt] = None

    def to_json(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "he_id": self.he_id,
            "branch_id": self.branch_id,
            "compare_status": self.compare_status,
            "exact_equivalent": self.exact_equivalent,
            "typed_divergence_count": self.typed_divergence_count,
            "counts": self.counts,
            "xml_op_count": self.xml_op_count,
            "pdf_op_count": self.pdf_op_count,
            "detail": self.detail,
            "payload_compared": self.payload_compared,
            "payload_deferred": self.payload_deferred,
            "payload_skipped": self.payload_skipped,
            "divergences": [
                {"kind": d.kind, "target_ref": d.target_ref, "detail": d.detail}
                for d in self.divergences
            ],
        }
        # Conditional keys: absent on the free (witness-less) sweep so its JSONL is
        # byte-identical; present only once a witness engaged the CORROBORATE edge.
        if self.escalation_pending is not None:
            payload["escalation_pending"] = self.escalation_pending.to_json()
        if self.corroboration_receipt is not None:
            payload["corroboration_receipt"] = self.corroboration_receipt.to_json()
        return payload


def _row_from_result(result: HECompareResult) -> HEDiffRow:
    return HEDiffRow(
        he_id=result.he_id,
        branch_id=result.branch_id,
        compare_status=result.compare_status,
        exact_equivalent=result.exact_equivalent,
        typed_divergence_count=result.typed_divergence_count,
        counts=result.counts,
        xml_op_count=result.xml_op_count,
        pdf_op_count=result.pdf_op_count,
        detail=result.detail,
        divergences=result.divergences,
        payload_compared=result.payload_compared,
        payload_deferred=result.payload_deferred,
        payload_skipped=result.payload_skipped,
        escalation_pending=result.escalation_pending,
        corroboration_receipt=result.corroboration_receipt,
    )


@dataclass(frozen=True, slots=True)
class HECorpusReport:
    """The folded corpus-wide HE proposed-effect divergence distribution."""

    n_attempted: int
    status_counts: Dict[str, int]
    n_compared: int
    n_exact: int
    total_typed_divergences: int
    bucket_counts: Dict[str, int]
    worst: Tuple[HEDiffRow, ...]
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0
    #: Honest partition of the NON-compared rows (a non-compared result is NOT
    #: automatically benign). ``n_benign`` = the explicit allowlist (genuinely nothing
    #: to compare); ``n_escalation_pending`` = vision re-read candidates (``garble_suspect``,
    #: un-accounted); ``n_pathology`` = typed potential defects / deferrals (un-accounted).
    #: A "0 divergences" pass with a nonzero escalation/pathology surface is NOT clean.
    n_benign: int = 0
    n_escalation_pending: int = 0
    n_pathology: int = 0
    escalation_counts: Dict[str, int] = field(default_factory=dict)
    pathology_counts: Dict[str, int] = field(default_factory=dict)
    #: CORROBORATE-edge receipt statistics (0 / empty on the free witness-less sweep).
    #: ``n_escalation_resolved`` = escalation-pending units a vision witness produced a
    #: receipt for; ``n_agreed`` / ``n_verdict_changed`` fold the receipts' verdicts. The
    #: receipts are the statistics substrate for later deriving the operating point.
    n_escalation_resolved: int = 0
    n_agreed: int = 0
    n_verdict_changed: int = 0
    receipts: Tuple[CorroborationReceipt, ...] = ()

    @property
    def exact_match_rate(self) -> float:
        return (self.n_exact / self.n_compared) if self.n_compared else 0.0


def _rank_worst(rows: Sequence[HEDiffRow], limit: int) -> Tuple[HEDiffRow, ...]:
    """Compared rows with typed divergences, worst-first (deterministic)."""
    hot = [
        r for r in rows if r.compare_status == "compared" and r.typed_divergence_count > 0
    ]
    hot.sort(key=lambda r: (-r.typed_divergence_count, r.he_id))
    return tuple(hot[:limit])


def aggregate_rows(rows: Sequence[HEDiffRow], *, worst_limit: int = 15) -> HECorpusReport:
    """Fold the per-HE rows into the corpus divergence distribution."""
    status_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {
        "op_missing_in_pdf": 0,
        "op_extra_in_pdf": 0,
        "kind_mismatch": 0,
        "payload_mismatch": 0,
        # First-class WITNESS DISAGREEMENT (PDF out-read a narrow XML op-set on an omnibus
        # HE's out-of-scope second bill), NOT a PDF op_extra defect — tallied in its OWN
        # bucket so the op_extra_in_pdf defect count stays honest (metric integrity).
        "pdf_out_of_scope_statute": 0,
        # First-class WITNESS DISAGREEMENT: a consequential repeal of an outside act in
        # another bill's commencement clause, which the law-level XML op-set omits (PDF more
        # complete) — its own bucket, distinct from a titled second bill and from a defect.
        "pdf_consequential_repeal": 0,
    }
    n_compared = n_exact = total_typed = 0
    payload_compared = payload_deferred = payload_skipped = 0
    n_benign = n_escalation = n_pathology = 0
    escalation_counts: Dict[str, int] = {}
    pathology_counts: Dict[str, int] = {}
    n_resolved = n_agreed = n_verdict_changed = 0
    receipts: List[CorroborationReceipt] = []
    for r in rows:
        status_counts[r.compare_status] = status_counts.get(r.compare_status, 0) + 1
        # CORROBORATE-edge receipts (present only when a witness was injected). A resolved
        # unit is one the witness produced a receipt for; agreed / verdict_changed fold its
        # verdict. Un-resolvable escalation-pending rows carry no receipt (stay pending).
        if r.corroboration_receipt is not None:
            receipts.append(r.corroboration_receipt)
            n_resolved += 1
            if r.corroboration_receipt.agreed:
                n_agreed += 1
            if r.corroboration_receipt.verdict_changed:
                n_verdict_changed += 1
        if r.compare_status == "compared":
            n_compared += 1
            if r.exact_equivalent:
                n_exact += 1
            total_typed += r.typed_divergence_count
            for k, v in r.counts.items():
                if k in bucket_counts:
                    bucket_counts[k] += v
            payload_compared += r.payload_compared
            payload_deferred += r.payload_deferred
            payload_skipped += r.payload_skipped
        elif r.compare_status in _BENIGN_NONCOMPARED_STATUSES:
            n_benign += 1
        elif r.compare_status in _ESCALATION_PENDING_STATUSES:
            n_escalation += 1
            escalation_counts[r.compare_status] = escalation_counts.get(r.compare_status, 0) + 1
        else:
            # Anything non-compared and not benign is a potential defect / deferral —
            # un-accounted, never folded into the clean count (includes any unrecognised
            # status, so a new one can never silently read as benign).
            n_pathology += 1
            pathology_counts[r.compare_status] = pathology_counts.get(r.compare_status, 0) + 1
    return HECorpusReport(
        n_attempted=len(rows),
        status_counts=status_counts,
        n_compared=n_compared,
        n_exact=n_exact,
        total_typed_divergences=total_typed,
        bucket_counts=bucket_counts,
        worst=_rank_worst(rows, worst_limit),
        payload_compared=payload_compared,
        payload_deferred=payload_deferred,
        payload_skipped=payload_skipped,
        n_benign=n_benign,
        n_escalation_pending=n_escalation,
        n_pathology=n_pathology,
        escalation_counts=escalation_counts,
        pathology_counts=pathology_counts,
        n_escalation_resolved=n_resolved,
        n_agreed=n_agreed,
        n_verdict_changed=n_verdict_changed,
        receipts=tuple(receipts),
    )


@dataclass
class _JsonlSink:
    """Append-only JSONL sink; a ``None`` path buffers rows in memory (tests)."""

    path: Optional[str]
    rows: List[Dict[str, object]] = field(default_factory=list)

    def write(self, row: HEDiffRow) -> None:
        payload = row.to_json()
        self.rows.append(payload)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_he_corpus(
    units: Sequence[HEUnit],
    comparer: Callable[[HEUnit], HECompareResult],
    *,
    out_path: Optional[str] = None,
    worst_limit: int = 15,
    progress: Optional[Callable[[int, int, HEDiffRow], None]] = None,
) -> HECorpusReport:
    """Drive the HE proposed-op diff over ``units`` and fold the distribution.

    Each unit's result is persisted as a JSONL row and folded into the returned report.
    A comparer that raises is turned into a typed ``error`` row so one bad HE never sinks
    the run.
    """
    sink = _JsonlSink(out_path)
    rows: List[HEDiffRow] = []
    total = len(units)
    for i, unit in enumerate(units):
        try:
            result = comparer(unit)
        except Exception as exc:  # one bad HE never sinks the corpus run
            result = HECompareResult(
                unit.he_id,
                f"fi/he/{unit.he_year}/{unit.he_number}",
                "error",
                (),
                0,
                0,
                f"{type(exc).__name__}: {exc}",
            )
        row = _row_from_result(result)
        rows.append(row)
        sink.write(row)
        if progress is not None:
            progress(i + 1, total, row)
    return aggregate_rows(rows, worst_limit=worst_limit)


# --------------------------------------------------------------------------- #
# Corpus enumeration + rendering.                                             #
# --------------------------------------------------------------------------- #


def enumerate_he_units(
    farchive: str, *, sample: Optional[int] = None, seed: int = 0
) -> List[HEUnit]:
    """Enumerate HE units that have BOTH a fin@ main.xml and main.pdf, optionally sampled.

    A deterministic ``seed`` makes the random sample reproducible; without ``sample`` the
    full paired set is returned in sorted (year, number) order.
    """
    from farchive import Farchive

    fa = Farchive(farchive)
    try:
        locs = [
            loc
            for loc in fa.locators()
            if isinstance(loc, str) and loc.startswith(_AKN_PATH_PREFIX)
        ]
    finally:
        fa.close()
    xml_keys: set[Tuple[int, int]] = set()
    pdf_keys: set[Tuple[int, int]] = set()
    for loc in locs:
        rest = loc[len(_AKN_PATH_PREFIX):].split("/")
        if len(rest) < 4:
            continue
        try:
            yr, num = int(rest[0]), int(rest[1])
        except ValueError:
            continue
        if loc.endswith("/fin@/main.xml"):
            xml_keys.add((yr, num))
        elif loc.endswith("/fin@/main.pdf"):
            pdf_keys.add((yr, num))
    paired = sorted(xml_keys & pdf_keys)
    if sample is not None and sample < len(paired):
        rng = random.Random(seed)
        paired = sorted(rng.sample(paired, sample))
    return [HEUnit(yr, num, f"HE {num}/{yr} vp") for yr, num in paired]


def build_llm_johtolause_classify_fn(
    *,
    base_url: Optional[str] = None,
    store_path: str = FI_HE_JOHTOLAUSE_TAG_STORE,
) -> Tuple[Callable[[str], object], Callable[[], None]]:
    """Build the LLM johtolause ``classify_fn`` + a ``close`` for its cache store.

    Wires the determinism-firewall cache (:class:`JohtolauseTagStore` at ``store_path``) to a
    real local-LLM transport (:class:`LlmWorkflowAdjudicator._chat`, region_locator
    ``'johtolause_tag'``). The returned callable is ``window -> JohtolauseTag``: a cache HIT
    is free, a MISS makes ONE chat call and persists the tag content-addressed. The tag is a
    pure function of ``(window, tagger_id, prompt)``; the model id is folded into
    ``tagger_id`` so re-runs are stable and a model swap re-keys.
    """
    from lawvm.finland.he_johtolause_tagger import (
        JohtolauseTagStore,
        classify_candidate_cached,
    )
    from lawvm.ingest.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator

    adj = (
        LlmWorkflowAdjudicator(base_url=base_url)
        if base_url
        else LlmWorkflowAdjudicator()
    )
    tagger_id = f"llm_workflow:{adj._resolve_model()}"
    store = JohtolauseTagStore(store_path)

    def chat_fn(system: str, user: str) -> str:
        return adj._chat(system, user, region_locator="johtolause_tag")

    def classify_fn(window: str) -> object:
        return classify_candidate_cached(
            window, chat_fn=chat_fn, tagger_id=tagger_id, store=store
        ).tag

    return classify_fn, store.close


def make_comparer(
    farchive: str = _DEFAULT_FARCHIVE,
    *,
    max_pages: int = 5000,
    llm_johtolause: bool = False,
    johtolause_cache: str = FI_HE_JOHTOLAUSE_TAG_STORE,
    base_url: Optional[str] = None,
    vision_reader: Optional[VisionReader] = None,
    witness_prompt: str = "",
    witness_model: str = "",
) -> Callable[[HEUnit], HECompareResult]:
    """Build the farchive-backed ``comparer`` (HEUnit → :class:`HECompareResult`).

    With ``llm_johtolause=False`` (default) the mechanical, char-bounded enacting-clause
    segmentation runs (no LLM, no behaviour change). With ``llm_johtolause=True`` a real
    cache-through LLM johtolause classifier is bound (store at ``johtolause_cache``, transport
    at ``base_url`` or the adjudicator's default :8080) and threaded into every comparison so
    whole mega-amendment bills are recovered rather than dropped.

    ``vision_reader`` is the OPTIONAL vision witness for the CORROBORATE edge, threaded to
    :func:`~lawvm.tools.fi_he_ir_compare.compare_he_from_farchive`. ``None`` (default) is the
    free offline sweep — every result is byte-identical to today. When injected, an
    escalation-pending unit is corroborated and carries the receipt (fingerprinted by
    ``witness_prompt`` / ``witness_model``).
    """
    classify_fn: Optional[Callable[[str], object]] = None
    if llm_johtolause:
        classify_fn, _close = build_llm_johtolause_classify_fn(
            base_url=base_url, store_path=johtolause_cache
        )

    def comparer(unit: HEUnit) -> HECompareResult:
        return compare_he_from_farchive(
            farchive,
            unit.he_year,
            unit.he_number,
            he_id=unit.he_id,
            max_pages=max_pages,
            classify_fn=classify_fn,
            vision_reader=vision_reader,
            witness_prompt=witness_prompt,
            witness_model=witness_model,
        )

    return comparer


def render_report(report: HECorpusReport) -> str:
    """Deterministic text render of the corpus divergence distribution."""
    lines: List[str] = []
    lines.append("# fi-he-ir-corpus — HE proposed-effect IR op-diff distribution (phase 2)")
    lines.append(f"# attempted={report.n_attempted}")
    lines.append("")
    lines.append("## STATUS STRATA")
    for status in sorted(report.status_counts):
        lines.append(f"  {status:<26} {report.status_counts[status]}")
    lines.append("")
    lines.append("## HONEST ACCOUNTING (a non-compared row is NOT automatically benign)")
    lines.append(
        f"  verified(exact)={report.n_exact}  typed_divergence={report.n_compared - report.n_exact}"
        f"  genuinely_benign={report.n_benign}"
    )
    lines.append(
        f"  escalation_pending(vision re-read)={report.n_escalation_pending} "
        f"{dict(sorted(report.escalation_counts.items()))}"
    )
    lines.append(
        f"  corroborated(vision witness)={report.n_escalation_resolved} "
        f"agreed={report.n_agreed} verdict_changed={report.n_verdict_changed}"
    )
    lines.append(
        f"  pathology/un-accounted={report.n_pathology} "
        f"{dict(sorted(report.pathology_counts.items()))}"
    )
    lines.append("")
    lines.append("## OVER THE 'compared' SET (clean born-digital gold, XML has amendment ops)")
    lines.append(
        f"  compared={report.n_compared}  exact_equivalent={report.n_exact}  "
        f"exact_match_rate={report.exact_match_rate:.4f}"
    )
    lines.append(f"  total_genuine_typed_divergences={report.total_typed_divergences}")
    lines.append(
        f"  buckets: op_missing_in_pdf={report.bucket_counts['op_missing_in_pdf']}  "
        f"op_extra_in_pdf={report.bucket_counts['op_extra_in_pdf']}  "
        f"kind_mismatch={report.bucket_counts['kind_mismatch']}  "
        f"payload_mismatch={report.bucket_counts.get('payload_mismatch', 0)}"
    )
    lines.append(
        "  witness_disagreement (NOT a PDF defect): "
        f"pdf_out_of_scope_statute={report.bucket_counts.get('pdf_out_of_scope_statute', 0)}  "
        f"pdf_consequential_repeal={report.bucket_counts.get('pdf_consequential_repeal', 0)}"
    )
    lines.append(
        f"  payload stage: compared={report.payload_compared}  "
        f"deferred={report.payload_deferred}  no_body_skipped={report.payload_skipped}"
    )
    lines.append("")
    lines.append("## RANKED WORST (compared, by typed_divergence_count)")
    lines.append("rank,he_id,typed,xml_ops,pdf_ops,kinds")
    for rank, r in enumerate(report.worst, start=1):
        kinds = "|".join(
            f"{d.kind}:{d.target_ref}" for d in r.divergences if d.kind != "matched"
        )
        lines.append(
            f"{rank},{r.he_id},{r.typed_divergence_count},"
            f"{r.xml_op_count},{r.pdf_op_count},{kinds}"
        )
    return "\n".join(lines)


def report_to_json(report: HECorpusReport) -> Dict[str, object]:
    return {
        "n_attempted": report.n_attempted,
        "status_counts": report.status_counts,
        "n_compared": report.n_compared,
        "n_exact": report.n_exact,
        "exact_match_rate": report.exact_match_rate,
        "total_typed_divergences": report.total_typed_divergences,
        "n_benign": report.n_benign,
        "n_escalation_pending": report.n_escalation_pending,
        "n_pathology": report.n_pathology,
        "escalation_counts": report.escalation_counts,
        "pathology_counts": report.pathology_counts,
        # CORROBORATE-edge receipt statistics + the receipts themselves (0 / [] on the free
        # witness-less sweep; the receipt store is the substrate for the empirical operating
        # point). Additive keys — they do NOT touch the per-row JSONL the free lane emits.
        "n_escalation_resolved": report.n_escalation_resolved,
        "n_agreed": report.n_agreed,
        "n_verdict_changed": report.n_verdict_changed,
        "receipts": [rcpt.to_json() for rcpt in report.receipts],
        "bucket_counts": report.bucket_counts,
        "payload_compared": report.payload_compared,
        "payload_deferred": report.payload_deferred,
        "payload_skipped": report.payload_skipped,
        "worst": [
            {
                "he_id": r.he_id,
                "typed_divergence_count": r.typed_divergence_count,
                "xml_op_count": r.xml_op_count,
                "pdf_op_count": r.pdf_op_count,
                "divergences": [
                    {"kind": d.kind, "target_ref": d.target_ref, "detail": d.detail}
                    for d in r.divergences
                    if d.kind != "matched"
                ],
            }
            for r in report.worst
        ],
    }


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-he-ir-corpus``."""
    farchive = args.farchive or _DEFAULT_FARCHIVE
    units = enumerate_he_units(farchive, sample=args.sample, seed=args.seed)
    if args.limit:
        units = units[: args.limit]
    out_path = args.out
    if out_path:
        open(out_path, "w", encoding="utf-8").close()

    comparer = make_comparer(
        farchive,
        max_pages=args.max_pages,
        llm_johtolause=getattr(args, "llm_johtolause", False),
        johtolause_cache=getattr(args, "johtolause_cache", None) or FI_HE_JOHTOLAUSE_TAG_STORE,
        base_url=getattr(args, "base_url", None) or None,
    )

    def progress(done: int, total: int, row: HEDiffRow) -> None:
        print(
            f"[{done}/{total}] {row.he_id} status={row.compare_status} "
            f"typed={row.typed_divergence_count}",
            flush=True,
        )

    report = run_he_corpus(
        units,
        comparer,
        out_path=out_path,
        progress=progress if args.verbose else None,
    )
    if args.json:
        print(json.dumps(report_to_json(report), ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
