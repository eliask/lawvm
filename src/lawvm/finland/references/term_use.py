"""Finnish defined-term USE resolution pass.

The defined-term BINDER (``defined_terms.py``) recognises where a local term is
INTRODUCED and tied to a target.  This module is the dual: given the body text of
a statute and the list of :class:`DefinedTermBinding` recognised in it, it finds
later USES of those terms in the prose and resolves each use back to its binding.

A use may be inflected --- binding term ``sivutuote`` is used as ``sivutuotteen``
/ ``sivutuotteita``; binding term ``sivutuoteasetus`` as ``sivutuoteasetuksen``.
We use the M1 morphology engine (read-only) to GENERATE the case forms of each
binding term and match inflected uses against that generated set.

Fail-loud, tag-don't-guess (AGENTS.md §1.8 / FI_PARSE_OVERLAY_IR_MODEL
"tag-don't-guess"):

  * ``resolved``  --- exactly ONE in-scope binding matches the use.
  * ``open``      --- a use that matches a binding's surface but has NO in-scope
                      binding: the only matching binding(s) appear AFTER the use
                      (use precedes its definition --- a scope/order violation).
                      NEVER silently resolved.
  * ``ambiguous`` --- MORE THAN ONE in-scope binding matches the use; ALL are
                      listed, none is picked.

A token that matches NO binding surface at all is simply NOT emitted: we resolve
USES of the GIVEN defined terms, we do not fabricate "term-shaped" uses out of
arbitrary prose (that would flag every common word).  ``open`` is therefore the
scope/order violation case --- a binding for the surface exists, but not one
positioned before the use.

Morphology boundary: if the M1 engine cannot inflect a binding term (its
``classify`` hits a wall, or the binding was already flagged
``unsupported_morphology`` by the binder), we DO NOT guess case forms.  We fall
back to EXACT-SURFACE matching against the binding's written term and tag every
use found that way with ``rule_id`` ``term_use.exact_surface`` so the degraded
matching is visible, never silent.

This is a SELF-CONTAINED recognizer over plain text.  Integration into
``extract_all_reference_mentions`` is a later serial step; this module imports the
binding type and morphology engine READ-ONLY and edits neither.

Per AGENTS.md §1.11: the term-token scanner is a single bounded pattern; the
generated-form set is built once per binding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.morphology import (
    MorphNumber,
    classify,
    generate_forms,
    head_entry,
    is_known_head,
)
from lawvm.finland.morphology.api import MorphEntry
from lawvm.finland.references.defined_terms import (
    BINDING_TARKOITETAAN,
    STATUS_UNSUPPORTED_MORPHOLOGY,
    DefinedTermBinding,
)

# ---------------------------------------------------------------------------
# Status / rule-id constants (closed sets)
# ---------------------------------------------------------------------------

STATUS_RESOLVED = "resolved"
STATUS_OPEN = "open"
STATUS_AMBIGUOUS = "ambiguous"

#: rule_id values recorded on each TermUse, documenting HOW the match was made.
RULE_MORPH = "term_use.morph"          # matched a morphology-generated case form
RULE_EXACT_SURFACE = "term_use.exact_surface"  # exact written-term fallback match
RULE_BEFORE_BINDING = "term_use.before_binding"  # matched but used before binding


# ---------------------------------------------------------------------------
# Typed output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TermUse:
    """A resolved (or unresolved) use of a defined term in statute body text.

    Attributes:
        term_surface:  The surface form as it appears in the body (inflected),
                       e.g. ``sivutuotteen``.
        lemma:         The binding term lemma the use was matched against
                       (citation/nominative form), or the use's own surface when
                       no binding matched (``status="open"``).
        binding:       The single resolved :class:`DefinedTermBinding` when
                       ``status="resolved"``, else ``None`` (open / ambiguous list
                       the candidates separately, never collapse to one).
        bindings:      ALL matching in-scope bindings.  Length 1 for resolved,
                       0 for open, >1 for ambiguous.  Never silently truncated.
        source_span:   Byte range of the use token in the body text.
        use_status:    ``resolved`` / ``open`` / ``ambiguous``.
        rule_id:       How the match was found (see ``RULE_*``).
    """

    term_surface: str
    lemma: str
    binding: Optional[DefinedTermBinding]
    source_span: SourceSpan
    use_status: str
    rule_id: str
    bindings: tuple[DefinedTermBinding, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Term-token scanner
# ---------------------------------------------------------------------------
#
# A word token of a Finnish term: letters (incl. ä ö å, upper/lower) plus an
# internal hyphen.  Bounded run; matched on word boundaries so we capture the
# full inflected surface (``sivutuotteen``) as one token.

_WORD = re.compile(
    r"[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+(?:-[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+)*"
)


# ---------------------------------------------------------------------------
# Morphology-driven form generation for a binding term
# ---------------------------------------------------------------------------


def _entry_for_term(term: str) -> Optional[MorphEntry]:
    """Build a :class:`MorphEntry` for ``term`` if the M1 engine can inflect it.

    Two paths, both fail-loud:

      * final-head compound: if the term's trailing run is a known closed-class
        head (``…asetus``, ``…laki``), inflect the HEAD and re-attach the
        invariant modifier prefix.  This is the binder's own "final-head
        compound" notion and the only reliable compound path.
      * simplex: ``classify`` the whole term; only build an entry when
        classification RESOLVES (not ``needs_flag`` / ``ambiguous`` /
        ``unsupported``).  A wall -> ``None`` -> exact-surface fallback upstream.

    Returns ``None`` when the engine declines to commit (never a guess).
    """
    t = term.strip()
    if not t or " " in t:
        # Multi-word NP -> the binder would have flagged it unsupported; no entry.
        return None

    # Known head, used bare or as the final-head of a compound: inflect the head.
    # (The modifier prefix is invariant and re-attached by the caller.)  Longest
    # matching head wins so e.g. ``…oikeus`` is not mis-split on a shorter head.
    low = t.lower()
    best_head: Optional[str] = None
    for head in _KNOWN_HEADS:
        if low.endswith(head):
            if best_head is None or len(head) > len(best_head):
                best_head = head
    if best_head is not None:
        return head_entry(best_head)

    # Simplex: classify the whole term.
    cls = classify(t)
    if cls.classification_status == "resolved" and cls.morph_class is not None:
        return MorphEntry(
            lemma_id=f"term:{low}",
            lemma=t,
            referent_kind="common",
            morph_class=cls.morph_class,
        )
    return None


def _generated_surfaces(term: str) -> Optional[frozenset[str]]:
    """Return the lowercased generated case-form set for ``term``, or ``None``.

    ``None`` signals "morphology unsupported for this term" -> caller falls back
    to exact-surface matching.  When an entry IS built we union the SG cases the
    engine commits to (``certainty="deterministic"``); plural is M1-unsupported
    and contributes nothing (its uses fall through to exact-surface).

    For a final-head compound the engine inflects only the head, so we prepend
    the invariant modifier prefix to each generated head form.
    """
    entry = _entry_for_term(term)
    if entry is None:
        return None

    low = term.strip().lower()
    # If this is a compound (entry.head is a known head shorter than the term),
    # the engine's surface is the HEAD form; re-attach the modifier prefix.
    prefix = ""
    if entry.head is not None and low.endswith(entry.head) and len(low) > len(
        entry.head
    ):
        prefix = low[: len(low) - len(entry.head)]

    surfaces: set[str] = {low}  # the written nominative always matches
    for form in generate_forms(entry, numbers=(MorphNumber.SG,)):
        if form.certainty != "deterministic" or not form.surface:
            continue
        surfaces.add((prefix + form.surface).lower())
    return frozenset(surfaces)


# Closed head set, longest-match scan order handled in ``_entry_for_term``.
# (Imported names are functions; we cannot enumerate ``_HEADS`` privately, so we
# keep a local list of the heads we are willing to treat as compound tails. This
# mirrors the morphology ``heads`` module's closed class.)
_KNOWN_HEADS: tuple[str, ...] = (
    "laki",
    "asetus",
    "päätös",
    "sopimus",
    "säädös",
    "määräys",
    "ohje",
    "ilmoitus",
    "direktiivi",
    "virasto",
    "hallinto",
    "ministeriö",
    "lautakunta",
    "keskus",
    "laitos",
    "oikeus",
)
# Defensive: keep the local list in sync with the engine's closed class.
_KNOWN_HEADS = tuple(h for h in _KNOWN_HEADS if is_known_head(h))


# ---------------------------------------------------------------------------
# Per-binding match index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BindingMatcher:
    """Precomputed matching surfaces for one binding."""

    binding: DefinedTermBinding
    #: lowercased surfaces that count as a use of this binding.
    surfaces: frozenset[str]
    #: True when ``surfaces`` came from morphology, False = exact-surface only.
    morph_supported: bool
    #: byte offset after which a use is in scope (binding's span end).
    binding_end: int


def _build_matchers(
    bindings: list[DefinedTermBinding],
) -> list[_BindingMatcher]:
    matchers: list[_BindingMatcher] = []
    for b in bindings:
        term = b.term.strip()
        if not term:
            continue
        gen: Optional[frozenset[str]] = None
        # The binder may have already declared the term morphologically
        # unsupported; honour that and do not attempt generation.
        if b.binding_status != STATUS_UNSUPPORTED_MORPHOLOGY:
            gen = _generated_surfaces(term)
        if gen is not None:
            surfaces = gen
            morph_supported = True
        else:
            surfaces = frozenset({term.lower()})
            morph_supported = False
        matchers.append(
            _BindingMatcher(
                binding=b,
                surfaces=surfaces,
                morph_supported=morph_supported,
                binding_end=b.source_span.byte_offset + b.source_span.byte_len,
            )
        )
    return matchers


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def resolve_term_uses(
    text: str,
    bindings: list[DefinedTermBinding],
    *,
    source_file: str = "",
) -> list[TermUse]:
    """Resolve uses of defined terms in ``text`` against ``bindings``.

    Scans body tokens; a token is a USE when its lowercased surface is in some
    binding's match-surface set (morphology-generated case forms, or the exact
    written term when morphology is unsupported).  Tokens that fall INSIDE a
    binding's own source span are skipped --- the binding site itself is not a
    "use".

    Status (fail-loud):
      * exactly one in-scope binding matches -> ``resolved``.
      * a matched binding lies AFTER the use (use precedes its definition)
        with no other in-scope binding -> ``open`` (scope/order violation).
      * more than one in-scope binding matches -> ``ambiguous`` (all listed).

    A token that matches NO binding is NOT emitted --- we do not fabricate
    "term-shaped" uses out of arbitrary prose.  The ``open`` status is reserved
    for tokens that DO match a binding's surface but fail the scope/order test
    (so a binding exists for the surface, but not an in-scope one).

    Returns the uses in source order.
    """
    if not text or not bindings:
        return []

    matchers = _build_matchers(bindings)
    if not matchers:
        return []

    # All binding spans, to skip tokens inside a binding site.
    binding_spans = [
        (m.binding.source_span.byte_offset, m.binding_end) for m in matchers
    ]

    uses: list[TermUse] = []
    for tok in _WORD.finditer(text):
        start = tok.start()
        end = tok.end()
        # Skip tokens that fall inside ANY binding's own span (the binding site).
        if any(s <= start < e for s, e in binding_spans):
            continue
        surface = tok.group(0)
        low = surface.lower()

        # Find every matcher whose surface set contains this token.
        hits = [m for m in matchers if low in m.surfaces]
        if not hits:
            continue

        # Partition hits by scope/order: a hit is IN SCOPE only if the use occurs
        # AFTER the binding's span end (a term cannot be used before it is bound).
        in_scope = [m for m in hits if start >= m.binding_end]

        if len(in_scope) == 1:
            m = in_scope[0]
            rule = RULE_MORPH if m.morph_supported else RULE_EXACT_SURFACE
            uses.append(
                TermUse(
                    term_surface=surface,
                    lemma=m.binding.term,
                    binding=m.binding,
                    source_span=SourceSpan(source_file, start, end - start),
                    use_status=STATUS_RESOLVED,
                    rule_id=rule,
                    bindings=(m.binding,),
                )
            )
        elif len(in_scope) > 1:
            uses.append(
                TermUse(
                    term_surface=surface,
                    lemma=in_scope[0].binding.term,
                    binding=None,
                    source_span=SourceSpan(source_file, start, end - start),
                    use_status=STATUS_AMBIGUOUS,
                    rule_id=RULE_MORPH,
                    bindings=tuple(m.binding for m in in_scope),
                )
            )
        else:
            # Matched a binding's surface, but every match is out of scope
            # (use precedes its definition).
            #
            # CONSERVATIVE common-word guard: when EVERY out-of-scope match is a
            # ``tarkoitetaan`` (definitions-section) binding, an occurrence BEFORE
            # the definition is the ordinary-language word, not a forward
            # reference to the definition.  A statute that defines a common term
            # (``auto`` / ``käyttö`` / ``palvelu``) in a definitions section and
            # uses it throughout the operative provisions is normal Finnish
            # drafting — flagging every prior occurrence floods
            # USED_BEFORE_DEFINITION with false positives.  We therefore do NOT
            # emit such a pre-definition occurrence as a use at all (consistent
            # with "we do not fabricate term-shaped uses out of arbitrary prose").
            #
            # ALIAS bindings (parenthetical / jäljempänä) are different: a local
            # short-name used before it is introduced IS a genuine order
            # violation, so a pre-binding use of an alias surface still yields an
            # ``open`` use (the canonical USED_BEFORE_DEFINITION true positive).
            if all(m.binding.binding_kind == BINDING_TARKOITETAAN for m in hits):
                continue
            uses.append(
                TermUse(
                    term_surface=surface,
                    lemma=hits[0].binding.term,
                    binding=None,
                    source_span=SourceSpan(source_file, start, end - start),
                    use_status=STATUS_OPEN,
                    rule_id=RULE_BEFORE_BINDING,
                    bindings=(),
                )
            )

    uses.sort(key=lambda u: u.source_span.byte_offset)
    return uses


__all__ = [
    "RULE_BEFORE_BINDING",
    "RULE_EXACT_SURFACE",
    "RULE_MORPH",
    "STATUS_AMBIGUOUS",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "TermUse",
    "resolve_term_uses",
]
