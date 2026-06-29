"""Canonical serialization for LawVM IRNode trees.

The IRNode tree is a frozen recursive structure (``kind``, ``label``,
``text``, ``attrs``, ``children``). This module provides the stable
JSON serialization and deserialization — the canonical on-disk format
for storing parsed IR trees (attachment PDFs, OCR'd statutes, any
structured source that LawVM parsed from non-XML input).

Format::

    {
      "kind": "SECTION",
      "label": "5",
      "text": "",
      "attrs": {"eId": "sec_5"},
      "children": [
        {"kind": "HEADING", "label": null, "text": "Soveltamisala", ...},
        {"kind": "CONTENT", "label": null, "text": "Tätä asetusta...", ...}
      ]
    }

Stable: the five fields (kind, label, text, attrs, children) are the
IRNode's full public surface — no private fields leaked. New attrs
keys are additive (consumers ignore unknown keys).

Versioning: the ``ir_format_version`` field on the wrapper metadata
(not inside individual nodes) tracks schema evolution. Current v1.

Used by:
  - ``data/finland/attachment_ir/`` — git-tracked canonical IR for
    Finlex attachment PDFs, keyed by (statute_id, pdf_name).
  - Future: OCR'd statutes, scanned-source frontends.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind

IR_FORMAT_VERSION = 1


def ir_to_json(node: IRNode) -> dict[str, Any]:
    """Serialize an IRNode tree to a JSON-compatible dict.

    Pure transform — no side effects, no I/O. The dict is round-trip-safe
    via :func:`ir_from_json`.
    """
    return {
        "kind": str(node.kind.name if hasattr(node.kind, "name") else node.kind),
        "label": node.label,
        "text": node.text,
        "attrs": dict(node.attrs) if node.attrs else {},
        "children": [ir_to_json(c) for c in node.children],
    }


def ir_from_json(d: dict[str, Any]) -> IRNode:
    """Deserialize a JSON-compatible dict back to an IRNode tree.

    Unknown ``kind`` values fall back to ``P`` (reasonable — an unknown
    structural kind is treated as a text-bearing leaf; §1.10 fail loud
    but don't crash the load).
    """
    kind_name = str(d.get("kind") or "")
    try:
        kind = IRNodeKind[kind_name]
    except KeyError:
        kind = IRNodeKind.P
    return IRNode(
        kind=kind,
        label=d.get("label"),
        text=d.get("text", ""),
        attrs=d.get("attrs", {}),
        children=tuple(ir_from_json(c) for c in d.get("children", [])),
    )


def ir_to_json_str(node: IRNode, *, indent: int = 2) -> str:
    """Serialize an IRNode tree to a JSON string."""
    return json.dumps(ir_to_json(node), ensure_ascii=False, indent=indent)


def ir_from_json_str(s: str) -> IRNode:
    """Deserialize a JSON string to an IRNode tree."""
    return ir_from_json(json.loads(s))


# ---------------------------------------------------------------------------
# Canonical file store (git-tracked)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ATTACHMENT_IR_DIR = _REPO_ROOT / "data" / "finland" / "attachment_ir"


def attachment_ir_path(statute_id: str, pdf_name: str) -> Path:
    """Canonical path for a git-tracked attachment IR file."""
    safe_sid = statute_id.replace("/", "_")
    safe_pdf = pdf_name.replace("/", "_").replace(".pdf", ".json")
    return _ATTACHMENT_IR_DIR / safe_sid / safe_pdf


def load_attachment_ir(statute_id: str, pdf_name: str) -> IRNode | None:
    """Load a git-tracked canonical attachment IR if present.

    Returns the IRNode tree, or ``None`` when no canonical file exists
    (the caller falls back to live extraction).

    Tolerates either of two on-disk wrapper shapes:

      * the canonical :func:`store_attachment_ir` schema (``{"ir": …, "meta": …}``),
      * the ``lawvm.source_document_ir.d0`` schema produced by the
        SourceDocumentIR pipeline (``{"root": …, "source": …, "extraction": …}``).

    Both wrap the same IRNode JSON tree under different keys; the loader
    reads whichever is present and falls back to ``None`` if the file is
    unreadable or carries neither. (The D0 schema is the source-document-
    level wrapper; it is not a "legacy" path the loader is asked to
    deprecate — the goal is one wrapper, but the D0 fixture predates the
    serializer, so the read-time tolerance lets the canonical store
    migrate file-by-file rather than in one big-bang rewrite.)
    """
    path = attachment_ir_path(statute_id, pdf_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    wrapped = data.get("ir") if isinstance(data, dict) else None
    if wrapped is None and isinstance(data, dict):
        wrapped = data.get("root")
    if not isinstance(wrapped, dict):
        return None
    try:
        return ir_from_json(wrapped)
    except (KeyError, TypeError, ValueError):
        return None


def store_attachment_ir(
    statute_id: str,
    pdf_name: str,
    ir: IRNode,
    *,
    source_ref: str = "",
    extraction_method: str = "",
    pdf_sha256: str = "",
) -> Path:
    """Persist an attachment IR tree to the canonical git-tracked store.

    Creates the directory if needed. Writes atomically (tmp + rename).
    Returns the path written to.
    """
    path = attachment_ir_path(statute_id, pdf_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ir_format_version": IR_FORMAT_VERSION,
        "ir": ir_to_json(ir),
        "meta": {
            "statute_id": statute_id,
            "pdf_name": pdf_name,
            "source_ref": source_ref,
            "extraction_method": extraction_method,
            "pdf_sha256": pdf_sha256,
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def list_stored_attachment_irs() -> list[Path]:
    """List all canonical attachment IR files in the store."""
    if not _ATTACHMENT_IR_DIR.exists():
        return []
    return sorted(_ATTACHMENT_IR_DIR.rglob("*.json"))


# ---------------------------------------------------------------------------
# Content-addressed runtime cache (non-git; speed only)
# ---------------------------------------------------------------------------

_CACHE_DIR = (
    _REPO_ROOT / "data" / "finland" / ".attachment_ir_cache"
)


def load_cached_ir(pdf_bytes: bytes) -> tuple[IRNode, dict[str, Any]] | None:
    """Load from the runtime content-addressed cache (NOT the git store).

    The cache is keyed on pdf_bytes sha256 — stable across runs as long
    as the PDF content doesn't change. This is a speed optimisation; the
    canonical store is :func:`load_attachment_ir` (git-tracked).
    """
    import hashlib

    key = hashlib.sha256(pdf_bytes).hexdigest()
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ir_from_json(data["ir"]), data.get("meta", {})
    except (json.JSONDecodeError, KeyError):
        return None


def store_cached_ir(
    pdf_bytes: bytes,
    ir: IRNode,
    *,
    pdf_name: str,
    source_ref: str,
    extraction_method: str,
) -> None:
    """Persist to the runtime content-addressed cache."""
    import hashlib
    from datetime import date

    key = hashlib.sha256(pdf_bytes).hexdigest()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{key}.json"
    data = {
        "ir": ir_to_json(ir),
        "meta": {
            "pdf_name": pdf_name,
            "source_ref": source_ref,
            "extraction_method": extraction_method,
            "cached_at": date.today().isoformat(),
            "sha256": key,
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
