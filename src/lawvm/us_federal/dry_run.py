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
from typing import Any, Mapping

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    agreement_surface_from_residuals,
)
from lawvm.core.comparison_normalization import normalize_inline_comparison_text
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePath, tree_path_from_legal_address
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import lower_plaw_amendatory
from lawvm.us_federal.sources import (
    UsArchiveReader,
    read_plaw_locator,
    read_usc_annual,
)
from lawvm.us_federal.source_tree import (
    UscSection,
    UscSourceDocument,
    parse_usc_title_document,
    split_statutory_subsections,
)
from lawvm.us_federal.sunset import (
    DISPOSITION_SUNSET_REVERSION,
    US_SUNSET_REVERSION_RULE_ID,
    SunsetClassification,
    SunsetFinding,
    classify_sunset_reversion,
)

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

# Typed refusals (no materialization attempted / not representable at section level).
US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID = "us_dry_run_refused_target_outside_proof_title"
US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID = "us_dry_run_refused_target_section_not_present_in_before_edition"
US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID = (
    "us_dry_run_refused_structural_op_not_representable_at_section_granularity"
)
US_DRY_RUN_REFUSED_NO_TEXT_PATCH_RULE_ID = "us_dry_run_refused_text_op_missing_text_patch"

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
            return node.text
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


def _section_tree_path(title: str, section: str) -> TreePath:
    return tree_path_from_legal_address(
        LegalAddress(path=(("title", title), ("section", section)))
    )


# ---------------------------------------------------------------------------
# Typed row + refusal carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class USDryRunSectionRow:
    """One claimed-section outcome: materialized text vs oracle after-text.

    ``status`` is ``agree`` or ``residual``; ``disposition`` is empty on agreement
    and carries the witness-side gap on a residual.
    """

    op_id: str
    action: str
    target_address: str
    section_key: str
    status: str
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
            "status": self.status,
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
    replay_authorized: bool = False

    def sunset_reversion_section_keys(self) -> frozenset[str]:
        return frozenset(f"{self.title}:{c.section}" for c in self.sunset_reversions)

    # --- agreement / residual partitions -------------------------------------

    def agreeing_rows(self) -> tuple[USDryRunSectionRow, ...]:
        return tuple(r for r in self.rows if r.status == "agree")

    def residual_rows(self) -> tuple[USDryRunSectionRow, ...]:
        return tuple(r for r in self.rows if r.status != "agree")

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
        missing = tuple(
            sorted((changed - set(self.claimed_sections)) - sunset_keys)
        )
        sunset = tuple(sorted(changed & sunset_keys))
        return {
            "oracle_changed_section_count": denom,
            "sections_materialized_in_agreement": numer,
            "coverage_fraction": (numer / denom) if denom else None,
            "claimed_section_count": len(self.claimed_sections),
            "missing_source_sections": list(missing),
            "missing_source_section_count": len(missing),
            "sunset_reversion_sections": list(sunset),
            "sunset_reversion_section_count": len(sunset),
        }

    def agreement_surface(self) -> dict[str, Any]:
        """Project per-section rows into a typed agreement surface (core reuse)."""
        residuals: list[AgreementResidual] = []
        for row in self.rows:
            if row.status == "agree":
                residuals.append(
                    AgreementResidual(
                        residual_id=f"us:{self.title}:{row.section_key}:{row.op_id}:agrees",
                        jurisdiction="us",
                        agreement_surface=_AGREEMENT_SURFACE,
                        family="agreement",
                        status="agrees",
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
                        status="residual",
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
        # UNLESS the temporal layer reclassifies it as a sunset reversion (F2).
        claimed = set(self.claimed_sections)
        sunset_keys = self.sunset_reversion_section_keys()
        sunset_by_key = {
            f"{self.title}:{c.section}": c for c in self.sunset_reversions
        }
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
                        status="residual",
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
            residuals.append(
                AgreementResidual(
                    residual_id=f"us:{self.title}:{section_key}:missing_source",
                    jurisdiction="us",
                    agreement_surface=_AGREEMENT_SURFACE,
                    family="source_footing_gap",
                    status="residual",
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
            "north_star": self.north_star(),
            "boundary_status": self.boundary_proof.status,
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


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Materialization kernel (section-level surface)
# ---------------------------------------------------------------------------


def _materialize_one(
    operation: LegalOperation,
    before_text: str,
    *,
    before_section: UscSection | None = None,
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
            # hit the first occurrence anywhere).
            node_text = _locate_subsection_text(before_section, operation.target)
            if node_text is not None and node_text in before_text:
                if match_text not in node_text:
                    return ("", US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID, DISPOSITION_LAWVM_WRONG)
                new_node_text = node_text.replace(match_text, replacement or "", count)
                # Substitute the patched node text back into the running section text
                # (first occurrence — the node text is unique enough to anchor on).
                materialized = before_text.replace(node_text, new_node_text, 1)
                return (materialized, "", "")
            # Sub-section node not locatable (the split did not expose it cleanly).
            # Fall back to a section-level string replace when the anchor is
            # UNAMBIGUOUS — each-place, or a single occurrence in the section: a
            # precise match_text needs no node location. Only a multi-occurrence
            # anchor we cannot place stays a typed residual (genuinely ambiguous).
            if match_text in before_text and (count == -1 or before_text.count(match_text) == 1):
                materialized = before_text.replace(match_text, replacement or "", count)
                return (materialized, "", "")
            if match_text not in before_text:
                return ("", US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID, DISPOSITION_LAWVM_WRONG)
            return (
                "",
                US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
                DISPOSITION_LAWVM_WRONG,
            )

        if match_text not in before_text:
            # Honest gap: the strike anchor is not literally present. Never
            # fuzzy-match. This is a residual (our op vs this section), disposition
            # lawvm_wrong: the lowering produced a match_text the source doesn't carry.
            return ("", US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID, DISPOSITION_LAWVM_WRONG)
        materialized = before_text.replace(match_text, replacement or "", count)
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
        anchor_text = _locate_subsection_text(before_section, operation.anchor)
        if anchor_text is None or anchor_text not in before_text:
            return (
                "",
                US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
                DISPOSITION_LAWVM_WRONG,
            )
        materialized = before_text.replace(
            anchor_text, f"{anchor_text} {payload_text}".strip(), 1
        )
        return (materialized, "", "")

    if action is StructuralAction.REPEAL and is_subsection:
        # "by striking subsection (X)" — remove the node's span from the section
        # text and recompose. Only at sub-section granularity; a whole-section
        # repeal (handled below) is not a text edit.
        node_text = _locate_subsection_text(before_section, operation.target)
        if node_text is None or node_text not in before_text:
            # The struck node is not in the before edition — it was introduced by an
            # un-lowered sibling/future op (or the strike is a conditional/sunset
            # repeal of a not-yet-present node). Striking a node that is not there is
            # a NO-OP against the before text, not a wrong materialization: refuse the
            # op (do not compose it, do not tank the section's other ops). The honest
            # gap stays visible as a typed refusal.
            return USDryRunRefusal(
                op_id=op_id,
                rule_id=US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
                message=(
                    f"strike-subsection target {str(operation.target)} not present in "
                    "the before edition (introduced by an un-lowered sibling op or a "
                    "conditional/sunset strike); not composed"
                ),
                target_address=str(operation.target),
            )
        materialized = before_text.replace(node_text, "", 1)
        # Collapse the double space the removed span may leave between neighbours.
        materialized = re.sub(r"\s{2,}", " ", materialized).strip()
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
            node_text = _locate_subsection_text(before_section, operation.target)
            if node_text is None or node_text not in before_text:
                # We could not locate the targeted node in the before section (the
                # split did not expose it cleanly, or an earlier op moved it): a
                # sub-section payload cannot be applied blindly. Typed residual.
                return (
                    "",
                    US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
                    DISPOSITION_LAWVM_WRONG,
                )
            if action is StructuralAction.REPLACE:
                # amend-to-read of the sub-section node: the payload IS the node's
                # new body. Substitute it for the located node text.
                materialized = before_text.replace(node_text, payload_text, 1)
            else:  # INSERT after a sub-section node -> append the payload to the node.
                materialized = before_text.replace(
                    node_text, f"{node_text} {payload_text}".strip(), 1
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
            materialized = f"{before_text} {operation.payload.text}".strip()
        else:  # REPLACE whole node -> the payload IS the new section body.
            materialized = operation.payload.text
        return (materialized, "", "")

    if action is StructuralAction.RENUMBER and is_subsection and operation.destination is not None:
        # "redesignating paragraph (N) as paragraph (M)" — relabel ONLY the node's
        # leading enumerator inside its located span. The from-label appears in the
        # node text as its leading "(N)"; we rewrite that single leading enumerator,
        # never a cross-reference elsewhere in the section. When the node is not
        # cleanly locatable, a typed residual (never an unscoped global relabel).
        from_label = operation.target.leaf_label()
        to_label = operation.destination.leaf_label()
        node_text = _locate_subsection_text(before_section, operation.target)
        if node_text is None or node_text not in before_text:
            return (
                "",
                US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
                DISPOSITION_LAWVM_WRONG,
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
    claimed_sections: set[str] = set()

    # Phase 1: route every op to a typed refusal or to its section's materializing
    # queue. A section amended by several window laws (e.g. Title 11 §101 in
    # 2018->2020) gets ALL its text ops composed in source order before a single
    # comparison against the oracle — comparing each op independently against the
    # fully-amended oracle would spuriously fail every section with more than one
    # amendment.
    section_ops: dict[str, list[LegalOperation]] = {}
    for statute_id, blob in sorted(plaw_blobs.items()):
        report = lower_plaw_amendatory(blob, statute_id=statute_id, enacted=enacted)
        for operation in report.operations():
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

            if section not in before_text_by_section:
                refusals.append(
                    USDryRunRefusal(
                        op_id=operation.op_id,
                        rule_id=US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID,
                        message=(
                            f"target section {section!r} is not present in the before "
                            f"edition (title {title}, year {before_year})"
                        ),
                        target_address=str(operation.target),
                        detail={"section": section},
                    )
                )
                continue

            section_ops.setdefault(section, []).append(operation)

    # Phase 2: compose each section's ops onto its before-text in source order, then
    # compare the composed result against the oracle once.
    for section, operations in section_ops.items():
        section_key = f"{title}:{section}"
        before_text = before_text_by_section[section]
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
        for operation in operations:
            outcome = _materialize_one(
                operation, running, before_section=before_section
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
            running = materialized

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
                    status="residual",
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
                    status="agree",
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
                    status="residual",
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
                    status="residual",
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

    def _paths(section_keys: Any) -> tuple[TreePath, ...]:
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
        status=status,
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
