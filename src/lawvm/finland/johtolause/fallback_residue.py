"""Closed, measured fallback-residue-class registry for the FI johtolause parser.

The grammar parser (:mod:`lawvm.finland.johtolause.grammar.parser`) is the
production primary for amendment johtolauses. On a known, bounded minority of
clauses it declines loudly by raising :class:`~...grammar.parser.OutOfScope`
with a *generalized reason*, and the caller falls back to the legacy
``surface_parse``. That fallback set is real residue: a typed terminal state the
project tracks under "total accounting, not total ownership" (Pro P0 #2).

This module turns the open-ended decline set into a NAMED, COUNTED, CLOSED
classification:

  * :data:`FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0` — the closed set of residue
    classes. Every generalized ``OutOfScope`` reason the parser can surface to
    the fallback boundary MUST map to exactly one class.
  * :func:`classify_decline_reason` — pure mapping reason -> ``class_id`` (or
    ``None`` = UNREGISTERED, which the CI test treats as a hard failure).
  * :data:`FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE` — pinned per-class counts
    over the full canonical corpus, so any silent growth in the fallback set is
    detectable in CI.

The classification is **conservative and total**: a reason that does not clearly
belong to a class maps to ``None`` and fails the closed-set guarantee loudly. We
never silently bucket an unknown reason into a catch-all.

``future_path`` records the disposition decided for each class:

  * ``own``         — the grammar will eventually parse this natively (a recovery
                      lane is open or planned); fallback here is temporary.
  * ``adjudicate``  — genuinely ambiguous source shapes where declining (and
                      letting the legacy parser settle it) is a defensible choice
                      pending a human/oracle ruling.
  * ``keep_legacy`` — the grammar deliberately defers to the legacy parser for
                      this shape and is not expected to own it.

This module is a pure addition: it imports nothing from the grammar and changes
no parsing behavior. It is the accounting ledger over the fallback boundary.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

FuturePath = Literal["own", "adjudicate", "keep_legacy"]


@dataclass(frozen=True)
class FallbackResidueClass:
    """One named residue class over the parser's fallback boundary."""

    class_id: str
    #: Generalized ``OutOfScope`` reasons (post ``_generalize_decline``) this
    #: class covers. Each reason belongs to exactly one class across the set.
    reasons: frozenset[str]
    summary: str
    future_path: FuturePath
    #: What the (future) typed-fallback terminal state should record when a
    #: clause lands in this class. Human-readable disposition note.
    strict_disposition: str
    #: Pinned full-corpus baseline count for this class. ``-1`` means "latent" —
    #: a guard reason that exists in the grammar but does not currently surface
    #: any clause to the fallback boundary (count 0, registered for closure).
    baseline_count: int = field(default=0)


# ---------------------------------------------------------------------------
# THE CLOSED SET. Every generalized OutOfScope reason maps into exactly one of
# these. Adding/removing a reason or changing a baseline is a deliberate, human
# edit — the CI test fails on any drift.
# ---------------------------------------------------------------------------
FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0: tuple[FallbackResidueClass, ...] = (
    # --- complex enumerations the recognizers cannot yet continue through ----
    FallbackResidueClass(
        class_id="complex_enumeration_with_interleaved_provenance",
        reasons=frozenset(
            {
                "undecodable insertion continuation",
            }
        ),
        summary=(
            "Multi-target enumeration whose insertion continuation interleaves "
            "provenance/anchor material the continuation recognizer cannot yet "
            "decode; declined so no enumerated target is silently dropped."
        ),
        future_path="own",
        strict_disposition=(
            "record as continuation-undecodable; recovery lane (#32/#38) targets "
            "native ownership"
        ),
        # 355 -> 354: one interleaved-provenance clause became fully grammar-owned
        # via the bare-``uusi`` whole-target recovery (the Pattern-D
        # citation-stripped bare-section insert) — its leading bare-section arm,
        # which previously made the recovery decline, is now consumed and the rest
        # of the clause parses cleanly. Net -1 (no other interleaved clause shifted:
        # the recovery's structural-OOS self-guard keeps heading/appendix folds
        # declining exactly as before).
        #
        # 354 -> 348: the appendix-drop tail recovery
        # (``_insertion_tail_is_appendix_drop``) natively owns 6 clauses whose
        # ONLY decline blocker was a trailing whole-statute ``… sekä/ja [uusi]
        # liite[N]`` appendix arm. The old ``surface_parse`` has no appendix-insert
        # family, so it emits ZERO operative nodes for that tail and the outer loop
        # swallows it; the grammar now drops the same tail and owns the clause,
        # proven byte-identical to legacy on all 6 (full-model equal, not just
        # structural). The recovery is TIGHT: the tail must carry a ``LIITE`` and no
        # further structural target noun (§/momentti/kohta/luku/otsikko) or later
        # verb, so no clause with a real kept target is silently stripped. Net -6.
        #
        # 348 -> 347: the no-``uusi`` trailing anaphoric heading-residue recovery
        # owns 1995/407 (``… nojalla [provenance], asetukseen uusi 25 a § ja sen
        # edellä väliotsikko``) — its ONLY remaining blocker was the bare
        # ``sen edellä väliotsikko`` tail (no ``uusi`` before the heading noun),
        # the no-``uusi`` sibling of the already-owned ``EDELLA uusi`` residue. The
        # old parser drops the väliotsikko in both forms and emits only the §25a
        # SECTION insert; the grammar now reproduces that full-model byte-identically
        # (consolidated esitutkinta-asetus 575/1988 carries §25a, eId sec_25a). The
        # recovery is gated to a STRICTLY-TERMINAL residue (clause end), so a
        # mid-clause no-``uusi`` heading residue inside a complex multi-verb
        # enumeration (1996/581) still declines. Net -1.
        baseline_count=347,
    ),
    FallbackResidueClass(
        class_id="complex_enumeration_with_subtarget_continuation",
        reasons=frozenset(
            {
                "undecodable insertion tail (no separator)",
                "undecodable heading-change continuation",
            }
        ),
        summary=(
            "Enumeration whose insertion/heading-change tail lacks a decodable "
            "separator boundary, so the sub-target continuation cannot be split "
            "deterministically; declined rather than mis-segment."
        ),
        future_path="own",
        strict_disposition=(
            "record as tail-undecodable; recovery lane targets native ownership"
        ),
        baseline_count=72,
    ),
    FallbackResidueClass(
        class_id="complex_multi_verb_enumeration_insert_tail",
        reasons=frozenset(
            {
                "mixed insertion/non-insertion continuation in verb group",
                "infinitive amendment-verb residue keeps insert list",
            }
        ),
        summary=(
            "Verb group that mixes insertion and non-insertion continuations, or "
            "carries an infinitive amendment-verb residue (e.g. '... sekä lisätä "
            "...') with a kept insert list the legacy parser keeps; declined to "
            "avoid dropping the trailing inserts."
        ),
        future_path="own",
        strict_disposition=(
            "record as mixed/infinitive insert-tail; recovery lane targets native "
            "ownership"
        ),
        baseline_count=21,
    ),
    # --- insertion-shape declines -------------------------------------------
    FallbackResidueClass(
        class_id="dropped_tail_keeps_old_nodes",
        reasons=frozenset(
            {
                "dropped section/container tail keeps old nodes",
            }
        ),
        summary=(
            "Section/container tail the grammar would drop but the legacy parser "
            "retains as nodes; declined so the retained nodes are not lost."
        ),
        future_path="own",
        strict_disposition=(
            "record as dropped-tail; recovery requires the continuation recognizer "
            "to consume the tail natively"
        ),
        # 49 -> 50: the both-parser drop-recovery round shifted one clause's
        # decline reason into this class (total registered declines still fell
        # 907 -> 904; benign redistribution).
        baseline_count=50,
    ),
    FallbackResidueClass(
        class_id="single_verb_bare_number_insert",
        reasons=frozenset(
            {
                "out-of-scope insertion shape (uusi anchor present)",
            }
        ),
        summary=(
            "Insertion shape carrying a 'uusi' anchor the insertion recognizer "
            "declines (out-of-scope insertion shape); declined rather than "
            "mis-reading the anchor section as a section reference. The "
            "citation-stripped bare-section subset (old Pattern D: ``uusi <numlist> "
            "[§]``, chained ``ja uusi``, or END-terminated with no structural noun) "
            "is now natively owned by the grammar's bare-``uusi`` whole-target "
            "recognizer; what remains carries a genuinely out-of-scope downstream "
            "feature (heading/appendix/nimike placement, or a tail the old parser "
            "itself silently drops) the grammar must not reproduce."
        ),
        future_path="own",
        strict_disposition=(
            "record as out-of-scope insertion shape; insertion recognizer scope "
            "expansion targets native ownership"
        ),
        # 270 -> 264: the bare-``uusi`` whole-target recovery (the Pattern-D
        # citation-stripped bare-section insert: ``uusi <numlist> [§]``, chained
        # ``ja uusi``, or END-terminated with no structural noun) natively owns the
        # 6 clean clauses from this class, proven byte-identical to legacy. The arm
        # carries a structural-OOS self-guard (it declines when a downstream
        # heading/appendix/backref fold sits in the same batch span), so no clause
        # with such a fold is silently stripped — those stay declined. Net -6 here;
        # no recipient class grows (the single interleaved-provenance clause the arm
        # also freed became fully owned, -1 there too).
        #
        # 264 -> 263: the no-``uusi`` trailing anaphoric heading-residue recovery
        # owns 1995/1387 (``lakiin uusi 5 a § ja sen edelle väliotsikko``) — the
        # bare ``sen edelle väliotsikko`` tail (no ``uusi`` before the heading noun)
        # was its only blocker. The old parser drops the väliotsikko and emits only
        # the §5a SECTION insert; the grammar now reproduces that full-model
        # byte-identically (consolidated arvo-osuustililaki 827/1991 carries §5a,
        # eId sec_5a, behind a cross-heading). Gated to a strictly-terminal residue,
        # so complex mid-clause heading residues still decline. Net -1.
        baseline_count=263,
    ),
    # --- not-a-target / target-position declines -----------------------------
    FallbackResidueClass(
        class_id="not_a_target_at_target_position",
        reasons=frozenset(
            {
                "not a target at target position",
            }
        ),
        summary=(
            "Token at a target position is not a recognizable amendment target "
            "(e.g. a soveltamissaannos/applicability-clause phrasing); declined "
            "rather than coercing a non-target into a target node."
        ),
        future_path="adjudicate",
        strict_disposition=(
            "record as non-target-at-target-position; needs source-shape ruling on "
            "whether grammar should own these phrasings"
        ),
        baseline_count=119,
    ),
    # --- provenance leaks ----------------------------------------------------
    FallbackResidueClass(
        class_id="provenance_leak",
        reasons=frozenset(
            {
                "section näistä/niistä provenance leak",
                "container näistä/niistä provenance continuation",
            }
        ),
        summary=(
            "A 'näistä'/'niistä' provenance continuation (sellaisina kuin ... ovat) "
            "leaks into the section/container target region; declined so the "
            "provenance clause is not mis-attributed as a target."
        ),
        future_path="own",
        strict_disposition=(
            "record as provenance-leak; provenance-continuation recognizer targets "
            "native ownership"
        ),
        # 83 (was 82): a leading ``N §:n nojalla`` authority basis whose real
        # target list carries a ``niistä N § sellaisina kuin`` provenance leak now
        # reaches this class — the authority skip recovers the bare-name target
        # list, surfacing the provenance leak that the whole-clause authority
        # decline previously masked.
        baseline_count=83,
    ),
    # --- authority-basis misreads --------------------------------------------
    FallbackResidueClass(
        class_id="authority_basis_misread",
        reasons=frozenset(
            {
                "authority-basis nojalla citation mis-read as target",
                "out-of-scope authority insertion (bare-number insert after nojalla lead-in)",  # noqa: E501
            }
        ),
        summary=(
            "An authority-basis citation ('... nojalla ...') sits where a target "
            "would, or a bare-number insert follows a 'nojalla' lead-in; declined "
            "so the authority list is not mis-read as a target/insertion."
        ),
        future_path="own",
        strict_disposition=(
            "record as authority-basis misread; recognizer must distinguish "
            "authority basis from target list"
        ),
        baseline_count=7,
    ),
    # --- cross-verb-group discourse ------------------------------------------
    FallbackResidueClass(
        class_id="cross_verb_anaphora",
        reasons=frozenset(
            {
                "relabel from context (cross-verb-group resolution)",
                "cross-verb move retarget (cross-verb-group resolution)",
            }
        ),
        summary=(
            "Resolution that requires reading context across verb groups (relabel "
            "or move-retarget from a prior verb group); deliberately deferred to "
            "legacy. The cross-verb frontier is spent: the recoverable slice (the "
            "'lisätään sanottuun pykälään uusi N momentti' anaphora, +23) has been "
            "harvested into the grammar, and the 97.8% of declines that look like "
            "later-group failures reproduce identically in isolation — they are "
            "intra-group complex-enumeration residue, not cross-verb. The 2 "
            "residual clauses are distinct one-off discourse shapes (an "
            "exception-clause relabel that renumbers an excepted section, and a "
            "move that retargets an already-amended section into a new chapter) "
            "with no shared recognizer pattern; owning them is negative-EV."
        ),
        future_path="keep_legacy",
        strict_disposition=(
            "record as cross-verb anaphora; cross-verb frontier spent, recoverable "
            "anaphora slice already harvested, residual 2 are one-off discourse "
            "shapes deliberately deferred to legacy (not expected to own)"
        ),
        baseline_count=2,
    ),
    # --- meta-only -----------------------------------------------------------
    FallbackResidueClass(
        class_id="meta_only_no_verb",
        reasons=frozenset(
            {
                "no amendment verb (meta-only clause)",
                "empty first verb group",
            }
        ),
        summary=(
            "Clause the OLD parser treats as an amendment johtolause but which "
            "carries no decodable amendment verb group for the grammar (meta-only "
            "or empty first group); declined as out of amendment scope."
        ),
        future_path="adjudicate",
        strict_disposition=(
            "record as meta-only; likely a denominator/old-parser artifact, needs "
            "ruling on whether it is an amendment at all"
        ),
        baseline_count=2,
    ),
    # --- latent verb-group guards (registered for closure; count 0) ----------
    FallbackResidueClass(
        class_id="verb_group_structure_guard",
        reasons=frozenset(
            {
                "expected verb at verb-group start",
                "unconsumed tail at token N (CAT)",
            }
        ),
        summary=(
            "Structural verb-group guards (a verb-group start without a verb, or "
            "an unconsumed token tail at end-of-parse). Latent: these guards exist "
            "in the grammar but currently surface no clause to the fallback "
            "boundary on the canonical corpus (baseline 0)."
        ),
        future_path="own",
        strict_disposition=(
            "record as structural guard; latent today, registered so a future "
            "occurrence is classified rather than unregistered"
        ),
        baseline_count=0,
    ),
)


# Pinned per-class baseline counts (full canonical corpus). A human bumps these
# deliberately when the fallback set legitimately changes; the CI test fails on
# any un-bumped increase.
FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE: dict[str, int] = {
    rc.class_id: rc.baseline_count
    for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0
}

# Pinned total declined-clause count over the full canonical corpus. Equals the
# sum of per-class baselines. The CI test fails if the live declined count
# exceeds this without a deliberate bump.
FI_JOHTOLAUSE_FALLBACK_RESIDUE_TOTAL_BASELINE: int = sum(
    FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE.values()
)


# Reason -> class_id index, built once. A reason mapped by two classes is a
# registry bug (the closed set must partition the reason space); we assert
# disjointness at import time so it can never silently happen.
def _build_reason_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0:
        for reason in rc.reasons:
            if reason in index:
                raise ValueError(
                    f"fallback-residue registry: reason {reason!r} claimed by both "
                    f"{index[reason]!r} and {rc.class_id!r} (classes must partition)"
                )
            index[reason] = rc.class_id
    return index


_REASON_INDEX: dict[str, str] = _build_reason_index()


def classify_decline_reason(reason: str) -> str | None:
    """Map a generalized ``OutOfScope`` reason to a residue ``class_id``.

    Returns ``None`` when the reason is not registered. Callers (the CI test)
    treat ``None`` as a hard failure: the closed set must cover every reason the
    parser can surface to the fallback boundary. This function never guesses or
    buckets an unknown reason into a catch-all.
    """
    return _REASON_INDEX.get(reason)


def registered_reasons() -> frozenset[str]:
    """All generalized reasons the registry covers (closed-set membership)."""
    return frozenset(_REASON_INDEX)


def registered_class_ids() -> tuple[str, ...]:
    """The ``class_id`` of every residue class, in registry order."""
    return tuple(rc.class_id for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0)


# ---------------------------------------------------------------------------
# Corpus audit. Reusable from both the standalone audit script and the CI test
# so the closed-set + count guarantees are measured against the live corpus.
# ---------------------------------------------------------------------------
def generalize_decline_reason(msg: str) -> str:
    """Collapse a raw ``OutOfScope`` message to its generalized shape.

    Byte-for-byte identical to ``validate_census.py::_generalize_decline`` so the
    audit, the census, and this registry agree on what a "reason" is.
    """
    msg = re.sub(r"token \d+", "token N", msg)
    msg = re.sub(r"\([A-Z_]+\)", "(CAT)", msg)
    msg = re.sub(r":.*$", "", msg).strip()
    return msg[:80]


@dataclass(frozen=True)
class ResidueAuditResult:
    """Outcome of a full-corpus fallback-residue audit."""

    total_amendment_clauses: int
    total_declined: int
    #: generalized reason -> count
    reason_counts: dict[str, int]
    #: generalized reason -> a sample statute id
    reason_samples: dict[str, str]
    #: class_id -> count (registered reasons only)
    class_counts: dict[str, int]
    #: generalized reasons that map to no registered class (closure failures)
    unregistered_reasons: list[str]


def audit_corpus(limit: int = 0) -> ResidueAuditResult:
    """Run the new parser over the canonical corpus and classify every decline.

    Requires the canonical Finlex corpus (``LAWVM_CANONICAL_DATA_ROOT``). Imports
    the corpus + parser lazily so importing this registry module stays cheap and
    dependency-free.
    """
    from farchive import Farchive

    from lawvm.finland.johtolause import surface_parse
    from lawvm.finland.johtolause.grammar import parser as new_parser
    from lawvm.finland.johtolause.grammar.diff import parse_text_with
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]

    reason_counts: Counter[str] = Counter()
    reason_samples: dict[str, str] = {}
    total = 0
    declined = 0

    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            johto = get_johtolause(xb) or ""
        except Exception:
            continue
        if not johto:
            continue
        try:
            old_model = parse_text_with(johto, surface_parse.parse)
        except Exception:
            continue
        if not old_model.verb_groups:
            continue
        total += 1
        try:
            parse_text_with(johto, new_parser.parse)
        except new_parser.OutOfScope as exc:
            declined += 1
            reason = generalize_decline_reason(str(exc))
            reason_counts[reason] += 1
            reason_samples.setdefault(reason, sid)
        except Exception:  # noqa: BLE001
            # genuine crash delta — not a clean decline; out of residue scope.
            continue

    class_counts: Counter[str] = Counter()
    unregistered: list[str] = []
    for reason, n in reason_counts.items():
        cid = classify_decline_reason(reason)
        if cid is None:
            unregistered.append(reason)
        else:
            class_counts[cid] += n

    return ResidueAuditResult(
        total_amendment_clauses=total,
        total_declined=declined,
        reason_counts=dict(reason_counts),
        reason_samples=dict(reason_samples),
        class_counts=dict(class_counts),
        unregistered_reasons=sorted(unregistered),
    )
