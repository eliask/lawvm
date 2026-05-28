"""Pure EE text morphology helpers.

This module holds sentence-level text helpers extracted from the larger Estonia
frontend so Phase 4 file decomposition can start without changing behavior.
"""
from __future__ import annotations

from functools import lru_cache
from itertools import pairwise
import re

_EE_AMETIKOHT_TEENISTUSKOHT_FORMS_RULE = "ee_case_inflected_ametikoht_teenistuskoht_forms"
_EE_OLEMASOLEV_TAHKEL_KUTUSEL_PHRASE_FORMS_RULE = (
    "ee_case_inflected_olemasolev_tahkel_kutusel_phrase_forms"
)
_EE_VOLITATUD_VASTUTAV_FORMS_RULE = "ee_case_inflected_volitatud_vastutav_forms"
_EE_TAOTLUSVOOR_COORDINATION_FORMS_RULE = "ee_case_inflected_taotlusvoor_coordination_forms"
_EE_MIXED_ACRONYM_SUFFIX_CASE_REWRITE_RULE = "ee_case_inflected_mixed_acronym_suffix_case"
_EE_NETO_OMAVAHEND_PREFIX_FORMS_RULE = "ee_case_inflected_neto_omavahend_prefix_forms"
_EE_KYSK_RTK_FORMS_RULE = "ee_case_inflected_kysk_riigi_tugiteenuste_keskus_forms"
_EE_ARUANDED_ARUANNE_FORMS_RULE = "ee_case_inflected_aruanded_aruanne_forms"
_EE_ARUANDED_HEADING_AGREEMENT_RULE = "ee_case_inflected_aruanded_heading_agreement"
_EE_RIIKLIK_REGISTER_INFOSUSTEEM_FORMS_RULE = (
    "ee_case_inflected_riiklik_register_infosusteem_forms"
)


def case_inflected_phrase_source_family(old_text: str | None, new_text: str | None) -> str:
    """Return the owned morphology family for a case-inflected source rewrite."""
    if old_text == "olemasolev tahkel kütusel põhinev kütteseade" and new_text == "olemasolev kütteseade":
        return _EE_OLEMASOLEV_TAHKEL_KUTUSEL_PHRASE_FORMS_RULE
    if old_text == "volitatud" and new_text == "vastutav":
        return _EE_VOLITATUD_VASTUTAV_FORMS_RULE
    if (
        old_text == "teine ja viies taotlusvoor"
        and new_text == "teine, viies ja järgnevad taotlusvoorud"
    ):
        return _EE_TAOTLUSVOOR_COORDINATION_FORMS_RULE
    if old_text == "neto-omavahend" and new_text == "omavahend":
        return _EE_NETO_OMAVAHEND_PREFIX_FORMS_RULE
    if old_text == "KÜSK" and new_text == "Riigi Tugiteenuste Keskus":
        return _EE_KYSK_RTK_FORMS_RULE
    if old_text == "aruanded" and new_text == "aruanne":
        return _EE_ARUANDED_ARUANNE_FORMS_RULE
    if (
        old_text
        and old_text.casefold() == "riiklik pensionikindlustuse register"
        and new_text == "sotsiaalkaitse infosüsteem"
    ):
        return _EE_RIIKLIK_REGISTER_INFOSUSTEEM_FORMS_RULE
    if new_text and re.fullmatch(r"[A-ZÕÄÖÜŠŽ]{2,}-[a-zäöõüšž]+", new_text.strip()):
        return _EE_MIXED_ACRONYM_SUFFIX_CASE_REWRITE_RULE
    return ""


def split_ee_sentences(text: str) -> list[str]:
    """Split EE prose into sentences without breaking on ordinal/date markers."""
    stripped = (text or "").strip()
    if not stripped:
        return []
    parts: list[str] = []
    start = 0
    for match in re.finditer(r'\.\s+', stripped):
        dot_idx = match.start()
        prev_char = stripped[dot_idx - 1] if dot_idx > 0 else ""
        next_char = stripped[match.end()] if match.end() < len(stripped) else ""
        if prev_char.isdigit() and next_char.islower():
            continue
        part = stripped[start: dot_idx + 1].strip()
        if part:
            parts.append(part)
        start = match.end()
    tail = stripped[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def replace_first_sentence(text: str, replacement: str) -> str:
    """Replace the first sentence in text, preserving the rest."""
    stripped = (text or "").strip()
    repl = (replacement or "").strip()
    if not stripped:
        return repl
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return repl
    if len(sentences) == 1:
        return repl
    if not repl:
        return " ".join(sentences[1:]).strip()
    sentences[0] = repl
    return " ".join(sentences).strip()


def replace_sentence(text: str, replacement: str, sentence_index: int) -> str:
    """Replace one sentence in text, preserving the rest."""
    stripped = (text or "").strip()
    repl = (replacement or "").strip()
    if not stripped:
        return repl
    if sentence_index == 0:
        return replace_first_sentence(stripped, repl)
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return repl
    if sentence_index >= 1_000_000:
        sentence_index = len(sentences) - 1
    if sentence_index >= len(sentences):
        return stripped
    if not repl:
        kept = [sentence for idx, sentence in enumerate(sentences) if idx != sentence_index]
        return " ".join(kept).strip()
    sentences[sentence_index] = repl
    return " ".join(sentences).strip()


def replace_sentence_span(text: str, replacement: str, sentence_indexes: list[int]) -> str:
    """Replace a contiguous span of targeted sentences, preserving the rest."""
    stripped = (text or "").strip()
    repl = (replacement or "").strip()
    if not sentence_indexes:
        return stripped
    if len(sentence_indexes) == 1:
        return replace_sentence(stripped, repl, sentence_indexes[0])
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return repl
    normalized = sorted(
        {
            len(sentences) - 1 if index >= 1_000_000 else index
            for index in sentence_indexes
        }
    )
    if not normalized or normalized[0] < 0 or normalized[-1] >= len(sentences):
        return stripped
    if normalized != list(range(normalized[0], normalized[-1] + 1)):
        return stripped
    replacement_sentences = split_ee_sentences(repl) if repl else []
    sentences[normalized[0] : normalized[-1] + 1] = replacement_sentences
    return " ".join(sentences).strip()


def insert_sentence_after(text: str, inserted: str, sentence_index: int) -> str:
    """Insert one sentence after the targeted sentence index in EE prose."""
    stripped = (text or "").strip()
    ins = (inserted or "").strip()
    if not stripped:
        return ins
    if not ins:
        return stripped
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return f"{stripped} {ins}".strip()
    insert_at = min(sentence_index + 1, len(sentences))
    sentences.insert(insert_at, ins)
    return " ".join(sentences).strip()


def insert_sentence_before(text: str, inserted: str, sentence_index: int) -> str:
    """Insert one sentence before the targeted sentence index in EE prose."""
    stripped = (text or "").strip()
    ins = (inserted or "").strip()
    if not stripped:
        return ins
    if not ins:
        return stripped
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return f"{ins} {stripped}".strip()
    insert_at = max(0, min(sentence_index, len(sentences)))
    sentences.insert(insert_at, ins)
    return " ".join(sentences).strip()


def surface_pattern(text: str) -> str:
    """Build a bounded regex that tolerates RT spacing around hyphens."""
    parts: list[str] = []
    for char in text:
        if char.isspace():
            parts.append(r"\s+")
        elif char in "-–‒−":
            parts.append(r"\s*[–‒−-]\s*")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def wrap_word_boundaries(pattern: str, text: str) -> str:
    """Avoid matching bare-word/number replacements inside larger tokens."""
    starts_with_word = bool(re.match(r"[A-Za-zÄÖÕÜäöõüŠŽšž]", text))
    ends_with_word = bool(re.search(r"[A-Za-zÄÖÕÜäöõüŠŽšž]$", text))
    starts_with_digit = bool(re.match(r"\d", text))
    ends_with_digit = bool(re.search(r"\d$", text))
    if not starts_with_word and not ends_with_word and not starts_with_digit and not ends_with_digit:
        return pattern
    wrapped = pattern
    if starts_with_word:
        wrapped = r"(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])" + wrapped
    elif starts_with_digit:
        wrapped = r"(?<!\d)" + wrapped
    if ends_with_word:
        wrapped = wrapped + r"(?![A-Za-zÄÖÕÜäöõüŠŽšž-])"
    elif ends_with_digit:
        wrapped = wrapped + r"(?!\d)"
    return wrapped


def case_preserved_replacement(
    match: re.Match[str],
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Compute one case-preserved replacement string for a regex match."""
    matched = match.group(0)
    if matched.isupper() and new and not _has_mixed_acronym_suffix_case(new):
        return new.upper()
    if matched and matched[0].isupper() and new:
        if preserve_match_capital:
            return new[0].upper() + new[1:]
        if new[0].isupper():
            return new
        prefix = match.string[:match.start()].rstrip()
        if capitalize_sentence_start and (not prefix or prefix[-1] in '.!?("«'):
            return new[0].upper() + new[1:]
        return new
    if (
        matched
        and len(matched) >= 2
        and matched[0] in '"„“«'
        and matched[1].isupper()
        and new
        and not new[0].isupper()
    ):
        prefix = match.string[:match.start()].rstrip()
        if capitalize_sentence_start and (not prefix or prefix[-1] in '.!?("«'):
            return new[0].upper() + new[1:]
    return new


def _has_mixed_acronym_suffix_case(text: str) -> bool:
    """Return true for source-authored acronym case suffixes such as ``EMTAK-i``.

    Python ``str.isupper`` ignores digits and punctuation, so a match like
    ``EMTAK 2008,`` looks all-uppercase. The replacement surface may still own
    a lowercase Estonian suffix after a hyphen, and uppercasing it would mutate
    the source payload.
    """
    return bool(re.search(r"\b[A-ZÕÄÖÜŠŽ]{2,}-[a-zäöõüšž]+\b", text))


def replace_case_preserving(
    text: str,
    old: str,
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Replace all occurrences of old with new, preserving sentence-case starts."""
    pattern = re.compile(
        wrap_word_boundaries(surface_pattern(old), old),
        re.IGNORECASE,
    )

    def _repl(match: re.Match[str]) -> str:
        return case_preserved_replacement(
            match,
            new,
            capitalize_sentence_start=capitalize_sentence_start,
            preserve_match_capital=preserve_match_capital,
        )

    return pattern.sub(_repl, text)


def sentence_indexes_from_notes(note_text: str) -> list[int]:
    """Extract targeted EE sentence indexes from amendment prose notes."""
    indexes: list[int] = []
    ordinal_patterns = [
        (r"esime(?:ne|se|ses|st|sest)", 0),
        (r"tei(?:ne|se|ses|st|sest)", 1),
        (r"kolma(?:s|st|ndat|ndas|ndast)", 2),
        (r"nelja(?:s|nda|ndat|ndas|ndast)", 3),
        (r"vii(?:es|enda|endat|endas|endast)", 4),
        (r"kuu(?:es|enda|endat|endas|endast)", 5),
        (r"seitsme(?:s|nda|ndat|ndas|ndast)", 6),
        (r"kaheks(?:as|anda|andat|andas|andast)", 7),
        (r"viima(?:ne|se|st|ses|sest)", 1_000_000),
    ]
    for (left_pat, left_idx), (right_pat, right_idx) in pairwise(ordinal_patterns):
        if re.search(
            rf"\b{left_pat}\s+ja\s+{right_pat}\s+lause(?:t|s|st|ga)?\b",
            note_text,
        ):
            indexes.extend([left_idx, right_idx])
    for pattern, idx in ordinal_patterns:
        if re.search(rf"\b{pattern}\s+lause(?:t|s|st|ga)?\b", note_text):
            indexes.append(idx)
    return sorted(set(indexes))


def sentence_index_from_notes(note_text: str) -> int | None:
    """Extract the first targeted EE sentence index from amendment prose notes."""
    indexes = sentence_indexes_from_notes(note_text)
    return indexes[0] if indexes else None


def _ee_declension_forms(word: str) -> dict[str, str] | None:
    """Infer a small set of Estonian case forms for bounded text-replace use."""
    if not word:
        return None
    lower = word.lower()
    if lower.endswith("pudel"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
            "pl_part": word + "eid",
            "pl_ine": word + "ites",
            "pl_ela": word + "itest",
            "pl_all": word + "itele",
            "pl_ade": word + "itel",
            "pl_abl": word + "itelt",
            "pl_trn": word + "iteks",
        }
    if lower.endswith("anum"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ad",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower == "vorm":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower == "nimistu":
        stem = word
        plural_stem = word + "t"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": plural_stem + "e",
            "pl_part": stem + "id",
            "pl_ine": plural_stem + "es",
            "pl_ela": plural_stem + "est",
            "pl_all": plural_stem + "ele",
            "pl_ade": plural_stem + "el",
            "pl_abl": plural_stem + "elt",
            "pl_trn": plural_stem + "eks",
        }
    if lower == "meri":
        stem = word[:-1] + "e"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-1] + "d",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word[:-2] + "rre",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "madal":
        return {
            "sg_nom": word,
            "sg_gen": word + "a",
            "sg_part": word + "at",
            "sg_ine": word + "as",
            "sg_ela": word + "ast",
            "sg_ill": word + "asse",
            "sg_all": word + "ale",
            "sg_ade": word + "al",
            "sg_abl": word + "alt",
            "sg_trn": word + "aks",
            "sg_ter": word + "ani",
            "sg_ess": word + "ana",
            "sg_abe": word + "ata",
            "sg_com": word + "aga",
            "pl_nom": word + "ad",
            "pl_gen": word + "ate",
            "pl_part": word + "aid",
            "pl_ine": word + "ates",
            "pl_ela": word + "atest",
            "pl_all": word + "atele",
            "pl_ade": word + "atel",
            "pl_abl": word + "atelt",
            "pl_trn": word + "ateks",
        }
    if lower == "aine":
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("seade"):
        prefix = word[: -len("seade")]
        stem = prefix + "seadme"
        plural_stem = prefix + "seadmete"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": prefix + "seadet",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": prefix + "seadmed",
            "pl_gen": plural_stem,
            "pl_part": prefix + "seadmeid",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower.endswith("ettevõte"):
        stem = word[:-1] + "te"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower == "pere":
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower == "ained":
        stem = word[:-1]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("tõend"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "it",
            "sg_ine": stem + "is",
            "sg_ela": stem + "ist",
            "sg_ill": stem + "isse",
            "sg_all": stem + "ile",
            "sg_ade": stem + "il",
            "sg_abl": stem + "ilt",
            "sg_trn": stem + "iks",
            "sg_ter": stem + "ini",
            "sg_ess": stem + "ina",
            "sg_abe": stem + "ita",
            "sg_com": stem + "iga",
            "pl_nom": stem + "id",
            "pl_gen": stem + "ite",
            "pl_part": word + "eid",
            "pl_ine": stem + "ites",
            "pl_ela": stem + "itest",
            "pl_all": stem + "itele",
            "pl_ade": stem + "itel",
            "pl_abl": stem + "itelt",
            "pl_trn": stem + "iteks",
        }
    if lower.endswith("geen"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word[:-1] + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("vägi"):
        prefix = word[:-4]
        gen_stem = prefix + "väe"
        part_stem = prefix + "väge"
        pl_stem = prefix + "vägede"
        return {
            "sg_nom": word,
            "sg_gen": gen_stem,
            "sg_part": part_stem,
            "sg_ine": gen_stem + "s",
            "sg_ela": gen_stem + "st",
            "sg_ill": prefix + "väkke",
            "sg_all": gen_stem + "le",
            "sg_ade": gen_stem + "l",
            "sg_abl": gen_stem + "lt",
            "sg_trn": gen_stem + "ks",
            "sg_ter": gen_stem + "ni",
            "sg_ess": gen_stem + "na",
            "sg_abe": gen_stem + "ta",
            "sg_com": gen_stem + "ga",
            "pl_nom": prefix + "väed",
            "pl_gen": pl_stem,
            "pl_part": prefix + "vägesid",
            "pl_ine": pl_stem + "s",
            "pl_ela": pl_stem + "st",
            "pl_all": pl_stem + "le",
            "pl_ade": pl_stem + "l",
            "pl_abl": pl_stem + "lt",
            "pl_trn": pl_stem + "ks",
        }
    if lower == "ärakiri":
        stem = word[:-1] + "ja"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-1] + "a",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "veekogu":
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower.endswith("kogu"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("mäng"):
        stem = word + "u"
        plural_stem = word + "ude"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": plural_stem,
            "pl_part": word + "e",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower.endswith("loom"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": word + "i",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("tšintšilja"):
        stem = word
        plural_stem = word + "de"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": plural_stem,
            "pl_part": stem + "sid",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower.endswith("korraldaja"):
        stem = word
        plural_stem = word + "te"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": plural_stem,
            "pl_part": stem + "id",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower == "koht":
        stem = word[:-2] + "ha"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "a",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word + "a",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "liit":
        gen_stem = word[:-1] + "du"
        part_stem = word[:-1] + "tu"
        plural_stem = word[:-1] + "tude"
        return {
            "sg_nom": word,
            "sg_gen": gen_stem,
            "sg_part": part_stem,
            "sg_ine": gen_stem + "s",
            "sg_ela": gen_stem + "st",
            "sg_ill": part_stem,
            "sg_all": gen_stem + "le",
            "sg_ade": gen_stem + "l",
            "sg_abl": gen_stem + "lt",
            "sg_trn": gen_stem + "ks",
            "sg_ter": gen_stem + "ni",
            "sg_ess": gen_stem + "na",
            "sg_abe": gen_stem + "ta",
            "sg_com": gen_stem + "ga",
            "pl_nom": gen_stem + "d",
            "pl_gen": plural_stem,
            "pl_part": word[:-1] + "te",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower == "merematke":
        base = word[:-1]
        return {
            "sg_nom": base,
            "sg_gen": word,
            "sg_part": base + "et",
            "sg_ine": base + "es",
            "sg_ela": base + "est",
            "sg_all": base + "ele",
            "sg_ade": base + "el",
            "sg_abl": base + "elt",
            "sg_trn": base + "eks",
            "sg_ter": base + "eni",
            "sg_ess": base + "ena",
            "sg_abe": base + "eta",
            "sg_com": base + "ega",
            "pl_nom": base + "ed",
            "pl_gen": base + "ete",
            "pl_part": base + "eid",
        }
    if lower == "puksiir":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
        }
    if lower == "pukser":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "eid",
        }
    if lower.endswith("jad"):
        stem = word[:-1]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("id"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "ide",
            "pl_part": stem + "e",
            "pl_ine": stem + "ides",
            "pl_ela": stem + "idest",
            "pl_all": stem + "idele",
            "pl_ade": stem + "idel",
            "pl_abl": stem + "idelt",
            "pl_trn": stem + "ideks",
        }
    if lower.endswith("used"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "i",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("ed"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("õpe"):
        stem = word[:-3] + "õppe"
        part = word[:-3] + "õpet"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": part,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("mine"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("segu"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("olu"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("is"):
        stem = word[:-2] + "ise"
        plural_stem = word[:-2] + "is"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "ist",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("us"):
        stem = word + "e"
        plural_stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word + "se",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("ioon"):
        return {
            "sg_nom": word,
            "sg_gen": word + "i",
            "sg_part": word + "i",
            "sg_ine": word + "is",
            "sg_ela": word + "ist",
            "sg_all": word + "ile",
            "sg_ade": word + "il",
            "sg_abl": word + "ilt",
            "sg_trn": word + "iks",
            "sg_ter": word + "ini",
            "sg_ess": word + "ina",
            "sg_abe": word + "ita",
            "sg_com": word + "iga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("ist"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("amet"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "it",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word + "isse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("juht"):
        stem = word[:-2] + "hi"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": word + "ide",
        }
    if lower.endswith("direktor"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("ministeerium"):
        stem = word + "i"
        plural_stem = word[:-2] + "e"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "id",
            "pl_gen": plural_stem + "ide",
        }
    if lower.endswith("line"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("lane"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("minister"):
        stem = word[:-2] + "ri"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("arst"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("vanem"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "at",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("relv"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ad",
            "pl_gen": word + "ade",
        }
    if lower.endswith("vool"):
        stem = word + "u"
        plural_stem = word + "ude"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": plural_stem,
            "pl_part": word + "usid",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower.endswith("kond"):
        stem = word[:-4] + "konna"
        plural_stem = word[:-4] + "kondade"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem[:-1] + "d",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": plural_stem,
            "pl_part": word[:-4] + "kondi",
            "pl_ine": plural_stem + "s",
            "pl_ela": plural_stem + "st",
            "pl_all": plural_stem + "le",
            "pl_ade": plural_stem + "l",
            "pl_abl": plural_stem + "lt",
            "pl_trn": plural_stem + "ks",
        }
    if lower.endswith("ane"):
        stem = word[:-2] + "se"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("süsteem"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
        }
    if lower.endswith("moon"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ad",
            "pl_gen": word + "ade",
        }
    if lower.endswith("riik"):
        stem = word[:-1] + "gi"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "i",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "i",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
        }
    if lower.endswith("lik"):
        # -lik adjectives: riiklik, avalik-like — strong grade gemination in oblique
        # sg_nom=riiklik, sg_gen=riikliku, sg_part=riiklikku (NOT riiklikut)
        stem = word + "u"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "ku",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": word + "ute",
            "pl_part": word + "uid",
            "pl_ine": word + "utes",
            "pl_ela": word + "utest",
            "pl_all": word + "utele",
            "pl_ade": word + "utel",
            "pl_abl": word + "utelt",
            "pl_trn": word + "uteks",
        }
    if lower.endswith("line"):
        stem = word[:-4] + "se"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("liige"):
        prefix = word[: -len("liige")]
        stem = prefix + "liikme"
        plural_stem = prefix + "liikme"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": prefix + "liikmed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "id",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("nikud"):
        singular_forms = _ee_declension_forms(word[:-2])
        if singular_forms is not None:
            return {
                key: value
                for key, value in singular_forms.items()
                if key.startswith("pl_")
            }
    if lower.endswith("nik"):
        stem = word + "u"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "ku",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": word + "e",
            "pl_part": word + "ke",
            "pl_ine": word + "es",
            "pl_ela": word + "est",
            "pl_all": word + "ele",
            "pl_ade": word + "el",
            "pl_abl": word + "elt",
            "pl_trn": word + "eks",
        }
    if lower.endswith("ik"):
        stem = word + "u"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": word + "ute",
            "pl_part": word + "uid",
            "pl_ine": word + "utes",
            "pl_ela": word + "utest",
            "pl_all": word + "utele",
            "pl_ade": word + "utel",
            "pl_abl": word + "utelt",
            "pl_trn": word + "uteks",
        }
    if lower.endswith("uk"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("ladu"):
        stem = word[:-4] + "lao"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word[:-2] + "ttu",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "d",
            "pl_gen": stem + "de",
        }
    if lower.endswith("ve"):
        # e.g. järelevalve, haldusjärelevalve: gen=X, part=Xt, ine=Xs
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("an"):
        # e.g. järelevalveorgan: gen=Xani->Xi, part=Xit, ine=Xis
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("al"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("oll"):
        # protocol/kontroll-family compounds: protokoll -> protokolli,
        # transfusiooniprotokoll -> transfusiooniprotokolli.
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("register"):
        # register-family compounds: täitemenetlusregister -> täitemenetlusregistri.
        stem = word[:-2] + "ri"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "it",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
            "pl_part": word + "eid",
            "pl_ine": word + "ites",
            "pl_ela": word + "itest",
            "pl_all": word + "itele",
            "pl_ade": word + "itel",
            "pl_abl": word + "itelt",
            "pl_trn": word + "iteks",
        }
    if lower.endswith("i"):
        if lower.endswith("kustuti"):
            plural_stem = word + "te"
            return {
                "sg_nom": word,
                "sg_gen": word,
                "sg_part": word,
                "sg_ine": word + "s",
                "sg_ela": word + "st",
                "sg_all": word + "le",
                "sg_ade": word + "l",
                "sg_abl": word + "lt",
                "sg_trn": word + "ks",
                "sg_ter": word + "ni",
                "sg_ess": word + "na",
                "sg_abe": word + "ta",
                "sg_com": word + "ga",
                "pl_nom": word + "d",
                "pl_gen": plural_stem,
                "pl_part": word[:-1] + "eid",
                "pl_ine": plural_stem + "s",
                "pl_ela": plural_stem + "st",
                "pl_all": plural_stem + "le",
                "pl_ade": plural_stem + "l",
                "pl_abl": plural_stem + "lt",
                "pl_trn": plural_stem + "ks",
            }
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("a"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("o"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    return None


def _ee_phrase_forms(text: str) -> dict[str, str] | None:
    """Infer bounded case forms for a word or a phrase whose last token inflects."""
    stripped = text.strip()

    def _acronym_coordination_forms(value: str) -> dict[str, str] | None:
        match = re.fullmatch(
            r"([A-ZÕÄÖÜŠŽ]{2,})(\s+(?:või|ja|ning)\s+)([A-ZÕÄÖÜŠŽ]{2,})",
            value.strip(),
        )
        if match is None:
            return None
        left, joiner, right = match.groups()

        def both(suffix: str) -> str:
            return f"{left}{suffix}{joiner}{right}{suffix}"

        return {
            "sg_nom": value.strip(),
            "sg_gen": value.strip(),
            "sg_part": both("-d"),
            "sg_ine": both("-s"),
            "sg_ela": both("-st"),
            "sg_all": both("-le"),
            "sg_ade": both("-l"),
            "sg_abl": both("-lt"),
            "sg_trn": both("-ks"),
            "sg_ter": both("-ni"),
            "sg_ess": both("-na"),
            "sg_abe": both("-ta"),
            "sg_com": both("-ga"),
            "pl_nom": both("-d"),
            "pl_gen": both("-te"),
            "pl_part": both("-sid"),
            "pl_ine": both("-tes"),
            "pl_ela": both("-test"),
            "pl_all": both("-tele"),
            "pl_ade": both("-tel"),
            "pl_abl": both("-telt"),
            "pl_trn": both("-teks"),
        }

    acronym_forms = _acronym_coordination_forms(stripped)
    if acronym_forms is not None:
        return acronym_forms

    def _shared_prefix_coordination_forms(segments: list[str], separator: str) -> dict[str, str] | None:
        """Handle shared-prefix coordination such as ``linna- või vallavolikogu``."""
        if len(segments) < 2 or not all(segment.endswith("-") for segment in segments[:-1]):
            return None
        tail_forms = _ee_phrase_forms(segments[-1])
        if tail_forms is None:
            return None
        static_prefix = separator.join(segments[:-1])
        return {
            key: f"{static_prefix}{separator}{value}"
            for key, value in tail_forms.items()
        }

    def _elliptic_genitive_coordination_forms(segments: list[str], separator: str) -> dict[str, str] | None:
        """Handle shared-head coordination such as ``sihtasutuse või juhatuse liige``."""
        if len(segments) < 2 or not all(" " not in segment for segment in segments[:-1]):
            return None
        last_parts = segments[-1].split()
        if len(last_parts) < 2:
            return None
        head_forms = _ee_phrase_forms(last_parts[-1])
        if head_forms is None:
            return None
        prefix = separator.join([*segments[:-1], " ".join(last_parts[:-1])])
        return {
            key: f"{prefix} {value}"
            for key, value in head_forms.items()
        }

    leading_prefix_match = re.match(r"^((?:või|ja|ning|koos)\s+)(.+)$", stripped, re.IGNORECASE)
    if leading_prefix_match is not None:
        prefix = leading_prefix_match.group(1)
        core_forms = _ee_phrase_forms(leading_prefix_match.group(2).strip())
        if core_forms is not None:
            return {
                key: f"{prefix}{value}"
                for key, value in core_forms.items()
            }
    trailing_punct_match = re.match(r"^(.*?)([,:;])$", stripped)
    if trailing_punct_match is not None:
        core_forms = _ee_phrase_forms(trailing_punct_match.group(1).strip())
        if core_forms is not None:
            punctuation = trailing_punct_match.group(2)
            return {
                key: f"{value}{punctuation}"
                for key, value in core_forms.items()
            }
    for conjunction in (" ja", " ning", " või"):
        if stripped.endswith(conjunction):
            core_forms = _ee_phrase_forms(stripped[: -len(conjunction)].strip())
            if core_forms is not None:
                return {
                    key: f"{value}{conjunction}"
                    for key, value in core_forms.items()
                }
    if "," in text:
        segments = [segment.strip() for segment in text.split(",") if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: ", ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
    if " või " in text:
        segments = [segment.strip() for segment in re.split(r"\s+või\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " või ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
            static_prefix_forms = _shared_prefix_coordination_forms(segments, " või ")
            if static_prefix_forms is not None:
                return static_prefix_forms
            elliptic_forms = _elliptic_genitive_coordination_forms(segments, " või ")
            if elliptic_forms is not None:
                return elliptic_forms
    if " ning " in text:
        segments = [segment.strip() for segment in re.split(r"\s+ning\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " ning ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
            static_prefix_forms = _shared_prefix_coordination_forms(segments, " ning ")
            if static_prefix_forms is not None:
                return static_prefix_forms
            elliptic_forms = _elliptic_genitive_coordination_forms(segments, " ning ")
            if elliptic_forms is not None:
                return elliptic_forms
    if " ja " in text:
        segments = [segment.strip() for segment in re.split(r"\s+ja\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " ja ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
            static_prefix_forms = _shared_prefix_coordination_forms(segments, " ja ")
            if static_prefix_forms is not None:
                return static_prefix_forms
            elliptic_forms = _elliptic_genitive_coordination_forms(segments, " ja ")
            if elliptic_forms is not None:
                return elliptic_forms
    if " " not in text:
        return _ee_declension_forms(text)

    def _ee_modifier_forms(token: str) -> dict[str, str] | None:
        if token.endswith("sed"):
            stem = token[:-3]
            return {
                "pl_nom": token,
                "pl_gen": stem + "ste",
                "pl_part": stem + "si",
                "pl_ine": stem + "stes",
                "pl_ela": stem + "stest",
                "pl_all": stem + "stele",
                "pl_ade": stem + "stel",
                "pl_abl": stem + "stelt",
                "pl_trn": stem + "steks",
            }
        if token.endswith("ikud"):
            base = token[:-2]
            return {
                "pl_nom": token,
                "pl_gen": base + "e",
                "pl_part": base + "ke",
                "pl_ine": base + "es",
                "pl_ela": base + "est",
                "pl_all": base + "ele",
                "pl_ade": base + "el",
                "pl_abl": base + "elt",
                "pl_trn": base + "eks",
            }
        if token.endswith("tev"):
            stem = token[:-2]
            return {
                "sg_nom": token,
                "sg_gen": stem + "va",
                "sg_part": stem + "vat",
                "sg_ine": stem + "vas",
                "sg_ela": stem + "vast",
                "sg_all": stem + "vale",
                "sg_ade": stem + "val",
                "sg_abl": stem + "valt",
                "sg_trn": stem + "vaks",
                "sg_ter": stem + "vani",
                "sg_ess": stem + "vana",
                "sg_abe": stem + "vata",
                "sg_com": stem + "vaga",
                "pl_nom": stem + "vad",
                "pl_gen": stem + "vate",
                "pl_part": stem + "vaid",
                "pl_ine": stem + "vates",
                "pl_ela": stem + "vatest",
                "pl_all": stem + "vatele",
                "pl_ade": stem + "vatel",
                "pl_abl": stem + "vatelt",
                "pl_trn": stem + "vateks",
            }
        if token.endswith("tud") or token.endswith("dud"):
            return {
                key: token
                for key in (
                    "sg_nom",
                    "sg_gen",
                    "sg_part",
                    "sg_ine",
                    "sg_ela",
                    "sg_ill",
                    "sg_all",
                    "sg_ade",
                    "sg_abl",
                    "sg_trn",
                    "sg_ter",
                    "sg_ess",
                    "sg_abe",
                    "sg_com",
                    "pl_nom",
                    "pl_gen",
                    "pl_part",
                    "pl_ine",
                    "pl_ela",
                    "pl_all",
                    "pl_ade",
                    "pl_abl",
                    "pl_trn",
                )
            }
        if token.endswith("v"):
            return {
                "sg_nom": token,
                "sg_gen": token + "a",
                "sg_part": token + "at",
                "sg_ine": token + "as",
                "sg_ela": token + "ast",
                "sg_all": token + "ale",
                "sg_ade": token + "al",
                "sg_abl": token + "alt",
                "sg_trn": token + "aks",
                "sg_ter": token + "ani",
                "sg_ess": token + "ana",
                "sg_abe": token + "ata",
                "sg_com": token + "aga",
                "pl_nom": token + "ad",
                "pl_gen": token + "ate",
                "pl_part": token + "aid",
                "pl_ine": token + "ates",
                "pl_ela": token + "atest",
                "pl_all": token + "atele",
                "pl_ade": token + "atel",
                "pl_abl": token + "atelt",
                "pl_trn": token + "ateks",
            }
        if token.endswith("ik") or token.endswith("line"):
            return _ee_declension_forms(token)
        return None

    parts = text.split()
    head_forms = _ee_declension_forms(parts[-1])
    if head_forms is None:
        head_forms = _ee_modifier_forms(parts[-1])
    if head_forms is None:
        return None
    if len(parts) == 1:
        return head_forms

    token_forms: list[dict[str, str]] = []
    for token in parts[:-1]:
        forms = _ee_modifier_forms(token)
        if forms is None:
            forms = {key: token for key in head_forms.keys()}
        token_forms.append(forms)

    shared_keys = set(head_forms.keys())
    for forms in token_forms:
        shared_keys &= set(forms.keys())
    if not shared_keys:
        return None

    combined = {key: " ".join([*(forms[key] for forms in token_forms), head_forms[key]]) for key in shared_keys}
    if token_forms and "sg_gen" in token_forms[-1] and "sg_com" in head_forms:
        prefix_parts = [
            forms["sg_gen"] if idx == len(token_forms) - 1 else forms.get("sg_com", forms.get("sg_gen", ""))
            for idx, forms in enumerate(token_forms)
        ]
        if all(prefix_parts):
            combined["sg_com"] = " ".join([*prefix_parts, head_forms["sg_com"]])
    return combined


def _ee_law_reference_l6ige_forms(text: str) -> dict[str, str] | None:
    """Return common inflected forms for ``§ ... lõige/lõiked ...`` references."""
    cleaned = _ee_normalize_text_replace_surface(text)
    match = re.fullmatch(r"(§\s*[\d\s_]+)\s+(lõige|lõiked)\s+(.+)", cleaned)
    if match is None:
        return None
    prefix, head, tail = match.groups()
    if head == "lõige":
        return {
            "nom": f"{prefix} lõige {tail}",
            "gen": f"{prefix} lõike {tail}",
            "part": f"{prefix} lõiget {tail}",
            "ine": f"{prefix} lõikes {tail}",
            "ela": f"{prefix} lõikest {tail}",
            "ill": f"{prefix} lõikesse {tail}",
            "all": f"{prefix} lõikele {tail}",
            "ade": f"{prefix} lõikel {tail}",
            "abl": f"{prefix} lõikelt {tail}",
            "trn": f"{prefix} lõikeks {tail}",
            "ter": f"{prefix} lõikeni {tail}",
            "ess": f"{prefix} lõikena {tail}",
            "abe": f"{prefix} lõiketa {tail}",
            "com": f"{prefix} lõikega {tail}",
        }
    return {
        "nom": f"{prefix} lõiked {tail}",
        "gen": f"{prefix} lõigete {tail}",
        "part": f"{prefix} lõikeid {tail}",
        "ine": f"{prefix} lõigetes {tail}",
        "ela": f"{prefix} lõigetest {tail}",
        "ill": f"{prefix} lõigetesse {tail}",
        "all": f"{prefix} lõigetele {tail}",
        "ade": f"{prefix} lõigetel {tail}",
        "abl": f"{prefix} lõigetelt {tail}",
        "trn": f"{prefix} lõigeteks {tail}",
    }


def _ee_normalize_text_replace_surface(text: str) -> str:
    """Normalize RT spacing artifacts for text-replace matching/output."""
    normalized = re.sub(r"(?<=\d)\s*[–-]\s*(?=\d)", "–", text)
    normalized = re.sub(
        r"(?<=[A-Za-zÄÖÕÜäöõüŠŽšž])\s*-\s*(?=[A-Za-zÄÖÕÜäöõüŠŽšž])",
        "-",
        normalized,
    )
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r" +([.,;:!?)])", r"\1", normalized)
    return normalized.strip()


@lru_cache(maxsize=4096)
def _ee_text_replace_variants(old: str, new: str, *, case_inflected: bool) -> tuple[tuple[str, str], ...]:
    """Build replacement pairs, longest-first, for bounded case-aware rewrites."""
    variants: dict[str, str] = {}

    def _add_vts_operator_forms() -> None:
        """Own RT's 2026 VTS terminology rewrite with explicit case forms."""
        if not case_inflected:
            return
        form_sets = {
            ("VTS operaator", "laevaliiklusjuht"): {
                "VTS operaator": "laevaliiklusjuht",
                "VTS operaatori": "laevaliiklusjuhi",
                "VTS operaatorit": "laevaliiklusjuhti",
                "VTS operaatorisse": "laevaliiklusjuhti",
                "VTS operaatoris": "laevaliiklusjuhis",
                "VTS operaatorist": "laevaliiklusjuhist",
                "VTS operaatorile": "laevaliiklusjuhile",
                "VTS operaatoril": "laevaliiklusjuhil",
                "VTS operaatorilt": "laevaliiklusjuhilt",
                "VTS operaatoriks": "laevaliiklusjuhiks",
                "VTS operaatorini": "laevaliiklusjuhini",
                "VTS operaatorina": "laevaliiklusjuhina",
                "VTS operaatorita": "laevaliiklusjuhita",
                "VTS operaatoriga": "laevaliiklusjuhiga",
                "VTS operaatorid": "laevaliiklusjuhid",
                "VTS operaatorite": "laevaliiklusjuhtide",
            },
            ("VTS vanemoperaator", "vanemlaevaliiklusjuht"): {
                "VTS vanemoperaator": "vanemlaevaliiklusjuht",
                "VTS vanemoperaatori": "vanemlaevaliiklusjuhi",
                "VTS vanemoperaatorit": "vanemlaevaliiklusjuhti",
                "VTS vanemoperaatorisse": "vanemlaevaliiklusjuhti",
                "VTS vanemoperaatoris": "vanemlaevaliiklusjuhis",
                "VTS vanemoperaatorist": "vanemlaevaliiklusjuhist",
                "VTS vanemoperaatorile": "vanemlaevaliiklusjuhile",
                "VTS vanemoperaatoril": "vanemlaevaliiklusjuhil",
                "VTS vanemoperaatorilt": "vanemlaevaliiklusjuhilt",
                "VTS vanemoperaatoriks": "vanemlaevaliiklusjuhiks",
                "VTS vanemoperaatorini": "vanemlaevaliiklusjuhini",
                "VTS vanemoperaatorina": "vanemlaevaliiklusjuhina",
                "VTS vanemoperaatorita": "vanemlaevaliiklusjuhita",
                "VTS vanemoperaatoriga": "vanemlaevaliiklusjuhiga",
                "VTS vanemoperaatorid": "vanemlaevaliiklusjuhid",
                "VTS vanemoperaatorite": "vanemlaevaliiklusjuhtide",
            },
        }
        for old_form, new_form in form_sets.get((old, new), {}).items():
            variants.setdefault(old_form, new_form)

    def _add_ametikoht_teenistuskoht_forms() -> None:
        """Own ametikoht -> teenistuskoht forms for RT's 2013 rescue-service rewrite."""
        if not case_inflected or (old, new) != ("ametikoht", "teenistuskoht"):
            return
        form_pairs = {
            "ametikoht": "teenistuskoht",
            "ametikoha": "teenistuskoha",
            "ametikohta": "teenistuskohta",
            "ametikohtade": "teenistuskohtade",
            "ametikohti": "teenistuskohti",
            "ametikohtades": "teenistuskohtades",
            "ametikohtadest": "teenistuskohtadest",
            "ametikohtadele": "teenistuskohtadele",
            "ametikohtadel": "teenistuskohtadel",
            "ametikohtadelt": "teenistuskohtadelt",
            "ametikohtadeks": "teenistuskohtadeks",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_olemasolev_tahkel_kutusel_forms() -> None:
        """Own RT's 2025 kütteseade phrase contraction with explicit forms."""
        if (
            not case_inflected
            or old != "olemasolev tahkel kütusel põhinev kütteseade"
            or new != "olemasolev kütteseade"
        ):
            return
        form_pairs = {
            "olemasolev tahkel kütusel põhinev kütteseade": "olemasolev kütteseade",
            "olemasoleva tahkel kütusel põhineva kütteseadme": "olemasoleva kütteseadme",
            "olemasolevat tahkel kütusel põhinevat kütteseadet": "olemasolevat kütteseadet",
            "olemasolevas tahkel kütusel põhinevas kütteseadmes": "olemasolevas kütteseadmes",
            "olemasolevast tahkel kütusel põhinevast kütteseadmest": "olemasolevast kütteseadmest",
            "olemasolevale tahkel kütusel põhinevale kütteseadmele": "olemasolevale kütteseadmele",
            "olemasoleval tahkel kütusel põhineval kütteseadmel": "olemasoleval kütteseadmel",
            "olemasolevalt tahkel kütusel põhinevalt kütteseadmelt": "olemasolevalt kütteseadmelt",
            "olemasolevaks tahkel kütusel põhinevaks kütteseadmeks": "olemasolevaks kütteseadmeks",
            "olemasoleva tahkel kütusel põhineva kütteseadmena": "olemasoleva kütteseadmena",
            "olemasoleva tahkel kütusel põhineva kütteseadmega": "olemasoleva kütteseadmega",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_volitatud_vastutav_forms() -> None:
        """Own RT's 2026 volitatud -> vastutav rewrite with explicit forms."""
        if not case_inflected or old != "volitatud" or new != "vastutav":
            return
        form_pairs = {
            "volitatud töötlejale": "vastutavale töötlejale",
            "volitatud töötleja vahelises": "vastutava töötleja vahelises",
            "Volitatud töötlejal": "Vastutaval töötlejal",
            "volitatud töötlejal": "vastutaval töötlejal",
            "volitatud": "vastutav",
            "volitatu": "vastutava",
            "volitatut": "vastutavat",
            "volitatus": "vastutavas",
            "volitatust": "vastutavast",
            "volitatule": "vastutavale",
            "volitatul": "vastutaval",
            "volitatult": "vastutavalt",
            "volitatuks": "vastutavaks",
            "volitatuna": "vastutavana",
            "volitatuga": "vastutavaga",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_reagent_reaktiiv_forms() -> None:
        """Own reagent -> reaktiiv forms for RT's 2023 lab terminology rewrite."""
        if not case_inflected or old != "reagent" or new != "reaktiiv":
            return
        form_pairs = {
            "reagent": "reaktiiv",
            "reagendi": "reaktiivi",
            "reagenti": "reaktiivi",
            "reagendisse": "reaktiivi",
            "reagendis": "reaktiivis",
            "reagendist": "reaktiivist",
            "reagendile": "reaktiivile",
            "reagendil": "reaktiivil",
            "reagendilt": "reaktiivilt",
            "reagendiks": "reaktiiviks",
            "reagendina": "reaktiivina",
            "reagendiga": "reaktiiviga",
            "reagendid": "reaktiivid",
            "reagentide": "reaktiivide",
            "reagente": "reaktiive",
            "reagentidele": "reaktiividele",
            "reagentidega": "reaktiividega",
            "anti-D reagenti": "anti-D reaktiivi",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_taotlusvoor_coordination_forms() -> None:
        """Own RT's coordinated taotlusvoor phrase agreement in a 2015 global rewrite."""
        if (
            not case_inflected
            or old != "teine ja viies taotlusvoor"
            or new != "teine, viies ja järgnevad taotlusvoorud"
        ):
            return
        form_pairs = {
            "teine ja viies taotlusvoor": "teine, viies ja järgnev taotlusvoor",
            "teise ja viienda taotlusvooru": "teise, viienda ja järgneva taotlusvooru",
        }
        for old_form, new_form in form_pairs.items():
            variants[old_form] = new_form

    def _add_neto_omavahend_prefix_forms() -> None:
        """Own neto-omavahend -> omavahend prefix removal with explicit forms."""
        if not case_inflected or old != "neto-omavahend" or new != "omavahend":
            return
        form_pairs = {
            "neto-omavahend": "omavahend",
            "neto-omavahendi": "omavahendi",
            "neto-omavahendit": "omavahendit",
            "neto-omavahendisse": "omavahendisse",
            "neto-omavahendis": "omavahendis",
            "neto-omavahendist": "omavahendist",
            "neto-omavahendile": "omavahendile",
            "neto-omavahendil": "omavahendil",
            "neto-omavahendilt": "omavahendilt",
            "neto-omavahendiks": "omavahendiks",
            "neto-omavahendina": "omavahendina",
            "neto-omavahendiga": "omavahendiga",
            "neto-omavahendid": "omavahendid",
            "neto-omavahendite": "omavahendite",
            "neto-omavahendeid": "omavahendeid",
            "neto-omavahendites": "omavahendites",
            "neto-omavahenditest": "omavahenditest",
            "neto-omavahenditele": "omavahenditele",
            "neto-omavahenditel": "omavahenditel",
            "neto-omavahenditelt": "omavahenditelt",
            "neto-omavahenditeks": "omavahenditeks",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_kysk_riigi_tugiteenuste_keskus_forms() -> None:
        """Own KÜSK abbreviation expansion with explicit Estonian case forms."""
        if not case_inflected or old != "KÜSK" or new != "Riigi Tugiteenuste Keskus":
            return
        form_pairs = {
            "KÜSK": "Riigi Tugiteenuste Keskus",
            "KÜSKi": "Riigi Tugiteenuste Keskuse",
            "KÜSKit": "Riigi Tugiteenuste Keskust",
            "KÜSKisse": "Riigi Tugiteenuste Keskusesse",
            "KÜSKis": "Riigi Tugiteenuste Keskuses",
            "KÜSKist": "Riigi Tugiteenuste Keskusest",
            "KÜSKile": "Riigi Tugiteenuste Keskusele",
            "KÜSKil": "Riigi Tugiteenuste Keskusel",
            "KÜSKilt": "Riigi Tugiteenuste Keskuselt",
            "KÜSKiks": "Riigi Tugiteenuste Keskuseks",
            "KÜSKina": "Riigi Tugiteenuste Keskusena",
            "KÜSKiga": "Riigi Tugiteenuste Keskusega",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_aruanded_aruanne_forms() -> None:
        """Own aruanded -> aruanne plural-to-singular report forms."""
        if not case_inflected or old != "aruanded" or new != "aruanne":
            return
        form_pairs = {
            "aruanded": "aruanne",
            "aruannete": "aruande",
            "aruandeid": "aruannet",
            "aruannetes": "aruandes",
            "aruannetesse": "aruandesse",
            "aruannetest": "aruandest",
            "aruannetele": "aruandele",
            "aruannetel": "aruandel",
            "aruannetelt": "aruandelt",
            "aruanneteks": "aruandeks",
            "aruannetena": "aruandena",
            "aruannetega": "aruandega",
        }
        for old_form, new_form in form_pairs.items():
            variants.setdefault(old_form, new_form)

    def _add_riiklik_register_infosusteem_forms() -> None:
        """Own the 2016 register-to-information-system rewrite with explicit phrase forms."""
        if (
            not case_inflected
            or old.casefold() != "riiklik pensionikindlustuse register"
            or new != "sotsiaalkaitse infosüsteem"
        ):
            return
        lower_pairs = {
            "riiklik pensionikindlustuse register": "sotsiaalkaitse infosüsteem",
            "riikliku pensionikindlustuse registri": "sotsiaalkaitse infosüsteemi",
            "riiklikku pensionikindlustuse registrit": "sotsiaalkaitse infosüsteemi",
            "riikliku pensionikindlustuse registrisse": "sotsiaalkaitse infosüsteemi",
            "riiklikku pensionikindlustuse registrisse": "sotsiaalkaitse infosüsteemi",
            "riiklikus pensionikindlustuse registris": "sotsiaalkaitse infosüsteemis",
            "riiklikust pensionikindlustuse registrist": "sotsiaalkaitse infosüsteemist",
            "riiklikule pensionikindlustuse registrile": "sotsiaalkaitse infosüsteemile",
            "riiklikul pensionikindlustuse registril": "sotsiaalkaitse infosüsteemil",
            "riiklikult pensionikindlustuse registrilt": "sotsiaalkaitse infosüsteemilt",
        }
        for old_form, new_form in lower_pairs.items():
            variants.setdefault(old_form, new_form)
            variants.setdefault(old_form.capitalize(), new_form.capitalize())

    def _strip_wrapping_quotes(surface: str) -> str | None:
        stripped = surface.strip()
        if len(stripped) < 2:
            return None
        quote_pairs = (("„", "”"), ("„", "“"), ("“", "”"), ('"', '"'), ("«", "»"))
        for left_quote, right_quote in quote_pairs:
            if stripped.startswith(left_quote) and stripped.endswith(right_quote):
                inner = stripped[len(left_quote) : -len(right_quote)].strip()
                return inner or None
        return None

    def _left_branch_genitive_to_nominative_variant(surface: str) -> str | None:
        if " või " not in surface:
            return None
        left, right = surface.split(" või ", 1)
        left_parts = left.split()
        if not left_parts:
            return None
        head = left_parts[-1]
        if not head.endswith("use"):
            return None
        nominative = f"{head[:-3]}us"
        if _ee_declension_forms(nominative) is None:
            return None
        return " ".join([*left_parts[:-1], nominative, "või", right])

    def _left_branch_shared_genitive_elided_variant(surface: str) -> str | None:
        if " või " not in surface:
            return None
        left, right = surface.split(" või ", 1)
        left_parts = left.split()
        right_parts = right.split()
        if len(left_parts) < 2 or not right_parts or left_parts[-1].casefold() != right_parts[0].casefold():
            return None
        return " ".join([*left_parts[:-1], "või", right])

    def _authorized_member_three_branch_variant(surface: str) -> str | None:
        match = re.fullmatch(
            r"(?P<prefix>.+?)\s+(?P<organ>[A-Za-zÄÖÕÜäöõüŠŽšž-]+use)\s+või\s+"
            r"(?P=organ)\s+liikme\s+poolt\s+volitatud\s+isik",
            surface,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        organ = match.group("organ")
        organ_nom = f"{organ[:-3]}us"
        if _ee_declension_forms(organ_nom) is None:
            return None
        return (
            f"{match.group('prefix')} {organ_nom} või {organ} liige või "
            f"{organ} liikme poolt volitatud isik"
        )

    if old:
        variants[old] = new
        _add_vts_operator_forms()
        _add_ametikoht_teenistuskoht_forms()
        _add_olemasolev_tahkel_kutusel_forms()
        _add_volitatud_vastutav_forms()
        _add_reagent_reaktiiv_forms()
        _add_taotlusvoor_coordination_forms()
        _add_neto_omavahend_prefix_forms()
        _add_kysk_riigi_tugiteenuste_keskus_forms()
        _add_aruanded_aruanne_forms()
        _add_riiklik_register_infosusteem_forms()
        old_norm = _ee_normalize_text_replace_surface(old)
        new_norm = _ee_normalize_text_replace_surface(new)
        if old_norm and old_norm not in variants:
            variants[old_norm] = new_norm
        if old_norm and new_norm.lower().startswith(old_norm.lower()):
            genitive_plural_old = _ee_genitive_singular_modifier_phrase_to_plural(old_norm)
            if genitive_plural_old and genitive_plural_old not in variants:
                variants[genitive_plural_old] = f"{genitive_plural_old}{new_norm[len(old_norm):]}"
        if any(char in old for char in "„“”"):
            guillemet_old = old.replace("„", "«").replace("“", "»").replace("”", "»")
            guillemet_new = new.replace("„", "«").replace("“", "»").replace("”", "»")
            if guillemet_old and guillemet_old not in variants:
                variants[guillemet_old] = guillemet_new
        unquoted_old = _strip_wrapping_quotes(old)
        if unquoted_old is not None:
            quoted_wrappers = (('"', '"'), ("„", "”"), ("„", "“"), ("“", "”"), ("«", "»"))
            for old_form, new_form in _ee_text_replace_variants(
                unquoted_old,
                new,
                case_inflected=case_inflected,
            ):
                if old_form and old_form not in variants:
                    variants[old_form] = new_form
                for left_quote, right_quote in quoted_wrappers:
                    quoted_old_form = f"{left_quote}{old_form}{right_quote}"
                    if quoted_old_form not in variants:
                        variants[quoted_old_form] = new_form
    citation_match = re.fullmatch(r"§\s+(.+)", old.strip())
    new_citation_match = re.fullmatch(r"§\s+(.+)", new.strip())
    if citation_match is not None and new_citation_match is not None:
        old_ref = citation_match.group(1).strip()
        new_ref = new_citation_match.group(1).strip()
        for suffix in ("s", "st", "le", "l", "lt", "ni", "na", "ta", "ga"):
            old_variant = f"§-{suffix} {old_ref}"
            if old_variant not in variants:
                variants[old_variant] = f"§-{suffix} {new_ref}"
    if old == "teabevaldajale" and new == "töötlevale üksusele ja juurdepääsuõigusega füüsilisele isikule":
        special_pairs = {
            "töötlevale üksusele": new,
        }
        for old_form, new_form in special_pairs.items():
            if old_form not in variants:
                variants[old_form] = new_form
    if old == "teabevaldaja" and new == "töötlev üksus ja juurdepääsuõigusega füüsiline isik":
        special_pairs = {
            "töötlev üksus": new,
        }
        for old_form, new_form in special_pairs.items():
            if old_form not in variants:
                variants[old_form] = new_form
    if old == "pedagoogidele" and new == "teistele õppe- ja kasvatusalal töötavatele isikutele":
        special_pairs = {
            "teistele pedagoogidele": "teistele õppe- ja kasvatusalal töötavatele isikutele",
        }
        for old_form, new_form in special_pairs.items():
            if old_form not in variants:
                variants[old_form] = new_form
    if case_inflected:
        old_l6ige_forms = _ee_law_reference_l6ige_forms(old)
        new_l6ige_forms = _ee_law_reference_l6ige_forms(new)
        if old_l6ige_forms is not None and new_l6ige_forms is not None:
            shared_case_keys = set(old_l6ige_forms) & set(new_l6ige_forms)
            for key in shared_case_keys:
                old_form = old_l6ige_forms[key]
                new_form = new_l6ige_forms[key]
                if old_form and new_form and old_form not in variants:
                    variants[old_form] = new_form
        old_forms = _ee_phrase_forms(old)
        new_forms = _ee_phrase_forms(new)
        numeric_suffix_match = re.fullmatch(
            re.escape(old.strip()) + r"\s+(\d[\d\s_]*)",
            new.strip(),
            flags=re.IGNORECASE,
        )
        if numeric_suffix_match is not None and old_forms is not None:
            suffix = numeric_suffix_match.group(1).strip()
            for old_form in old_forms.values():
                if old_form and old_form not in variants:
                    variants[old_form] = f"{old_form} {suffix}"
        if new == "":
            stripped_old = old.strip()
            for conj in (" või", " ja"):
                if stripped_old.endswith(conj):
                    base_forms = _ee_phrase_forms(stripped_old[: -len(conj)].strip())
                    if base_forms is not None:
                        for old_form in base_forms.values():
                            candidate = f"{old_form}{conj}"
                            if candidate and candidate not in variants:
                                variants[candidate] = ""
            if old_forms is not None:
                for old_form in old_forms.values():
                    if old_form and old_form not in variants:
                        variants[old_form] = ""
            tail_match = re.search(
                r"^(.*?)([A-Za-zÄÖÕÜäöõüŠŽšž-]+)$",
                old,
            )
            if tail_match is not None:
                prefix = tail_match.group(1)
                head = tail_match.group(2)
                head_forms = _ee_declension_forms(head)
                if head_forms is not None:
                    for head_form in head_forms.values():
                        candidate = f"{prefix}{head_form}"
                        if candidate and candidate not in variants:
                            variants[candidate] = ""
        elif old_forms is not None and new_forms is not None:
            nominative_left_branch = _left_branch_genitive_to_nominative_variant(old)
            if nominative_left_branch and new_forms.get("sg_nom") and nominative_left_branch not in variants:
                variants[nominative_left_branch] = new_forms["sg_nom"]
            elided_left_branch = _left_branch_shared_genitive_elided_variant(old)
            if elided_left_branch and new_forms.get("sg_nom") and elided_left_branch not in variants:
                variants[elided_left_branch] = new_forms["sg_nom"]
            authorized_member_variant = _authorized_member_three_branch_variant(old)
            if authorized_member_variant and new_forms.get("sg_nom") and authorized_member_variant not in variants:
                variants[authorized_member_variant] = new_forms["sg_nom"]
            preferred_keys = (
                "sg_nom",
                "sg_gen",
                "sg_part",
                "sg_ine",
                "sg_ela",
                "sg_ill",
                "sg_all",
                "sg_ade",
                "sg_abl",
                "sg_trn",
                "sg_ter",
                "sg_ess",
                "sg_abe",
                "sg_com",
                "pl_nom",
                "pl_gen",
                "pl_part",
                "pl_ine",
                "pl_ela",
                "pl_all",
                "pl_ade",
                "pl_abl",
                "pl_trn",
            )
            for key in preferred_keys:
                old_form = old_forms.get(key)
                new_form = new_forms.get(key)
                if old_form and new_form and old_form not in variants:
                    variants[old_form] = new_form
                if old_form and new_form:
                    old_form_norm = _ee_normalize_text_replace_surface(old_form)
                    new_form_norm = _ee_normalize_text_replace_surface(new_form)
                    if old_form_norm and old_form_norm not in variants:
                        variants[old_form_norm] = new_form_norm
            if old == "madalik" and new == "madal":
                shallow_forms = {
                    "madalile": "madalale",
                    "madalil": "madalal",
                }
                for old_form, new_form in shallow_forms.items():
                    if old_form not in variants:
                        variants[old_form] = new_form
        if old == "amet" and new == "ametikoht":
            office_position_forms = {
                "ametisse": "ametikohale",
                "ametist": "ametikohalt",
            }
            for old_form, new_form in office_position_forms.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "õppekogunemine" and new == "reservteenistus":
            reserve_service_forms = {
                "õppekogunemise": "reservteenistuse",
                "õppekogunemisel": "reservteenistuses",
            }
            for old_form, new_form in reserve_service_forms.items():
                variants[old_form] = new_form
        if (
            old == "rahvusvaheline konventsioon tsiviilvastutusest naftareostuskahjude eest, 1969"
            and new
            == "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvaheline konventsioon"
        ):
            special_pairs = {
                "rahvusvahelise konventsiooni tsiviilvastutusest naftareostuskahjude eest, 1969": (
                    "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvahelise konventsiooni"
                ),
                "rahvusvahelisest konventsioonist tsiviilvastutusest naftareostuskahjude eest, 1969": (
                    "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvahelisest konventsioonist"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "laeva omanik," and new == "":
            for old_form in ("laeva omanikul,", "laeva omaniku,", "laeva omanik,"):
                if old_form not in variants:
                    variants[old_form] = ""
        if old == "sõjarelvad, laskemoon" and new == "sõjarelv, relvasüsteem, sõjarelva laskemoon":
            special_pairs = {
                "sõjarelvade, laskemoona": "sõjarelvade, relvasüsteemi, sõjarelva laskemoona",
                "sõjarelvi, laskemoona": "sõjarelvi, relvasüsteemi, sõjarelva laskemoona",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old
            in {
                "asutus, põhiseaduslik institutsioon või juriidiline isik",
                "asutus, põhiseaduslik institutsioon ja juriidiline isik",
            }
            and new == "töötlev üksus"
        ):
            joiner = " või " if " või " in old else " ja "
            special_pairs = {
                f"asutuse, põhiseadusliku institutsiooni{joiner}juriidilise isiku": "töötleva üksuse",
                f"asutusele, põhiseaduslikule institutsioonile{joiner}juriidilisele isikule": "töötlevale üksusele",
                f"asutusel, põhiseaduslikul institutsioonil{joiner}juriidilisel isikul": "töötleval üksusel",
                f"asutuses, põhiseaduslikus institutsioonis{joiner}juriidilises isikus": "töötlevas üksuses",
                "asutuste, põhiseaduslike institutsioonide ning füüsiliste ja juriidiliste isikute": "töötlevate üksuste",
                "asutusi, põhiseaduslikke institutsioone ning füüsilisi ja juriidilisi isikuid": "töötlevaid üksusi",
                "asutustele, põhiseaduslikele institutsioonidele ning füüsilistele ja juriidilistele isikutele": "töötlevatele üksustele",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "teabevaldaja" and new == "töötlev üksus":
            special_pairs = {
                "teabevaldaja turvaala": "töötleva üksuse turvaala",
                "teabevaldaja turvaalal": "töötleva üksuse turvaalal",
                "teabevaldaja arhiivis": "töötleva üksuse arhiivis",
                "teabevaldaja seadusest": "töötleva üksuse seadusest",
                "teabevaldaja kohustused": "töötleva üksuse kohustused",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "teabevaldaja" and new == "töötlev üksus ja juurdepääsuõigusega füüsiline isik":
            special_pairs = {
                "töötlev üksus": new,
                "töötleva üksuse": "töötleva üksuse ja juurdepääsuõigusega füüsilise isiku",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "abikaasa" and new == "abikaasa või registreeritud elukaaslane":
            special_pairs = {
                "teise abikaasa": "teise abikaasa või registreeritud elukaaslase",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "ametikoht, millel töötamise" and new == "töö- või ametikoht, mille ülesannete täitmise":
            special_pairs = {
                "ametikohad, millel töötamise": "töö- või ametikohad, mille ülesannete täitmise",
                "ametikohal, millel töötamise": "töö- või ametikohal, mille ülesannete täitmise",
                "ametikohale, millel töötamise": "töö- või ametikohale, mille ülesannete täitmise",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "Kaitsevägi" and new == "Kaitseministeeriumi valitsemisala valitsusasutus":
            special_pairs = {
                "Kaitseväe kaudu": "Kaitseministeeriumi valitsemisala valitsusasutuse kaudu",
                "Kaitseväge": "Kaitseministeeriumi valitsemisala valitsusasutust",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old == "kantserogeenid või mutageenid"
            and new == "kantserogeenid, mutageenid või reproduktiivtoksilised ained"
        ):
            special_pairs = {
                "kantserogeenide või mutageenidega": (
                    "kantserogeenide, mutageenide või reproduktiivtoksiliste ainetega"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old == "kantserogeenid ja mutageenid"
            and new == "kantserogeenid, mutageenid ja reproduktiivtoksilised ained"
        ):
            special_pairs = {
                "kantserogeenide ja mutageenidega": (
                    "kantserogeenide, mutageenide ja reproduktiivtoksiliste ainetega"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old == "kantserogeen või mutageen"
            and new == "kantserogeen, mutageen või reproduktiivtoksiline aine"
        ):
            special_pairs = {
                "kantserogeeni või mutageeni": (
                    "kantserogeeni, mutageeni või reproduktiivtoksilise aine"
                ),
                "kantserogeeni või mutageeniga": (
                    "kantserogeeni, mutageeni või reproduktiivtoksilise ainega"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old == "Euroopa Komisjoni arengukoostööprojekt"
            and new == "Euroopa Komisjoni arengukoostöö- ja humanitaarabiprojekt"
        ):
            special_pairs = {
                "Euroopa Komisjoni arengukoostööprojekti": (
                    "Euroopa Komisjoni arengukoostöö- ja humanitaarabiprojekti"
                ),
                "Euroopa Komisjoni arengukoostööprojektide": (
                    "Euroopa Komisjoni arengukoostöö- ja humanitaarabiprojektide"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        for old_form, new_form in tuple(variants.items()):
            if "-ja " not in old_form:
                continue
            compact_old_form = old_form.replace("-ja ", "-ja")
            if compact_old_form and compact_old_form not in variants:
                variants[compact_old_form] = new_form
        if old == "veekogu" and new == "meri":
            special_pairs = {
                "süvendatakse veekogu": "süvendatakse merd",
                "paigutatakse veekogu põhja": "paigutatakse mere põhja",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
    return tuple(sorted(variants.items(), key=lambda item: len(item[0]), reverse=True))


def _ee_genitive_singular_modifier_phrase_to_plural(text: str) -> str:
    """Return a narrow -us genitive modifier plural variant for source anchors."""
    parts = text.split(" ", 1)
    if len(parts) != 2:
        return ""
    first, rest = parts
    if not first.endswith("use") or len(first) <= 4:
        return ""
    return f"{first[:-2]}ste {rest}"



__all__ = [
    "_EE_AMETIKOHT_TEENISTUSKOHT_FORMS_RULE",
    "_EE_ARUANDED_ARUANNE_FORMS_RULE",
    "_EE_ARUANDED_HEADING_AGREEMENT_RULE",
    "_EE_KYSK_RTK_FORMS_RULE",
    "_EE_MIXED_ACRONYM_SUFFIX_CASE_REWRITE_RULE",
    "_EE_NETO_OMAVAHEND_PREFIX_FORMS_RULE",
    "_EE_OLEMASOLEV_TAHKEL_KUTUSEL_PHRASE_FORMS_RULE",
    "_EE_RIIKLIK_REGISTER_INFOSUSTEEM_FORMS_RULE",
    "_EE_TAOTLUSVOOR_COORDINATION_FORMS_RULE",
    "_EE_VOLITATUD_VASTUTAV_FORMS_RULE",
    "_ee_declension_forms",
    "_ee_genitive_singular_modifier_phrase_to_plural",
    "_ee_law_reference_l6ige_forms",
    "_ee_normalize_text_replace_surface",
    "_ee_phrase_forms",
    "_ee_text_replace_variants",
    "case_preserved_replacement",
    "case_inflected_phrase_source_family",
    "insert_sentence_after",
    "insert_sentence_before",
    "replace_first_sentence",
    "replace_case_preserving",
    "replace_sentence",
    "replace_sentence_span",
    "sentence_index_from_notes",
    "sentence_indexes_from_notes",
    "surface_pattern",
    "split_ee_sentences",
    "wrap_word_boundaries",
]
