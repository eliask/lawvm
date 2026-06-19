"""Bitemporal BROKEN-reference detection — the dangling-citation detector.

First consumer artifact of the resolved reference graph. Given a *resolved*
citation (a ``ReferenceMention`` that names a target statute + provision and the
interval over which the citing context is valid), decide whether the target
provision is a live, reachable address today.

The bitemporal question this detector answers, for one resolved mention::

    (a) Did the target provision EXIST at the citation's ``valid_at`` start?
    (b) Does the target provision STILL EXIST in the current statute tree?

    existed-when-cited AND gone-now            -> BROKEN
    did-not-exist-when-cited                   -> BROKEN (never_existed)
    existed-when-cited AND still-present        -> no finding
    cannot materialize either tree             -> BrokenCheckUnavailable

A reference that points at a provision that was alive when the citation was
written but has since been repealed or renumbered is a *dangling citation*: the
statute book still tells the reader to look at an address that no longer holds
what the citing text assumes. That is the finding.

Purity / dependency injection
-----------------------------
This module is a *pure* detector. It does **not** import the heavy
replay/materialization engine. The two capabilities it needs —

  * ``tree_as_of(statute_id, on)`` — the materialized statute tree as it stood
    on a given date (or ``None`` if it cannot be materialized), and
  * ``provision_present(tree, ref)`` — whether a ``ProvisionRef`` resolves to a
    live node in that tree —

are **injected** as callables. This keeps the detector trivially testable with
synthetic in-memory trees and a fake clock, and keeps the (expensive, stateful)
replay machinery out of the import graph.

A thin read-only adapter over the real engine is provided
(``default_tree_as_of`` / ``default_provision_present``); see the SEAM note on
``default_tree_as_of`` for why it is a documented seam rather than a fully wired
default — the as-of materialization path is the heavy ``legal_pit`` replay and
must be opted into explicitly by the integration layer.

Fail-loud (AGENTS.md §1.1)
--------------------------
If ``tree_as_of`` returns ``None`` for a tree we must inspect, we emit a typed
``BrokenCheckUnavailable`` finding — never a false ``BROKEN``. The detector never
guesses a verdict it could not actually establish.

This module produces findings only. It does NOT mutate the input mentions; an
integration step can re-tag a mention's ``cite_confidence`` to ``BROKEN`` from a
finding. It also does not own ``ref_mention_extractor`` or any other references
lane.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Optional

from lawvm.core.ir import IRNode

if TYPE_CHECKING:
    from lawvm.corpus_store import CorpusStore
from lawvm.core.reference_mention import (
    CiteConfidence,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)

__all__ = [
    "BrokenReason",
    "BrokenReferenceFinding",
    "BrokenCheckUnavailable",
    "TreeAsOf",
    "ProvisionPresent",
    "detect_broken",
    "default_tree_as_of",
    "default_provision_present",
]


# ---------------------------------------------------------------------------
# Finding types
# ---------------------------------------------------------------------------
#
# These are intentionally distinct from the parquet-row-flavored
# ``core.reference_mention.BrokenReferenceFinding`` (which is the audit-lane
# record paired with a BROKEN ReferenceMention and owned by the extractor
# lanes). This detector is a fresh, self-contained consumer and carries the
# richer bitemporal evidence (typed reason + the detected interval + the
# resolved source/target refs + the source span), so it defines its own
# finding types here rather than coupling to the extractor's row schema.


class BrokenReason(Enum):
    """Why a resolved reference is BROKEN, as a closed enum (never a string)."""

    REPEALED_SINCE = "repealed_since"
    """Target existed when cited; absent from the current tree (repealed)."""

    RENUMBERED_SINCE = "renumbered_since"
    """Target existed when cited; absent at the same address now but the
    enclosing statute still exists — i.e. the provision moved / was renumbered.

    Distinguished from ``REPEALED_SINCE`` only when we can confirm the target
    statute is still present today (so the gap is an address move, not a whole
    repeal). Without that confirmation we conservatively report
    ``REPEALED_SINCE``."""

    NEVER_EXISTED = "never_existed"
    """Target did not exist at the citation's ``valid_at`` — a citation that was
    dangling from the moment it was written (typo, premature reference, or a
    misresolution upstream)."""


@dataclass(frozen=True, slots=True)
class BrokenReferenceFinding:
    """A resolved citation whose target is not reachable today.

    Attributes:
        source: Where the citation lives (the citing provision).
        target: Where it points (the resolved target provision).
        reason: Closed-enum reason (repealed / renumbered / never existed).
        detected_interval: ``(cited_on, detected_on)`` — the citation's
            ``valid_at`` start that anchored the "existed-when-cited" check, and
            the as-of date used for the "still-exists" check. Either may be
            ``None`` when the corresponding bound was open.
        source_span: Provenance back into the source text, when known.
        rule_id: Stable rule identifier (AGENTS.md §7).
    """

    source: ProvisionRef
    target: ProvisionRef
    reason: BrokenReason
    detected_interval: tuple[Optional[date], Optional[date]]
    source_span: Optional[SourceSpan]
    rule_id: str = "fi.refs.broken_detection"


@dataclass(frozen=True, slots=True)
class BrokenCheckUnavailable:
    """The brokenness of a reference could not be established (fail-loud).

    Emitted instead of a (false) ``BrokenReferenceFinding`` when a required
    statute tree could not be materialized. Carries which check failed so the
    integration layer can route it (retry / record / escalate) rather than
    silently treating the reference as fine OR as broken.

    Attributes:
        source: The citing provision.
        target: The resolved target provision we could not check.
        unavailable_for: Which materialization failed — ``"cited"`` (the
            as-of-citation tree) or ``"current"`` (the present tree).
        as_of: The date for which materialization was requested, if any.
        reason: Human-readable diagnostic.
        rule_id: Stable rule identifier.
    """

    source: ProvisionRef
    target: ProvisionRef
    unavailable_for: str
    as_of: Optional[date]
    reason: str
    rule_id: str = "fi.refs.broken_detection.unavailable"


# Injected capability signatures. Documented as type aliases so callers (and the
# adapters below) share one contract.
TreeAsOf = Callable[[str, date], Optional[IRNode]]
"""``(statute_id, on) -> materialized tree as-of ``on``, or None if it cannot be
materialized.`` Returning ``None`` is a fail-loud signal, NOT "empty tree"."""

ProvisionPresent = Callable[[IRNode, ProvisionRef], bool]
"""``(tree, ref) -> whether ``ref`` resolves to a live node in ``tree``.``"""


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


# A far-future sentinel for "the current tree". We materialize the present tree
# by asking for the tree as-of today. ``date.today()`` is intentionally read at
# call time inside ``detect_broken`` so the function stays deterministic per
# invocation; tests inject their own ``tree_as_of`` and never hit a real clock.


def _citation_start(mention: ReferenceMention) -> Optional[date]:
    start, _end = mention.valid_at_interval
    return start


def _is_resolved_target(mention: ReferenceMention) -> bool:
    """Only resolved references carry a checkable target.

    UNRESOLVED/OPEN/AMBIGUOUS mentions have no single concrete target to test
    (and UNRESOLVED carries ``target_provision_ref is None`` by the core
    invariant). A mention already typed BROKEN is skipped — it is the input we
    would re-derive, not something to re-flag.
    """
    if mention.target_provision_ref is None:
        return False
    if mention.cite_confidence in (
        CiteConfidence.UNRESOLVED,
        CiteConfidence.OPEN,
        CiteConfidence.AMBIGUOUS,
        CiteConfidence.BROKEN,
    ):
        return False
    return True


def detect_broken(
    mentions: Iterable[ReferenceMention],
    *,
    tree_as_of: TreeAsOf,
    provision_present: ProvisionPresent,
    current_as_of: Optional[date] = None,
) -> list[BrokenReferenceFinding | BrokenCheckUnavailable]:
    """Detect dangling citations among already-resolved reference mentions.

    For each mention with a concrete resolved target:

      1. Materialize the target statute tree as-of the citation's ``valid_at``
         start (``tree_as_of(target_statute_id, cited_on)``).
      2. Materialize the *current* target statute tree
         (``tree_as_of(target_statute_id, current_as_of or today)``).
      3. existed-when-cited := ``provision_present(cited_tree, target)``.
         present-now      := ``provision_present(current_tree, target)``.
      4. ``not existed-when-cited``           -> NEVER_EXISTED finding.
         ``existed-when-cited and not now``    -> REPEALED_SINCE /
                                                  RENUMBERED_SINCE finding.
         ``existed-when-cited and present-now`` -> no finding.

    Args:
        mentions: Resolved reference mentions to check. Non-resolved mentions
            (UNRESOLVED / OPEN / AMBIGUOUS / already-BROKEN, or ``None`` target)
            are skipped — there is no single concrete target to test.
        tree_as_of: Injected as-of materializer. Returning ``None`` means
            "cannot materialize" and yields a ``BrokenCheckUnavailable`` finding
            (never a false BROKEN).
        provision_present: Injected presence test for a ``ProvisionRef`` against
            a materialized tree.
        current_as_of: The date taken as "now" for the still-exists check;
            defaults to ``date.today()`` at call time.

    Returns:
        A list of findings — ``BrokenReferenceFinding`` for confirmed dangling
        citations and ``BrokenCheckUnavailable`` for checks that could not be
        completed. Mentions are never mutated.
    """
    now = current_as_of if current_as_of is not None else date.today()
    findings: list[BrokenReferenceFinding | BrokenCheckUnavailable] = []

    for mention in mentions:
        if not _is_resolved_target(mention):
            continue

        target = mention.target_provision_ref
        assert target is not None  # guarded by _is_resolved_target
        source = mention.source_provision_ref
        target_statute = target.statute_id
        if not target_statute:
            # Internal/anaphoric target with no resolved statute identity is not
            # a cross-statute brokenness question this detector can answer.
            continue

        cited_on = _citation_start(mention)

        # --- (b) current tree first: if the whole statute is gone now, the
        # reference is dangling regardless of when it was written. ---
        current_tree = tree_as_of(target_statute, now)
        if current_tree is None:
            findings.append(
                BrokenCheckUnavailable(
                    source=source,
                    target=target,
                    unavailable_for="current",
                    as_of=now,
                    reason=(
                        f"current tree for target statute {target_statute!r} "
                        "could not be materialized; brokenness undetermined"
                    ),
                )
            )
            continue

        # --- (a) the as-of-citation tree. With no citation start date we have
        # no temporal anchor for "existed-when-cited"; that is a missing input,
        # not a broken reference. ---
        if cited_on is None:
            cited_tree: Optional[IRNode] = current_tree
        else:
            cited_tree = tree_as_of(target_statute, cited_on)
            if cited_tree is None:
                findings.append(
                    BrokenCheckUnavailable(
                        source=source,
                        target=target,
                        unavailable_for="cited",
                        as_of=cited_on,
                        reason=(
                            f"as-of-{cited_on.isoformat()} tree for target "
                            f"statute {target_statute!r} could not be "
                            "materialized; brokenness undetermined"
                        ),
                    )
                )
                continue

        existed_when_cited = provision_present(cited_tree, target)
        present_now = provision_present(current_tree, target)

        if not existed_when_cited:
            findings.append(
                BrokenReferenceFinding(
                    source=source,
                    target=target,
                    reason=BrokenReason.NEVER_EXISTED,
                    detected_interval=(cited_on, now),
                    source_span=mention.source_span,
                )
            )
            continue

        if present_now:
            # Live, reachable target — not a finding.
            continue

        # Existed when cited, gone now. Distinguish renumber from repeal: if the
        # statute root still exists today (it does — we materialized it) but the
        # provision moved, that is a renumber. We only have provision-level
        # presence here, so we treat "statute materializes but provision absent"
        # as RENUMBERED_SINCE only when the statute clearly still carries other
        # content; otherwise REPEALED_SINCE. Without a structural diff we cannot
        # always tell them apart, so the conservative default is REPEALED_SINCE.
        reason = _classify_disappearance(current_tree)
        findings.append(
            BrokenReferenceFinding(
                source=source,
                target=target,
                reason=reason,
                detected_interval=(cited_on, now),
                source_span=mention.source_span,
            )
        )

    return findings


def _classify_disappearance(current_tree: IRNode) -> BrokenReason:
    """Best-effort repeal-vs-renumber call for a target absent from the current tree.

    The current statute tree materialized (so the *statute* exists today). If it
    still carries provision-bearing structure, a missing target is more likely a
    renumber/move than a whole-statute repeal; if the tree is effectively empty
    (a repeal placeholder), it is a repeal. This is deliberately conservative —
    it never invents a target address; it only labels the closed-enum reason.
    """
    if _has_provision_content(current_tree):
        return BrokenReason.RENUMBERED_SINCE
    return BrokenReason.REPEALED_SINCE


def _has_provision_content(tree: IRNode) -> bool:
    """True if the tree contains at least one SECTION-bearing node."""
    from lawvm.core.semantic_types import IRNodeKind

    stack: list[IRNode] = [tree]
    while stack:
        node = stack.pop()
        if node.kind is IRNodeKind.SECTION:
            return True
        stack.extend(node.children)
    return False


# ---------------------------------------------------------------------------
# Thin read-only adapters over the real engine
# ---------------------------------------------------------------------------
#
# SEAM NOTE — the as-of materializer.
# -----------------------------------
# The real "statute tree as-of date" capability is the ``legal_pit`` replay:
#   replay_xml(request=ReplayXmlRequest(parent_id=statute_id, mode="legal_pit",
#              as_of=on.isoformat(), ...)).products -> materialized_state.tree
# That path is the heavy, stateful materialization engine (full amendment
# replay). This module deliberately does NOT import it at module load. The
# adapter below performs a LAZY import so the detector stays pure/importable
# without the engine, and so the integration layer must consciously opt into the
# cost. ``default_tree_as_of`` is therefore a documented seam: it returns a
# ``TreeAsOf`` closure but the actual replay wiring is intentionally minimal and
# expected to be completed/parameterized by the integration step (corpus
# selection, oracle selector, strictness, caching of per-(statute, as_of)
# materializations). Until then, prefer injecting your own ``tree_as_of`` in
# tests and callers.


def default_tree_as_of(store: "CorpusStore") -> TreeAsOf:
    """Build a read-only ``TreeAsOf`` over the real ``legal_pit`` replay engine.

    SEAM: this is a thin adapter, not a fully tuned default. It lazily imports
    the replay engine (kept out of this module's import surface) and asks for the
    point-in-time materialized tree of one statute as-of a date. ``store`` is the
    corpus/consolidated store the replay should read from.

    It returns ``None`` (fail-loud) on any materialization failure rather than
    raising, so brokenness stays *undetermined* (→ ``BrokenCheckUnavailable``)
    instead of crashing the sweep or producing a false BROKEN.

    Caching, oracle selection, and strict-mode policy are left to the
    integration layer — see the SEAM NOTE above.
    """

    def _tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        try:
            from lawvm.finland.replay_entrypoint import replay_xml
            from lawvm.finland.replay_request import ReplayXmlRequest

            request = ReplayXmlRequest(
                parent_id=statute_id,
                mode="legal_pit",
                as_of=on.isoformat(),
                corpus=store,
                quiet=True,
            )
            result = replay_xml(request=request)
        except Exception:
            # Any failure to materialize → undetermined, fail-loud upstream.
            return None
        return _extract_materialized_tree(result)

    return _tree_as_of


def _extract_materialized_tree(result: object) -> Optional[IRNode]:
    """Pull the materialized IR tree out of a ReplayResult, defensively.

    The exact accessor path on ReplayResult/products is owned by the replay
    lane; this adapter reaches for the materialized point-in-time state and
    returns ``None`` if the shape is not as expected (fail-loud, no guess).
    """
    products = getattr(result, "products", None)
    state = getattr(products, "materialized_state", None)
    # The point-in-time IR tree lives on ``materialized_state.ir``. (``.tree`` is a
    # last-resort lxml parse of the ORIGINAL base XML, not the as-of IRNode, so it
    # must NOT be preferred — doing so made every target read back unavailable.)
    for attr in ("ir", "tree", "body", "root"):
        candidate = getattr(state, attr, None)
        if isinstance(candidate, IRNode):
            return candidate
    return None


def default_provision_present(tree: IRNode, ref: ProvisionRef) -> bool:
    """Default presence test: does ``ref`` resolve to a live node in ``tree``?

    Uses ``core.tree_ops.find_all`` to look up the section (and, when given,
    the subsection / item beneath it). A reference resolves as present iff every
    named structural level resolves to at least one node. Statute-only refs
    (no ``section_label``) are present iff the tree materialized at all.
    """
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.core.tree_ops import find_all

    if not ref.section_label:
        # Statute-level reference: the materialized tree IS the statute.
        return True

    section_paths = find_all(
        tree,
        IRNodeKind.SECTION.value,
        ref.section_label,
    )
    if not section_paths:
        return False

    if ref.subsection_num is None:
        return True

    sub_label = str(ref.subsection_num)
    sub_paths = find_all(
        tree,
        IRNodeKind.SUBSECTION.value,
        sub_label,
        scope_kind=IRNodeKind.SECTION.value,
        scope_label=ref.section_label,
    )
    if not sub_paths:
        return False

    if not ref.item_label:
        return True

    item_paths = find_all(
        tree,
        IRNodeKind.ITEM.value,
        ref.item_label,
        scope_kind=IRNodeKind.SUBSECTION.value,
        scope_label=sub_label,
    )
    return bool(item_paths)
