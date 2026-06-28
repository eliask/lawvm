from __future__ import annotations

import re
from typing import Optional

from lawvm.core.semantic_types import StructuralAction, structural_action_from_str
from lawvm.uk_legislation.effects import _is_uk_repealed_by_effect_type


# ---------------------------------------------------------------------------
# Benign application/extent/overlay effect types
# ---------------------------------------------------------------------------
#
# A large class of UK effects-feed verbs describe overlay relationships rather
# than mutations of the affected Act's consolidated text: another instrument
# *applies*, *modifies*, *excludes*, *restricts* or *extends* the affected
# provision for some purpose, *confers a power* to do so, *transfers functions*,
# or records *housekeeping* about an earlier affecting provision. None of these
# insert, replace, or repeal printed text in the base Act, so lowering correctly
# produces no replay operation. Routing them through the terminal
# ``no_supported_action`` *blocking* rejection makes them masquerade as
# ``unhandled_op`` self-consistency defects (~84% of that cluster), when the
# correct outcome is a non-blocking observation: there is genuinely nothing to
# apply.
#
# This set is deliberately conservative. It must NOT capture genuinely textual
# verbs (``substituted``, ``inserted``, ``repealed``, ``omitted``,
# ``words substituted``, ``added``, ``replaced``, ``renumbered``,
# ``amended``/``text amended``, ``sum substituted``, ``table substituted``,
# ``entry substituted`` …) — those are real handlers / fixes owned by other
# lanes and a no-supported-action there is a genuine defect.

# Leading verb phrases (after qualifier/citation-tail stripping) that denote a
# benign non-textual overlay. Matched as whole-phrase prefixes on the stripped
# base type, so e.g. ``applied``, ``applied in part`` and
# ``applied (with modifications)`` all resolve to the ``applied`` family.
_UK_BENIGN_OVERLAY_BASE_VERBS = frozenset(
    {
        "applied",
        "applied with modifications",
        "incorporated",
        "modified",
        "excluded",
        "restricted",
        "extended",
        "disapplied",
        "saved",
        "continued",
        "continues to apply",
        "referred to",
        "construed as one with",
        "having effect as specified",
        "functions transferred",
        "transfer of functions",
        "transfer of powers",
        "functions made exercisable",
        "functions exercisable",
        "function exercisable",
        "certain functions made exercisable",
        "functions cease to be exercisable",
        "functions exercisable jointly",
        "duty to apply imposed",
        "powers of seizure extended",
        "power extended",
        "power to make rules extended",
    }
)

# Verb phrases that are themselves prefixes of a benign overlay family but where
# the trailing words carry the meaning, matched via ``startswith``.
_UK_BENIGN_OVERLAY_PREFIXES = (
    "power to ",  # power to apply/modify/amend/extend/exclude/... conferred
    "amendment to earlier affecting provision",
    "amendment to earlier amending provision",
    "amendment to earlier commencing",
    "amendment to earlier",
    "savings for ",
    "savings for the effects",
    "saving",
    "suspension of earlier affecting provision",
    "expiry of earlier affecting provision",
)

# Citation tails like "by 2017 c. 32, sch. 14 para. 10c(b) (as inserted)" or
# "by s.i. 2001/2599, sch. 1 (as substituted)" follow the overlay verb in the
# feed Type and must be stripped before matching so the leading verb is exposed.
_UK_OVERLAY_CITATION_TAIL_RE = re.compile(
    r"\bby\b.*$",
    flags=re.I | re.S,
)
# Trailing parenthetical qualifiers: jurisdiction (``(ni)``, ``(ew)``, ``(s)``),
# temporal (``(temp.)``, ``(temp. until 31/3/2005)``, ``(retrosp.)``,
# ``(prosp.)``, ``(pt.prosp.)``) and ``(with modifications)`` / ``(conditional)``.
_UK_OVERLAY_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
# ``applied in part``, ``modified in part``, ``extended in part`` etc.
_UK_OVERLAY_IN_PART_RE = re.compile(r"\s+in\s+part\b.*$", flags=re.I)


def _strip_uk_overlay_qualifiers(effect_type: str) -> str:
    """Reduce a verbose overlay feed-type to its leading verb phrase.

    Strips citation tails (``by ...``), trailing parenthetical qualifiers
    (jurisdiction / temporal / ``with modifications``), and an ``in part``
    scope so the leading verb can be matched against the benign overlay set.
    This is purely a *classification* normalizer; it never affects what is
    applied to the tree.
    """
    text = " ".join(str(effect_type or "").strip().lower().split())
    if not text:
        return ""
    text = _UK_OVERLAY_CITATION_TAIL_RE.sub("", text).strip()
    # Strip any number of trailing parenthetical qualifiers.
    prev = None
    while prev != text:
        prev = text
        text = _UK_OVERLAY_TRAILING_PAREN_RE.sub("", text).strip()
    text = _UK_OVERLAY_IN_PART_RE.sub("", text).strip()
    # Normalise "(with modifications)" that survived as bare words and the
    # "applied with modifications" spelling.
    text = re.sub(r"\s+with\s+modifications\b.*$", "", text).strip()
    return text


# ---------------------------------------------------------------------------
# Territorial-extent-with-modifications detector (M4 extent-variant axis)
# ---------------------------------------------------------------------------
#
# An ``extended (<external territory>) (with modifications)`` effect — e.g.
# ``ukpga/2006/46`` Part 28 Ch. 1 ``extended (Isle of Man) (with modifications)``
# by ``uksi/2008/3122`` — does NOT amend the principal (UK) consolidated text.
# It declares that the affected provision *extends to* an external territory
# (Isle of Man, the Channel Islands, Jersey, Guernsey) in a *modified* form,
# i.e. it creates a territorially-scoped VARIANT text. LawVM has no extent-variant
# model yet, so the only source-faithful outcome is to BLOCK the effect to the
# manual-compilation frontier (the M4 extent-variant axis), NOT to lower it.
#
# The hazard this guards against: the modifying Schedule body carried by such an
# effect contains drafting verbs ("omit subsections (4) and (5)", "insert", ...)
# that the empty-effect-type source-action inference would otherwise sniff and
# lower as a structural REPEAL/REPLACE of the affected Part in the principal text
# (the forbidden §2.1 over-repeal direction). Those verbs operate on the *variant*
# extent text, not the principal consolidation.
#
# Distinguished from a PLAIN extent extension ("extended (Isle of Man)" with no
# "with modifications" qualifier): a plain extension carries no variant-creating
# modification body and is left to ordinary lowering / observation. Distinguished
# from GB-internal jurisdiction suffixes ("(s)", "(ni)", "(ew)") which are NOT
# external-territory extents.

# External territories whose extent produces a variant text outside the principal
# UK consolidation. GB-internal jurisdiction suffixes (s/ni/ew/sc) are excluded.
_UK_EXTERNAL_EXTENT_TERRITORIES = (
    "isle of man",
    "channel islands",
    "jersey",
    "guernsey",
)


def is_uk_territorial_extent_with_modifications_effect_type(effect_type: str) -> bool:
    """Return True for an ``extended (<external territory>) (with modifications)``.

    Recognises the territorial-extent-with-modifications variant-creating effect:
    the leading overlay verb is ``extended`` (the extent family), the type names
    an external territory (Isle of Man / Channel Islands / Jersey / Guernsey), and
    a ``with modifications`` qualifier is present. Such an effect declares a
    territorially-scoped VARIANT text; it must not lower as a mutation of the
    principal consolidation. Plain extent extensions (no ``with modifications``)
    return False so they are not blocked.
    """
    raw = " ".join(str(effect_type or "").strip().lower().split())
    if not raw:
        return False
    if "with modifications" not in raw:
        return False
    if not any(territory in raw for territory in _UK_EXTERNAL_EXTENT_TERRITORIES):
        return False
    # Leading verb must be the ``extended`` extent family. ``_strip_uk_overlay_
    # qualifiers`` reduces "extended (Isle of Man) (with modifications)" and
    # "extended in part (...) (with modifications)" to "extended"; guard against
    # the bare ``extended`` token still leading.
    base = _strip_uk_overlay_qualifiers(effect_type)
    return base == "extended" or raw.startswith("extended")


def is_uk_benign_application_overlay_effect_type(effect_type: str) -> bool:
    """Return True for non-textual application/extent/overlay effect verbs.

    These effects (``applied``, ``modified``, ``excluded``, ``extended``,
    ``power to ... conferred``, ``transfer of functions``, ``amendment to
    earlier affecting provision ...`` …) describe overlay relationships and do
    not mutate the affected Act's consolidated text, so a lowering that produces
    no replay operation is *correct* — it should be a non-blocking observation,
    not a blocking ``no_supported_action`` rejection.

    The match is deliberately narrow: genuinely textual verbs (``substituted``,
    ``inserted``, ``repealed``, ``omitted``, ``amended``, ``added``,
    ``replaced``, ``renumbered``, ``words/word ...`` …) are excluded so their
    no-supported-action stays a genuine defect owned elsewhere.
    """
    base = _strip_uk_overlay_qualifiers(effect_type)
    if not base:
        return False
    if base in _UK_BENIGN_OVERLAY_BASE_VERBS:
        return True
    return any(base.startswith(prefix) for prefix in _UK_BENIGN_OVERLAY_PREFIXES)


UK_WORD_LEVEL_EFFECT_TYPES = frozenset(
    {
        "words substituted",
        "word substituted",
        "substituted for words",
        "words repealed",
        "word repealed",
        "words omitted",
        "word omitted",
        "words inserted",
        "word inserted",
        "words added",
        "word added",
    }
)


_UK_EFFECT_TYPE_ACTIONS = {
    "inserted": "insert",
    "word inserted": "insert",
    "words inserted": "insert",
    "entry inserted": "insert",
    "added": "insert",
    "words added": "insert",
    "word added": "insert",
    "repealed": "repeal",
    "entry repealed": "repeal",
    "repealed in part": "replace",
    "revoked in part": "replace",  # OPC drafting synonym: "repealed in part"
    "words repealed": "replace",
    "word repealed": "replace",
    "substituted": "replace",
    "words substituted": "replace",
    "substituted for words": "replace",
    "word substituted": "replace",
    "replaced": "replace",
    "words omitted": "replace",
    "word omitted": "replace",
    "omitted": "repeal",
    "entry omitted": "repeal",
    "ceases to have effect": "repeal",
}


def _uk_effect_type_action(
    effect_type: str,
    *,
    has_metadata_renumber_targets: bool = False,
) -> Optional[str]:
    """Return the canonical lowering action implied by a UK effect type."""
    normalized_effect_type = str(effect_type or "").strip().lower()
    action = _UK_EFFECT_TYPE_ACTIONS.get(normalized_effect_type)
    if action is not None:
        return action
    if normalized_effect_type.startswith("substituted for"):
        return "replace"
    if _is_uk_repealed_by_effect_type(normalized_effect_type):
        return "repeal"
    if has_metadata_renumber_targets:
        return "renumber"
    return None


def _is_uk_word_level_effect_type(effect_type: str) -> bool:
    """Return True for UK effects that describe an intra-node word-level edit."""
    return str(effect_type or "").strip().lower() in UK_WORD_LEVEL_EFFECT_TYPES


# OPC Drafting Guidance 6.9 — non-textual modification verbs. An effect with one
# of these heads is an applicability/extent overlay (the affected text is
# *applied*, *modified*, *excluded*, *restricted*, or *disapplied* with or
# without parentheses-suffix qualifications like ``(temp.)`` / ``(with
# modifications)`` / ``(...) (as inserted)``). The modifier Schedule body carries
# drafting verbs (omit / insert / substitute) but those describe the variant
# body, not the principal-text mutation. Sniffing them into a structural
# repeal/replace of the principal text is the forbidden §1.11 surface predicate.
# Must stay in sync with ``source_adjudication._UK_NON_TEXTUAL_MODIFICATION_EFFECT_VERBS``.
_UK_NON_TEXTUAL_MODIFICATION_EFFECT_VERBS = frozenset(
    {"applied", "excluded", "disapplied", "modified", "restricted"}
)


def is_uk_non_textual_modification_effect_type(effect_type: str) -> bool:
    """Return True if the effect_type's leading verb is a non-textual
    modification verb (applied / excluded / disapplied / modified / restricted).

    Mirrors the closed-vocabulary predicate at
    ``source_adjudication._is_uk_non_textual_modification_effect_type``.
    Parentheses-suffix qualifications like ``(temp.)`` / ``(with
    modifications)`` / ``(as inserted)`` are stripped before checking the head;
    the verb vocabulary is the closed discriminator (§1.11, no free-text
    match).
    """
    norm = str(effect_type or "").strip().lower()
    if not norm:
        return False
    # Strip parentheses-suffix qualifications: "modified (temp.)" → "modified"
    # and "applied by ... (as inserted)" → "applied by ..." → "applied"
    head = norm.split("(", 1)[0].strip()
    first = head.split(maxsplit=1)[0] if head else ""
    return first in _UK_NON_TEXTUAL_MODIFICATION_EFFECT_VERBS


def _to_structural_action(action: str) -> StructuralAction:
    """Map lowering action strings to canonical StructuralAction values.

    Fail-loud: an action string naming no ``StructuralAction`` member raises
    ``ValueError`` rather than silently collapsing to ``META``. Delegates to the
    shared jurisdiction-neutral codec.
    """
    return structural_action_from_str(action, on_unknown="raise")
