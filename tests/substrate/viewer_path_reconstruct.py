"""Point-in-time text reconstructors for the two viewer paths (parity proof).

This is the small, dependency-free reconstruction layer the
``test_viewer_path_parity`` differential test drives. It deliberately mirrors
exactly what each *viewer* does at render time, so a parity pass here is a proof
about the artifacts the browsers actually consume — not about some third
re-implementation:

* :func:`reconstruct_old` reads the dense ``transition-graph.v1`` SQLite the OLD
  ``export-transition-graph`` → ``statute-timeline`` path emits. Point-in-time
  text at date ``D`` = for every ``active_at`` row at ``D`` (in document order),
  the rendered text of its ``content_blobs`` subtree. The blob is the exact
  ``IRNode.to_jsonable_dict()`` the engine stored; we deserialize it and render
  text with :func:`irnode_to_text` — the same text recipe the NEW path's content
  leaf is built from.

* :func:`reconstruct_new` reads the sparse ``lawvm.pack.work.v0`` pack the NEW
  ``pack-work`` → ``law-graph.js`` path emits. Point-in-time text at date ``D`` =
  per address, the ``node_version`` whose ``effect_interval`` half-open
  ``[start, end)`` contains ``D`` (the ``governing_text`` selection profile),
  rendered from its ``content_leaf`` text. This is a line-for-line Python mirror
  of ``substrate-pack.js`` ``versionAt`` / ``textAt``.

Both reconstructors key on the SAME canonical covering-unit address strings
(``chapter:2/section:7``) the engine emits, so addresses compare directly with
no normalization beyond a trailing-whitespace strip on the rendered text (the
two paths agree byte-for-byte before stripping at the dates tested; the strip is
defensive, not a divergence cover).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from lawvm.core.ir_helpers import irnode_from_dict, irnode_to_text

# --------------------------------------------------------------------------- #
# OLD path — dense transition-graph.v1 SQLite                                  #
# --------------------------------------------------------------------------- #


def old_change_dates(db_path: str | Path) -> list[str]:
    """The engine change-date axis the dense export committed to (``meta``)."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='change_dates'").fetchone()
        return list(json.loads(row[0])) if row else []
    finally:
        conn.close()


def reconstruct_old(db_path: str | Path, date: str) -> dict[str, str]:
    """``{address: rendered_text}`` live at ``date`` per the dense ``active_at``.

    Renders each active covering unit's stored subtree blob with the same
    text recipe (:func:`irnode_to_text`) the NEW path's content leaf uses, so a
    text difference is a real state difference, never a serialization artifact.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        out: dict[str, str] = {}
        blob_text: dict[str, str] = {}
        rows = conn.execute(
            "SELECT address, content_hash FROM active_at WHERE date=? ORDER BY rowid",
            (date,),
        ).fetchall()
        for r in rows:
            chash = r["content_hash"]
            if chash not in blob_text:
                blob = conn.execute(
                    "SELECT content_json FROM content_blobs WHERE content_hash=?",
                    (chash,),
                ).fetchone()
                if blob is None:
                    raise AssertionError(
                        f"dense active_at references content_hash {chash!r} with no "
                        f"content_blobs row (broken dense export)"
                    )
                node = irnode_from_dict(json.loads(blob["content_json"]))
                blob_text[chash] = irnode_to_text(node).rstrip()
            out[r["address"]] = blob_text[chash]
        return out
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# NEW path — sparse lawvm.pack.work.v0 pack                                    #
# --------------------------------------------------------------------------- #


class _PackText:
    """Loaded pack indices for ``governing_text`` interval selection.

    Mirrors ``substrate-pack.js``: a content-leaf map (hash -> text), an
    address-node map (struct_node_id -> address_path), and node_versions grouped
    by address. :meth:`text_at` is the Python twin of ``versionAt``/``textAt``.
    """

    def __init__(self, pack_dir: str | Path) -> None:
        pack = Path(pack_dir)
        self.leaves: dict[str, str] = {}
        self.address_by_node: dict[str, str] = {}
        self.versions_by_addr: dict[str, list[dict]] = defaultdict(list)
        for rel in ("base/base.jsonl", "state/state.jsonl"):
            path = pack / rel
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)["object"]
                schema = obj.get("schema")
                if schema == "lawvm.content_leaf.v1":
                    self.leaves[obj["content_leaf_hash"]] = obj.get("text", "")
                elif schema == "lawvm.address_node.v1":
                    self.address_by_node[obj["struct_node_id"]] = obj["address_path"]
                elif schema == "lawvm.node_version.v1":
                    addr = self.address_by_node.get(obj["struct_node_id"])
                    if addr is None:
                        # Defer: address node may come after its version. Park by
                        # struct id and resolve in a second pass below.
                        self.versions_by_addr.setdefault("\x00pending:" + obj["struct_node_id"], []).append(obj)
                    else:
                        self.versions_by_addr[addr].append(obj)
        # Resolve any node_versions parked before their address node was seen.
        for key in [k for k in self.versions_by_addr if k.startswith("\x00pending:")]:
            struct_id = key[len("\x00pending:") :]
            addr = self.address_by_node.get(struct_id)
            if addr is None:
                raise AssertionError(
                    f"pack node_version references struct_node_id {struct_id!r} with no "
                    f"address_node (broken pack)"
                )
            self.versions_by_addr[addr].extend(self.versions_by_addr.pop(key))

    def version_at(self, addr: str, date: str) -> dict | None:
        """The node_version whose half-open ``[start, end)`` contains ``date``."""
        for v in self.versions_by_addr.get(addr, ()):
            start, end = v["effective_interval"]
            if (not start or start <= date) and (end is None or date < end):
                return v
        return None

    def text_at(self, addr: str, date: str) -> str | None:
        v = self.version_at(addr, date)
        if v is None:
            return None
        return self.leaves.get(v["content_leaf_hash"], "").rstrip()


def reconstruct_new(pack_dir: str | Path, date: str) -> dict[str, str]:
    """``{address: rendered_text}`` live at ``date`` per the pack intervals.

    The ``governing_text`` selection profile: each address contributes the text
    of its node_version whose half-open ``effect_interval`` contains ``date``;
    an address with no live version at ``date`` (deleted / not yet commenced) is
    omitted, matching the OLD path's "no active_at row" behaviour.
    """
    pack = _PackText(pack_dir)
    out: dict[str, str] = {}
    for addr in pack.versions_by_addr:
        text = pack.text_at(addr, date)
        if text is not None:
            out[addr] = text
    return out
