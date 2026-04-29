"""Estonia target-resolution helpers.

This module is the first mechanical extraction from the old target-resolution
cluster in ``grafter.py``. It currently hosts the strict/title matching helpers
that feed the registry-backed target gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Sequence, cast
import xml.etree.ElementTree as ET
import html as html_lib

from lawvm.core.ir import LegalOperation, OperationSource, StructuralAction

from lawvm.estonia.act_identity_registry import (
    EEActIdentityRecord,
    act_identity_matches_title,
    lookup_ee_act_identity,
)
from lawvm.estonia.peg import _extract_quoted_content, _normalize_num, extract_ee_ops, parse_html_op_items

_EE_DIRECT_TARGET_PREFIX_STRIP_RULE = "ee_direct_target_title_prefix_stripped_for_structural_repeal"
_EE_OLD_FORMAT_WRAPPER_SCOPE_INHERITED_RULE = "ee_old_format_wrapper_scope_inherited"


def _registry_record_matches_all(record: object, *surfaces: str) -> bool:
    """Return True only when every non-empty surface belongs to the same registry record."""
    narrowed_record = cast(EEActIdentityRecord, record)
    checked = [surface for surface in surfaces if surface]
    return bool(checked) and all(act_identity_matches_title(narrowed_record, surface) for surface in checked)


@dataclass(frozen=True)
class NewFormatGateFlags:
    """Root-level new-format amendment routing flags."""

    is_omnibus: bool
    dedicated_kws: tuple[str, ...]
    has_dedicated: bool


@dataclass(frozen=True)
class NewFormatParagraphContext:
    """Per-paragraph routing context for new-format amendment parsing."""

    para_label: str
    para_title: str
    first_tava: str
    has_direct_target_clause: bool
    embedded_target_sections: tuple[str, ...]
    base_act_name: str
    source: OperationSource


def title_matches_para(target_title: str, para_title: str) -> bool:
    """Return True if para_title refers to the same statute as target_title."""
    if not para_title:
        return False
    registry_record = lookup_ee_act_identity(title=target_title, alias=para_title)
    if registry_record is not None and _registry_record_matches_all(registry_record, target_title, para_title):
        return True
    # Normalize: strip "muutmine", convert genitive endings
    para_norm = re.sub(r'\s+muutmine\s*$', '', para_title.strip(), flags=re.IGNORECASE)
    para_norm = re.sub(r'\bseaduse\b', 'seadus', para_norm)
    para_norm = re.sub(r'\bseadust\b', 'seadus', para_norm)
    para_norm = re.sub(r'\bseadustiku\b', 'seadustik', para_norm)
    para_norm = re.sub(r'\bseadustikku\b', 'seadustik', para_norm)
    para_norm = re.sub(r'\bkoodeksi\b', 'koodeks', para_norm)
    para_norm = re.sub(r'\bkoodeksit\b', 'koodeks', para_norm)
    para_norm = re.sub(r'\bseaduste\b', 'seadus', para_norm)
    para_norm = re.sub(r'\bmäärust\b', 'määrus', para_norm)
    para_norm = re.sub(r'\bmääruse\b', 'määrus', para_norm)
    para_norm = para_norm.lower().strip()
    target_norm = target_title.lower().strip()
    if ("rakendamise seadus" in para_norm) != ("rakendamise seadus" in target_norm):
        return False
    # Direct substring match (most common case)
    if target_norm in para_norm or para_norm in target_norm:
        return True
    # Word-overlap: all meaningful words in target must appear exactly after
    # normalization. Avoid loose prefix fallback here because it causes
    # cross-statute contamination such as "Kohtutäituri" matching "Kohtute".
    _SKIP = {'seadus', 'seadustik', 'koodeks', 'ja', 'ning', 'või', 'nende'}
    target_words = [w for w in re.split(r'\W+', target_norm) if len(w) > 3 and w not in _SKIP]
    if not target_words:
        return False
    para_words = {w for w in re.split(r'\W+', para_norm) if w}

    def _word_matches(target_word: str) -> bool:
        if target_word in para_words:
            return True
        if target_word.endswith("ne") and (target_word[:-2] + "se") in para_words:
            return True
        return False

    return all(_word_matches(w) for w in target_words)


def strict_title_match_para(target: str, para: str) -> bool:
    """Strict title match for wrapper headers that must name the same statute."""
    if not para or not target:
        return False
    registry_record = lookup_ee_act_identity(title=target, alias=para)
    if registry_record is not None and _registry_record_matches_all(registry_record, target, para):
        return True

    def _normalize_word(word: str) -> str:
        replacements = (
            ("seadustes", "seadus"),
            ("seaduses", "seadus"),
            ("seaduste", "seadus"),
            ("seaduse", "seadus"),
            ("seadust", "seadus"),
            ("seadustikus", "seadustik"),
            ("seadustiku", "seadustik"),
            ("seadustikku", "seadustik"),
            ("koodeksis", "koodeks"),
            ("koodeksi", "koodeks"),
            ("koodeksit", "koodeks"),
            ("määruses", "määrus"),
            ("määruse", "määrus"),
            ("määrust", "määrus"),
        )
        for suffix, repl in replacements:
            if word.endswith(suffix):
                return word[: -len(suffix)] + repl
        return word

    para_norm = re.sub(r'\s+muutmine\s*$', '', para.strip(), flags=re.IGNORECASE).lower()
    target_norm = target.strip().lower()
    if ("rakendamise seadus" in para_norm) != ("rakendamise seadus" in target_norm):
        return False
    _SKIP = {'seadus', 'seadustik', 'koodeks', 'ja', 'ning', 'või', 'nende'}
    target_words = [
        _normalize_word(w)
        for w in re.split(r'\W+', target_norm)
        if len(_normalize_word(w)) > 3 and _normalize_word(w) not in _SKIP
    ]
    if not target_words:
        return False
    para_words = {
        _normalize_word(w)
        for w in re.split(r'\W+', para_norm)
        if _normalize_word(w)
    }
    return all(w in para_words for w in target_words)


def matches_target_statute_header(target_title: str, para_title: str) -> bool:
    """Match statute-targeting paragraph headers without overfitting to nominative form."""
    registry_record = lookup_ee_act_identity(title=para_title, alias=target_title)
    if registry_record is not None and _registry_record_matches_all(registry_record, target_title, para_title):
        return True
    return strict_title_match_para(target_title, para_title) or title_matches_para(target_title, para_title)


def paragrahv_to_act_id(title: str) -> str:
    """Heuristically derive a short base act name from a paragrahv title."""
    title = re.sub(r"\s+muutmine\s*$", "", title.strip(), flags=re.IGNORECASE)
    title = re.sub(r"\s+§.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"(seadustiku|seaduse|koodeksi)$",
        lambda m: {
            "seadustiku": "seadustik",
            "seaduse": "seadus",
            "koodeksi": "koodeks",
        }.get(m.group(1), m.group(1)),
        title,
        flags=re.IGNORECASE,
    )
    return title.lower().strip()


def extract_intro_statute_fragment(text: str) -> str:
    """Extract a leading target-statute phrase from an untitled amendment clause."""
    if not text:
        return ""
    text = re.sub(r"\s*\(RT[^)]*\)", "", text, flags=re.IGNORECASE)
    year_prefix = ""
    year_match = re.match(r"^(\d{4}\.\s*aasta\s+)", text, re.IGNORECASE)
    if year_match is not None:
        year_prefix = year_match.group(1)
        text = text[year_match.end():]
    fragment = ""
    fragment_from_quoted_title = False
    quoted_title_match = re.match(
        r"^[A-ZÜÕÖÄ][^\n]{0,240}?\b(?:seaduse|seaduses|seadust|seadustiku|koodeksi|määruse|määruses|määrust)\b"
        r"(?:\s+nr\.?\s*[\w./-]+)?\s+[„\"“](?P<title>[^„”“\"]+)[”“\"]"
        r"\s+(?:§|paragrahv|\btehakse\b|\bmuudetakse\b|\btunnistatakse\b|\btäiendatakse\b|\bjäetakse\b)",
        text,
        re.IGNORECASE,
    )
    if quoted_title_match:
        fragment = quoted_title_match.group("title").strip()
        fragment_from_quoted_title = True
    section_scoped_match = re.match(
        r"^([A-ZÜÕÖÄ][^\n.]{4,180}?\b(?:seadus|seaduse|seadust|"
        r"seadustik|seadustiku|seadustikku|koodeks|koodeksi|koodeksit|"
        r"määrus|määruse|määrust)\b)"
        r"(\s+§[^.]*)?\s+"
        r"(muudetakse|asendatakse|tunnistatakse|täiendatakse|jäetakse)",
        text,
        re.IGNORECASE,
    )
    if not fragment and section_scoped_match:
        fragment = section_scoped_match.group(1).strip()
        if section_scoped_match.group(3).lower() == "jäetakse" and section_scoped_match.group(2):
            fragment = f"{fragment}{section_scoped_match.group(2)}"
    elif not fragment:
        direct_match = re.match(
            r"^([A-ZÜÕÖÄ][^\n.]{4,180}?)"
            r"(?:tehakse|tehtakse|asendatakse|tunnistatakse|täiendatakse|jäetakse)",
            text,
            re.IGNORECASE,
        )
        if direct_match:
            fragment = direct_match.group(1).strip()
    if not fragment:
        return ""
    if year_prefix:
        fragment = f"{year_prefix}{fragment}".strip()
    if not fragment_from_quoted_title and not any(
        token in fragment.lower()
        for token in (
            "seadus",
            "seaduse",
            "seadust",
            "seadustik",
            "seadustiku",
            "seadustikku",
            "koodeks",
            "koodeksi",
            "koodeksit",
            "määrus",
            "määruse",
            "määrust",
        )
    ):
        return ""
    return fragment


def is_specific_direct_target_fragment(fragment: str) -> bool:
    """Return True when a clause fragment names a statute, not a generic law noun."""
    if not fragment:
        return False
    cleaned = re.sub(r"\s+", " ", fragment.strip())
    lowered = cleaned.lower()
    generic_only = {
        "seadus",
        "seaduse",
        "seadust",
        "seadustik",
        "seadustiku",
        "seadustikku",
        "koodeks",
        "koodeksi",
        "koodeksit",
        "määrus",
        "määruse",
        "määrust",
    }
    if lowered in generic_only:
        return False
    if re.match(
        r"^(?:käesolev|nimetatud|see|sama|kõnealune|nimetatud)\s+"
        r"(?:seadus|seaduse|seadust|seadustik|seadustiku|seadustikku|koodeks|koodeksi|koodeksit|määrus|määruse|määrust)\b",
        lowered,
        re.IGNORECASE,
    ):
        return False
    return True


def direct_target_clause_matches_registry(
    *,
    fragment: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
) -> bool:
    """Resolve a direct-target fragment against the act-identity registry."""
    if not is_specific_direct_target_fragment(fragment):
        return False
    registry_record = lookup_act_identity(alias=fragment or "")
    if registry_record is not None and _registry_record_matches_all(registry_record, target_title, fragment):
        return True
    registry_record = lookup_act_identity(title=fragment, alias=target_title)
    if registry_record is not None and _registry_record_matches_all(registry_record, target_title, fragment):
        return True
    return title_matcher(target_title, fragment)


def intro_fragment_matches_target(
    *,
    target_title: str,
    stat_fragment: str,
    source_id: str = "",
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when an untitled intro fragment still targets the requested statute."""
    if not target_title or not stat_fragment:
        return False
    lookup_kwargs: dict[str, object] = {
        "title": target_title,
        "alias": stat_fragment,
    }
    if source_id:
        lookup_kwargs["akt_viide"] = source_id
    registry_record = lookup_act_identity(**lookup_kwargs)
    if registry_record is not None and _registry_record_matches_all(registry_record, target_title, stat_fragment):
        return True
    return title_matches_para(target_title, stat_fragment)


def registry_supports_target_statute(
    *,
    para_title: str,
    first_tava: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when the paragraph title or intro fragment resolve to the target statute."""
    if not target_title:
        return False
    stat_fragment = extract_intro_statute_fragment(first_tava)
    registry_record = lookup_act_identity(
        title=para_title,
        alias=stat_fragment or target_title,
    )
    return bool(
        registry_record is not None
        and (
            _registry_record_matches_all(registry_record, target_title, stat_fragment)
            or _registry_record_matches_all(registry_record, target_title, para_title)
        )
    )


def detect_dedicated_target_gate(
    *,
    paragraph_infos: Sequence[tuple[str, str]],
    target_title: str,
    dedicated_kws: Sequence[str],
) -> bool:
    """Return True when any paragraph looks like a dedicated target-statute gate."""
    if not target_title:
        return False
    for para_title, first_tava in paragraph_infos:
        para_title_lower = para_title.lower()
        stat_fragment = extract_intro_statute_fragment(first_tava)
        if registry_supports_target_statute(
            para_title=para_title,
            first_tava=first_tava,
            target_title=target_title,
        ):
            return True
        if any(kw in para_title_lower for kw in dedicated_kws) and strict_title_match_para(target_title, para_title):
            return True
        if stat_fragment and title_matches_para(target_title, stat_fragment):
            return True
    return False


def prepare_new_format_gate_flags(
    *,
    root: ET.Element,
    ns_str: str,
    target_title: str,
    first_tavatekst_text: Callable[[ET.Element, str], str],
    text_finder: Callable[[ET.Element | None], str],
    find_child: Callable[[ET.Element, str, str], ET.Element | None],
    is_omnibus_amendment: Callable[[ET.Element, str, str], bool],
) -> NewFormatGateFlags:
    """Collect root-level gate flags for new-format amendment parsing."""
    dedicated_kws = ("muutmine", "kehtetuks tunnistamine", "täiendamine")
    is_omnibus = is_omnibus_amendment(root, ns_str, target_title) if target_title else False
    has_dedicated = detect_dedicated_target_gate(
        paragraph_infos=[
            (
                text_finder(find_child(para, ns_str, "paragrahvPealkiri")) or "",
                first_tavatekst_text(para, ns_str),
            )
            for para in root.iter(_ns(ns_str, "paragrahv"))
        ],
        target_title=target_title,
        dedicated_kws=dedicated_kws,
    )
    return NewFormatGateFlags(
        is_omnibus=is_omnibus,
        dedicated_kws=dedicated_kws,
        has_dedicated=has_dedicated,
    )


def plain_html_text(html_block: str) -> str:
    """Normalize one HTML block to plain text for target-routing heuristics."""
    text = re.sub(r"<[^>]+>", " ", html_block)
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_embedded_act_sections(html_block: str) -> list[str]:
    """Split hybrid new-format HTML blocks that embed internal §-act sections."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", html_block, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return []

    sections: list[str] = []
    current: list[str] = []
    saw_header = False
    current_started_from_intro = False
    quote_balance = 0
    for para_html in paras:
        plain = plain_html_text(para_html)
        intro_fragment = extract_intro_statute_fragment(plain.lstrip('„"« '))
        starts_with_quote = plain.lstrip().startswith(("„", '"', "«"))
        starts_from_paragraph_header = bool(
            re.match(r"^§\s*\d+\.\s+", plain)
            and any(kw in plain.lower() for kw in ("muutmine", "kehtetuks tunnistamine", "täiendamine"))
        ) or bool(intro_fragment)
        if starts_from_paragraph_header:
            if current:
                sections.append("\n".join(current))
            current = [para_html]
            saw_header = True
            current_started_from_intro = bool(intro_fragment) and starts_with_quote
            quote_balance = (
                plain.count("„") + plain.count("«") + plain.count('"')
                - plain.count("”") - plain.count("»")
            )
            continue
        if current:
            bare_section_header = bool(re.match(r"^§\s*\d+\.\s+", plain))
            if bare_section_header and not current_started_from_intro:
                sections.append("\n".join(current))
                current = []
                current_started_from_intro = False
                quote_balance = 0
                continue
            if current_started_from_intro:
                m_outer_resume = re.match(r"^\s*(\d+)\)", plain)
                if m_outer_resume and int(m_outer_resume.group(1)) >= 20:
                    sections.append("\n".join(current))
                    current = []
                    current_started_from_intro = False
                    quote_balance = 0
                    continue
            current.append(para_html)
            if current_started_from_intro:
                quote_balance += (
                    plain.count("„") + plain.count("«") + plain.count('"')
                    - plain.count("”") - plain.count("»")
                )
                if quote_balance <= 0:
                    sections.append("\n".join(current))
                    current = []
                    current_started_from_intro = False
                    quote_balance = 0
    if current:
        sections.append("\n".join(current))
    return sections if saw_header else []


def split_embedded_paragraph_headers(html_block: str) -> list[str]:
    """Fallback split on any paragraph-level §-header in giant omnibus blocks."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", html_block, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return []

    sections: list[str] = []
    current: list[str] = []
    saw_header = False
    for para_html in paras:
        plain = plain_html_text(para_html)
        if re.match(r"^§\s*\d+\.\s+", plain):
            if current:
                sections.append("\n".join(current))
            current = [para_html]
            saw_header = True
            continue
        if current:
            current.append(para_html)
    if current:
        sections.append("\n".join(current))
    return sections if saw_header else []


def collect_embedded_target_sections(
    *,
    html_blocks: Iterable[str],
    target_title: str,
) -> list[str]:
    """Collect embedded HTML sections that target the requested statute."""
    embedded_target_sections: list[str] = []
    if not target_title:
        return embedded_target_sections
    for html_block in html_blocks:
        split_sections = split_embedded_act_sections(html_block)
        if len(split_sections) == 1:
            full_item_count = len(parse_html_op_items(html_block))
            split_item_count = len(parse_html_op_items(split_sections[0]))
            first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", html_block, re.DOTALL | re.IGNORECASE)
            first_para_plain = plain_html_text(first_para_m.group(1) if first_para_m else html_block)
            first_para_fragment = extract_intro_statute_fragment(first_para_plain.lstrip('„"« '))
            block_matches_target = bool(
                first_para_fragment and title_matches_para(target_title, first_para_fragment)
            )
            if full_item_count > split_item_count and block_matches_target:
                split_sections = [html_block]
        if not split_sections:
            split_sections = split_embedded_paragraph_headers(html_block)
        if not split_sections:
            continue
        for section_html in split_sections:
            first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", section_html, re.DOTALL | re.IGNORECASE)
            header_plain = plain_html_text(first_para_m.group(1) if first_para_m else section_html)
            header_fragment = extract_intro_statute_fragment(header_plain.lstrip('„"« '))
            is_paragraph_header = bool(re.match(r"^§\s*\d+\.\s+", header_plain))
            if (
                (is_paragraph_header and strict_title_match_para(target_title, header_plain))
                or (header_fragment and title_matches_para(target_title, header_fragment))
            ):
                embedded_target_sections.append(section_html)
    if embedded_target_sections and all(
        plain_html_text(section_html).lstrip().startswith(("„", '"', "«"))
        and not parse_html_op_items(section_html)
        and (
            "§" not in plain_html_text(section_html)
            or any(
                kw in plain_html_text(section_html).lower()
                for kw in ("asendatakse", "muudetakse", "täiendatakse", "tunnistatakse", "jäetakse välja", "lisatakse")
            )
        )
        for section_html in embedded_target_sections
    ):
        return []
    return embedded_target_sections


def filter_direct_target_clause_op_texts(
    *,
    op_texts: Sequence[str],
    target_title: str,
    para_title: str,
    has_direct_target_clause: bool,
    embedded_target_sections: Sequence[str],
    first_tava: str,
) -> list[str]:
    """Keep only op texts that still belong to the requested direct-target statute."""
    if not (
        target_title
        and has_direct_target_clause
        and not embedded_target_sections
        and not matches_target_statute_header(target_title, para_title)
    ):
        return list(op_texts)

    stat_fragment = extract_intro_statute_fragment(first_tava)
    if stat_fragment and title_matches_para(target_title, stat_fragment):
        return list(op_texts)

    filtered_op_texts: list[str] = []
    for op_text in op_texts:
        op_text_plain = re.sub(r"^\(?\d[\d\s_]*\)\s*", "", op_text).strip()
        plain_fragment = extract_intro_statute_fragment(op_text_plain)
        nested = _extract_quoted_content(op_text_plain)
        nested_fragment = extract_intro_statute_fragment(nested) if nested else ""
        if (
            title_matches_para(target_title, op_text_plain)
            or direct_target_clause_matches_registry(fragment=plain_fragment, target_title=target_title)
            or (nested and title_matches_para(target_title, nested))
            or (
                nested
                and direct_target_clause_matches_registry(fragment=nested_fragment, target_title=target_title)
            )
        ):
            filtered_op_texts.append(op_text)
    return filtered_op_texts


def new_format_lower_op_texts(
    *,
    op_texts: Sequence[str],
    source: OperationSource,
    target_title: str,
    base_act_name: str,
    amendment_section_label: str,
    seq_start: int,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    extract_ops: Callable[[str, OperationSource, int], list[LegalOperation]],
    has_section_ref: Callable[[str], bool],
    section_from_ops: Callable[[list[LegalOperation]], str | None],
    normalize_act_id: Callable[[str], str],
) -> tuple[list[LegalOperation], int]:
    """Lower new-format op texts with carried section context and cross-statute guard."""
    lowered: list[LegalOperation] = []
    global_seq = seq_start
    last_section: str | None = None

    for op_text in op_texts:
        amendment_item_label = old_format_item_label(op_text)
        if target_title:
            m_stat_ref = re.match(
                r'^([A-ZÜÕÖÄ][^\n.]{4,80}?'
                r'\b(?:seaduse|seadustiku|koodeksi|määruse)\b[^\n]{0,10}?)'
                r'(?:§\s*\d|paragrahvi?\s+\d)',
                op_text,
                re.IGNORECASE,
            )
            if m_stat_ref:
                stat_fragment = m_stat_ref.group(1)
                registry_record = lookup_act_identity(
                    akt_viide=source.statute_id,
                    title=target_title,
                    alias=stat_fragment,
                )
                if registry_record is not None and (
                    _registry_record_matches_all(registry_record, target_title, stat_fragment)
                ):
                    pass
                elif normalize_act_id(stat_fragment) == normalize_act_id(target_title):
                    pass
                elif not title_matcher(target_title, stat_fragment):
                    continue

        effective = re.sub(r'^\(?\d[\d\s_]*\)\s*', '', op_text).strip()
        direct_prefix_stripped = False
        effective, direct_prefix_stripped = strip_direct_target_title_prefix(
            effective,
            target_title,
            title_matcher=title_matcher,
        )
        if last_section and not has_section_ref(op_text):
            last_sect_raw = last_section.replace("_", " ")
            effective = f"paragrahvi {last_sect_raw} {op_text}"
        ops = extract_ops(effective, source, global_seq)
        sect = section_from_ops(ops)
        if sect:
            last_section = sect
        tagged_ops: list[LegalOperation] = []
        for op in ops:
            tags = list(op.provenance_tags)
            if amendment_section_label:
                tags.append(f"old_format_amendment_section:{amendment_section_label}")
            if amendment_item_label:
                tags.append(f"old_format_amendment_item:{amendment_item_label}")
            if base_act_name:
                tags.append(f"base_act: {base_act_name}")
            witness_rule_id = op.witness_rule_id
            if direct_prefix_stripped:
                tags.append(_EE_DIRECT_TARGET_PREFIX_STRIP_RULE)
                if op.action is not StructuralAction.META:
                    witness_rule_id = _EE_DIRECT_TARGET_PREFIX_STRIP_RULE
            tagged_ops.append(
                replace(
                    op,
                    provenance_tags=tuple(tags),
                    witness_rule_id=witness_rule_id,
                )
            )
        ops = tagged_ops
        lowered.extend(ops)
        global_seq += len(ops)

    return lowered, global_seq


def new_format_collect_all_ops(
    *,
    root: ET.Element,
    ns_str: str,
    source_id: str,
    target_title: str,
    seq_start: int,
    prepare_new_format_gate_flags: Callable[..., NewFormatGateFlags],
    prepare_new_format_paragraph_context: Callable[..., NewFormatParagraphContext],
    should_admit_new_format_paragraph: Callable[..., bool],
    new_format_collect_op_texts: Callable[..., list[str]],
    filter_direct_target_clause_op_texts: Callable[..., list[str]],
    new_format_lower_op_texts: Callable[..., tuple[list[LegalOperation], int]],
    first_tavatekst_text: Callable[[ET.Element, str], str],
    text_finder: Callable[[ET.Element | None], str],
    find_child: Callable[[ET.Element, str, str], ET.Element | None],
    is_omnibus_amendment: Callable[[ET.Element, str, str], bool],
    para_contains_direct_target_clause: Callable[[ET.Element, str, str], bool],
    collect_embedded_target_sections: Callable[..., list[str]],
    normalize_act_id: Callable[[str], str],
    title_matcher: Callable[[str, str], bool],
    lookup_act_identity: Callable[..., object | None],
    extract_ops: Callable[[str, OperationSource, int], list[LegalOperation]],
    has_section_ref: Callable[[str], bool],
    section_from_ops: Callable[[list[LegalOperation]], str | None],
) -> tuple[list[LegalOperation], int]:
    """Collect all lowered ops for a new-format amendment act."""
    all_ops: list[LegalOperation] = []
    global_seq = seq_start

    gate_flags = prepare_new_format_gate_flags(
        root=root,
        ns_str=ns_str,
        target_title=target_title,
        first_tavatekst_text=first_tavatekst_text,
        text_finder=text_finder,
        find_child=find_child,
        is_omnibus_amendment=is_omnibus_amendment,
    )

    for para in root.iter(_ns(ns_str, "paragrahv")):
        context = prepare_new_format_paragraph_context(
            para=para,
            ns_str=ns_str,
            source_id=source_id,
            target_title=target_title,
            text_finder=text_finder,
            find_child=find_child,
            first_tavatekst_text=first_tavatekst_text,
            para_contains_direct_target_clause=para_contains_direct_target_clause,
            collect_embedded_target_sections=collect_embedded_target_sections,
            normalize_act_id=normalize_act_id,
        )
        if not should_admit_new_format_paragraph(
            target_title=target_title,
            context=context,
            gate_flags=gate_flags,
            source_id=source_id,
            lookup_act_identity=lookup_act_identity,
        ):
            continue

        op_texts = new_format_collect_op_texts(
            para=para,
            ns_str=ns_str,
            embedded_target_sections=context.embedded_target_sections,
        )
        op_texts = filter_direct_target_clause_op_texts(
            op_texts=op_texts,
            target_title=target_title,
            para_title=context.para_title,
            has_direct_target_clause=context.has_direct_target_clause,
            embedded_target_sections=context.embedded_target_sections,
            first_tava=context.first_tava,
        )
        ops, global_seq = new_format_lower_op_texts(
            op_texts=op_texts,
            source=context.source,
            target_title=target_title,
            base_act_name=context.base_act_name,
            amendment_section_label=context.para_label,
            seq_start=global_seq,
            title_matcher=title_matcher,
            lookup_act_identity=lookup_act_identity,
            extract_ops=extract_ops,
            has_section_ref=has_section_ref,
            section_from_ops=section_from_ops,
            normalize_act_id=normalize_act_id,
        )
        all_ops.extend(ops)

    return all_ops, global_seq


def should_admit_new_format_paragraph(
    *,
    target_title: str,
    context: NewFormatParagraphContext,
    gate_flags: NewFormatGateFlags,
    source_id: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when a new-format amendment paragraph should be parsed for the target statute."""
    para_title = context.para_title
    first_tava = context.first_tava
    has_direct_target_clause = context.has_direct_target_clause
    embedded_target_sections = context.embedded_target_sections
    has_dedicated = gate_flags.has_dedicated
    is_omnibus = gate_flags.is_omnibus
    dedicated_kws = gate_flags.dedicated_kws
    if any(kw in para_title.lower() for kw in ("jõustumise", "jõustumine", "rakendussätted")):
        return False

    if has_dedicated:
        if looks_like_self_referential_amendment_act_para(
            target_title,
            para_title,
            first_tava,
            lookup_act_identity=lookup_act_identity,
        ):
            return False
        pt_lower = para_title.lower()
        stat_fragment = extract_intro_statute_fragment(first_tava)
        registry_supports_target = registry_supports_target_statute(
            para_title=para_title,
            first_tava=first_tava,
            target_title=target_title,
            lookup_act_identity=lookup_act_identity,
        )
        is_dedicated = (
            registry_supports_target
            or (
                any(kw in pt_lower for kw in dedicated_kws)
                and matches_target_statute_header(target_title, para_title)
            )
            or (bool(stat_fragment) and title_matches_para(target_title, stat_fragment))
        )
        if not is_dedicated and not has_direct_target_clause:
            return False

    if target_title and para_title and not embedded_target_sections:
        if looks_like_self_referential_amendment_act_para(
            target_title,
            para_title,
            first_tava,
            lookup_act_identity=lookup_act_identity,
        ):
            return False
        pt_lower = para_title.lower()
        stat_fragment = extract_intro_statute_fragment(first_tava)
        registry_supports_target = registry_supports_target_statute(
            para_title=para_title,
            first_tava=first_tava,
            target_title=target_title,
            lookup_act_identity=lookup_act_identity,
        )
        is_statute_specific = (
            registry_supports_target
            or (
                any(kw in pt_lower for kw in dedicated_kws)
                and matches_target_statute_header(target_title, para_title)
            )
            or (bool(stat_fragment) and title_matches_para(target_title, stat_fragment))
        )
        if is_omnibus and not is_statute_specific and not has_direct_target_clause:
            return False
    elif target_title and not para_title and is_omnibus and not embedded_target_sections:
        stat_fragment = extract_intro_statute_fragment(first_tava)
        if not intro_fragment_matches_target(
            target_title=target_title,
            stat_fragment=stat_fragment,
            lookup_act_identity=lookup_act_identity,
        ) and stat_fragment:
            return False
    elif target_title and not para_title and not embedded_target_sections:
        stat_fragment = extract_intro_statute_fragment(first_tava)
        if not intro_fragment_matches_target(
            target_title=target_title,
            stat_fragment=stat_fragment,
            source_id=source_id,
            lookup_act_identity=lookup_act_identity,
        ) and stat_fragment:
            return False

    return True


def new_format_collect_op_texts(
    *,
    para: ET.Element,
    ns_str: str,
    embedded_target_sections: Sequence[str],
    allow_plain_paragraph_items: bool = False,
) -> list[str]:
    """Assemble new-format amendment instruction texts before lowering."""
    op_texts: list[str] = []
    html_blocks: list[str] = []

    def _b_sentinel_fallback(m: re.Match[str]) -> str:
        inner = m.group(1)
        inner_plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        inner_plain = html_lib.unescape(inner_plain)
        if "§" in inner_plain:
            return inner_plain + "\x01"
        return inner

    def _strip_html(html_block: str) -> str:
        text = re.sub(r"<b>(.*?)</b>", _b_sentinel_fallback, html_block, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)
        text = text.replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    op_kws = (
        "paragrahvi", "paragrahvist", "lõiget", "lõikest", "lõikes",
        "muudetakse", "täiendatakse", "tunnistatakse",
        "asendatakse", "jäetakse välja", "lisatakse",
        "§-ga", "§-dega",
    )

    def _html_wrapper_instruction(html_block: str) -> str:
        m = re.split(
            r"(?=<[pb][^>]*>\s*<(?:b|strong)>\(?\d+\)\s*[^<]*</(?:b|strong)>|<(?:b|strong)>\(?\d+\)\s*[^<]*</(?:b|strong)>)",
            html_block,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        if len(m) < 2:
            return ""
        intro = _strip_html(m[0])
        intro = re.sub(
            r"^§\s*\d+\.\s+[^§]{0,200}?\b(?:muutmine|kehtetuks tunnistamine|täiendamine)\b\s*",
            "",
            intro,
            flags=re.IGNORECASE,
        ).strip()
        if not intro:
            return ""
        if not (
            any(kw in intro.lower() for kw in op_kws)
            or re.search(r"§(?:-[a-zõäöüšž]+)?\s*\d", intro, re.IGNORECASE)
        ):
            return ""
        return intro

    tava_instructions: list[str] = []
    for st in para.iter(_ns(ns_str, "sisuTekst")):
        st_html: list[str] = []
        for hk in st.findall(_ns(ns_str, "HTMLKonteiner")):
            raw_html = hk.text or ""
            html_sections = embedded_target_sections or [raw_html]
            for html in html_sections:
                st_html.append(html)
                html_blocks.append(html)
                item_texts = parse_html_op_items(
                    html,
                    allow_plain_paragraph_items=allow_plain_paragraph_items,
                )
                wrapper_instruction = _html_wrapper_instruction(html)
                if item_texts and wrapper_instruction:
                    for item_text in item_texts:
                        combined = f"{wrapper_instruction} {item_text}".strip()
                        if combined not in op_texts:
                            op_texts.append(combined)
                else:
                    op_texts.extend(item_texts)

        st_tava: list[str] = []
        for t in st.findall(_ns(ns_str, "tavatekst")):
            txt = " ".join(str(_t) for _t in t.itertext()).replace("\xa0", " ")
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                st_tava.append(txt)

        if st_tava and not st_html:
            tava_text = " ".join(st_tava)
            if any(kw in tava_text.lower() for kw in op_kws) or re.search(r"§\s*\d", tava_text):
                split_tava = split_plaintext_numbered_op_texts(tava_text)
                if split_tava:
                    op_texts.extend(split_tava)
                else:
                    tava_instructions = [tava_text]
        elif st_html and not st_tava and tava_instructions:
            for html_block in st_html:
                content_plain = _strip_html(html_block)
                is_quoted_payload = bool(
                    content_plain and content_plain[0] in ('"', "\u201e", "\u00ab", "\u201c")
                )
                if (
                    content_plain
                    and (is_quoted_payload or not any(kw in content_plain.lower() for kw in op_kws))
                    and not parse_html_op_items(
                        html_block,
                        allow_plain_paragraph_items=allow_plain_paragraph_items,
                    )
                ):
                    for instr in tava_instructions:
                        combined = f"{instr} {content_plain}"
                        if combined not in op_texts:
                            op_texts.append(combined)

    if not op_texts and html_blocks:
        for html_block in html_blocks:
            text = _strip_html(html_block)
            if text and (text[0] in ('"', "\u201e", "\u00ab", "\u201c") or text.startswith("(")):
                continue
            if text and any(kw in text.lower() for kw in op_kws):
                op_texts.append(text)

    if not op_texts:
        text_parts: list[str] = []
        for st in para.iter(_ns(ns_str, "sisuTekst")):
            for t in st.findall(_ns(ns_str, "tavatekst")):
                txt = " ".join(str(_t) for _t in t.itertext()).replace("\xa0", " ")
                txt = re.sub(r"\s+", " ", txt).strip()
                if txt:
                    text_parts.append(txt)
        full_text = " ".join(text_parts)
        if full_text and (
            any(
                kw in full_text.lower()
                for kw in (
                    "paragrahvi", "paragrahvist", "lõiget", "lõikest", "lõikes",
                    "muudetakse", "täiendatakse", "tunnistatakse",
                    "asendatakse", "jäetakse välja", "lisatakse",
                )
            )
            or re.search(r"§\s*\d", full_text)
        ):
            split_full_text = split_plaintext_numbered_op_texts(full_text)
            if split_full_text:
                op_texts.extend(split_full_text)
            else:
                op_texts.append(full_text)

    if not op_texts and tava_instructions:
        op_texts.extend(tava_instructions)

    return op_texts


def prepare_new_format_paragraph_context(
    *,
    para: ET.Element,
    ns_str: str,
    source_id: str,
    target_title: str,
    text_finder: Callable[[ET.Element | None], str],
    find_child: Callable[[ET.Element, str, str], ET.Element | None],
    first_tavatekst_text: Callable[[ET.Element, str], str],
    para_contains_direct_target_clause: Callable[[ET.Element, str, str], bool],
    collect_embedded_target_sections: Callable[..., list[str]],
    normalize_act_id: Callable[[str], str],
) -> NewFormatParagraphContext:
    """Collect the per-paragraph context needed before new-format lowering."""
    para_label = text_finder(find_child(para, ns_str, "paragrahvNr")) or ""
    para_title = text_finder(find_child(para, ns_str, "paragrahvPealkiri")) or ""
    first_tava = first_tavatekst_text(para, ns_str)
    if not first_tava:
        for st in para.iter(_ns(ns_str, "sisuTekst")):
            for hk in st.findall(_ns(ns_str, "HTMLKonteiner")):
                html_text = plain_html_text(hk.text or "")
                if html_text:
                    first_tava = html_text
                    break
            if first_tava:
                break
    has_direct_target_clause = (
        para_contains_direct_target_clause(para, ns_str, target_title)
        if target_title
        else False
    )
    embedded_target_sections = collect_embedded_target_sections(
        html_blocks=[
            hk.text or ""
            for st in para.iter(_ns(ns_str, "sisuTekst"))
            for hk in st.findall(_ns(ns_str, "HTMLKonteiner"))
        ],
        target_title=target_title,
    )
    base_act_name = normalize_act_id(para_title)
    source = OperationSource(statute_id=source_id, title=para_title, raw_text=para_title)
    return NewFormatParagraphContext(
        para_label=_normalize_num(para_label) if para_label else "",
        para_title=para_title,
        first_tava=first_tava,
        has_direct_target_clause=has_direct_target_clause,
        embedded_target_sections=tuple(embedded_target_sections),
        base_act_name=base_act_name,
        source=source,
    )


def old_format_section_matches_target(
    target: str,
    header: str,
    *,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when an old-format section header names the same statute."""
    if not target or not header:
        return False

    registry_record = lookup_act_identity(title=target, alias=header)
    if registry_record is not None:
        if _registry_record_matches_all(registry_record, target, header):
            return True

    def _normalize_word(word: str) -> str:
        replacements = (
            ("seaduses", "seadus"),
            ("seaduse", "seadus"),
            ("seadust", "seadus"),
            ("seadustikus", "seadustik"),
            ("seadustiku", "seadustik"),
            ("seadustikku", "seadustik"),
            ("koodeksi", "koodeks"),
            ("koodeksit", "koodeks"),
            ("määruses", "määrus"),
            ("määruse", "määrus"),
            ("määrust", "määrus"),
        )
        for suffix, repl in replacements:
            if word.endswith(suffix):
                return word[: -len(suffix)] + repl
        return word

    skip = {"seadus", "seadustik", "koodeks", "määrus", "ja", "ning", "või", "nende"}
    target_words = [
        _normalize_word(w)
        for w in re.split(r"\W+", target.lower())
        if _normalize_word(w) and len(_normalize_word(w)) > 3 and _normalize_word(w) not in skip
    ]
    header_words = {
        _normalize_word(w)
        for w in re.split(r"\W+", header.lower())
        if _normalize_word(w)
    }
    return bool(target_words) and all(w in header_words for w in target_words)


def old_format_is_section_header_text(header_text: str) -> bool:
    """Return True when normalized old-format header text looks like an act section header."""
    if not header_text:
        return False
    return bool(
        re.search(r"§\s*\d", header_text)
        or re.match(r"^[IVXLCDM]+\.", header_text, re.IGNORECASE)
    )


def old_format_section_targets_title(
    *,
    header_text: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when an old-format section header targets the requested statute."""
    if not target_title:
        return True
    return old_format_section_matches_target(
        target_title,
        header_text,
        lookup_act_identity=lookup_act_identity,
    )


def strip_old_format_html_text(html_block: str) -> str:
    """Strip old-format amendment HTML to plain text while preserving bold section sentinels."""

    def _b_sentinel_fallback(m: re.Match[str]) -> str:
        inner = m.group(1)
        inner_plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if "§" in inner_plain:
            return inner_plain + "\x01"
        return inner

    text = re.sub(r"<b>(.*?)</b>", _b_sentinel_fallback, html_block, flags=re.DOTALL)
    text = re.sub(r"<strong>(.*?)</strong>", _b_sentinel_fallback, text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_old_format_paragraph_sections(html: str) -> list[str]:
    """Split old-format HTML into statute sections using paragraph headers."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", html, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return []

    act_markers = (
        "seadus",
        "seaduse",
        "seaduses",
        "seadust",
        "seadustik",
        "seadustiku",
        "seadustikus",
        "koodeks",
        "koodeksi",
        "määrus",
        "määruse",
        "määruses",
        "määrust",
    )
    header_pat = re.compile(r"^(?:§\s*\d+\.?|[IVXLCDM]+\.)\s+", re.IGNORECASE)

    sections_out: list[str] = []
    current: list[str] = []
    saw_header = False

    for para in paras:
        plain = strip_old_format_html_text(para)
        starts_section = bool(header_pat.match(plain)) and any(
            marker in plain[:220].lower() for marker in act_markers
        )
        if starts_section:
            if current:
                sections_out.append("\n".join(current))
            current = [para]
            saw_header = True
            continue
        if current:
            current.append(para)

    if current:
        sections_out.append("\n".join(current))

    return sections_out if saw_header and len(sections_out) > 1 else []


def split_old_format_wrapper_blocks(section_html: str) -> list[str]:
    """Split a target old-format act section by nested §-wrapper paragraphs."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", section_html, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return []

    wrapper_pat = re.compile(r"^§\s*\d+\.", re.IGNORECASE)

    def _current_has_unclosed_payload_quote() -> bool:
        text = "\n".join(current)
        return text.count("„") > text.count("“")

    blocks: list[str] = []
    current: list[str] = []
    saw_wrapper = False

    for para in paras:
        plain = strip_old_format_html_text(para)
        if wrapper_pat.match(plain) and not _current_has_unclosed_payload_quote():
            if current:
                blocks.append("\n".join(current))
            current = [para]
            saw_wrapper = True
            continue
        if current:
            current.append(para)

    if current:
        blocks.append("\n".join(current))

    return blocks if saw_wrapper and len(blocks) > 1 else []


def split_old_format_top_level_parenthesized_blocks(content_html: str) -> list[str]:
    """Split wrapper content at later top-level `(N)` markers while keeping inner items grouped."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", content_html, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return []

    top_pat_html = re.compile(
        r"^\s*<p\b[^>]*>\s*<(?:b|strong)>\s*\(\d+\)\s*</(?:b|strong)>",
        re.IGNORECASE,
    )
    blocks: list[str] = []
    current: list[str] = []
    saw_top = False

    for para in paras:
        if top_pat_html.match(para):
            if current:
                blocks.append("\n".join(current))
            current = [para]
            saw_top = True
            continue
        current.append(para)

    if current:
        blocks.append("\n".join(current))

    return blocks if saw_top and len(blocks) > 1 else []


def old_format_has_section_ref(text: str) -> bool:
    """True if the instruction preamble already contains an explicit structural reference."""
    preamble_end = len(text)
    for marker in ("\u201e", "\u00ab", "järgmises sõnastuses:", "järgmiselt:"):
        idx = text.find(marker)
        if 0 <= idx < preamble_end:
            preamble_end = idx
    preamble = text[:preamble_end]
    return bool(
        re.search(
            r"\bparagrahvid(?:e[s]?)?\s+\d|\bparagrahvi(?:s|st)?\s+\d|\bparagrahv\s+\d|"
            r"§(?:-d|-s|-ga|-iga|-des|-dega)?\s*\d|"
            r"\blisa(?:d|de|sid|ga)?\s+\d|"
            r"\bpeatüki\s+\d|\bjao\s+\d|\bjaotis(?:e|es|t)?\s+\d|"
            r"\bpeatükiga\s+\d|"
            r"\b\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s*[.]\s*peatük|\b\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s*[.]\s*jao|"
            r"\b\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s*[.]\s*jaotis",
            preamble,
            re.IGNORECASE,
        )
    )


def strip_direct_target_title_prefix(
    text: str,
    target_title: str,
    *,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
) -> tuple[str, bool]:
    """Strip an explicit target-act lead-in before a structural target."""
    if not text or not target_title:
        return text, False
    match = re.match(
        r"^[A-ZÜÕÖÄ].{0,360}?\b"
        r"(?:seaduse|seadustiku|koodeksi|määruse)\b"
        r"(?:\s+nr\.?\s*[\w./-]+)?\s+[„\"“](?P<title>[^„”“\"]+)[”“\"]\s+"
        r"(?P<tail>.+)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return text, False
    quoted_title = re.sub(r"\s+", " ", match.group("title")).strip()
    tail = re.sub(r"\s+", " ", match.group("tail")).strip()
    if not tail or not title_matcher(target_title, quoted_title):
        return text, False
    if not re.match(
        r"(?:\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\.\s*,?\s*)?"
        r"(?:\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\.\s*,?\s*(?:ja\s+)?)?|"
        r"§|paragrahv|paragrahvi|peatükk|peatüki|osa|jagu|jaotis|"
        r"tunnistatakse|muudetakse|asendatakse|täiendatakse|jäetakse|lisatakse)",
        tail,
        flags=re.IGNORECASE,
    ):
        return text, False
    return tail, True


def old_format_section_from_ops(ops: list[LegalOperation]) -> str | None:
    """Return the section label from the first op that has one, else None."""
    for op in ops:
        d = dict(op.target.path)
        if "section" in d:
            return d["section"]
    return None


def old_format_section_from_header_text(header_text: str) -> str | None:
    """Extract an initial target section label from an old-format section header."""
    if not header_text:
        return None

    paragrahv_match = re.search(
        r"\bparagrahvi\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹]*)",
        header_text,
        re.IGNORECASE,
    )
    if paragrahv_match:
        return _normalize_num(paragrahv_match.group(1))

    section_refs = re.findall(r"§\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹]*)", header_text)
    if len(section_refs) >= 2:
        return _normalize_num(section_refs[1])
    return None


def old_format_act_section_from_header_text(header_text: str) -> str | None:
    """Extract the amendment-act section label from an old-format section header."""
    if not header_text:
        return None
    match = re.search(r"§\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)", header_text)
    if not match:
        return None
    return _normalize_num(match.group(1))


def old_format_item_label(op_text: str) -> str | None:
    """Extract the leading old-format amendment item label from one op text."""
    if not op_text:
        return None
    match = re.match(r"^\(?(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\)\s*", op_text.strip())
    if not match:
        return None
    return _normalize_num(match.group(1))


def old_format_strip_item_label(op_text: str) -> str:
    """Remove only the leading old-format amendment item label from an item body."""
    return re.sub(
        r"^\(?\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s*",
        "",
        op_text.strip(),
        count=1,
    )


def old_format_section_from_intro_context(content_block: str) -> str | None:
    """Extract a carried target section from intro prose before numbered items."""
    paras = re.findall(r"<p\b[^>]*>.*?</p>", content_block, flags=re.DOTALL | re.IGNORECASE)
    if not paras:
        return None
    item_start = re.compile(
        r"^\s*\(?\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s+",
        re.IGNORECASE,
    )
    labels: list[str] = []
    for idx, para in enumerate(paras):
        plain = strip_old_format_html_text(para)
        if idx == 0 and old_format_is_section_header_text(plain):
            continue
        if item_start.match(plain):
            break
        for match in re.finditer(
            r"(?:§(?:-s|-st|-le|-ga|-des|-dega)?|paragrahvi(?:s|st)?|paragrahv)\s*"
            r"(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)",
            plain,
            re.IGNORECASE,
        ):
            labels.append(_normalize_num(match.group(1)))
    return labels[-1] if labels else None


def split_plaintext_numbered_op_texts(text: str) -> list[str]:
    """Split a flat plaintext amendment body into top-level numbered clauses."""
    if not text:
        return []
    normalized = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    normalized = re.split(
        r"\s§\s*\d+\.\s+Määrus(?:t)?\s+(?:jõustub|rakendatakse)\b",
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    start_pattern = re.compile(
        r"(?:^|\s)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\)\s+"
        r"(?=(?:paragrahvi|paragrahv|määruse|määrust|seaduse|seadust|lisa|lisad|§)\b)",
        re.IGNORECASE,
    )

    def _inside_open_quote(pos: int) -> bool:
        open_quote = False
        for char in normalized[:pos]:
            if char in {"„", "“", "”"}:
                open_quote = not open_quote
        return open_quote

    starts = [
        match.start(1)
        for match in start_pattern.finditer(normalized)
        if not _inside_open_quote(match.start(1))
    ]
    if not starts:
        return []
    clauses: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(normalized)
        clause = normalized[start:end].strip()
        if clause:
            clauses.append(clause)
    return clauses if len(clauses) > 1 else []


def old_format_extract_op_texts(content_block: str, block_header_text: str) -> list[str]:
    """Extract instruction texts from an old-format wrapper block.

    Prefer numbered HTML items when present. Otherwise recover a single effective
    instruction from the stripped block body, mirroring the legacy fallback in
    ``grafter.py``.
    """
    item_source_block = content_block
    if _old_format_header_names_specific_act(block_header_text):
        first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", content_block, re.DOTALL | re.IGNORECASE)
        if first_para_m is not None:
            rest = content_block[first_para_m.end():]
            rest_first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", rest, re.DOTALL | re.IGNORECASE)
            rest_first_plain = (
                strip_old_format_html_text(rest_first_para_m.group(1))
                if rest_first_para_m is not None
                else ""
            )
            if not old_format_is_section_header_text(rest_first_plain):
                item_source_block = rest
    op_texts = parse_html_op_items(item_source_block)
    if op_texts:
        return op_texts

    body_html = re.sub(
        r"^\s*(?:<[^>]+>\s*)?<(?:b|strong)>.*?</(?:b|strong)>\s*(?:</[^>]+>\s*<[^>]+>\s*)?",
        "",
        content_block,
        count=1,
        flags=re.DOTALL,
    )
    plain_text = strip_old_format_html_text(body_html)
    if plain_text.startswith("§") and "\x01" in plain_text:
        plain_text = plain_text.split("\x01", 1)[1].strip()

    plain_preamble_end = len(plain_text)
    for marker in ("\u201e", "\u00ab", "järgmises sõnastuses:", "järgmiselt:"):
        idx = plain_text.find(marker)
        if 0 <= idx < plain_preamble_end:
            plain_preamble_end = idx
    plain_preamble = plain_text[:plain_preamble_end]

    plain_has_instruction = bool(plain_preamble) and any(
        kw in plain_preamble.lower()
        for kw in (
            "muudetakse",
            "täiendatakse",
            "tunnistatakse",
            "asendatakse",
            "jäetakse välja",
            "lisatakse",
        )
    )
    plain_is_quoted_payload = bool(
        plain_text and plain_text[0] in ('"', "\u201e", "\u00ab", "\u201c")
    )
    header_has_instruction = bool(block_header_text) and any(
        kw in block_header_text.lower()
        for kw in (
            "muudetakse",
            "täiendatakse",
            "tunnistatakse",
            "asendatakse",
            "jäetakse välja",
            "lisatakse",
        )
    )
    if plain_is_quoted_payload and header_has_instruction:
        return [f"{block_header_text} {plain_text}".strip()]
    if plain_has_instruction:
        return [plain_text]
    if plain_text and (
        any(
            kw in plain_text.lower()
            for kw in (
                "paragrahvi",
                "paragrahvist",
                "lõiget",
                "lõikest",
                "lõikes",
                "muudetakse",
                "täiendatakse",
                "tunnistatakse",
                "asendatakse",
                "jäetakse välja",
                "lisatakse",
            )
        )
        or re.search(r"§\s*\d", plain_text)
    ):
        return [f"{block_header_text} {plain_text}".strip()]
    if block_header_text and any(
        kw in block_header_text.lower()
        for kw in (
            "tunnistatakse kehtetuks",
            "muudetakse",
            "täiendatakse",
            "asendatakse",
            "jäetakse välja",
            "lisatakse",
        )
    ):
        return [block_header_text]
    return []


def old_format_extract_section_header_text(section_html: str) -> str:
    """Extract and normalize the first section header text from an old-format section block."""
    sect_p = r"(?:§|&sect;)"
    bold_open_p = r"<(?:b|strong)\b[^>]*>"
    bold_close_p = r"</(?:b|strong)>"

    first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", section_html, re.DOTALL | re.IGNORECASE)
    if first_para_m:
        header_raw = first_para_m.group(1)
    else:
        bold_m = re.match(
            bold_open_p
            + r"\s*"
            + sect_p
            + r"\s*\d+[^<]*?"
            + bold_close_p
            + r"(.*?)(?="
            + bold_open_p
            + r"\s*1\)|tehakse)",
            section_html,
            re.DOTALL,
        )
        if not bold_m:
            header_raw = re.split(r"<(?:b|strong)>\s*1\)\s*</(?:b|strong)>", section_html)[0]
        else:
            header_raw = bold_m.group(0) + bold_m.group(1)

    header_text = re.sub(r"<[^>]+>", " ", header_raw)
    header_text = re.sub(r"&sect;", "§", header_text)
    header_text = html_lib.unescape(header_text)
    header_text = re.sub(r"\s+", " ", header_text).strip()
    return header_text


def old_format_extract_base_act_name(header_text: str) -> str:
    """Extract the provenance title from an old-format section header."""
    base_act_name = header_text.split("(", 1)[0].strip()
    base_act_name = re.sub(r"§\s*\d+\.?\s*", "", base_act_name).strip()
    return base_act_name


def old_format_make_source(*, source_id: str, base_act_name: str, work_header_text: str) -> OperationSource:
    """Build an OperationSource for one old-format work section."""
    return OperationSource(
        statute_id=source_id,
        title=base_act_name,
        raw_text=work_header_text[:200],
    )


def _old_format_header_names_specific_act(header_text: str) -> bool:
    """Return True when an old-format section header names a specific act.

    Generic wrappers such as ``§ 2. Seadust täiendatakse ...`` inherit the
    surrounding roman statute header; they must not split into standalone act
    sections merely because they contain the word ``seadust``.
    """
    text = re.sub(r"^\s*(?:§|&sect;)\s*\d+\.?\s*", "", header_text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    act_noun_p = (
        r"(?:seadus|seaduse|seadust|seadustik|seadustiku|seadustikku|"
        r"koodeks|koodeksi|koodeksit|määrus|määruse|määrust|määruses)"
    )
    generic = {
        "seadus",
        "seaduse",
        "seadust",
        "seadustiku",
        "seadustikku",
        "koodeksi",
        "koodeksit",
        "määruse",
        "määrust",
        "määruses",
    }
    first_word = text.split(" ", 1)[0].strip(".,;:()")
    if first_word in generic:
        return False
    if re.search(r"\bmäärus(?:t|e|es)?\s+nr\b", text):
        return True
    if re.search(r"\b(?:määrus|määruse|määrust|seadus|seaduse|seadust)\s+[„\"“]", text):
        return True
    return bool(
        re.search(rf"\b[a-zäöõüšž]{{3,}}{act_noun_p}\b", text)
        or re.search(rf"\b[a-zäöõüšž]{{3,}}\s+{act_noun_p}\b", text)
    )


def old_format_split_sections(full_html: str) -> list[str]:
    """Split old-format amendment HTML into act-targeting sections.

    First prefer bold section headers immediately followed by an RT reference.
    If that does not produce multiple sections, fall back to any bold section
    header that explicitly names a law/code, and finally to paragraph-based
    statute-section splitting.
    """
    sect_p = r"(?:§|&sect;)"
    rt_ref_p = r"\(RT\s+[IV]+[\s,]"
    bold_open_p = r"<(?:b|strong)\b[^>]*>"
    bold_close_p = r"</(?:b|strong)>"
    # Prefer the outer paragraph boundary when old-format headers are encoded
    # as <p><b>§ ...</b>. Matching both <p> and the nested <b> creates orphan
    # paragraph fragments that can merge unrelated statutes into one section.
    header_start_p = r"(?:<p\b[^>]*>\s*" + bold_open_p + r"|(?<!>)" + bold_open_p + r")"
    header_terms_p = (
        r"(?:seadus|seaduse|seadust|seadustik|seadustiku|seadustikku|"
        r"koodeks|koodeksi|koodeksit|määrus|määruse|määrust|määruses)\b"
    )

    sections = re.split(
        r"(?="
        + header_start_p
        + r"\s*"
        + sect_p
        + r"\s*\d+[^<]*?"
        + bold_close_p
        + r"\s*"
        + rt_ref_p
        + r")",
        full_html,
        flags=re.IGNORECASE,
    )
    if len(sections) == 1:
        candidates = re.split(
            r"(?="
            + header_start_p
            + r"\s*"
            + sect_p
            + r"\s*\d+[^<]*?"
            + bold_close_p
            + r")",
            full_html,
            flags=re.IGNORECASE,
        )
        if len(candidates) > 1:
            filtered = [candidates[0]]
            for sec in candidates[1:]:
                m = re.match(
                    header_start_p + r"\s*" + sect_p + r"\s*\d+[^<]*?" + bold_close_p,
                    sec,
                    flags=re.IGNORECASE,
                )
                if m:
                    first_para_m = re.match(
                        r"\s*(<p\b[^>]*>.*?</p>)",
                        sec,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    header_probe = first_para_m.group(1) if first_para_m is not None else sec[: m.end()]
                    hdr = re.sub(r"<[^>]+>", " ", header_probe)
                    hdr = html_lib.unescape(hdr).lower()
                    if re.search(header_terms_p, hdr) and _old_format_header_names_specific_act(hdr):
                        filtered.append(sec)
                        continue
                filtered[-1] += sec
            if len(filtered) > 1:
                sections = filtered
    if len(sections) == 1:
        paragraph_sections = split_old_format_paragraph_sections(full_html)
        if paragraph_sections:
            sections = paragraph_sections
    return sections


def old_format_normalize_header_text(raw_header_html: str) -> str:
    """Normalize an old-format header paragraph or fragment to plain text."""
    header_text = re.sub(r"<[^>]+>", " ", raw_header_html)
    header_text = html_lib.unescape(header_text)
    header_text = re.sub(r"\s+", " ", header_text).strip()
    return header_text


def old_format_split_content_blocks(content_section: str, work_header_text: str, *, has_subblocks: bool) -> tuple[list[str], bool]:
    """Split a work section into content blocks and indicate whether block-local headers apply."""
    content_blocks = [content_section]
    use_block_header = False
    if (
        not has_subblocks
        and re.search(
            r"<(?:b|strong)>\s*\(\d+\)\s*</(?:b|strong)>\s*Paragrahvi\s+\d",
            content_section,
            re.IGNORECASE,
        )
    ):
        split_blocks = split_old_format_top_level_parenthesized_blocks(content_section)
        if split_blocks:
            return split_blocks, True
    elif old_format_section_from_header_text(work_header_text):
        split_blocks = split_old_format_top_level_parenthesized_blocks(content_section)
        if split_blocks:
            return split_blocks, False
    return content_blocks, use_block_header


def old_format_prepare_work_section(
    work_section: str,
    *,
    has_subblocks: bool,
) -> tuple[str, str, list[str], bool] | None:
    """Normalize one old-format work section into header text and content blocks.

    Returns ``None`` when the work section is a normitehniline märkus block that
    should be skipped.
    """
    work_first_para_m = re.match(r"\s*(<p\b[^>]*>.*?</p>)", work_section, re.DOTALL | re.IGNORECASE)
    work_header_raw = work_first_para_m.group(1) if work_first_para_m else work_section
    work_header_text = old_format_normalize_header_text(work_header_raw)
    if re.search(r"normitehnili\w*\s+märkus\w*", work_header_text, re.IGNORECASE):
        return None

    content_section = (
        work_section[work_first_para_m.end():]
        if has_subblocks and work_first_para_m is not None
        else work_section
    )
    content_blocks, use_block_header = old_format_split_content_blocks(
        content_section,
        work_header_text,
        has_subblocks=has_subblocks,
    )
    return work_header_text, content_section, content_blocks, use_block_header


def old_format_resolve_block_header_text(content_block: str, work_header_text: str, *, use_block_header: bool) -> str:
    """Resolve the effective header text for one old-format content block."""
    if not use_block_header:
        return work_header_text
    block_first_para_m = re.match(
        r"\s*(<p\b[^>]*>.*?</p>)",
        content_block,
        re.DOTALL | re.IGNORECASE,
    )
    if block_first_para_m:
        return old_format_normalize_header_text(block_first_para_m.group(1))
    return work_header_text


def old_format_lower_op_texts(
    op_texts: list[str],
    source: OperationSource,
    *,
    seq_start: int,
    base_act_name: str = "",
    initial_last_section: str | None = None,
    amendment_section_label: str | None = None,
) -> tuple[list[LegalOperation], int, str | None]:
    """Lower old-format op texts with carried section context."""
    lowered: list[LegalOperation] = []
    global_seq = seq_start
    last_section = initial_last_section

    for op_text in op_texts:
        amendment_item_label = old_format_item_label(op_text)
        effective = op_text
        inherited_wrapper_scope = False
        is_container_heading_relabel = bool(
            re.search(r"\btekstiosa[a-z]*\s+[„\"“][^”\"]*?\bpeatükk\b", op_text, re.IGNORECASE)
            and re.search(r"\basendatakse\s+tekstiosaga\b", op_text, re.IGNORECASE)
        )
        if last_section and not old_format_has_section_ref(op_text) and not is_container_heading_relabel:
            last_sect_raw = last_section.replace("_", " ")
            item_body = old_format_strip_item_label(op_text)
            effective = f"paragrahvi {last_sect_raw} {item_body}"
        elif (
            not last_section
            and not old_format_has_section_ref(op_text)
            and re.match(r"^\(?\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\)\s*asendatakse\b|^asendatakse\b", op_text.strip(), re.IGNORECASE)
        ):
            item_body = old_format_strip_item_label(op_text)
            effective = f"määruses {item_body}"
            inherited_wrapper_scope = True
        ops = extract_ee_ops(effective, source, seq_start=global_seq)
        ops = [
            op
            for op in ops
            if not (
                op.action == StructuralAction.META
                and op.payload is None
                and op.op_id.startswith("ee-unknown-")
            )
        ]
        sect = old_format_section_from_ops(ops)
        if sect:
            last_section = sect
        tagged_ops: list[LegalOperation] = []
        for op in ops:
            tags = list(op.provenance_tags)
            if amendment_section_label:
                tags.append(f"old_format_amendment_section:{amendment_section_label}")
            if amendment_item_label:
                tags.append(f"old_format_amendment_item:{amendment_item_label}")
            if base_act_name:
                tags.append(f"base_act: {base_act_name}")
            witness_rule_id = op.witness_rule_id
            if inherited_wrapper_scope:
                tags.append(_EE_OLD_FORMAT_WRAPPER_SCOPE_INHERITED_RULE)
                if op.action is not StructuralAction.META and witness_rule_id is None:
                    witness_rule_id = _EE_OLD_FORMAT_WRAPPER_SCOPE_INHERITED_RULE
            tagged_ops.append(replace(op, provenance_tags=tuple(tags), witness_rule_id=witness_rule_id))
        ops = tagged_ops
        lowered.extend(ops)
        global_seq += len(ops)

    return lowered, global_seq, last_section


def old_format_collect_section_ops(
    section: str,
    *,
    source_id: str,
    target_title: str,
    seq_start: int,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    split_wrapper_blocks: Callable[[str], list[str]],
) -> tuple[list[LegalOperation], int]:
    """Collect lowered operations for one old-format act-target section."""
    header_text = old_format_extract_section_header_text(section)
    if not old_format_is_section_header_text(header_text):
        return [], seq_start
    if not old_format_section_targets_title(
        header_text=header_text,
        target_title=target_title,
        lookup_act_identity=lookup_act_identity,
    ):
        return [], seq_start

    all_ops: list[LegalOperation] = []
    global_seq = seq_start
    subblocks = split_wrapper_blocks(section)
    work_sections = subblocks or [section]
    amendment_section_label = old_format_act_section_from_header_text(header_text)

    for work_section in work_sections:
        prepared = old_format_prepare_work_section(
            work_section,
            has_subblocks=bool(subblocks),
        )
        if prepared is None:
            continue
        work_header_text, _content_section, content_blocks, use_block_header = prepared
        base_act_name = old_format_extract_base_act_name(header_text)
        source = old_format_make_source(
            source_id=source_id,
            base_act_name=base_act_name,
            work_header_text=work_header_text,
        )

        for content_block in content_blocks:
            block_header_text = old_format_resolve_block_header_text(
                content_block,
                work_header_text,
                use_block_header=use_block_header,
            )
            op_texts = old_format_extract_op_texts(content_block, block_header_text)
            initial_section = (
                old_format_section_from_header_text(block_header_text)
                or old_format_section_from_intro_context(content_block)
            )
            ops, global_seq, _ = old_format_lower_op_texts(
                op_texts,
                source,
                seq_start=global_seq,
                base_act_name=base_act_name,
                initial_last_section=initial_section,
                amendment_section_label=amendment_section_label,
            )
            all_ops.extend(ops)

    return all_ops, global_seq


def old_format_collect_fallback_ops(
    *,
    full_html: str,
    source_id: str,
    target_title: str,
    seq_start: int,
) -> tuple[list[LegalOperation], int]:
    """Lower the old-format single-act fallback path with carried section context."""
    source = OperationSource(statute_id=source_id, title=target_title or "")
    op_texts = parse_html_op_items(full_html)
    ops, global_seq, _ = old_format_lower_op_texts(
        op_texts,
        source,
        seq_start=seq_start,
    )
    return ops, global_seq


def old_format_collect_nested_direct_target_ops(
    *,
    full_html: str,
    source_id: str,
    target_title: str,
    seq_start: int,
) -> tuple[list[LegalOperation], int]:
    """Recover embedded direct target-law clauses inside old-format wrapper inserts.

    Some old-format omnibus acts amend an amendment-and-implementation act by
    inserting a provision whose body is itself an explicit amendment to another
    statute. This helper only admits the nested instruction when the quoted body
    names ``target_title`` directly, so a wrapper insert such as ``§ 89^1`` does
    not get replayed against the nested target statute.
    """
    if not target_title:
        return [], seq_start

    source = OperationSource(statute_id=source_id, title=target_title)
    lowered: list[LegalOperation] = []
    global_seq = seq_start

    for op_text in parse_html_op_items(full_html):
        nested = _extract_quoted_content(op_text)
        if not nested or "\x01" not in nested:
            continue
        nested_instruction = nested.split("\x01", 1)[1].strip()
        if not nested_instruction or not title_matches_para(target_title, nested_instruction):
            continue
        ops = extract_ee_ops(
            nested_instruction,
            replace(source, raw_text=nested_instruction[:200]),
            seq_start=global_seq,
        )
        amendment_item_label = old_format_item_label(op_text)
        for op in ops:
            if (
                op.action == StructuralAction.META
                and op.payload is None
                and op.op_id.startswith("ee-unknown-")
            ):
                continue
            tags = [
                *op.provenance_tags,
                "ee_nested_direct_target_law_clause",
            ]
            if amendment_item_label:
                tags.append(f"old_format_amendment_item:{amendment_item_label}")
            lowered.append(replace(op, provenance_tags=tuple(tags), sequence=global_seq))
            global_seq += 1

    return lowered, global_seq


def old_format_collect_all_ops(
    *,
    full_html: str,
    source_id: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    split_wrapper_blocks: Callable[[str], list[str]],
) -> list[LegalOperation]:
    """Collect all operations from an old-format amendment HTML body."""
    sections = old_format_split_sections(full_html)
    all_ops: list[LegalOperation] = []
    global_seq = 1

    for section in sections:
        ops, global_seq = old_format_collect_section_ops(
            section,
            source_id=source_id,
            target_title=target_title,
            seq_start=global_seq,
            lookup_act_identity=lookup_act_identity,
            split_wrapper_blocks=split_wrapper_blocks,
        )
        all_ops.extend(ops)

    if not all_ops and target_title:
        ops, global_seq = old_format_collect_nested_direct_target_ops(
            full_html=full_html,
            source_id=source_id,
            target_title=target_title,
            seq_start=global_seq,
        )
        all_ops.extend(ops)

    if not all_ops and (not target_title or len(sections) == 1):
        ops, global_seq = old_format_collect_fallback_ops(
            full_html=full_html,
            source_id=source_id,
            target_title=target_title,
            seq_start=global_seq,
        )
        all_ops.extend(ops)

    return all_ops


def _ns(ns_str: str, tag: str) -> str:
    return f"{{{ns_str}}}{tag}"


def _first_tavatekst_text(para: ET.Element, ns_str: str) -> str:
    for st in para.iter(_ns(ns_str, "sisuTekst")):
        for t in st.findall(_ns(ns_str, "tavatekst")):
            txt = " ".join(str(_t) for _t in t.itertext()).replace("\xa0", " ")
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                return txt
    return ""


def extract_intro_statute_fragment(text: str) -> str:  # noqa: F811
    """Extract a leading target-statute phrase from an untitled amendment clause."""
    if not text:
        return ""
    text = re.sub(r"\s*\(RT[^)]*\)", "", text, flags=re.IGNORECASE)
    year_prefix = ""
    year_match = re.match(r"^(\d{4}\.\s*aasta\s+)", text, re.IGNORECASE)
    if year_match is not None:
        year_prefix = year_match.group(1)
        text = text[year_match.end():]
    fragment = ""
    fragment_from_quoted_title = False
    quoted_title_match = re.match(
        r"^[A-ZÜÕÖÄ][^\n]{0,240}?\b(?:seaduse|seaduses|seadust|seadustiku|koodeksi|määruse|määruses|määrust)\b"
        r"(?:\s+nr\.?\s*[\w./-]+)?\s+[„\"“](?P<title>[^„”“\"]+)[”“\"]"
        r"\s+(?:§|paragrahv|\btehakse\b|\bmuudetakse\b|\btunnistatakse\b|\btäiendatakse\b|\bjäetakse\b)",
        text,
        re.IGNORECASE,
    )
    if quoted_title_match:
        fragment = quoted_title_match.group("title").strip()
        fragment_from_quoted_title = True
    section_scoped_match = re.match(
        r"^([A-ZÜÕÖÄ][^\n.]{4,180}?\b(?:seadus|seaduse|seadust|"
        r"seadustik|seadustiku|seadustikku|koodeks|koodeksi|koodeksit|"
        r"määrus|määruse|määrust)\b)"
        r"(\s+§[^.]*)?\s+"
        r"(muudetakse|asendatakse|tunnistatakse|täiendatakse|jäetakse)",
        text,
        re.IGNORECASE,
    )
    if not fragment and section_scoped_match:
        fragment = section_scoped_match.group(1).strip()
        if section_scoped_match.group(3).lower() == "jäetakse" and section_scoped_match.group(2):
            fragment = f"{fragment}{section_scoped_match.group(2)}"
    elif not fragment:
        direct_match = re.match(
            r"^([A-ZÜÕÖÄ][^\n.]{4,180}?)"
            r"(?:tehakse|tehtakse|asendatakse|tunnistatakse|täiendatakse|jäetakse)",
            text,
            re.IGNORECASE,
        )
        if direct_match:
            fragment = direct_match.group(1).strip()
    if not fragment:
        return ""
    if year_prefix:
        fragment = f"{year_prefix}{fragment}".strip()
    if not fragment_from_quoted_title and not any(
        token in fragment.lower()
        for token in (
            "seadus",
            "seaduse",
            "seadust",
            "seadustik",
            "seadustiku",
            "seadustikku",
            "koodeks",
            "koodeksi",
            "koodeksit",
            "määrus",
            "määruse",
            "määrust",
        )
    ):
        return ""
    return fragment


def is_specific_direct_target_fragment(fragment: str) -> bool:  # noqa: F811
    """Return True when a clause fragment names a statute, not just a generic law noun."""
    if not fragment:
        return False
    cleaned = re.sub(r"\s+", " ", fragment.strip())
    lowered = cleaned.lower()
    generic_only = {
        "seadus", "seaduse", "seadust",
        "seadustik", "seadustiku", "seadustikku",
        "koodeks", "koodeksi", "koodeksit",
        "määrus", "määruse", "määrust",
    }
    if lowered in generic_only:
        return False
    if re.match(
        r"^(?:käesolev|nimetatud|see|sama|kõnealune|nimetatud)\s+"
        r"(?:seadus|seaduse|seadust|seadustik|seadustiku|seadustikku|koodeks|koodeksi|koodeksit|määrus|määruse|määrust)\b",
        lowered,
        re.IGNORECASE,
    ):
        return False
    return True


def direct_target_clause_matches_registry(  # noqa: F811
    *,
    fragment: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
) -> bool:
    """Resolve a direct-target fragment against the act-identity registry."""
    if not is_specific_direct_target_fragment(fragment):
        return False
    registry_record = lookup_act_identity(alias=fragment or "")
    if registry_record is not None:
        if _registry_record_matches_all(registry_record, target_title, fragment):
            return True
    registry_record = lookup_act_identity(title=fragment, alias=target_title)
    if registry_record is not None:
        if _registry_record_matches_all(registry_record, target_title, fragment):
            return True
    return title_matcher(target_title, fragment)


def paragrahv_to_act_id(title: str) -> str:  # noqa: F811
    """Heuristically derive a short base act name from a paragrahv title."""
    title = re.sub(r"\s+muutmine\s*$", "", title.strip(), flags=re.IGNORECASE)
    title = re.sub(r"\s+§.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"(seadustiku|seaduse|koodeksi)$",
        lambda m: {
            "seadustiku": "seadustik",
            "seaduse": "seadus",
            "koodeksi": "koodeks",
        }.get(m.group(1), m.group(1)),
        title,
        flags=re.IGNORECASE,
    )
    return title.lower().strip()


def looks_like_self_referential_amendment_act_para(
    target_title: str,
    para_title: str,
    first_tava: str,
    *,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
) -> bool:
    """Return True when a para title contains the target only as part of a longer act title."""
    if not target_title or not para_title or not first_tava:
        return False

    def _normalize(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+muutmine\s*$", "", text)
        return text

    target_norm = _normalize(target_title)
    para_norm = _normalize(para_title)
    first_norm = re.sub(r"\s+", " ", first_tava.strip().lower())
    derived_target = paragrahv_to_act_id(para_title)
    registry_record = lookup_act_identity(title=para_title, alias=target_title)
    quoted_target = _extract_quoted_content(para_title)

    if para_norm == target_norm or (derived_target and derived_target == target_norm):
        return False
    if quoted_target and title_matches_para(target_title, quoted_target):
        return False
    if registry_record is not None and getattr(registry_record, "source_family", "") == "self_referential_amendment_act":
        return True

    return (
        bool(target_norm)
        and target_norm in para_norm
        and para_norm != target_norm
        and first_norm.startswith(para_norm)
    )


def para_contains_direct_target_clause(
    para: ET.Element,
    ns_str: str,
    target_title: str,
    *,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
) -> bool:
    """Detect direct target-statute clauses embedded inside a non-target paragraph."""
    if not target_title:
        return False
    for st in para.iter(_ns(ns_str, "sisuTekst")):
        for hk in st.findall(_ns(ns_str, "HTMLKonteiner")):
            for item_text in parse_html_op_items(hk.text or ""):
                item_plain = re.sub(r"^\(?\d[\d\s_]*\)\s*", "", item_text).strip()
                stat_fragment = extract_intro_statute_fragment(item_plain)
                if direct_target_clause_matches_registry(
                    fragment=stat_fragment,
                    target_title=target_title,
                    lookup_act_identity=lookup_act_identity,
                    title_matcher=title_matcher,
                ):
                    return True
                if is_specific_direct_target_fragment(stat_fragment) and title_matcher(target_title, stat_fragment):
                    return True
                nested = _extract_quoted_content(item_plain)
                if nested:
                    nested_fragment = extract_intro_statute_fragment(nested)
                    if direct_target_clause_matches_registry(
                        fragment=nested_fragment,
                        target_title=target_title,
                        lookup_act_identity=lookup_act_identity,
                        title_matcher=title_matcher,
                    ):
                        return True
                    if is_specific_direct_target_fragment(nested_fragment) and title_matcher(target_title, nested_fragment):
                        return True
        for t in st.findall(_ns(ns_str, "tavatekst")):
            txt = " ".join(str(_t) for _t in t.itertext()).replace("\xa0", " ")
            txt = re.sub(r"\s+", " ", txt).strip()
            stat_fragment = extract_intro_statute_fragment(txt)
            if direct_target_clause_matches_registry(
                fragment=stat_fragment,
                target_title=target_title,
                lookup_act_identity=lookup_act_identity,
                title_matcher=title_matcher,
            ):
                return True
            if is_specific_direct_target_fragment(stat_fragment) and title_matcher(target_title, stat_fragment):
                return True
    return False


def is_omnibus_amendment(
    root: ET.Element,
    ns_str: str,
    target_title: str,
    *,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    strict_title_matcher: Callable[[str, str], bool] = strict_title_match_para,
) -> bool:
    """Return True if this amendment act contains paragrahvs targeting different statutes."""
    if not target_title:
        return False
    for para in root.iter(_ns(ns_str, "paragrahv")):
        ptitle_el = para.find(_ns(ns_str, "paragrahvPealkiri"))
        ptitle = ((ptitle_el.text or "").replace("\xa0", " ").strip() if ptitle_el is not None else "")
        first_tava = _first_tavatekst_text(para, ns_str)
        stat_fragment = extract_intro_statute_fragment(first_tava)
        is_statute_specific = False
        candidate_title = ptitle
        registry_record = lookup_act_identity(title=ptitle, alias=stat_fragment or target_title)
        if ptitle:
            pt_lower = ptitle.lower()
            is_statute_specific = any(kw in pt_lower for kw in ("muutmine", "kehtetuks tunnistamine", "täiendamine"))
        elif stat_fragment:
            candidate_title = stat_fragment
            is_statute_specific = True
        if not is_statute_specific:
            continue
        if registry_record is not None:
            if (
                _registry_record_matches_all(registry_record, target_title, stat_fragment)
                or _registry_record_matches_all(registry_record, target_title, ptitle)
            ):
                continue
        if not strict_title_matcher(target_title, candidate_title):
            return True
    return False


def parse_constitutional_review_ops(
    xml_bytes: bytes,
    *,
    source_id: str,
    target_title: str,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
    normalize_num: Callable[[str], str] = _normalize_num,
    extract_ops: Callable[[str, OperationSource], list[LegalOperation]] = extract_ee_ops,
) -> list[LegalOperation]:
    """Handle Riigikohus constitutional-review judgments that invalidate provisions."""
    if not target_title:
        return []
    xml_text = xml_bytes.decode("utf-8", errors="ignore")
    xml_lower = xml_text.lower()
    if "põhiseaduspärasuse kontroll" not in xml_lower:
        return []
    if "põhiseadusega vastuolus olevaks ja kehtetuks" not in xml_lower:
        return []

    plain = re.sub(r"<[^>]+>", " ", xml_text)
    plain = html_lib.unescape(plain).replace("\xa0", " ")
    plain = re.sub(r"\s+", " ", plain).strip()
    match = re.search(
        r"Tunnistada\s+(.+?)\s+§\s*(\d[\d\s]*)"
        r"(?:\s+(?:lõige|lõike(?:s|st|ga|t)?)\s*(\d[\d\s]*))?"
        r"(?:\s+(?:punkt|punkti|punktis|punktist|punktiga)\s*(\d[\d\s]*))?"
        r"\s+põhiseadusega vastuolus olevaks ja kehtetuks\.",
        plain,
        flags=re.IGNORECASE,
    )
    if match is None:
        return []
    statute_fragment = re.sub(r"\s+", " ", match.group(1).strip())
    registry_record = lookup_act_identity(akt_viide=source_id, title=statute_fragment, alias=target_title)
    if registry_record is None:
        if not title_matcher(target_title, statute_fragment):
            return []
    else:
        if not _registry_record_matches_all(registry_record, target_title, statute_fragment):
            if not title_matcher(target_title, statute_fragment):
                return []

    section = normalize_num(match.group(2).strip()).replace("_", " ")
    subsection = normalize_num(match.group(3).strip()).replace("_", " ") if match.group(3) else ""
    item = normalize_num(match.group(4).strip()).replace("_", " ") if match.group(4) else ""
    clause = f"paragrahvi {section}"
    if subsection:
        clause += f" lõike {subsection}"
    if item:
        clause += f" punkti {item}"
    clause += " tunnistatakse kehtetuks."
    source = OperationSource(statute_id=source_id, title="põhiseaduspärasuse kontroll", raw_text=clause)
    return extract_ops(clause, source)


def parse_preambul_single_target_ops(
    root: ET.Element,
    source_id: str,
    ns_str: str,
    target_title: str,
    *,
    lookup_act_identity: Callable[..., object | None] = lookup_ee_act_identity,
    title_matcher: Callable[[str, str], bool] = title_matches_para,
    tavatekst_text: Callable[[ET.Element, str], str],
    parse_muutmisseadus_ops: Callable[[ET.Element, str, str, str], list[LegalOperation]],
) -> list[LegalOperation]:
    """Handle single-target amendment acts expressed as preambul plus one content block."""
    if not target_title:
        return []
    sisu = root.find(_ns(ns_str, "sisu"))
    if sisu is None:
        return []
    preambul = sisu.find(_ns(ns_str, "preambul"))
    if preambul is None:
        return []
    pre_tava_el = preambul.find(_ns(ns_str, "tavatekst"))
    pre_tava = tavatekst_text(pre_tava_el, ns_str) if pre_tava_el is not None else ""
    if not pre_tava:
        return []
    stat_fragment = extract_intro_statute_fragment(pre_tava)
    direct_sisu_blocks = [child for child in list(sisu) if child.tag == _ns(ns_str, "sisuTekst")]
    intro_tava = pre_tava
    if not stat_fragment:
        for child in direct_sisu_blocks:
            for text_el in child.findall(_ns(ns_str, "tavatekst")):
                candidate = tavatekst_text(text_el, ns_str)
                candidate_intro = re.sub(r"^§\s*\d+\.\s*", "", candidate).strip()
                target_intro_match = re.match(
                    r"(.{0,500}?\btehakse\s+järgmised\s+muudatused:)",
                    candidate_intro,
                    re.IGNORECASE,
                )
                if target_intro_match is not None:
                    candidate_intro = target_intro_match.group(1).strip()
                candidate_fragment = extract_intro_statute_fragment(candidate_intro)
                if candidate_fragment:
                    stat_fragment = candidate_fragment
                    intro_tava = candidate_intro
                    break
            if stat_fragment:
                break
            for html_el in child.findall(_ns(ns_str, "HTMLKonteiner")):
                html_text = "".join(str(part) for part in html_el.itertext())
                for para_html in re.findall(r"<p\b[^>]*>.*?</p>", html_text, flags=re.IGNORECASE | re.DOTALL):
                    candidate = plain_html_text(para_html)
                    candidate_fragment = extract_intro_statute_fragment(candidate)
                    if candidate_fragment:
                        stat_fragment = candidate_fragment
                        intro_tava = candidate
                        break
                if stat_fragment:
                    break
            if stat_fragment:
                break
    registry_record = lookup_act_identity(akt_viide=source_id, title=stat_fragment, alias=target_title)
    if registry_record is None:
        if not (stat_fragment and title_matcher(target_title, stat_fragment)):
            return []
    else:
        if not _registry_record_matches_all(registry_record, target_title, stat_fragment):
            if not (stat_fragment and title_matcher(target_title, stat_fragment)):
                return []

    if not direct_sisu_blocks:
        return extract_ee_ops(
            intro_tava,
            OperationSource(statute_id=source_id, title=target_title, raw_text=intro_tava),
        )

    synthetic_root = ET.Element(root.tag)
    synthetic_sisu = ET.SubElement(synthetic_root, _ns(ns_str, "sisu"))
    synthetic_para = ET.SubElement(synthetic_sisu, _ns(ns_str, "paragrahv"))
    para_title = ET.SubElement(synthetic_para, _ns(ns_str, "paragrahvPealkiri"))
    para_title.text = f"{target_title} muutmine"

    first_st = ET.SubElement(synthetic_para, _ns(ns_str, "sisuTekst"))
    first_t = ET.SubElement(first_st, _ns(ns_str, "tavatekst"))
    first_t.text = intro_tava
    for child in direct_sisu_blocks:
        cloned_child = ET.fromstring(ET.tostring(child, encoding="utf-8"))
        for text_el in cloned_child.iter(_ns(ns_str, "tavatekst")):
            extracted_text = tavatekst_text(text_el, ns_str)
            text_el.clear()
            text_el.text = extracted_text
        synthetic_para.append(cloned_child)
    return parse_muutmisseadus_ops(synthetic_root, source_id, ns_str, target_title)


__all__ = [
    "NewFormatGateFlags",
    "NewFormatParagraphContext",
    "direct_target_clause_matches_registry",
    "extract_intro_statute_fragment",
    "is_omnibus_amendment",
    "is_specific_direct_target_fragment",
    "looks_like_self_referential_amendment_act_para",
    "matches_target_statute_header",
    "new_format_collect_all_ops",
    "new_format_lower_op_texts",
    "old_format_section_matches_target",
    "parse_constitutional_review_ops",
    "parse_preambul_single_target_ops",
    "para_contains_direct_target_clause",
    "paragrahv_to_act_id",
    "prepare_new_format_gate_flags",
    "prepare_new_format_paragraph_context",
    "strict_title_match_para",
    "title_matches_para",
]
