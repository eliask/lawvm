"""H4 — shared replay-vs-oracle divergence diagnosis core."""
from __future__ import annotations

import re
from typing import Any, Literal

import Levenshtein

from lawvm.finland.oracle_comparison import (
    strip_editorial_annotations,
    strip_figure_legend_paragraphs,
    strip_kumottu_attribution,
    strip_non_substantive_source_projection_residue,
    strip_standalone_subsection_ordinals,
    strip_temporary_residue_annotations,
)
from lawvm.tools.divergence_heuristics import (
    high_overlap_text_corruption,
    oracle_text_has_removable_duplicate_sentence,
    oracle_text_reduces_to_bare_section_stub,
)

DiagnosisCode = Literal[
    "ORACLE_STALE",
    "REPLAY_EXTRA",
    "REPLAY_MISSING",
    "EDITORIAL_CONVENTION",
    "SOURCE_PATHOLOGY",
    "UNKNOWN",
]


_INLINE_EDITORIAL_RESIDUE_MARKERS = (
    "aiempi sanamuoto",
    "l:lla",
    "tulee voimaan",
    "tuli voimaan",
    "väliaikaisesti voimassa",
    "oli väliaikaisesti",
    "on kumottu",
)


def has_inline_editorial_residue_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INLINE_EDITORIAL_RESIDUE_MARKERS)


def clean_comparison_text(text: str) -> str:
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def diagnose_section_divergence(
    replay_text: str,
    oracle_text: str,
    blame_op: dict[str, Any] | Any | None,
    *,
    oracle_selector_mode: str = "latest_cached_editorial",
    include_explanation: bool = False,
) -> DiagnosisCode | tuple[DiagnosisCode, str]:
    o_without_temporary = strip_temporary_residue_annotations(oracle_text)
    if o_without_temporary != oracle_text:
        if (
            oracle_selector_mode == "bench_comparable"
            and oracle_text_reduces_to_bare_section_stub(oracle_text)
        ):
            return _finish(
                "EDITORIAL_CONVENTION",
                "bench-comparable oracle carries only temporary-law editorial residue",
                include_explanation,
            )
        if Levenshtein.ratio(clean_comparison_text(replay_text), clean_comparison_text(o_without_temporary)) >= 0.95:
            return _finish(
                "ORACLE_STALE",
                "oracle retains expired temporary-residue annotations beyond the live replay state",
                include_explanation,
            )

    oracle_lower = oracle_text.lower()
    stub_text = strip_editorial_annotations(oracle_text)
    if not any(token in oracle_lower for token in ("kumottu", "väliaik", "voimassa", "tulee voimaan")):
        stripped_stub = re.sub(
            r"^\d+\s*[a-zäöå]?\s*§\s*",
            "",
            stub_text,
            count=2,
            flags=re.IGNORECASE,
        ).strip()
        if not stripped_stub:
            return _finish(
                "EDITORIAL_CONVENTION",
                "oracle is a bare section stub with no substantive text",
                include_explanation,
            )

    replay_stripped = strip_editorial_annotations(replay_text)
    oracle_stripped = strip_editorial_annotations(oracle_text)
    replay_clean = clean_comparison_text(replay_stripped)
    oracle_clean = clean_comparison_text(oracle_stripped)
    if Levenshtein.ratio(replay_clean, oracle_clean) >= 0.999:
        return _finish(
            "EDITORIAL_CONVENTION",
            "divergence is repeal placeholders or date annotations — oracle editorial choice",
            include_explanation,
        )

    if "kumottu" in replay_text and "kumottu" in oracle_text:
        replay_k = strip_kumottu_attribution(replay_text)
        oracle_k = strip_kumottu_attribution(oracle_text)
        if Levenshtein.ratio(clean_comparison_text(replay_k), clean_comparison_text(oracle_k)) >= 0.95:
            return _finish(
                "EDITORIAL_CONVENTION",
                "divergence is repeal attribution or aiempi-sanamuoto residue — oracle editorial choice",
                include_explanation,
            )

    if high_overlap_text_corruption(replay_stripped, oracle_stripped):
        return _finish(
            "SOURCE_PATHOLOGY",
            "same-section source/oracle text mostly overlaps but one witness is corrupted",
            include_explanation,
        )

    if (
        replay_clean
        and oracle_clean
        and (
            has_inline_editorial_residue_marker(replay_text)
            or has_inline_editorial_residue_marker(oracle_text)
        )
        and Levenshtein.ratio(replay_clean, oracle_clean) >= 0.95
    ):
        return _finish(
            "EDITORIAL_CONVENTION",
            "divergence is inline editorial residue — oracle editorial choice",
            include_explanation,
        )

    if oracle_text_has_removable_duplicate_sentence(replay_text, oracle_text):
        return _finish(
            "ORACLE_STALE",
            "oracle duplicates one same-section sentence fragment beyond the replay/source-backed text",
            include_explanation,
        )

    oracle_legend_stripped = strip_figure_legend_paragraphs(oracle_text)
    if oracle_legend_stripped != oracle_text:
        oracle_legend_clean = clean_comparison_text(oracle_legend_stripped)
        if replay_clean and oracle_legend_clean and Levenshtein.ratio(replay_clean, oracle_legend_clean) >= 0.95:
            return _finish(
                "EDITORIAL_CONVENTION",
                "oracle carries figure-legend caption paragraphs absent from replay",
                include_explanation,
            )

    replay_source_residue_stripped = strip_non_substantive_source_projection_residue(replay_text)
    if replay_source_residue_stripped != replay_text:
        residue_clean = clean_comparison_text(strip_editorial_annotations(replay_source_residue_stripped))
        if residue_clean and oracle_clean and Levenshtein.ratio(residue_clean, oracle_clean) >= 0.999:
            return _finish(
                "EDITORIAL_CONVENTION",
                "replay carries non-substantive source heading/promulgation residue absent from oracle",
                include_explanation,
            )

    oracle_without_subsection_ordinals = strip_standalone_subsection_ordinals(oracle_text)
    if oracle_without_subsection_ordinals != oracle_text:
        ordinal_clean = clean_comparison_text(strip_editorial_annotations(oracle_without_subsection_ordinals))
        if replay_clean and ordinal_clean and Levenshtein.ratio(replay_clean, ordinal_clean) >= 0.999:
            return _finish(
                "EDITORIAL_CONVENTION",
                "oracle carries subsection ordinal prefixes already represented by structure",
                include_explanation,
            )

    clean_len_diff = len(clean_comparison_text(replay_text)) - len(clean_comparison_text(oracle_text))
    source = _blame_field(blame_op, "source_statute", "?")
    action = _blame_field(blame_op, "action", "").upper()

    if clean_len_diff > 40:
        if blame_op and action in ("REPLACE", "INSERT"):
            return _finish(
                "ORACLE_STALE",
                f"replay incorporates {source}, oracle may not have this amendment",
                include_explanation,
            )
        return _finish(
            "REPLAY_EXTRA",
            "replay has significantly more content — possible double-insert or uncleaned placeholder",
            include_explanation,
        )

    if clean_len_diff < -40:
        return _finish(
            "REPLAY_MISSING",
            "replay has significantly less content — possible missed operation",
            include_explanation,
        )

    return _finish(
        "UNKNOWN",
        "similar length but different content — needs manual investigation",
        include_explanation,
    )


def _blame_field(blame_op: dict[str, Any] | Any | None, key: str, default: str) -> str:
    if blame_op is None:
        return default
    if isinstance(blame_op, dict):
        return str(blame_op.get(key) or default)
    return str(getattr(blame_op, key, default) or default)


def _finish(
    code: DiagnosisCode,
    explanation: str,
    include_explanation: bool,
) -> DiagnosisCode | tuple[DiagnosisCode, str]:
    if include_explanation:
        return code, explanation
    return code
