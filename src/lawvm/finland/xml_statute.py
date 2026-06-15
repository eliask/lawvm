"""XML-backed statute wrapper and operative-text serialization for Finland."""

from __future__ import annotations

from typing import Any, Optional

import lxml.etree as etree

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.scoped_section_resolver import find_scoped_section_path
from lawvm.finland.xml_ir import fi_xml_to_ir_node

_OPERATIVE_TEXT_SKIP_HCONTAINERS = frozenset(
    {"signatures", "attachments", "conclusions", "omission"}
)


def _serialize_oper_body_text(node: IRNode) -> str:
    if (
        node.kind == IRNodeKind.HCONTAINER
        and node.attrs.get("name") in _OPERATIVE_TEXT_SKIP_HCONTAINERS
    ):
        return ""
    if node.text:
        return node.text
    return " ".join(
        part for part in (_serialize_oper_body_text(child) for child in node.children) if part
    )


class XMLStatute:
    """Thin convenience wrapper around a statute XML tree."""

    def __init__(self, xml_bytes: bytes):
        self._base_xml_bytes: bytes = xml_bytes
        self.tree = etree.fromstring(xml_bytes)
        self.id = self._get_id()
        self.title = self._get_title()
        body_el = self.tree.find(".//{*}body")
        if body_el is None:
            body_el = self.tree
        self._ir: IRNode = fi_xml_to_ir_node(body_el, _fi_label_postprocessor)
        self._base_ir: IRNode = self._ir
        self._label_index: Optional[_tops.LabelIndex] = None
        self.timelines: Optional[dict[str, Any]] = None

    @property
    def ir(self) -> IRNode:
        return self._ir

    @ir.setter
    def ir(self, value: IRNode) -> None:
        self._ir = value
        self._label_index = None

    def _get_label_index(self) -> "_tops.LabelIndex":
        if self._label_index is None:
            self._label_index = _tops.build_label_index(self._ir)
        return self._label_index

    def _get_id(self) -> str:
        num_el = self.tree.find(".//{*}docNumber")
        return num_el.text.strip() if num_el is not None else "0/0"

    def _get_title(self) -> str:
        title_el = self.tree.find(".//{*}docTitle")
        return (
            etree.tostring(title_el, method="text", encoding="unicode").strip()
            if title_el is not None
            else "Unknown"
        )

    def _find_path(
        self,
        kind: str,
        label: str,
        scope_kind: Optional[str] = None,
        scope_label: Optional[str] = None,
    ) -> tuple[tuple[str, str], ...] | None:
        path = _tops.find(
            self._ir,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
            label_index=self._get_label_index(),
        )
        if path is not None:
            return path
        return _tops.find(
            self._ir,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )

    def _find_node(
        self,
        kind: str,
        label: str,
        scope_kind: Optional[str] = None,
        scope_label: Optional[str] = None,
    ) -> Optional[IRNode]:
        path = self._find_path(kind, label, scope_kind, scope_label)
        return _tops.resolve(self.ir, path) if path is not None else None

    def find_section_path(
        self,
        sec_num: str,
        chapter_num: Optional[str] = None,
        part_num: Optional[str] = None,
    ) -> tuple[tuple[str, str], ...] | None:
        return find_scoped_section_path(
            self.ir,
            target_section=sec_num,
            target_chapter=chapter_num,
            target_part=part_num,
            find_path=self._find_path,
        )

    def find_section(
        self,
        sec_num: str,
        chapter_num: Optional[str] = None,
        part_num: Optional[str] = None,
    ) -> Optional[IRNode]:
        path = self.find_section_path(sec_num, chapter_num, part_num)
        return _tops.resolve(self.ir, path) if path is not None else None

    def find_base_section(
        self,
        sec_num: str,
        chapter_num: Optional[str] = None,
        part_num: Optional[str] = None,
    ) -> Optional[IRNode]:
        if part_num:
            part_path = _tops.find(self._base_ir, "part", part_num)
            part_node = (
                _tops.resolve(self._base_ir, part_path) if part_path is not None else None
            )
            if part_path is not None and part_node is not None:
                if chapter_num:
                    chapter_path = _tops.find(part_node, "chapter", chapter_num)
                    chapter_node = (
                        _tops.resolve(part_node, chapter_path)
                        if chapter_path is not None
                        else None
                    )
                    if chapter_path is not None and chapter_node is not None:
                        section_path = _tops.find(chapter_node, "section", sec_num)
                        if section_path is not None:
                            return _tops.resolve(
                                self._base_ir,
                                part_path + chapter_path + section_path,
                            )
                section_path = _tops.find(part_node, "section", sec_num)
                if section_path is not None:
                    return _tops.resolve(self._base_ir, part_path + section_path)
        path = _tops.find(
            self._base_ir,
            "section",
            sec_num,
            scope_kind=IRNodeKind.CHAPTER.value if chapter_num else None,
            scope_label=chapter_num,
        )
        return _tops.resolve(self._base_ir, path) if path is not None else None

    def find_chapter(self, chap_num: str) -> Optional[IRNode]:
        return self._find_node("chapter", chap_num)

    def find_part(self, part_num: str) -> Optional[IRNode]:
        return self._find_node("part", part_num)

    def serialize_text(self) -> str:
        return _serialize_oper_body_text(self.ir)


def serialize_text(ir: IRNode) -> str:
    """Serialize operative body text from an IRNode, excluding appendices."""
    return _serialize_oper_body_text(ir)
