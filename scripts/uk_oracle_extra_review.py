#!/usr/bin/env python3
"""Classify UK oracle-extra candidates by inspecting the oracle XML target.

This is a manual review aid only. It does not authorize replay and does not
claim the official consolidation is wrong.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from lxml import etree as ET

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport


_LEG_BASE = "https://www.legislation.gov.uk"
_DEFAULT_ARCHIVE = Path("data/uk_legislation.farchive")
_FORBIDDEN_SHORTCUTS = (
    "oracle_extra_state_as_source_truth",
    "oracle_commentary_as_replay_authorization",
    "oracle_target_presence_as_mutation_proof",
)


@dataclass(frozen=True)
class UKOracleExtraReviewRow:
    statute_id: str
    target: str
    review_status: str
    reason: str
    base_target_present: bool
    base_text_witness_present: bool
    oracle_target_present: bool
    oracle_markup_kinds: tuple[str, ...]
    oracle_commentaries: tuple[str, ...]
    oracle_text_preview: str
    agreement_residual: Mapping[str, Any]


def load_reviews(candidates_path: Path, *, archive_path: Path) -> list[UKOracleExtraReviewRow]:
    from farchive import Farchive

    rows = _load_rows(candidates_path)
    archive = Farchive(archive_path)
    try:
        out: list[UKOracleExtraReviewRow] = []
        for row in rows:
            statute_id = str(row.get("statute_id") or "")
            if not statute_id:
                continue
            base_xml = _archive_get_optional(
                archive, f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
            )
            oracle_xml = _archive_get_optional(archive, f"{_LEG_BASE}/{statute_id}/data.xml")
            if not oracle_xml:
                continue
            for target in _target_tuple(row):
                out.append(
                    review_target(
                        statute_id=statute_id,
                        target=target,
                        base_xml=base_xml,
                        oracle_xml=oracle_xml,
                    )
                )
        return out
    finally:
        archive.close()


def review_target(
    *,
    statute_id: str,
    target: str,
    base_xml: bytes | None,
    oracle_xml: bytes,
) -> UKOracleExtraReviewRow:
    base_root = _parse_xml(base_xml) if base_xml else None
    oracle_root = _parse_xml(oracle_xml)
    base_el = _find_id(base_root, target) if base_root is not None else None
    oracle_el = _find_id(oracle_root, target)
    markup_kinds = _markup_kinds(oracle_el)
    commentaries = _commentaries(oracle_root, oracle_el)
    text_preview = _text_preview(oracle_el)
    base_text_witness_present = _base_text_witness_present(base_root, text_preview)
    status, reason = _status(
        target=target,
        base_present=base_el is not None,
        base_text_witness_present=base_text_witness_present,
        oracle_present=oracle_el is not None,
        markup_kinds=markup_kinds,
        commentaries=commentaries,
        text_preview=text_preview,
    )
    residual = _agreement_residual(
        statute_id=statute_id,
        target=target,
        review_status=status,
        base_present=base_el is not None,
        base_text_witness_present=base_text_witness_present,
        oracle_present=oracle_el is not None,
        markup_kinds=markup_kinds,
        commentaries=commentaries,
    )
    return UKOracleExtraReviewRow(
        statute_id=statute_id,
        target=target,
        review_status=status,
        reason=reason,
        base_target_present=base_el is not None,
        base_text_witness_present=base_text_witness_present,
        oracle_target_present=oracle_el is not None,
        oracle_markup_kinds=markup_kinds,
        oracle_commentaries=commentaries,
        oracle_text_preview=text_preview,
        agreement_residual=residual.to_dict(),
    )


def _status(
    *,
    target: str,
    base_present: bool,
    base_text_witness_present: bool,
    oracle_present: bool,
    markup_kinds: Sequence[str],
    commentaries: Sequence[str],
    text_preview: str,
) -> tuple[str, str]:
    if not oracle_present:
        return "oracle_target_missing", "The sampled oracle target was not found."
    if "wrapper" in target:
        return (
            "likely_topology_wrapper_residual",
            "The target is a generated wrapper EID; inspect topology/granularity before source-chain review.",
        )
    if _is_annotation_target(target):
        return (
            "likely_annotation_projection_residual",
            "The target is an annotation-style projection EID; inspect projection topology before source-chain review.",
        )
    if _is_compacted_range_or_legacy_label(target, text_preview):
        return (
            "likely_range_or_legacy_label_residual",
            "The target looks like a compacted range or legacy label EID, not a simple added legal unit.",
        )
    if base_text_witness_present:
        return (
            "likely_base_text_materialization_gap",
            "The target text is already visible in enacted XML under a different or coarser structure.",
        )
    lower_notes = " ".join(commentaries).lower()
    if "repeal" in lower_notes or "omitted" in lower_notes or "omit" in lower_notes:
        return (
            "likely_repeal_display_convention",
            "The oracle target carries repeal or omission commentary.",
        )
    if markup_kinds and commentaries:
        return (
            "likely_source_chain_or_lowering_gap",
            "The oracle target has amendment markup with commentary; prove the source chain before suspecting the oracle.",
        )
    if base_present:
        return (
            "likely_compare_shape_or_replay_gap",
            "The target also exists in enacted XML, so this is not a simple oracle-only addition.",
        )
    if markup_kinds:
        return (
            "needs_commentary_review",
            "The oracle target has amendment markup but no commentary text was found.",
        )
    return (
        "manual_review_candidate",
        "The oracle target has no obvious wrapper, repeal, or amendment-commentary explanation.",
    )


def _agreement_residual(
    *,
    statute_id: str,
    target: str,
    review_status: str,
    base_present: bool,
    base_text_witness_present: bool,
    oracle_present: bool,
    markup_kinds: Sequence[str],
    commentaries: Sequence[str],
) -> AgreementResidual:
    return AgreementResidual(
        residual_id=f"uk-oracle-extra:{statute_id}:{target}",
        jurisdiction="uk",
        agreement_surface="enacted_xml_vs_current_oracle_xml_target_presence",
        family=_residual_family(review_status),
        status=_residual_status(review_status),
        owner_phase="compare_oracle_classification",
        rule_id=f"uk_oracle_extra_{review_status}",
        source_artifact_id=statute_id,
        replay_count=1 if base_present else 0,
        oracle_count=1 if oracle_present else 0,
        missing_proofs=_missing_proofs(review_status),
        safe_default="manual_review_without_replay_promotion",
        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
        detail={
            "target": target,
            "review_status": review_status,
            "base_target_present": base_present,
            "base_text_witness_present": base_text_witness_present,
            "oracle_target_present": oracle_present,
            "oracle_markup_kinds": list(markup_kinds),
            "oracle_commentary_count": len(commentaries),
        },
    )


def _residual_family(review_status: str) -> str:
    if review_status == "likely_topology_wrapper_residual":
        return "topology_granularity_mismatch"
    if review_status in {
        "likely_annotation_projection_residual",
        "likely_range_or_legacy_label_residual",
        "likely_base_text_materialization_gap",
    }:
        return "non_commensurable_surface"
    if review_status in {
        "likely_source_chain_or_lowering_gap",
        "needs_commentary_review",
    }:
        return "source_footing_gap"
    if review_status == "likely_repeal_display_convention":
        return "oracle_editorial_pathology"
    if review_status == "likely_compare_shape_or_replay_gap":
        return "replay_bug"
    if review_status == "manual_review_candidate":
        return "unknown"
    return "source_footing_gap"


def _residual_status(review_status: str) -> str:
    if review_status == "manual_review_candidate":
        return "residual"
    return "frontier"


def _missing_proofs(review_status: str) -> tuple[str, ...]:
    if review_status == "likely_topology_wrapper_residual":
        return ("topology_granularity_review",)
    if review_status in {
        "likely_annotation_projection_residual",
        "likely_range_or_legacy_label_residual",
        "likely_base_text_materialization_gap",
    }:
        return ("compare_projection_review",)
    if review_status in {
        "likely_source_chain_or_lowering_gap",
        "needs_commentary_review",
    }:
        return ("source_instruction_witness", "canonical_operation_lowering")
    if review_status == "manual_review_candidate":
        return ("source_instruction_witness", "editorial_policy_review")
    return ()


def _emit_json(rows: Sequence[UKOracleExtraReviewRow]) -> str:
    status_counts: dict[str, int] = {}
    residual_family_counts: dict[str, int] = {}
    residual_status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.review_status] = status_counts.get(row.review_status, 0) + 1
        residual = row.agreement_residual
        family = str(residual.get("family") or "")
        status = str(residual.get("status") or "")
        residual_family_counts[family] = residual_family_counts.get(family, 0) + 1
        residual_status_counts[status] = residual_status_counts.get(status, 0) + 1
    summary = {
        "row_count": len(rows),
        "review_status_counts": status_counts,
        "manual_review_candidate_count": status_counts.get("manual_review_candidate", 0),
        "agreement_residual_family_counts": residual_family_counts,
        "agreement_residual_status_counts": residual_status_counts,
    }
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_oracle_extra_review",
        schema="lawvm.uk_oracle_extra_review.v1",
        truth_claim="oracle_extra_target_review_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filtered_summary=summary,
        rows=tuple(asdict(row) for row in rows),
        detail={"forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS)},
    )
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _emit_markdown(rows: Sequence[UKOracleExtraReviewRow]) -> str:
    manual = [row for row in rows if row.review_status == "manual_review_candidate"]
    lines = [
        "# UK oracle-extra manual review candidates",
        "",
        "This is a review queue, not a legal conclusion.",
        "",
    ]
    if not manual:
        lines.append("No sampled target currently survives as a clean manual-review candidate.")
        return "\n".join(lines) + "\n"
    for index, row in enumerate(manual, start=1):
        lines.append(f"{index}. {row.statute_id} {row.target}: {row.reason}")
        lines.append(f"Current text: {row.oracle_text_preview}")
        lines.append("")
    return "\n".join(lines)


def _load_rows(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text())
    rows = data.get("rows", data) if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path} does not contain candidate rows")
    return [row for row in rows if isinstance(row, Mapping)]


def _target_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    for key in ("oracle_only_samples", "retained_repeal_targets", "replay_only_samples"):
        values = row.get(key)
        if isinstance(values, list):
            return tuple(str(value) for value in values if str(value))
    return ()


def _archive_get_optional(archive: Any, locator: str) -> bytes | None:
    try:
        return archive.get(locator)
    except KeyError:
        return None


def _parse_xml(xml: bytes | None) -> ET._Element:
    if not xml:
        raise ValueError("XML bytes are required")
    return ET.fromstring(xml)


def _find_id(root: ET._Element | None, target: str) -> ET._Element | None:
    if root is None:
        return None
    found = root.xpath("//*[@id=$target or @eId=$target]", target=target)
    if not found:
        lowered = target.lower()
        found = root.xpath(
            "//*[translate(@id, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')=$target "
            "or translate(@eId, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')=$target]",
            target=lowered,
        )
    return found[0] if found else None


def _base_text_witness_present(root: ET._Element | None, text_preview: str) -> bool:
    if root is None:
        return False
    needle = _materialization_witness_needle(text_preview)
    if not needle:
        return False
    haystack = _normalize_materialization_witness(" ".join(root.itertext()))
    return needle in haystack


def _materialization_witness_needle(text_preview: str) -> str:
    normalized = _normalize_materialization_witness(text_preview)
    tokens = normalized.split()
    if tokens and len(tokens[0]) == 1:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return ""
    return " ".join(tokens[:12])


def _normalize_materialization_witness(text: str) -> str:
    normalized = (
        text.replace("“", " ")
        .replace("”", " ")
        .replace("’", "'")
        .replace("—", " ")
        .replace("–", "-")
        .lower()
    )
    punctuation = '!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~'
    for char in punctuation:
        normalized = normalized.replace(char, " ")
    return _squash(normalized)


def _is_annotation_target(target: str) -> bool:
    return target == "annotation" or "-annotation-" in target or target.startswith(
        "annotation-"
    )


def _is_compacted_range_or_legacy_label(target: str, text_preview: str) -> bool:
    if not target.startswith("section-"):
        return False
    leaf = target.rsplit("-", 1)[-1].rstrip(".")
    if not leaf.isdigit() or len(leaf) < 2:
        return False
    normalized_preview = _normalize_materialization_witness(text_preview)
    if normalized_preview == leaf:
        return True
    first_token = text_preview.split(maxsplit=1)[0] if text_preview else ""
    if "–" in first_token or "-" in first_token:
        return True
    if target.endswith("."):
        return True
    return len(leaf) >= 3


def _markup_kinds(el: ET._Element | None) -> tuple[str, ...]:
    if el is None:
        return ()
    kinds: set[str] = set()
    for node in el.iter():
        tag = ET.QName(node).localname if isinstance(node.tag, str) else ""
        if tag in {"Addition", "Substitution", "Repeal"}:
            kinds.add(tag)
    return tuple(sorted(kinds))


def _commentaries(root: ET._Element, el: ET._Element | None) -> tuple[str, ...]:
    if el is None:
        return ()
    refs: list[str] = []
    for value in el.xpath(
        './/*[local-name()="CommentaryRef"]/@Ref | .//@CommentaryRef | .//@ChangeId'
    ):
        if value not in refs:
            refs.append(str(value))
    out: list[str] = []
    for ref in refs:
        for comment in root.xpath('//*[local-name()="Commentary" and @id=$ref]', ref=ref):
            text = _squash(" ".join(comment.itertext()))
            if text and text not in out:
                out.append(text)
    return tuple(out[:3])


def _text_preview(el: ET._Element | None) -> str:
    if el is None:
        return ""
    return _squash(" ".join(el.itertext()))[:500]


def _squash(text: str) -> str:
    return " ".join(text.split())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review UK oracle-extra targets against oracle XML markup/commentary."
    )
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--archive", type=Path, default=_DEFAULT_ARCHIVE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    rows = load_reviews(args.candidates, archive_path=args.archive)
    payload = _emit_json(rows)
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload)
    if args.markdown:
        args.markdown.write_text(_emit_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
