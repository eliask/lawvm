"""Norway replay-vs-current consistency checks."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from lawvm.core.comparison_normalization import ComparisonNormalizationRule, normalize_comparison_text
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.ir_helpers import kind_str
from lawvm.core.mutation_boundary import (
    TreePath,
    normalize_tree_path_for_relation,
    path_is_strict_prefix,
    paths_related,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops
from lawvm.core.timeline_consistency import ConsistencyDivergence, ingest_consolidated, verify_consistency
from lawvm.core.verification_contracts import (
    DivergencePartition as NOPrimaryDivergencePartition,
    FilteredDivergenceRecord as NOFilteredDivergence,
)
from lawvm.norway.grafter import parse_no_statute
from lawvm.norway.index import NOAmendmentIndex, build_no_amendment_index, load_no_amendment_index
from lawvm.norway.inventory import build_no_inventory
from lawvm.norway.replay import NOReplayResult, replay_no_to_pit
from lawvm.norway.sources import (
    load_no_current_bytes,
    resolve_no_source_path,
)
from lawvm.norway.sources import NOReplayStatus
from lawvm.core.quirks_disposition import QuirksDisposition

_NO_VERIFY_WS_RE = re.compile(r"\s+")
_NO_VERIFY_PUNCT_RE = re.compile(r"\s+([,.;:])")
_NO_VERIFY_PAREN_OPEN_RE = re.compile(r"\(\s+")
_NO_VERIFY_REPEALED_RE = re.compile(r"^(?:§\s*[0-9A-Za-z-]+\.\s*)?\(Opphevet\)$", re.IGNORECASE)
# Lovdata *Vedlegg* annex-token prefix on a section label (e.g. ``v22c`` for
# EEA Agreement Annex XXII nr. 10c). Compiled module-scope per §2.4.
_NO_ANNEX_TOKEN_RE = re.compile(r"v\d+[a-z]*", re.IGNORECASE)
_NO_VERIFY_OTHER_LAWS_PLACEHOLDER_RE = re.compile(
    r"((?:gjøres følgende endringer(?: i andre lover)?|gjerast i andre lover|skal desse endringane gjerast i andre lover):)\s*(?:[-–—]\s*){2,}$",
    re.IGNORECASE,
)
_NO_VERIFY_TRAILING_FOOTNOTE_RE = re.compile(r"([.!?])\s+\d+$")
_NO_VERIFY_STANDALONE_FOOTNOTE_RE = re.compile(r"([.!?])\s+\d+\s+(?=[A-ZÆØÅ])")
_NO_VERIFY_CONTINGENT_OTHER_LAWS_RE = re.compile(
    r"^(?:Fra|Frå|Med virkning fra den tid)\b.*?(?:Kongen fastsetter|Kongen bestemmer).*?(?:gjøres følgende endringer|gjerast i andre lover|skal desse endringane gjerast i andre lover)",
    re.IGNORECASE,
)
_NO_VERIFY_SECTION_SHELL_RE = re.compile(
    r"^I §\s*(?P<label>[0-9A-Za-z-]+)(?:\s+nr\.\s*\d+)?\b",
    re.IGNORECASE,
)
_NO_COMPARISON_NORMALIZATION_RULES = (
    ComparisonNormalizationRule(
        name="no_compare_nbsp",
        rule_class="presentation_cleanup",
        kind="literal",
        description="Project non-breaking spaces to ordinary spaces for Norway comparison text.",
        old_text="\xa0",
        new_text=" ",
    ),
    ComparisonNormalizationRule(
        name="no_compare_whitespace_collapse",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Collapse whitespace runs for Norway comparison text.",
        pattern=_NO_VERIFY_WS_RE,
        replacement=" ",
    ),
    ComparisonNormalizationRule(
        name="no_compare_punctuation_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove spaces before punctuation for Norway comparison text.",
        pattern=_NO_VERIFY_PUNCT_RE,
        replacement=r"\1",
    ),
    ComparisonNormalizationRule(
        name="no_compare_open_paren_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove spaces after opening parenthesis for Norway comparison text.",
        pattern=_NO_VERIFY_PAREN_OPEN_RE,
        replacement="(",
    ),
    ComparisonNormalizationRule(
        name="no_compare_inline_footnote_marker",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove inline numeric footnote markers between sentences.",
        pattern=re.compile(r"(?<=[a-zæøå])\s+\d+\s+(?=[A-ZÆØÅ])"),
        replacement=" ",
    ),
    ComparisonNormalizationRule(
        name="no_compare_standalone_footnote_marker",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove standalone numeric footnote markers after punctuation.",
        pattern=_NO_VERIFY_STANDALONE_FOOTNOTE_RE,
        replacement=r"\1 ",
    ),
    ComparisonNormalizationRule(
        name="no_compare_numeric_hyphen_gap",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Close a spacing gap before hyphen after a digit.",
        pattern=re.compile(r"(\d)\s+-"),
        replacement=r"\1-",
    ),
    ComparisonNormalizationRule(
        name="no_compare_other_laws_placeholder_dash_tail",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Suppress pure dash tails in other-laws placeholder clauses.",
        pattern=_NO_VERIFY_OTHER_LAWS_PLACEHOLDER_RE,
        replacement=r"\1",
    ),
    ComparisonNormalizationRule(
        name="no_compare_trailing_footnote_marker",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove trailing numeric footnote markers after terminal punctuation.",
        pattern=_NO_VERIFY_TRAILING_FOOTNOTE_RE,
        replacement=r"\1",
    ),
)
NO_VERIFY_COMPARE_REPEALED_SHELL_BLANKED = "no_verify.compare_repealed_shell_blanked"
NO_VERIFY_COMPARE_SENTENCE_CHILDREN_COLLAPSED = "no_verify.compare_sentence_children_collapsed"
NO_VERIFY_COMPARE_NESTED_ITEM_TAIL_SUPPRESSED = "no_verify.compare_nested_item_tail_suppressed"
NO_VERIFY_COMPARE_SELF_SECTION_SHELL_BLANKED = "no_verify.compare_self_section_shell_blanked"
NO_VERIFY_COMPARE_CONTINGENT_OTHER_LAWS_PLACEHOLDER_SUPPRESSED = (
    "no_verify.compare_contingent_other_laws_placeholder_suppressed"
)
NO_VERIFY_COMPARE_DEFINITION_SUBSECTION_PAIRS_COLLAPSED = (
    "no_verify.compare_definition_subsection_pairs_collapsed"
)
NO_VERIFY_COMPARE_OTHER_LAWS_CONTEXT_SUPPRESSED = "no_verify.compare_other_laws_context_suppressed"


@dataclass(frozen=True)
class NOCompareProjection:
    surface: str
    rule_id: str
    reason: str
    address: TreePath
    before_kind: str
    before_label: str | None
    before_text: str
    after_text: str
    before_child_count: int
    after_child_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "rule_id": self.rule_id,
            "family": "editorial_projection",
            "phase": "oracle_compare",
            "reason": self.reason,
            "address": [list(step) for step in self.address],
            "before_kind": self.before_kind,
            "before_label": self.before_label,
            "before_text": self.before_text,
            "after_text": self.after_text,
            "before_child_count": self.before_child_count,
            "after_child_count": self.after_child_count,
        }


@dataclass
class NOVerifyResult:
    base_id: str
    as_of: str
    current_title: str = ""
    replay_status: str = ""
    consistent: bool = False
    divergence_count: int = 0
    divergence_counts: dict[str, int] | None = None
    raw_divergence_count: int = 0
    raw_divergence_counts: dict[str, int] | None = None
    filtered_divergence_count: int = 0
    filtered_divergence_rule_counts: dict[str, int] | None = None
    filtered_divergences: list[NOFilteredDivergence] | None = None
    compare_projection_count: int = 0
    compare_projection_rule_counts: dict[str, int] | None = None
    compare_projections: list[NOCompareProjection] | None = None
    divergences: list[ConsistencyDivergence] | None = None
    indexed_amendment_count: int = 0
    applied_amendment_count: int = 0
    replay_op_count: int = 0
    source_signal: str | None = None
    replay: Optional[NOReplayResult] = None
    error: str | None = None
    # Typed finding (or ``None`` when the base_id had a canonical year) emitted
    # when :func:`_no_base_year` could not extract a year from the result's
    # ``base_id``. Replaces the silent ``base_year = 0`` sentinel that used to
    # swallow ``IndexError`` / ``ValueError`` on undocumented base_id shapes
    # (§1.10 invisible-heuristic smell). ``None`` is the steady-state.
    source_signal_diagnostic: Optional[dict[str, Any]] = None


_NO_RELATION_CONTAINER_KINDS = {"part", "chapter"}
_NO_RELATION_SPECIAL_LABELS = {"last", "first"}


def normalize_no_comparison_text(text: str) -> str:
    """Normalize bounded Norway editorial spacing noise for compare-only use."""
    normalized = normalize_comparison_text(text, _NO_COMPARISON_NORMALIZATION_RULES).text.strip()
    if _NO_VERIFY_REPEALED_RE.fullmatch(normalized):
        return ""
    return normalized


def _has_no_other_laws_marker(text: str) -> bool:
    lowered = normalize_no_comparison_text(text).lower()
    return (
        "endringer i andre lover" in lowered
        or "endringar i andre lover" in lowered
        or "gjøres følgende endringer" in lowered
        or "gjerast i andre lover" in lowered
        or "desse endringane gjerast i andre lover" in lowered
    )


def _is_no_contingent_other_laws_placeholder(text: str) -> bool:
    lowered = normalize_no_comparison_text(text).lower()
    return bool(_NO_VERIFY_CONTINGENT_OTHER_LAWS_RE.search(lowered))


def _is_no_self_section_lead_shell(section_label: str | None, text: str) -> bool:
    normalized = normalize_no_comparison_text(text)
    if not normalized:
        return False
    match = _NO_VERIFY_SECTION_SHELL_RE.match(normalized)
    if not match:
        return False
    if section_label and match.group("label") != section_label:
        return False
    lowered = normalized.lower()
    return (
        "endringer i " in lowered
        or "endringar i " in lowered
        or " om endringer i " in lowered
        or " om endringar i " in lowered
        or "skal ny endring lyde:" in lowered
        or "skal nye endringer lyde:" in lowered
        or "skal ny endring lyde :" in lowered
        or "skal nye endringer lyde :" in lowered
    )


def _no_base_year(base_id: str) -> tuple[int, dict | None]:
    """Extract the enactment year from a Norway ``base_id`` with a typed finding.

    A Norway ``base_id`` has the canonical form ``no/lov/YYYY-MM-DD-N`` (per
    :func:`lawvm.norway.grafter.lovdata_path_to_address`). The enactment year
    lives in the third ``/``-separated segment, first 4 chars. Returns ``(year,
    None)`` on the canonical shape; ``(0, finding)`` when the shape is
    unrecognized, with a typed ``rule_id=no_verify_source_signal_base_year_unresolved``
    finding so the caller (or downstream JSON consumer) sees the
    unmappable ``base_id`` rather than the bare ``0`` silent sentinel
    previously produced by ``except (IndexError, ValueError): base_year = 0``,
    which (per AGENTS.md §1.10) is exactly the kind of invisible heuristic
    the spec forbids.

    The finding carries the offending ``base_id`` so a triager can find the
    malformed id without re-running extraction.
    """
    segments = base_id.split("/")
    if len(segments) < 3 or len(segments[2]) < 4 or not segments[2][:4].isdigit():
        return 0, {
            "rule_id": "no_verify_source_signal_base_year_unresolved",
            "phase": "verify",
            "family": "source_pathology",
            "reason": (
                "Norway base_id does not carry a canonical no/lov/YYYY-MM-DD-N form; "
                "source-signal inference cannot use an enactment year, falling through "
                "the sparse-indexed-history branch unconditionally."
            ),
            "base_id": base_id,
            "base_year": 0,
            "blocking": False,
            "strict_disposition": "warn",
            "quirks_disposition": QuirksDisposition.RECORD,
        }
    return int(segments[2][:4]), None


def _infer_no_source_signal(
    *,
    divergence_count: int,
    indexed_amendment_count: int,
    replay_op_count: int,
    base_year: int,
) -> str | None:
    # §2.1 family witness: the ``sparse_indexed_history`` shape originally
    # observed on no/lov/2006-06-30-50 (SCE-loven) — 1 indexed amendment,
    # 3 replay ops, 212 primary divergences, 2006 base — extends to
    # no/lov/2001-01-05-1 (Vaktvirksomhetsloven): 2 indexed amendments,
    # 3 replay ops, 83 primary divergences, 2001 base. The acquisition
    # ceiling is ``≤2`` indexing events for an EEA-implementing or
    # guard-company regulation meant to track ~24 years of post-2000
    # activity — not ``≤1``. The divergence_count gate (≥50 trigger-1,
    # ≥15 trigger-2) AND ops_count AND base_year publish independently
    # tuned shapes that bound the family; the ``indexed_amendment_count``
    # bound is the only one widened in this revision.
    if (
        divergence_count >= 50
        and indexed_amendment_count <= 2
        and replay_op_count <= 5
        and base_year
        and base_year <= 2020
    ):
        return "sparse_indexed_history"
    if (
        divergence_count >= 15
        and indexed_amendment_count <= 2
        and replay_op_count <= 2
        and base_year
        and base_year <= 2025
    ):
        return "sparse_indexed_history"
    return None


def _no_kind_value(kind: IRNodeKind | str) -> str:
    # IRNode.kind is annotated IRNodeKind, but parse paths that build the
    # comparison tree may assign a plain str (e.g. "sentence") rather than the
    # enum member. Mirror the canonical core.ir_helpers.kind_str coercion so the
    # Norway compare/verify path tolerates both forms.
    return kind_str(kind)


def _no_kind_is(kind: IRNodeKind | str, target: IRNodeKind) -> bool:
    # `kind is target` silently evaluates False when `kind` is a plain str
    # mirroring the enum value (e.g. "sentence" vs IRNodeKind.SENTENCE). Coerce
    # both sides to their canonical string form so the str case takes the same
    # branch the equivalent enum case would, without altering enum-case logic.
    return _no_kind_value(kind) == target.value


def _no_kind_in(kind: IRNodeKind | str, targets: frozenset[IRNodeKind]) -> bool:
    # Membership counterpart of _no_kind_is; tolerates a str kind that mirrors an
    # enum member without changing the enum-case outcome.
    value = _no_kind_value(kind)
    return any(value == target.value for target in targets)


def _append_no_compare_projection(
    projections_out: list[NOCompareProjection] | None,
    *,
    surface: str,
    rule_id: str,
    reason: str,
    path: TreePath,
    before: IRNode,
    after: IRNode,
) -> None:
    if projections_out is None:
        return
    projections_out.append(
        NOCompareProjection(
            surface=surface,
            rule_id=rule_id,
            reason=reason,
            address=path,
            before_kind=_no_kind_value(before.kind),
            before_label=before.label,
            before_text=before.text,
            after_text=after.text,
            before_child_count=len(before.children),
            after_child_count=len(after.children),
        )
    )


def _no_compare_child_path(
    path: TreePath,
    child: IRNode,
) -> TreePath:
    if child.label:
        return (*path, (_no_kind_value(child.kind), child.label))
    return path


def normalize_no_relation_path(path: TreePath) -> TreePath:
    return normalize_tree_path_for_relation(
        path,
        ignored_kinds=frozenset(_NO_RELATION_CONTAINER_KINDS),
    )


def no_paths_related(
    left: TreePath,
    right: TreePath,
) -> bool:
    return paths_related(
        left,
        right,
        ignored_kinds=frozenset(_NO_RELATION_CONTAINER_KINDS),
        special_labels=frozenset(_NO_RELATION_SPECIAL_LABELS),
    )


def _concretize_no_relation_path(
    body: IRNode,
    path: TreePath,
) -> TreePath:
    concrete: list[tuple[str, str]] = []
    for kind, label in path:
        if label not in _NO_RELATION_SPECIAL_LABELS:
            concrete.append((kind, label))
            continue
        parent = tree_ops.resolve(body, concrete) if concrete else body
        if parent is None:
            return path
        candidates = [child for child in parent.children if _no_kind_value(child.kind) == kind and child.label]
        if not candidates:
            return path
        chosen = candidates[-1] if label == "last" else candidates[0]
        concrete.append((kind, chosen.label or label))
    return tuple(concrete)


def collect_no_touched_path_counts(
    *,
    base_id: str,
    index: NOAmendmentIndex,
    data_dir: Optional[Path] = None,
    replayed_body: Optional[IRNode] = None,
) -> tuple[Counter[TreePath], int, int]:
    from lawvm.norway.grafter import iter_no_document_change_ops
    from lawvm.norway.sources import load_no_amendment_artifact_bytes

    # ``NOAmendmentIndex.data_dir`` is a required field (declared in
    # src/lawvm/norway/index.py); the previous ``getattr(index, "data_dir",
    # None)`` defense was redundant §1.9 dynamic-shape over an already-typed
    # object. The precedence (prefer the index's recorded source path, fall
    # back to the explicit data_dir arg) is preserved unchanged — the original
    # intent of preferring the path the index was built against is honored.
    source_path = resolve_no_source_path(Path(index.data_dir) if index.data_dir else data_dir)
    norm_base_id = base_id if base_id.startswith("no/") else f"no/{base_id.removeprefix('lov/')}"
    touched_path_counts: Counter[TreePath] = Counter()
    touched_source_count = 0
    touched_op_count = 0

    for entry in index.entries_for_base(norm_base_id):
        html_bytes = load_no_amendment_artifact_bytes(
            entry.source_id,
            entry.archive,
            entry.member_name,
            source_path,
        )
        if html_bytes is None:
            continue
        source_touched = False
        for group_base, ops in iter_no_document_change_ops(html_bytes, entry.source_id):
            if group_base != norm_base_id:
                continue
            for op in ops:
                touched_op_count += 1
                op_paths = {tuple(op.target.path)}
                # ``op.targets`` is an *optional* multi-target view some IR
                # node subtypes carry (not in the base ``LegalOperation``
                # dataclass, hence getattr); ``op.anchor`` and
                # ``op.destination`` are typed Optional fields on every
                # LegalOperation, so the getattr defenses against them were
                # §1.9 dynamic-shape over typed carriers. Direct attribute
                # access now: the typed Optional[LegalAddress] = None default
                # is the contract.
                for candidate in getattr(op, "targets", []) or []:
                    op_paths.add(tuple(candidate.path))
                anchor = op.anchor
                if anchor is not None:
                    op_paths.add(tuple(anchor.path))
                destination = op.destination
                if destination is not None:
                    op_paths.add(tuple(destination.path))
                if op_paths:
                    source_touched = True
                for path in op_paths:
                    if replayed_body is not None:
                        path = _concretize_no_relation_path(replayed_body, path)
                    touched_path_counts[path] += 1
        if source_touched:
            touched_source_count += 1

    return touched_path_counts, touched_source_count, touched_op_count


def build_no_verify_coverage_summary(
    *,
    verify_result: NOVerifyResult,
    index: NOAmendmentIndex,
    data_dir: Optional[Path] = None,
) -> dict[str, Any]:
    # ``getattr`` defenses stay here because the test suite mocks
    # ``verify_result`` with ``types.SimpleNamespace`` call sites that do
    # not set the replay field explicitly. The §1.9 "typed carriers over
    # dynamic shape" rule permits exactly this local exception — test
    # scaffolding duck-typed assertions against a typed dataclass.
    replay = getattr(verify_result, "replay", None)
    replayed_body = replay.replayed.body if replay is not None and getattr(replay, "replayed", None) is not None else None
    touched_path_counts, touched_source_count, touched_op_count = collect_no_touched_path_counts(
        base_id=verify_result.base_id,
        index=index,
        data_dir=data_dir,
        replayed_body=replayed_body,
    )
    touched_path_set = set(touched_path_counts)
    divergences = list(verify_result.divergences or [])
    touched_divergence_count = 0
    untouched_divergence_count = 0
    for divergence in divergences:
        divergence_path = tuple(divergence.address.path)
        if any(no_paths_related(path, divergence_path) for path in touched_path_set):
            touched_divergence_count += 1
        else:
            untouched_divergence_count += 1
    return {
        "touched_path_count": len(touched_path_counts),
        "touched_source_count": touched_source_count,
        "touched_op_count": touched_op_count,
        "touched_divergence_count": touched_divergence_count,
        "untouched_divergence_count": untouched_divergence_count,
    }


def irnode_to_no_comparison_text(node: IRNode) -> str:
    """Norway compare-only materialization.

    Current Lovdata consolidated texts sometimes omit section-title headings that
    appear in amendment-side future payloads. Ignore direct section heading
    children so verify focuses on operative text and structure rather than
    heading-only editorial drift.
    """
    if _no_kind_in(node.kind, frozenset({IRNodeKind.SUBSECTION, IRNodeKind.ITEM})) and node.children:
        parts = [node.text or ""]
        parts.extend(irnode_to_no_comparison_text(child) for child in node.children)
        return " ".join(part for part in parts if part)
    if node.text:
        return node.text
    children = node.children
    if _no_kind_is(node.kind, IRNodeKind.SECTION):
        children = [
            child
            for child in children
            if not _no_kind_is(child.kind, IRNodeKind.HEADING)
        ]
    parts = [irnode_to_no_comparison_text(child) for child in children]
    return " ".join(part for part in parts if part)


def _normalize_no_compare_tree(
    node: IRNode,
    *,
    projections_out: list[NOCompareProjection] | None = None,
    surface: str = "",
    path: tuple[tuple[str, str], ...] = (),
) -> IRNode:
    """Collapse sentence-only Norway containers for compare-only verification."""
    text = node.text
    text_projection_rule: tuple[str, str] | None = None
    if text and normalize_no_comparison_text(text) == "":
        text = ""
        text_projection_rule = (
            NO_VERIFY_COMPARE_REPEALED_SHELL_BLANKED,
            "Repealed shell text is blanked for compare-only missing-equals-empty verification.",
        )
    normalized_children = [
        _normalize_no_compare_tree(
            child,
            projections_out=projections_out,
            surface=surface,
            path=_no_compare_child_path(path, child),
        )
        for child in node.children
    ]
    if _no_kind_in(node.kind, frozenset({IRNodeKind.SUBSECTION, IRNodeKind.ITEM})):
        sentence_children = [child for child in normalized_children if _no_kind_is(child.kind, IRNodeKind.SENTENCE)]
        other_children = [child for child in normalized_children if not _no_kind_is(child.kind, IRNodeKind.SENTENCE)]
        if sentence_children:
            text = " ".join(child.text for child in sentence_children if child.text).strip()
            if text:
                text = " ".join(part for part in [normalize_no_comparison_text(node.text or ""), text] if part).strip()
            if not other_children:
                after = IRNode(
                    kind=node.kind,
                    label=node.label,
                    text=text,
                    attrs=dict(node.attrs),
                    children=(),
                )
                _append_no_compare_projection(
                    projections_out,
                    surface=surface,
                    rule_id=NO_VERIFY_COMPARE_SENTENCE_CHILDREN_COLLAPSED,
                    reason="Sentence children are collapsed into parent text for compare-only materialization.",
                    path=path,
                    before=node,
                    after=after,
                )
                return after
            after = IRNode(
                kind=node.kind,
                label=node.label,
                text=text,
                attrs=dict(node.attrs),
                children=tuple(other_children),
            )
            _append_no_compare_projection(
                projections_out,
                surface=surface,
                rule_id=NO_VERIFY_COMPARE_SENTENCE_CHILDREN_COLLAPSED,
                reason="Sentence children are collapsed into parent text for compare-only materialization.",
                path=path,
                before=node,
                after=after,
            )
            return after
        nested_item_children = [child for child in other_children if _no_kind_is(child.kind, IRNodeKind.ITEM)]
        if _no_kind_is(node.kind, IRNodeKind.ITEM) and nested_item_children and text:
            normalized_parent = normalize_no_comparison_text(text)
            cut_points = [
                normalized_parent.find(child_text)
                for child in nested_item_children
                if (child_text := normalize_no_comparison_text(child.text or ""))
            ]
            cut_points = [idx for idx in cut_points if idx > 0]
            if cut_points:
                text = normalized_parent[: min(cut_points)].rstrip(" ,;")
                text_projection_rule = (
                    NO_VERIFY_COMPARE_NESTED_ITEM_TAIL_SUPPRESSED,
                    "Parent item text duplicated in nested item children is suppressed for compare-only materialization.",
                )
    if _no_kind_is(node.kind, IRNodeKind.SECTION):
        heading_children = [child for child in normalized_children if _no_kind_is(child.kind, IRNodeKind.HEADING)]
        non_heading_children = [child for child in normalized_children if not _no_kind_is(child.kind, IRNodeKind.HEADING)]
        if (
            not non_heading_children
            and _is_no_self_section_lead_shell(node.label, node.text or "")
        ):
            after = IRNode(
                kind=node.kind,
                label=node.label,
                text="",
                attrs=dict(node.attrs),
                children=tuple(heading_children),
            )
            _append_no_compare_projection(
                projections_out,
                surface=surface,
                rule_id=NO_VERIFY_COMPARE_SELF_SECTION_SHELL_BLANKED,
                reason="Self-section amendment lead shell is blanked for compare-only materialization.",
                path=path,
                before=node,
                after=after,
            )
            return after
        heading_texts = [
            normalize_no_comparison_text(child.text or "").lower()
            for child in normalized_children
            if _no_kind_is(child.kind, IRNodeKind.HEADING) and child.text
        ]
        subsection_children = [child for child in normalized_children if _no_kind_is(child.kind, IRNodeKind.SUBSECTION)]
        if subsection_children and any(
            _is_no_contingent_other_laws_placeholder(child.text or "") for child in subsection_children
        ):
            after = IRNode(
                kind=node.kind,
                label=node.label,
                text="",
                attrs=dict(node.attrs),
                children=(),
            )
            _append_no_compare_projection(
                projections_out,
                surface=surface,
                rule_id=NO_VERIFY_COMPARE_CONTINGENT_OTHER_LAWS_PLACEHOLDER_SUPPRESSED,
                reason="Contingent other-laws placeholder section is suppressed for compare-only materialization.",
                path=path,
                before=node,
                after=after,
            )
            return after
        if subsection_children:
            first = subsection_children[0]
            intro_text = normalize_no_comparison_text(first.text or "")
            if intro_text.lower().endswith("forstås med:"):
                rebuilt_items: list[IRNode] = []
                if first.children and all(_no_kind_is(child.kind, IRNodeKind.ITEM) for child in first.children):
                    rebuilt_items = [
                        IRNode(
                            kind=IRNodeKind.ITEM,
                            label=child.label,
                            text=normalize_no_comparison_text(child.text or ""),
                            attrs=dict(child.attrs),
                            children=child.children,
                        )
                        for child in first.children
                    ]
                elif (
                    len(subsection_children) >= 3
                    and len(subsection_children[1:]) % 2 == 0
                    and all(not child.children for child in subsection_children[1:])
                ):
                    pairs = list(zip(subsection_children[1::2], subsection_children[2::2], strict=True))
                    if all(
                        normalize_no_comparison_text(left.text or "").endswith(":")
                        and normalize_no_comparison_text(right.text or "")
                        for left, right in pairs
                    ):
                        rebuilt_items = [
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label=str(idx),
                                text=normalize_no_comparison_text(
                                    " ".join(
                                        part
                                        for part in [
                                            normalize_no_comparison_text(left.text or ""),
                                            normalize_no_comparison_text(right.text or ""),
                                        ]
                                        if part
                                    )
                                ),
                            )
                            for idx, (left, right) in enumerate(pairs, start=1)
                        ]
                if rebuilt_items:
                    rebuilt_first = IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label=first.label,
                        text=intro_text,
                        attrs=dict(first.attrs),
                        children=tuple(rebuilt_items),
                    )
                    kept_children: list[IRNode] = []
                    replaced = False
                    for child in normalized_children:
                        if _no_kind_is(child.kind, IRNodeKind.SUBSECTION):
                            if not replaced:
                                kept_children.append(rebuilt_first)
                                replaced = True
                            continue
                        kept_children.append(child)
                    after = IRNode(
                        kind=node.kind,
                        label=node.label,
                        text=text,
                        attrs=dict(node.attrs),
                        children=tuple(kept_children),
                    )
                    _append_no_compare_projection(
                        projections_out,
                        surface=surface,
                        rule_id=NO_VERIFY_COMPARE_DEFINITION_SUBSECTION_PAIRS_COLLAPSED,
                        reason="Definition term/value subsection pairs are collapsed into item children for compare-only materialization.",
                        path=path,
                        before=node,
                        after=after,
                    )
                    return after
        subsection_texts = [
            normalize_no_comparison_text(child.text or "").lower()
            for child in subsection_children
        ]
        has_other_laws_marker = any(_has_no_other_laws_marker(heading) for heading in heading_texts) or any(
            _has_no_other_laws_marker(subsection_text) for subsection_text in subsection_texts
        )
        if (
            has_other_laws_marker
            and subsection_children
        ):
            first_detail_index = next(
                (
                    idx
                    for idx, child in enumerate(subsection_children)
                    if _has_no_other_laws_marker(child.text or "")
                ),
                None,
            )
            if first_detail_index is None:
                first_detail_index = 0
            kept_children: list[IRNode] = []
            detail_seen = 0
            for child in normalized_children:
                if not _no_kind_is(child.kind, IRNodeKind.SUBSECTION):
                    kept_children.append(child)
                    continue
                if detail_seen == first_detail_index:
                    kept_children.append(
                        IRNode(
                            kind=child.kind,
                            label=child.label,
                            text=normalize_no_comparison_text(child.text or ""),
                            attrs=dict(child.attrs),
                            children=child.children,
                        )
                    )
                detail_seen += 1
            after = IRNode(
                kind=node.kind,
                label=node.label,
                text=text,
                attrs=dict(node.attrs),
                children=tuple(kept_children),
            )
            _append_no_compare_projection(
                projections_out,
                surface=surface,
                rule_id=NO_VERIFY_COMPARE_OTHER_LAWS_CONTEXT_SUPPRESSED,
                reason="Other-laws detail children are suppressed for compare-only materialization.",
                path=path,
                before=node,
                after=after,
            )
            return after
    after = IRNode(
        kind=node.kind,
        label=node.label,
        text=text,
        attrs=dict(node.attrs),
        children=tuple(normalized_children),
    )
    if text_projection_rule is not None:
        rule_id, reason = text_projection_rule
        _append_no_compare_projection(
            projections_out,
            surface=surface,
            rule_id=rule_id,
            reason=reason,
            path=path,
            before=node,
            after=after,
        )
    return after


def _non_container_path(path: TreePath) -> TreePath:
    return tuple(step for step in path if step[0] not in {"part", "chapter"})


def _is_chapter_relocation_pair(
    left: ConsistencyDivergence,
    right: ConsistencyDivergence,
) -> bool:
    kinds = {left.divergence_type, right.divergence_type}
    if kinds != {"OPS_MISSING", "CONSOLIDATED_MISSING"}:
        return False
    left_text = normalize_no_comparison_text(left.ops_text or left.consolidated_text or "")
    right_text = normalize_no_comparison_text(right.ops_text or right.consolidated_text or "")
    if not left_text or left_text != right_text:
        return False
    left_path = tuple(left.address.path)
    right_path = tuple(right.address.path)
    return left_path != right_path and _non_container_path(left_path) == _non_container_path(right_path)


def _strip_annex_section_prefix(label: str) -> str:
    """Strip a Lovdata *Vedlegg* annex-token prefix from a Norway section label.

    Lovdata encodes EEA-agreement annexes as a top-level chapter whose
    ``chapter`` step label is the annex token (e.g. ``v22c`` for *Vedlegg
    22c*, EEA Agreement Annex XXII nr. 10c). Section labels inside that
    annex-chapter carry the SAME token duplicated as a slash-prefix (e.g.
    ``v22c/a1``), distinct from a normal section label (``a1``) that
    addresses the same operative content in the canonical chapter body.

    The strip is one-shot (only the leading ``<token>/`` is removed); ``a1``
    has no slash and is returned unchanged. The label is also returned
    unchanged when the prefix does not match the Lovdata annex-token shape
    (``v\\d+[a-z]*``), so unrelated slash-bearing section labels are not
    silently coerced into a false pairing.

    §1.11 firewall: this is a comparison-plane *recognizer*, not a
    legal-state authorization — it never mutates replay; it only routes
    two divergences into a paired-partition receipt (§1.8 conservation).
    """
    if "/" not in label:
        return label
    prefix, _, rest = label.partition("/")
    if not _NO_ANNEX_TOKEN_RE.fullmatch(prefix):
        return label
    return rest or label


def _is_annex_prefixed_relocation_pair(
    left: ConsistencyDivergence,
    right: ConsistencyDivergence,
) -> bool:
    """Pair two byte-identical-text OPS_MISSING/CONSOLIDATED_MISSING divergences
    whose non-container paths differ only by a Lovdata annex-token section-label prefix.

    Distinct from :func:`_is_chapter_relocation_pair` because the chapter
    labels are structurally paired by annex *encoding*, not by chapter-only
    differences at the same address level: one side's chapter is the annex
    token (e.g. ``v22c``), the other's is the canonical legislative body
    (e.g. ``1``), and BOTH the chapter and the section label carry the token
    (the section is ``v22c/a1`` versus ``a1``). The ``_non_container_path``
    helper already strips the chapter step, so a plain chapter-only pairing
    would not match the section-label prefix artifact and would leave both
    divergences on the primary surface (counted as two separate mismatches).

    §1.11 firewall: this predicate only *partitions* divergences on the
    compare surface; it does not authorize legal state, mutation, lifecycle,
    or target scope. Strict-mode behavior: ``proceed`` — partition is a
    presentation receipt, not a mutation.

    §1.8 conservation: every filtered pair emits two ``NOFilteredDivergence``
    receipts (one per divergence) under the
    ``no_verify.annex_prefixed_relocation_pair`` rule_id, so the suppression
    is auditable.

    Source witness: ``no/lov/2006-06-30-50`` (samvirkeforetaksloven — SCE-loven)
    on the Norway compare surface — 215 paired OPS_MISSING/
    CONSOLIDATED_MISSING entries across chapters I–IX whose text is
    byte-identical modulo a single ``v22c/`` annex-token prefix on the
    section label. The remaining ~104 entries carry Lovdata's
    ``[ES]`` / ``[ES-stat]`` EEA-adaptation editorial annotations and do
    NOT pair under this rule (text genuinely differs); they belong on the
    cross-act-placement frontier as an owned claim, not a code fix.
    """
    kinds = {left.divergence_type, right.divergence_type}
    if kinds != {"OPS_MISSING", "CONSOLIDATED_MISSING"}:
        return False
    left_text = normalize_no_comparison_text(left.ops_text or left.consolidated_text or "")
    right_text = normalize_no_comparison_text(right.ops_text or right.consolidated_text or "")
    if not left_text or left_text != right_text:
        return False
    left_path = tuple(left.address.path)
    right_path = tuple(right.address.path)
    if left_path == right_path:
        return False
    # Only fire when at least one paired section label carries an annex
    # prefix that the stripping resolves to equality. This excludes pure
    # chapter_relocation_pair cases (no section-label difference) so the
    # two rules stay disjoint: the annex_prefix rule strictly owns the
    # annex-encoded-relocation shape, and plain chapter relocations
    # remain the chapter_relocation_pair's property.
    def _has_annex_strip_candidate(path: TreePath) -> bool:
        return any(
            kind == "section" and _strip_annex_section_prefix(label) != label
            for kind, label in path
        )

    if not (_has_annex_strip_candidate(left_path) or _has_annex_strip_candidate(right_path)):
        return False

    # The annex-normalized path drops the container steps (part/chapter —
    # the same strip ``_non_container_path`` applies to ``chapter_
    # relocation_pair``) AND additionally strips the annex-token prefix
    # from any ``section`` step. Both transformations are required: the
    # chapter strip resolves ``chapter:1`` vs ``chapter:v22c`` (Lovdata
    # encodes the *Vedlegg* as a chapter), and the section strip resolves
    # ``section:a1`` vs ``section:v22c/a1`` (Lovdata duplicates the same
    # token on the section label inside that chapter). Subsection /
    # item / sentence levels are kept untouched, so a true mismatch at a
    # deeper level still surfaces as primary.
    def _annex_normalized(path: TreePath) -> TreePath:
        return tuple(
            (kind, _strip_annex_section_prefix(label) if kind == "section" else label)
            for kind, label in _non_container_path(path)
        )

    return _annex_normalized(left_path) == _annex_normalized(right_path)


def _partition_primary_divergences(divergences: list[ConsistencyDivergence]) -> NOPrimaryDivergencePartition:
    primary_candidates: list[ConsistencyDivergence] = []
    filtered: list[NOFilteredDivergence] = []
    paths = [tuple(div.address.path) for div in divergences]
    for idx, divergence in enumerate(divergences):
        path = paths[idx]
        if any(path_is_strict_prefix(path, other_path) for j, other_path in enumerate(paths) if j != idx):
            filtered.append(
                NOFilteredDivergence(
                    divergence=divergence,
                    rule_id="no_verify.prefix_descendant_suppressed",
                    reason="Divergence address is a strict prefix of another raw divergence address.",
                )
            )
            continue
        primary_candidates.append(divergence)

    primary: list[ConsistencyDivergence] = []
    paired: set[int] = set()
    # Pairing precedence: annex-prefixed relocation is tried first because the
    # predicate is strict about an annex-token section-label prefix — every
    # case it matches would NOT be matched by ``_is_chapter_relocation_pair``
    # (which preserves section labels). Pure-chapter relocations fall through
    # to the chapter_relocation_pair rule, so the two rules stay disjoint: a
    # filtered divergence always carries the rule that actually explains its
    # shape, never both.
    for idx, divergence in enumerate(primary_candidates):
        if idx in paired:
            continue
        partner_idx = next(
            (
                j
                for j in range(idx + 1, len(primary_candidates))
                if j not in paired and _is_annex_prefixed_relocation_pair(divergence, primary_candidates[j])
            ),
            None,
        )
        if partner_idx is not None:
            paired.add(partner_idx)
            for member in (divergence, primary_candidates[partner_idx]):
                filtered.append(
                    NOFilteredDivergence(
                        divergence=member,
                        rule_id="no_verify.annex_prefixed_relocation_pair",
                        reason=(
                            "Replay and current contain the same non-container provision text "
                            "whose only path difference is a Lovdata annex-token prefix on the "
                            "section label (e.g. chapter:v22c/section:v22c/a1 vs chapter:1/section:a1)."
                        ),
                    )
                )
            continue
        partner_idx = next(
            (
                j
                for j in range(idx + 1, len(primary_candidates))
                if j not in paired and _is_chapter_relocation_pair(divergence, primary_candidates[j])
            ),
            None,
        )
        if partner_idx is not None:
            paired.add(partner_idx)
            filtered.append(
                NOFilteredDivergence(
                    divergence=divergence,
                    rule_id="no_verify.chapter_relocation_pair",
                    reason="Replay and current contain the same non-container provision text at different chapter paths.",
                )
            )
            filtered.append(
                NOFilteredDivergence(
                    divergence=primary_candidates[partner_idx],
                    rule_id="no_verify.chapter_relocation_pair",
                    reason="Replay and current contain the same non-container provision text at different chapter paths.",
                )
            )
            continue
        primary.append(divergence)
    return NOPrimaryDivergencePartition(primary=tuple(primary), filtered=tuple(filtered))


def _primary_divergences(divergences: list[ConsistencyDivergence]) -> list[ConsistencyDivergence]:
    return list(_partition_primary_divergences(divergences).primary)


def load_no_current_statute(base_id: str, data_dir: Optional[Path] = None) -> IRStatute:
    data_dir = resolve_no_source_path(data_dir)
    current_bytes = load_no_current_bytes(base_id, data_dir)
    if current_bytes is None:
        raise FileNotFoundError(f"current Norway act not found: {base_id}")
    return parse_no_statute(current_bytes, base_id)


def verify_no_against_current(
    base_id: str,
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
) -> NOVerifyResult:
    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)

    indexed_entries = index.entries_for_base(base_id)

    replay = replay_no_to_pit(
        base_id,
        as_of=as_of,
        data_dir=data_dir,
        index=index,
        commencement_path=commencement_path,
    )
    # Closed-set replay_status derivation: REPLACE-IIF over the typed enum —
    # the only legal values of NOVerifyResult.replay_status per the §1.9
    # StrEnum closure, so the nested-ternary shape could never produce an
    # out-of-enum value. The enum carries the bench / scan / report
    # classification through `==` comparisons byte-for-byte (StrEnum).
    if replay.error:
        replay_status: NOReplayStatus = NOReplayStatus.ERROR
    elif replay.amendments_skipped_contingent:
        replay_status = NOReplayStatus.BLOCKED_CONTINGENT
    elif replay.amendments_skipped_unknown_effective:
        replay_status = NOReplayStatus.BLOCKED_UNKNOWN
    elif replay.amendments_skipped_missing_source:
        replay_status = NOReplayStatus.BLOCKED_MISSING_SOURCE
    else:
        replay_status = NOReplayStatus.REPLAYED
    result = NOVerifyResult(
        base_id=replay.base_id or base_id,
        as_of=as_of,
        replay=replay,
        indexed_amendment_count=len(indexed_entries),
        applied_amendment_count=len(replay.amendments_applied),
        replay_op_count=replay.n_ops,
        replay_status=replay_status,
    )
    if replay.error:
        result.error = replay.error
        return result
    if replay.replayed is None:
        result.error = "replay produced no statute"
        return result

    try:
        current = load_no_current_statute(result.base_id, data_dir)
    except FileNotFoundError as exc:
        result.error = str(exc)
        return result
    result.current_title = current.title

    compare_projections: list[NOCompareProjection] = []
    replay_compare = IRStatute(
        statute_id=replay.replayed.statute_id,
        title=replay.replayed.title,
        body=_normalize_no_compare_tree(
            replay.replayed.body,
            projections_out=compare_projections,
            surface="replay",
        ),
        supplements=replay.replayed.supplements,
        metadata=dict(replay.replayed.metadata),
    )
    current_compare = IRStatute(
        statute_id=current.statute_id,
        title=current.title,
        body=_normalize_no_compare_tree(
            current.body,
            projections_out=compare_projections,
            surface="current",
        ),
        supplements=current.supplements,
        metadata=dict(current.metadata),
    )

    replay_tl = ingest_consolidated(replay_compare, as_of=as_of)
    current_tl = ingest_consolidated(current_compare, as_of=as_of)
    divergences = verify_consistency(
        replay_tl,
        current_tl,
        as_of=as_of,
        irnode_to_text=irnode_to_no_comparison_text,
        text_normalizer=normalize_no_comparison_text,
        missing_equals_empty=True,
    )
    partition = _partition_primary_divergences(divergences)
    primary = list(partition.primary)
    counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    filtered_rule_counts: dict[str, int] = {}
    for divergence in primary:
        counts[divergence.divergence_type] = counts.get(divergence.divergence_type, 0) + 1
    for divergence in divergences:
        raw_counts[divergence.divergence_type] = raw_counts.get(divergence.divergence_type, 0) + 1
    for filtered in partition.filtered:
        filtered_rule_counts[filtered.rule_id] = filtered_rule_counts.get(filtered.rule_id, 0) + 1

    result.consistent = not primary
    result.divergence_count = len(primary)
    result.divergence_counts = counts
    result.raw_divergence_count = len(divergences)
    result.raw_divergence_counts = raw_counts
    result.filtered_divergence_count = len(partition.filtered)
    result.filtered_divergence_rule_counts = filtered_rule_counts
    result.filtered_divergences = list(partition.filtered)
    result.compare_projection_count = len(compare_projections)
    result.compare_projection_rule_counts = dict(Counter(projection.rule_id for projection in compare_projections))
    result.compare_projections = compare_projections
    result.divergences = primary
    base_year, base_year_finding = _no_base_year(result.base_id)
    if base_year_finding is not None:
        result.source_signal_diagnostic = base_year_finding
    result.source_signal = _infer_no_source_signal(
        divergence_count=result.divergence_count,
        indexed_amendment_count=result.indexed_amendment_count,
        replay_op_count=result.replay_op_count,
        base_year=base_year,
    )
    return result


def build_no_verify_scan(
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
    limit: int = 10,
    base_ids: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)

    inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    executable_status_map = inventory.amended_executable_law_status_map()
    candidates = sorted(
        (
            base_id
            for base_id, status in executable_status_map.items()
            if status == "fully_replayable"
        ),
        key=lambda base_id: (-len(inventory.base_to_sources.get(base_id, [])), base_id),
    )
    if base_ids:
        wanted = set(base_ids)
        candidates = [base_id for base_id in candidates if base_id in wanted]

    results = []
    summary = {
        "consistent": 0,
        "divergent": 0,
        "error": 0,
    }
    source_signal_counts: dict[str, int] = {}
    selected = candidates[:limit]
    for idx, base_id in enumerate(selected, start=1):
        if progress_callback is not None:
            progress_callback(f"[{idx}/{len(selected)}] {base_id}")
        verify_result = verify_no_against_current(
            base_id,
            as_of=as_of,
            data_dir=data_dir,
            index=index,
            commencement_path=commencement_path,
        )
        entry = {
            "base_id": verify_result.base_id,
            "current_title": verify_result.current_title,
            "replay_status": verify_result.replay_status,
            "consistent": verify_result.consistent,
            "divergence_count": verify_result.divergence_count,
            "divergence_counts": dict(verify_result.divergence_counts or {}),
            "amendment_count": len(inventory.base_to_sources.get(base_id, [])),
            "indexed_amendment_count": verify_result.indexed_amendment_count,
            "applied_amendment_count": verify_result.applied_amendment_count,
            "replay_op_count": verify_result.replay_op_count,
            "source_signal": verify_result.source_signal or "",
            "error": verify_result.error or "",
        }
        if verify_result.error:
            summary["error"] += 1
        elif verify_result.consistent:
            summary["consistent"] += 1
        else:
            summary["divergent"] += 1
        if verify_result.source_signal:
            source_signal_counts[verify_result.source_signal] = (
                source_signal_counts.get(verify_result.source_signal, 0) + 1
            )
        results.append(entry)

    return {
        "data_dir": str(data_dir),
        "as_of": as_of,
        "candidate_count": len(candidates),
        "scanned_count": len(results),
        "summary": summary,
        "source_signal_counts": source_signal_counts,
        "results": results,
    }


def build_no_verify_partition(
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
    limit: int = 10,
    base_ids: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    loaded_index = index if index is not None else _load_no_index(index_path=index_path, data_dir=data_dir)
    scan = build_no_verify_scan(
        as_of=as_of,
        data_dir=data_dir,
        index=loaded_index,
        index_path=index_path,
        commencement_path=commencement_path,
        limit=limit,
        base_ids=base_ids,
        progress_callback=progress_callback,
    )
    replay_defects: list[dict[str, Any]] = []
    untouched_drift: list[dict[str, Any]] = []
    source_sparse: list[dict[str, Any]] = []
    consistent: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in scan["results"]:
        if item["error"]:
            errors.append(item)
        elif item["consistent"]:
            consistent.append(item)
        elif item["source_signal"]:
            source_sparse.append(item)
        else:
            verify_result = verify_no_against_current(
                item["base_id"],
                as_of=as_of,
                data_dir=data_dir,
                index=loaded_index,
                index_path=index_path,
                commencement_path=commencement_path,
            )
            coverage = build_no_verify_coverage_summary(
                verify_result=verify_result,
                index=loaded_index,
                data_dir=data_dir,
            )
            item_with_coverage = dict(item)
            item_with_coverage.update(coverage)
            if coverage["touched_divergence_count"] > 0:
                replay_defects.append(item_with_coverage)
            else:
                untouched_drift.append(item_with_coverage)

    def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        return (
            -int(item.get("divergence_count", 0) or 0),
            -int(item.get("replay_op_count", 0) or 0),
            str(item.get("base_id", "")),
        )

    replay_defects.sort(key=_sort_key)
    untouched_drift.sort(key=_sort_key)
    source_sparse.sort(key=_sort_key)
    consistent.sort(key=_sort_key)
    errors.sort(key=lambda item: str(item.get("base_id", "")))

    return {
        "data_dir": scan["data_dir"],
        "as_of": scan["as_of"],
        "candidate_count": scan["candidate_count"],
        "scanned_count": scan["scanned_count"],
        "summary": dict(scan["summary"]),
        "source_signal_counts": dict(scan.get("source_signal_counts", {})),
        "partitions": {
            "replay_defect": replay_defects,
            "untouched_drift": untouched_drift,
            "source_sparse": source_sparse,
            "consistent": consistent,
            "error": errors,
        },
    }


def _load_no_index(
    *,
    index_path: Optional[Path],
    data_dir: Optional[Path],
) -> NOAmendmentIndex:
    if index_path is not None:
        return load_no_amendment_index(index_path)
    return build_no_amendment_index(data_dir)
