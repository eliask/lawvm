"""UK replay oracle EID grounding helpers."""

from __future__ import annotations

import re
from typing import Optional

import Levenshtein

from lawvm.uk_legislation.addressing import _uk_eid_value, _uk_kind_value
from lawvm.uk_legislation.canonicalize import uk_is_transparent_wrapper_kind, uk_semantic_path_key
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_text import _normalize_text_for_grounding
from lawvm.uk_legislation.uk_grafter import _clean_num, _semantic_hash


def _grounding_eid(node: UKMutableNode) -> Optional[str]:
    """Return the EID/id from a node's attrs, accepting both UK XML spellings."""
    return _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))


def _grounding_clean_label(kind_name: str, label: Optional[str]) -> str:
    clean_label = _clean_num(label) if label else ""
    if not clean_label:
        return ""
    kind_prefix = str(kind_name or "").lower()
    if kind_prefix in {"part", "chapter"}:
        stripped = re.sub(rf"^{re.escape(kind_prefix)}\s+", "", clean_label).strip()
        if stripped:
            return stripped
    return clean_label


def _slugify_grounding_heading(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")


def _grounding_node_full_text(node: UKMutableNode) -> str:
    """Collect normalized full-subtree text for a node, matching oracle text_map."""
    parts = []
    if node.text:
        parts.append(node.text.strip())
    for child in node.children:
        text = _grounding_node_full_text(child)
        if text:
            parts.append(text)
    return _normalize_text_for_grounding(" ".join(parts))


class UKReplayGroundingMixin:
    statute: UKMutableStatute
    eid_map: dict[str, str]
    text_map: dict[str, str]
    oracle_alignment_events: list[dict[str, object]]

    def ground_ids(self):
        """Walks the entire statute and updates EIDs to match the Oracle map."""
        if not self.eid_map:
            return

        # Collect the full set of oracle EID values (the canonical IDs we want to
        # assign).  Used both for pre-seeding and in the main matching loop.
        oracle_id_values: set = set(self.eid_map.values())

        # Pre-seed seen_oracle_ids with EIDs that are already correct.
        # These nodes already carry an oracle-canonical EID and must NOT be
        # cleared — they would otherwise be reset to generic local IDs and
        # potentially mis-re-grounded to a different oracle EID.
        seen_oracle_ids: set = set()

        def _preseed_correct_eids(node: UKMutableNode) -> None:
            eid = _grounding_eid(node)
            if eid and eid in oracle_id_values:
                seen_oracle_ids.add(eid)
            for c in node.children:
                _preseed_correct_eids(c)

        if getattr(self.statute, "body", None):
            _preseed_correct_eids(self.statute.body)
        for sch in self.statute.supplements:
            _preseed_correct_eids(sch)

        def _clear_eids(node: UKMutableNode) -> None:
            """Clear EIDs that are NOT already in oracle (those stay for matching)."""
            eid = _grounding_eid(node)
            if eid and eid not in oracle_id_values:
                # Non-canonical EID — clear it so the grounding pass can assign
                # the correct oracle ID.
                for key in ("eId", "id"):
                    if key in node.attrs:
                        del node.attrs[key]
            # Children may need grounding even if the parent is already correct.
            for c in node.children:
                _clear_eids(c)

        if getattr(self.statute, "body", None):
            _clear_eids(self.statute.body)
        for sch in self.statute.supplements:
            _clear_eids(sch)

        # Pre-pass: ensure every node has a reasonable local eId.
        # Skip nodes that already have an oracle-canonical EID (under either
        # 'eId' or 'id' key) — those were preserved by _clear_eids and must
        # not be overwritten with a generic local label.
        def _ensure_local_eid(node: UKMutableNode) -> None:
            kind_value = _uk_kind_value(node.kind)
            if kind_value == "schedule_entry":
                for key in ("eId", "id"):
                    node.attrs.pop(key, None)
            elif "eId" not in node.attrs and "id" not in node.attrs and kind_value != "body":
                clean_label = _grounding_clean_label(kind_value, node.label)
                if clean_label:
                    node.attrs["eId"] = f"{kind_value}-{clean_label}"
                else:
                    node.attrs["eId"] = kind_value
            for c in node.children:
                _ensure_local_eid(c)

        if getattr(self.statute, "body", None):
            _ensure_local_eid(self.statute.body)
        for sch in self.statute.supplements:
            _ensure_local_eid(sch)

        def _ground_node(node: UKMutableNode, parent_path_key, parent_eid=None, ordinal=1, context="body"):
            nonlocal seen_oracle_ids
            parent_eid = _uk_eid_value(parent_eid)
            if _uk_kind_value(node.kind) == "schedule_entry":
                for key in ("eId", "id"):
                    node.attrs.pop(key, None)
                return
            # Fast path: if this node already has a correct oracle EID (preserved
            # from the pre-seed pass), skip the multi-pass matching for this node
            # and recurse into children with updated context.  The EID is already
            # registered in seen_oracle_ids from the pre-seed pass.
            existing_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
            if existing_eid and existing_eid in oracle_id_values and existing_eid in seen_oracle_ids:
                kind = node.kind
                kind_name = _uk_kind_value(kind).lower()
                clean_label = _grounding_clean_label(kind_name, node.label)
                next_path_key = uk_semantic_path_key(
                    parent_path_key,
                    kind=kind_name,
                    clean_label=clean_label,
                )
                new_context = context
                if kind_name == "schedule" and clean_label:
                    new_context = f"schedule-{clean_label}"
                elif kind_name == "body":
                    new_context = "body"
                kind_counts: dict = {}
                for child in node.children:
                    child_kind = _uk_kind_value(child.kind)
                    kind_counts[child_kind] = kind_counts.get(child_kind, 0) + 1
                    _ground_node(
                        child, next_path_key, existing_eid, ordinal=kind_counts[child_kind], context=new_context
                    )
                return

            kind = node.kind
            kind_name = _uk_kind_value(kind).lower()
            clean_label = _grounding_clean_label(kind_name, node.label)
            raw_label = str(node.label or "").strip()
            heading = node.attrs.get("heading") or ""
            if (
                not heading
                and kind_name in ("p1group", "pblock", "crossheading", "chapter", "part")
                and node.text
                and len(node.text) < 200
            ):
                heading = node.text
            slug = _slugify_grounding_heading(heading)

            node_key_part = f"{kind_name}-{clean_label}" if clean_label else (f"{kind_name}-{slug}" if slug else kind_name)

            # Use : as separator for semantic path matching against eid_map
            if not parent_path_key:
                hierarchical_path_key = str(node_key_part)
            else:
                hierarchical_path_key = f"{parent_path_key}:{node_key_part}"

            next_path_key = uk_semantic_path_key(
                parent_path_key,
                kind=kind_name,
                clean_label=clean_label or slug,
            )

            oracle_id = None
            matched_cand = None

            # Pass 0: Exact Hash Matching (NEW - Grounding 2.0)
            # ONLY match meaningful text to avoid dot-shell collisions.
            # Skip for: (a) structural containers (part/chapter/schedule) — heading text
            # can collide with inline term definitions, (b) nodes whose exact hierarchical
            # path exists in oracle eid_map — flat matching will succeed and is more precise
            # (prevents section-1 enacted text matching oracle's subsection-1-1 with same text).
            _structural_kinds = {"part", "chapter", "schedule", "annex"}
            # Kinds that may legitimately match oracle term-* EIDs (definition nodes).
            # All other structural kinds (section, paragraph, subsection …) must NOT be
            # grounded to a term-* oracle EID via hash — the hash collision is accidental
            # (e.g. paragraph-a whose text begins with a term name).
            _term_eid_kinds = {"p1group", "crossheading", "section", "article"}
            is_dots = bool(node.text and re.match(r"^[.\s]+$", node.text))
            _has_structural_path = str(hierarchical_path_key).lower() in self.eid_map
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
            ):
                h = _semantic_hash(node.text)
                hash_key = f"hash:{h}"
                if hash_key in self.eid_map:
                    candidate_id = self.eid_map[hash_key]
                    if candidate_id not in seen_oracle_ids:
                        # Guard: reject a term-* oracle EID for non-term node kinds.
                        # Prevents paragraph-a (e.g. "(a) chief constable means…") from
                        # hash-colliding with the oracle's term-chief-constable definition.
                        _is_term_eid = candidate_id.startswith("term-")
                        if not _is_term_eid or kind_name in _term_eid_kinds:
                            oracle_id = candidate_id
                            matched_cand = f"hash:{h}"

            # Pass 0.5: Fuzzy Text Matching (NEW - Grounding 2.1)
            # Use node.text (direct text only) for the length/Levenshtein comparison.
            # Transparent wrapper nodes (p1group, crossheading) are excluded from fuzzy
            # matching because:
            #   (a) p1group direct text is typically empty — fuzzy wouldn't fire anyway
            #       but using full-subtree text would steal oracle EIDs from child sections.
            #   (b) crossheading direct text is the heading — it can fuzzy-match oracle
            #       term-* EIDs whose text equals the heading name.  Instead, a separate
            #       guard (below) blocks crossheading → term-* matches explicitly.
            # Non-transparent nodes (section, paragraph, subsection…) use direct text and
            # additionally must not fuzzy-match term-* oracle EIDs (same guard as hash pass).
            _fuzzy_skip_kinds = {"p1group", "pblock"}  # transparent wrappers whose children own the EIDs
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
                and kind_name not in _fuzzy_skip_kinds
            ):
                node_norm = _normalize_text_for_grounding(node.text)
                if len(node_norm) > 30:
                    best_score = 0
                    best_id = None
                    for oid, otext in self.text_map.items():
                        if oid in seen_oracle_ids:
                            continue
                        if abs(len(otext) - len(node_norm)) > 0.1 * len(node_norm):
                            continue
                        score = Levenshtein.ratio(node_norm, otext)
                        if score > 0.92 and score > best_score:
                            best_score = score
                            best_id = oid
                    if best_id:
                        # Guard: crossheadings must not fuzzy-match term-* oracle EIDs.
                        # A crossheading "domestic abuse protection notices" should match
                        # oracle's crossheading EID (not term-domestic-abuse-protection-notice)
                        # even if the heading text and term text are nearly identical.
                        # When a crossheading matches a term-* EID the bench penalises the
                        # match because the crossheading's full subtree (all its sections) is
                        # compared to the oracle term's short text → very low text similarity.
                        _is_term_eid = best_id.startswith("term-")
                        if not _is_term_eid or kind_name not in ("crossheading", "pblock", "chapter"):
                            oracle_id = best_id
                            matched_cand = f"fuzzy:{best_score:.3f}"

            kind_syns: list[str] = [kind_name]
            if kind_name == "pblock":
                kind_syns.extend(["chapter", "crossheading", "eusection", "division"])
            elif kind_name == "chapter":
                kind_syns.extend(["pblock", "crossheading", "euchapter", "division"])
            elif kind_name == "crossheading":
                kind_syns.extend(["pblock", "chapter", "eusection", "division"])
            elif kind_name == "p1group":
                kind_syns.extend(["section", "crossheading", "paragraph", "article"])
            elif kind_name == "schedule":
                kind_syns.extend(["annex"])
            elif kind_name in ("section", "p1", "article"):
                kind_syns = ["section", "p1", "article"]
            elif kind_name in ("paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"):
                kind_syns = ["paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"]

            # Pass 1: Local & Flat Matching (High Priority for top-level nodes)
            if not oracle_id:
                flat_cands = []
                # Check hierarchical keys with synonyms
                for k in kind_syns:
                    parts = str(hierarchical_path_key).split(":")
                    last = parts[-1]
                    if "-" in last:
                        parts[-1] = f"{k}-{last.split('-', 1)[1]}"
                    else:
                        parts[-1] = k
                    flat_cands.append(":".join(parts).lower())

                # Check flat/suffix keys
                # crossheading/pblock are included so that ECHR-article Pblocks in
                # Schedule 1 can match oracle chapter-N EIDs via the suffix slug key.
                #
                # IMPORTANT: Suppress the short context:kind-label flat candidates for
                # sub-section-level nodes (paragraph, subsection, subparagraph, item)
                # that are deeply nested *inside a section* (parent_path_key contains
                # a "section-N" or "article-N" segment).  Without this guard a paragraph
                # node inside section-1-7 matches oracle's section-25-1-b via the shared
                # key "body:paragraph-b", stealing the oracle EID from section-25.
                # Structural containers (section, chapter, part, schedule) are NOT
                # restricted — their flat keys are the primary lookup path and they do
                # not collide across sections.
                _sub_kinds = {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3"}
                _is_inside_section = bool(
                    kind_name in _sub_kinds and re.search(r":(section|article|rule|regulation)-", parent_path_key or "")
                )
                # Suppress flat matching for paragraph/subparagraph/item nodes inside
                # schedule chapters/parts. Without this guard, "paragraph 2" under
                # chapter-1 matches oracle's chapter-10-paragraph-2 via the shared
                # key "schedule-1:paragraph-2". Schedule descendant nodes must match
                # via hierarchical paths or hash/fuzzy, not flat context:kind-label keys.
                _is_inside_schedule_chapter = bool(
                    kind_name in _sub_kinds
                    and context.startswith("schedule")
                    and re.search(r":(chapter|part)-", parent_path_key or "")
                )
                _schedule_structural_flat = bool(
                    context.startswith("schedule") and kind_name in {"part", "chapter", "crossheading", "pblock", "division"}
                )
                if kind_name in (
                    "section",
                    "article",
                    "schedule",
                    "annex",
                    "part",
                    "chapter",
                    "paragraph",
                    "crossheading",
                    "pblock",
                    "division",
                ):
                    for k in kind_syns:
                        if clean_label:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:{k}-{clean_label}")
                                flat_cands.append(f"{context}:suffix:{k}-{clean_label}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{clean_label}")
                        elif slug:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:suffix:{k}-{slug}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{slug}")

                if kind_name == "subsection" and clean_label and parent_eid:
                    parent_match = re.match(
                        r"^(section|article|rule|regulation)-(.+)$",
                        parent_eid,
                        re.I,
                    )
                    if parent_match:
                        parent_suffix = _clean_num(parent_match.group(2))
                        if parent_suffix:
                            flat_cands.append(f"{context}:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{context}:suffix:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{parent_path_key}:subsection-{parent_suffix}-{clean_label}")

                for cand in flat_cands:
                    if cand.lower() in self.eid_map:
                        candidate_id = self.eid_map[cand.lower()]
                        if candidate_id not in seen_oracle_ids:
                            oracle_id = candidate_id
                            matched_cand = f"flat:{cand.lower()}"
                            break

            # Pass 3: Ordinal Matching (Fallback for non-semantic IDs)
            # Guard: before accepting an ordinal match, verify text similarity when the
            # oracle text_map has content for the candidate.  This prevents a case where
            # enacted section[1] inside part-1 matches oracle section[1]-inside-part-1
            # (which is section-21, a definitions section) purely by position even though
            # the content is completely different — e.g. enacted Part 1 had sections 1-20
            # but after amendments only section-21 (definitions) remains in oracle Part 1.
            #
            # Two-factor rejection:
            #   (a) length ratio: if max/min > 3.0, texts are too different in size.
            #   (b) Levenshtein ratio < 0.50: text content does not match well enough.
            # Either condition alone rejects the candidate.  Both must pass to accept.
            # Threshold 0.50 is intentionally strict because legitimate ordinal matches
            # (same provision at same structural position) will score 0.80+ while wrong
            # ordinal matches (different section at same ordinal slot after amendments)
            # typically score 0.30-0.55 even for similar legal vocabulary.
            _ORDINAL_LEN_RATIO_MAX = 3.0
            _ORDINAL_TEXT_THRESHOLD = 0.50
            if not oracle_id:
                ord_key = f"{parent_path_key}:{kind_name}[{ordinal}]".lower()
                if ord_key in self.eid_map:
                    candidate_id = self.eid_map[ord_key]
                    if candidate_id not in seen_oracle_ids:
                        # Text guard: if oracle has text for the candidate, require
                        # the node full text to be sufficiently similar to oracle text.
                        oracle_text = self.text_map.get(candidate_id, "")
                        accept = True
                        if oracle_text:
                            node_full = _grounding_node_full_text(node)
                            if node_full and len(node_full) > 20 and len(oracle_text) > 20:
                                max_len = max(len(node_full), len(oracle_text))
                                min_len = min(len(node_full), len(oracle_text))
                                if max_len / min_len > _ORDINAL_LEN_RATIO_MAX:
                                    accept = False
                                else:
                                    ratio = Levenshtein.ratio(node_full, oracle_text)
                                    if ratio < _ORDINAL_TEXT_THRESHOLD:
                                        accept = False
                        if accept:
                            oracle_id = candidate_id
                            matched_cand = f"ordinal:{ord_key}"

            if oracle_id:
                before_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
                node.attrs["eId"] = oracle_id
                seen_oracle_ids.add(oracle_id)
                self.oracle_alignment_events.append(
                    {
                        "rule_id": "uk_oracle_eid_alignment_adapter",
                        "phase": "oracle_alignment",
                        "family": "oracle_alignment_adapter",
                        "kind": str(node.kind),
                        "label": node.label,
                        "before_eid": before_eid,
                        "after_eid": oracle_id,
                        "match_method": str(matched_cand).split(":", 1)[0] if matched_cand else "oracle_preserved",
                        "match_key": matched_cand,
                    }
                )
                if matched_cand:
                    self._log(f"  Matched {node.kind} {node.label or ''} to {oracle_id} via {matched_cand}")
            else:
                if uk_is_transparent_wrapper_kind(kind_name):
                    if "eId" in node.attrs:
                        before_eid = _uk_eid_value(node.attrs.get("eId"))
                        del node.attrs["eId"]
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": None,
                                "match_method": "transparent_wrapper_cleared",
                                "match_key": None,
                            }
                        )
                elif parent_eid:
                    before_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
                    local_label = clean_label
                    if (
                        raw_label
                        and kind_name in {"subparagraph", "item", "point"}
                        and re.fullmatch(
                            r"[ivxlcdm]+",
                            raw_label,
                            re.IGNORECASE,
                        )
                    ):
                        local_label = raw_label.lower().strip(".")
                    part = local_label if local_label else kind_name
                    if context.startswith("schedule") and clean_label:
                        if kind_name in {"paragraph", "subparagraph", "subsection", "item", "point", "p2", "p3"}:
                            # UK schedule descendant IDs flatten nested paragraph/item levels
                            # to bare suffixes once the first schedule paragraph is established.
                            if re.search(r"(?:^|-)paragraph-[^-]+(?:-|$)", parent_eid):
                                part = local_label
                            else:
                                part = f"paragraph-{local_label}"
                        else:
                            part = f"{kind_name}-{clean_label}"
                    fallback_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{part}"
                    if not clean_label and kind_name not in {"schedule", "part", "chapter"}:
                        for key in ("eId", "id"):
                            node.attrs.pop(key, None)
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": None,
                                "match_method": "local_fallback_unlabeled_blocked",
                                "match_key": None,
                            }
                        )
                    else:
                        node.attrs["eId"] = fallback_eid
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": fallback_eid,
                                "match_method": "local_fallback",
                                "match_key": None,
                            }
                        )

            kind_counts = {}
            new_context = context
            if kind_name == "schedule" and clean_label:
                new_context = f"schedule-{clean_label}"
            elif kind_name == "body":
                new_context = "body"

            actual_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id") or parent_eid)
            for child in node.children:
                child_kind = _uk_kind_value(child.kind)
                kind_counts[child_kind] = kind_counts.get(child_kind, 0) + 1
                _ground_node(child, next_path_key, actual_eid, ordinal=kind_counts[child_kind], context=new_context)

        grounded_count = 0

        def _visit_count(n):
            nonlocal grounded_count
            eid = n.attrs.get("eId")
            if eid and eid in self.eid_map.values():
                grounded_count += 1
            for c in n.children:
                _visit_count(c)

        body_node = getattr(self.statute, "body", None)
        if body_node:
            kind_counts = {}
            for node in body_node.children:
                node_kind = _uk_kind_value(node.kind)
                kind_counts[node_kind] = kind_counts.get(node_kind, 0) + 1
                _ground_node(node, "body", None, ordinal=kind_counts[node_kind], context="body")
            _visit_count(body_node)

        for i, sch in enumerate(self.statute.supplements):
            _ground_node(sch, "", None, ordinal=i + 1, context="schedule")
            _visit_count(sch)

        self._log(f"  EXECUTOR: grounded {grounded_count} nodes against Oracle map")
