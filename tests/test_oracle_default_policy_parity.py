"""Cross-jurisdiction parity for the ONE shared oracle-divergence default policy.

Audit fix #4. ``core.oracle_divergence`` used to advertise itself as "the universal
oracle-comparison kernel … every frontend", but it is imported by **UK alone**
(``tools.uk_oracle_check``); Finland, Estonia, EU, US and NZ each reimplement
oracle-divergence typing in their own vocabulary. The "shared algebra" is therefore
NOT a shared code path — so this test pins what IS meant to be shared: the *default
policy* for the one ambiguous case the audit names.

THE POLICY (kernel: ``core/oracle_divergence.py`` only-oracle default; EU alignment
#3: ``eu_oracle_divergence`` only-oracle → ``deterministic_gap``):

    An EID / article / provision **present in the oracle but absent from replay**
    ("only-oracle") DEFAULTS to the *deterministic-gap class* — a lawvm-side replay
    miss to investigate — and is promoted to the benign *manual-frontier class*
    (needs an owned claim / source ambiguous / out-of-scope, not a plain bug) ONLY
    behind an explicit evidence predicate.

Defaulting only-oracle straight to a benign bucket without evidence is FAIL-OPEN: it
launders a genuine replay miss into a "not our bug" cell. A frontend that does so is
*softer* than the kernel and must EITHER be aligned OR carry a documented, justified
exception here — never a silent drift.

THE PROJECTION (two vocabularies, one policy). Frontends type divergences in two
different vocabularies; this test projects each onto the same three-valued
only-oracle default class:

* ``DETERMINISTIC_GAP`` — the shared default. In the kernel's ``DivergenceKind`` this
  is ``DETERMINISTIC_GAP``; in the neutral spec-ledger ``WitnessDisposition`` this is
  a *falsifying* disposition, ``_FALSIFYING = ("lawvm_wrong", "structural")`` — the
  codebase's own definition of "counts against replay". The projection is grounded
  in that constant, not an arbitrary set.
* ``MANUAL_FRONTIER`` — the benign "needs an owned claim, not a plain bug" class. In
  the kernel this is ``MANUAL_FRONTIER``; in the neutral vocabulary this is
  ``missing_source`` (EU's own adapter maps ``manual_frontier → missing_source``, so
  a ``missing_source`` default IS an only-oracle → manual_frontier default).
* ``ORACLE_SUSPECT`` — "blame the oracle" (exculpate replay). The softest bucket;
  defaulting only-oracle here without evidence is the fail-open sin. NO frontend —
  not even a documented exception — may default only-oracle to this class.

Surveyed defaults (see the per-frontend probe drivers below):

* Finland  ``tools.oracle_check``    only-oracle ``MISSING``→structural /
            ``REPLAY_MISSING``→lawvm_wrong  → DETERMINISTIC_GAP (match)
* Estonia  ``estonia.spec_ledger_adapter`` ``OPS_MISSING``→lawvm_wrong
            → DETERMINISTIC_GAP (match)
* EU       ``eu.eu_oracle_divergence``  only-oracle → ``deterministic_gap``
            → DETERMINISTIC_GAP (match; evidence-gated to ``manual_frontier``)
* UK/kernel ``core.oracle_divergence``  only-oracle → ``DETERMINISTIC_GAP``
            → DETERMINISTIC_GAP (the reference)
* US       ``us_federal.dry_run``       only-oracle → ``missing_source``
            → MANUAL_FRONTIER  (DOCUMENTED JUSTIFIED EXCEPTION)
* NZ       ``new_zealand.dry_run_oracle`` only-oracle → topology/frontier family
            → MANUAL_FRONTIER  (DOCUMENTED JUSTIFIED EXCEPTION)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from lawvm.tools.spec_ledger import _FALSIFYING, WitnessDisposition, disposition_for


class OnlyOracleClass(Enum):
    """The three-valued only-oracle default class every frontend projects onto."""

    DETERMINISTIC_GAP = "deterministic_gap"  # lawvm-side replay miss (the shared default)
    MANUAL_FRONTIER = "manual_frontier"  # benign "needs an owned claim, not a plain bug"
    ORACLE_SUSPECT = "oracle_suspect"  # "blame the oracle" — the fail-open exculpation


#: The shared default the policy mandates for only-oracle, absent evidence.
SHARED_DEFAULT = OnlyOracleClass.DETERMINISTIC_GAP


def _class_of_disposition(disposition: WitnessDisposition) -> OnlyOracleClass:
    """Project a neutral spec-ledger ``WitnessDisposition`` onto the only-oracle class.

    Grounded in the codebase's own ``_FALSIFYING`` constant: a *falsifying*
    disposition (``lawvm_wrong`` / ``structural``) is the deterministic-gap class —
    it counts against replay, exactly the kernel's ``deterministic_gap``. The benign
    ``missing_source`` is the manual-frontier class (EU's adapter maps
    ``manual_frontier → missing_source``). ``oracle_suspect`` is its own peer. An
    unmapped disposition (e.g. ``unknown``) is a fail-loud programming error, never a
    silent bucket.
    """
    if disposition in _FALSIFYING:  # ("lawvm_wrong", "structural")
        return OnlyOracleClass.DETERMINISTIC_GAP
    if disposition == "missing_source":
        return OnlyOracleClass.MANUAL_FRONTIER
    if disposition == "oracle_suspect":
        return OnlyOracleClass.ORACLE_SUSPECT
    raise AssertionError(
        f"disposition {disposition!r} has no only-oracle-class projection; a new "
        "WitnessDisposition must be classified deliberately (falsifying → "
        "DETERMINISTIC_GAP, benign-frontier → MANUAL_FRONTIER, blame-oracle → "
        "ORACLE_SUSPECT), never left to default silently"
    )


@dataclass(frozen=True)
class FrontendProbe:
    """One frontend's observed only-oracle default(s), driven on synthetic input.

    ``classes`` is the SET of only-oracle default classes the frontend's real
    classification surface produces WITHOUT any evidence predicate — a singleton for
    every frontend today, but a set so that a frontend with several only-oracle
    diagnoses (Finland) is caught if ANY one softens.
    """

    jurisdiction: str
    classes: frozenset[OnlyOracleClass]
    driver_note: str


# ---------------------------------------------------------------------------
# Per-frontend probe drivers — each drives the frontend's REAL classification
# surface on a synthetic only-oracle input (no live replay / fixtures needed).
# ---------------------------------------------------------------------------


def _probe_kernel() -> FrontendProbe:
    """UK/kernel: ``classify_divergences`` with an only-oracle EID and no evidence."""
    from lawvm.core.oracle_divergence import (
        DivergenceClassifierInputs,
        DivergenceKind,
        classify_divergences,
    )

    report = classify_divergences(
        only_oracle={"section-1"},
        only_replay=set(),
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
    )
    kind_class = {
        DivergenceKind.DETERMINISTIC_GAP: OnlyOracleClass.DETERMINISTIC_GAP,
        DivergenceKind.MANUAL_FRONTIER: OnlyOracleClass.MANUAL_FRONTIER,
        DivergenceKind.ORACLE_SUSPECT: OnlyOracleClass.ORACLE_SUSPECT,
    }
    landed = {
        kind_class[k] for k, eids in report.buckets.items() if eids and k in kind_class
    }
    return FrontendProbe(
        "uk_kernel",
        frozenset(landed),
        "core.oracle_divergence.classify_divergences(only_oracle={'section-1'})",
    )


def _probe_finland() -> FrontendProbe:
    """Finland: ``tools.oracle_check`` only-oracle diagnoses via the FI disposition map.

    ``tools.oracle_check`` assigns ``MISSING`` (replay node absent) or
    ``REPLAY_MISSING`` (section keyed but replay text empty) for only-oracle; both
    route through this map in ``fi_ledger_inputs``.
    """
    from lawvm.finland.spec_ledger_adapter import _FI_DIAGNOSIS_DISPOSITION

    classes = {
        _class_of_disposition(disposition_for(diag, _FI_DIAGNOSIS_DISPOSITION))
        for diag in ("MISSING", "REPLAY_MISSING")
    }
    return FrontendProbe(
        "finland",
        frozenset(classes),
        "disposition_for({'MISSING','REPLAY_MISSING'}, _FI_DIAGNOSIS_DISPOSITION)",
    )


def _probe_estonia() -> FrontendProbe:
    """Estonia: ``OPS_MISSING`` (in oracle, not replay) via the EE disposition map."""
    from lawvm.estonia.spec_ledger_adapter import _EE_DIAGNOSIS_DISPOSITION

    cls = _class_of_disposition(
        disposition_for("OPS_MISSING", _EE_DIAGNOSIS_DISPOSITION)
    )
    return FrontendProbe(
        "estonia",
        frozenset({cls}),
        "disposition_for('OPS_MISSING', _EE_DIAGNOSIS_DISPOSITION)",
    )


def _probe_eu() -> FrontendProbe:
    """EU: native ``CorpusDivergenceAccount`` with one only-oracle article, no evidence.

    Also asserts the evidence-gated promotion is honored (an only-oracle article WITH
    a ``manual_frontier_labels`` witness lands in ``manual_frontier``) so the default
    is provably a *default*, not a constant.
    """
    from lawvm.eu.eu_oracle_divergence import (
        ArticleDivergence,
        CorpusDivergenceAccount,
        OracleComparison,
    )

    comp = OracleComparison(as_of="2020-01-01", base_celex="32000L0001")
    comp.divergences.append(
        ArticleDivergence(
            article_label="5",
            kind="present_in_oracle_absent_in_replay",
            oracle_text="x",
        )
    )
    default_acc = CorpusDivergenceAccount()
    default_acc.add(comp)
    corpus_class = {
        "deterministic_gap": OnlyOracleClass.DETERMINISTIC_GAP,
        "manual_frontier": OnlyOracleClass.MANUAL_FRONTIER,
        "oracle_suspect": OnlyOracleClass.ORACLE_SUSPECT,
    }
    landed = {
        corpus_class[c]
        for c, n in default_acc.class_counts.items()
        if n and c in corpus_class
    }
    return FrontendProbe(
        "eu",
        frozenset(landed),
        "CorpusDivergenceAccount.add(present_in_oracle_absent_in_replay)",
    )


def _probe_us() -> FrontendProbe:
    """US federal: only-oracle (oracle changed a section no rule claimed) → missing_source."""
    from lawvm.us_federal.dry_run import DISPOSITION_MISSING_SOURCE
    from lawvm.us_federal.spec_ledger_adapter import _US_DISPOSITION

    cls = _class_of_disposition(
        disposition_for(DISPOSITION_MISSING_SOURCE, _US_DISPOSITION)
    )
    return FrontendProbe(
        "us_federal",
        frozenset({cls}),
        "disposition_for(DISPOSITION_MISSING_SOURCE, _US_DISPOSITION)",
    )


#: NZ types only-oracle in an ``AgreementResidualFamily`` taxonomy, not a
#: ``WitnessDisposition``. Its only-oracle families are benign frontier families
#: (a topology/granularity difference between two trees, or an
#: accepted-non-executable-frontier node in a repeal-only dry-run) — the
#: manual-frontier class, NOT a falsifying deterministic-gap default.
_NZ_ONLY_ORACLE_BENIGN_FAMILIES = frozenset(
    {"topology_granularity_mismatch", "accepted_non_executable_frontier"}
)


def _probe_new_zealand() -> FrontendProbe:
    """NZ: ``classify_comparator_status_family('oracle_only')`` → benign frontier family."""
    from lawvm.new_zealand.dry_run_oracle import classify_comparator_status_family

    family = classify_comparator_status_family("oracle_only")
    assert family in _NZ_ONLY_ORACLE_BENIGN_FAMILIES, (
        f"NZ only-oracle family {family!r} is not one of the known benign frontier "
        "families; re-survey the NZ oracle surface before updating this probe"
    )
    return FrontendProbe(
        "new_zealand",
        frozenset({OnlyOracleClass.MANUAL_FRONTIER}),
        "classify_comparator_status_family('oracle_only')",
    )


#: Every frontend probed here. Adding a new jurisdiction frontend that types
#: oracle divergences MUST add a probe (and land in compliant OR exceptions),
#: or this registry's coverage assertion fails — the anti-silent-drift ratchet.
_PROBES = (
    _probe_kernel,
    _probe_finland,
    _probe_estonia,
    _probe_eu,
    _probe_us,
    _probe_new_zealand,
)


# ---------------------------------------------------------------------------
# Documented, justified exceptions (per-frontend allowlist WITH a reason).
# Each entry PINS the known softer default so (a) a FURTHER softening (e.g. a
# slide to ORACLE_SUSPECT) is still caught, and (b) a future alignment to the
# shared default forces a conscious removal of the entry. Honesty over uniformity
# (audit fix #4): a divergence that would move real corpus verdicts to align is
# recorded here, never silently tolerated.
# ---------------------------------------------------------------------------
JUSTIFIED_EXCEPTIONS: dict[str, tuple[OnlyOracleClass, str]] = {
    "us_federal": (
        OnlyOracleClass.MANUAL_FRONTIER,
        "US only-oracle means the oracle changed a section NO lowering rule claimed "
        "(the amending Public Law was never lowered) — a source-footing / coverage "
        "gap honestly typed `missing_source` and kept VISIBLE (never exculpated to "
        "`oracle_suspect`). Aligning to the falsifying `lawvm_wrong`/deterministic-gap "
        "class would fabricate a rule-falsification where no rule fired, and would "
        "move US bench Beta-Bernoulli rule scores (non-falsifying → falsifying). "
        "Aligning is not low-risk; deferred as a follow-on.",
    ),
    "new_zealand": (
        OnlyOracleClass.MANUAL_FRONTIER,
        "NZ only-oracle lands in a benign frontier family: the standalone "
        "candidate-vs-oracle comparator types it as a topology/granularity mismatch "
        "between two SOURCE trees (not a replay miss), and the whole-tree dry-run "
        "applies ONLY repeal ops, so a non-repeal oracle-only node is expected-by-"
        "construction (`accepted_non_executable_frontier`). Neither is a fail-open "
        "laundering of a replay miss, but the benignness is assumed-by-construction "
        "rather than evidence-gated. Aligning would move the NZ temporal_mismatch "
        "frontier corpus verdicts; deferred as a follow-on (candidate: add an "
        "explicit evidence gate to the whole-tree `else` branch).",
    ),
}


def _probe_for(jurisdiction: str) -> FrontendProbe:
    for probe in _PROBES:
        result = probe()
        if result.jurisdiction == jurisdiction:
            return result
    raise AssertionError(f"no probe for {jurisdiction!r}")


def _all_probes() -> list[FrontendProbe]:
    return [probe() for probe in _PROBES]


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_disposition_projection_grounded_in_falsifying_constant() -> None:
    # The deterministic-gap class is EXACTLY the codebase's own _FALSIFYING set.
    assert _FALSIFYING == ("lawvm_wrong", "structural")
    for d in _FALSIFYING:
        assert _class_of_disposition(d) is OnlyOracleClass.DETERMINISTIC_GAP
    assert _class_of_disposition("missing_source") is OnlyOracleClass.MANUAL_FRONTIER
    assert _class_of_disposition("oracle_suspect") is OnlyOracleClass.ORACLE_SUSPECT
    with pytest.raises(AssertionError, match="no only-oracle-class projection"):
        _class_of_disposition("unknown")


def test_compliant_frontends_default_only_oracle_to_deterministic_gap() -> None:
    # The kernel, EU and the two disposition-mapped frontends (FI, EE) that are NOT
    # documented exceptions must default only-oracle to the SHARED deterministic-gap
    # class — nothing softer.
    for probe in _all_probes():
        if probe.jurisdiction in JUSTIFIED_EXCEPTIONS:
            continue
        assert probe.classes == frozenset({SHARED_DEFAULT}), (
            f"frontend {probe.jurisdiction!r} defaults only-oracle to "
            f"{sorted(c.value for c in probe.classes)!r}, not the SHARED default "
            f"{SHARED_DEFAULT.value!r}. Only-oracle (present in oracle, absent from "
            "replay) must default to the deterministic-gap class (a lawvm-side "
            "replay miss to investigate); a benign default is FAIL-OPEN. Align it to "
            "the kernel policy (core/oracle_divergence.py) or add a documented, "
            f"justified exception for {probe.jurisdiction!r} in JUSTIFIED_EXCEPTIONS "
            f"(driven surface: {probe.driver_note})."
        )


def test_no_frontend_defaults_only_oracle_to_oracle_suspect() -> None:
    # The universal fail-open guard: NO frontend — not even a documented exception —
    # may default only-oracle straight to ORACLE_SUSPECT (blame the oracle, exculpate
    # replay) without evidence. That is the softest, most dangerous laundering.
    for probe in _all_probes():
        assert OnlyOracleClass.ORACLE_SUSPECT not in probe.classes, (
            f"frontend {probe.jurisdiction!r} defaults an only-oracle divergence to "
            "ORACLE_SUSPECT (blames the oracle) with no evidence — the fail-open "
            "exculpation the policy forbids for EVERY frontend, exceptions included "
            f"(driven surface: {probe.driver_note})."
        )


def test_documented_exceptions_pin_their_known_softer_default() -> None:
    # Each allowlisted exception must (a) actually diverge from the shared default —
    # a stale entry that now matches must be removed — and (b) still match its PINNED
    # class, so a further drift is caught; and (c) carry a substantive reason.
    for jurisdiction, (expected_class, reason) in JUSTIFIED_EXCEPTIONS.items():
        probe = _probe_for(jurisdiction)
        assert expected_class is not SHARED_DEFAULT, (
            f"exception {jurisdiction!r} pins the SHARED default — a real exception "
            "must diverge; if it now matches, delete the JUSTIFIED_EXCEPTIONS entry."
        )
        assert probe.classes == frozenset({expected_class}), (
            f"documented exception {jurisdiction!r} now defaults only-oracle to "
            f"{sorted(c.value for c in probe.classes)!r}, not its pinned "
            f"{expected_class.value!r}. If it was ALIGNED to the shared default, "
            "remove its JUSTIFIED_EXCEPTIONS entry; if it drifted further, that is a "
            "regression to investigate."
        )
        assert len(reason.strip()) >= 80, (
            f"exception {jurisdiction!r} needs a substantive justification (why it "
            "cannot cheaply align), not a rubber stamp."
        )


def test_every_probed_frontend_is_compliant_or_a_documented_exception() -> None:
    # Total coverage: no probed frontend may be neither compliant nor allowlisted,
    # and the two sets are disjoint. A new frontend without a probe simply is not
    # covered — the module docstring / _PROBES registry is the place to add it.
    probed = {p.jurisdiction for p in _all_probes()}
    expected = {"uk_kernel", "finland", "estonia", "eu", "us_federal", "new_zealand"}
    assert probed == expected, (
        "the set of probed frontends changed; keep _PROBES and this expectation in "
        "sync (adding a frontend requires a probe so it cannot silently escape parity)"
    )
    assert set(JUSTIFIED_EXCEPTIONS) <= probed
    for probe in _all_probes():
        compliant = probe.classes == frozenset({SHARED_DEFAULT})
        excepted = probe.jurisdiction in JUSTIFIED_EXCEPTIONS
        assert compliant != excepted, (
            f"{probe.jurisdiction!r} must be EITHER compliant with the shared default "
            "OR a documented exception, never both and never neither."
        )
