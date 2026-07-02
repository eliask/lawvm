"""temporal_holdout.py — the frozen-catalog temporal-holdout generalization
experiment (#182, from ``FABLE_SPEC_RECONSTRUCTION.md`` §6.2).

The falsifiability experiment for the "we reconstructed the spec, not memorized
the corpus" claim. The reconstructed spec (witness_rule_ids + editorial rules)
was *mined from bench failures on the corpus the bench measures* — so the bench
% is **train accuracy**, and nothing in the ordinary gate distinguishes it from
test accuracy (§6.2 "the uncomfortable fact"). A held-out slice tests whether
the spec **generalizes** (small train↔holdout gap) or **overfits** (large drop
on unseen law).

Framing (honest, per the task):

    A literal "next Finlex release" is not available offline. So this is a
    RETROSPECTIVE, not prospective, holdout: we partition each statute's already
    published-consolidation anchors by a cutoff date T. Every anchor whose
    ``as_of > T`` is unseen-by-construction *relative to the earlier anchors of
    the same statute* — the later snapshots of a statute's life are exactly the
    "future" the dev loop tuned against the earlier ones. The corpus-regen at
    #130 is the partial *prospective* instance (a genuine later Finlex slice);
    this module is the backward-pointing sibling of that experiment (the same
    idea the all_pit aux target embodies: each historical snapshot the dev loop
    never explicitly compared against is quasi-held-out).

    This is weaker than a prospective holdout (dev-loop leakage: a rule mined
    from a late anchor of statute A can shape early anchors of statute B). It is
    still the strongest cheap generalization signal available offline, and the
    generalization gap it measures is a real, directional falsifier: a spec that
    memorized would show holdout ≪ training; a spec that generalizes shows
    holdout ≈ training.

This module is **ADDITIVE**. It reuses the frozen-anchor / all_pit scoring path
(:func:`lawvm.tools.fi_anchor_manifest.attribute_statute`) verbatim — the
per-anchor ``struct_sim`` it consumes is byte-commensurable with the headline
bench number — and never mutates replay, apply, the grafter, scoring, or the
corpus. The split + gap computation is a pure function over already-scored
:class:`~lawvm.tools.fi_anchor_manifest.AnchorObservation` lists, so it is unit
testable with synthetic fixtures and no corpus build.

Keep runs BOUNDED: the naive full-3546 all_pit sweep deadlocks, so the CLI takes
an explicit small ``--corpus`` list (or reads one from a file).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.tools.fi_anchor_manifest import (
    AnchorObservation,
    StatuteAttribution,
    TouchObservation,
    attribute_statute,
    observation_to_residual,
)


HOLDOUT_SCHEMA = "lawvm.temporal_holdout.v1"


# ---------------------------------------------------------------------------
# The split: partition a statute's scored anchors by a cutoff date T
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldoutSplit:
    """One statute's anchors partitioned by the cutoff date T.

    ``training`` = anchors with ``as_of <= T`` (the era the spec was tuned on).
    ``holdout``  = anchors with ``as_of >  T`` (unseen-by-construction).
    Only scored anchors (``struct_sim >= 0``, i.e. placeable and commensurable)
    participate — an UNPLACEABLE / ORACLE_CONTENT_ABSENT anchor is neither.
    """

    sid: str
    cutoff: str
    training: tuple[AnchorObservation, ...]
    holdout: tuple[AnchorObservation, ...]

    @property
    def n_training(self) -> int:
        return len(self.training)

    @property
    def n_holdout(self) -> int:
        return len(self.holdout)

    @property
    def is_informative(self) -> bool:
        """True iff BOTH sides carry at least one scored anchor.

        A statute all of whose anchors fall on one side of T cannot exhibit a
        train↔holdout gap, so it contributes to neither mean (it would bias the
        aggregate toward whichever side it lands on). It is still reported, but
        excluded from the gap.
        """
        return self.n_training > 0 and self.n_holdout > 0


def split_anchors(
    anchors: list[AnchorObservation] | tuple[AnchorObservation, ...],
    cutoff: str,
    *,
    sid: str = "",
) -> HoldoutSplit:
    """Partition scored anchors by the cutoff date T (ISO ``YYYY-MM-DD``).

    Pure function — no corpus. Anchors with no ``as_of`` or ``struct_sim < 0``
    (UNPLACEABLE / ORACLE_CONTENT_ABSENT / errored) are dropped from both sides:
    they are unobserved, not train and not test. ISO-date string comparison is
    exact (fixed-width, zero-padded), so lexical ``<=`` is chronological ``<=``.
    """
    training: list[AnchorObservation] = []
    holdout: list[AnchorObservation] = []
    for a in anchors:
        if a.as_of is None or a.struct_sim < 0.0:
            continue
        if a.as_of <= cutoff:
            training.append(a)
        else:
            holdout.append(a)
    return HoldoutSplit(
        sid=sid,
        cutoff=cutoff,
        training=tuple(training),
        holdout=tuple(holdout),
    )


def _mean_struct_sim(anchors: tuple[AnchorObservation, ...]) -> Optional[float]:
    """Mean per-anchor ``struct_sim`` (accuracy), or None when no anchors.

    ``struct_sim`` is the bench-commensurable per-anchor structural similarity
    (1.0 = every non-editorial section matches). The mean over a side's anchors
    is that side's mean replay accuracy — the quantity the generalization gap
    compares.
    """
    if not anchors:
        return None
    return sum(a.struct_sim for a in anchors) / len(anchors)


# ---------------------------------------------------------------------------
# The generalization signal: per-statute train-vs-holdout gap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteHoldout:
    """One statute's train-vs-holdout accuracy comparison."""

    sid: str
    cutoff: str
    split: HoldoutSplit
    train_acc: Optional[float]
    holdout_acc: Optional[float]
    holdout_residuals: tuple[AgreementResidual, ...] = ()

    @property
    def gap(self) -> Optional[float]:
        """train_acc − holdout_acc (positive ⇒ accuracy DROPPED on holdout).

        Only defined for informative statutes (both sides populated); a positive
        gap is the overfitting direction, ~0 is the generalization direction, a
        negative gap means the spec is *better* on unseen-later law (which the
        oracle-error lane can genuinely produce).
        """
        if self.train_acc is None or self.holdout_acc is None:
            return None
        return self.train_acc - self.holdout_acc

    @property
    def is_informative(self) -> bool:
        return self.split.is_informative

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "cutoff": self.cutoff,
            "n_training": self.split.n_training,
            "n_holdout": self.split.n_holdout,
            "train_acc": self.train_acc,
            "holdout_acc": self.holdout_acc,
            "gap": self.gap,
            "informative": self.is_informative,
            "holdout_residuals": [r.to_dict() for r in self.holdout_residuals],
        }


def _window_end(obs: TouchObservation) -> Optional[str]:
    """Right endpoint of an observation window ``"t0..t1"`` (the divergence date)."""
    _, _, tail = obs.window.partition("..")
    return tail or None


def _holdout_residuals(
    attribution: StatuteAttribution, split: HoldoutSplit
) -> tuple[AgreementResidual, ...]:
    """Project the touch-attribution observations that land in the HOLDOUT era.

    Reuses the existing §3.3 touch-relation attribution (already computed by
    :func:`attribute_statute`) and the shared AgreementResidual taxonomy. A
    holdout-era divergence is one whose observation window ENDS after T, i.e.
    its right endpoint ``as_of`` is a holdout anchor. This is what lets the
    report say *why* holdout accuracy diverged, in the same typed vocabulary the
    rest of the metric uses (oracle_editorial_pathology vs replay_bug vs …).
    """
    holdout_dates = {a.as_of for a in split.holdout if a.as_of}
    rows: list[AgreementResidual] = []
    for obs in attribution.observations:
        if _window_end(obs) in holdout_dates:
            rows.append(observation_to_residual(obs))
    return tuple(rows)


def compute_statute_holdout(
    attribution: StatuteAttribution, cutoff: str
) -> StatuteHoldout:
    """Compute one statute's holdout comparison from a scored attribution.

    Pure over the attribution (no corpus) — the corpus work already happened in
    :func:`attribute_statute`. This is the composable seam the tests exercise.
    """
    split = split_anchors(attribution.anchors, cutoff, sid=attribution.sid)
    return StatuteHoldout(
        sid=attribution.sid,
        cutoff=cutoff,
        split=split,
        train_acc=_mean_struct_sim(split.training),
        holdout_acc=_mean_struct_sim(split.holdout),
        holdout_residuals=_holdout_residuals(attribution, split),
    )


# ---------------------------------------------------------------------------
# Corpus aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusHoldout:
    """Corpus-level generalization signal: mean train vs mean holdout accuracy.

    The aggregate means are taken over INFORMATIVE statutes only (both sides
    populated), so the gap is apples-to-apples: each contributing statute
    supplies one train-side mean and one holdout-side mean. ``mean_gap`` is the
    mean of the per-statute gaps — the headline generalization number.
    """

    cutoff: str
    statutes: tuple[StatuteHoldout, ...]
    errors: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def statute_count(self) -> int:
        return len(self.statutes)

    @property
    def informative(self) -> tuple[StatuteHoldout, ...]:
        return tuple(s for s in self.statutes if s.is_informative)

    @property
    def n_informative(self) -> int:
        return len(self.informative)

    @property
    def mean_train_acc(self) -> Optional[float]:
        accs = [s.train_acc for s in self.informative if s.train_acc is not None]
        return sum(accs) / len(accs) if accs else None

    @property
    def mean_holdout_acc(self) -> Optional[float]:
        accs = [s.holdout_acc for s in self.informative if s.holdout_acc is not None]
        return sum(accs) / len(accs) if accs else None

    @property
    def mean_gap(self) -> Optional[float]:
        """Mean per-statute (train − holdout) gap over informative statutes.

        This is the headline generalization number. ~0 ⇒ the spec generalizes;
        large positive ⇒ accuracy dropped on unseen-later law (overfitting or
        undiscovered spec on newer law); negative ⇒ the spec is better on the
        held-out era (an oracle-error signature — the held-out later consolidation
        is more often the faulty one).
        """
        gaps = [s.gap for s in self.informative if s.gap is not None]
        return sum(gaps) / len(gaps) if gaps else None

    @property
    def holdout_residual_family_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.informative:
            for r in s.holdout_residuals:
                counts[r.family] = counts.get(r.family, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": HOLDOUT_SCHEMA,
            "jurisdiction": "finland",
            "framing": "retrospective_cutoff_holdout",
            "cutoff": self.cutoff,
            "statute_count": len(self.statutes),
            "informative_statute_count": self.n_informative,
            "mean_train_acc": self.mean_train_acc,
            "mean_holdout_acc": self.mean_holdout_acc,
            "mean_gap": self.mean_gap,
            "holdout_residual_family_counts": self.holdout_residual_family_counts,
            "statutes": [s.to_dict() for s in self.statutes],
            "errors": [{"sid": sid, "error": err} for sid, err in self.errors],
        }


def run_corpus_holdout(
    sids: list[str], cutoff: str, *, corpus: Any = None
) -> CorpusHoldout:
    """Score each statute (via ``attribute_statute``) then split by T.

    BOUNDED: pass an explicit small ``sids`` list — the full-corpus all_pit sweep
    deadlocks. Runs statutes serially and deterministically (sorted input), so
    the report is reproducible byte-for-byte across runs on a fixed corpus.
    """
    from lawvm.finland.corpus import get_corpus

    if corpus is None:
        corpus = get_corpus()

    results: list[StatuteHoldout] = []
    errors: list[tuple[str, str]] = []
    for sid in sorted(set(sids)):
        try:
            attribution = attribute_statute(sid, corpus=corpus)
        except Exception as exc:  # noqa: BLE001 — one bad statute must not sink the sweep
            errors.append((sid, f"attribute-error:{exc}"))
            continue
        if attribution.status != "OK":
            errors.append((sid, attribution.status))
            continue
        results.append(compute_statute_holdout(attribution, cutoff))
    return CorpusHoldout(
        cutoff=cutoff, statutes=tuple(results), errors=tuple(errors)
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_corpus_arg(values: list[str]) -> list[str]:
    """A --corpus list is either inline sids or @file (one sid per line)."""
    out: list[str] = []
    for v in values:
        if v.startswith("@"):
            with open(v[1:], encoding="utf-8") as fh:
                out.extend(
                    line.strip()
                    for line in fh
                    if line.strip() and not line.startswith("#")
                )
        else:
            out.append(v)
    return out


def _print_report(report: CorpusHoldout) -> None:
    def pct(x: Optional[float]) -> str:
        return "  n/a" if x is None else f"{100 * x:6.2f}%"

    print(f"\n=== temporal holdout (cutoff T = {report.cutoff}) ===")
    print("  framing: retrospective cutoff holdout over published anchors")
    print(
        f"  statutes={report.statute_count}  informative(both sides)={report.n_informative}"
        f"  errors={len(report.errors)}"
    )
    print(f"  mean TRAIN accuracy   (as_of <= T): {pct(report.mean_train_acc)}")
    print(f"  mean HOLDOUT accuracy (as_of  > T): {pct(report.mean_holdout_acc)}")
    gap = report.mean_gap
    print(
        f"  GENERALIZATION GAP (train − holdout): {pct(gap)}"
        + (
            "   (≈0 ⇒ generalizes; large + ⇒ overfit; − ⇒ better on holdout)"
            if gap is not None
            else ""
        )
    )
    fam = report.holdout_residual_family_counts
    if fam:
        print("  holdout-era divergence families:")
        for name, n in fam.items():
            print(f"    {name:<32} {n}")
    print("\n  per-statute:")
    for s in report.statutes:
        tag = "info" if s.is_informative else "one-sided"
        print(
            f"    {s.sid:<12} train={pct(s.train_acc)} "
            f"holdout={pct(s.holdout_acc)} gap={pct(s.gap)} "
            f"[{s.split.n_training}tr/{s.split.n_holdout}ho {tag}]"
        )
    for sid, err in report.errors:
        print(f"    {sid:<12} ERROR: {err}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.temporal_holdout",
        description="Frozen-catalog temporal-holdout generalization experiment "
        "(#182): split each statute's published anchors by a cutoff date T and "
        "compare mean replay accuracy on training-era vs holdout-era anchors.",
    )
    parser.add_argument(
        "--cutoff",
        required=True,
        help="cutoff date T (ISO YYYY-MM-DD); anchors after T are the holdout",
    )
    parser.add_argument(
        "--corpus",
        nargs="+",
        required=True,
        help="explicit statute ids (BOUNDED — the full sweep deadlocks), or "
        "@file with one sid per line",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        default=None,
        help="also write the full typed report to this JSON path",
    )
    args = parser.parse_args(argv)

    sids = _read_corpus_arg(list(args.corpus))
    report = run_corpus_holdout(sids, args.cutoff)
    _print_report(report)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(
                report.to_dict(), fh, sort_keys=True, ensure_ascii=False, indent=2
            )
            fh.write("\n")
        print(f"\n  wrote {args.json_out}")

    # Non-zero exit only on hard errors, never on a measured gap: a large gap is
    # a RESULT to report, not a gate failure (this is an experiment, not a CI
    # ratchet). An empty informative set is a soft failure worth flagging.
    if report.n_informative == 0:
        print("\n  WARNING: no informative statutes (no statute spans the cutoff)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
