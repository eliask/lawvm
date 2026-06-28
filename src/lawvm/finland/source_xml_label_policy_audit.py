"""Non-mutating audit for Finland source XML label normalization policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import lxml.etree as etree

from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.helpers import (
    _fi_label_postprocessor,
    _normalize_source_part_num,
    _normalize_source_section_num,
    _norm_num_token,
)


@dataclass(frozen=True, slots=True)
class LabelPolicyValue:
    policy: str
    value: str


@dataclass(frozen=True, slots=True)
class SourceXmlLabelAuditRow:
    statute_id: str
    element_kind: str
    raw_num: str
    sourceline: int | None
    path: tuple[tuple[str, str], ...]
    policies: tuple[LabelPolicyValue, ...]

    @property
    def divergent(self) -> bool:
        return len({item.value for item in self.policies}) > 1

    def to_jsonable(self) -> dict[str, object]:
        return {
            "statute_id": self.statute_id,
            "element_kind": self.element_kind,
            "raw_num": self.raw_num,
            "sourceline": self.sourceline,
            "path": [f"{kind}:{label}" for kind, label in self.path],
            "divergent": self.divergent,
            "policies": {item.policy: item.value for item in self.policies},
        }


def audit_source_xml_label_policies(
    statute_id: str,
    xml_bytes: bytes,
    *,
    include_agreeing: bool = False,
) -> tuple[SourceXmlLabelAuditRow, ...]:
    """Return source-label policy comparison rows for one Finland XML document.

    The audit is intentionally observational. It compares candidate policies
    that already exist in the codebase; it does not choose a canonical policy or
    alter replay behavior.
    """
    root = parse_corpus_xml(xml_bytes, recover=True)
    rows: list[SourceXmlLabelAuditRow] = []
    for element in root.iter():
        kind = _localname(element)
        if kind not in {"part", "chapter", "section"}:
            continue
        raw_num = _direct_num_text(element)
        if not raw_num:
            continue
        policies = _policy_values(kind, raw_num)
        row = SourceXmlLabelAuditRow(
            statute_id=statute_id,
            element_kind=kind,
            raw_num=raw_num,
            sourceline=element.sourceline,
            path=_ancestor_label_path(element),
            policies=policies,
        )
        if include_agreeing or row.divergent:
            rows.append(row)
    return tuple(rows)


def summarize_label_policy_rows(rows: Iterable[SourceXmlLabelAuditRow]) -> dict[str, object]:
    rows_tuple = tuple(rows)
    by_kind: dict[str, int] = {}
    divergent_by_kind: dict[str, int] = {}
    raw_examples: list[dict[str, object]] = []
    for row in rows_tuple:
        by_kind[row.element_kind] = by_kind.get(row.element_kind, 0) + 1
        if row.divergent:
            divergent_by_kind[row.element_kind] = divergent_by_kind.get(row.element_kind, 0) + 1
            if len(raw_examples) < 20:
                raw_examples.append(row.to_jsonable())
    return {
        "rows": len(rows_tuple),
        "divergent_rows": sum(1 for row in rows_tuple if row.divergent),
        "by_kind": by_kind,
        "divergent_by_kind": divergent_by_kind,
        "examples": raw_examples,
    }


def _policy_values(kind: str, raw_num: str) -> tuple[LabelPolicyValue, ...]:
    if kind == "part":
        return (
            LabelPolicyValue("source_part_num", _normalize_source_part_num(raw_num)),
            LabelPolicyValue("fi_label_postprocessor", _fi_postprocessed(raw_num, "part")),
        )
    if kind == "chapter":
        return (
            LabelPolicyValue("norm_strip_luku", _norm_num_token(raw_num).removesuffix("luku")),
            LabelPolicyValue("fi_label_postprocessor", _fi_postprocessed(raw_num, "chapter")),
        )
    return (
        LabelPolicyValue("source_section_num", _normalize_source_section_num(raw_num)),
        LabelPolicyValue("fi_label_postprocessor", _fi_postprocessed(raw_num, "section")),
    )


def _fi_postprocessed(raw_num: str, kind: str) -> str:
    return _fi_label_postprocessor(kind, _norm_num_token(raw_num))


def _localname(element: etree._Element) -> str:
    tag = element.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _direct_num_text(element: etree._Element) -> str:
    num = element.find("{*}num")
    if num is None:
        return ""
    return " ".join("".join(str(part) for part in num.itertext()).split())


def _ancestor_label_path(element: etree._Element) -> tuple[tuple[str, str], ...]:
    path: list[tuple[str, str]] = []
    chain: list[etree._Element] = []
    current: etree._Element | None = element
    while current is not None:
        chain.append(current)
        current = current.getparent()
    for node in reversed(chain):
        kind = _localname(node)
        if kind not in {"part", "chapter", "section"}:
            continue
        raw_num = _direct_num_text(node)
        if raw_num:
            path.append((kind, _policy_values(kind, raw_num)[-1].value))
    return tuple(path)
