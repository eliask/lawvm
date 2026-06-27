"""Finnish definition-list introducer predicate (§2.3 firewall de-leak).

The cross-jurisdiction kernel ``core/tree_ops.find_flattened_sublist_warnings``
previously branched on a Finnish-language fragment (``tarkoitetaan``) to decide
whether a parent opens a definitions list. That check was a §2.3 core/frontend
firewall leak — the kernel may not interpret jurisdiction-language fragments.

This module owns that fragment. The kernel keeps the jurisdiction-neutral
suffix-colon (``:``) drafting convention and asks a frontend-supplied predicate
for the language-specific signal; Finland wires this predicate in at the FI
replay projection call sites and the FI invariant-bisect CLI dispatcher.

The predicate answers a *parent* IRNode (typically a ``SUBSECTION`` or
``SECTION``) — the same shape the kernel previously inspected. It walks the
parent's direct ``INTRO`` children for the Finnish definition-list idioms:

  - ``Tässä luvussa tarkoitetaan`` …
  - ``… :llä tarkoitetaan …`` (inline)
  - ``joilla tarkoitetaan`` / ``jolla tarkoitetaan``

The suffix-colon (``:``) case is left to the kernel because it is a universal
drafting convention; only the Finnish-language fragment is owned here.
"""

from __future__ import annotations

from typing import List

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import kind_str


# ---------------------------------------------------------------------------
# Module-scope constants
# ---------------------------------------------------------------------------

# Finnish definition-list introducer phrases. Originally in
# ``core/tree_ops._FI_DEFINITION_INTRO_PHRASES``; moved here as part of the
# §2.3 core/frontend firewall de-leak (notes/REGEX_TO_GRAMMAR_MIGRATION.md
# rank 11). The kernel never reads this tuple.
#
#lawvm-regex: substring classifier over an intro child's own text; pure
#frontend language-fragment signal — mints/drops no legal state, the kernel
#treats the predicate result as opaque.
_FI_DEFINITION_INTRO_PHRASES: tuple[str, ...] = (
    "tarkoitetaan",
    "joilla tarkoitetaan",
    "jolla tarkoitetaan",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _node_intro_text(node: IRNode) -> str:
    """Return visible intro/content text from one IR node.

    Mirrors the kernel's same-named helper (now ``_node_intro_text`` in
    ``core/tree_ops.py``) so the FI predicate inspects the same text the
    kernel did when the fragment lived there. Local rather than imported
    because the kernel helper is private, and replicating the 7-line
    definition here keeps the FI predicate self-contained.
    """
    parts: List[str] = []
    if node.text and node.text.strip():
        parts.append(node.text.strip())
    for child in node.children:
        if kind_str(child.kind) in {"intro", "content"} and child.text and child.text.strip():
            parts.append(child.text.strip())
    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fi_definition_list_introducer_predicate(parent: IRNode) -> bool:
    """Return True when *parent*'s intro child carries a FI definition-list idiom.

    Parameters
    ----------
    parent
        The IRNode whose direct children should be inspected. Typically a
        ``SUBSECTION`` or ``SECTION`` parent in a Finnish statute.

    Returns
    -------
    bool
        True iff at least one direct ``INTRO`` child of *parent* contains one
        of ``_FI_DEFINITION_INTRO_PHRASES``. The kernel's universal suffix-colon
        check is separate; this predicate contributes only the Finnish-language
        fragment signal.

    Notes
    -----
    The kernel (``core/tree_ops.find_flattened_sublist_warnings``) calls this
    predicate when ``definition_introducer_predicate`` is wired through the
    FI replay projection call sites. Core treats the predicate's verdict as
    an opaque ``bool`` — it does not parse or interpret the Finnish fragment
    (AGENTS.md §2.3, "core may host a hook used by frontends, document that
    core does not interpret frontend-local values").
    """
    for child in parent.children:
        if kind_str(child.kind) != "intro":
            continue
        text = _node_intro_text(child)
        if not text:
            continue
        lowered = text.lower()
        if any(phrase in lowered for phrase in _FI_DEFINITION_INTRO_PHRASES):
            return True
    return False


__all__ = ["fi_definition_list_introducer_predicate"]
