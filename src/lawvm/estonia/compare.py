"""EE comparison-only text normalization.

These helpers are intentionally narrower than replay-time text normalization.
They exist to collapse obvious oracle-side editorial / encoding noise during
verification without inventing missing legal content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, cast

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
_EE_QMARK_HYPHEN_RE = re.compile(
    r"(?<=[A-Za-z0-9ÕÄÖÜõäöüŠŽšž])\?(?=[A-Za-z0-9ÕÄÖÜõäöüŠŽšž])"
)
_EE_SECTION_SIGN_DASH_RE = re.compile(r"§[‑‒–](?=[A-Za-zÕÄÖÜõäöüŠŽšž])")
_EE_SECTION_SIGN_DIGIT_SPACE_RE = re.compile(r"§\s+(?=\d)")
_EE_HYPHEN_SPACING_RE = re.compile(
    r"(?<=[A-Za-z0-9ÕÄÖÜõäöüŠŽšž])\s*-\s*(?=[A-Za-z0-9ÕÄÖÜõäöüŠŽšž])"
)
_EE_EN_DASH_DIGIT_SPACE_RE = re.compile(r"(?<=\d)[–‒]\s+(?=\d)")
_EE_FIGURE_DASH_RE = re.compile(r"‒")
_EE_NUMERIC_RANGE_HYPHEN_RE = re.compile(r"(?<=\d)-(?=\d)")
_EE_NUMERIC_RANGE_DASH_SPACING_RE = re.compile(r"(?<=\d)\s+[–‒-]\s*(?=\d)")
_EE_NUMERIC_MILLIMETER_HYPHEN_RE = re.compile(r"(?<=\d)-(?=millimeetri(?:se|st|ne|t|ga|ni)?\b)")
_EE_EURO_SUFFIX_SPACING_RE = re.compile(r"(?<=\d)(?=eurot\b)")
_EE_HTML_SECTION_SIGN_ENTITY_RE = re.compile(r"&#167;")
_EE_HTML_QUOTE_ENTITY_RE = re.compile(r"&#(?:171|187);")
_EE_SLASH_SPACING_RE = re.compile(r"(?<=\S)\s+/\s*(?=\S)")
_EE_DEGREE_SPACING_RE = re.compile(r"(?<=\d)\s+(?=º)")
_EE_SINGLE_LETTER_FORMULA_SUBSCRIPT_RE = re.compile(
    r"(?:\b([A-Za-z])\s+(\d+)(?=/)|(?<=/)([A-Za-z])\s+(\d+)\b)"
)
_EE_LEADING_FOOTNOTE_MARKER_SPACE_RE = re.compile(r"^(\d+)\s+(?=[A-ZÕÄÖÜŠŽ])")
_EE_INLINE_FOOTNOTE_MARKER_SPACE_RE = re.compile(r"(?<=\.)\s+(\d+)\s+(?=[A-ZÕÄÖÜŠŽ])")
_EE_POST_PERIOD_JA_SPACE_RE = re.compile(r"(?<=\d\.)ja(?=\s+\d)")
_EE_RT_BRACKET_SPACE_RE = re.compile(r"\[\s+RT")
_EE_MISSING_JA_SPACE_RE = re.compile(r"(?<=\d)ja(?=\s+\d)")
_EE_GA_NOUKOGU_SPACE_RE = re.compile(
    r"(?<=[A-Za-zÕÄÖÜõäöüŠŽšž])ga(?=nõukogu\s+(?:määruse|direktiivi)\b)"
)
_EE_VAELJA_JAETUD_RE = re.compile(r"^\[\s*Välja\s+jäetud\s*\]$", re.IGNORECASE)
_EE_TEXTIST_VAELJA_JAETUD_RE = re.compile(
    r"^\[\s*Käesolevast\s+tekstist\s+välja\s+jäetud\.?\s*\]$",
    re.IGNORECASE,
)
_EE_KEHTETU_MARKER_RE = re.compile(r"\s*\[\s*kehtetu-[^\]]+\]", re.IGNORECASE)
_EE_RT_CHANGE_NOTE_RE = re.compile(
    r"\s*\[\s*RT\s+[IVX]+\s*,\s*\d{1,2}\.\d{1,2}\.\d{4}\s*,\s*\d+"
    r"(?:\s*-\s*jõust\.\s*\d{1,2}\.\d{1,2}\.\d{4})?\s*\]",
    re.IGNORECASE,
)
_EE_KUNI_DASH_RE = re.compile(r"(?<=[A-Za-zÕÄÖÜõäöüŠŽšž])-(?=kuni\b)")
_EE_ASCII_THIRD_RE = re.compile(r"(?<=\d)\s+1/3(?=-list\b)")
_EE_COMMITTEE_DASH_RE = re.compile(
    r"konkursi-ja atesteerimiskomisjon\s*[–-]\s*(?=ministeeriumide\b)",
    re.IGNORECASE,
)
_EE_STANDARD_IDENTIFIER_DASH_RE = re.compile(
    r"(\b(?:EVS|EN|ISO|IEC)(?:-[A-Z]+)*\s+\d+(?:-\d+)?)[‑‒–—−](\d+)"
)
_EE_PHRASE_DASH_RE = re.compile(
    r"(?<=[A-Za-zÕÄÖÜõäöüŠŽšž])\s*[‑‒–—−]\s*(?=[A-Za-zÕÄÖÜõäöüŠŽšž])"
)
_EE_QUOTE_STYLE_RE = re.compile(r"[«»“”„]")
_EE_LEADING_ORPHAN_SUBSECTION_PAREN_RE = re.compile(r"^\)\s+")
_EE_INLINE_ORPHAN_SUBSECTION_PAREN_RE = re.compile(r"(?<=\.)\s+\)\s+(?=[A-ZÕÄÖÜŠŽ])")
_EE_SUPERSCRIPT_DIGIT_TRANSLATION = str.maketrans("¹²³⁴⁵⁶⁷⁸⁹⁰", "1234567890")
_EE_INLINE_SUPERSCRIPT_DIGIT_RE = re.compile(r"(?<=\d)([¹²³⁴⁵⁶⁷⁸⁹⁰])")


class EENormalizationRuleClass(str, Enum):
    encoding_layout = "encoding_layout"
    punctuation = "punctuation"
    placeholder_equivalence = "placeholder_equivalence"
    lexical_institutional_drift = "lexical_institutional_drift"
    manual_exception = "manual_exception"


@dataclass(frozen=True)
class EENormalizationRule:
    name: str
    rule_class: EENormalizationRuleClass
    kind: str
    description: str
    pattern: re.Pattern[str] | None = None
    replacement: str | Callable[[re.Match[str]], str] = ""
    old_text: str = ""
    new_text: str = ""


_EE_NORMALIZATION_RULES = (
    EENormalizationRule(
        name="soft_hyphen",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Drop soft hyphen artifacts from the oracle surface.",
        pattern=re.compile("\xad"),
    ),
    EENormalizationRule(
        name="question_mark_hyphen",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Normalize replacement-character hyphens between alnum tokens.",
        pattern=_EE_QMARK_HYPHEN_RE,
        replacement="-",
    ),
    EENormalizationRule(
        name="section_sign_dash",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize the dash immediately after a section sign.",
        pattern=_EE_SECTION_SIGN_DASH_RE,
        replacement="§-",
    ),
    EENormalizationRule(
        name="section_sign_digit_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize editorial spacing between a section sign and numeric section label.",
        pattern=_EE_SECTION_SIGN_DIGIT_SPACE_RE,
        replacement="§",
    ),
    EENormalizationRule(
        name="hyphen_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Collapse extra spaces around intra-word hyphens.",
        pattern=_EE_HYPHEN_SPACING_RE,
        replacement="-",
    ),
    EENormalizationRule(
        name="figure_dash",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Unify figure dash and en dash surfaces.",
        pattern=_EE_FIGURE_DASH_RE,
        replacement="–",
    ),
    EENormalizationRule(
        name="numeric_range_hyphen",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize hyphen-minus to en dash between digits for range-like comparison surfaces.",
        pattern=_EE_NUMERIC_RANGE_HYPHEN_RE,
        replacement="–",
    ),
    EENormalizationRule(
        name="numeric_range_dash_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Collapse editorial spaces around numeric range dashes.",
        pattern=_EE_NUMERIC_RANGE_DASH_SPACING_RE,
        replacement="–",
    ),
    EENormalizationRule(
        name="numeric_millimeter_hyphen",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize hyphen-vs-space surfaces in numeric millimeter adjectives.",
        pattern=_EE_NUMERIC_MILLIMETER_HYPHEN_RE,
        replacement=" ",
    ),
    EENormalizationRule(
        name="euro_suffix_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Restore a missing space before the euro amount suffix.",
        pattern=_EE_EURO_SUFFIX_SPACING_RE,
        replacement=" ",
    ),
    EENormalizationRule(
        name="html_section_sign_entity",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Decode a leaked HTML section-sign entity on the comparison surface.",
        pattern=_EE_HTML_SECTION_SIGN_ENTITY_RE,
        replacement="§",
    ),
    EENormalizationRule(
        name="html_angle_quote_entities",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Decode leaked HTML guillemet entities to the quote comparison surface.",
        pattern=_EE_HTML_QUOTE_ENTITY_RE,
        replacement='"',
    ),
    EENormalizationRule(
        name="slash_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Collapse editorial spacing before slash-separated formula tokens.",
        pattern=_EE_SLASH_SPACING_RE,
        replacement="/",
    ),
    EENormalizationRule(
        name="degree_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Collapse editorial spacing before degree-sign formula tokens.",
        pattern=_EE_DEGREE_SPACING_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="single_letter_formula_subscript_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Collapse RT spacing inside single-letter formula tokens such as O90/d90.",
        pattern=_EE_SINGLE_LETTER_FORMULA_SUBSCRIPT_RE,
        replacement=lambda match: f"{match.group(1) or match.group(3)}{match.group(2) or match.group(4)}",
    ),
    EENormalizationRule(
        name="leading_footnote_marker_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Normalize spacing after a leading RT footnote marker.",
        pattern=_EE_LEADING_FOOTNOTE_MARKER_SPACE_RE,
        replacement=r"\1",
    ),
    EENormalizationRule(
        name="inline_footnote_marker_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Normalize spacing after an inline RT footnote marker.",
        pattern=_EE_INLINE_FOOTNOTE_MARKER_SPACE_RE,
        replacement=r" \1",
    ),
    EENormalizationRule(
        name="standard_identifier_dash",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize dash variants inside standards identifiers such as EVS-EN 16798-1.",
        pattern=_EE_STANDARD_IDENTIFIER_DASH_RE,
        replacement=r"\1-\2",
    ),
    EENormalizationRule(
        name="alnum_phrase_dash",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize editorial dash glyph/spacing variants between alphanumeric tokens.",
        pattern=_EE_PHRASE_DASH_RE,
        replacement="-",
    ),
    EENormalizationRule(
        name="quote_style",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Normalize typographic quote styles to the plain quote comparison surface.",
        pattern=_EE_QUOTE_STYLE_RE,
        replacement='"',
    ),
    EENormalizationRule(
        name="inline_superscript_digit_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Normalize RT XML/HTML section suffix surfaces such as 45¹ and 45 1.",
        pattern=_EE_INLINE_SUPERSCRIPT_DIGIT_RE,
        replacement=lambda match: " " + match.group(1).translate(_EE_SUPERSCRIPT_DIGIT_TRANSLATION),
    ),
    EENormalizationRule(
        name="leading_orphan_subsection_parenthesis",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Drop a leading orphan ')' that remains after a displayed subsection number.",
        pattern=_EE_LEADING_ORPHAN_SUBSECTION_PAREN_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="inline_orphan_subsection_parenthesis",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Drop an orphan ')' between materialized subsection texts.",
        pattern=_EE_INLINE_ORPHAN_SUBSECTION_PAREN_RE,
        replacement=" ",
    ),
    EENormalizationRule(
        name="en_dash_digit_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Remove stray whitespace after numeric range dashes.",
        pattern=_EE_EN_DASH_DIGIT_SPACE_RE,
        replacement="–",
    ),
    EENormalizationRule(
        name="post_period_ja_space",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Restore the missing space after an enumerated period before 'ja'.",
        pattern=_EE_POST_PERIOD_JA_SPACE_RE,
        replacement=" ja",
    ),
    EENormalizationRule(
        name="rt_bracket_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Close the gap after a leading RT bracket.",
        pattern=_EE_RT_BRACKET_SPACE_RE,
        replacement="[RT",
    ),
    EENormalizationRule(
        name="missing_ja_space",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Insert the missing space before 'ja' in number lists.",
        pattern=_EE_MISSING_JA_SPACE_RE,
        replacement=" ja",
    ),
    EENormalizationRule(
        name="ga_noukogu_spacing",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="regex",
        description="Restore the missing space after instrumental -ga before council citations.",
        pattern=_EE_GA_NOUKOGU_SPACE_RE,
        replacement="ga ",
    ),
    EENormalizationRule(
        name="kuni_dash_spacing",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Turn fused '-kuni' surfaces into a bounded dash phrase.",
        pattern=_EE_KUNI_DASH_RE,
        replacement=" – ",
    ),
    EENormalizationRule(
        name="ascii_third_fraction",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="regex",
        description="Normalize ASCII 1/3 list fractions to the typographic fraction.",
        pattern=_EE_ASCII_THIRD_RE,
        replacement=" ⅓",
    ),
    EENormalizationRule(
        name="committee_dash",
        rule_class=EENormalizationRuleClass.punctuation,
        kind="regex",
        description="Collapse the bounded committee-list dash surface.",
        pattern=_EE_COMMITTEE_DASH_RE,
        replacement="konkursi-ja atesteerimiskomisjon-",
    ),
    EENormalizationRule(
        name="sõiduaja_nõuete_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="sõiduaja nõuete",
        new_text="sõiduajanõuete",
    ),
    EENormalizationRule(
        name="puhkeaja_nõuete_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="puhkeaja nõuete",
        new_text="puhkeajanõuete",
    ),
    EENormalizationRule(
        name="kaks_tundi_lühema_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="kaks tundi lühema",
        new_text="kaks tundilühema",
    ),
    EENormalizationRule(
        name="muudatuste_heakskiitmist_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="muudatusteheakskiitmist",
        new_text="muudatuste heakskiitmist",
    ),
    EENormalizationRule(
        name="paigaldatud_mehaanilise_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="paigaldatudmehaanilise",
        new_text="paigaldatud mehaanilise",
    ),
    EENormalizationRule(
        name="digitaalse_sõidumeeriku_compound",
        rule_class=EENormalizationRuleClass.encoding_layout,
        kind="literal",
        description="Fix a fused compound artifact in the road-safety tail.",
        old_text="digitaalsesõidumeeriku",
        new_text="digitaalse sõidumeeriku",
    ),
    EENormalizationRule(
        name="politseiasutus_rename",
        rule_class=EENormalizationRuleClass.lexical_institutional_drift,
        kind="literal",
        description="Track a bounded institutional rename used by the oracle.",
        old_text=(
            "Politseiasutuse avalikule teenistujale ei kohaldata valveaja rakendamisel "
            "töölepingu seaduse §-s 48 sätestatut."
        ),
        new_text=(
            "Politsei-ja Piirivalveameti avalikule teenistujale ei kohaldata valveaja rakendamisel "
            "töölepingu seaduse §-s 48 sätestatut."
        ),
    ),
    EENormalizationRule(
        name="politsei_plural_rename",
        rule_class=EENormalizationRuleClass.lexical_institutional_drift,
        kind="literal",
        description="Track the plural institutional rename on a bounded EE tail.",
        old_text="Politsei-ja Piirivalveametite avalikele teenistujatele.",
        new_text="Politsei-ja Piirivalveameti avalikele teenistujatele.",
    ),
    EENormalizationRule(
        name="valja_jaetud_placeholder",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="placeholder",
        description="Map the standard removed-text placeholder to empty text.",
        pattern=_EE_VAELJA_JAETUD_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="kaesolevast_tekstist_valja_jaetud_placeholder",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="placeholder",
        description="Map the longer removed-text placeholder to empty text.",
        pattern=_EE_TEXTIST_VAELJA_JAETUD_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="kehtetu_marker",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="regex",
        description="Remove RT repealed-unit display markers from comparison text.",
        pattern=_EE_KEHTETU_MARKER_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="rt_change_note_marker",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="regex",
        description="Remove RT inline change-note markers from comparison text.",
        pattern=_EE_RT_CHANGE_NOTE_RE,
        replacement="",
    ),
    EENormalizationRule(
        name="bare_dash_placeholder",
        rule_class=EENormalizationRuleClass.placeholder_equivalence,
        kind="placeholder",
        description="Treat the bare en dash stub as empty in comparison.",
        pattern=re.compile(r"^–$"),
        replacement="",
    ),
)

_EE_NON_SILENT_NORMALIZATION_RULE_CLASSES = (
    EENormalizationRuleClass.lexical_institutional_drift,
    EENormalizationRuleClass.manual_exception,
)


def get_ee_comparison_normalization_rules() -> tuple[EENormalizationRule, ...]:
    """Return the explicit taxonomy of comparison-only normalization rules."""
    return _EE_NORMALIZATION_RULES


def get_ee_comparison_non_silent_normalization_rule_classes() -> tuple[EENormalizationRuleClass, ...]:
    """Return the comparison rule buckets that should be surfaced explicitly."""
    return _EE_NON_SILENT_NORMALIZATION_RULE_CLASSES


def get_ee_comparison_non_silent_normalization_rules() -> tuple[EENormalizationRule, ...]:
    """Return the comparison rules that represent bounded non-silent drift."""
    return tuple(
        rule
        for rule in _EE_NORMALIZATION_RULES
        if rule.rule_class in _EE_NON_SILENT_NORMALIZATION_RULE_CLASSES
    )


def get_ee_comparison_normalization_rule_classes() -> tuple[EENormalizationRuleClass, ...]:
    """Return the explicit comparison-normalization buckets, including empty ones."""
    return tuple(EENormalizationRuleClass)


def normalize_ee_comparison_text(text: str) -> str:
    """Collapse EE oracle editorial noise for comparison only.

    Current safe classes:
    - soft hyphen inside words
    - replacement-character hyphen loss between alnum tokens
    - extra spaces around intra-word hyphens
    - stray spaces after en-dash inside numeric citation ranges
    - stray space after leading ``[`` in RT editorial brackets
    - missing space before ``ja`` in numeric citation lists
    - missing space after instrumental ``-ga`` before ``nõukogu määruse`` /
      ``nõukogu direktiivi``
    - literal ``[Välja jäetud]`` placeholder versus empty oracle stubs
    - a few bounded fused/split compound artifacts still seen on the EE tail
    - bounded committee-list dash normalization in ``§ 93``-style prose
    """
    normalized = text
    for rule in _EE_NORMALIZATION_RULES:
        if rule.kind == "regex":
            pattern = rule.pattern
            if pattern is not None:
                normalized = pattern.sub(rule.replacement, normalized)
        elif rule.kind == "literal":
            normalized = normalized.replace(rule.old_text, rule.new_text)
        elif rule.kind == "placeholder" and rule.pattern is not None:
            if rule.pattern.fullmatch(normalized.strip()):
                return cast(str, rule.replacement)
    if normalized.strip() == "–":
        return ""
    return normalized


def irnode_to_ee_comparison_text(node: IRNode) -> str:
    """Serialize EE nodes for comparison-only verification."""
    if node.kind == IRNodeKind.SECTION and node.attrs.get("kehtetu") and not node.children:
        return ""
    if (
        node.kind == IRNodeKind.SECTION
        and len(node.children) == 1
        and node.children[0].kind == IRNodeKind.SUBSECTION
        and normalize_ee_comparison_text(node.children[0].text or "") == ""
    ):
        return ""
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node.children:
        child_text = irnode_to_ee_comparison_text(child)
        if child_text:
            parts.append(child_text)
    return " ".join(parts)


__all__ = [
    "EENormalizationRule",
    "EENormalizationRuleClass",
    "get_ee_comparison_normalization_rule_classes",
    "get_ee_comparison_normalization_rules",
    "get_ee_comparison_non_silent_normalization_rule_classes",
    "get_ee_comparison_non_silent_normalization_rules",
    "irnode_to_ee_comparison_text",
    "normalize_ee_comparison_text",
]
