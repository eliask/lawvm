"""U.S. federal section-level dry-run kernel: the P7.5 gate before any replay.

This surface proves candidate amendatory operations against the USC annual-edition
oracle BEFORE any replay claim. It is the end-to-end Title 11 proof:

    before-edition (USC year Y) -> lower the window's Public Law(s) -> materialize
    candidate AFTER section text -> compare to the after-edition oracle (USC year
    Y+1).

It is deliberately narrow and honest:

- Granularity is SECTION-LEVEL. The comparison surface is one normalized
  statutory-text string per section address (``title``->``section``), exactly the
  oracle surface :func:`lawvm.us_federal.source_tree.iter_section_oracle_rows`
  yields. Sub-section structural redesignations that the section surface cannot
  faithfully represent are emitted as typed refusals, never wrong materializations.
- The oracle is a WITNESS, not ground truth (AGENTS.md §0/§9). When our
  materialized text disagrees with the oracle we NEVER silently repair our text to
  match it. The disagreement is carried as a typed residual with a disposition:
  ``lawvm_wrong`` (our op is wrong), ``oracle_suspect`` (the edition looks off), or
  ``missing_source`` (we failed to find/lower an amendment the oracle reflects).
- ``replay_authorized`` is ``False`` always. This is the dry-run gate; replay stays
  blocked here.

Two honest proofs are produced and projected onto the shared core objects:

1. **Mutation-boundary proof** (:mod:`lawvm.core.mutation_boundary_proof`): the
   oracle's actual changed-section set vs the section set our ops claim to touch.
   Each section is one mutation-boundary tree path ``(("title","11"),
   ("section","507"))``. Oracle-changed-but-not-claimed sections are the honest
   ``missing_source`` gap (we did not lower the amendment). Claimed-but-oracle-
   unchanged sections are ``lawvm_wrong``/``oracle_suspect``.
2. **Agreement residual surface** (:mod:`lawvm.core.agreement_residual`): for each
   claimed section, our materialized text vs the oracle after text (normalized).
   Agreement -> covered; disagreement -> typed residual with disposition.

3. **Witness-anchored north-star** (mirrors NZ ``dry_run_north_star``): the
   denominator is the oracle's changed-section count for the window — a fact of the
   source that does not move as lowering improves; the numerator is the sections we
   materialized in agreement. Monotone coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from enum import StrEnum
from typing import Any, Iterable, Mapping, assert_never

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    agreement_surface_from_residuals,
)
from lawvm.core.comparison_normalization import normalize_inline_comparison_text
from lawvm.core.branch_authority import PENDING_CONDITION_STATUS
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePath, tree_path_from_legal_address
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import (
    RULE_STRIKE_INSERT_TAIL,
    RULE_STRIKE_INSERT_THROUGH_TAIL,
    USAmendatoryReport,
    lower_plaw_amendatory,
)
from lawvm.us_federal.sources import (
    UsArchiveReader,
    read_plaw_locator,
    read_usc_annual,
)
from lawvm.us_federal.source_tree import (
    UscSection,
    UscSourceDocument,
    _SUBSECTION_PARSE_AMBIGUOUS,
    _USC_LADDER,
    _marker_interpretations,
    _normalize_text,
    _resolve_marker_level,
    parse_usc_title_document,
    split_statutory_subsections,
    strip_replacement_section_catchline,
    synthetic_usc_section,
)
from lawvm.us_federal.sunset import (
    DISPOSITION_SUNSET_REVERSION,
    US_SUNSET_REVERSION_RULE_ID,
    SunsetClassification,
    SunsetFinding,
    classify_sunset_reversion,
)

# Structural-marker scanner used when indexing a synthetic node payload: it finds
# markers anywhere in the text, unlike source_tree._MARKER_RE which is anchored to
# the start of a statutory paragraph.  Markers not preceded by non-whitespace are
# still boundary-safe; cross-references are filtered by level-aware parsing.
# A run-in head like ``(B)(i)`` or ``(ii)(I)`` has the child marker immediately
# follow a closing parenthesis; that parenthesis is also a valid marker boundary,
# so the scanner recognizes the nested marker without treating cross-references
# such as ``section 761(a)`` as structural children.
_BOUNDARY_MARKER_RE = re.compile(r"(?<![^\s)])\((?P<token>[0-9A-Za-z]+)\)")

# --- Stable rule-id vocabulary (agreement / residual / refusal). --------------

US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID = "us_dry_run_surface_not_replay_authorized"

# A requested window source (before/after edition or a PL blob) was absent from
# the archive: the window cannot be assembled, refused loudly (never partial).
US_DRY_RUN_WINDOW_SOURCE_MISSING_RULE_ID = "us_dry_run_window_source_not_in_archive"

# Section materialized-vs-oracle outcomes.
US_DRY_RUN_SECTION_AGREES_RULE_ID = "us_dry_run_section_materialized_text_matches_oracle"
# Our materialized text disagrees with the oracle after-text. Disposition decides
# whether it is our bug, an oracle pathology, or a missing lowering.
US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID = "us_dry_run_residual_materialized_text_mismatch_with_oracle"
# A claimed section that the oracle did not change at all (we touched a section the
# source did not). Never an agreement.
US_DRY_RUN_RESIDUAL_CLAIMED_BUT_ORACLE_UNCHANGED_RULE_ID = (
    "us_dry_run_residual_claimed_section_unchanged_in_oracle"
)
# The oracle changed a section we never claimed: the honest lowering gap.
US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID = (
    "us_dry_run_residual_oracle_changed_section_not_claimed"
)
# The op's match_text was not found in the before section text. We refuse rather
# than fuzzy-match into a guess.
US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID = "us_dry_run_residual_match_text_not_found_in_before_section"
# A sub-section-scoped op named a node (paragraph/clause/...) the before-section
# split does not expose, or whose text is no longer locatable in the running
# composition (an earlier op mutated it). We surface this as a typed residual
# rather than fall back to an unscoped whole-section string replace.
US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID = (
    "us_dry_run_residual_subsection_target_node_not_located_in_before_section"
)
# A structural-redesignation payload introduced a clause whose body is a bare
# prefix in the source XML (e.g., USLM quotedContent ended after "(i) any member"),
# while the oracle shows the full clause body.  The materialization is source-faithful;
# the gap is on the source/oracle side, so the residual is classified as oracle_suspect
# rather than lawvm_wrong.
US_DRY_RUN_RESIDUAL_SOURCE_TRUNCATED_PAYLOAD_RULE_ID = (
    "us_dry_run_residual_source_truncated_payload"
)
# The USC annual edition's source tree does not expose the structural level the
# amendment names: the target level is absent from the parsed section (e.g. a
# non-positive-law title that omits subsection/paragraph markers but renders
# deeper subparagraph/clause markers). The materialization cannot safely locate
# the node, and the gap is in the source comparison surface, so the residual is
# classified as a source-footing gap rather than a lawvm_wrong lowering bug.
US_DRY_RUN_RESIDUAL_TARGET_LEVEL_ABSENT_IN_SOURCE_TREE_RULE_ID = (
    "us_dry_run_residual_target_level_absent_in_source_tree"
)
# The USC annual edition's source tree exposes the structural level the
# amendment names, but the parsing of that section's markers is ambiguous
# (e.g. a section whose text precedes the first enumerated marker, or a marker
# that is genuinely ambiguous between levels). The materialization cannot safely
# locate a specific node without fabricating structure, so the residual is
# classified as a source-footing gap rather than a lawvm_wrong lowering bug.
US_DRY_RUN_RESIDUAL_SOURCE_TREE_PARSE_AMBIGUOUS_RULE_ID = (
    "us_dry_run_residual_source_tree_parse_ambiguous"
)
# The USC annual edition's source tree exposes the target's structural level
# deeper in the section, but an ancestor level named in the address is missing.
# An amendment targeting subsection (b)/paragraph (1) cannot be located when
# subsection (b) itself is not rendered in the source edition.  Like
# target_level_absent, this is a source-footing gap, not a lawvm_wrong bug.
US_DRY_RUN_RESIDUAL_TARGET_ANCESTOR_ABSENT_IN_SOURCE_TREE_RULE_ID = (
    "us_dry_run_residual_target_ancestor_absent_in_source_tree"
)

# Typed refusals (no materialization attempted / not representable at section level).
US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID = "us_dry_run_refused_target_outside_proof_title"
US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID = "us_dry_run_refused_target_section_not_present_in_before_edition"
US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID = (
    "us_dry_run_refused_structural_op_not_representable_at_section_granularity"
)
US_DRY_RUN_REFUSED_NO_TEXT_PATCH_RULE_ID = "us_dry_run_refused_text_op_missing_text_patch"
# A text-patch (TEXT_REPLACE / TEXT_REPEAL) or RENUMBER op whose target node — or,
# for a whole-section text strike, whose match anchor — is not present in the
# before/running edition of this window. The node was introduced by an un-lowered
# sibling op, removed/renamed by an earlier op, or is simply absent from this
# window's before-edition. Striking/relabelling a node that is not there is a NO-OP
# against the before text, not a wrong materialization: it is refused (the honest
# absent-target gap stays a visible typed refusal) rather than composed as a
# section-tanking divergence that would CORRUPT a sibling op's correct
# materialization of the same section. Mirrors the REPEAL absent-node refusal.
US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID = (
    "us_dry_run_refused_text_or_renumber_target_node_absent_in_before_edition"
)
# The instruction carries an effective date after the after-edition snapshot, so
# it is not yet in force for the dry-run window. It is skipped rather than applied
# as an immediate amendment, and surfaced as a visible typed refusal.
US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID = (
    "us_dry_run_deferred_op_not_yet_effective"
)

# Residual dispositions (AGENTS.md §0/§9). The oracle is a witness; a residual
# carries which side the gap is on, never a silent repair-to-oracle.
DISPOSITION_LAWVM_WRONG = "lawvm_wrong"
DISPOSITION_ORACLE_SUSPECT = "oracle_suspect"
DISPOSITION_MISSING_SOURCE = "missing_source"
# A changed section the amendment layer would call missing_source, but whose real
# mechanism is the expiry of a temporary provision reverting to the prior
# permanent form (F2). Carries a temporal witness; never repaired to the oracle.
# (``DISPOSITION_SUNSET_REVERSION`` is imported from :mod:`sunset`.)

# Mutation-boundary proof / agreement-surface identity constants.
_BOUNDARY_SURFACE = "us_dry_run_section_changed_set"
_AGREEMENT_SURFACE = "us_dry_run_section_text"
_OWNER_PHASE = "dry_run"
_BOUNDARY_SAFE_DEFAULT = "classify_section_change_boundary_without_authorizing_replay"
_BOUNDARY_FORBIDDEN_SHORTCUTS = (
    "dry_run_boundary_as_replay_authorization",
    "oracle_changed_set_as_source_truth",
)

# The OLRC consolidation sometimes editorially incorporates a future-effective
# amendment's text into the consolidation BEFORE its statutory effective date
# (an editorial pre-dating). LawVM correctly defers the op against the
# after-edition cutoff, so the section appears in oracle-changed-but-not-claimed
# (the honest ``missing_source`` shape). This is NOT a lowering gap: the
# amendment WAS lowered but refused on temporal grounds. Reclassify the residual
# from ``missing_source`` to ``oracle_suspect`` (OLRC editorial-on-the-oracle)
# so the finding type is honest and not inflated as a missing-amendment gap.
US_DRY_RUN_DEFERRED_OP_INFLATED_AS_MISSING_SOURCE_RULE_ID = (
    "us_dry_run_resdeferred_op_inflated_as_missing_source"
)

# Owned target-resolution recovery (AGENTS.md §0/§2.1; family
# ``target_resolution_recovery``). When the amendatory lowerer emits a bare-leaf
# sub-section target — e.g. ``title:10/section:2432/paragraph:1`` WITHOUT the
# parent ``subsection:b`` segment — the materializer's strict-equality matcher
# (``_locate_subsection_text``) refused the op as ``..._target_node_not_located``
# even when the targeted node was unambiguously present in the source-tree split.
# This is the suffix-match fallback: when strict equality fails, collect every
# source-tree node whose sub-section segments END with the target's segments; if
# EXACTLY ONE matches, resolve to it. Multiple matches refuse (§1.1: no silent
# target hijacking — the ambiguity stays visible as the existing typed residual).
#
# §0 ownership: the heuristic is named (this rule id), witness-anchored (the bare-
# leaf target address + the resolved node segments travel on
# :class:`USDryRunTargetRecovery`), strict-mode rejectable (the dry-run surface is
# ``replay_authorized=False`` always — the recovery cannot leak into replay; a
# reviewer can compare ``target_recoveries`` against the residual ledger to audit
# any agree that depended on a recovery), and pinned by a synthetic regression
# (the witnessing test). The source witness is the lowerer's bare-leaf address
# (parsed amendment prose naming a leaf sub-unit without its parent subsection).
US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID = (
    "us_dry_run_recovered_bare_leaf_target_via_unique_suffix_match"
)


def _norm(text: str) -> str:
    return normalize_inline_comparison_text(text)


# The OLRC consolidation strips the quotation marks USLM wraps a quoted-inserted
# statutory block in (the enacted text reads ``"(1) ...; "(2) ...``; the published
# Code reads ``(1) ...; (2) ...``) and adds courtesy spacing after the em-dash that
# introduces the inserted block. These are editorial normalizations on the oracle
# side, not lowering defects: when our composed text matches the oracle after this
# editorial projection (but NOT before it), the residual is ``oracle_suspect``, the
# generalized F1 class — we never repair our materialized text to the oracle.
# Both curly AND straight quote marks are folded: the enacted USLM amendment
# wraps inserted matter and defined terms in curly quotes (``‘CARES forbearance
# claim’``), while the OLRC consolidated Code re-renders them as straight quotes
# (``"CARES forbearance claim"``) or drops them. Equating quote *shape* across the
# two surfaces can never manufacture a false agreement between texts that differ in
# any non-quote character; it only undoes the OLRC's quote re-rendering.
_EDITORIAL_QUOTE_CHARS = "“”‘’„‚«»\"'"
# The OLRC re-spaces the boundary where a quoted block is spliced in: the enacted
# text wraps the inserted matter in quotes (``if—“(1) ...``) and, with the quotes
# stripped, the published Code inserts a courtesy space after the introductory
# dash/colon (``if— (1) ...``). Collapse that boundary space for classification.
_EDITORIAL_DASH_PAREN_SPACE_RE = re.compile(r"([—–:])\s+\(")
# OLRC insert-after courtesy space (F1, §507(d) and the comma-anchor generalization):
# the enacted instruction inserts matter directly after an anchor that ends in a
# closing ``)`` (``inserting "excluding …" after "(a)(8)"`` -> faithful
# ``(a)(8)excluding``) or a comma (``inserting "1182(1)," after "707(b),"`` ->
# faithful ``707(b),1182(1),``; ``inserting "Mount Vernon," after "Tacoma,"`` ->
# ``Tacoma,Mount Vernon,``), but the published Code adds a courtesy space after the
# anchor (``(a)(8) excluding`` / ``707(b), 1182(1),`` / ``Tacoma, Mount Vernon,``).
# The quotedText literal carries NO leading space (verified against the source
# bytes), so the materialization is faithful and the space is oracle editorial.
# Collapse a single space the oracle adds after a closing ``)`` or a ``,`` that is
# followed by a word/quote character. Applied to BOTH sides symmetrically: it only
# erases the difference when that anchor-adjacent space is the SOLE divergence — it
# never invents agreement between texts that differ in any other character.
_EDITORIAL_INSERT_AFTER_ANCHOR_SPACE_RE = re.compile(r"([),])\s+(?=[\w“”‘’\"'])")

# Detect a payload that opens with a new-section catchline (possibly after the USLM
# quotedContent wrapper's leading quote). Used to decide when a whole-section INSERT
# should project its own catchline off the body-only oracle surface.
_SECTION_CATCHLINE_RE = re.compile(
    r"^\s*(?:[\"“]\s*)?\[?\s*§+\s*"
    r"(?P<num>[0-9]+[A-Za-z]*(?:[-‐‑–][0-9]+[A-Za-z]*)?)\.\s*"
)


def _payload_section_number(payload_text: str) -> str | None:
    m = _SECTION_CATCHLINE_RE.match(payload_text or "")
    return m.group("num") if m is not None else None


def _norm_editorial(text: str) -> str:
    """Comparison projection that additionally undoes OLRC editorial splicing.

    Drops the quote marks the USLM amendment wraps inserted statutory matter in,
    the courtesy space the OLRC inserts after the introductory dash/colon of an
    inserted block, and the courtesy space it inserts after an insert-after anchor
    ending in ``)`` or ``,``. Used only to CLASSIFY a residual (lawvm_wrong vs
    oracle_suspect); never to repair the materialized text. A residual that vanishes
    under this projection but not under :func:`_norm` is editorial on the oracle
    side — the generalized F1 class.
    """
    stripped = text.translate({ord(ch): None for ch in _EDITORIAL_QUOTE_CHARS})
    respaced = _EDITORIAL_DASH_PAREN_SPACE_RE.sub(r"\1(", stripped)
    respaced = _EDITORIAL_INSERT_AFTER_ANCHOR_SPACE_RE.sub(r"\1", respaced)
    return _norm(respaced)


def _is_subsection_target(address: LegalAddress) -> bool:
    """True when the op target is deeper than ``section`` (paragraph/clause/...).

    A REPLACE/INSERT whose payload is a sub-section body cannot be faithfully
    materialized at the section-text surface (it would substitute the whole section
    with a fragment). Such ops are typed-refused, not wrong-materialized.
    """
    seen_section = False
    for kind, _label in address.path:
        if kind == "section":
            seen_section = True
            continue
        if seen_section and kind not in ("title",):
            return True
    return False


def _subsection_segments(address: LegalAddress) -> tuple[tuple[str, str], ...]:
    """The sub-section segments of an address (everything below ``section``)."""
    out: list[tuple[str, str]] = []
    seen_section = False
    for kind, label in address.path:
        if kind == "section":
            seen_section = True
            continue
        if seen_section and kind != "title":
            out.append((kind, label))
    return tuple(out)


def _source_tree_resolution_state(
    section: UscSection, address: LegalAddress
) -> tuple[bool, bool, bool]:
    """Returns ``(target_level_absent, target_ancestor_absent, parse_ambiguous)``.

    ``target_level_absent`` is true when the section exposes no node at the
    target's deepest structural level at all.  ``target_ancestor_absent`` is
    true when the target level occurs somewhere in the section, but no node has
    the target's ancestor sequence (e.g. an amendment targets ``paragraph:1`` of
    a ``subsection:b`` that is not rendered).  ``parse_ambiguous`` is true when
    the section's source-tree split emitted ambiguity findings, so locating a
    specific target node is unsafe even if the levels look present.
    """
    target_segments = _subsection_segments(address)
    if not target_segments:
        return False, False, False
    target_level = target_segments[-1][0]
    nodes, findings = split_statutory_subsections(section)
    node_segment_sets = {_subsection_segments(node.address) for node in nodes}
    has_target_level = any(
        _subsection_segments(node.address)[-1][0] == target_level
        for node in nodes
    )
    # A missing ancestor prefix is diagnosed before a missing node: even if the
    # target level exists elsewhere, the specific address anchor is absent.
    target_ancestor_absent = False
    for i in range(1, len(target_segments)):
        prefix = target_segments[:i]
        if prefix not in node_segment_sets:
            target_ancestor_absent = True
            break
    parse_ambiguous = any(
        f.get("rule_id") == _SUBSECTION_PARSE_AMBIGUOUS for f in findings
    )
    return (
        not has_target_level,
        target_ancestor_absent,
        parse_ambiguous,
    )


def _source_tree_gap_rule_for_address(
    section: UscSection, address: LegalAddress
) -> str | None:
    """Return a dedicated source-footing-gap rule id if the section's source tree
    cannot cleanly locate ``address``, otherwise ``None``.
    """
    level_absent, ancestor_absent, parse_ambiguous = _source_tree_resolution_state(
        section, address
    )
    if level_absent:
        return US_DRY_RUN_RESIDUAL_TARGET_LEVEL_ABSENT_IN_SOURCE_TREE_RULE_ID
    if ancestor_absent:
        return US_DRY_RUN_RESIDUAL_TARGET_ANCESTOR_ABSENT_IN_SOURCE_TREE_RULE_ID
    if parse_ambiguous:
        return US_DRY_RUN_RESIDUAL_SOURCE_TREE_PARSE_AMBIGUOUS_RULE_ID
    return None


# Conservative pattern for a structural clause introduced by a redesignation payload
# whose quotedContent was truncated by the USLM converter: a lowercase clause label
# directly after an em-dash/colon introducer, followed by a very short body (1-3
# words).  The oracle may show the same words plus a longer completing phrase.
_SOURCE_TRUNCATED_CLAUSE_RE = re.compile(
    r"(?P<intro>[—–:])\s*\((?P<label>[a-z]+)\)\s*(?P<body>(?:[^\s()]+\s*){1,3})"
)


def _has_source_truncated_clause_payload(materialized: str, oracle: str) -> bool:
    """Detect a clause body the source XML truncated during lowering.

    Some USLM redesignations wrap a new clause in quotedContent that ends after a
    bare noun phrase.  The oracle after-edition supplies the full clause body.  The
    materialization is source-faithful; the gap belongs to the source/oracle surface,
    so the residual should be oracle_suspect, not lawvm_wrong.

    This test is intentionally narrow: the clause must be introduced by a structural
    dash/colon, the materialized body must be a short prefix of the oracle body, and
    the oracle body must continue with substantial additional text.  It never
    modifies either text; it only guides classification.
    """
    for m in _SOURCE_TRUNCATED_CLAUSE_RE.finditer(materialized):
        label = m.group("label")
        body = m.group("body").strip()
        if not body or body.rstrip().endswith((".", ";")):
            continue
        # The oracle must have the same label and its body must start with the same
        # words and continue significantly.
        pattern = rf"[—–:]\s*\({re.escape(label)}\)\s*"
        for om in re.finditer(pattern, oracle):
            rest = oracle[om.end():]
            # Compare case-insensitively and quote-insensitively for the prefix.
            prefix_match = (
                rest.lower().startswith(body.lower())
                or _norm(body).lower() == _norm(rest[: len(body)]).lower()
            )
            if prefix_match:
                tail = rest[len(body):]
                # Require substantial oracle continuation that looks like completed
                # clause text, not just conjunctions or punctuation.
                trimmed_tail = tail.lstrip(" \t\n\r\u201c\u201d\"'")
                if len(trimmed_tail) >= 30 and not trimmed_tail[:8].lower().startswith(
                    ("and", "or")
                ):
                    return True
    return False

def _locate_subsection_text(
    section: UscSection | None, address: LegalAddress
) -> str | None:
    """Return the verbatim before-text of the sub-section node ``address`` names.

    Uses :func:`split_statutory_subsections` (the pinned USC address convention) to
    find the node whose address segments below the section match the op target.
    Returns ``None`` when the section is unavailable, the split flags the node as
    ambiguous (it is not emitted as a clean node), or no node matches — in which
    case the caller surfaces a typed residual rather than guessing a span. The node
    text is the leading enumerated paragraph plus its attached continuation lines,
    so substituting it inside the section text is faithful, not a fragment.

    When strict-equality fails, a suffix-match recovery (rule
    ``US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID``, family
    ``target_resolution_recovery`` — see :func:`_locate_subsection_text_resolved`)
    is attempted: if exactly one source-tree node ENDS with the target's sub-section
    segments, that node is returned. Multiple matches refuse (§1.1).
    """
    resolved = _locate_subsection_text_resolved(section, address)
    return resolved.text if resolved is not None else None


@dataclass(frozen=True)
class _ResolvedSubsectionNode:
    """Located sub-section node text plus the segments it actually resolved to.

    ``resolved_segments`` equals ``_subsection_segments(address)`` on a strict-
    equality match. When the suffix-match recovery fired, it carries the full
    segments of the resolved source-tree node (the recovered ancestor included),
    so callers can detect the recovery (``resolved_segments != target_segments``)
    and surface a witness (:class:`USDryRunTargetRecovery`).
    """

    text: str
    resolved_segments: tuple[tuple[str, str], ...]


def _locate_subsection_text_resolved(
    section: UscSection | None, address: LegalAddress
) -> _ResolvedSubsectionNode | None:
    """Locate ``address``'s node and report which segments it resolved to.

    :returns: ``None`` when no node matches OR the suffix match is ambiguous (more
        than one node ends with the target's segments — §1.1 no silent target
        hijacking, the caller takes its existing residual path); otherwise the
        node text and its full sub-section segments.
    """
    if section is None:
        return None
    target_segments = _subsection_segments(address)
    if not target_segments:
        return None
    nodes, _findings = split_statutory_subsections(section)
    for node in nodes:
        node_segments = _subsection_segments(node.address)
        if node_segments == target_segments:
            return _ResolvedSubsectionNode(text=node.text, resolved_segments=node_segments)
    # Suffix-match fallback (AGENTS.md §0 owned heuristic, family
    # ``target_resolution_recovery``). When the lowerer emitted a bare-leaf
    # address (no parent subsection prefix) and exactly one source-tree node ends
    # with the target's segments, recover that node. Multiple matches refuse
    # (§1.1 no silent target hijacking): the caller surfaces the existing typed
    # residual rather than guess.
    suffix_matches: list[_ResolvedSubsectionNode] = []
    for node in nodes:
        node_segments = _subsection_segments(node.address)
        if (
            len(node_segments) >= len(target_segments)
            and node_segments[-len(target_segments) :] == target_segments
        ):
            suffix_matches.append(
                _ResolvedSubsectionNode(text=node.text, resolved_segments=node_segments)
            )
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    return None


def _section_key_from_address(address: LegalAddress) -> tuple[str, str] | None:
    """Return ``(title, section)`` for a pinned USC address, or None.

    Sub-section segments are dropped: the section-level surface compares at the
    section address, so any deeper target resolves to its enclosing section.
    """
    title = ""
    section = ""
    for kind, label in address.path:
        if kind == "title":
            title = label
        elif kind == "section":
            section = label
    if not title or not section:
        return None
    return title, section


def _section_target_number(address: LegalAddress) -> str | None:
    """Return the ``section`` label of a pinned USC address (``"2196"``), or None."""
    key = _section_key_from_address(address)
    return key[1] if key is not None else None


def _section_tree_path(title: str, section: str) -> TreePath:
    return tree_path_from_legal_address(
        LegalAddress(path=(("title", title), ("section", section)))
    )


# ---------------------------------------------------------------------------
# Typed row + refusal carriers
# ---------------------------------------------------------------------------


class USDryRunRowStatus(StrEnum):
    """Closed set of per-section dry-run outcomes.

    A ``StrEnum`` so existing string consumers (JSON dict keys, test
    ``== "..."`` comparisons) keep working byte-for-byte while the value set is
    closed and dispatch can be made exhaustive.
    """

    AGREE = "agree"
    """Materialized text matches the oracle after-text."""

    RESIDUAL = "residual"
    """Materialized text diverges; ``disposition`` carries the witness-side gap."""


@dataclass(frozen=True)
class USDryRunSectionRow:
    """One claimed-section outcome: materialized text vs oracle after-text.

    ``row_status`` is a :class:`USDryRunRowStatus` (``agree`` or ``residual``);
    ``disposition`` is empty on agreement and carries the witness-side gap on a
    residual.
    """

    op_id: str
    action: str
    target_address: str
    section_key: str
    row_status: USDryRunRowStatus
    rule_id: str
    disposition: str = ""
    match_text: str = ""
    replacement: str = ""
    before_text: str = ""
    materialized_text: str = ""
    oracle_text: str = ""
    oracle_changed: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "action": self.action,
            "target_address": self.target_address,
            "section_key": self.section_key,
            "row_status": self.row_status,
            "rule_id": self.rule_id,
            "disposition": self.disposition,
            "match_text": self.match_text,
            "replacement": self.replacement,
            "materialized_text_len": len(self.materialized_text),
            "oracle_text_len": len(self.oracle_text),
            "oracle_changed": self.oracle_changed,
        }


@dataclass(frozen=True)
class USDryRunRefusal:
    """A typed refusal for one op (no materialization performed)."""

    op_id: str
    rule_id: str
    message: str
    target_address: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "rule_id": self.rule_id,
            "message": self.message,
            "target_address": self.target_address,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class USDryRunTargetRecovery:
    """One target-resolution recovery observation (AGENTS.md §0 typed emission).

    Witness-anchored audit row for the suffix-match fallback in
    :func:`_locate_subsection_text_resolved`/``_locate_subsection_text``: when the
    amendatory lowerer emitted a bare-leaf sub-section target (e.g.
    ``title:10/section:2432/paragraph:1`` WITHOUT the parent ``subsection:b``
    segment) and exactly one source-tree node ended with the target's segments,
    the materializer resolved to that node. The recovery is non-blocking
    (materialization proceeds) and non-authoritative (the dry-run is
    ``replay_authorized=False`` always); this row only makes the heuristic visible
    so a reviewer can audit any agreement that depended on a recovery.

    ``target_segments`` is what the lowerer emitted (carries the family witness:
    a sub-section segment with no parent subsection prefix). ``resolved_node_segments``
    is the source-tree node the materializer resolved to (carries the recovered
    ancestor).
    """

    op_id: str
    target_address: str
    target_segments: tuple[tuple[str, str], ...]
    resolved_node_segments: tuple[tuple[str, str], ...]
    family: str = "target_resolution_recovery"

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "rule_id": US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID,
            "target_address": self.target_address,
            "target_segments": [[k, v] for k, v in self.target_segments],
            "resolved_node_segments": [[k, v] for k, v in self.resolved_node_segments],
            "family": self.family,
        }


@dataclass(frozen=True)
class USDryRunReport:
    """Section-level dry-run report for one (before-edition, PL window) pair.

    ``replay_authorized`` is ``False`` always; this is the dry-run gate. The report
    carries the per-section rows, typed refusals, the changed-section mutation
    boundary, and the witness-anchored coverage north-star.
    """

    title: int
    before_year: str
    after_year: str
    statute_ids: tuple[str, ...]
    rows: tuple[USDryRunSectionRow, ...]
    refusals: tuple[USDryRunRefusal, ...]
    oracle_changed_sections: tuple[str, ...]
    claimed_sections: tuple[str, ...]
    boundary_proof: MutationBoundaryProof
    # F2: temporal sunset reclassification of otherwise-missing_source sections.
    # ``sunset_reversions`` maps a section_key ("11:109") to its temporal witness;
    # ``sunset_findings`` carries ambiguous temporal residuals (no reversion claim).
    sunset_reversions: tuple[SunsetClassification, ...] = ()
    sunset_findings: tuple[SunsetFinding, ...] = ()
    # Owned target-resolution recoveries (AGENTS.md §0 typed emission, family
    # ``target_resolution_recovery``): one row per op whose bare-leaf sub-section
    # target was resolved via the suffix-match fallback in
    # :func:`_locate_subsection_text`. Carries the witness (target + resolved
    # segments). Non-blocking and non-authoritative (the dry-run surface stays
    # ``replay_authorized=False``); surfaced for audit only.
    target_recoveries: tuple[USDryRunTargetRecovery, ...] = ()
    replay_authorized: bool = False

    def sunset_reversion_section_keys(self) -> frozenset[str]:
        return frozenset(f"{self.title}:{c.section}" for c in self.sunset_reversions)

    def deferred_op_section_keys(self) -> frozenset[str]:
        """Sections whose only on-target ops were deferred (future-effective).

        These are NOT missing_source: LawVM lowered the right amendment but the
        after-edition cutoff precedes its statutory effective date. If the OLRC's
        after-edition text already reflects the deferred amendment, the gap is
        OLRC editorial pre-dating, not a missing amendment.
        """
        keys: set[str] = set()
        for ref in self.refusals:
            if ref.rule_id != US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID:
                continue
            ta = ref.target_address
            if "/section:" not in ta:
                continue
            parts = ta.split("/", 2)
            # "title:N/section:S[/sub]" -> title=N, section=S
            title_val = ""
            sec_val = ""
            for p in parts[:3]:
                if p.startswith("title:"):
                    title_val = p[6:]
                elif p.startswith("section:"):
                    sec_val = p[8:].split("/", 1)[0]
                    break
            if title_val and sec_val:
                keys.add(f"{title_val}:{sec_val}")
        return frozenset(keys)

    # --- agreement / residual partitions -------------------------------------

    def agreeing_rows(self) -> tuple[USDryRunSectionRow, ...]:
        return tuple(r for r in self.rows if r.row_status is USDryRunRowStatus.AGREE)

    def residual_rows(self) -> tuple[USDryRunSectionRow, ...]:
        return tuple(r for r in self.rows if r.row_status is not USDryRunRowStatus.AGREE)

    def agreeing_sections(self) -> tuple[str, ...]:
        # Distinct sections materialized in agreement with the oracle after-text.
        return tuple(sorted({r.section_key for r in self.agreeing_rows()}))

    # --- witness-anchored north-star -----------------------------------------

    def north_star(self) -> dict[str, Any]:
        """Denominator = oracle changed-section count (a fact of the source);
        numerator = sections we materialized in agreement. Monotone coverage."""
        denom = len(self.oracle_changed_sections)
        # Only agreeing sections that are actually in the oracle changed set count
        # toward coverage (an agreement on an unchanged section is not progress).
        changed = set(self.oracle_changed_sections)
        numer = len({s for s in self.agreeing_sections() if s in changed})
        # A sunset reversion (F2) is an EXPLAINED change (the temporal layer owns
        # it), so it is not a source-footing gap — exclude it from missing_source.
        sunset_keys = self.sunset_reversion_section_keys()
        deferred_keys = self.deferred_op_section_keys()
        missing = tuple(
            sorted((changed - set(self.claimed_sections) - sunset_keys - deferred_keys))
        )
        sunset = tuple(sorted(changed & sunset_keys))
        deferred = tuple(sorted(changed & deferred_keys))
        return {
            "oracle_changed_section_count": denom,
            "sections_materialized_in_agreement": numer,
            "coverage_fraction": (numer / denom) if denom else None,
            "claimed_section_count": len(self.claimed_sections),
            "missing_source_sections": list(missing),
            "missing_source_section_count": len(missing),
            "sunset_reversion_sections": list(sunset),
            "sunset_reversion_section_count": len(sunset),
            "deferred_op_sections": list(deferred),
            "deferred_op_section_count": len(deferred),
        }

    def agreement_surface(self) -> dict[str, Any]:
        """Project per-section rows into a typed agreement surface (core reuse)."""
        residuals: list[AgreementResidual] = []
        for row in self.rows:
            match row.row_status:
                case USDryRunRowStatus.AGREE:
                    is_agreement = True
                case USDryRunRowStatus.RESIDUAL:
                    is_agreement = False
                case _ as unreachable:
                    assert_never(unreachable)
            if is_agreement:
                residuals.append(
                    AgreementResidual(
                        residual_id=f"us:{self.title}:{row.section_key}:{row.op_id}:agrees",
                        jurisdiction="us",
                        agreement_surface=_AGREEMENT_SURFACE,
                        family="agreement",
                        agreement_residual_status="agrees",
                        owner_phase=_OWNER_PHASE,
                        rule_id=row.rule_id,
                        source_artifact_id=row.op_id,
                        replay_count=1,
                        oracle_count=1,
                        safe_default="classify_dry_run_agreement_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "dry_run_agreement_as_replay_authorization",
                            "oracle_after_text_as_source_truth",
                        ),
                        detail={"target_address": row.target_address, "section_key": row.section_key},
                    )
                )
            else:
                residuals.append(
                    AgreementResidual(
                        residual_id=f"us:{self.title}:{row.section_key}:{row.op_id}:residual",
                        jurisdiction="us",
                        agreement_surface=_AGREEMENT_SURFACE,
                        family=_residual_family(row.disposition),
                        agreement_residual_status="residual",
                        owner_phase=_OWNER_PHASE,
                        rule_id=row.rule_id,
                        source_artifact_id=row.op_id,
                        replay_count=1,
                        oracle_count=1 if row.oracle_changed else 0,
                        safe_default="keep_dry_run_residual_visible_without_repairing_to_oracle",
                        forbidden_shortcuts=(
                            "dry_run_residual_repaired_to_oracle",
                            "oracle_after_text_as_source_truth",
                        ),
                        detail={
                            "target_address": row.target_address,
                            "section_key": row.section_key,
                            "disposition": row.disposition,
                        },
                    )
                )
        # The honest lowering gap: oracle changed a section we never claimed —
        # UNLESS the temporal layer reclassifies it as a sunset reversion (F2),
        # or a deferred-op refusal proves the amendment was lowered but refused on
        # temporal grounds (the OLRC editorially pre-dated a future-effective
        # amendment's text into the consolidation before its effective date).
        claimed = set(self.claimed_sections)
        sunset_keys = self.sunset_reversion_section_keys()
        sunset_by_key = {
            f"{self.title}:{c.section}": c for c in self.sunset_reversions
        }
        # Sections whose only on-target ops were deferred (the OLRC editorially
        # pre-dated their future-effective text into the consolidation before
        # the effective date). Not missing_source: reclassify to oracle_suspect.
        deferred_sections = self.deferred_op_section_keys()
        for section_key in self.oracle_changed_sections:
            if section_key in claimed:
                continue
            if section_key in sunset_keys:
                # F2: the change is the expiry of a temporary provision reverting
                # to the prior permanent form, not a missing amendment. Carry the
                # temporal witness; never repair to the oracle, replay stays off.
                witness = sunset_by_key[section_key].witness
                residuals.append(
                    AgreementResidual(
                        residual_id=f"us:{self.title}:{section_key}:sunset_reversion",
                        jurisdiction="us",
                        agreement_surface=_AGREEMENT_SURFACE,
                        family="temporal_mismatch",
                        agreement_residual_status="residual",
                        owner_phase=_OWNER_PHASE,
                        rule_id=US_SUNSET_REVERSION_RULE_ID,
                        source_artifact_id=f"{self.title}:{section_key}",
                        replay_count=0,
                        oracle_count=1,
                        safe_default="classify_sunset_reversion_without_authorizing_temporal_replay",
                        forbidden_shortcuts=(
                            "sunset_reversion_as_replay_authorization",
                            "oracle_after_text_as_source_truth",
                        ),
                        detail={
                            "section_key": section_key,
                            "disposition": DISPOSITION_SUNSET_REVERSION,
                            "sunset_date": witness.sunset_date,
                            "reverts_to_edition_year": witness.reverts_to_edition_year,
                            "note_head": witness.note_head,
                        },
                    )
                )
                continue
            if section_key in deferred_sections:
                # The amendment was lowered but the after-edition cutoff preceded
                # its statutory effective date; the OLRC editorially pre-dated the
                # amendment's text into the consolidation anyway. Not a missing
                # amendment — an editorial-on-the-oracle misclassification.
                residuals.append(
                    AgreementResidual(
                        residual_id=f"us:{self.title}:{section_key}:deferred_inflated",
                        jurisdiction="us",
                        agreement_surface=_AGREEMENT_SURFACE,
                        family="oracle_editorial_pathology",
                        agreement_residual_status="residual",
                        owner_phase=_OWNER_PHASE,
                        rule_id=US_DRY_RUN_DEFERRED_OP_INFLATED_AS_MISSING_SOURCE_RULE_ID,
                        source_artifact_id=f"{self.title}:{section_key}",
                        replay_count=0,
                        oracle_count=1,
                        safe_default="classify_deferred_op_as_oracle_suspect_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "deferred_op_as_missing_source_gap",
                            "oracle_after_text_as_source_truth",
                        ),
                        detail={"section_key": section_key, "disposition": DISPOSITION_ORACLE_SUSPECT},
                    )
                )
                continue
            residuals.append(
                AgreementResidual(
                    residual_id=f"us:{self.title}:{section_key}:missing_source",
                    jurisdiction="us",
                    agreement_surface=_AGREEMENT_SURFACE,
                    family="source_footing_gap",
                    agreement_residual_status="residual",
                    owner_phase=_OWNER_PHASE,
                    rule_id=US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
                    source_artifact_id=f"{self.title}:{section_key}",
                    replay_count=0,
                    oracle_count=1,
                    safe_default="keep_missing_source_gap_visible_without_inventing_an_op",
                    forbidden_shortcuts=(
                        "missing_source_gap_as_replay_bug",
                        "oracle_after_text_as_source_truth",
                    ),
                    detail={"section_key": section_key, "disposition": DISPOSITION_MISSING_SOURCE},
                )
            )
        denom = len(self.oracle_changed_sections)
        numer = len(
            {s for s in self.agreeing_sections() if s in set(self.oracle_changed_sections)}
        )
        surface = agreement_surface_from_residuals(
            tuple(residuals),
            jurisdiction="us",
            agreement_surface=_AGREEMENT_SURFACE,
            materialization_id=f"us_dry_run:title{self.title}:{self.before_year}->{self.after_year}",
            comparison_target_id=f"us_usc_after_edition:title{self.title}:{self.after_year}",
            comparison_kind="dry_run_after_section_text_vs_usc_annual_edition",
            materialization_kind="proposed_future_branch",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=(numer / denom) if denom else None,
        )
        return surface.to_dict()

    def summary(self) -> dict[str, Any]:
        agreeing = self.agreeing_rows()
        residual = self.residual_rows()
        return {
            "title": self.title,
            "before_year": self.before_year,
            "after_year": self.after_year,
            "statute_ids": list(self.statute_ids),
            "oracle_changed_section_count": len(self.oracle_changed_sections),
            "oracle_changed_sections": list(self.oracle_changed_sections),
            "claimed_section_count": len(self.claimed_sections),
            "claimed_sections": list(self.claimed_sections),
            "sections_dry_run": len(self.rows),
            "sections_refused": len(self.refusals),
            "section_agreements": len(agreeing),
            "section_residuals": len(residual),
            "residual_disposition_counts": _counts(r.disposition for r in residual),
            "refusal_rule_counts": _counts(r.rule_id for r in self.refusals),
            "sunset_reversion_count": len(self.sunset_reversions),
            "sunset_reversion_sections": [
                f"{self.title}:{c.section}" for c in self.sunset_reversions
            ],
            "sunset_finding_count": len(self.sunset_findings),
            "target_recovery_count": len(self.target_recoveries),
            "north_star": self.north_star(),
            "boundary_status": self.boundary_proof.boundary_proof_status,
            # Dry-run gate: replay stays blocked here.
            "replay_authorized": self.replay_authorized,
            "replay_claims": False,
            "dry_run_claims": True,
        }

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "us_federal",
            "report_kind": "dry_run_section_replay",
            "truth_claim": "dry_run_after_section_text_vs_usc_annual_edition_not_actual_replay",
            "replay_authorized": self.replay_authorized,
            "replay_claims": False,
            "dry_run_claims": True,
            "actual_replay_blocking_rule_id": US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["rows"] = [row.to_jsonable() for row in self.rows]
        payload["refusals"] = [refusal.to_jsonable() for refusal in self.refusals]
        payload["mutation_boundary_proof"] = self.boundary_proof.to_dict()
        payload["agreement_surface"] = self.agreement_surface()
        payload["sunset_reversions"] = [c.to_jsonable() for c in self.sunset_reversions]
        payload["sunset_findings"] = [f.to_jsonable() for f in self.sunset_findings]
        payload["target_recoveries"] = [r.to_jsonable() for r in self.target_recoveries]
        return payload


def _residual_family(disposition: str) -> AgreementResidualFamily:
    if disposition == DISPOSITION_LAWVM_WRONG:
        return "replay_bug"
    if disposition == DISPOSITION_ORACLE_SUSPECT:
        return "oracle_editorial_pathology"
    if disposition == DISPOSITION_MISSING_SOURCE:
        return "source_footing_gap"
    if disposition == DISPOSITION_SUNSET_REVERSION:
        return "temporal_mismatch"
    return "unknown"


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Materialization kernel (section-level surface)
# ---------------------------------------------------------------------------


def _refuse_absent_text_target(
    operation: LegalOperation,
    *,
    absent_kind: str,
    absent_text: str,
) -> USDryRunRefusal:
    """Refuse a text-patch / RENUMBER op whose target node (or anchor) is absent.

    Mirrors the REPEAL absent-node refusal: striking/relabelling a node — or
    striking a match anchor — that is not present in the before/running edition of
    this window is a NO-OP against the before text, not a wrong materialization.
    Refusing (rather than emitting a section-tanking ``lawvm_wrong`` residual) keeps
    the absent-target gap visible AND prevents this op from corrupting a sibling
    op's correct materialization of the same section. The offending absent text is
    embedded so the refusal is self-evidencing.
    """
    return USDryRunRefusal(
        op_id=operation.op_id,
        rule_id=US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID,
        message=(
            f"{operation.action.value} op target {str(operation.target)}: "
            f"{absent_kind} {absent_text!r} not present in the before/running edition "
            "(introduced by an un-lowered sibling op, removed by an earlier op, or "
            "absent from this window); refused, not composed"
        ),
        target_address=str(operation.target),
    )


def _index_node_text(
    node_text: str,
    base_segments: tuple[tuple[str, str], ...],
    node_overrides: NodeOverrides,
    *,
    as_root: bool = False,
) -> tuple[tuple[str, str], ...] | None:
    """Index ``node_text`` and its descendants into ``node_overrides``.

    Used after a sub-section node is created or mutated.  The first emitted marker
    in ``node_text`` is taken as the root.  When ``as_root=True``, ``base_segments``
    IS the address of that root node; descendants are stored relative to it.  When
    ``as_root=False``, ``base_segments`` is the PARENT prefix and the root node is
    appended to it (used for an inserted sibling whose kind is known only from the
    payload).

    Structural markers that are at the same level as the root (e.g. cross-reference
    ``paragraph (1)`` inside a replaced paragraph) are treated as prose, not as
    sibling units, so the root text and its true children are indexed correctly.

    Returns the full address segments of the indexed root node, or ``None`` when no
    structural markers are found.
    """
    if not node_text:
        return None

    text = _normalize_text(node_text)
    # Quote-stripped scan surface: inserted structural units are often wrapped in
    # curly or straight quotes (``except that—“(A) ...; “(B) ...``).  Replacing quotes
    # with spaces lets the boundary marker scanner see them while keeping the match
    # positions valid in the original ``text``.
    scan_surface = (
        text.replace('"', " ")
        .replace("“", " ")
        .replace("”", " ")
        .replace("‘", " ")
        .replace("’", " ")
    )
    root_match = _BOUNDARY_MARKER_RE.search(scan_surface)
    if root_match is None:
        # No structural marker: store as a leaf at the intended root address.
        intended_root = base_segments
        node_overrides[intended_root] = text
        return intended_root

    root_token = root_match.group("token")

    def _level_for_kind(kind: str) -> int:
        for i, ladder_kind in enumerate(_USC_LADDER):
            if ladder_kind == kind:
                return i
        return -1

    def _pick_level(token: str, stack: list[tuple[int, int]], expected: int | None, *, run_in_child: bool) -> tuple[int, int] | None:
        interps = _marker_interpretations(token)
        if not interps:
            return None
        if expected is not None:
            for lvl, ord_ in interps:
                if lvl == expected:
                    return lvl, ord_
            return None
        if len(interps) == 1:
            return interps[0]
        resolved = _resolve_marker_level(token, stack, run_in_child=run_in_child)
        return resolved

    if as_root:
        # The caller has named the root's address. Its level is the ladder position of
        # the last segment's kind; its label is the last segment's label. A payload that
        # opens with a different token is a child-first virtual-root payload.
        root_level = _level_for_kind(base_segments[-1][0]) if base_segments else -1
        root_level_expected = root_level
        root_token_expected = base_segments[-1][1] if base_segments else ""
        root_level_ord = _pick_level(root_token, [], root_level, run_in_child=False)
        use_virtual_root = (
            root_level < 0
            or root_token != root_token_expected
            or root_level_ord is None
            or root_level_ord[0] != root_level
        )
    else:
        # The caller has named the parent prefix. The first marker is the first child
        # of that parent, i.e. one level deeper than the parent's level.
        parent_level = _level_for_kind(base_segments[-1][0]) if base_segments else -1
        root_level_expected = parent_level + 1
        root_level_ord = _pick_level(root_token, [], root_level_expected, run_in_child=False)
        use_virtual_root = root_level_ord is None
    if use_virtual_root:
        root_level = root_level_expected
        root_ordinal = 0
        intended_root = base_segments
        stack: list[tuple[int, int]] = [(root_level, root_ordinal)]
        starts: list[int] = [0]
        spans: dict[tuple[tuple[str, str], ...], tuple[int, int]] = {(): (0, len(text))}
        prev_end = 0
        label_stack: list[str] = [""]
    else:
        if root_level_ord is None:
            # Cannot place the root marker; index the whole text as a leaf.
            intended_root = base_segments
            node_overrides[intended_root] = text
            return intended_root
        root_level, root_ordinal = root_level_ord

        if as_root:
            intended_root = base_segments
        else:
            kind = _USC_LADDER[root_level] if root_level < len(_USC_LADDER) else f"level{root_level}"
            intended_root = base_segments + ((kind, root_token),)

        # Stack of (level, ordinal) for the open chain of structural markers starting
        # from the root.  Only markers deeper than the root are structural children.
        stack = [(root_level, root_ordinal)]
        # Start of the text span for each stack entry.
        starts = [root_match.start()]
        # Spans indexed by path tuple relative to intended_root.
        spans: dict[tuple[tuple[str, str], ...], tuple[int, int]] = {(): (root_match.start(), len(text))}

        prev_end = root_match.end()

        label_stack = [root_token]

    def _path() -> tuple[tuple[str, str], ...]:
        return tuple(
            (_USC_LADDER[lvl] if lvl < len(_USC_LADDER) else f"level{lvl}", label)
            for (lvl, _), label in zip(stack[1:], label_stack[1:], strict=True)
        )

    for m in _BOUNDARY_MARKER_RE.finditer(scan_surface, 0 if use_virtual_root else root_match.end()):
        token = m.group("token")
        run_in_child = prev_end == m.start()
        resolved = _pick_level(token, stack, None, run_in_child=run_in_child)
        prev_end = m.end()
        if resolved is None:
            continue
        lvl, _ord = resolved
        if lvl <= root_level:
            # Same level as root or shallower: a cross-reference (e.g. ``(1)`` inside
            # a paragraph), not a structural child.
            continue
        # Pop structural ancestors at or deeper than this level.
        while stack and stack[-1][0] >= lvl:
            closed_path = _path() if len(stack) > 1 else ()
            if closed_path in spans:
                spans[closed_path] = (spans[closed_path][0], m.start())
            stack.pop()
            label_stack.pop()
            starts.pop()
        stack.append((lvl, _ord))
        label_stack.append(token)
        starts.append(m.start())
        opened_path = _path()
        spans[opened_path] = (m.start(), len(text))

    # Finalize any still-open structural markers to the end of the text.
    while len(stack) > 1:
        closed_path = _path()
        spans[closed_path] = (spans[closed_path][0], len(text))
        stack.pop()
        label_stack.pop()
        starts.pop()

    # Store the root node with the FULL text (not truncated by cross-references).
    node_overrides[intended_root] = text
    for rel_path, (start, end) in spans.items():
        key = intended_root + rel_path
        node_overrides[key] = _normalize_text(text[start:end])

    return intended_root


NodeOverrides = dict[tuple[tuple[str, str], ...], str]


def _refresh_ancestor_overrides(
    node_overrides: NodeOverrides,
    changed_key: tuple[tuple[str, str], ...],
    old_text: str,
    new_text: str,
    running: str,
) -> None:
    """Propagate a node-text change to every strict ancestor stored in ``node_overrides``.

    When a descendant is patched, its parent/grandparent entries become stale because
    they still contain the old descendant text.  This helper walks up the address path
    and substitutes the old child text for the new text inside each ancestor entry, as
    long as the resulting text is still present in ``running`` (the safety check guards
    against replacing the wrong occurrence when a substring is repeated).  If an ancestor
    cannot be refreshed safely, propagation stops there: higher ancestors are not
    updated, so they remain stale but no false fresh state is introduced.
    """
    if old_text == new_text:
        return
    current_old = old_text
    current_new = new_text
    for ancestor_len in range(len(changed_key) - 1, 0, -1):
        ancestor_key = changed_key[:ancestor_len]
        ancestor_text = node_overrides.get(ancestor_key)
        if ancestor_text is None:
            continue
        if current_old not in ancestor_text:
            break
        refreshed = ancestor_text.replace(current_old, current_new, 1)
        if refreshed == ancestor_text:
            break
        if refreshed not in running:
            # The substituted occurrence was not the child's unique span in the ancestor
            # (or the ancestor also spans unrelated text). Stop to avoid corrupting a parent.
            break
        node_overrides[ancestor_key] = refreshed
        current_old = ancestor_text
        current_new = refreshed


def _refresh_sibling_overrides(
    node_overrides: NodeOverrides,
    changed_key: tuple[tuple[str, str], ...],
    running: str,
) -> None:
    """Re-locate sibling node text in the post-patch running text.

    After a patch, ``_index_node_text`` re-indexes the patched node's subtree and
    ``_refresh_ancestor_overrides`` propagates up to strict ancestors. But SIBLING
    entries (same parent prefix, different leaf) are NOT refreshed — their stored
    text remains a substring of the pre-patch ``before_text``, and subsequent ops
    targeting those siblings fail at ``before_text.find(node_text)`` because the
    sibling's position shifted in the post-patch materialized text.

    This function walks every key in ``node_overrides`` that shares the same parent
    prefix as ``changed_key`` (same length, same prefix[:-1], different last segment)
    AND is not an ancestor or descendant of ``changed_key``. For each such sibling,
    it tries to find the sibling's CURRENT stored text in ``running`` (the post-patch
    materialized text). If the text is found at EXACTLY ONE position, the override is
    left as-is (it's already correct — the text didn't shift or the find confirms it).
    If the text is NOT found in ``running``, the sibling's stored text is stale and
    cannot be safely relocated — leave it stale and let the existing refusal fire
    (§1.1: no silent target hijacking; §0: preserve uncertainty rather than guess).

    The refresh is deliberately conservative: it only confirms that sibling text IS
    still present in the running text, which lets ``_running_node_text``'s
    ``current in running`` check pass. The actual position used by
    ``_apply_text_patch_to_target_subtree``'s ``before_text.find(node_text)`` is
    resolved at apply time against the live ``running`` text, so we don't need to
    store positional offsets — only the correct text.
    """
    if not changed_key:
        return
    parent_prefix = changed_key[:-1]
    parent_len = len(parent_prefix)
    for key in list(node_overrides):
        # Skip ancestors (shorter keys), descendants (longer keys with changed_key
        # as prefix), and the patched node itself.
        if len(key) <= parent_len:
            continue
        if key[:parent_len] != parent_prefix:
            continue
        if key == changed_key:
            continue
        # Same parent prefix, different leaf → SIBLING or COUSIN.
        # Only refresh direct siblings (same key length as changed_key).
        if len(key) != len(changed_key):
            continue
        sibling_text = node_overrides[key]
        if not sibling_text:
            continue
        # Check whether the sibling's stored text is still present in the
        # post-patch running text. If it is, the override is valid — the later
        # ``before_text.find(node_text)`` will succeed. If not, it's stale.
        # Per §1.1: do NOT try to re-locate by guessing; just leave it stale.
        count = running.count(sibling_text)
        if count == 0:
            # Stale: the sibling's text is no longer in the running text.
            # This can happen when the sibling's text was a substring of the
            # patched node's text (run-in heads / overlapping spans). Remove
            # the stale entry so the next op falls through to the pristine
            # re-split via _running_node_text, rather than using stale text.
            # Only remove if the sibling's text is short enough that it could
            # plausibly have been displaced by the patch (heuristic: < 200 chars).
            # Longer texts are unlikely to be fully displaced; leaving them
            # stale and letting the find() return -1 is the same refusal.
            if len(sibling_text) < 200:
                del node_overrides[key]


def _running_node_text(
    before_section: UscSection | None,
    address: LegalAddress,
    running: str,
    node_overrides: NodeOverrides | None,
    *,
    op_id: str = "",
    recoveries: list[USDryRunTargetRecovery] | None = None,
) -> str | None:
    """Return the targeted node's text AS IT CURRENTLY STANDS in ``running``.

    A single section is often amended by SEVERAL ops against ONE sub-section node —
    a multi-patch instruction ("in clause (ix), by striking 'X' and inserting 'Y',
    and by striking 'Z'") lowered to one op per patch, applied in source order. Each
    op must act on the node text the PRIOR op produced, not on the pristine before-
    edition node :func:`_locate_subsection_text` re-splits every call: once an earlier
    patch rewrote the node, its pristine located span is no longer present in
    ``running`` and a later patch would (wrongly) fail to locate.

    ``node_overrides`` records, per located node (keyed by its sub-section segments),
    the CURRENT text of that node inside ``running``. We consult it first: if a prior
    op already rewrote this node, the override IS the live node span; otherwise we
    locate the pristine node via the split. Either way we only return a span that is
    actually present in ``running`` (``None`` when the node is unexposed/absent, or a
    sibling mutation desynchronized the tracked span from the running text — the
    caller then takes its absent/residual path, never a guess).

    When the resolved source-tree node was located via the suffix-match recovery
    (``resolved_segments != target_segments``), an earlier op against the SAME
    resolved node from a DIFFERENT address path may have seeded the override under
    the resolved (full) segments key; consult that key for the live node text before
    falling back to the pristine before-edition span (which may already be stale).
    The recovery observation is emitted via ``recoveries`` (AGENTS.md §0 typed
    emission, family ``target_resolution_recovery``).
    """
    segments = _subsection_segments(address)
    if node_overrides is not None and segments in node_overrides:
        current = node_overrides[segments]
        if current in running:
            return current
        # Stale override: the stored text isn't an exact substring of the
        # running text (a sibling op's ancestor refresh shifted the descendant
        # text). Try a prefix-match fallback: the first 60 chars include the
        # leading enumerator marker and are unique enough to locate the node.
        # §0-safe: re-derive the text from the ACTUAL running text, not the
        # stale stored copy — the patch composes on the live span.
        prefix_len = min(60, len(current))
        prefix = current[:prefix_len]
        start = running.find(prefix)
        if start != -1:
            # Find the end of the node: scan forward to the next structural
            # marker at the same or shallower depth, or to the end of running.
            # For now, use the stored text's length as the upper bound (the
            # node's text length shouldn't change drastically from a sibling).
            end = min(start + len(current), len(running))
            return running[start:end]
        # Prefix also not found → fall through to pristine re-split
    resolved = _locate_subsection_text_resolved(before_section, address)
    if resolved is None:
        return None
    recovered = resolved.resolved_segments != segments
    if recovered:
        if recoveries is not None:
            recoveries.append(
                USDryRunTargetRecovery(
                    op_id=op_id,
                    target_address=str(address),
                    target_segments=segments,
                    resolved_node_segments=resolved.resolved_segments,
                )
            )
        # An earlier op against the same resolved node from a different address
        # path may have seeded the override under the resolved (full) segments
        # key. Use that live text when present (the pristine before-edition span
        # would be stale in the running composition).
        if (
            node_overrides is not None
            and resolved.resolved_segments in node_overrides
        ):
            candidate = node_overrides[resolved.resolved_segments]
            if candidate in running:
                return candidate
    return resolved.text if resolved.text in running else None


def _running_subtree_text(
    before_section: UscSection | None,
    address: LegalAddress,
    running: str,
    node_overrides: NodeOverrides | None,
    *,
    op_id: str = "",
    recoveries: list[USDryRunTargetRecovery] | None = None,
) -> str | None:
    """Return the target node and its current descendants as one contiguous span.

    Some USC paragraphs (e.g., ``(10) ... but—``) are split into a parent intro
    plus separate child nodes for ``(A), (B), (C)``.  An ``insert after paragraph
    (10)`` amendment must splice *after* the whole paragraph, not after the parent
    intro.  This helper unions the node's own span with every descendant span
    currently indexed under it.
    """
    segments = _subsection_segments(address)
    if not segments:
        return _running_node_text(
            before_section,
            address,
            running,
            node_overrides,
            op_id=op_id,
            recoveries=recoveries,
        )
    own: str | None = None
    if node_overrides is not None and segments in node_overrides:
        own = node_overrides[segments]
    if own is None:
        resolved = _locate_subsection_text_resolved(before_section, address)
        if resolved is not None:
            own = resolved.text
            if resolved.resolved_segments != segments:
                # Suffix-match recovery fired for the anchor. An earlier op may have
                # seeded the override under the resolved (full) segments key; use
                # that live text when present, and emit the recovery observation.
                if recoveries is not None:
                    recoveries.append(
                        USDryRunTargetRecovery(
                            op_id=op_id,
                            target_address=str(address),
                            target_segments=segments,
                            resolved_node_segments=resolved.resolved_segments,
                        )
                    )
                if (
                    node_overrides is not None
                    and resolved.resolved_segments in node_overrides
                ):
                    candidate = node_overrides[resolved.resolved_segments]
                    if candidate in running:
                        own = candidate
    if own is None or own not in running:
        return None
    spans: list[tuple[int, int]] = [(running.find(own), running.find(own) + len(own))]
    descendant_texts: set[str] = set()
    if node_overrides is not None:
        for key, text in node_overrides.items():
            if key == segments or key[: len(segments)] != segments:
                continue
            # Synthetic flush-block nodes have an empty label; they are structural
            # siblings, not descendants of the anchor node, so they must not be
            # included in the anchor's subtree span.
            if any(label == "" for _kind, label in key[len(segments) :]):
                continue
            descendant_texts.add(text)
    # When the node overrides are empty or stale (e.g. a direct unit test calling
    # ``_materialize_one`` without pre-seeding ``node_overrides``), fall back to
    # the before-edition split to discover the anchor's current descendants.
    if before_section is not None:
        for node in split_statutory_subsections(before_section)[0]:
            node_segments = _subsection_segments(node.address)
            if (
                len(node_segments) > len(segments)
                and node_segments[: len(segments)] == segments
                and not any(label == "" for _kind, label in node_segments[len(segments):])
            ):
                descendant_texts.add(node.text)
    for text in descendant_texts:
        if text not in running:
            continue
        start = running.find(text)
        spans.append((start, start + len(text)))
    if not spans:
        return None
    start = min(s[0] for s in spans)
    end = max(s[1] for s in spans)
    return running[start:end]


@lru_cache(maxsize=512)
def _word_boundary_pattern(match_text: str) -> re.Pattern[str]:
    """Compile and cache a word-boundary pattern for an alphabetic amendatory token.

    Per AGENTS.md §2.7: per-provision ``re.compile(rf\"(?<!\\w){re.escape(match_text)}(?!\\w)\")``
    calls dominated compile cost. The set of distinct quoted amendatory tokens is
    small and repeats heavily across ops (``'or'``, ``'and'``, ``'shall'`` ...),
    so a bounded LRU cache is sound.
    """
    return re.compile(rf"(?<!\w){re.escape(match_text)}(?!\w)")


def _token_in_text(text: str, match_text: str) -> bool:
    """True when ``text`` contains ``match_text`` as a standalone token.

    Alphabetic tokens use word-boundary matching (see
    :func:`_replace_token_in_text`).  Non-alphabetic tokens use literal
    substring presence.
    """
    if match_text.isalpha():
        return _word_boundary_pattern(match_text).search(text) is not None
    return match_text in text


def _token_count_in_text(text: str, match_text: str) -> int:
    """Count occurrences of ``match_text`` as a standalone token."""
    if match_text.isalpha():
        return len(_word_boundary_pattern(match_text).findall(text))
    return text.count(match_text)


def _replace_token_in_text(
    text: str,
    match_text: str,
    replacement: str,
    count: int,
    *,
    last_occurrence: bool = False,
) -> str:
    """Apply a text patch with word-boundary safety for alphabetic tokens.

    When a quoted amendatory token is a single alphabetic word (``'or'``,
    ``'and'``), a literal string replacement can maim unrelated words that happen
    to contain it (``order`` -> ``der``).  A word-boundary match is what the
    enacted quotation means: replace the standalone word, not a substring inside
    another word.

    Non-alphabetic tokens (``120``, ``15-year``, ``; or``) keep the existing
    literal semantics so phrase-shape patches continue to work unchanged.

    ``last_occurrence=True`` overrides the legacy ``count=-1`` ALL semantics
    (``str.replace(count=-1)`` and ``re.sub(count=0)`` both mean EVERY match —
    silently multiplying the op's effect across every period in a multi-sentence
    section). Set by ``_apply_text_patch_with_tail_dispatch`` when the op's
    ``TextSelector.occurrence_mode == "Last"`` (terminal-punct edits that name a
    single "the period at the end" anchor, not every period — AGENTS.md §0: no
    silent mutation beyond the target region).
    """
    if last_occurrence:
        if match_text.isalpha():
            pattern = _word_boundary_pattern(match_text)
            matches = list(pattern.finditer(text))
        else:
            matches = [
                (m.start(), m.end())
                for m in re.finditer(re.escape(match_text), text)
            ]
        if not matches:
            return text
        last_start, last_end = (
            (matches[-1].start(), matches[-1].end())
            if not isinstance(matches[-1], tuple)
            else matches[-1]
        )
        return text[:last_start] + (replacement or "") + text[last_end:]
    if match_text.isalpha():
        pattern = _word_boundary_pattern(match_text)
        new_text = pattern.sub(replacement or "", text, count=count if count != -1 else 0)
        return new_text
    return text.replace(match_text, replacement or "", count)


def _replace_token_tail_in_text(text: str, match_text: str, replacement: str, count: int) -> str:
    """Open-ended tail strike: replace from the anchor to the node end.

    A "striking 'X' and all that follows and inserting 'Y'" instruction deletes
    everything from the anchor to the end of the target node and inserts Y. We
    locate the anchor the same way :func:`_replace_token_in_text` does (word
    boundary for alphabetic tokens, literal otherwise) and drop the tail after it.

    The deletion always runs to the end of the node, so the LEFTMOST anchor's
    cut subsumes every later (rightward) anchor: there is exactly one meaningful
    cut point regardless of ``count``. An "each place" tail strike (``count ==
    -1``) is therefore identical to a first-occurrence tail strike — both cut at
    the leftmost anchor and append the replacement once. (Earlier code looped
    right-to-left rebuilding ``text`` from each anchor; that happened to land on
    the same leftmost result but read as a multi-occurrence rewrite, which a
    tail-to-end strike can never be.)
    """
    if not match_text:
        return text
    if match_text.isalpha():
        pattern = _word_boundary_pattern(match_text)
        starts = [m.start() for m in pattern.finditer(text)]
    else:
        starts = [m.start() for m in re.finditer(re.escape(match_text), text)]
    if not starts:
        return text
    return text[: starts[0]] + (replacement or "")


def _replace_token_through_in_text(
    text: str,
    start_text: str,
    end_text: str,
    replacement: str,
    count: int,
) -> str | None:
    """Bounded tail strike: delete from ``start_text`` THROUGH ``end_text``.

    Mirrors the open-ended tail strike, but stops the deletion at the END
    anchor (inclusive) instead of running to the end of the target node.
    Used for the "striking 'OLD' and all that follows through 'END' [and inserting
    'NEW']" family: the right-side text after END survives the op.

    The first (leftmost) ``start_text`` locates the deletion start; the
    first ``end_text`` occurrence strictly AFTER that start locates the
    inclusive right bound. Returns the patched text, or ``None`` when either
    anchor is absent or out of order (in which case the caller should refuse
    the op as an absent-target condition, not produce a wrong materialization).
    """
    if not start_text or not end_text:
        return text
    # Mirror _replace_token_tail_in_text's leftmost-start heuristic: use a
    # word-boundary pattern for alphabetic anchors (so "Definitions" doesn't
    # match inside "Definitions/foo"); use a literal find otherwise.
    if start_text.isalpha():
        start_pattern = _word_boundary_pattern(start_text)
        sm = start_pattern.search(text)
        if sm is None:
            return None
        start_pos = sm.start()
        after_start = sm.end()
    else:
        start_pos = text.find(start_text)
        if start_pos == -1:
            return None
        after_start = start_pos + len(start_text)
    end_start = text.find(end_text, after_start)
    if end_start == -1:
        return None
    end_pos = end_start + len(end_text)
    # Delete [start_text..end_text] inclusive, insert the replacement at the
    # START anchor's position, and PRESERVE the right-side text after END. The
    # earlier form dropped ``text[end_pos:]`` — silently destroying every byte
    # after the END anchor (forbidden by AGENTS.md §0 over-repeal). The
    # bounded deletion's whole point is that the inventoried text after END
    # survives the op.
    return text[:start_pos] + (replacement or "") + text[end_pos:]


def _apply_text_patch_with_tail_dispatch(
    operation: LegalOperation,
    text: str,
    *,
    match_text: str,
    replacement: str,
    count: int,
) -> str | None:
    """Dispatch a text patch by the operation's tail-provenance tag and selector.

    Routes the patch through one of three sibling functions:
      * THROUGH_TAIL — bounded [start..end] deletion + replacement
      * TAIL — open-ended deletion from start through end of node + replacement
      * regular — first/each occurrence replace

    Returns ``None`` when the THROUGH family cannot find its end anchor in the
    running text (the materializer should refuse, mirroring an absent-anchor
    refusal, rather than produce a wrong cut). For the TAIL/regular paths the
    existing helpers return the input text unchanged when anchors are missing
    (preserving their current behavior at section-level fallback sites).
    """
    if RULE_STRIKE_INSERT_THROUGH_TAIL in operation.provenance_tags:
        end_match = (
            operation.text_patch.selector.end_match_text
            if operation.text_patch is not None
            else None
        ) or ""
        return _replace_token_through_in_text(
            text, match_text, end_match, replacement, count
        )
    if RULE_STRIKE_INSERT_TAIL in operation.provenance_tags:
        return _replace_token_tail_in_text(text, match_text, replacement, count)
    last_occurrence = (
        operation.text_patch is not None
        and operation.text_patch.selector.occurrence_mode == "Last"
    )
    return _replace_token_in_text(
        text, match_text, replacement, count, last_occurrence=last_occurrence
    )


def _subtree_overrides(
    node_overrides: NodeOverrides,
    target_segments: tuple[tuple[str, str], ...],
) -> dict[tuple[tuple[str, str], ...], str]:
    """Return descendant node-override entries strictly under ``target_segments``."""
    return {
        key: text
        for key, text in node_overrides.items()
        if len(key) > len(target_segments) and key[: len(target_segments)] == target_segments
    }


def _apply_text_patch_to_target_subtree(
    before_text: str,
    *,
    target_segments: tuple[tuple[str, str], ...],
    match_text: str,
    replacement: str,
    count: int,
    node_overrides: NodeOverrides,
) -> str | None:
    """Apply a token-level text patch across the target node's subtree.

    The source may direct an edit at a container (e.g. ``paragraph (4)``) while
    the marked token (``120``) actually lives in deeper descendants (clause i of
    subparagraph (A), clause i of subparagraph (B)).  ``split_statutory_subsections``
    stores each descendant as its own node, so a single-container patch may not
    find the anchor in the immediate node text.

    When the immediate container does not carry the anchor, this helper locates
    every descendant node under the target that *does* contain the anchor, maps
    those nodes to their current spans in the running section text, and applies
    the patch within those spans only.  It never widens the edit outside the
    target subtree.

    Returns the materialized section text, or ``None`` when no descendant span
    contains the anchor (in which case the caller should treat the op as an
    absent anchor, not a wrong materialization).
    """
    subtree = _subtree_overrides(node_overrides, target_segments)
    if not subtree:
        return None

    # Locate every descendant node that currently carries the anchor and map it
    # to a concrete span in the running section text.
    hits: list[tuple[tuple[tuple[str, str], ...], int, int, str]] = []
    used: list[tuple[int, int]] = []
    candidates: list[
        tuple[tuple[tuple[str, str], ...], str]
    ] = sorted(
        ((key, text) for key, text in subtree.items() if _token_in_text(text, match_text)),
        key=lambda item: (len(item[0]), item[0]),
    )
    # Prefer the deepest (longest key) node at a given text position; run-in
    # heads appear as multiple ancestor/descendant keys sharing the same line,
    # and the deepest key is the most precise addressable unit.
    for key, node_text in reversed(candidates):
        start = before_text.find(node_text)
        if start == -1:
            # The stored node text may be stale after a sibling op patched the
            # parent (the ancestor refresh in _refresh_ancestor_overrides may
            # have introduced minor text shifts that make the full stored span
            # no longer an exact substring of the running text). Try a PREFIX
            # match: the first 60 chars of the node text (which include the
            # leading enumerator marker like "(A)" or "(1)") are unique enough
            # to locate the node. §0-safe: we only extend the span to the stored
            # text's length, never beyond — we're locating the same node, not
            # hijacking a sibling.
            prefix_len = min(60, len(node_text))
            prefix = node_text[:prefix_len]
            start = before_text.find(prefix)
            if start == -1:
                continue
            # Extend to the stored text's length (or to the next section
            # boundary if the running text is shorter — the node may have been
            # truncated by a sibling op's ancestor refresh).
            end = min(start + len(node_text), len(before_text))
        else:
            end = start + len(node_text)
        if any(start < u_end and end > u_start for u_start, u_end in used):
            continue
        used.append((start, end))
        # Use the ACTUAL running text at the located span, not the stored text —
        # this ensures the patch composes on the live text, not the stale copy.
        actual_node_text = before_text[start:end]
        hits.append((key, start, end, actual_node_text))
    if not hits:
        return None

    # Sort spans left-to-right so first-occurrence semantics follow reading order.
    hits.sort(key=lambda h: h[1])

    materialized = before_text
    if count == -1:
        # Process right-to-left so earlier span indices stay valid after edits.
        for key, start, end, node_text in sorted(hits, key=lambda h: h[1], reverse=True):
            new_node_text = _replace_token_in_text(node_text, match_text, replacement or "", -1)
            materialized = materialized[:start] + new_node_text + materialized[end:]
            node_overrides[key] = new_node_text
            # Keep run-in-duplicate ancestor/descendant keys consistent.
            for k in list(node_overrides):
                if (
                    len(k) > len(target_segments)
                    and k[: len(target_segments)] == target_segments
                    and node_overrides[k] == node_text
                ):
                    node_overrides[k] = new_node_text
        # Ancestor entries still contain the old descendant text; refresh them so a
        # later op that needs the parent span (e.g. add-at-end of the parent) can
        # locate it in the running text.
        for key, _start, _end, node_text in hits:
            _refresh_ancestor_overrides(
                node_overrides, key, node_text, node_overrides[key], materialized
            )
        return materialized

    # First-occurrence mode: replace the leftmost match that falls inside a
    # descendant span.  This mirrors the single-node behaviour where ``count==1``
    # consumes the first remaining match in reading order.
    for key, start, end, node_text in hits:
        # The leftmost standalone match inside this descendant node is what a
        # first-occurrence token-strike means.
        new_node_text = _replace_token_in_text(node_text, match_text, replacement or "", 1)
        if new_node_text == node_text:
            continue
        materialized = materialized[:start] + new_node_text + materialized[end:]
        node_overrides[key] = new_node_text
        for k in list(node_overrides):
            if (
                len(k) > len(target_segments)
                and k[: len(target_segments)] == target_segments
                and node_overrides[k] == node_text
            ):
                node_overrides[k] = new_node_text
        _refresh_ancestor_overrides(
            node_overrides, key, node_text, new_node_text, materialized
        )
        return materialized

    return None


def _materialize_one(
    operation: LegalOperation,
    before_text: str,
    *,
    before_section: UscSection | None = None,
    node_overrides: NodeOverrides | None = None,
    recoveries: list[USDryRunTargetRecovery] | None = None,
) -> tuple[str, str, str] | USDryRunRefusal:
    """Apply one op to a section's before-text -> (materialized, rule_id, disposition).

    Returns a typed refusal when the op cannot be faithfully represented at the
    section-text surface, or a residual row signal (via rule_id) when the
    match_text is not found. Never fuzzy-matches, never repairs to the oracle.

    ``before_section`` is the parsed before-edition :class:`UscSection`. When an op
    targets a sub-section node (paragraph/clause/...), it is used to materialize the
    op at SUB-SECTION granularity: the targeted node's text is located by the pinned
    USC address convention (:func:`split_statutory_subsections`) and the edit is
    confined to that node's span inside the running section text, then recomposed.
    This applies a sub-section payload to the right node instead of refusing it or
    string-replacing in the wrong place.

    ``node_overrides`` (mutated in place) carries the CURRENT text of each sub-section
    node a PRIOR op in this section's composition already rewrote, so several ops
    against the SAME node act on the running node text instead of re-locating the
    pristine (now-stale) before-edition node. Two SAME-anchor patches on one node
    each consume their OWN occurrence in source (left-to-right) order: patch 0 rewrites
    the leftmost match and records the result; patch 1 then operates on the post-patch
    node and rewrites the NEXT match. When the running node no longer carries the
    anchor (the prior identical patch already consumed the only/last occurrence, or a
    sibling op mutated it away), the op is REFUSED as an absent anchor — never composed
    as a wrong materialization, never collapsed into the prior patch's edit. See
    :func:`_running_node_text`.

    ``recoveries`` (mutated in place, optional) collects typed
    :class:`USDryRunTargetRecovery` observations when the suffix-match fallback in
    :func:`_locate_subsection_text` resolved a bare-leaf sub-section target to a
    unique source-tree node (AGENTS.md §0 typed emission, family
    ``target_resolution_recovery``). Non-blocking and non-authoritative — the
    dry-run surface stays ``replay_authorized=False`` always. ``None`` skips
    observation tracking (used by direct unit tests).
    """
    action = operation.action
    op_id = operation.op_id
    is_subsection = _is_subsection_target(operation.target)

    if action in (StructuralAction.TEXT_REPLACE, StructuralAction.TEXT_REPEAL):
        patch = operation.text_patch
        if patch is None:
            return USDryRunRefusal(
                op_id=op_id,
                rule_id=US_DRY_RUN_REFUSED_NO_TEXT_PATCH_RULE_ID,
                message="text op carries no text_patch; cannot materialize",
                target_address=str(operation.target),
            )
        match_text = patch.selector.match_text
        replacement = patch.replacement if patch.kind is TextPatchKindEnum.REPLACE else ""
        # occurrence: -1 (each place) -> replace all; 0 or 1 -> first occurrence.
        count = -1 if patch.selector.occurrence == -1 else 1

        if is_subsection:
            # Sub-section-scoped text patch: confine the match/replace to the
            # targeted node's text. This prevents striking an occurrence of the
            # anchor in the WRONG sub-section (a whole-section string replace would
            # hit the first occurrence anywhere). The node text is taken from the
            # RUNNING composition (via node_overrides) so a later patch against the
            # SAME node acts on the text a prior patch produced, not the now-stale
            # pristine before-edition node.
            node_text = _running_node_text(
                before_section,
                operation.target,
                before_text,
                node_overrides,
                op_id=op_id,
                recoveries=recoveries,
            )
            if node_text is not None:
                if not _token_in_text(node_text, match_text):
                    # The immediate container may not contain the anchor while deeper
                    # descendants do (e.g. "in paragraph (4)" but ``120`` only appears
                    # in subparagraph (A)/(B) clauses).  Apply the patch inside the
                    # target subtree without widening beyond it.
                    if node_overrides is not None:
                        subtree_materialized = _apply_text_patch_to_target_subtree(
                            before_text,
                            target_segments=_subsection_segments(operation.target),
                            match_text=match_text,
                            replacement=replacement or "",
                            count=count,
                            node_overrides=node_overrides,
                        )
                        if subtree_materialized is not None:
                            return (subtree_materialized, "", "")
                    # The anchor is not in the running node or its descendants. Either a
                    # prior IDENTICAL patch on this node already consumed the only/last
                    # occurrence (the dual-identical-patch tail), or a sibling op
                    # rewrote the node away from this anchor. Striking an anchor the
                    # running node no longer carries is a NO-OP against the live text, not
                    # a wrong materialization: REFUSE it (mirroring the absent-anchor
                    # refusal) so the section's already-applied sibling patches keep
                    # their correct composition instead of being tanked into a section-
                    # wide residual. Refusing (not collapsing onto the prior edit) is
                    # what keeps two identical patches from colliding on one occurrence.
                    return _refuse_absent_text_target(
                        operation, absent_kind="match anchor", absent_text=match_text
                    )
                # First-occurrence (count==1) or each-place (count==-1) replace inside
                # the RUNNING node text. First-occurrence always takes the LEFTMOST
                # remaining match: for two SAME-anchor patches on one node, patch 0
                # rewrites the leftmost match here and records the result in
                # node_overrides; patch 1 then sees the post-patch node and rewrites
                # the NEXT match — each consumes its own occurrence in source order.
                new_node_text = _apply_text_patch_with_tail_dispatch(
                    operation,
                    node_text,
                    match_text=match_text,
                    replacement=replacement or "",
                    count=count,
                )
                if new_node_text is None:
                    # Bounded through-tail: refuse when either anchor is absent from
                    # the running node, or when out of order; never fall through to a
                    # corrupt cut. The end anchor (or left anchor as a fallback) is the
                    # self-evidencing witness. (TAIL/regular helpers never return None,
                    # so reaching this refuse means the THROUGH family failed to find
                    # its end anchor in the running node text.)
                    end_match = (
                        operation.text_patch.selector.end_match_text
                        if operation.text_patch is not None
                        else None
                    ) or match_text
                    return _refuse_absent_text_target(
                        operation,
                        absent_kind="through end anchor",
                        absent_text=end_match,
                    )
                # Substitute the patched node text back into the running section text
                # (first occurrence — the node text is unique enough to anchor on).
                materialized = before_text.replace(node_text, new_node_text, 1)
                if node_overrides is not None:
                    # Re-index the patched node and any children it carries so later
                    # ops can still locate descendants of the mutated node.
                    target_segments = _subsection_segments(operation.target)
                    _index_node_text(
                        new_node_text,
                        target_segments,
                        node_overrides,
                        as_root=True,
                    )
                    _refresh_ancestor_overrides(
                        node_overrides,
                        target_segments,
                        old_text=node_text,
                        new_text=node_overrides[target_segments],
                        running=materialized,
                    )
                    _refresh_sibling_overrides(
                        node_overrides,
                        target_segments,
                        running=materialized,
                    )
                return (materialized, "", "")
            # Sub-section node not locatable (the split did not expose it cleanly).
            # Fall back to a section-level string replace when the anchor is
            # UNAMBIGUOUS — each-place, or a single occurrence in the section: a
            # precise match_text needs no node location. Only a multi-occurrence
            # anchor we cannot place stays a typed residual (genuinely ambiguous).
            if _token_in_text(before_text, match_text) and (
                count == -1 or _token_count_in_text(before_text, match_text) == 1
            ):
                materialized = _apply_text_patch_with_tail_dispatch(
                    operation,
                    before_text,
                    match_text=match_text,
                    replacement=replacement or "",
                    count=count,
                )
                if materialized is None:
                    # The THROUGH family's end anchor was not locatable in the
                    # running section text (the section-level fallback reached the
                    # function but the end anchor is absent or out of order). Refuse
                    # rather than silently fall back to a single-occurrence replace.
                    end_match = (
                        operation.text_patch.selector.end_match_text
                        if operation.text_patch is not None
                        else None
                    ) or match_text
                    return _refuse_absent_text_target(
                        operation, absent_kind="through end anchor", absent_text=end_match
                    )
                return (materialized, "", "")
            if not _token_in_text(before_text, match_text):
                # The targeted sub-section node is not locatable AND its match anchor
                # is absent from the whole section: the target node is not present in
                # this window's before edition. Refuse (mirroring REPEAL) rather than
                # tank the section's sibling ops with a divergent residual.
                return _refuse_absent_text_target(
                    operation, absent_kind="match anchor", absent_text=match_text
                )
            rule_id = US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
            disposition = DISPOSITION_LAWVM_WRONG
            if before_section is not None:
                gap_rule = _source_tree_gap_rule_for_address(
                    before_section, operation.target
                )
                if gap_rule is not None:
                    rule_id = gap_rule
                    disposition = DISPOSITION_MISSING_SOURCE
            return (
                "",
                rule_id,
                disposition,
            )

        if not _token_in_text(before_text, match_text):
            # The strike anchor is not literally present in the (whole) section: the
            # target node this op edits is absent from this window's before edition.
            # Never fuzzy-match. Refuse (mirroring REPEAL) so a sibling op's correct
            # materialization of the same section is not corrupted into an empty,
            # section-tanking residual.
            return _refuse_absent_text_target(
                operation, absent_kind="match anchor", absent_text=match_text
            )
        materialized = _apply_text_patch_with_tail_dispatch(
            operation,
            before_text,
            match_text=match_text,
            replacement=replacement or "",
            count=count,
        )
        if materialized is None:
            end_match = (
                operation.text_patch.selector.end_match_text
                if operation.text_patch is not None
                else None
            ) or match_text
            return _refuse_absent_text_target(
                operation, absent_kind="through end anchor", absent_text=end_match
            )
        return (materialized, "", "")

    if (
        action is StructuralAction.INSERT
        and operation.anchor is not None
        and _is_subsection_target(operation.anchor)
    ):
        # "inserting after paragraph (N) the following: <block>" — splice the
        # payload as a NEW node immediately after the anchor node's span. The
        # anchor (not the target) names the node to insert after. Gated on the
        # anchor being a SUB-SECTION node so an add-at-end op (anchored at the whole
        # section) still takes the append-at-section-end path below.
        payload_text = operation.payload.text if operation.payload is not None else ""
        if not payload_text:
            return USDryRunRefusal(
                op_id=op_id,
                rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
                message=f"insert-node-after op ({str(operation.anchor)}) has no payload",
                target_address=str(operation.anchor),
            )
        # Resolve against the RUNNING anchor subtree (via node_overrides) so an append
        # that follows an earlier text patch on the SAME node splices onto the text
        # that patch produced.  We use the full subtree (parent + descendants) because
        # a paragraph split into ``(10) ... but—`` plus ``(A)/(B)/(C)`` must receive
        # the insertion *after* subparagraph (C), not after the parent intro.
        anchor_text = _running_subtree_text(
            before_section,
            operation.anchor,
            before_text,
            node_overrides,
            op_id=op_id,
            recoveries=recoveries,
        )
        if anchor_text is None:
            rule_id = US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
            disposition = DISPOSITION_LAWVM_WRONG
            if before_section is not None:
                gap_rule = _source_tree_gap_rule_for_address(
                    before_section, operation.anchor
                )
                if gap_rule is not None:
                    rule_id = gap_rule
                    disposition = DISPOSITION_MISSING_SOURCE
            return (
                "",
                rule_id,
                disposition,
            )
        new_anchor_text = f"{anchor_text} {payload_text}".strip()
        materialized = before_text.replace(anchor_text, new_anchor_text, 1)
        if node_overrides is not None:
            anchor_segments = _subsection_segments(operation.anchor)
            node_overrides[anchor_segments] = new_anchor_text
            # When the inserted payload opens with a structural marker it is a new
            # structural unit. If the source adds the payload AT THE END of the
            # anchor node itself (target == anchor), the payload's markers are
            # children of the anchor (e.g. clauses (i)-(iii) under subparagraph
            # (B)). Otherwise this is an insert-after-anchor sibling, and the
            # payload's top-level units are children of the anchor's parent.
            if payload_text.lstrip().startswith("("):
                if str(operation.target) == str(operation.anchor):
                    _index_node_text(
                        new_anchor_text,
                        anchor_segments,
                        node_overrides,
                        as_root=True,
                    )
                else:
                    _index_node_text(
                        payload_text,
                        anchor_segments[:-1],
                        node_overrides,
                        as_root=False,
                    )
            _refresh_ancestor_overrides(
                node_overrides,
                anchor_segments,
                old_text=anchor_text,
                new_text=node_overrides[anchor_segments],
                running=materialized,
            )
        return (materialized, "", "")

    if action is StructuralAction.REPEAL and is_subsection:
        # "by striking subsection (X)" — remove the node's span from the section
        # text and recompose. Only at sub-section granularity; a whole-section repeal
        # (handled below) is not a text edit. The node may have been introduced by an
        # earlier op in this section's composition (e.g. an insert followed by a
        # conforming strike), so resolve against the running node state.
        node_text = _running_node_text(
            before_section,
            operation.target,
            before_text,
            node_overrides,
            op_id=op_id,
            recoveries=recoveries,
        )
        if node_text is None:
            # The struck node is not present — introduced by an un-lowered sibling/future
            # op or a conditional/sunset repeal of a not-yet-present node. Striking a
            # node that is not there is a NO-OP against the before text, not a wrong
            # materialization: refuse the op so the section's sibling ops keep their
            # correct composition.
            return USDryRunRefusal(
                op_id=op_id,
                rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
                message=(
                    f"strike-subsection target {str(operation.target)} not present in "
                    "the before/running edition (introduced by an un-lowered sibling op or a "
                    "conditional/sunset strike); not composed"
                ),
                target_address=str(operation.target),
            )
        materialized = before_text.replace(node_text, "", 1)
        # Collapse the double space the removed span may leave between neighbours.
        materialized = re.sub(r"\s{2,}", " ", materialized).strip()
        if node_overrides is not None:
            # Remove the repealed node and any indexed descendants from the live state.
            target_segments = _subsection_segments(operation.target)
            for key in list(node_overrides.keys()):
                if key[: len(target_segments)] == target_segments:
                    del node_overrides[key]
        return (materialized, "", "")

    if action in (StructuralAction.REPLACE, StructuralAction.INSERT):
        # Whole-node replace / add-at-end carry a structured payload node. At the
        # section-text surface we can only faithfully represent these when the
        # payload is plain text appended/substituted; a structural redesign of the
        # section body is NOT section-text representable -> typed refusal.
        if is_subsection:
            payload_text = operation.payload.text if operation.payload is not None else ""
            if not payload_text:
                return USDryRunRefusal(
                    op_id=op_id,
                    rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
                    message=(
                        f"{action.value} op targets sub-section node "
                        f"({str(operation.target)}) with no plain-text payload"
                    ),
                    target_address=str(operation.target),
                )
            # Resolve against the RUNNING node (via node_overrides): a node-scoped
            # REPLACE (amend-to-read) or INSERT (append) following an earlier patch on
            # the SAME node must act on the text that patch produced. Both are
            # non-destructive at the node boundary (the node is substituted or
            # appended-to, never deleted), so composing on the running node is the
            # faithful order. (The whole-node REPEAL strike and RENUMBER relabel stay
            # pristine-anchored and refuse a sibling-mutated node — deleting/relabelling
            # a node a prior patch transformed would be a wrong materialization.)
            node_text = _running_node_text(
                before_section, operation.target, before_text, node_overrides
            )
            if node_text is None:
                # We could not locate the targeted node in the running section (the
                # split did not expose it cleanly, or an earlier op moved it): a
                # sub-section payload cannot be applied blindly. Typed residual.
                rule_id = US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
                disposition = DISPOSITION_LAWVM_WRONG
                if before_section is not None:
                    gap_rule = _source_tree_gap_rule_for_address(
                        before_section, operation.target
                    )
                    if gap_rule is not None:
                        rule_id = gap_rule
                        disposition = DISPOSITION_MISSING_SOURCE
                return (
                    "",
                    rule_id,
                    disposition,
                )
            if action is StructuralAction.REPLACE:
                # amend-to-read of the sub-section node: the payload IS the node's
                # new body. Substitute it for the located node text.
                new_node_text = payload_text
            else:  # INSERT after a sub-section node -> append the payload to the node.
                new_node_text = f"{node_text} {payload_text}".strip()
            materialized = before_text.replace(node_text, new_node_text, 1)
            if node_overrides is not None:
                # Index the substituted/appended node and any descendants it carries
                # (e.g. a replaced paragraph with subparagraphs) so follow-on ops
                # targeting children or later inserted siblings can locate them.
                target_segments = _subsection_segments(operation.target)
                _index_node_text(
                    new_node_text,
                    target_segments,
                    node_overrides,
                    as_root=True,
                )
                _refresh_ancestor_overrides(
                    node_overrides,
                    target_segments,
                    old_text=node_text,
                    new_text=node_overrides[target_segments],
                    running=materialized,
                )
            return (materialized, "", "")
        if operation.payload is None or not operation.payload.text:
            return USDryRunRefusal(
                op_id=op_id,
                rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
                message=(
                    f"{action.value} op has no plain-text payload representable at "
                    "section-text granularity"
                ),
                target_address=str(operation.target),
            )
        if action is StructuralAction.INSERT:
            payload_text = operation.payload.text
            section_number = _section_target_number(operation.target)
            # A whole-new-section insert payload carries its own catchline. Project it
            # off the body-only oracle surface. First strip the leading wrapper
            # smart-quote (the USLM converter precedes "§ <num>." with ""); then
            # strip_replacement_section_catchline returns the body text starting AT
            # the body-marker "" (the USLM nested-quote opener the OLRC strips).
            if section_number is not None:
                candidate = payload_text
                if candidate and candidate[0] in "\"“":
                    after_quote = candidate[1:].lstrip()
                    if _payload_section_number(after_quote) == section_number:
                        candidate = after_quote
                stripped = strip_replacement_section_catchline(
                    candidate, section_number
                )
                if stripped is not None:
                    payload_text = stripped
            # After catchline strip (or for non-catchline inserts), strip the leading
            # wrapper/body-marker smart-quote (U+201C) the converter attaches to
            # quotedContent. The published USC never carries it; keeping it as
            # materialized text would mismatch the oracle without a substantive
            # statutory difference. The trailing U+201D is likewise stripped.
            if payload_text and payload_text[0] == "“":
                payload_text = payload_text[1:].lstrip()
            if payload_text and payload_text[-1] == "”":
                payload_text = payload_text[:-1].rstrip()
            materialized = f"{before_text} {payload_text}".strip()
            if node_overrides is not None and payload_text.lstrip().startswith("("):
                # Index the appended block's top-level units so later sub-section ops on
                # a freshly inserted section can locate them without re-splitting the
                # whole running text.
                _index_node_text(
                    payload_text, (), node_overrides, as_root=False
                )
        else:  # REPLACE whole node -> the payload IS the new section body.
            # An "amend ... to read as follows" payload opens with the section's
            # own ``§ <num>. <heading>`` catchline before the first quoted body
            # unit. The body-only oracle surface carries the catchline in the
            # heading, not the statutory text, so project it off the materialized
            # body to compare like-with-like (a surface projection, not a repair).
            payload_text = operation.payload.text
            section_number = _section_target_number(operation.target)
            if section_number is not None:
                stripped = strip_replacement_section_catchline(
                    payload_text, section_number
                )
                if stripped is not None:
                    payload_text = stripped
            materialized = payload_text
        return (materialized, "", "")

    if action is StructuralAction.RENUMBER and is_subsection and operation.destination is not None:
        # "redesignating paragraph (N) as paragraph (M)" — relabel ONLY the node's
        # leading enumerator inside its located span. The from-label appears in the
        # node text as its leading "(N)"; we rewrite that single leading enumerator,
        # never a cross-reference elsewhere in the section. When the node is not
        # cleanly locatable, a typed residual (never an unscoped global relabel).
        from_label = operation.target.leaf_label()
        to_label = operation.destination.leaf_label()
        resolved = _locate_subsection_text_resolved(before_section, operation.target)
        if resolved is not None and resolved.resolved_segments != _subsection_segments(
            operation.target
        ) and recoveries is not None:
            recoveries.append(
                USDryRunTargetRecovery(
                    op_id=op_id,
                    target_address=str(operation.target),
                    target_segments=_subsection_segments(operation.target),
                    resolved_node_segments=resolved.resolved_segments,
                )
            )
        node_text = resolved.text if resolved is not None else None
        if node_text is None or node_text not in before_text:
            # The from-node being redesignated is not present in this window's before
            # edition (introduced by an un-lowered sibling op, or already moved by an
            # earlier op). Relabelling an absent node is a NO-OP against the before
            # text: refuse (mirroring REPEAL) rather than tank the section's sibling
            # ops with a divergent residual that would corrupt the composition.
            return _refuse_absent_text_target(
                operation, absent_kind="redesignate-from node", absent_text=f"({from_label})"
            )
        lead = f"({from_label})"
        if not node_text.lstrip().startswith(lead):
            # The located node does not begin with the expected enumerator: refuse
            # to relabel rather than rewrite the wrong token.
            return (
                "",
                US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
                DISPOSITION_LAWVM_WRONG,
            )
        new_node_text = node_text.replace(lead, f"({to_label})", 1)
        materialized = before_text.replace(node_text, new_node_text, 1)
        return (materialized, "", "")

    # REPEAL / RENUMBER / HEADING_* are structural at the section level: a section
    # repeal becomes a repealed stub, a renumber moves a node. The section-text
    # surface cannot faithfully represent these as a text edit -> typed refusal.
    return USDryRunRefusal(
        op_id=op_id,
        rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
        message=f"{action.value} op is structural and not representable at section-text granularity",
        target_address=str(operation.target),
    )


def _oracle_changed_section_keys(
    before: UscSourceDocument,
    after: UscSourceDocument,
) -> tuple[str, ...]:
    """The set of section keys whose normalized statutory text changed.

    A fact of the source editions: the witness denominator for the window.
    """
    before_map = {s.section: s.statutory_text for s in before.sections}
    after_map = {s.section: s.statutory_text for s in after.sections}
    changed: list[str] = []
    for section in set(before_map) | set(after_map):
        if _norm(before_map.get(section, "")) != _norm(after_map.get(section, "")):
            changed.append(f"{before.title}:{section}")
    return tuple(sorted(changed, key=_section_sort_key))


def _section_sort_key(section_key: str) -> tuple[int, str, str]:
    title, _, section = section_key.partition(":")
    digits = "".join(ch for ch in section if ch.isdigit())
    return (int(digits) if digits else 0, section, title)


def build_us_dry_run(
    *,
    before_htm: bytes,
    after_htm: bytes,
    plaw_blobs: Mapping[str, bytes],
    title: int,
    before_year: str = "",
    after_year: str = "",
    enacted: str = "",
    prior_edition_htms: Mapping[str, bytes] | None = None,
) -> USDryRunReport:
    """Build the section-level dry-run report for one (before, after, PL) window.

    ``plaw_blobs`` maps a statute id (e.g. ``"PL 118-42"``) to its USLM XML bytes.
    Only ops whose resolved target is under ``title`` are materialized; off-title
    ops are typed-refused (never materialized into the wrong corpus).

    ``prior_edition_htms`` maps a year string to an EARLIER USC edition's title
    htm (years at or before ``before_year``). These editions are the prior-
    permanent reversion targets for the F2 sunset/temporal layer (channel a): a
    changed section the amendment layer would call ``missing_source`` is consulted
    against :mod:`lawvm.us_federal.sunset`, and reclassified ``sunset_reversion``
    when the after-text matches an earlier edition and/or a sunset note dates the
    expiry inside the window. No prior editions => channel (a) is unavailable, but
    the note-based channel (b) still fires.
    """
    after_cutoff: date | None = None
    if after_year.isdigit():
        after_cutoff = date(int(after_year), 12, 31)

    def _op_not_yet_in_force(op: LegalOperation) -> str | None:
        """Return a human reason if ``op`` should be skipped for temporal reasons."""
        if after_cutoff is None or op.source is None:
            return None
        if op.source.legal_status == PENDING_CONDITION_STATUS:
            return (
                "source effective date is conditional/pending and not demonstrably "
                f"in force as of after-edition cutoff {after_cutoff.isoformat()}"
            )
        effective = op.source.effective or ""
        if effective:
            try:
                op_effective = date.fromisoformat(effective)
            except ValueError:
                return None
            if op_effective > after_cutoff:
                return f"effective date {effective} is after after-edition cutoff {after_cutoff.isoformat()}"
        expires = op.source.expires or ""
        if expires:
            try:
                op_expires = date.fromisoformat(expires)
            except ValueError:
                return None
            # Source-side expiry is the *inclusive* last day in force. The dry-run
            # after-edition represents the end of the after-year, so if the provision
            # expired on or before that date it is no longer in force for the window.
            if op_expires <= after_cutoff:
                return f"expiry date {expires} is on or before after-edition cutoff {after_cutoff.isoformat()}"
        return None

    before_doc = parse_usc_title_document(before_htm, title=title, year=before_year)
    after_doc = parse_usc_title_document(after_htm, title=title, year=after_year)
    prior_docs: dict[str, UscSourceDocument] = {}
    for year, htm in (prior_edition_htms or {}).items():
        prior_docs[year] = parse_usc_title_document(htm, title=title, year=year)
    before_text_by_section = {s.section: s.statutory_text for s in before_doc.sections}
    before_section_by_number = {s.section: s for s in before_doc.sections}
    after_text_by_section = {s.section: s.statutory_text for s in after_doc.sections}

    oracle_changed = _oracle_changed_section_keys(before_doc, after_doc)

    rows: list[USDryRunSectionRow] = []
    refusals: list[USDryRunRefusal] = []
    target_recoveries: list[USDryRunTargetRecovery] = []
    claimed_sections: set[str] = set()

    # Phase 1: lower each Public Law, then route every op to its section's
    # materializing queue in ENACTMENT ORDER — sorted by enacted date, tie-broken
    # by statute id (so laws sharing an enacted date have a stable, total order).
    # Off-title ops are refused here;
    # section-existence checks are deferred until after Phase 1a so that a new
    # section created by one window law (a section-level INSERT) can be amended by
    # a later window law in the same window.
    section_ops: dict[str, list[LegalOperation]] = {}
    lowered_reports: list[tuple[str, str, bytes, USAmendatoryReport]] = []
    for statute_id, blob in plaw_blobs.items():
        report = lower_plaw_amendatory(
            blob, statute_id=statute_id, enacted=enacted, proof_title=str(title)
        )
        report_enacted = report.enacted or statute_id
        lowered_reports.append((report_enacted, statute_id, blob, report))
    lowered_reports.sort(key=lambda x: (x[0] or x[1], x[1]))
    for _enacted, statute_id, _blob, report in lowered_reports:
        for operation in report.operations():
            # Temporal guard: skip instructions that are not yet in force, or that
            # have already expired, relative to the after-edition snapshot. Both
            # source-side effective and expiry dates travel in OperationSource so the
            # dry-run window can refuse future/conditional provisions without silently
            # corrupting the comparison.
            temporal_reason = _op_not_yet_in_force(operation)
            if temporal_reason is not None:
                refusals.append(
                    USDryRunRefusal(
                        op_id=operation.op_id,
                        rule_id=US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID,
                        message=f"{temporal_reason}; not applied in this dry-run window",
                        target_address=str(operation.target),
                        detail={
                            "effective": operation.source.effective if operation.source else "",
                            "expires": operation.source.expires if operation.source else "",
                            "after_cutoff": after_cutoff.isoformat() if after_cutoff else "",
                            "statute_id": operation.source.statute_id if operation.source else "",
                        },
                    )
                )
                continue

            key = _section_key_from_address(operation.target)
            if key is None or int(key[0]) != int(title):
                refusals.append(
                    USDryRunRefusal(
                        op_id=operation.op_id,
                        rule_id=US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
                        message=(
                            f"op target {str(operation.target)!r} does not resolve under "
                            f"title {title}; out of proof scope"
                        ),
                        target_address=str(operation.target),
                    )
                )
                continue
            _op_title, section = key
            section_ops.setdefault(section, []).append(operation)

    # Phase 1a: Seed synthetic before-sections for sections created by window-level
    # INSERT ops. A new section has no before-edition paragraph structure, so later
    # sub-section ops (e.g. amend-to-read of paragraph (1)) cannot locate nodes.
    # We parse the initial INSERT payload into a best-effort UscSection so
    # subsequent ops can compose sub-section edits on the newly created section.
    for operations in section_ops.values():
        for op in operations:
            if op.action is not StructuralAction.INSERT:
                continue
            if op.payload is None or not op.payload.text:
                continue
            section_number = _payload_section_number(op.payload.text)
            if section_number is None:
                continue
            if section_number in before_section_by_number:
                continue
            payload_text = op.payload.text
            # Apply the same catchline-stripping projection the materializer uses so
            # the synthetic section's text matches the running text after the insert.
            candidate = payload_text
            if candidate and candidate[0] in "\"“":
                after_quote = candidate[1:].lstrip()
                if _payload_section_number(after_quote) == section_number:
                    candidate = after_quote
            stripped = strip_replacement_section_catchline(candidate, section_number)
            if stripped is not None:
                candidate = stripped
            # Drop the leading nested-quote marker so structural markers like ``(1)``
            # are recognized by paragraph splitting (the quote is not whitespace and
            # would otherwise hide the first ``(token)`` boundary).
            if candidate and candidate[0] in "\"“":
                candidate = candidate[1:].lstrip()
            before_section_by_number[section_number] = synthetic_usc_section(
                title=title,
                section=section_number,
                text=candidate,
            )
            # Ensure the before-text map also has an entry (empty: the materializer
            # will fill it from the insert).
            if section_number not in before_text_by_section:
                before_text_by_section[section_number] = ""

    # Phase 1b: Refuse ops on sections still absent from the before edition and not
    # created by a window-level INSERT.
    for section, operations in list(section_ops.items()):
        if section in before_section_by_number:
            continue
        for operation in operations:
            refusals.append(
                USDryRunRefusal(
                    op_id=operation.op_id,
                    rule_id=US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID,
                    message=(
                        f"target section {section!r} is not present in the before "
                        f"edition (title {title}, year {before_year}) and no INSERT "
                        f"in this window creates it"
                    ),
                    target_address=str(operation.target),
                    detail={"section": section},
                )
            )
        del section_ops[section]

    # Phase 2: compose each section's ops onto its before-text in source order, then
    # compare the composed result against the oracle once.
    for section, operations in section_ops.items():
        section_key = f"{title}:{section}"
        before_text = before_text_by_section.get(section, "")
        before_section = before_section_by_number.get(section)
        oracle_text = after_text_by_section.get(section, "")
        oracle_changed_here = _norm(before_text) != _norm(oracle_text)

        running = before_text
        op_ids: list[str] = []
        actions: list[str] = []
        match_texts: list[str] = []
        replacements: list[str] = []
        composed_refused = False
        residual_signal: tuple[str, str] | None = None
        # Tracks the CURRENT text of each sub-section node a prior op in this
        # section's composition rewrote, so several ops against the SAME node (a
        # multi-patch instruction, or two SAME-anchor patches on different
        # occurrences) act on the running node text, each consuming its own
        # occurrence in source order. See _running_node_text / _materialize_one.
        node_overrides: NodeOverrides = {}
        # Seed from the before-edition split for accurate first-op addresses; new
        # windows use the synthetic section created by a window-level INSERT.
        if before_section is not None:
            for node in split_statutory_subsections(before_section)[0]:
                node_overrides[_subsection_segments(node.address)] = node.text
        for operation in operations:
            outcome = _materialize_one(
                operation,
                running,
                before_section=before_section,
                node_overrides=node_overrides,
                recoveries=target_recoveries,
            )
            if isinstance(outcome, USDryRunRefusal):
                refusals.append(outcome)
                composed_refused = True
                continue
            materialized, signal_rule_id, signal_disposition = outcome
            op_ids.append(operation.op_id)
            actions.append(operation.action.value)
            patch = operation.text_patch
            match_texts.append(patch.selector.match_text if patch else "")
            replacements.append((patch.replacement or "") if patch else "")
            if signal_rule_id:
                # match_text-not-found against the running text: a residual for the
                # whole section (we refuse to fuzzy-match and cannot faithfully
                # continue composing past an anchor the source does not carry).
                residual_signal = (signal_rule_id, signal_disposition)
                break
            running = _normalize_text(materialized)

        if not op_ids and composed_refused:
            # Every op for this section was a typed refusal (e.g. all sub-section
            # structural redesigns): no section-text row is produced.
            continue

        claimed_sections.add(section_key)
        row_op_id = "+".join(op_ids) if op_ids else section_key
        row_action = "+".join(dict.fromkeys(actions)) if actions else ""
        row_match = " | ".join(m for m in match_texts if m)
        row_replacement = " | ".join(r for r in replacements if r)
        target_address = str(operations[0].target)

        if residual_signal is not None:
            rule_id, disposition = residual_signal
            rows.append(
                USDryRunSectionRow(
                    op_id=row_op_id,
                    action=row_action,
                    target_address=target_address,
                    section_key=section_key,
                    row_status=USDryRunRowStatus.RESIDUAL,
                    rule_id=rule_id,
                    disposition=disposition,
                    match_text=row_match,
                    replacement=row_replacement,
                    before_text=before_text,
                    materialized_text="",
                    oracle_text=oracle_text,
                    oracle_changed=oracle_changed_here,
                )
            )
            continue

        materialized = running
        if _norm(materialized) == _norm(oracle_text):
            rows.append(
                USDryRunSectionRow(
                    op_id=row_op_id,
                    action=row_action,
                    target_address=target_address,
                    section_key=section_key,
                    row_status=USDryRunRowStatus.AGREE,
                    rule_id=US_DRY_RUN_SECTION_AGREES_RULE_ID,
                    match_text=row_match,
                    replacement=row_replacement,
                    before_text=before_text,
                    materialized_text=materialized,
                    oracle_text=oracle_text,
                    oracle_changed=oracle_changed_here,
                )
            )
        elif _norm_editorial(materialized) == _norm_editorial(oracle_text):
            # The residual vanishes once the OLRC quote-stripping/spacing editorial
            # projection is applied: our materialization is faithful to the enacted
            # instruction; the gap is on the oracle's editorial side. Disposition
            # oracle_suspect (generalized F1). We do NOT repair our text to match.
            rows.append(
                USDryRunSectionRow(
                    op_id=row_op_id,
                    action=row_action,
                    target_address=target_address,
                    section_key=section_key,
                    row_status=USDryRunRowStatus.RESIDUAL,
                    rule_id=US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
                    disposition=DISPOSITION_ORACLE_SUSPECT,
                    match_text=row_match,
                    replacement=row_replacement,
                    before_text=before_text,
                    materialized_text=materialized,
                    oracle_text=oracle_text,
                    oracle_changed=oracle_changed_here,
                )
            )
        elif _has_source_truncated_clause_payload(materialized, oracle_text):
            # The source XML supplied a truncated structural redesignation payload
            # (e.g., a clause introduced as "(i) any member"); our materialization
            # faithfully reproduces that source, while the oracle shows the completed
            # clause body.  The gap is on the source/oracle surface, not the lowering.
            rows.append(
                USDryRunSectionRow(
                    op_id=row_op_id,
                    action=row_action,
                    target_address=target_address,
                    section_key=section_key,
                    row_status=USDryRunRowStatus.RESIDUAL,
                    rule_id=US_DRY_RUN_RESIDUAL_SOURCE_TRUNCATED_PAYLOAD_RULE_ID,
                    disposition=DISPOSITION_ORACLE_SUSPECT,
                    match_text=row_match,
                    replacement=row_replacement,
                    before_text=before_text,
                    materialized_text=materialized,
                    oracle_text=oracle_text,
                    oracle_changed=oracle_changed_here,
                )
            )
        else:
            # Our composed text disagrees with the oracle after-text. We do NOT
            # repair to the oracle. Disposition lawvm_wrong; the rule id
            # distinguishes "claimed a section the oracle never changed" from
            # "materialization wrong vs a genuine oracle change".
            if not oracle_changed_here:
                rule_id = US_DRY_RUN_RESIDUAL_CLAIMED_BUT_ORACLE_UNCHANGED_RULE_ID
            else:
                rule_id = US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
            rows.append(
                USDryRunSectionRow(
                    op_id=row_op_id,
                    action=row_action,
                    target_address=target_address,
                    section_key=section_key,
                    row_status=USDryRunRowStatus.RESIDUAL,
                    rule_id=rule_id,
                    disposition=DISPOSITION_LAWVM_WRONG,
                    match_text=row_match,
                    replacement=row_replacement,
                    before_text=before_text,
                    materialized_text=materialized,
                    oracle_text=oracle_text,
                    oracle_changed=oracle_changed_here,
                )
            )

    claimed_tuple = tuple(sorted(claimed_sections, key=_section_sort_key))

    # F2: for each oracle-changed section the kernel did NOT claim (it would be a
    # missing_source gap), consult the temporal/sunset detector before settling on
    # missing_source. A proven reversion reclassifies to sunset_reversion with a
    # temporal witness; an ambiguous temporal note becomes a typed finding (still
    # missing_source). The detector never repairs to the oracle.
    sunset_reversions, sunset_findings = _detect_sunsets(
        title=title,
        before_year=before_year,
        after_year=after_year,
        before_doc=before_doc,
        after_doc=after_doc,
        prior_docs=prior_docs,
        oracle_changed=oracle_changed,
        claimed=set(claimed_tuple),
    )

    boundary = _build_boundary_proof(
        title=title,
        oracle_changed=oracle_changed,
        claimed=claimed_tuple,
    )

    return USDryRunReport(
        title=title,
        before_year=before_year,
        after_year=after_year,
        statute_ids=tuple(sorted(plaw_blobs)),
        rows=tuple(rows),
        refusals=tuple(refusals),
        oracle_changed_sections=oracle_changed,
        claimed_sections=claimed_tuple,
        boundary_proof=boundary,
        sunset_reversions=sunset_reversions,
        sunset_findings=sunset_findings,
        target_recoveries=tuple(target_recoveries),
    )


def _detect_sunsets(
    *,
    title: int,
    before_year: str,
    after_year: str,
    before_doc: UscSourceDocument,
    after_doc: UscSourceDocument,
    prior_docs: Mapping[str, UscSourceDocument],
    oracle_changed: tuple[str, ...],
    claimed: set[str],
) -> tuple[tuple[SunsetClassification, ...], tuple[SunsetFinding, ...]]:
    """Run the sunset detector over the unclaimed oracle-changed sections."""
    before_text_by_section = {s.section: s.statutory_text for s in before_doc.sections}
    after_by_section = {s.section: s for s in after_doc.sections}

    reversions: list[SunsetClassification] = []
    findings: list[SunsetFinding] = []
    for section_key in oracle_changed:
        if section_key in claimed:
            continue
        _t, _, section = section_key.partition(":")
        after_section = after_by_section.get(section)
        if after_section is None:
            # The section vanished from the after edition: not a text reversion.
            continue
        prior_texts: dict[str, str] = {}
        for year, doc in prior_docs.items():
            ps = doc.section_by_number(section)
            if ps is not None:
                prior_texts[year] = ps.statutory_text
        result = classify_sunset_reversion(
            title=title,
            section=section,
            before_year=before_year,
            after_year=after_year,
            before_text=before_text_by_section.get(section, ""),
            after_text=after_section.statutory_text,
            after_section=after_section,
            prior_edition_texts=prior_texts,
        )
        if result.classification is not None:
            reversions.append(result.classification)
        elif result.finding is not None:
            findings.append(result.finding)
    return tuple(reversions), tuple(findings)


def _build_boundary_proof(
    *,
    title: int,
    oracle_changed: tuple[str, ...],
    claimed: tuple[str, ...],
) -> MutationBoundaryProof:
    """Prove the claimed changed-section set against the oracle's actual one.

    Each section is one mutation-boundary tree path. ``changed_paths`` is the
    oracle's actual changed-section set (the witness); ``selected_target_paths`` is
    the section set our ops claim to touch. Covered = the intersection;
    unexplained = oracle-changed-but-not-claimed (the honest ``missing_source``
    gap). When the two sets match exactly the boundary is ``proved``; otherwise it
    is ``unresolved`` (a missing lowering surfaces here, never hidden).
    """
    changed_set = set(oracle_changed)
    claimed_set = set(claimed)

    def _paths(section_keys: Iterable[str]) -> tuple[TreePath, ...]:
        out: list[TreePath] = []
        for section_key in section_keys:
            sk_title, _, section = section_key.partition(":")
            out.append(_section_tree_path(sk_title or str(title), section))
        return tuple(out)

    changed_paths = _paths(sorted(changed_set, key=_section_sort_key))
    selected_paths = _paths(claimed)
    covered = sorted(changed_set & claimed_set, key=_section_sort_key)
    unexplained = sorted(changed_set - claimed_set, key=_section_sort_key)
    # Claimed sections the oracle did not change are an over-claim: surface them in
    # detail (they are not part of the oracle changed set, so not "changed_paths").
    over_claimed = sorted(claimed_set - changed_set, key=_section_sort_key)

    boundary_holds = not unexplained and not over_claimed
    status = "proved" if boundary_holds else "unresolved"
    result_codes = () if boundary_holds else ("REPLAY_APPLY_BOUNDARY_UNRESOLVED",)

    return MutationBoundaryProof(
        proof_id=f"us_dry_run:title{title}:changed_set",
        jurisdiction="us",
        materialization_surface=_BOUNDARY_SURFACE,
        operation_id=f"us_dry_run_title{title}_window",
        owner_phase=_OWNER_PHASE,
        rule_id=(
            "us_dry_run_changed_section_set_matches_oracle"
            if boundary_holds
            else "us_dry_run_changed_section_set_diverges_from_oracle"
        ),
        boundary_proof_status=status,
        outcome="changed_section_set_boundary",
        selected_target_paths=selected_paths,
        changed_paths=changed_paths,
        covered_changed_paths=_paths(covered),
        unexplained_changed_paths=_paths(unexplained),
        result_codes=result_codes,
        path_set_invariant_holds=boundary_holds,
        safe_default=_BOUNDARY_SAFE_DEFAULT,
        forbidden_shortcuts=_BOUNDARY_FORBIDDEN_SHORTCUTS,
        detail={
            "oracle_changed_sections": list(oracle_changed),
            "claimed_sections": list(claimed),
            "missing_source_sections": list(unexplained),
            "over_claimed_sections": list(over_claimed),
        },
    )


# ---------------------------------------------------------------------------
# Archive-backed window assembly
# ---------------------------------------------------------------------------


class USDryRunWindowError(ValueError):
    """A requested dry-run window source was not present in the archive."""

    def __init__(self, rule_id: str, message: str) -> None:
        super().__init__(message)
        self.rule_id = rule_id


def build_us_dry_run_from_archive(
    archive: UsArchiveReader,
    *,
    title: int,
    before_year: int,
    after_year: int,
    plaw_locators: Mapping[str, str],
    enacted: str = "",
    prior_edition_years: tuple[int, ...] = (),
) -> USDryRunReport:
    """Assemble and run the dry-run for one window directly from the archive.

    ``plaw_locators`` maps a statute id (e.g. ``"PL 118-42"``) to its canonical
    ``us://plaw/...`` locator. Every requested source (both editions and each PL)
    must be present; a missing source raises :class:`USDryRunWindowError` rather
    than running a silently-partial window.

    ``prior_edition_years`` are earlier USC edition years (at or before
    ``before_year``) loaded as prior-permanent reversion targets for the F2 sunset
    layer (channel a). Years not present in the archive are skipped silently — they
    only strengthen channel (a); the note-based channel (b) does not depend on
    them. Required window sources (the before/after editions and the PLs) still
    raise loudly when absent.
    """
    before = read_usc_annual(archive, before_year, title)
    if before is None:
        raise USDryRunWindowError(
            US_DRY_RUN_WINDOW_SOURCE_MISSING_RULE_ID,
            f"before edition us://usc/{before_year}/title{title}.htm not in archive",
        )
    after = read_usc_annual(archive, after_year, title)
    if after is None:
        raise USDryRunWindowError(
            US_DRY_RUN_WINDOW_SOURCE_MISSING_RULE_ID,
            f"after edition us://usc/{after_year}/title{title}.htm not in archive",
        )

    plaw_blobs: dict[str, bytes] = {}
    for statute_id, locator in plaw_locators.items():
        blob = read_plaw_locator(archive, locator)
        if blob is None:
            raise USDryRunWindowError(
                US_DRY_RUN_WINDOW_SOURCE_MISSING_RULE_ID,
                f"public law {statute_id} ({locator}) not in archive",
            )
        plaw_blobs[statute_id] = blob

    prior_edition_htms: dict[str, bytes] = {}
    for year in prior_edition_years:
        if year > before_year:
            continue
        prior = read_usc_annual(archive, year, title)
        if prior is not None:
            prior_edition_htms[str(year)] = prior

    return build_us_dry_run(
        before_htm=before,
        after_htm=after,
        plaw_blobs=plaw_blobs,
        title=title,
        before_year=str(before_year),
        after_year=str(after_year),
        enacted=enacted,
        prior_edition_htms=prior_edition_htms,
    )
