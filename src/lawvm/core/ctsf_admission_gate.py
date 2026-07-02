"""CTSF control-pair admission gate — the load-bearing guardrail (task #184, §1.4).

Without this, CTSF is just a fancier ``_section_diff_is_bench_neutralized``: a
blacklist that grows one ad-hoc neutralizer per campaign, each a bug-masking hole.
The admission gate is the structural defense against quotient creep.

THE RULE (test-enforced by ``run_admission_gate`` and the shard tests):

    No editorial-quotient rule may enter CTSF unless it ships ALL of:
      (a) an unamended-unit control pair — source-side truth is known; the rule
          must make ``ctsf(source_as_enacted) == ctsf(oracle_unit)`` and must NOT
          change the source-side truth;
      (b) a quoted-payload control pair (where applicable) — the amendment's own
          quoted result text is source-level truth for a freshly-substituted unit;
      (c) a congruence-with-amendment-application test — projecting then applying
          equals applying then projecting, for the addressable part
          (``π(apply(a, x)) == apply(a, π(x))`` in CTSF);
      (d) a projection witness per elided fragment — every discarded fragment
          emits an auditable witness.

Each admitted rule is *also* a falsifiable P-rule: it carries a ``falsifier``
sentence and is registered in the #181 spec-ledger glue catalog
(``spec_ledger_glue._LENS``) with a pointer back to its control-pair fixtures
here.  The admission gate IS the ledger's admission test.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from lawvm.core.ctsf import (
    CTSFNode,
    collect_elisions,
    ctsf_equal,
    to_ctsf,
)
from lawvm.semantic.model import SemanticStructureNode


@dataclass(frozen=True, slots=True)
class ControlPair:
    """One control pair where truth is known independently of replay.

    ``left`` / ``right`` are logical-IR nodes whose CTSF projections the rule
    claims are equal (unamended-unit and quoted-payload pairs), or — for the
    congruence obligation — the pre/post-application nodes.
    """

    label: str
    left: SemanticStructureNode
    right: SemanticStructureNode
    must_be_equal: bool = True


@dataclass(frozen=True, slots=True)
class CongruenceCase:
    """A congruence check: applying an amendment then projecting == projecting
    then applying, for the addressable part.

    ``pre`` is the source node; ``apply_fn`` is the amendment's effect on the
    logical IR; the obligation is ``ctsf(apply_fn(pre)) == apply_ctsf(ctsf(pre))``
    where ``apply_ctsf`` is the same effect expressed on CTSF.  We test the
    weaker-but-sufficient form: both routes reach the same CTSF.
    """

    label: str
    pre: SemanticStructureNode
    apply_fn: Callable[[SemanticStructureNode], SemanticStructureNode]
    apply_ctsf: Callable[[CTSFNode], CTSFNode]


@dataclass(frozen=True, slots=True)
class WitnessCase:
    """A node whose CTSF projection MUST emit at least one elision witness for
    the fragment this rule discards (projection-witness obligation)."""

    label: str
    node: SemanticStructureNode


@dataclass(frozen=True, slots=True)
class CTSFEditorialRule:
    """A registered CTSF editorial rule with its four control-pair obligations.

    Migrated from the neutralizer blacklist.  ``ledger_glue_id`` points at its
    #181 spec-ledger glue entry; ``falsifier`` is the Popper-falsifier sentence
    (also stored in the ledger).
    """

    rule_id: str
    jurisdiction: str
    believed_spec: str
    falsifier: str
    ledger_glue_id: str
    unamended_control_pairs: tuple[ControlPair, ...] = ()
    quoted_payload_control_pairs: tuple[ControlPair, ...] = ()
    congruence_cases: tuple[CongruenceCase, ...] = ()
    witness_cases: tuple[WitnessCase, ...] = ()
    # Set True for a rule that legitimately has no quoted-payload analogue
    # (e.g. a heading-only or structure-only editorial rule).  Documented, not
    # a silent skip.
    quoted_payload_not_applicable: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    rule_id: str
    admitted: bool
    failures: tuple[str, ...] = field(default_factory=tuple)


def _check_pair(pair: ControlPair) -> str | None:
    left = to_ctsf(pair.left)
    right = to_ctsf(pair.right)
    equal = ctsf_equal(left, right)
    if equal != pair.must_be_equal:
        want = "equal" if pair.must_be_equal else "distinct"
        return f"control pair {pair.label!r}: CTSF projections are not {want}"
    return None


def _check_congruence(case: CongruenceCase) -> str | None:
    # project-then-apply
    route_a = case.apply_ctsf(to_ctsf(case.pre))
    # apply-then-project
    route_b = to_ctsf(case.apply_fn(case.pre))
    if not ctsf_equal(route_a, route_b):
        return (
            f"congruence case {case.label!r}: project-then-apply != "
            "apply-then-project in CTSF"
        )
    return None


def _check_witness(case: WitnessCase) -> str | None:
    node = to_ctsf(case.node)
    if not collect_elisions(node):
        return (
            f"witness case {case.label!r}: rule discarded a fragment but emitted "
            "no elision witness (silent drop)"
        )
    return None


def check_rule_admission(rule: CTSFEditorialRule) -> AdmissionResult:
    """Return the admission verdict for a single rule.

    A rule is ADMITTED iff it ships all four obligations AND each obligation's
    fixtures pass.  A rule missing any obligation is REJECTED with a typed
    failure — this is what makes CTSF a whitelist and not a garbage disposal.
    """
    failures: list[str] = []

    # (a) unamended-unit control pair — mandatory, at least one.
    if not rule.unamended_control_pairs:
        failures.append("missing obligation (a): no unamended-unit control pair")
    for pair in rule.unamended_control_pairs:
        err = _check_pair(pair)
        if err:
            failures.append(err)

    # (b) quoted-payload control pair — mandatory unless explicitly N/A.
    if not rule.quoted_payload_control_pairs and not rule.quoted_payload_not_applicable:
        failures.append(
            "missing obligation (b): no quoted-payload control pair "
            "(and quoted_payload_not_applicable is False)"
        )
    for pair in rule.quoted_payload_control_pairs:
        err = _check_pair(pair)
        if err:
            failures.append(err)

    # (c) congruence-with-amendment-application — mandatory, at least one.
    if not rule.congruence_cases:
        failures.append("missing obligation (c): no congruence case")
    for case in rule.congruence_cases:
        err = _check_congruence(case)
        if err:
            failures.append(err)

    # (d) projection witness per elided fragment — mandatory, at least one.
    if not rule.witness_cases:
        failures.append("missing obligation (d): no projection-witness case")
    for case in rule.witness_cases:
        err = _check_witness(case)
        if err:
            failures.append(err)

    # A rule must also carry its falsifier + ledger pointer (ledger unification).
    if not rule.falsifier.strip():
        failures.append("rule has no falsifier (not a valid P-rule)")
    if not rule.ledger_glue_id.strip():
        failures.append("rule has no ledger glue pointer (#181 unification broken)")

    return AdmissionResult(
        rule_id=rule.rule_id,
        admitted=not failures,
        failures=tuple(failures),
    )


def run_admission_gate(
    rules: "tuple[CTSFEditorialRule, ...] | None" = None,
) -> tuple[AdmissionResult, ...]:
    """Run the admission gate over ``rules`` (default: the registered set).

    The shard test asserts every registered rule is admitted; a rule that lacks
    any control pair is rejected here, so the whitelist can never silently accept
    an unvalidated neutralizer.
    """
    from lawvm.core.ctsf_rules import registered_ctsf_rules

    if rules is None:
        rules = registered_ctsf_rules()
    return tuple(check_rule_admission(rule) for rule in rules)
