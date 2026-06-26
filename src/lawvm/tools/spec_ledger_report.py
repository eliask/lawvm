"""Ledger report / persistence / frontier-queue layer for the spec-discovery ledger.

This is a **read-only consumer** of ``spec_ledger.SpecLedger``'s public API
(``.rules``, ``.ranked_entries()``, ``.unattributed``, ``.statute_real_bugs``,
``.to_dict()``).  It does not mutate the ledger and does not touch the replay
path.  It owns three jobs:

1. **Persistence** — write a diffable, deterministic JSON + Markdown artifact
   under ``data/<jurisdiction>/``.  Determinism is the contract: running twice
   on the same ledger produces byte-identical output (stable key ordering
   everywhere, no set-iteration order, no timestamps in the body).
2. **Frontier queue** — rank the blind spots (``unattributed`` falsifying
   divergences with no attributable witness rule = the next-work queue) and the
   statutes where real bugs concentrate (``statute_real_bugs``) into a readable,
   most-contradicted-first work queue.
3. **Regression guard** — ``diff_catalog_coverage`` flags catalog drift (a rule
   that lost its ``believed_spec``, or a new uncataloged rule appearing) so the
   spec catalog cannot silently rot.

Authority grounding (``spec_authority``, Stream C) is an *optional* sibling
module; it is guard-imported, and when absent the rendered report simply omits
the grounding column.  This keeps Stream D independent of Stream C's landing
order.  When present, ``load_uk_authority_grounding()`` returns
``dict[str, AuthorityGrounding]`` and each grounded UK rule row gains an
``authority_tier`` + ``HAVE|GAP|SPEC`` column read straight off the frozen row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, cast

from lawvm.tools.spec_ledger import SpecLedger

if TYPE_CHECKING:
    from lawvm.tools.spec_authority import AuthorityGrounding

# ---------------------------------------------------------------------------
# Optional authority grounding (Stream C).  Guard-import: absent => no column.
# Bind the imported names to ``Optional`` aliases so the absent-sibling fallback
# is type-clean (a missing module degrades to ``None``, never a type error) while
# the present path reads the real frozen ``AuthorityGrounding`` rows.
# ---------------------------------------------------------------------------
_AuthorityGroundingType: type | None
_load_uk_authority_grounding: Callable[..., dict[str, Any]] | None
try:  # pragma: no cover - exercised by the import-present/absent tests via inject
    from lawvm.tools.spec_authority import (
        AuthorityGrounding as _AuthorityGroundingImport,
    )
    from lawvm.tools.spec_authority import (
        load_uk_authority_grounding as _load_uk_authority_grounding_import,
    )

    _AuthorityGroundingType = _AuthorityGroundingImport
    _load_uk_authority_grounding = _load_uk_authority_grounding_import
except ImportError:
    _AuthorityGroundingType = None
    _load_uk_authority_grounding = None


# A grounding lookup maps rule_id -> (authority_tier, status) where status is one
# of HAVE / GAP / SPEC.  We accept either a real ``AuthorityGrounding`` object
# (queried via a small duck-typed adapter) or a plain dict so tests can inject a
# fake without depending on Stream A's concrete shape.
GroundingRow = Tuple[str, str]  # (authority_tier, status)
_VALID_STATUS = ("HAVE", "GAP", "SPEC")


def _repo_root() -> Path:
    # src/lawvm/tools/spec_ledger_report.py -> parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Grounding normalization
# ---------------------------------------------------------------------------

def _normalize_grounding(
    grounding: object,
) -> Optional[Dict[str, GroundingRow]]:
    """Coerce a grounding source into ``rule_id -> (tier, status)`` or None.

    Accepts:
      * ``None`` -> None (no grounding column);
      * a plain ``dict`` whose values are either Stream C
        ``AuthorityGrounding`` rows (the real loader shape:
        ``load_uk_authority_grounding() -> dict[str, AuthorityGrounding]``),
        ``(tier, status)`` pairs, or ``{"authority_tier":..,"status":..}``
        dicts (test injection / persisted JSON).

    Unknown statuses degrade to ``"GAP"`` (loud-ish: visible, not silently HAVE).
    """
    if grounding is None:
        return None
    if not isinstance(grounding, dict):
        return None

    raw = cast(Dict[object, object], grounding)
    out: Dict[str, GroundingRow] = {}
    for rule_id, value in raw.items():
        tier, status = _coerce_grounding_value(value)
        out[str(rule_id)] = (tier, status)
    return out


def _coerce_grounding_value(value: object) -> GroundingRow:
    # Stream C's frozen AuthorityGrounding row: read its fields directly so the
    # real ``load_uk_authority_grounding()`` map normalizes to (tier, status)
    # instead of degrading via the str(value) fallback.
    if (
        _AuthorityGroundingType is not None
        and isinstance(value, _AuthorityGroundingType)
    ):
        row = cast("AuthorityGrounding", value)
        tier = str(row.authority_tier)
        status = str(row.authority_status)
    elif isinstance(value, dict):
        vd = cast(Dict[object, object], value)
        tier = str(vd.get("authority_tier") or vd.get("tier") or "")
        status = str(vd.get("status") or "")
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        seq = cast("tuple[object, ...] | list[object]", value)
        tier = str(seq[0])
        status = str(seq[1])
    else:
        tier = str(value)
        status = ""
    status = status.upper()
    if status not in _VALID_STATUS:
        status = "GAP"
    return tier, status


def _load_grounding_for(jurisdiction: str) -> Optional[Dict[str, GroundingRow]]:
    """Best-effort load of authority grounding for a jurisdiction, or None.

    Only UK has a loader in Stream C's first cut.  Absence degrades to None so
    the report renders without a grounding column.
    """
    if _load_uk_authority_grounding is None:
        return None
    if jurisdiction != "uk":
        return None
    grounding = _load_uk_authority_grounding()
    return _normalize_grounding(grounding)


# ---------------------------------------------------------------------------
# Deterministic dict builder for the persisted artifact
# ---------------------------------------------------------------------------

def _ranked_rule_rows(ledger: SpecLedger) -> List[Dict[str, object]]:
    """Rule rows sorted by a *total* key, each with nested collections stabilized.

    contradicted desc, divergences desc, then rule_id asc to break ties — so the
    ordering is deterministic even when two rules tie on the falsifying counts.
    """
    rules = sorted(
        ledger.rules.values(),
        key=lambda e: (-e.contradicted, -e.divergences, e.rule_id),
    )
    rule_rows: List[Dict[str, object]] = []
    for e in rules:
        d = e.to_dict()
        # Stabilize nested collections that defaultdict/list insertion order touch.
        d["by_disposition"] = dict(sorted(e.by_disposition.items()))
        d["exemplars"] = sorted(
            (dict(sorted(ex.items())) for ex in e.exemplars[:8]),
            key=lambda ex: (
                str(ex.get("statute", "")),
                str(ex.get("section", "")),
                str(ex.get("diagnosis", "")),
            ),
        )
        rule_rows.append(dict(sorted(d.items())))
    return rule_rows


def _deterministic_payload(ledger: SpecLedger) -> Dict[str, object]:
    """A fully sort-stable view of the ledger for byte-identical persistence.

    ``SpecLedger.to_dict`` already ranks rules and top-statutes, but ranking ties
    are not total and ``by_disposition`` / ``exemplars`` carry insertion order.
    We re-sort every collection by a total key so the artifact is diffable and
    re-runnable to the byte.
    """
    rule_rows = _ranked_rule_rows(ledger)

    unattributed = sorted(
        (dict(sorted(u.items())) for u in ledger.unattributed),
        key=lambda u: (
            str(u.get("statute", "")),
            str(u.get("section", "")),
            str(u.get("diagnosis", "")),
        ),
    )

    top_statutes = sorted(
        ledger.statute_real_bugs.items(),
        # real-bug count desc, then sid asc.
        key=lambda kv: (-kv[1], kv[0]),
    )

    return {
        "jurisdiction": ledger.jurisdiction,
        "mode": ledger.mode,
        "statutes": ledger.statutes,
        "statute_errors": ledger.statute_errors,
        "n_rules": len(ledger.rules),
        "n_unattributed": len(ledger.unattributed),
        "rules": rule_rows,
        "unattributed": unattributed,
        "top_statutes": [
            {"statute": sid, "real_bugs": count} for sid, count in top_statutes
        ],
    }


# ---------------------------------------------------------------------------
# Markdown rendering (with optional grounding column)
# ---------------------------------------------------------------------------

def render_report_markdown(
    ledger: SpecLedger,
    *,
    grounding: object = None,
) -> str:
    """Render a diffable Markdown view of the ledger.

    ``grounding`` may be ``None``, a plain ``rule_id -> (tier, status)`` (or
    ``{"authority_tier":.., "status":..}``) dict, or an ``AuthorityGrounding``
    object; it is normalized internally.  When present each rule row gains a
    grounding column (``authority_tier`` + ``HAVE|GAP|SPEC`` status).  Rows are
    ordered by the same total key as the persisted JSON so the two artifacts
    agree.
    """
    norm_grounding = _normalize_grounding(grounding)
    has_grounding = norm_grounding is not None

    lines = [
        f"# Spec-discovery ledger report (-j {ledger.jurisdiction}, {ledger.mode})",
        "",
        f"statutes={ledger.statutes} errors={ledger.statute_errors} "
        f"rules={len(ledger.rules)} "
        f"unattributed_divergences={len(ledger.unattributed)}",
        "",
    ]

    if has_grounding:
        header = (
            "| rule_id | cat | grounding | firings | corrob~ | "
            "contradicted | divergences | dispositions |"
        )
        sep = (
            "|---------|-----|-----------|---------|---------|"
            "--------------|-------------|--------------|"
        )
    else:
        header = (
            "| rule_id | cat | firings | corrob~ | contradicted | "
            "divergences | dispositions |"
        )
        sep = (
            "|---------|-----|---------|---------|--------------|"
            "-------------|--------------|"
        )
    lines += [header, sep]

    rule_rows = _ranked_rule_rows(ledger)
    for row in rule_rows:
        rule_id = str(row["rule_id"])
        cat = "Y" if row.get("cataloged") else "·"
        by_disp = row["by_disposition"]
        disp_items = sorted(by_disp.items()) if isinstance(by_disp, dict) else []
        disp = " ".join(f"{k}:{v}" for k, v in disp_items)
        cells = [rule_id, cat]
        if norm_grounding is not None:
            cells.append(_grounding_cell(norm_grounding, rule_id))
        cells += [
            str(row["firings"]),
            str(row["corroborated_est"]),
            str(row["contradicted"]),
            str(row["divergences"]),
            disp,
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", render_blind_spot_frontier(ledger)]
    return "\n".join(lines)


def _grounding_cell(grounding: Dict[str, GroundingRow], rule_id: str) -> str:
    row = grounding.get(rule_id)
    if row is None:
        return "—/GAP"
    tier, status = row
    tier = tier or "—"
    return f"{tier}/{status}"


# ---------------------------------------------------------------------------
# Blind-spot frontier / work queue
# ---------------------------------------------------------------------------

def render_blind_spot_frontier(ledger: SpecLedger) -> str:
    """Rank the spec-discovery frontier into a readable, most-contradicted-first queue.

    Two work surfaces:

    * **statutes where real bugs concentrate** (``statute_real_bugs``): the
      efficient mining targets — fix the statute with the most falsifying
      divergences first;
    * **unattributed falsifying divergences** (``unattributed``): blind spots
      with a real diagnosis but no witness rule to blame — the spec is silent
      here, so these need a *new* named rule before they can be ranked per-rule.

    Both are grouped/sorted deterministically (count desc, then id asc).
    """
    lines: List[str] = ["## Blind-spot frontier (next-work queue)"]

    # 1. Statutes where real bugs concentrate.
    statute_rows = sorted(
        ledger.statute_real_bugs.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    lines.append("")
    lines.append(
        f"### Statutes where real bugs concentrate ({len(statute_rows)})"
    )
    if statute_rows:
        lines.append("| statute | real_bugs |")
        lines.append("|---------|-----------|")
        for sid, count in statute_rows:
            lines.append(f"| {sid} | {count} |")
    else:
        lines.append("(none)")

    # 2. Unattributed divergences (blind spots), grouped by diagnosis so the
    #    most-contradicted shape surfaces first, with concrete exemplars.
    by_diagnosis: Dict[str, List[Dict[str, str]]] = {}
    for u in ledger.unattributed:
        by_diagnosis.setdefault(str(u.get("diagnosis", "")), []).append(u)
    grouped = sorted(
        by_diagnosis.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )
    lines.append("")
    lines.append(
        f"### Unattributed divergences — blind spots ({len(ledger.unattributed)})"
    )
    if grouped:
        lines.append("| diagnosis | count | exemplars |")
        lines.append("|-----------|-------|-----------|")
        for diagnosis, rows in grouped:
            exemplars = sorted(
                f"{r.get('statute', '')} {r.get('section', '')}".strip()
                for r in rows
            )
            shown = "; ".join(exemplars[:5])
            if len(exemplars) > 5:
                shown += f"; +{len(exemplars) - 5} more"
            lines.append(f"| {diagnosis or '—'} | {len(rows)} | {shown} |")
    else:
        lines.append("(none — every falsifying divergence has a witness rule)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_ledger(ledger: SpecLedger, out_dir: Path) -> Path:
    """Write a deterministic JSON + Markdown artifact under ``out_dir``.

    Layout::

        <out_dir>/spec_ledger.json
        <out_dir>/spec_ledger.md

    Determinism: ``json.dumps`` with ``sort_keys=True`` over a fully
    sort-stable payload; trailing newline; no timestamps in the body.  Running
    twice on the same ledger yields byte-identical files.

    Returns the path of the JSON artifact.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = _deterministic_payload(ledger)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    json_path = out_dir / "spec_ledger.json"
    json_path.write_text(json_text + "\n", encoding="utf-8")

    grounding = _load_grounding_for(ledger.jurisdiction)
    md_text = render_report_markdown(ledger, grounding=grounding)
    md_path = out_dir / "spec_ledger.md"
    md_path.write_text(md_text + "\n", encoding="utf-8")

    return json_path


def persist_ledger_for_jurisdiction(ledger: SpecLedger) -> Path:
    """Persist under the repo's canonical ``data/<jurisdiction>/`` directory."""
    out_dir = _repo_root() / "data" / ledger.jurisdiction
    return persist_ledger(ledger, out_dir)


# ---------------------------------------------------------------------------
# Regression guard: catalog-coverage drift
# ---------------------------------------------------------------------------

def diff_catalog_coverage(prev: dict, cur: dict) -> List[str]:
    """Flag catalog drift between a previous and current persisted ledger dict.

    Both arguments are persisted payloads (as written by ``persist_ledger`` /
    ``_deterministic_payload``), i.e. each has a ``"rules"`` list whose rows
    carry ``rule_id``, ``believed_spec``, and ``cataloged``.

    Returns a sorted list of human-readable drift messages for:

    * a rule that **lost its ``believed_spec``** (was cataloged in ``prev``,
      is no longer cataloged in ``cur``) — the spec regressed;
    * a **new uncataloged rule id** that appears in ``cur`` without a
      ``believed_spec`` and was not present in ``prev`` — a fresh blind spot
      that arrived without a spec hypothesis.

    An empty list means no drift.  This is a pure function so CI can diff a
    checked-in snapshot against a freshly built ledger and fail loudly.
    """
    prev_rules = _index_rules(prev)
    cur_rules = _index_rules(cur)

    messages: List[str] = []

    for rule_id, cur_row in cur_rules.items():
        cur_cataloged = _is_cataloged(cur_row)
        prev_row = prev_rules.get(rule_id)
        if prev_row is None:
            if not cur_cataloged:
                messages.append(
                    f"NEW UNCATALOGED RULE: {rule_id} appeared without a believed_spec"
                )
            continue
        if _is_cataloged(prev_row) and not cur_cataloged:
            messages.append(
                f"DECATALOGED RULE: {rule_id} lost its believed_spec"
            )

    return sorted(messages)


def _index_rules(payload: dict) -> Dict[str, dict]:
    rows = payload.get("rules") or []
    out: Dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("rule_id"):
            out[str(row["rule_id"])] = row
    return out


def _is_cataloged(row: dict) -> bool:
    if "cataloged" in row:
        return bool(row["cataloged"])
    return bool(row.get("believed_spec"))
