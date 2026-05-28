"""Pipeline intermediate capture for per-step error attribution.

Captures the input/output of each processing step per amendment,
enabling isolated testing and error attribution without pipeline changes.

Usage:
    from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore

    captures = []
    replay_xml(sid, intermediates_out=captures)
    store = CaptureStore()
    for c in captures:
        store.save(c)

    # Per-step testing:
    for c in store.load(statute_id):
        actual_ops = extract_ops(c.preamble_raw)
        assert actual_ops == c.peg_ops

API tier
--------
Internal debugging/capture surface. Stable enough for tooling, but not a
public semantic authority layer.

Note on field naming
--------------------
``preamble_raw`` and ``preamble_normalized`` replace earlier
frontend-specific field names. ``CaptureStore.load`` transparently migrates
old DB records that carry those legacy names.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AmendmentCapture:
    """Intermediate values from processing one amendment."""

    statute_id: str
    amendment_id: str

    # Step 1: amendment preamble / enacting-clause extraction.
    preamble_raw: str = ""          # from frontend preamble extraction
    preamble_normalized: str = ""   # after verb-normalization pass
    used_sec1_fallback: bool = False

    # Step 2: op extraction
    peg_ops: list[dict] = field(default_factory=list)  # from PEG
    extraction_path: str = ""  # "peg" | "fallback_heuristic" | "title_fallback" | "sec1"

    # Step 3: citation routing
    citation_match: bool = True    # did the preamble reference the parent?
    citation_action: str = ""      # "pass" | "skip_num_collision" | "skip_citation_mismatch"

    # Step 4: op normalization
    resolved_ops: list[dict] = field(default_factory=list)  # after compile_amendment_ops
    failed_ops: list[dict] = field(default_factory=list)

    # Step 5: body content
    body_section_labels: list[str] = field(default_factory=list)
    body_has_omissions: dict[str, bool] = field(default_factory=dict)

    # Step 6: apply results (per-section text snapshots)
    sections_modified: list[str] = field(default_factory=list)
    sections_inserted: list[str] = field(default_factory=list)
    sections_repealed: list[str] = field(default_factory=list)

    # Metadata
    effective_date: str = ""
    source_title: str = ""


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_captures (
    statute_id   TEXT NOT NULL,
    amendment_id TEXT NOT NULL,
    data         TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (statute_id, amendment_id)
);
"""


def _amendment_sort_key(amendment_id: str) -> tuple[int, int, int, str]:
    """Sort amendment ids by parsed year/number when available.

    Lexicographic ordering misplaces ids like ``2017/1000`` before
    ``2017/794``. Gold-capture consumers expect legal-ish source order, so
    prefer parsed numeric year/number and only fall back to the raw string
    when the id does not match the normal ``YYYY/NNN`` shape.
    """
    try:
        year_s, num_s = amendment_id.split("/", 1)
        return (0, int(year_s), int(num_s), amendment_id)
    except Exception:
        return (1, 0, 0, amendment_id)


class CaptureStore:
    """SQLite store for pipeline intermediate captures."""

    def __init__(self, db_path: str = ".cache/pipeline_gold.db") -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_CREATE_SQL)
        self._conn.commit()

    def save(self, capture: AmendmentCapture) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_captures VALUES (?, ?, ?, ?)",
            (
                capture.statute_id,
                capture.amendment_id,
                json.dumps(asdict(capture), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def save_batch(self, captures: list[AmendmentCapture]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO pipeline_captures VALUES (?, ?, ?, ?)",
            [
                (c.statute_id, c.amendment_id,
                 json.dumps(asdict(c), ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat())
                for c in captures
            ],
        )
        self._conn.commit()

    def load(self, statute_id: str) -> list[AmendmentCapture]:
        rows = self._conn.execute(
            "SELECT data FROM pipeline_captures WHERE statute_id = ?",
            (statute_id,),
        ).fetchall()
        result = []
        for (data_json,) in rows:
            d = json.loads(data_json)
            # Migrate legacy frontend-specific field names produced by older captures.
            if "johtolause_raw" in d and "preamble_raw" not in d:
                d["preamble_raw"] = d.pop("johtolause_raw")
            if "johtolause_normalized" in d and "preamble_normalized" not in d:
                d["preamble_normalized"] = d.pop("johtolause_normalized")
            result.append(AmendmentCapture(**{
                k: v for k, v in d.items()
                if k in AmendmentCapture.__dataclass_fields__
            }))
        result.sort(key=lambda capture: _amendment_sort_key(capture.amendment_id))
        return result

    def load_amendment(self, statute_id: str, amendment_id: str) -> Optional[AmendmentCapture]:
        row = self._conn.execute(
            "SELECT data FROM pipeline_captures WHERE statute_id = ? AND amendment_id = ?",
            (statute_id, amendment_id),
        ).fetchone()
        if not row:
            return None
        d = json.loads(row[0])
        # Migrate legacy frontend-specific field names (see load() for rationale).
        if "johtolause_raw" in d and "preamble_raw" not in d:
            d["preamble_raw"] = d.pop("johtolause_raw")
        if "johtolause_normalized" in d and "preamble_normalized" not in d:
            d["preamble_normalized"] = d.pop("johtolause_normalized")
        return AmendmentCapture(**{
            k: v for k, v in d.items()
            if k in AmendmentCapture.__dataclass_fields__
        })

    def statutes(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT DISTINCT statute_id FROM pipeline_captures ORDER BY statute_id"
        ).fetchall()]

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM pipeline_captures").fetchone()[0]
        statutes = self._conn.execute("SELECT COUNT(DISTINCT statute_id) FROM pipeline_captures").fetchone()[0]
        return {"total_amendments": total, "statutes": statutes}
