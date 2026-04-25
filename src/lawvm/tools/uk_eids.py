"""lawvm uk-eids -- inspect nearby UK EIDs/text by prefix."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


def _iter_prefixed_rows(
    eid_map: dict[str, str],
    text_map: dict[str, str],
    *,
    prefix: str,
) -> Iterable[tuple[str, str]]:
    wanted = prefix.lower()
    seen: set[str] = set()
    for eid in sorted(set(eid_map.values())):
        if eid.lower().startswith(wanted) and eid not in seen:
            seen.add(eid)
            yield eid, text_map.get(eid, "")


def _snippet(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes

    statute_id: str = args.statute_id
    prefix: str = args.prefix
    side: str = getattr(args, "side", "both")
    limit: int | None = getattr(args, "limit", None)
    show_text: bool = bool(getattr(args, "show_text", False))
    db_arg = getattr(args, "db", None)

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    sides = ["base", "oracle"] if side == "both" else [side]

    with Farchive(db_path) as archive:
        for which in sides:
            enacted = which == "base"
            url = _archive_url_for_statute(statute_id, pit_date=None, enacted=enacted)
            blob = archive.get(url)
            print(f"{which.upper()}: {statute_id}")
            if not blob or len(blob) < 100:
                print("  (missing)")
                print()
                continue
            data = extract_eid_map_bytes(blob)
            rows = list(_iter_prefixed_rows(data.get("eid_map", {}), data.get("text_map", {}), prefix=prefix))
            if limit is not None:
                rows = rows[:limit]
            print(f"  prefix: {prefix}")
            print(f"  matches: {len(rows)}")
            for eid, text in rows:
                print(f"  {eid}")
                if show_text:
                    print(f"    {_snippet(text, limit=220)}")
            print()
