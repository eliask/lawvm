"""``lawvm.core.char_span_metric`` — the char-span region metric (§3.4).

Design reference: ``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.4 ("Boundary
is metric-agnostic — IR-paths vs char-spans"). This module ships the SECOND
instantiation of the metric-agnostic mutation-boundary invariant, the one the
US-federal text-level materializer plugs in.

THE METRIC. The mutation-boundary invariant — *the op changed only its declared
region, and nothing outside it* — is abstract over the metric used to NAME a
region (§3.4). The tree frontends name a region as a set of ``TreePath``s
(``core/apply_seam.IR_PATH_METRIC`` over ``diff_ir_paths_identity_pruned``). US is
a HYBRID: it lowers amendment idioms to STRUCTURED ``LegalOperation``s
(``us_federal/amendatory.py``) but MATERIALIZES at text level — its materializer
resolves a structured op's target to a ``(start, end)`` char span inside a
section's text blob and does string surgery confined to that span. So US joins the
apply seam at the OP level like everyone else; it differs only in which
``Materializer`` + ``RegionMetric`` it plugs in.

A ``Span`` here is a half-open ``(start, end)`` char interval into one section's
normalized text blob. The metric's four operations are exactly the §3.4 protocol:

  * ``declared_region(op, state)`` — the located target span. For a sub-section
    op, the span of the resolved node text inside the section text (US's
    ``_locate_subsection_text`` resolves the node; this module finds its offset).
    For a whole-section op, ``(0, len(section_text))`` — the whole blob.
  * ``observed_region(before, after)`` — the minimal char span that actually
    changed between the before/after section text (common-prefix / common-suffix
    trim). The empty span ``(n, n)`` when nothing changed.
  * ``within(observed, declared)`` — ``observed ⊆ declared``.
  * ``disjoint_elsewhere(before, after, declared)`` — nothing OUTSIDE the
    declared span changed (the text before ``declared.start`` and after
    ``declared.end`` is byte-identical between ``before`` and ``after``).

WRAPS, DOES NOT REINVENT, THE US SPAN-LOCATION CODE. ``declared_region`` takes a
pre-located node text (US's ``_locate_subsection_text`` / the section-text span
map at ``dry_run.py``) and computes the offset; it never re-parses the address or
re-implements ``split_statutory_subsections``. The US frontend supplies the
located node text via :class:`CharSpanState`; this module only does offset + diff
arithmetic over strings.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). Pure projection: every method is a pure
function of its string inputs. It never mutates state, never repairs to an oracle,
never raises on shape-valid input (an unlocatable node yields a ``None`` declared
span — the caller surfaces it, this module never guesses). The boundary verdict it
feeds is ADDITIVE evidence over the US apply; it does not change what text US
produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, TypeVar

__all__ = [
    "Span",
    "CharSpanState",
    "CharSpanRegionMetric",
    "CHAR_SPAN_METRIC",
    "char_span_boundary_holds",
    "CharSpanBoundaryVerdict",
]

#: A half-open ``(start, end)`` char interval into a section's text blob.
Span = tuple[int, int]

Region = TypeVar("Region")


class _RegionMetricProtocol(Protocol[Region]):
    """The full §3.4 region-metric protocol (declared/observed/within/disjoint).

    The :class:`~lawvm.core.apply_seam.RegionMetric` shipped in the seam declares
    only ``observed_region`` (the one method the seam's IR boundary gate calls);
    this protocol is the COMPLETE §3.4 surface the char-span lane exercises. A
    char-span metric satisfies BOTH: it is plug-compatible with the seam's
    ``RegionMetric`` (it has ``observed_region``) and it carries the three extra
    boundary operations the char-span audit needs.
    """

    def declared_region(self, op: object, state: object) -> Optional[Region]: ...

    def observed_region(self, before: object, after: object) -> Region: ...

    def within(self, observed: Region, declared: Region) -> bool: ...

    def disjoint_elsewhere(
        self, before: object, after: object, declared: Region
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class CharSpanState:
    """The per-op char-span apply state the US materializer threads (§3.4).

    For the tree frontends the seam ``State`` is the frozen ``IRNode`` body; for
    US it is a section's TEXT plus the located target node text, bundled so the
    metric can resolve the declared span without re-parsing the address.

    * ``section_text`` — the section's running text blob (the ``State`` proper).
    * ``located_node_text`` — the verbatim before-text of the node the op's
      ``LegalAddress`` names, as resolved by US's ``_locate_subsection_text`` /
      ``_locate_subsection_text_resolved``. ``None`` for a WHOLE-section op (the
      declared span is the whole blob) or when the node could not be located (the
      declared span is undefined — the metric returns ``None`` and the caller
      surfaces it rather than guessing a span, §1.1 no silent target hijacking).
    """

    section_text: str
    located_node_text: Optional[str] = None


def _common_prefix_len(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def _common_suffix_len(a: str, b: str, *, cap: int) -> int:
    i = 0
    la, lb = len(a), len(b)
    while i < cap and a[la - 1 - i] == b[lb - 1 - i]:
        i += 1
    return i


def _minimal_changed_span(before: str, after: str) -> Span:
    """The minimal half-open char span that differs between ``before``/``after``.

    Trims the common prefix and the common suffix. When the strings are equal the
    result is the empty span ``(p, p)`` at the first (== whole) shared index. The
    span is indexed into ``before`` (the pre-edit blob): ``before[start:end]`` is
    the deleted/replaced region; the inserted text lives at the same ``start`` in
    ``after``. ``end`` is clamped so the trimmed suffix never overruns the prefix
    in either string (the classic prefix+suffix-overlap guard).
    """
    if before == after:
        p = _common_prefix_len(before, after)
        return (p, p)
    prefix = _common_prefix_len(before, after)
    # The suffix may not overlap the already-consumed prefix in EITHER string.
    cap = min(len(before) - prefix, len(after) - prefix)
    suffix = _common_suffix_len(before, after, cap=cap)
    start = prefix
    end = len(before) - suffix
    return (start, end)


def _locate_span_in(blob: str, fragment: str) -> Optional[Span]:
    """First-occurrence ``(start, end)`` of ``fragment`` inside ``blob``.

    Returns ``None`` when the fragment is absent (the node text was located in a
    DIFFERENT normalization than the section blob — the caller treats an absent
    declared span as undefined, not as a violation). The empty fragment yields
    ``None`` (an empty node names no span). Deterministic first-occurrence; the US
    materializer's own occurrence selection is its concern, the metric only needs
    a containing span to test ``within`` against.
    """
    if not fragment:
        return None
    idx = blob.find(fragment)
    if idx < 0:
        return None
    return (idx, idx + len(fragment))


@dataclass(frozen=True, slots=True)
class CharSpanRegionMetric:
    """Char-span instantiation of the §3.4 region metric (US text materializer).

    A region is a ``(start, end)`` char span into a section's text blob. The four
    operations wrap pure string arithmetic over the US-located node text; nothing
    here parses an address or mutates state (US's ``_locate_subsection_text`` did
    the location upstream, its result handed in via :class:`CharSpanState`).
    """

    def declared_region(
        self, op: object, state: object
    ) -> Optional[Span]:
        """The located target span: the node text's offset, or the whole blob.

        ``state`` is a :class:`CharSpanState`. When it carries a
        ``located_node_text`` the declared span is that fragment's first-occurrence
        span inside the section blob (US located the node; this finds where it
        sits). When ``located_node_text is None`` the op addresses the WHOLE
        section and the declared span is ``(0, len(section_text))``. Returns
        ``None`` only when a node text was supplied but does not occur in the blob
        (the location used a different normalization) — the caller surfaces an
        unresolved declared span rather than the metric guessing one (§1.1).
        """
        assert isinstance(state, CharSpanState), (
            "CHAR_SPAN_METRIC.declared_region requires a CharSpanState"
        )
        if state.located_node_text is None:
            return (0, len(state.section_text))
        return _locate_span_in(state.section_text, state.located_node_text)

    def observed_region(self, before: object, after: object) -> Span:
        """The minimal char span that changed between before/after section text."""
        assert isinstance(before, str) and isinstance(after, str), (
            "CHAR_SPAN_METRIC.observed_region requires str before/after section text"
        )
        return _minimal_changed_span(before, after)

    def within(self, observed: Span, declared: Span) -> bool:
        """``observed ⊆ declared`` (the edited span sits inside the target span).

        An EMPTY observed span (nothing changed: ``start == end``) is within any
        declared span whose closed range contains the point — a no-op op never
        escapes its boundary.
        """
        o_start, o_end = observed
        d_start, d_end = declared
        if o_start == o_end:
            return d_start <= o_start <= d_end
        return d_start <= o_start and o_end <= d_end

    def disjoint_elsewhere(
        self, before: object, after: object, declared: Span
    ) -> bool:
        """True iff NOTHING outside ``declared`` changed (the text is identical).

        ``declared`` names a region in the BEFORE text: the prefix
        ``before[:start]`` and the suffix ``before[end:]`` are everything OUTSIDE
        it. The boundary holds iff the op replaced ONLY ``before[start:end]`` —
        i.e. ``after`` begins with the unchanged prefix and ENDS with the
        unchanged suffix. Because a replacement may grow or shrink the region, the
        after-suffix is matched by its LENGTH (``len(before) - end``) against the
        tail of ``after``, not by indexing ``after`` at ``end`` (which is a
        before-text offset, not an after-text one). For a whole-blob declared span
        (``start == 0``, ``end == len(before)``) both the prefix and the suffix
        are empty, so the boundary holds trivially — there is nothing outside the
        whole blob. This is the char-span analogue of the IR metric's "no changed
        path outside the declared prefix set".
        """
        assert isinstance(before, str) and isinstance(after, str), (
            "CHAR_SPAN_METRIC.disjoint_elsewhere requires str before/after text"
        )
        start, end = declared
        prefix_ok = before[:start] == after[:start]
        suffix = before[end:]
        # The unchanged suffix must be the tail of ``after`` at the same byte
        # length (the region between prefix and suffix is the only edited part).
        if suffix:
            suffix_ok = len(after) >= len(suffix) and after[len(after) - len(suffix):] == suffix
        else:
            suffix_ok = True
        return prefix_ok and suffix_ok


#: The char-span / US-text-surgery region metric (design §3.4 ``CHAR_SPAN_METRIC``).
CHAR_SPAN_METRIC: CharSpanRegionMetric = CharSpanRegionMetric()


@dataclass(frozen=True, slots=True)
class CharSpanBoundaryVerdict:
    """The char-span per-op mutation-boundary verdict (§3.4 analogue of LS-01).

    * ``within_boundary`` — ``True`` iff the op's observed edit is a subset of its
      declared target span AND nothing outside the declared span changed. The
      char-span analogue of ``PerOpMutationBoundaryVerdict.within_boundary``.
    * ``declared_span`` — the located target span, or ``None`` when the target
      node could not be located in the blob (an UNRESOLVED declared region; the
      verdict is then ``within_boundary=False`` with ``unresolved_declared=True``
      so an unlocatable target is never silently treated as in-boundary).
    * ``observed_span`` — the minimal changed span.
    * ``unresolved_declared`` — ``True`` when ``declared_span is None``.
    """

    op_id: str
    within_boundary: bool
    declared_span: Optional[Span]
    observed_span: Span
    unresolved_declared: bool


def char_span_boundary_holds(
    before_text: str,
    after_text: str,
    state: CharSpanState,
    *,
    op_id: str = "",
    metric: CharSpanRegionMetric = CHAR_SPAN_METRIC,
) -> CharSpanBoundaryVerdict:
    """Run the char-span mutation-boundary audit for one US op (§3.4).

    Computes the declared target span (via ``metric.declared_region`` over the
    located node text) and the observed changed span (via
    ``metric.observed_region``), then asserts BOTH ``within`` (edited span ⊆
    declared span) AND ``disjoint_elsewhere`` (nothing outside the declared span
    changed). This is the metric-agnostic mutation-boundary invariant — *the op
    changed only its declared region, nothing outside it* — at char-span
    granularity (design §3.4).

    Pure projection: never mutates, never repairs to an oracle. An UNRESOLVED
    declared span (the node could not be located) yields ``within_boundary=False``
    with ``unresolved_declared=True`` — an unlocatable target is surfaced, never
    silently passed (§0). The caller decides whether the verdict becomes a finding
    (observe) or a barrier (block); this returns the verdict only.
    """
    observed = metric.observed_region(before_text, after_text)
    declared = metric.declared_region(None, state)
    if declared is None:
        return CharSpanBoundaryVerdict(
            op_id=str(op_id or ""),
            within_boundary=False,
            declared_span=None,
            observed_span=observed,
            unresolved_declared=True,
        )
    holds = metric.within(observed, declared) and metric.disjoint_elsewhere(
        before_text, after_text, declared
    )
    return CharSpanBoundaryVerdict(
        op_id=str(op_id or ""),
        within_boundary=holds,
        declared_span=declared,
        observed_span=observed,
        unresolved_declared=False,
    )
