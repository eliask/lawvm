"""Typed UK oracle/source adjudication helpers.

Architectural observations
--------------------------
- UK already has a reasonably explicit adjudication vocabulary, which is good.
- Cross-jurisdiction comparison still shows UK findings as wrapper-surface
  kinds rather than shared direct/core kinds. So the vocabulary is typed but
  not yet harmonized into the common kernel.
- This module is therefore a good staging area for normalization, but not yet
  the final shared adjudication surface.

TODO
----
- Promote reusable UK finding families into governed cross-jurisdiction/core
  finding kinds where they are not genuinely UK-specific.
- Keep compare-shape noise separated from source-pathology claims.

Actionables
-----------
- New UK adjudication work should prefer shared finding families first, then
  UK-local wrappers only when the phenomenon is truly jurisdiction-specific.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from lawvm.replay_adjudication import SourceAdjudication


UK_CORE_COMPARISON_CLASSES = frozenset({"commensurable", "unapplied_oracle_expansion"})
UK_EFFECT_SOURCE_PATHOLOGY_CLASSES = frozenset(
    {
        "missing_extracted_source",
        "unhandled_instruction_text",
        "reference_only_source_fragment",
        "fragment_context_missing",
        "instruction_text_reused_as_payload",
        "broad_source_reused_as_payload",
        "misselected_target_context",
        "nonstructural_root_gap",
        "non_substantive_shell_payload",
    }
)
UK_EFFECT_COMPARE_SHAPE_CLASSES = frozenset(
    {
        "collapsed_subtree_oracle_shape",
        "descendant_only_oracle_wrapper",
        "legacy_labeled_oracle_shape",
        "oracle_missing_live_branch",
        "retained_repeal_oracle_branch",
        "territorial_extension_oracle_gap",
    }
)
UK_REPLAY_BUG_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_tree_invariant_violation",
        "uk_replay_target_not_found",
        "uk_replay_payload_mismatch",
        "uk_replay_payload_missing",
        "uk_replay_unsupported_action",
    }
)

UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_empty_descendant_shape_gap",
        "uk_replay_empty_schedule_shape_gap",
        "uk_replay_existing_target_gap",
        "uk_replay_malformed_target_gap",
        "uk_replay_missing_source_target_gap",
        "uk_replay_missing_parent_shape_gap",
        "uk_replay_part_order_shape_gap",
        "uk_replay_chapter_order_shape_gap",
        "uk_replay_paragraph_order_shape_gap",
        "uk_replay_subparagraph_order_shape_gap",
        "uk_replay_item_order_shape_gap",
        "uk_replay_section_order_shape_gap",
        "uk_replay_payload_shape_gap",
        "uk_replay_repealed_target_gap",
        "uk_replay_table_shape_gap",
    }
)

UK_REPLAY_BUG_PROOF_KIND_BY_ADJUDICATION_KIND = {
    "uk_replay_tree_invariant_violation": "uk_replay_tree_invariant_violation",
    "uk_replay_target_not_found": "uk_replay_target_not_found",
    "uk_replay_payload_mismatch": "uk_replay_payload_mismatch",
    "uk_replay_payload_missing": "uk_replay_payload_missing",
    "uk_replay_unsupported_action": "uk_replay_unsupported_action",
}

UK_REPLAY_BUG_PROOF_KIND_PRIORITY = (
    "uk_replay_tree_invariant_violation",
    "uk_replay_target_not_found",
    "uk_replay_payload_mismatch",
    "uk_replay_payload_missing",
    "uk_replay_unsupported_action",
)


def classify_uk_bench_comparison(
    *,
    n_enacted_eids: int,
    n_oracle_eids: int,
    n_effects: int,
    raw_score: float,
) -> str:
    """Classify whether a UK bench row is commensurable for replay work.

    This deliberately operates on post-parse facts rather than raw XML folklore.
    It is not a replay diagnosis; it is a benchmark-triage classification.
    """
    if n_oracle_eids <= 0:
        return "no_oracle_eids"
    if n_enacted_eids <= 0:
        return "no_enacted_eids"
    if n_enacted_eids >= max(3 * n_oracle_eids, 30) and raw_score < 0.5:
        return "oracle_collapsed_structure"
    if n_oracle_eids >= max(2 * n_enacted_eids, 30):
        if n_effects > 0:
            return "unapplied_oracle_expansion"
        return "oracle_expansion_without_effects"
    return "commensurable"


def normalize_uk_replay_compare_eids(
    replayed_eids: Iterable[str],
    oracle_eids: Iterable[str],
) -> tuple[set[str], set[str]]:
    """Normalize UK replay-vs-oracle EID sets for known compare-shape noise.

    This is intentionally narrow and only applies to replay comparison, not
    mutation semantics. It currently handles:

    - case-only EID drift (`2a` vs `2A`)
    - replay-only descendant nodes under a `section` / `article` root when
      oracle exposes only the collapsed root text and no child EIDs
    - replay-only wrapper paragraph nodes under `part` / `crossheading` parents
      where oracle collapses the paragraph into the parent node
    - replay-only wrapper nodes whose descendants exist in oracle but the
      wrapper itself does not (`paragraph 2A` vs oracle-only `2A-1..4`)
    """
    replay_norm = {eid.lower() for eid in replayed_eids if eid}
    oracle_norm = {eid.lower() for eid in oracle_eids if eid}
    dropped_prefixes: set[str] = set()
    kept: set[str] = set()
    collapsed_roots: set[str] = set()

    for root in oracle_norm:
        if not root.startswith(("section-", "article-", "crossheading-")):
            continue
        if any(other.startswith(root + "-") for other in oracle_norm):
            continue
        replay_descendants = sum(1 for other in replay_norm if other.startswith(root + "-"))
        if replay_descendants >= 2:
            collapsed_roots.add(root)

    for eid in sorted(replay_norm, key=lambda s: len(s)):
        if any(eid == prefix or eid.startswith(prefix + "-") for prefix in dropped_prefixes):
            continue
        if any(eid.startswith(root + "-") for root in collapsed_roots):
            continue
        if eid in oracle_norm:
            kept.add(eid)
            continue
        if any(other.startswith(eid + "-") for other in oracle_norm):
            continue
        match = re.match(r"(.+?)(?:_|-)paragraph-[0-9a-z]+$", eid)
        if match:
            parent = match.group(1)
            if parent in oracle_norm and ("-part-" in parent or "-crossheading-" in parent):
                dropped_prefixes.add(eid)
                continue
        kept.add(eid)

    return kept, oracle_norm


def is_core_uk_comparison(comparison_class: str) -> bool:
    """Return True when the UK comparison belongs in the core replay frontier."""
    return comparison_class in UK_CORE_COMPARISON_CLASSES


def classify_uk_replay_residual(
    *,
    only_in_replayed: Iterable[str] = (),
    only_in_oracle: Iterable[str] = (),
    adjudication_kinds: Iterable[str] = (),
) -> tuple[str, str]:
    """Classify UK replay residuals into proved-vs-unresolved buckets.

    Residual EID mismatch alone is not sufficient to prove a replay bug.
    We only promote to PROVED_REPLAY_BUG when the replay engine emitted a
    direct replay-owned adjudication. Otherwise the residual remains
    unresolved and is partitioned by which side still carries residue.
    """
    replay_only = [str(eid or "") for eid in only_in_replayed if str(eid or "")]
    oracle_only = [str(eid or "") for eid in only_in_oracle if str(eid or "")]
    adjudications = {
        str(kind or "")
        for kind in adjudication_kinds
        if str(kind or "")
    }
    if adjudications & UK_REPLAY_BUG_ADJUDICATION_KINDS:
        for kind in UK_REPLAY_BUG_PROOF_KIND_PRIORITY:
            if kind in adjudications:
                return ("PROVED_REPLAY_BUG", UK_REPLAY_BUG_PROOF_KIND_BY_ADJUDICATION_KIND[kind])
    if adjudications & UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS:
        if "uk_replay_empty_descendant_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_empty_descendant_shape_gap")
        if "uk_replay_existing_target_gap" in adjudications:
            return ("UNRESOLVED", "uk_existing_target_gap")
        if "uk_replay_missing_source_target_gap" in adjudications:
            return ("UNRESOLVED", "uk_missing_source_target_gap")
        if "uk_replay_missing_parent_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_missing_parent_shape_gap")
        if "uk_replay_part_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_part_order_shape_gap")
        if "uk_replay_chapter_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_chapter_order_shape_gap")
        if "uk_replay_paragraph_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_paragraph_order_shape_gap")
        if "uk_replay_subparagraph_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_subparagraph_order_shape_gap")
        if "uk_replay_item_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_item_order_shape_gap")
        if "uk_replay_section_order_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_section_order_shape_gap")
        if "uk_replay_payload_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_payload_shape_gap")
        if "uk_replay_malformed_target_gap" in adjudications:
            return ("UNRESOLVED", "uk_malformed_target_gap")
        if "uk_replay_table_shape_gap" in adjudications:
            return ("UNRESOLVED", "uk_table_shape_gap")
        if "uk_replay_repealed_target_gap" in adjudications:
            return ("UNRESOLVED", "uk_repealed_target_gap")
        return ("UNRESOLVED", "uk_empty_schedule_shape_gap")
    if "uk_replay_text_match_missing" in adjudications:
        if replay_only and oracle_only:
            return ("UNRESOLVED", "uk_text_match_missing_mixed_residual_eids")
        if replay_only:
            return ("UNRESOLVED", "uk_text_match_missing_replay_only_residual_eids")
        if oracle_only:
            return ("UNRESOLVED", "uk_text_match_missing_oracle_only_residual_eids")
        return ("UNRESOLVED", "uk_text_match_missing")
    if replay_only and oracle_only:
        return ("UNRESOLVED", "uk_mixed_residual_eids")
    if replay_only:
        return ("UNRESOLVED", "uk_replay_only_residual_eids")
    if oracle_only:
        return ("UNRESOLVED", "uk_oracle_only_residual_eids")
    return ("UNRESOLVED", "no_strong_claim")


def _normalize_effect_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _looks_like_instruction_text(text: str) -> bool:
    norm = _normalize_effect_text(text)
    return bool(
        re.search(
            r"\b("
            r"insert|substitute|omit|repeal|renumber|after|before|at the end|"
            r"in subsection|in paragraph|in sub-paragraph|in section|for paragraphs|for sub-paragraphs"
            r")\b",
            norm,
            re.I,
        )
    )


def _looks_like_non_substantive_shell(text: str) -> bool:
    norm = " ".join((text or "").split()).strip()
    if not norm:
        return False
    # UK extracted source often preserves a list label like "b" or "i" ahead
    # of dotted shell placeholders.
    norm = re.sub(r"^[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?\s+", "", norm)
    if re.search(r"[A-Za-z]", norm):
        return False
    return norm.count(".") >= 4


def _looks_like_reference_only_source(text: str) -> bool:
    norm = " ".join((text or "").split()).strip()
    if not norm:
        return False
    norm = re.sub(r"^[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?\s+", "", norm)
    if re.search(r"[\"“”'‘’]", norm):
        return False
    if _looks_like_instruction_text(norm):
        return False
    if re.match(
        r"^(?:section|sections|subsection|subsections|paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|schedule|part|chapter|article)\b",
        norm,
        re.I,
    ):
        return True
    if re.match(r"^[A-Z][A-Za-z'(),.& -]+ Act \d{4}[,.;]?$", norm):
        return True
    return False


def _target_depth(target_path: str) -> int:
    return sum(1 for part in target_path.split("/") if ":" in part)


def _required_instruction_depth(text: str) -> int:
    norm = _normalize_effect_text(text)
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+sub-?paragraphs?\b", norm):
        return 3
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+paragraphs?\b", norm):
        return 3
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+subsections?\b", norm):
        return 2
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+sections?\b", norm):
        return 1
    return 0


def classify_uk_effect_source_pathology(
    *,
    extracted_tag: str | None,
    extracted_text: str,
    op_actions: Iterable[str] = (),
    payload_kinds: Iterable[str] = (),
    payload_texts: Iterable[str] = (),
    target_paths: Iterable[str] = (),
    effect_type: str = "",
    is_structural: bool = True,
) -> str:
    """Classify deterministic UK source-pathology facts for one inspected effect row."""
    norm_text = _normalize_effect_text(extracted_text)
    norm_effect_type = _normalize_effect_text(effect_type)
    actions = list(op_actions)
    kinds = [kind for kind in payload_kinds if kind]
    payload_norms = [_normalize_effect_text(text) for text in payload_texts if _normalize_effect_text(text)]
    targets = [path for path in target_paths if path]

    if not norm_text and not actions:
        return "missing_extracted_source"
    if norm_text and _looks_like_non_substantive_shell(extracted_text):
        return "non_substantive_shell_payload"
    if norm_text and not actions and not is_structural:
        return "nonstructural_root_gap"
    if norm_text and not actions:
        if _looks_like_instruction_text(norm_text):
            return "unhandled_instruction_text"
        if _looks_like_reference_only_source(extracted_text):
            return "reference_only_source_fragment"
        if norm_effect_type.startswith("word ") or norm_effect_type.startswith("words "):
            return "fragment_context_missing"
        return ""

    if norm_text and payload_norms and any(payload == norm_text for payload in payload_norms):
        if (extracted_tag or "") in {"Schedule", "BlockAmendment"} and len(norm_text) >= 200:
            return "broad_source_reused_as_payload"
        if _looks_like_instruction_text(norm_text):
            return "instruction_text_reused_as_payload"

    if (
        norm_text
        and (extracted_tag or "") == "Schedule"
        and len(norm_text) >= 200
        and "repeal" in actions
        and "schedule" in kinds
    ):
        return "broad_source_reused_as_payload"

    if norm_text and targets:
        required_depth = _required_instruction_depth(norm_text)
        if required_depth > 0:
            min_depth = min(_target_depth(path) for path in targets)
            if min_depth < required_depth:
                return "misselected_target_context"

    return ""


def is_core_uk_effect_source_candidate(pathology_class: str) -> bool:
    """Return True when an inspected UK effect row is not already a typed source pathology."""
    return pathology_class not in UK_EFFECT_SOURCE_PATHOLOGY_CLASSES


def classify_uk_effect_compare_shape(
    *,
    affecting_title: str = "",
    effect_type: str = "",
    op_actions: Iterable[str] = (),
    payload_texts: Iterable[str] = (),
    resolver_eids: Iterable[str] = (),
    base_target_hits: Iterable[bool] = (),
    oracle_target_hits: Iterable[bool] = (),
    base_descendant_hits: Iterable[bool] = (),
    oracle_descendant_hits: Iterable[bool] = (),
    base_parent_hits: Iterable[bool] = (),
    oracle_parent_hits: Iterable[bool] = (),
    base_target_texts: Iterable[str] = (),
    oracle_target_texts: Iterable[str] = (),
    base_parent_texts: Iterable[str] = (),
    oracle_parent_texts: Iterable[str] = (),
    base_has_text: bool = False,
    base_has_children: bool = False,
    oracle_has_text: bool = False,
    oracle_has_children: bool = False,
) -> str:
    """Classify deterministic compare-shape-only lanes for one inspected effect row."""
    norm_affecting_title = _normalize_effect_text(affecting_title)
    actions = {action for action in op_actions if action}
    payload_norms = [
        re.sub(r"[^a-z0-9]+", "", text.lower())
        for text in payload_texts
        if re.sub(r"[^a-z0-9]+", "", text.lower())
    ]
    resolved = [eid for eid in resolver_eids if eid]
    base_hits = list(base_target_hits)
    oracle_hits = list(oracle_target_hits)
    base_descendants = list(base_descendant_hits)
    oracle_descendants = list(oracle_descendant_hits)
    base_parents = list(base_parent_hits)
    oracle_parents = list(oracle_parent_hits)
    base_target_norms = [
        re.sub(r"[^a-z0-9]+", "", text.lower())
        for text in base_target_texts
        if re.sub(r"[^a-z0-9]+", "", text.lower())
    ]
    oracle_target_norms = [
        re.sub(r"[^a-z0-9]+", "", text.lower())
        for text in oracle_target_texts
        if re.sub(r"[^a-z0-9]+", "", text.lower())
    ]
    base_parent_norms = [
        re.sub(r"[^a-z0-9]+", "", text.lower())
        for text in base_parent_texts
        if re.sub(r"[^a-z0-9]+", "", text.lower())
    ]
    oracle_parent_norms = [
        re.sub(r"[^a-z0-9]+", "", text.lower())
        for text in oracle_parent_texts
        if re.sub(r"[^a-z0-9]+", "", text.lower())
    ]

    if not resolved:
        return ""
    if (
        "gibraltar" in norm_affecting_title
        and "insert" in actions
        and (oracle_hits or oracle_descendants)
        and not any(oracle_hits)
        and not any(oracle_descendants)
        and (
            (base_parents and any(base_parents))
            or (oracle_parents and any(oracle_parents))
            or (base_hits and any(base_hits))
        )
    ):
        return "territorial_extension_oracle_gap"
    if (
        "gibraltar" in norm_affecting_title
        and actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and base_target_norms
        and oracle_target_norms
        and any(base_text == oracle_text for base_text in base_target_norms for oracle_text in oracle_target_norms)
    ):
        return "territorial_extension_oracle_gap"
    if (
        "gibraltar" in norm_affecting_title
        and actions & {"text_replace", "text_repeal"}
        and (oracle_hits or oracle_parents)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
    ):
        return "territorial_extension_oracle_gap"
    if (
        actions == {"repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and any(base_descendants)
        and any(oracle_descendants)
    ):
        return "retained_repeal_oracle_branch"
    if (
        actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and not any(oracle_hits)
        and (base_parents or oracle_parents)
        and any(base_parents)
        and not any(oracle_parents)
    ):
        return "oracle_missing_live_branch"
    if base_has_children and not base_has_text and oracle_has_text and not oracle_has_children:
        return "collapsed_subtree_oracle_shape"
    if (
        actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and not any(base_descendants)
        and any(oracle_descendants)
        and base_has_text
        and oracle_has_text
    ):
        return "collapsed_subtree_oracle_shape"
    if (
        "insert" in actions
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
        and payload_norms
        and oracle_parent_norms
    ):
        for payload in payload_norms:
            if len(payload) < 16:
                continue
            if any(payload in parent for parent in oracle_parent_norms) and not any(
                payload in parent for parent in base_parent_norms
            ):
                return "collapsed_subtree_oracle_shape"
        for eid in resolved:
            leaf = eid.rsplit("-", 1)[-1].lower()
            if len(leaf) < 2:
                continue
            if any(leaf in parent for parent in oracle_parent_norms) and not any(
                leaf in parent for parent in base_parent_norms
            ):
                return "collapsed_subtree_oracle_shape"
    if (
        "insert" in actions
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and not any(base_descendants)
        and any(oracle_descendants)
    ):
        return "descendant_only_oracle_wrapper"
    if not actions & {"text_replace", "text_repeal", "replace"}:
        return ""
    if (
        (effect_type or "").strip().lower().startswith("substituted for ")
        and "-" in (effect_type or "")
        and actions == {"replace"}
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
    ):
        return "legacy_labeled_oracle_shape"
    return ""


def is_core_uk_effect_compare_candidate(compare_shape_class: str) -> bool:
    """Return True when an inspected UK effect row is not already compare-shape only."""
    return compare_shape_class not in UK_EFFECT_COMPARE_SHAPE_CLASSES


def build_uk_source_adjudication(
    *,
    statute_id: str,
    cutoff_date: str,
    comparison_class: str,
    lineage: Iterable[dict[str, Any]] = (),
) -> SourceAdjudication:
    """Build typed UK source adjudication from a benchmark comparison class."""
    return SourceAdjudication(
        statute_id=statute_id,
        replay_mode="uk_bench",
        cutoff_date=cutoff_date,
        oracle_version_amendment_id="current" if not cutoff_date else cutoff_date,
        oracle_suspect="" if is_core_uk_comparison(comparison_class) else comparison_class,
        lineage=tuple(lineage),
    )


__all__ = [
    "UK_CORE_COMPARISON_CLASSES",
    "UK_EFFECT_COMPARE_SHAPE_CLASSES",
    "UK_EFFECT_SOURCE_PATHOLOGY_CLASSES",
    "build_uk_source_adjudication",
    "classify_uk_effect_compare_shape",
    "classify_uk_effect_source_pathology",
    "classify_uk_replay_residual",
    "classify_uk_bench_comparison",
    "is_core_uk_effect_compare_candidate",
    "is_core_uk_effect_source_candidate",
    "is_core_uk_comparison",
    "normalize_uk_replay_compare_eids",
]
