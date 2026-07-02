"""lawvm seeded-fault-study — inject known synthetic replay faults and measure
whether the accounting/verdict pipeline CATCHES them (surfaces a genuine-bug
signal) or ABSORBS them into a non-bug verdict (oracle_suspect / ORACLE_STALE /
editorial-convention / source-defect).

This is a READ-ONLY measurement harness.  It does NOT touch production replay:
it takes the *real* replay output for a statute, applies a small deterministic
perturbation to the materialized IR tree (or, for the dropped-op class, elides a
section the replay produced), and re-runs the SAME two-rail accounting pipeline
the corpus uses:

  Rail 1 (oracle-differential):  ``oracle_check._classify_statute`` with the
      perturbed ReplayResult injected.  Every replay/oracle section divergence
      lands on a typed diagnosis.  The diagnosis vocabulary partitions into

        GENUINE-BUG diagnoses (a mismatch the account would NOT explain away):
            REPLAY_EXTRA, REPLAY_MISSING, UNKNOWN, MISSING, EXTRA,
            SOURCE_PATHOLOGY, LIITE_DIFF, LIITE_BODY_DIFF
        ABSORBING diagnoses (a non-bug verdict — the escape-hatch surface):
            ORACLE_STALE, EDITORIAL_CONVENTION, SOURCE_INCOMPLETE,
            CORRIGENDUM_APPLIED

  Rail 2 (oracle-independent self-consistency, structural slice):
      ``tree_ops.check_invariants`` on the perturbed tree — label uniqueness,
      sibling sort order, nesting validity.  A NEW violation here cannot be
      blamed on the oracle: no oracle is consulted.  This is the slice of the
      self-consistency rail that an output-level perturbation can exercise
      (the chain/coverage signals derive from the fold execution, which a
      post-hoc output perturbation does not re-run — see the honest-limits note
      in the report).

An injected fault is scored per statute as:

  CAUGHT     the perturbation introduced at least one NEW genuine-bug rail-1
             diagnosis OR a new rail-2 invariant violation, relative to the
             unperturbed baseline for the same statute.
  ABSORBED   the perturbation changed the account, but ONLY by adding
             absorbing (non-bug) diagnoses and no new rail-2 violation — a
             false negative: the seeded fault hid inside a non-bug verdict.
  MASKED     the perturbation produced no change in either rail at all (the
             fault vanished — e.g. it landed on a section the oracle also lacks,
             or a whitespace-equal region that the comparison skips).

The headline numbers are the per-class CATCH RATE and ABSORPTION (false-negative)
RATE.  A high absorption rate would validate objection C in
FABLE_PUBLICATION_THESIS.md (§5) — "terminal verdicts are unfalsifiable escape
hatches"; a low one bounds it empirically.

Usage:
    lawvm seeded-fault-study                         # default bounded sample
    lawvm seeded-fault-study --statutes 1958/370,2004/629
    lawvm seeded-fault-study --sample 30 --workers 4
    lawvm seeded-fault-study --faults wrong_section_content,dropped_op
    lawvm seeded-fault-study --json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind

# ---------------------------------------------------------------------------
# Diagnosis partition (the accounting pipeline's verdict vocabulary)
# ---------------------------------------------------------------------------

# A genuine-bug rail-1 diagnosis: the account would NOT explain this away as a
# non-bug; it stays as an open, replay-owned divergence.
GENUINE_BUG_DIAGNOSES = frozenset(
    {
        "REPLAY_EXTRA",
        "REPLAY_MISSING",
        "UNKNOWN",
        "MISSING",
        "EXTRA",
        "SOURCE_PATHOLOGY",
        "LIITE_DIFF",
        "LIITE_BODY_DIFF",
    }
)

# An absorbing (non-bug) verdict: the escape-hatch surface objection C targets.
ABSORBING_DIAGNOSES = frozenset(
    {
        "ORACLE_STALE",
        "EDITORIAL_CONVENTION",
        "SOURCE_INCOMPLETE",
        "CORRIGENDUM_APPLIED",
    }
)


# ---------------------------------------------------------------------------
# IR perturbation primitives (deterministic, tree-local)
# ---------------------------------------------------------------------------


def _iter_sections(root: IRNode) -> List[Tuple[IRNode, Tuple[int, ...]]]:
    """Return (section_node, index_path) for every SECTION node, in tree order.

    ``index_path`` is the tuple of child indices from ``root`` to the section,
    used for deterministic, position-addressed rewrites.
    """
    out: List[Tuple[IRNode, Tuple[int, ...]]] = []

    def _walk(node: IRNode, path: Tuple[int, ...]) -> None:
        if node.kind == IRNodeKind.SECTION:
            out.append((node, path))
        for i, child in enumerate(node.children):
            _walk(child, path + (i,))

    _walk(root, ())
    return out


def _iter_sections_with_keys(
    root: IRNode,
) -> List[Tuple[IRNode, Tuple[int, ...], str]]:
    """Like ``_iter_sections`` but also returns each section's comparison key.

    The key matches ``section_keys.extract_ir_sections`` so callers can align a
    section node with the baseline classify result and prefer CLEAN sections
    (replay == oracle) as fault targets — corrupting an already-divergent
    section would mask the injected fault.
    """
    from lawvm.core.timeline_addresses import _iter_nodes_with_address
    from lawvm.tools.section_keys import section_key_from_path

    key_by_id: Dict[int, str] = {}
    for address, node in _iter_nodes_with_address(root):
        if not address.path or address.path[-1][0] != "section":
            continue
        key_by_id[id(node)] = section_key_from_path(address.path)

    out: List[Tuple[IRNode, Tuple[int, ...], str]] = []
    for node, path in _iter_sections(root):
        out.append((node, path, key_by_id.get(id(node), "")))
    return out


def _ordered_sections(
    root: IRNode, clean_keys: frozenset[str]
) -> List[Tuple[IRNode, Tuple[int, ...], str]]:
    """Sections ordered so CLEAN (currently-agreeing) sections come first.

    Preferring a clean target means an injected corruption is a genuinely NEW
    divergence rather than a modification of a section the account already
    flags — which is what makes CAUGHT/ABSORBED meaningful.
    """
    secs = _iter_sections_with_keys(root)
    return sorted(secs, key=lambda t: (0 if t[2] in clean_keys else 1,))


def _first_text_descendant_path(node: IRNode) -> Optional[Tuple[int, ...]]:
    """Return the index path (relative to ``node``) of the first substantive
    text-bearing descendant, or None."""
    if node.text and node.text.strip() and node.kind not in (IRNodeKind.NUM,):
        return ()

    def _walk(n: IRNode, path: Tuple[int, ...]) -> Optional[Tuple[int, ...]]:
        for i, child in enumerate(n.children):
            if (
                child.text
                and child.text.strip()
                and child.kind not in (IRNodeKind.NUM, IRNodeKind.HEADING)
            ):
                return path + (i,)
            found = _walk(child, path + (i,))
            if found is not None:
                return found
        return None

    return _walk(node, ())


def _rebuild_with_child_replaced(
    node: IRNode, path: Tuple[int, ...], transform: Callable[[IRNode], IRNode]
) -> IRNode:
    """Return a copy of ``node`` with the descendant at ``path`` transformed.

    ``path`` is a tuple of child indices.  Empty path transforms ``node``.
    """
    if not path:
        return transform(node)
    idx = path[0]
    children = list(node.children)
    children[idx] = _rebuild_with_child_replaced(children[idx], path[1:], transform)
    return dataclasses.replace(node, children=tuple(children))


def _rebuild_with_child_removed(node: IRNode, path: Tuple[int, ...]) -> IRNode:
    """Return a copy of ``node`` with the descendant at ``path`` removed."""
    idx = path[0]
    children = list(node.children)
    if len(path) == 1:
        del children[idx]
    else:
        children[idx] = _rebuild_with_child_removed(children[idx], path[1:])
    return dataclasses.replace(node, children=tuple(children))


def _rebuild_with_child_inserted(
    node: IRNode, path: Tuple[int, ...], new_child: IRNode
) -> IRNode:
    """Insert ``new_child`` as a child of the node at ``path`` (append)."""
    if not path:
        return dataclasses.replace(node, children=(*node.children, new_child))
    idx = path[0]
    children = list(node.children)
    children[idx] = _rebuild_with_child_inserted(children[idx], path[1:], new_child)
    return dataclasses.replace(node, children=tuple(children))


# ---------------------------------------------------------------------------
# Fault taxonomy — each returns a NEW body IR (or None if inapplicable)
# ---------------------------------------------------------------------------


@dataclass
class FaultOutcome:
    applied: bool
    body: Optional[IRNode] = None
    note: str = ""
    # Section comparison key(s) the perturbation targets. Scoring inspects the
    # rail-1 diagnosis delta AT THESE KEYS to isolate the injected fault from
    # incidental whole-body cascade (a known non-monotonicity artifact — a
    # single-section change shifts the body text score and can flip diagnoses on
    # unrelated sections). ``None`` => structural fault with no single owning
    # key; scoring falls back to the whole-body delta plus rail-2.
    target_keys: Optional[Tuple[str, ...]] = None


def _fault_wrong_section_content(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Swap a section's substantive text for a deterministic sentinel — models
    a wrong-content replay (the section exists but its body is corrupted)."""
    for sec, sec_path, key in _ordered_sections(body, clean_keys):
        tpath = _first_text_descendant_path(sec)
        if tpath is None:
            continue
        full = sec_path + tpath
        new_body = _rebuild_with_child_replaced(
            body,
            full,
            lambda n: dataclasses.replace(
                n, text="SEEDED_FAULT wrong section content sentinel."
            ),
        )
        clean = "clean" if key in clean_keys else "divergent"
        return FaultOutcome(
            True, new_body, f"section={sec.label}[{clean}]", target_keys=(key,)
        )
    return FaultOutcome(False, note="no text-bearing section")


def _fault_truncated_section(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Truncate a section's text to its first few characters — models a
    partial/dropped-tail replay of an existing section."""
    for sec, sec_path, key in _ordered_sections(body, clean_keys):
        tpath = _first_text_descendant_path(sec)
        if tpath is None:
            continue
        full = sec_path + tpath

        def _trunc(n: IRNode) -> IRNode:
            t = n.text or ""
            return dataclasses.replace(n, text=t[: max(1, len(t) // 4)])

        # Only apply where truncation is non-trivial.
        node = body
        for i in full:
            node = node.children[i]
        if len((node.text or "")) < 40:
            continue
        new_body = _rebuild_with_child_replaced(body, full, _trunc)
        clean = "clean" if key in clean_keys else "divergent"
        return FaultOutcome(
            True, new_body, f"section={sec.label}[{clean}]", target_keys=(key,)
        )
    return FaultOutcome(False, note="no long-text section")


def _fault_dropped_op(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Delete an entire section subtree from the output — models an amendment
    op that was skipped, so a section the oracle carries is absent from replay."""
    for sec, sec_path, key in _ordered_sections(body, clean_keys):
        if not sec_path:
            continue
        if sec.attrs.get("lawvm_repeal_placeholder") == "1":
            continue
        new_body = _rebuild_with_child_removed(body, sec_path)
        clean = "clean" if key in clean_keys else "divergent"
        return FaultOutcome(
            True, new_body, f"section={sec.label}[{clean}]", target_keys=(key,)
        )
    return FaultOutcome(False, note="no removable section")


def _fault_spurious_extra_section(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Append a fabricated section into a container — models a replay that
    invents a section the oracle does not have."""
    sections = _iter_sections(body)
    if not sections:
        return FaultOutcome(False, note="no section")
    _, sec_path = sections[0]
    if not sec_path:
        return FaultOutcome(False, note="section at root")
    container_path = sec_path[:-1]
    ghost = IRNode(
        kind=IRNodeKind.SECTION,
        label="9999",
        text="",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9999 §"),
            IRNode(
                kind=IRNodeKind.P,
                text="SEEDED_FAULT spurious extra section body.",
            ),
        ),
    )
    new_body = _rebuild_with_child_inserted(body, container_path, ghost)
    return FaultOutcome(True, new_body, "label=9999")


def _fault_off_by_one_label(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Relabel a section to an adjacent integer — models an off-by-one label
    bug.  Collides with a sibling if one exists (duplicate label = a rail-2
    invariant violation) or leaves a gap otherwise."""
    for sec, sec_path, _key in _ordered_sections(body, clean_keys):
        label = (sec.label or "").strip()
        if not label.isdigit():
            continue
        new_label = str(int(label) + 1)

        def _relabel(n: IRNode, new_label: str = new_label) -> IRNode:
            new_children = list(n.children)
            for i, ch in enumerate(new_children):
                if ch.kind == IRNodeKind.NUM:
                    new_children[i] = dataclasses.replace(
                        ch, text=f"{new_label} §"
                    )
            return dataclasses.replace(
                n, label=new_label, children=tuple(new_children)
            )

        new_body = _rebuild_with_child_replaced(body, sec_path, _relabel)
        return FaultOutcome(True, new_body, f"{label}->{new_label}")
    return FaultOutcome(False, note="no integer-labelled section")


def _fault_mis_nested_insert(
    body: IRNode, rng: random.Random, clean_keys: frozenset[str]
) -> FaultOutcome:
    """Move a section from its chapter to a different chapter — models a
    mis-nested INSERT (section materialized under the wrong container)."""
    # Find two distinct chapters, each with >=1 section child.
    chapters: List[Tuple[IRNode, Tuple[int, ...]]] = []

    def _walk(node: IRNode, path: Tuple[int, ...]) -> None:
        if node.kind == IRNodeKind.CHAPTER:
            chapters.append((node, path))
        for i, child in enumerate(node.children):
            _walk(child, path + (i,))

    _walk(body, ())
    src_idx = None
    dst_path = None
    moved_section = None
    for ch, cpath in chapters:
        sec_children = [
            (i, c) for i, c in enumerate(ch.children) if c.kind == IRNodeKind.SECTION
        ]
        if sec_children and src_idx is None:
            src_idx = (cpath, sec_children[0][0], sec_children[0][1])
        elif dst_path is None and src_idx is not None and cpath != src_idx[0]:
            dst_path = cpath
    if src_idx is None or dst_path is None:
        return FaultOutcome(False, note="need two chapters with sections")
    src_cpath, sec_child_i, moved_section = src_idx
    body2 = _rebuild_with_child_removed(body, src_cpath + (sec_child_i,))
    body3 = _rebuild_with_child_inserted(body2, dst_path, moved_section)
    return FaultOutcome(True, body3, f"section={moved_section.label} chapter-moved")


FAULT_TAXONOMY: Dict[
    str, Callable[[IRNode, random.Random, "frozenset[str]"], FaultOutcome]
] = {
    "wrong_section_content": _fault_wrong_section_content,
    "truncated_section": _fault_truncated_section,
    "dropped_op": _fault_dropped_op,
    "spurious_extra_section": _fault_spurious_extra_section,
    "off_by_one_label": _fault_off_by_one_label,
    "mis_nested_insert": _fault_mis_nested_insert,
}

FAULT_ORDER = tuple(FAULT_TAXONOMY.keys())


# ---------------------------------------------------------------------------
# ReplayResult perturbation
# ---------------------------------------------------------------------------


def _neutralized_replay(master: Any) -> Any:
    """Return a copy of ``master`` with the fold-rematerialization recovery
    disabled (``timelines=None``).

    Rail 1 (``_classify_statute``) does NOT compare the materialized output tree
    to the oracle naively: for every candidate divergence it RE-DERIVES the true
    section text from the replay ``timelines`` (``replay_section_matches_text_at_
    cutoff`` / ``replay_section_has_future_effective_version``) and, if that fold-
    reconstruction still matches the oracle, stamps ORACLE_STALE.  That recovery
    reads the fold products, NOT ``materialized_state.ir`` — so a perturbation of
    the output tree alone is invisible to it and every injected fault is
    reflexively re-absorbed as ORACLE_STALE.

    That behaviour is faithful to a *display-only* corruption but NOT to the
    fault class this study targets: a genuine materializer/apply bug corrupts the
    fold output itself, so the recovery re-run — which executes the SAME buggy
    fold — would see the same corruption and could NOT recover the true text.  We
    model that by clearing ``timelines`` so the recovery abstains, forcing the
    account to adjudicate the actual output tree.  The BASELINE snapshot is taken
    under the identical neutralization (see ``run_statute``), so any oracle-
    staleness the recovery would legitimately have masked is already present in
    the baseline and the injected-fault delta isolates only the new divergence.
    """
    products = master.products
    if products.timelines is None:
        return master
    new_products = dataclasses.replace(products, timelines=None)
    return dataclasses.replace(master, products=new_products)


def _perturbed_replay(master: Any, new_body: IRNode) -> Any:
    """Return a copy of ``master`` (a ReplayResult) whose materialized IR is
    ``new_body`` and whose fold-rematerialization recovery is disabled.  Non-
    frozen ``ReplayProducts`` is shallow-copied so the original is untouched.
    """
    master = _neutralized_replay(master)
    products = master.products
    new_mat = products.materialized_state.with_ir(new_body)
    new_products = dataclasses.replace(products, materialized_state=new_mat)
    return dataclasses.replace(master, products=new_products)


# ---------------------------------------------------------------------------
# Rail evaluation
# ---------------------------------------------------------------------------


@dataclass
class RailSnapshot:
    # Per-section-key rail-1 diagnosis (only divergent sections appear).
    diag_by_key: Dict[str, str] = field(default_factory=dict)
    invariant_violations: int = 0
    invariant_sample: List[str] = field(default_factory=list)


def _classify_diag_by_key(result: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if result is None or result.has_error:
        return out
    for s in result.section_results:
        out[str(s["section"])] = str(s["diagnosis"])
    return out


def _snapshot(statute_id: str, master: Any) -> RailSnapshot:
    """Run both rails against a (possibly perturbed) ReplayResult.

    ``statute_id`` is the CANONICAL id the tool was called with (e.g.
    ``1958/370``) — it must be used for the oracle lookup, NOT ``master.id``,
    which carries the internal year-last form (``370/1958``) and would silently
    resolve to NO_ORACLE.
    """
    from lawvm.core.tree_ops import check_invariants
    from lawvm.tools.oracle_check import _classify_statute

    body = master.materialized_state.ir
    violations = check_invariants(body)
    result = _classify_statute(
        statute_id, "official_consolidation", replay_result=master
    )
    return RailSnapshot(
        diag_by_key=_classify_diag_by_key(result),
        invariant_violations=len(violations),
        invariant_sample=violations[:3],
    )


def _diag_delta(base: Dict[str, str], pert: Dict[str, str]) -> Dict[str, str]:
    """Return {section_key: new_diagnosis} for every section whose diagnosis
    APPEARED or CHANGED in the perturbed run.

    Key-level (not aggregate-count) comparison: the injected fault flips exactly
    the perturbed section(s), so a section that newly diverges — or whose
    diagnosis changed — is where the fault surfaced. Sections that were already
    divergent and stayed the diagnosis are ignored (pre-existing account state).
    """
    out: Dict[str, str] = {}
    for key, diag in pert.items():
        if base.get(key) != diag:
            out[key] = diag
    return out


@dataclass
class InjectionResult:
    statute_id: str
    fault: str
    applied: bool
    outcome: str  # CAUGHT / ABSORBED / MASKED / INAPPLICABLE / ERROR
    # {section_key: diagnosis} for sections that newly diverged as a bug
    new_bug_diags: Dict[str, str] = field(default_factory=dict)
    # {section_key: diagnosis} for sections that newly diverged as a non-bug
    new_absorbing_diags: Dict[str, str] = field(default_factory=dict)
    rail2_new_violations: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _score_injection(
    statute_id: str,
    fault: str,
    applied: bool,
    note: str,
    base: RailSnapshot,
    pert: Optional[RailSnapshot],
    target_keys: Optional[Tuple[str, ...]] = None,
) -> InjectionResult:
    if not applied or pert is None:
        return InjectionResult(
            statute_id, fault, False, "INAPPLICABLE", note=note
        )
    delta = _diag_delta(base.diag_by_key, pert.diag_by_key)
    # When the fault owns specific section key(s), isolate the diagnosis delta to
    # those keys — the injected fault surfaces exactly there. Other sections that
    # flipped are whole-body cascade (non-monotonicity artifact) and are NOT the
    # injected fault. When target_keys is None (structural faults with no single
    # owning key), use the whole-body delta together with rail-2.
    if target_keys is not None:
        wanted = set(target_keys)
        delta = {k: v for k, v in delta.items() if k in wanted}
    new_bug: Dict[str, str] = {}
    new_absorb: Dict[str, str] = {}
    for key, diag in delta.items():
        if diag in ABSORBING_DIAGNOSES:
            new_absorb[key] = diag
        else:
            # GENUINE_BUG_DIAGNOSES and any other (unpartitioned) diagnosis are
            # both treated as bug-signalling: they are open, unexplained
            # divergences the account did NOT wave away.
            new_bug[key] = diag
    rail2_new = max(0, pert.invariant_violations - base.invariant_violations)

    if new_bug or rail2_new > 0:
        outcome = "CAUGHT"
    elif new_absorb:
        outcome = "ABSORBED"
    else:
        outcome = "MASKED"
    return InjectionResult(
        statute_id=statute_id,
        fault=fault,
        applied=True,
        outcome=outcome,
        new_bug_diags=new_bug,
        new_absorbing_diags=new_absorb,
        rail2_new_violations=rail2_new,
        note=note,
    )


def run_statute(
    statute_id: str, faults: Tuple[str, ...], seed: int = 0
) -> List[InjectionResult]:
    """Inject every requested fault class into one statute and score each."""
    from lawvm.tools.oracle_check import _classify_statute

    results: List[InjectionResult] = []
    try:
        base_classify = _classify_statute(statute_id, "official_consolidation")
    except Exception as exc:  # noqa: BLE001
        return [
            InjectionResult(statute_id, f, False, "ERROR", note=f"{type(exc).__name__}: {exc}")
            for f in faults
        ]
    if base_classify is None or base_classify.has_error:
        err = base_classify.error if base_classify else "None"
        return [
            InjectionResult(statute_id, f, False, "ERROR", note=f"baseline:{err}")
            for f in faults
        ]
    master = base_classify.replay_result
    if master is None:
        return [
            InjectionResult(statute_id, f, False, "ERROR", note="no replay_result")
            for f in faults
        ]
    # Take the BASELINE under the same recovery-neutralization the perturbed run
    # uses (timelines cleared), so the injected-fault delta is apples-to-apples:
    # any oracle-staleness the fold recovery legitimately masks is already in the
    # baseline, and CAUGHT/ABSORBED reflects only the NEW divergence the fault
    # introduces.  (See ``_neutralized_replay``.)
    neutral_master = _neutralized_replay(master)
    try:
        base = _snapshot(statute_id, neutral_master)
    except Exception as exc:  # noqa: BLE001
        return [
            InjectionResult(statute_id, f, False, "ERROR", note=f"baseline_snapshot:{type(exc).__name__}: {exc}")
            for f in faults
        ]
    base_body = master.materialized_state.ir

    # CLEAN sections = replay sections the neutralized-baseline account does NOT
    # flag.  Faults prefer these so the injected corruption is a genuinely NEW
    # divergence rather than a modification of an already-open one.
    from lawvm.tools.section_keys import extract_ir_sections

    divergent_keys = set(base.diag_by_key.keys())
    replay_keys = set(extract_ir_sections(base_body).keys())
    clean_keys = frozenset(replay_keys - divergent_keys)

    for fault in faults:
        rng = random.Random(f"{statute_id}:{fault}:{seed}")
        fn = FAULT_TAXONOMY[fault]
        try:
            fo = fn(base_body, rng, clean_keys)
        except Exception as exc:  # noqa: BLE001
            results.append(
                InjectionResult(statute_id, fault, False, "ERROR", note=f"apply:{type(exc).__name__}: {exc}")
            )
            continue
        if not fo.applied or fo.body is None:
            results.append(_score_injection(statute_id, fault, False, fo.note, base, None))
            continue
        try:
            pert_master = _perturbed_replay(master, fo.body)
            pert = _snapshot(statute_id, pert_master)
        except Exception as exc:  # noqa: BLE001
            results.append(
                InjectionResult(statute_id, fault, True, "ERROR", note=f"score:{type(exc).__name__}: {exc}")
            )
            continue
        results.append(
            _score_injection(
                statute_id,
                fault,
                True,
                fo.note,
                base,
                pert,
                target_keys=fo.target_keys,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------


# A small curated pool of FI statutes that resolve against the canonical data
# root, spanning trivially-clean and heavily-amended (multi-chapter) statutes so
# the study is not run only on structurally trivial acts.  Kept intentionally
# small and hardcoded so the module is self-contained (no dependency on the
# corpus-index lane) and the default run is bounded — the study is a spot-check,
# not a corpus sweep (that would trip the watchdog; see the report).
_CURATED_SAMPLE: Tuple[str, ...] = (
    "1958/370",  # multi-chapter, heavily amended
    "1978/38",
    "1999/731",  # perustuslaki
    "2000/812",
    "2004/629",
    "2011/379",
)


def _default_sample(n: int, seed: int) -> List[str]:
    """Return a bounded, deterministic sample of curated, resolvable FI IDs.

    Draws from ``_CURATED_SAMPLE``.  ``n <= 0`` or ``n >= len(pool)`` returns the
    whole pool; otherwise a deterministic ``seed``-keyed subset preserving a
    stable order.  Unresolvable IDs are handled downstream (scored as ERROR and
    excluded from the applied denominator), so the sample stays honest even if a
    canonical-root revision drops one.
    """
    pool = list(_CURATED_SAMPLE)
    if n <= 0 or n >= len(pool):
        return pool
    rng = random.Random(seed)
    picked = set(rng.sample(range(len(pool)), n))
    return [sid for i, sid in enumerate(pool) if i in picked]


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


@dataclass
class ClassSummary:
    fault: str
    applied: int = 0
    caught: int = 0
    absorbed: int = 0
    masked: int = 0
    inapplicable: int = 0
    error: int = 0

    @property
    def catch_rate(self) -> float:
        return self.caught / self.applied if self.applied else 0.0

    @property
    def absorption_rate(self) -> float:
        return self.absorbed / self.applied if self.applied else 0.0


def summarize(results: List[InjectionResult]) -> Dict[str, ClassSummary]:
    by_fault: Dict[str, ClassSummary] = {f: ClassSummary(fault=f) for f in FAULT_ORDER}
    for r in results:
        cs = by_fault.setdefault(r.fault, ClassSummary(fault=r.fault))
        if r.outcome == "INAPPLICABLE":
            cs.inapplicable += 1
            continue
        if r.outcome == "ERROR":
            cs.error += 1
            continue
        cs.applied += 1
        if r.outcome == "CAUGHT":
            cs.caught += 1
        elif r.outcome == "ABSORBED":
            cs.absorbed += 1
        elif r.outcome == "MASKED":
            cs.masked += 1
    return by_fault


def _format_report(
    statutes: List[str],
    results: List[InjectionResult],
    summaries: Dict[str, ClassSummary],
) -> str:
    lines: List[str] = []
    lines.append(
        f"Seeded-fault absorption study — {len(statutes)} statutes, "
        f"{len(FAULT_ORDER)} fault classes, {len(results)} injections"
    )
    lines.append("")
    lines.append(
        f"{'fault class':<24} {'applied':>7} {'CAUGHT':>7} {'ABSORB':>7} "
        f"{'MASK':>5} {'catch%':>7} {'absorb%':>8}"
    )
    lines.append("-" * 74)
    tot_applied = tot_caught = tot_absorbed = tot_masked = 0
    for fault in FAULT_ORDER:
        cs = summaries[fault]
        tot_applied += cs.applied
        tot_caught += cs.caught
        tot_absorbed += cs.absorbed
        tot_masked += cs.masked
        lines.append(
            f"{fault:<24} {cs.applied:>7} {cs.caught:>7} {cs.absorbed:>7} "
            f"{cs.masked:>5} {cs.catch_rate:>6.0%} {cs.absorption_rate:>7.0%}"
        )
    lines.append("-" * 74)
    overall_catch = tot_caught / tot_applied if tot_applied else 0.0
    overall_absorb = tot_absorbed / tot_applied if tot_applied else 0.0
    lines.append(
        f"{'ALL':<24} {tot_applied:>7} {tot_caught:>7} {tot_absorbed:>7} "
        f"{tot_masked:>5} {overall_catch:>6.0%} {overall_absorb:>7.0%}"
    )
    lines.append("")
    lines.append(
        "CAUGHT = new genuine-bug rail-1 diagnosis or new rail-2 invariant "
        "violation."
    )
    lines.append(
        "ABSORBED (false negative) = fault surfaced ONLY as a non-bug verdict "
        "(ORACLE_STALE / EDITORIAL_CONVENTION / SOURCE_INCOMPLETE / "
        "CORRIGENDUM_APPLIED)."
    )
    lines.append(
        "MASKED = fault produced no accounting change (vanished — landed where "
        "the comparison is blind)."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_sample(
    statutes: List[str], faults: Tuple[str, ...], workers: int, seed: int
) -> List[InjectionResult]:
    if workers <= 1 or len(statutes) <= 1:
        out: List[InjectionResult] = []
        for sid in statutes:
            out.extend(run_statute(sid, faults, seed=seed))
        return out
    # Bounded process pool; each statute is independent.
    import concurrent.futures as cf

    out = []
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_statute, sid, faults, seed): sid for sid in statutes}
        for fut in cf.as_completed(futs):
            try:
                out.extend(fut.result())
            except Exception as exc:  # noqa: BLE001
                sid = futs[fut]
                out.extend(
                    InjectionResult(sid, f, False, "ERROR", note=f"worker:{exc}")
                    for f in faults
                )
    return out


def main(args) -> None:
    seed = int(getattr(args, "seed", 0) or 0)
    explicit = (getattr(args, "statutes", "") or "").strip()
    if explicit:
        statutes = [s.strip() for s in explicit.split(",") if s.strip()]
    else:
        statutes = _default_sample(int(getattr(args, "sample", 24) or 24), seed)
    faults_arg = (getattr(args, "faults", "") or "").strip()
    if faults_arg:
        faults = tuple(f.strip() for f in faults_arg.split(",") if f.strip())
        unknown = set(faults) - set(FAULT_ORDER)
        if unknown:
            raise SystemExit(
                f"unknown fault class(es): {sorted(unknown)}; "
                f"choose from {list(FAULT_ORDER)}"
            )
    else:
        faults = FAULT_ORDER

    workers = int(getattr(args, "workers", 4) or 4)
    results = _run_sample(statutes, faults, workers, seed)
    summaries = summarize(results)

    if getattr(args, "json", False):
        payload = {
            "statutes": statutes,
            "faults": list(faults),
            "results": [r.to_dict() for r in results],
            "summary": {
                f: {
                    "applied": s.applied,
                    "caught": s.caught,
                    "absorbed": s.absorbed,
                    "masked": s.masked,
                    "inapplicable": s.inapplicable,
                    "error": s.error,
                    "catch_rate": s.catch_rate,
                    "absorption_rate": s.absorption_rate,
                }
                for f, s in summaries.items()
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(_format_report(statutes, results, summaries))


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lawvm-seeded-fault-study",
        description=(
            "Inject known synthetic replay faults and measure whether the "
            "terminal-verdict accounting CATCHES them (bug signal) or ABSORBS "
            "them into a non-bug verdict."
        ),
    )
    p.add_argument(
        "--statutes",
        default="",
        help="comma-separated canonical statute ids (e.g. 1958/370,2004/629); "
        "overrides --sample",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=24,
        help="size of the deterministic default sample when --statutes is unset",
    )
    p.add_argument(
        "--faults",
        default="",
        help=f"comma-separated fault classes; default = all: {list(FAULT_ORDER)}",
    )
    p.add_argument("--workers", type=int, default=1, help="parallel statute workers")
    p.add_argument("--seed", type=int, default=0, help="deterministic sample/rng seed")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return p


def cli_main(argv: Optional[List[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    main(args)


if __name__ == "__main__":  # pragma: no cover
    cli_main()
