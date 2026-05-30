from __future__ import annotations

from datetime import date
from itertools import combinations
import re
from typing import Any, Callable, Mapping

import Levenshtein

from lawvm.tools.editorial_hygiene import strip_editorial_annotations


def _clean(text: str) -> str:
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def looks_like_bare_section_stub(text: str) -> bool:
    squashed = re.sub(r"\s+", " ", text).strip()
    if not squashed:
        return True
    stripped = squashed
    for _ in range(2):
        stripped2 = re.sub(
            r"^\d+\s*[a-zäöå]?\s*§\s*",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if stripped2 == stripped:
            break
        stripped = stripped2
    alpha = re.sub(r"[^a-zäöå]", "", stripped.lower())
    return len(alpha) <= 12


def oracle_text_reduces_to_bare_section_stub(text: str) -> bool:
    """Return True when editorial stripping leaves only a bare section stub."""

    return looks_like_bare_section_stub(strip_editorial_annotations(text))


def blame_title_indicates_temporary_amendment(title: str) -> bool:
    lowered = (title or "").lower()
    return any(token in lowered for token in ("väliaikais", "tilapäis", "määräaikais"))


def is_probable_repeal_stale_oracle(
    replay_text: str,
    oracle_text: str,
    pre_blame_text: str,
) -> bool:
    replay_c = _clean(replay_text)
    oracle_c = _clean(oracle_text)
    pre_c = _clean(pre_blame_text)
    if not replay_c or not oracle_c or not pre_c:
        return False

    post_score = Levenshtein.ratio(replay_c, oracle_c)
    pre_score = Levenshtein.ratio(pre_c, oracle_c)

    if pre_score < 0.55:
        return False
    if pre_score <= post_score + 0.35:
        return False

    if "kumottu" not in replay_text.lower() and not looks_like_bare_section_stub(replay_text):
        return False

    if len(replay_c) > max(40, len(oracle_c) // 4):
        return False

    return True


_SECTION_KEY_RE = re.compile(r"^(?:(?P<prefix>.*)/)?section:(?P<label>\d+)$")


def oracle_section_duplicates_adjacent_section(
    section_key: str,
    oracle_text: str,
    oracle_text_by_key: Mapping[str, str],
    *,
    min_ratio: float = 0.98,
) -> bool:
    """Return True when an oracle section is effectively duplicated from ±1 neighbor."""
    match = _SECTION_KEY_RE.fullmatch(section_key)
    if match is None:
        return False

    current = _clean(oracle_text)
    if not current:
        return False

    prefix = match.group("prefix") or ""
    label = int(match.group("label"))
    for delta in (-1, 1):
        neighbor_key = f"{prefix + '/' if prefix else ''}section:{label + delta}"
        neighbor_text = oracle_text_by_key.get(neighbor_key, "")
        neighbor_clean = _clean(neighbor_text)
        if not neighbor_clean:
            continue
        if Levenshtein.ratio(current, neighbor_clean) >= min_ratio:
            return True
    return False


def oracle_text_has_removable_duplicate_sentence(
    replay_text: str,
    oracle_text: str,
    *,
    min_ratio: float = 0.995,
) -> bool:
    """Return True when oracle differs only by one duplicated sentence fragment.

    This targets oracle-side consolidation drift where a same-section sentence
    survives twice after a later amendment folded or moved it. We only accept
    the match when removing one duplicate oracle sentence collapses back onto
    replay at near-identity, keeping the heuristic bounded to clear duplicate
    residue rather than general paraphrase drift.
    """

    def _sentences(text: str) -> list[str]:
        parts = [
            re.sub(r"\s+", " ", part).strip()
            for part in re.split(r"(?<=[.!?])\s+", text or "")
        ]
        return [part for part in parts if part]

    replay_sentences = _sentences(replay_text)
    oracle_sentences = _sentences(oracle_text)
    if not replay_sentences or len(oracle_sentences) < 2:
        return False

    replay_counts: dict[str, int] = {}
    for sentence in replay_sentences:
        cleaned = _clean(sentence)
        if cleaned:
            replay_counts[cleaned] = replay_counts.get(cleaned, 0) + 1

    oracle_counts: dict[str, int] = {}
    for sentence in oracle_sentences:
        cleaned = _clean(sentence)
        if cleaned:
            oracle_counts[cleaned] = oracle_counts.get(cleaned, 0) + 1

    removable_duplicate_cleans = {
        cleaned
        for cleaned, count in oracle_counts.items()
        if count >= 2 and replay_counts.get(cleaned, 0) == 1
    }
    if not removable_duplicate_cleans:
        return False

    replay_clean = _clean(replay_text)
    if not replay_clean:
        return False

    for index, sentence in enumerate(oracle_sentences):
        cleaned = _clean(sentence)
        if cleaned not in removable_duplicate_cleans:
            continue
        reduced_oracle = " ".join(
            part for idx, part in enumerate(oracle_sentences) if idx != index
        )
        if Levenshtein.ratio(replay_clean, _clean(reduced_oracle)) >= min_ratio:
            return True
    return False


def oracle_text_reduces_to_replay_by_dropping_sentences(
    replay_text: str,
    oracle_text: str,
    *,
    max_drop: int = 2,
    min_ratio: float = 0.995,
) -> bool:
    """Return True when oracle collapses to replay by removing 1..N sentences.

    This captures oracle-side same-section residue where Finlex retains one or
    two superseded sentences after a later amendment has already changed the
    live wording. The heuristic is intentionally bounded:

    - only whole-sentence deletions are allowed
    - at most ``max_drop`` oracle sentences may be removed
    - the reduced oracle must then match replay at near-identity
    """

    def _sentences(text: str) -> list[str]:
        parts = [
            re.sub(r"\s+", " ", part).strip()
            for part in re.split(r"(?<=[.!?])\s+", text or "")
        ]
        return [part for part in parts if part]

    replay_clean = _clean(replay_text)
    oracle_sentences = _sentences(oracle_text)
    if not replay_clean or len(oracle_sentences) < 2:
        return False

    max_drop = max(1, min(max_drop, len(oracle_sentences) - 1))
    for drop_count in range(1, max_drop + 1):
        for indexes in combinations(range(len(oracle_sentences)), drop_count):
            reduced_oracle = " ".join(
                part for idx, part in enumerate(oracle_sentences) if idx not in indexes
            )
            if Levenshtein.ratio(replay_clean, _clean(reduced_oracle)) >= min_ratio:
                return True
    return False


# Compiled at module scope per §1.11.  Bounded — unbounded
# .*? with DOTALL risked O(N^2) backtracking on long oracle excerpts.
# 500 chars is generous: the banner + prior-wording header fits in ~100 chars
# in practice; 500 provides safety margin for edge cases.
_REPEAL_PRIOR_WORDING_BANNER_RE = re.compile(
    r"\bon\s+kumottu\b.{0,500}?\baiempi\s+sanamuoto\s+kuuluu\s*:",
    re.IGNORECASE | re.DOTALL,
)


def oracle_has_repeal_banner_with_prior_wording(text: str) -> bool:
    """Return True when oracle shows a repeal banner plus prior-wording header."""

    squashed = re.sub(r"\s+", " ", text or "").strip()
    if not squashed:
        return False
    # Fast guard: "aiempi sanamuoto" is required by the regex terminal.
    if 'aiempi sanamuoto' not in squashed.lower():
        return False
    return bool(_REPEAL_PRIOR_WORDING_BANNER_RE.search(squashed))


# Three-anchor variant: each .*? segment bounded to 500 chars.
_FUTURE_REPEAL_OVERLAY_RE = re.compile(
    r"on\s+kumottu\b.{0,500}?\bjoka\s+tulee\s+voimaan\b.{0,500}?\baiempi\s+sanamuoto\s+kuuluu\s*:",
    re.IGNORECASE | re.DOTALL,
)


def oracle_has_future_repeal_overlay(text: str) -> bool:
    """Return True when oracle text is replaced by a future-effective repeal banner.

    In legal PIT mode, a consolidated oracle can expose a section as already
    repealed even though the cited repeal amendment only comes into force after
    the PIT cutoff date. Those overlays typically read like:

        ``11 § on kumottu ... joka tulee voimaan DD.MM.YYYY. Aiempi sanamuoto kuuluu:``

    That is oracle-side stale state, not replay semantics.
    """
    squashed = re.sub(r"\s+", " ", text or "").strip()
    if not squashed:
        return False
    # Fast guards: both anchors are required by the regex.
    lo = squashed.lower()
    if 'aiempi sanamuoto' not in lo:
        return False
    if 'tulee voimaan' not in lo:
        return False
    return bool(_FUTURE_REPEAL_OVERLAY_RE.search(squashed))


def blame_source_postdates_oracle_version(
    blame_source: str,
    oracle_version_amendment_id: str,
) -> bool:
    """Return True when a blamed amendment is newer than the oracle PIT version."""

    def _parse(mid: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"(\d{4})/(\d+)", (mid or "").strip())
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

    blame_key = _parse(blame_source)
    oracle_key = _parse(oracle_version_amendment_id)
    if blame_key is None or oracle_key is None:
        return False
    return blame_key > oracle_key


def replay_section_has_future_effective_version(
    replay_result: Any,
    section_key: str,
    oracle_cutoff_date: date | None,
) -> bool:
    """Return True when the replayed section's latest version is future-dated.

    This is a narrow compare heuristic for statutes where replay materializes a
    later effective version than the oracle PIT.  In those cases the mismatch is
    source/version residue, not a replay topology bug.
    """
    if oracle_cutoff_date is None:
        return False
    timelines = getattr(replay_result, "timelines", None)
    if not timelines:
        return False

    target_versions: list[str] = []
    for address, timeline in getattr(timelines, "items", lambda: ())():
        if str(address) != section_key:
            continue
        versions = getattr(timeline, "versions", None) or ()
        for version in versions:
            effective = str(getattr(version, "effective", "") or "")
            if effective:
                target_versions.append(effective)
        break

    if not target_versions:
        return False

    latest_effective = max(target_versions)
    return latest_effective > oracle_cutoff_date.isoformat()


def replay_section_matches_text_at_cutoff(
    replay_result: Any,
    section_key: str,
    oracle_text: str,
    cutoff_iso: str,
    *,
    statute_id: str,
    title: str,
    label_norm: Callable[[str], str] | None = None,
    min_ratio: float = 0.98,
) -> bool:
    """Return True when cutoff-date rematerialization matches oracle text closely.

    This is used for mixed consolidated snapshots where Finlex exposes some
    future-effective oracle-version content while other sections still reflect
    the earlier cutoff state. In that case the normal replay product may be
    anchored to the oracle-version amendment date, but a rematerialization at
    the actual cutoff date can still witness that the oracle-side divergence is
    editorial/stale rather than a replay bug.
    """
    if not cutoff_iso or not oracle_text:
        return False
    if not _supports_cutoff_witness_rematerialization(replay_result):
        return False
    timelines = replay_result.timelines
    base_body = replay_result.replay_fold_state.ir
    migration_events = replay_result.products.migration_events
    if not timelines or base_body is None:
        return False

    from lawvm.core.ir import IRStatute
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.core.timeline import materialize_pit_ex
    from lawvm.tools.section_keys import extract_ir_sections

    witness = materialize_pit_ex(
        timelines,
        cutoff_iso,
        base=IRStatute(statute_id=statute_id, title=title, body=base_body),
        label_norm=label_norm,
        expires_as_of=cutoff_iso,
        migration_events=migration_events,
    )
    witness_sections = extract_ir_sections(witness.statute.body)
    witness_node = witness_sections.get(section_key)
    if witness_node is None:
        return False

    witness_clean = _clean(irnode_to_text(witness_node))
    oracle_clean = _clean(oracle_text)
    if not witness_clean or not oracle_clean:
        return False
    return Levenshtein.ratio(witness_clean, oracle_clean) >= min_ratio


def _supports_cutoff_witness_rematerialization(replay_result: Any) -> bool:
    if not hasattr(replay_result, "timelines"):
        return False
    if not replay_result.timelines:
        return False
    if not hasattr(replay_result, "replay_fold_state"):
        return False
    replay_fold_state = replay_result.replay_fold_state
    if replay_fold_state is None or not hasattr(replay_fold_state, "ir"):
        return False
    if replay_fold_state.ir is None:
        return False
    if not hasattr(replay_result, "products"):
        return False
    products = replay_result.products
    if products is None or not hasattr(products, "migration_events"):
        return False
    return True
