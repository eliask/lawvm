"""Chapter-qualified target resolution for uncovered-body recovery.

This is the ``WHERE does this body unit go?`` phase, isolated as a pure function
with an explicit, auditable verdict. It is the dominant correctness concern in
uncovered recovery: Finnish statutes frequently restart section numbering per
chapter (e.g. Metsälaki/Vesilaki — the same ``§ 1`` exists in many chapters), so
resolving a bare label without chapter qualification silently lands content in
the wrong chapter.

Reconstruction note (hostile-source / missing-spec compilation): the legacy
``_process_section_candidate`` cascade interleaves this resolution with
disposition (insert/replace/merge) and mutation. Here we separate *resolution*
(pure, read-only, one typed verdict) from disposition, so every placement
decision is inspectable and the chapter-restart invariant is enforced in exactly
one place rather than scattered across guards.

The verdict carries its own provenance (``reason``) so the recovery's placement
decisions can be audited without re-deriving them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional, Protocol, Sequence, Set, Tuple

PathStep = Tuple[str, str]
ProvisionPath = Tuple[PathStep, ...]

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


class TargetVerdict(Enum):
    """The kind of placement a resolved target implies."""

    EXISTING = "existing"      # resolves to a live section in the master tree
    NEW = "new"                # no live section found → this is an INSERT
    AMBIGUOUS = "ambiguous"    # duplicate label + no usable chapter context → unsafe to place


class StateLookup(Protocol):
    """The read-only slice of ReplayState the resolver depends on.

    Declared explicitly so the resolver is testable without a full ReplayState
    and so its inputs are an auditable contract rather than an opaque object.
    """

    duplicate_section_labels: Set[str]

    def find_section_path(self, label: str, chapter_num: Optional[str] = ...) -> Optional[ProvisionPath]: ...


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """Typed, auditable verdict for where an uncovered body section belongs.

    Pure data: resolution does not mutate the tree. ``reason`` is the provenance
    string for the audit trail (which rule produced this verdict).
    """

    verdict: TargetVerdict
    label: str
    amend_chapter: Optional[str]
    amend_part: Optional[str]
    existing_path: Optional[ProvisionPath]
    cross_chapter: bool
    used_unscoped_fallback: bool
    reason: str

    def __post_init__(self) -> None:
        # Invariants — these encode what a well-formed verdict must satisfy, so a
        # malformed resolution fails loud instead of silently mis-placing content.
        if self.verdict is TargetVerdict.EXISTING and self.existing_path is None:
            raise ValueError("EXISTING verdict requires a non-None existing_path")
        if self.verdict is TargetVerdict.NEW and self.existing_path is not None:
            raise ValueError("NEW verdict must not carry an existing_path")
        if self.used_unscoped_fallback and self.amend_chapter is None:
            raise ValueError("unscoped fallback only applies when a chapter context was present")


def _path_chapter(path: ProvisionPath) -> Optional[str]:
    return next((lbl for kind, lbl in path if kind == "chapter"), None)


def resolve_target(
    label: str,
    amend_chapter: Optional[str],
    amend_part: Optional[str],
    state: "ReplayState | StateLookup",
    owned_chapter_labels: Sequence[str] | Set[str],
) -> ResolvedTarget:
    """Resolve where an uncovered body section ``label`` belongs in the master.

    Pure and read-only. Returns one typed verdict:

    - ``EXISTING``: a live section was found (scoped, or unscoped-but-unique).
      ``cross_chapter`` is set when the found section sits in a different chapter
      than the amendment declared — the caller must NOT blindly replace it.
    - ``NEW``: no live section → INSERT at the declared chapter.
    - ``AMBIGUOUS``: the label is duplicated across chapters and there is no
      usable chapter context to disambiguate → placing it anywhere risks the
      wrong chapter (the chapter-restart-numbering failure mode). The caller
      should surface this rather than guess.

    The unscoped fallback (look up a bare label when the chapter-scoped lookup
    misses) is deliberately gated: only when the label is globally unique AND the
    declared chapter is not one this amendment newly inserts. Both guards prevent
    a same-numbered section in an unrelated chapter from being matched.
    """
    owned = set(owned_chapter_labels)

    # 1. Chapter-scoped lookup first.
    existing_path = state.find_section_path(label, amend_chapter)
    used_unscoped_fallback = False

    # 2. Unscoped fallback — only when safe (unique label, not a newly-owned chapter).
    if existing_path is None and amend_chapter:
        if label not in state.duplicate_section_labels and amend_chapter not in owned:
            existing_path = state.find_section_path(label)
            used_unscoped_fallback = existing_path is not None

    # 3. No live section anywhere → NEW insert.
    if existing_path is None:
        # Ambiguity check still applies for the no-chapter-context duplicate case:
        # without it we cannot even decide NEW vs collision. Here, with no path
        # found, NEW is the safe verdict (insert under the declared chapter).
        return ResolvedTarget(
            verdict=TargetVerdict.NEW,
            label=label,
            amend_chapter=amend_chapter,
            amend_part=amend_part,
            existing_path=None,
            cross_chapter=False,
            used_unscoped_fallback=False,
            reason="no_live_section_scoped_or_unique_unscoped",
        )

    # 4. A live section was found. Decide cross-chapter / ambiguity.
    cross_chapter = False
    reason = "scoped_match" if not used_unscoped_fallback else "unscoped_unique_match"

    if amend_chapter is not None:
        found_chapter = _path_chapter(existing_path)
        if found_chapter is None or found_chapter != amend_chapter:
            cross_chapter = True
            reason = "cross_chapter_mismatch"
    elif label in state.duplicate_section_labels:
        # No chapter context + the label is duplicated across chapters: the lookup
        # landed in an arbitrary chapter. Treat as ambiguous so the caller does
        # not replace the wrong chapter's section.
        return ResolvedTarget(
            verdict=TargetVerdict.AMBIGUOUS,
            label=label,
            amend_chapter=None,
            amend_part=amend_part,
            existing_path=existing_path,
            cross_chapter=True,
            used_unscoped_fallback=used_unscoped_fallback,
            reason="duplicate_label_no_chapter_context",
        )

    return ResolvedTarget(
        verdict=TargetVerdict.EXISTING,
        label=label,
        amend_chapter=amend_chapter,
        amend_part=amend_part,
        existing_path=existing_path,
        cross_chapter=cross_chapter,
        used_unscoped_fallback=used_unscoped_fallback,
        reason=reason,
    )
