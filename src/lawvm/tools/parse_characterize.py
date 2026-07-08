"""parse-characterize — snapshot the johtolause parser's CURRENT behavior as a
characterization golden corpus (the regression oracle for a parser rewrite).

For every amendment johtolause in the corpus this records what the parser
produces TODAY — bugs included — as a stable, comparable fingerprint:

    {sid, ops: [op.code(), ...], rules: [witness_rule_id, ...],
     n_ops, clean: bool, fp: <fingerprint hash>}

``op.code()`` is the canonical op string the spec tests already assert
(``"M P L:3 12 2"``), so the snapshot is exactly the granularity a rewrite must
match.  ``clean`` is the parse-bench label (no interior/trailing silent drop).

This is a CHARACTERIZATION snapshot, not a correctness oracle: it pins behavior
so a candidate new parser can be diffed against it.  Every divergence is then
either (a) a row the snapshot marks ``clean=true`` that changed → a REGRESSION,
or (b) a row marked ``clean=false`` (a known drop) that changed → a candidate
FIX.  The replay benchmark and the spec tests remain the truth sources; this is
the safety net that makes the rewrite mechanical instead of a blind bet.

The golden corpus is a REGENERABLE local working file (gitignored, like the
``.farchive`` corpus it derives from), NOT a committed artifact: it is a pure
function of (parser code + corpus), it changes on every grammar edit by design,
and it is only meaningful relative to a specific parser commit.  Regenerate it
on demand at the start of rewrite work; do not commit the 7 MB output.  The only
durable fact is the reference baseline below.

Reference baseline (FI johtolause parser.
2026-06-15): 32233 amendment johtolauses, 32046 clean / 187 known-drop.

Two subcommands:
    parse-characterize snapshot --out PATH   write the golden corpus
    parse-characterize verify   --golden PATH  re-run + diff vs a saved golden,
                                               report regressions / fixes / drift
"""

from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, asdict

_AMENDMENT_VERB_PREFIXES = ("muute", "kumot", "lisät", "siirre", "korva")


def _archive_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


@dataclass(frozen=True)
class CharRow:
    sid: str
    ops: tuple[str, ...]
    rules: tuple[str, ...]
    n_ops: int
    clean: bool
    fp: str


def _fingerprint(ops: tuple[str, ...], rules: tuple[str, ...]) -> str:
    h = hashlib.sha1()
    h.update("\x1f".join(ops).encode("utf-8"))
    h.update(b"\x1e")
    h.update("\x1f".join(rules).encode("utf-8"))
    return h.hexdigest()[:16]


def _characterize_one(sid: str) -> CharRow | None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.johtolause.coverage_audit import classify_uncovered_spans

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xb = store.read_source(sid) or store.read_amendment(sid)
    if not xb:
        return None
    try:
        johto = get_johtolause(xb) or ""
    except Exception:
        return None
    if not johto or "§" not in johto:
        return None
    head = " ".join(johto.split())[:24].lower()
    if not head.startswith(_AMENDMENT_VERB_PREFIXES):
        return None  # non-amendment enactment — not part of the grammar oracle

    try:
        parsed = parse_clause(johto, statute_id=sid)
        ops = tuple((o.code() or "") for o in (parsed.parsed_ops or []))
        rules = tuple(
            (o.witness.rule_id if o.witness else "") for o in (parsed.parsed_ops or [])
        )
        drops = [
            c
            for c in classify_uncovered_spans(johto)
            if c.position in ("interior", "trailing")
            and c.tier in ("verb_no_op", "unmatched_section")
        ]
        clean = not drops
    except Exception:
        ops, rules, clean = (), (), False

    return CharRow(
        sid=sid,
        ops=ops,
        rules=rules,
        n_ops=len(ops),
        clean=clean,
        fp=_fingerprint(ops, rules),
    )


def _all_rows(limit: int, workers: int) -> list[CharRow]:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]
    print(f"parse-characterize: scanning {len(ids)} statutes...", file=sys.stderr)
    rows: list[CharRow] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_characterize_one, ids, chunksize=50)):
            if r is not None:
                rows.append(r)
            if i and i % 10000 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)
    rows.sort(key=lambda r: r.sid)
    return rows


def _write_golden(rows: list[CharRow], path: str) -> None:
    clean = sum(1 for r in rows if r.clean)
    corpus_hash = hashlib.sha1(
        "\n".join(f"{r.sid}:{r.fp}" for r in rows).encode("utf-8")
    ).hexdigest()
    with open(path, "w") as f:
        f.write(
            json.dumps(
                {
                    "_meta": {
                        "kind": "fi_johtolause_characterization_golden_v1",
                        "n_amendment_johtolauses": len(rows),
                        "n_clean": clean,
                        "n_known_drop": len(rows) - clean,
                        "corpus_hash": corpus_hash,
                    }
                }
            )
            + "\n"
        )
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False, separators=(",", ":")) + "\n")
    print(
        f"wrote {len(rows)} rows ({clean} clean / {len(rows) - clean} known-drop) "
        f"to {path}\ncorpus_hash={corpus_hash}",
        file=sys.stderr,
    )


def _read_golden(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            obj = json.loads(line)
            if "_meta" in obj:
                continue
            rows[obj["sid"]] = obj
    return rows


def cmd_snapshot(args) -> None:
    rows = _all_rows(getattr(args, "limit", 0) or 0, getattr(args, "workers", 0) or 8)
    out = getattr(args, "out", "") or "data/finland/parse_characterization_golden.jsonl"
    _write_golden(rows, out)


def cmd_verify(args) -> None:
    golden_path = getattr(args, "golden", "") or "data/finland/parse_characterization_golden.jsonl"
    old = _read_golden(golden_path)
    new_rows = _all_rows(getattr(args, "limit", 0) or 0, getattr(args, "workers", 0) or 8)
    new = {r.sid: asdict(r) for r in new_rows}

    regressions: list[str] = []  # was clean, ops changed
    fixes: list[str] = []  # was known-drop, now clean
    drop_changes: list[str] = []  # known-drop, ops changed but still dropped
    new_sids = sorted(set(new) - set(old))
    gone_sids = sorted(set(old) - set(new))

    for sid, o in old.items():
        n = new.get(sid)
        if n is None:
            continue
        if o["fp"] == n["fp"]:
            continue
        if o["clean"] and n["clean"]:
            regressions.append(sid)  # clean row whose ops moved == regression
        elif o["clean"] and not n["clean"]:
            regressions.append(sid)  # clean -> drop == regression
        elif (not o["clean"]) and n["clean"]:
            fixes.append(sid)
        else:
            drop_changes.append(sid)

    as_json = getattr(args, "json", False)
    result = {
        "regressions": regressions,
        "fixes": fixes,
        "drop_changes": drop_changes,
        "new_statutes": new_sids,
        "removed_statutes": gone_sids,
        "n_regressions": len(regressions),
        "n_fixes": len(fixes),
    }
    if as_json:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    else:
        print("\n=== parse-characterize verify ===")
        print(f"  REGRESSIONS (clean row changed) : {len(regressions)}")
        for sid in regressions[:40]:
            print(f"    ! {sid}: {old[sid]['ops']} -> {new[sid]['ops']}")
        print(f"  FIXES (known-drop -> clean)     : {len(fixes)}")
        for sid in fixes[:40]:
            print(f"    + {sid}")
        print(f"  drop-row ops changed (still drop): {len(drop_changes)}")
        print(f"  new statutes                    : {len(new_sids)}")
        print(f"  removed statutes                : {len(gone_sids)}")
    # Non-zero exit on regression so this is usable as a gate.
    if regressions:
        sys.exit(1)


def main(args) -> None:
    sub = getattr(args, "characterize_cmd", "") or "snapshot"
    if sub == "verify":
        cmd_verify(args)
    else:
        cmd_snapshot(args)
