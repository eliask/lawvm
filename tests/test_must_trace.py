"""The MUST-trace DRIFT DETECTOR (Pro invariant-mining doc §13 step 5).

This is the ratchet companion to :mod:`lawvm.core.must_trace`. It enforces the
discipline: *every normative ``MUST`` in the DECLARED in-scope spec files maps to
one of — an invariant id, a checker step, a writer refusal, a declared
non-guarantee, or an honest ``deferred_with_owner`` gap.* A MUST that maps to
NOTHING is an unenforced normative claim (spec poetry); this gate surfaces it.

WHAT IS ASSERTED (and the honesty boundary):

1. **No unmapped MUST (the ratchet).** Re-scan each in-scope file for normative
   MUST occurrences (code fences stripped, explicit waivers removed) and assert
   the ledger holds exactly that many clauses, all attributed to that file. A NEW
   MUST added to an in-scope file with no ledger row FAILS this test.
2. **Every excerpt is real.** Each ledger clause's ``excerpt`` MUST text is a
   genuine substring of its ``spec_source`` file — the ledger cannot cite a
   sentence that is not in the spec.
3. **Every target resolves.** An ``invariant_id`` exists in ``V0_INVARIANTS``; a
   ``checker_step`` / ``writer_refusal`` dotted ref is importable; a
   ``declared_non_guarantee`` handle exists in the FI AssumptionRegister (matched
   by ``witness_rule_id``, a ``finding_refs`` entry, or a scope substring) OR is a
   declared ``allowed_non_guarantee`` handle of a v0 ClaimSpec.
4. **No silently-empty row.** Every clause lands in exactly one mapping kind.

This gate ranges over a DECLARED, VERSIONED SUBSET of spec files
(:data:`lawvm.core.must_trace.MUST_TRACE_V0_IN_SCOPE_FILES`), NOT all prose and
NOT source docstrings. A ``deferred_with_owner`` mapping is an honest UNENFORCED
gap (a finding), NOT a satisfied requirement. The gate NEVER asserts "every MUST
in the repo is enforced."
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from lawvm.core.assumption_register import AssumptionRegister
from lawvm.core.claim_surface_manifest import V0_CLAIMS
from lawvm.core.invariant_spec import V0_INVARIANTS
from lawvm.core.must_trace import (
    ENFORCED_MAPPING_KINDS,
    MAPPING_KINDS,
    MUST_TRACE_V0_IN_SCOPE_FILES,
    MustTraceLedger,
    V0_MUST_CLAUSES,
    v0_must_trace_ledger,
)
from lawvm.finland.fi_assumptions import build_fi_assumption_register

# Repo root = three levels up from this test file (tests/ -> repo).
_REPO_ROOT = Path(__file__).resolve().parents[1]

# In-scope occurrences that are genuinely NOT normative requirements (e.g. a
# literal mention of the word in non-requirement prose). Empty for v0 — every
# MUST in notes/LAWVM_PIPELINE_CONTRACT.md is a real requirement — but the
# mechanism exists so a future incidental MUST can be waived WITH A REASON
# rather than smuggled into the ledger. Keys are (file, normalized-sentence).
_NON_NORMATIVE_WAIVERS: dict[tuple[str, str], str] = {}

_MUST_TOKEN = re.compile(r"\bMUST\b")


def _strip_code_fences(text: str) -> str:
    """Drop fenced code blocks so a literal ``MUST`` in a fence never counts."""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def _normative_must_count(spec_path: str) -> int:
    """Count normative MUST occurrences in ``spec_path`` (fences + waivers removed)."""
    text = (_REPO_ROOT / spec_path).read_text(encoding="utf-8")
    prose = _strip_code_fences(text)
    total = len(_MUST_TOKEN.findall(prose))
    waived = sum(1 for (f, _s) in _NON_NORMATIVE_WAIVERS if f == spec_path)
    return total - waived


# --------------------------------------------------------------------------- #
# (1) THE RATCHET — every in-scope normative MUST is represented.              #
# --------------------------------------------------------------------------- #


def test_every_in_scope_must_is_represented_in_the_ledger():
    """RATCHET: a NEW unmapped MUST in an in-scope file fails this test.

    The ledger must hold exactly one clause per normative MUST occurrence in each
    declared in-scope file (code fences + explicit waivers excluded).
    """
    ledger = v0_must_trace_ledger()
    for spec_path in MUST_TRACE_V0_IN_SCOPE_FILES:
        scanned = _normative_must_count(spec_path)
        ledger_rows = sum(1 for c in ledger.clauses if c.spec_source == spec_path)
        assert ledger_rows == scanned, (
            f"{spec_path}: scanned {scanned} normative MUST occurrence(s) but the "
            f"ledger holds {ledger_rows} clause(s) for this file. A NEW unmapped "
            f"MUST is the debt this ratchet surfaces — add a MustClause mapping it "
            f"to an invariant id / checker step / writer refusal / declared "
            f"non-guarantee, or (if genuinely not a requirement) add a reasoned "
            f"_NON_NORMATIVE_WAIVERS entry."
        )


def test_every_clause_spec_source_is_in_scope():
    """No ledger clause cites a file outside the declared in-scope set."""
    in_scope = set(MUST_TRACE_V0_IN_SCOPE_FILES)
    stray = [c.must_id for c in V0_MUST_CLAUSES if c.spec_source not in in_scope]
    assert not stray, (
        f"clauses citing an out-of-scope spec file: {stray!r} "
        f"(in scope: {sorted(in_scope)!r})"
    )


def test_every_clause_excerpt_is_a_real_substring_of_its_spec():
    """Self-evidencing: each excerpt's normative text actually appears in the spec."""
    cache: dict[str, str] = {}
    for clause in V0_MUST_CLAUSES:
        if clause.spec_source not in cache:
            cache[clause.spec_source] = (
                (_REPO_ROOT / clause.spec_source).read_text(encoding="utf-8")
            )
        spec_text = cache[clause.spec_source]
        # The excerpt is a faithful quote; allow normalised whitespace.
        excerpt_norm = re.sub(r"\s+", " ", clause.excerpt).strip()
        spec_norm = re.sub(r"\s+", " ", spec_text)
        assert excerpt_norm in spec_norm, (
            f"clause {clause.must_id!r} excerpt is not a substring of "
            f"{clause.spec_source!r}: {clause.excerpt!r}"
        )
        assert "MUST" in clause.excerpt, (
            f"clause {clause.must_id!r} excerpt carries no MUST token"
        )


# --------------------------------------------------------------------------- #
# (2) TARGET RESOLUTION — every mapping points at a real artifact.             #
# --------------------------------------------------------------------------- #


def _resolve_dotted(ref: str) -> object:
    """Import a ``module`` or ``module:symbol`` ref; return the object."""
    if ":" in ref:
        module_name, symbol = ref.split(":", 1)
        module = importlib.import_module(module_name)
        obj: object = module
        for part in symbol.split("."):
            obj = getattr(obj, part)
        return obj
    return importlib.import_module(ref)


def _assumption_handles() -> set[str]:
    """All stable handles by which a clause may reference a declared non-guarantee.

    A handle resolves if it is: a FI AssumptionRegister entry's witness_rule_id,
    one of its finding_refs, OR an ``allowed_non_guarantees`` handle declared on a
    v0 ClaimSpec (the declared-boundary plane the AssumptionRegister backs).
    """
    handles: set[str] = set()
    fi_register: tuple[AssumptionRegister, ...] = build_fi_assumption_register()
    for entry in fi_register:
        if entry.witness_rule_id:
            handles.add(entry.witness_rule_id)
        handles.update(entry.finding_refs)
    for claim in V0_CLAIMS:
        handles.update(claim.allowed_non_guarantees)
    return handles


def test_invariant_id_targets_exist_in_v0_invariants():
    invariant_ids = {inv.id for inv in V0_INVARIANTS}
    for clause in V0_MUST_CLAUSES:
        if clause.mapping_kind == "invariant_id":
            assert clause.target_ref in invariant_ids, (
                f"clause {clause.must_id!r} maps to invariant id "
                f"{clause.target_ref!r} which is not in V0_INVARIANTS"
            )


def test_checker_and_refusal_targets_are_importable():
    for clause in V0_MUST_CLAUSES:
        if clause.mapping_kind in ("checker_step", "writer_refusal"):
            try:
                resolved = _resolve_dotted(clause.target_ref)
            except (ImportError, AttributeError) as exc:
                pytest.fail(
                    f"clause {clause.must_id!r} ({clause.mapping_kind}) target "
                    f"{clause.target_ref!r} does not resolve: {exc!r}"
                )
            assert resolved is not None


def test_declared_non_guarantee_targets_resolve_to_a_handle():
    handles = _assumption_handles()
    for clause in V0_MUST_CLAUSES:
        if clause.mapping_kind == "declared_non_guarantee":
            assert clause.target_ref in handles, (
                f"clause {clause.must_id!r} declared_non_guarantee handle "
                f"{clause.target_ref!r} resolves to no AssumptionRegister entry / "
                f"ClaimSpec non-guarantee handle. Known handles: {sorted(handles)!r}"
            )


def test_deferred_with_owner_targets_name_an_owner_and_reason():
    for clause in V0_MUST_CLAUSES:
        if clause.mapping_kind == "deferred_with_owner":
            assert "owner=" in clause.target_ref and "reason=" in clause.target_ref, (
                f"clause {clause.must_id!r} is deferred_with_owner but its target_ref "
                f"does not name owner= and reason=: {clause.target_ref!r} — an "
                f"unenforced gap MUST name its owner + reason, never be opaque"
            )


# --------------------------------------------------------------------------- #
# (3) NO SILENTLY-EMPTY ROW — exactly one mapping kind per MUST.               #
# --------------------------------------------------------------------------- #


def test_every_clause_lands_in_exactly_one_known_mapping_kind():
    for clause in V0_MUST_CLAUSES:
        assert clause.mapping_kind in MAPPING_KINDS, (
            f"clause {clause.must_id!r} has unknown mapping_kind {clause.mapping_kind!r}"
        )
        # __post_init__ already forbids empty target_ref / excerpt; re-assert here
        # so an empty mapping can never sit silently in the ledger.
        assert clause.excerpt.strip()
        assert clause.target_ref.strip()


def test_clause_ids_are_unique():
    ids = [c.must_id for c in V0_MUST_CLAUSES]
    assert len(ids) == len(set(ids)), f"duplicate must_id in ledger: {ids!r}"


# --------------------------------------------------------------------------- #
# (4) HONESTY — the findings (unenforced MUSTs) are visible + counted.         #
# --------------------------------------------------------------------------- #


def test_deferred_and_enforced_partition_the_ledger():
    """Every clause is either enforced (live path) or a deferred finding — no third state."""
    ledger = v0_must_trace_ledger()
    assert len(ledger.enforced) + len(ledger.deferred) == len(ledger)
    # The deferred bucket is exactly the non-enforced kind.
    assert all(c.mapping_kind == "deferred_with_owner" for c in ledger.deferred)
    assert all(c.mapping_kind in ENFORCED_MAPPING_KINDS for c in ledger.enforced)


def test_ledger_root_is_deterministic_and_membership_sensitive():
    l1 = v0_must_trace_ledger()
    l2 = v0_must_trace_ledger()
    assert l1.ledger_root == l2.ledger_root
    dropped = MustTraceLedger(V0_MUST_CLAUSES[:-1])
    assert dropped.ledger_root != l1.ledger_root


def test_ledger_is_versioned_and_scope_is_declared():
    """Completeness is claim-relative + versioned (Pro §12) — never absolute."""
    ledger = v0_must_trace_ledger()
    assert ledger.must_trace_version == "v0"
    assert ledger.in_scope_files == MUST_TRACE_V0_IN_SCOPE_FILES
    assert len(ledger.in_scope_files) >= 1
