r"""Footnote scoped collation (doc3 D4) — IR-pass over attachment IR.

Scope algorithm per doc3 §invariants SDOC-08 + ``notes_internal/REMAINING_WORK.md`` D4:

1. **Scope assignment** — each footnote belongs to the nearest
   ``APPENDIX``/``PART`` container that encloses its SCHEDULE_ENTRY body.
   Footnote labels are unique-within-scope, not globally. Counter resets
   when the scope changes (``osa_I`` → ``osa_II``).
2. **Marker extraction** — body and table-cell text contains footnote
   markers. The marker grammar is conservative (matches the existing
   ``_FN_MARKER_RE`` in ``pdf_layout.py``):

       (\d+[a-z]?[)])   # ``1)`` / ``12a)`` # followed by content
       (\d+)            # bare digit

   plus ``[N]``-style brackets. A marker is paired with its closest
   preceding text-bearing node — surfacing ``UnboundMarker`` for any
   marker without a body, ``UnreferencedBody`` for any SCHEDULE_ENTRY
   body without a marker.
3. **Link resolution** — markers map to bodies by label-within-scope. A
   marker outside the scope's label set is an ``UnboundMarker`` residual.
   A body in the label set without a corresponding marker is an
   ``UnreferencedBody`` residual.
4. **Duplicate detection** — two SCHEDULE_ENTRY bodies with the same
   label inside the same scope emit a ``DuplicateLabel`` residual
   (SDOC-08 violation — scope-local uniqueness requires uniqueness, and
   here it is breachable only at IR construction time, not at runtime).

This is a PURE IR post-processing pass — no PDF re-extraction, no
runtime cost (satisfies SDOC-11: "the runtime may not invoke PDF
extraction unless an explicit rebuild flag is set"). The pass consumes
an already-built IRNode tree (e.g. a canonical-store attachment IR or a
parsed StatuteContext tree) and emits a typed
:class:`FootnoteCollationResult` with linkages + residuals.

Why a separate module rather than inline in ir_tree_dump.py: the
collation algorithm is non-trivial and is invoked only by audit /
projection paths that explicitly ask for it. Keeping it separate prevents
the dump / show pretty-printers from acquiring hidden IR-analysis
behaviour (§2.10 — surface/overlay observations must not silently
acquire replay authority; the collation stays on the evidence plane).

Operating contract: AGENTS.md §1.9 (typed carriers) + §2.9 (synthetic +
corpus test per meaningful change) + §2.10 (planes type-distinct).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from lawvm.core.ir import IRNode
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import IRNodeKind


# ---------------------------------------------------------------------------
# Marker patterns (routed through compile_classifier_regex per §2.4)
# ---------------------------------------------------------------------------

# Match superscript-style markers at end of (or interior to) text:
#   ``1)`` / ``12a)`` / ``23b)``
# We do NOT match the rest of the content after a marker (the marker
# itself is the linkage key). Anchored no-leading-whitespace so we don't
# pick up footnote-body-introducer patterns (the SCHEDULE_ENTRY body's
# own label is its own kind — see _walk_bodies).
_MARKER_TAIL_RE = compile_classifier_regex(
    r"(\d+[a-z]?)[)\]]",
    classifier_id="lawvm.finland.footnote_collation.marker_tail",
)

# ``[1]`` / ``[12]`` / ``[12a]`` bracketed markers.
_MARKER_BRACKET_RE = compile_classifier_regex(
    r"\[(\d+[a-z]?)\]",
    classifier_id="lawvm.finland.footnote_collation.marker_bracket",
)


# ---------------------------------------------------------------------------
# Typed carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FootnoteLinkage:
    """One resolved marker-to-body link within a single scope."""

    scope_label: str
    marker_label: str
    body_label: str
    body_text_snippet: str
    marker_path: tuple[str, ...]
    body_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FootnoteCollationResult:
    """Typed carrier for the scoped collation pass.

    Every collected unit ends up *owned* (§1.8 conservation):
    * ``linkages``           — markers paired with SCHEDULE_ENTRY bodies;
    * ``unbound_markers``    — markers with no body in scope (could be a
      dangling reference or a marker the IR encoder never turned into a
      SCHEDULE_ENTRY);
    * ``unreferenced_bodies`` — SCHEDULE_ENTRY bodies no marker cites;
    * ``duplicate_labels``    — two bodies in the same scope with the
      same label (SDOC-08 violation, raised at IR construction time);
    * ``scopes_seen``        — total scopes walked (audit / coverage).
    """

    linkages: tuple[FootnoteLinkage, ...] = field(default_factory=tuple)
    unbound_markers: tuple[UnboundMarker, ...] = field(default_factory=tuple)
    unreferenced_bodies: tuple[UnreferencedBody, ...] = field(default_factory=tuple)
    duplicate_labels: tuple[DuplicateLabel, ...] = field(default_factory=tuple)
    scopes_seen: int = 0


@dataclass(frozen=True, slots=True)
class UnboundMarker:
    """Marker found in body text with no matching SCHEDULE_ENTRY in scope."""

    scope_label: str
    marker_label: str
    path: tuple[str, ...]
    text_snippet: str


@dataclass(frozen=True, slots=True)
class UnreferencedBody:
    """SCHEDULE_ENTRY body with no marker citing it in scope."""

    scope_label: str
    body_label: str
    path: tuple[str, ...]
    text_snippet: str


@dataclass(frozen=True, slots=True)
class DuplicateLabel:
    """Two SCHEDULE_ENTRY bodies with the same label inside one scope.

    SDOC-08 violation — labels must be scope-unique. A duplicate signals
    a parser or extractor bug at IR construction time, not a runtime
    resolve failure. Surfaces as a typed residual: someone must audit
    the IR producer; nothing here silently picks one body over the
    other.
    """

    scope_label: str
    label: str
    body_paths: tuple[tuple[str, ...], ...]


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


def _walk(node: IRNode, path: tuple[str, ...]) -> Iterator[tuple["IRNode", tuple[str, ...]]]:
    yield node, path
    for idx, child in enumerate(node.children):
        yield from _walk(
            child, path + (f"{child.kind.value}[{idx}]",)
        )


def _scope_label(node: IRNode) -> str:
    """The scope key — APPENDIX / PART label. Empty when no scope (root)."""
    if node.kind in (IRNodeKind.APPENDIX, IRNodeKind.PART):
        return node.label or ""
    return ""


def _is_scope_container(node: IRNode) -> bool:
    return node.kind in (IRNodeKind.APPENDIX, IRNodeKind.PART)


def _extract_markers(text: str) -> Iterator[str]:
    """Return the labels of any markers found in ``text``.

    Both the ``N)`` / ``Na)`` tail form (no leading whitespace, so the
    body's own ``"1) Footnote body...`` introducer is not double-counted —
    the introducer sits at start of text where the marker is captured by
    _walk_bodies, not by this regex) and the ``[N]`` bracketed form.
    """
    if not text:
        return
    seen: set[str] = set()
    for m in _MARKER_TAIL_RE.finditer(text):
        label = m.group(1)
        if label not in seen:
            seen.add(label)
            yield label
    for m in _MARKER_BRACKET_RE.finditer(text):
        label = m.group(1)
        if label not in seen:
            seen.add(label)
            yield label


def _walk_bodies(
    root: IRNode,
) -> Iterator[tuple[str, str, tuple[str, ...], str]]:
    """Yield ``(scope_label, body_label, path, text)`` for each SCHEDULE_ENTRY.

    Walks down — the nearest enclosing APPENDIX/PART scope determines the
    body's scope label. Bodies outside any APPENDIX/PART (e.g. loose in
    the HCONTAINER root) carry an empty scope label.
    """
    current_scope = ""

    def walk(node: IRNode, scope: str, path: tuple[str, ...]):
        nonlocal current_scope
        if _is_scope_container(node):
            scope = node.label or ""
        if node.kind == IRNodeKind.SCHEDULE_ENTRY:
            yield scope, node.label or "", path, (node.text or "")
            return
        for idx, child in enumerate(node.children):
            yield from walk(
                child, scope, path + (f"{child.kind.value}[{idx}]",)
            )

    yield from walk(root, "", (f"{root.kind.value}[root]",))


def _walk_markers(
    root: IRNode,
) -> Iterator[tuple[str, str, tuple[str, ...], str]]:
    """Yield ``(scope_label, marker_label, path, text)`` for each marker found.

    Walks the tree; for each TEXT-bearing leaf that is NOT a
    SCHEDULE_ENTRY (so the marker regex doesn't match the body's own
    intro), extract markers from the text and emit one tuple per
    marker. The nearest enclosing APPENDIX/PART determines the marker's
    scope.
    """

    def walk(node: IRNode, scope: str, path: tuple[str, ...]):
        if _is_scope_container(node):
            scope = node.label or ""
        # Only scan text-bearing content leaves — skip SCHEDULE_ENTRY (it
        # has its own label-on-the-rail) and skip pure structural nodes
        # without operative text.
        if node.kind != IRNodeKind.SCHEDULE_ENTRY and node.text:
            for marker_label in _extract_markers(node.text):
                yield scope, marker_label, path, node.text
        for idx, child in enumerate(node.children):
            yield from walk(
                child, scope, path + (f"{child.kind.value}[{idx}]",)
            )

    yield from walk(root, "", (f"{root.kind.value}[root]",))


def _classify_duplicates(
    bodies_by_scope: dict[str, dict[str, list[tuple[tuple[str, ...], str]]]],
) -> tuple[DuplicateLabel, ...]:
    """Find SCHEDULE_ENTRY bodies with the same label inside one scope."""
    out: list[DuplicateLabel] = []
    for scope_label, by_label in bodies_by_scope.items():
        for label, occurrences in by_label.items():
            if len(occurrences) > 1:
                out.append(
                    DuplicateLabel(
                        scope_label=scope_label,
                        label=label,
                        body_paths=tuple(p for p, _t in occurrences),
                    )
                )
    return tuple(out)


def collate_footnotes_by_scope(root: IRNode) -> FootnoteCollationResult:
    """Walk ``root`` and emit a typed collation result.

    * Count distinct scopes (APPENDIX/PART containers that contain at
      least one SCHEDULE_ENTRY body).
    * Assign each marker the scope of its nearest enclosing APPENDIX/PART.
    * Match markers to bodies by label-within-scope; emit
      :class:`FootnoteLinkage` records.
    * Emit :class:`UnboundMarker` for markers without a matching body in
      scope.
    * Emit :class:`UnreferencedBody` for bodies no marker cites.
    * Emit :class:`DuplicateLabel` when two bodies share a label in one
      scope (SDOC-08 violation — surfaces the producer bug).
    """
    bodies: list[tuple[str, str, tuple[str, ...], str]] = list(
        _walk_bodies(root)
    )
    scopes_with_bodies: set[str] = {
        scope for scope, _label, _path, _text in bodies
    }

    # Build scope → label → [(path, text)] index.
    bodies_by_scope: dict[str, dict[str, list[tuple[tuple[str, ...], str]]]] = {}
    for scope, label, path, text in bodies:
        bodies_by_scope.setdefault(scope, {}).setdefault(label, []).append(
            (path, text)
        )

    duplicates = _classify_duplicates(bodies_by_scope)

    # Index: scope → label → first body occurrence (path, text).
    # When duplicates exist we still use the first body for the linkage
    # — but the duplicate residual carries the audit flag.
    body_index: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {}
    for scope, by_label in bodies_by_scope.items():
        for label, occurrences in by_label.items():
            body_index[(scope, label)] = occurrences[0]

    linked_marker_keys: set[tuple[str, str, tuple[str, ...], str]] = set()
    linkages: list[FootnoteLinkage] = []
    unbound_markers: list[UnboundMarker] = []
    for scope, marker_label, path, text in _walk_markers(root):
        body = body_index.get((scope, marker_label))
        if body is None:
            unbound_markers.append(
                UnboundMarker(
                    scope_label=scope,
                    marker_label=marker_label,
                    path=path,
                    text_snippet=text[:120],
                )
            )
            continue
        body_path, body_text = body
        linkages.append(
            FootnoteLinkage(
                scope_label=scope,
                marker_label=marker_label,
                body_label=marker_label,
                body_text_snippet=body_text[:120],
                marker_path=path,
                body_path=body_path,
            )
        )
        linked_marker_keys.add((scope, marker_label, body_path, body_text))

    # Unreferenced bodies: those whose (scope, label, path) is not in
    # the linked set.
    referenced_body_keys: set[tuple[str, str, tuple[str, ...]]] = {
        (lk.scope_label, lk.body_label, lk.body_path) for lk in linkages
    }
    unreferenced_bodies: list[UnreferencedBody] = []
    for scope, _label, path, text in bodies:
        key = (scope, _label, path)
        if key not in referenced_body_keys:
            unreferenced_bodies.append(
                UnreferencedBody(
                    scope_label=scope,
                    body_label=_label,
                    path=path,
                    text_snippet=text[:120],
                )
            )

    return FootnoteCollationResult(
        linkages=tuple(linkages),
        unbound_markers=tuple(unbound_markers),
        unreferenced_bodies=tuple(unreferenced_bodies),
        duplicate_labels=duplicates,
        scopes_seen=len(scopes_with_bodies),
    )


__all__ = [
    "DuplicateLabel",
    "FootnoteCollationResult",
    "FootnoteLinkage",
    "UnboundMarker",
    "UnreferencedBody",
    "collate_footnotes_by_scope",
]
