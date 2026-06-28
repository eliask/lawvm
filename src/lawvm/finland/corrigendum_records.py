from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent
_OFFICIAL_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_official_fi.jsonl"
_ADJUDICATIONS_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_adjudications_fi.jsonl"
_SOURCES_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_sources_fi.jsonl"
# Upstream-corrigenda extraction retries — per-``stable_id`` overlay records
# targeting rows in ``_OFFICIAL_JSONL``. Kept in ``corrigendum_records`` (the
# canonical records module) so tools that read OR write overlays share one
# path constant with the loader in ``corrigendum.py``.
_RETRY_OVERLAYS_JSONL = (
    _LAWVM_DIR / "data" / "finland" / "corrigendum_retry_overlays_fi.jsonl"
)
# Oracle overrides — typed manual adjudication of Finlex consolidated text
# where the oracle (consolidated comparison surface) is wrong in any way:
# stale editorial, transcription error, wrong section, missing amendment
# effect, omitted repeal, editorial-convention-vs-legal-truth mismatch, etc.
# (LawVM-replay-is-right is only one of several possible oracle-wrong shapes.)
# A distinct plane from upstream-corrigenda retries and source-defect fixes:
# an override mutates the *comparison surface*, never the source XML
# (AGENTS.md §2.10 projection plane; §0 promotion boundary). Empty today;
# populated as oracle disagreements are adjudicated.
_ORACLE_OVERRIDES_YAML = (
    _LAWVM_DIR / "data" / "finland" / "oracle_overrides_fi.yaml"
)
# Unresolvable-corrigendum records — per-``stable_id`` overlays declaring an
# upstream-corrigendum item genuinely cannot be applied mechanically. The
# official row is SKIPPED at load time (no patch emitted); a typed finding
# records why. See notes/schema at top of ``corrigendum_unresolvable_fi.yaml``.
_UNRESOLVABLE_YAML = (
    _LAWVM_DIR / "data" / "finland" / "corrigendum_unresolvable_fi.yaml"
)

_OFFICIAL_FIELDS = [
    "stable_id",
    "source_pdf",
    "statute_id",
    "amendment_id",
    "lang",
    "correction_index",
    "correction_type",
    "location_desc",
    "wrong_text",
    "correct_text",
    "extraction_source",
    "date_published",
    "llm_extraction",
    "vision_extraction",
    "regex_extraction",
    "parse_error",
    "extract_agreed",
]

_ADJUDICATION_FIELDS = [
    "stable_id",
    "verified_in_source",
]

# Retry-overlay records — per-``stable_id`` overlays on official rows. Each
# overlay replaces the auto-extracted ``(wrong_text, correct_text)`` of one
# official row with one or more byte-exact ``patches`` that, applied
# together, realise the upstream-corrigendum effect.
#
# ``rule_id`` / ``family`` are labels (AGENTS.md §2.1 — not a heavy taxonomy
# at this layer). ``span_verified`` is the LLM-loop terminator: the
# ``wrong_text`` exists byte-exact in the source XML at the cited locator.
_RETRY_OVERLAY_FIELDS = [
    "stable_id",
    "rule_id",
    "family",
    "amendment_id",
    "source_pdf_witness",
    "correction_type",
    "span_verified",
    "verified_at",
    "patches",
]

# Unresolvable-corrigendum records — per-``stable_id`` overlay that declares
# the upstream-corrigendum item genuinely cannot be applied mechanically. The
# patch's effect may be purely semantic (no byte-level representation exists
# in source XML), or the source XML may not contain the referenced text at
# all, or applying it produces invalid XML no matter how the candidate is
# refined. This is NOT a retry (which is a verified byte-exact alternative);
# it is the typed-recording-of-impossibility counterpart. The official row is
# SKIPPED at load time, exactly as for retry overlays — but no patch is
# emitted. Instead, a typed finding records *why* it cannot apply, so the
# residual ledger is honest about the resolution rather than reporting a
# perpetual ``miss``.
#
# Witness kinds (closed vocabulary for the ``evidence.kind`` field; same as
# the schema block at the top of ``corrigendum_unresolvable_fi.yaml``):
#   source_missing_base_text      — base text corrigendum references is absent
#                                    from the acquired source XML (acquisition
#                                    defect — attachment not loaded).
#   byte_anchor_absent            — wrong_text has no byte-exact occurrence in
#                                    source XML after manual/agentic search.
#   semantic_only                 — corrigendum's effect requires semantic
#                                    interpretation not expressible as a
#                                    byte-level patch.
#   ambiguous_anchor_unresolvable — multiple byte-exact occurrences and no
#                                    scoped location_desc can disambiguate.
#
# NOTE: ``xml_invalid_after_apply`` is intentionally NOT a witness kind.
# LawVM patch operators must never author patches that, applied to XML
# bytes, produce malformed XML. The 71 ``post_patch_xml_invalid`` misapplied
# records remaining in the current ledger are operator-extraction bugs that
# need a retry-overlay with a correct XML-serialization-level patch — never a
# "give up, it's unresolvable" verdict.
_UNRESOLVABLE_FIELDS = [
    "stable_id",
    "rule_id",
    "amendment_id",
    "source_pdf_witness",
    "correction_type",
    "evidence",
    "verified_at",
]

# Oracle-override records — typed manual adjudication of Finlex consolidated
# text where LawVM replay is right and the oracle is stale. The schema is
# documented in detail at the top of ``oracle_overrides_fi.yaml``.
# ``override_kind`` / ``source_witness.kind`` are open string fields today;
# promoting them to typed enums is a follow-up once the corpus of overrides
# is large enough that a closed set amplifies review work.
_ORACLE_OVERRIDE_FIELDS = [
    "rule_id",
    "statute_id",
    "target_address",
    "override_kind",
    "source_witness",
    "evidence_summary",
    "strict_mode",
    "verified_at",
]

_SOURCE_FIELDS = [
    "source_pdf",
    "pdf_name",
    "statute_id",
    "amendment_id",
    "lang",
    "date_published",
    "date_status",
    "correction_item_count",
    "sha256",
    "size_bytes",
]

JsonRow = dict[str, Any]


def default_official_records_path() -> Path:
    return _OFFICIAL_JSONL


def default_adjudication_records_path() -> Path:
    return _ADJUDICATIONS_JSONL


def default_source_records_path() -> Path:
    return _SOURCES_JSONL


def default_retry_overlays_path() -> Path:
    """Canonical upstream-corrigenda retry overlay records path."""
    return _RETRY_OVERLAYS_JSONL


def default_oracle_overrides_path() -> Path:
    """Canonical oracle-override records path.

    The carrier for surface (b): typed manual adjudication of Finlex
    consolidated text where the oracle is wrong in any way (not just
    stale-editorial — wrong section, missing amendment effect, omitted
    repeal, transcription error, editorial-convention-vs-legal-truth
    mismatch, etc.). Mutates the *projection* / comparison plane, never
    the source XML — see AGENTS.md §2.10 and the spec at the top of
    ``oracle_overrides_fi.yaml``.
    """
    return _ORACLE_OVERRIDES_YAML


def default_unresolvable_overrides_path() -> Path:
    """Canonical unresolvable-corrigendum records path.

    Per-``stable_id`` overlays declaring an upstream-corrigendum item
    genuinely cannot be applied mechanically (source missing attachment,
    byte anchor absent post-search, semantic-only effect, ambiguous
    anchor). The official row is skipped at load time; a typed finding
    records why. See ``corrigendum_unresolvable_fi.yaml`` for the schema.
    """
    return _UNRESOLVABLE_YAML


def default_patch_records_path() -> Path:
    return _OFFICIAL_JSONL


def _load_jsonl_records(path: Path) -> list[JsonRow]:
    records: list[JsonRow] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
    return records


def _stable_id(record: JsonRow) -> str:
    source_pdf = str(record.get("source_pdf") or "").strip()
    idx = int(record.get("correction_index") or 0)
    return f"{source_pdf}#{idx}"


def _statute_sort_key(statute_id: object) -> tuple[int, int]:
    """statute_id is always YEAR/NUM (e.g. '1999/132')."""
    value = str(statute_id or "").strip()
    if "/" not in value:
        return (9999, 999999)
    a, b = value.split("/", 1)
    if a.isdigit() and b.isdigit():
        return (int(a), int(b))
    return (9999, 999999)


def _amendment_sort_key(amendment_id: object) -> tuple[int, int]:
    """amendment_id is NUM/YEAR (e.g. '41/2013')."""
    value = str(amendment_id or "").strip()
    if "/" not in value:
        return (9999, 999999)
    a, b = value.split("/", 1)
    if a.isdigit() and b.isdigit() and len(b) == 4:
        return (int(b), int(a))
    if a.isdigit() and b.isdigit() and len(a) == 4:
        return (int(a), int(b))
    return (9999, 999999)


def _date_sort_key(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if not text:
        return (9999, 99, 99)
    parts = text.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        day, month, year = (int(part) for part in parts)
        return (year, month, day)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        y, m, d = text.split("-")
        if y.isdigit() and m.isdigit() and d.isdigit():
            return (int(y), int(m), int(d))
    return (9999, 99, 99)


def _official_sort_key(record: JsonRow) -> tuple[tuple[int, int], tuple[int, int, int], tuple[int, int], int, str]:
    # statute first — keeps all corrections for one statute contiguous in the file
    # then date — chronological audit trail within a statute
    # then amendment, index, pdf — deterministic tiebreaking
    return (
        _statute_sort_key(record.get("statute_id")),
        _date_sort_key(record.get("date_published")),
        _amendment_sort_key(record.get("amendment_id")),
        int(record.get("correction_index") or 0),
        str(record.get("source_pdf") or ""),
    )


def _source_sort_key(record: JsonRow) -> tuple[tuple[int, int, int], int, int, str]:
    date_published = _date_sort_key(record.get("date_published"))
    amendment_year, amendment_num = _amendment_sort_key(record.get("amendment_id"))
    return (
        date_published,
        amendment_year,
        amendment_num,
        str(record.get("source_pdf") or ""),
    )


def _merge_official_and_adjudications(
    official_records: list[JsonRow],
    adjudication_records: list[JsonRow],
) -> list[JsonRow]:
    adjudications_by_id = {
        str(row.get("stable_id") or ""): row for row in adjudication_records if row.get("stable_id")
    }
    combined: list[JsonRow] = []
    for official in official_records:
        stable_id = str(official.get("stable_id") or "")
        row = dict(official)
        row.update(adjudications_by_id.get(stable_id, {}))
        row["stable_id"] = stable_id
        combined.append(row)
    return combined


def load_official_records(path: Path | None = None) -> list[JsonRow]:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    if target.exists():
        records = _load_jsonl_records(target)
        for row in records:
            row["stable_id"] = str(row.get("stable_id") or _stable_id(row))
        return records
    return []


def load_adjudication_records(path: Path | None = None) -> list[JsonRow]:
    target = Path(path) if path is not None else _ADJUDICATIONS_JSONL
    if target.exists():
        records = _load_jsonl_records(target)
        for row in records:
            row["stable_id"] = str(row.get("stable_id") or "")
        return records
    return []


def load_source_records(path: Path | None = None) -> list[JsonRow]:
    target = Path(path) if path is not None else _SOURCES_JSONL
    if target.exists():
        return _load_jsonl_records(target)
    return []


def load_patch_records(path: Path | None = None) -> list[JsonRow]:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    if target.exists():
        if target.name == _ADJUDICATIONS_JSONL.name:
            official = load_official_records()
            adjudications = load_adjudication_records(target)
            return _merge_official_and_adjudications(official, adjudications)
        adjudications_path = target.with_name(_ADJUDICATIONS_JSONL.name)
        official = load_official_records(target)
        adjudications = load_adjudication_records(adjudications_path)
        return _merge_official_and_adjudications(official, adjudications)
    if path is None:
        official = load_official_records()
        adjudications = load_adjudication_records()
        if official:
            return _merge_official_and_adjudications(official, adjudications)
    return []


def write_official_records(records: list[JsonRow], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=_official_sort_key):
        row = {field: record.get(field) for field in _OFFICIAL_FIELDS}
        row["stable_id"] = str(record.get("stable_id") or _stable_id(record))
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def write_adjudication_records(records: list[JsonRow], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else _ADJUDICATIONS_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=lambda r: str(r.get("stable_id") or "")):
        row = {field: record.get(field) for field in _ADJUDICATION_FIELDS}
        row["stable_id"] = str(record.get("stable_id") or "")
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def load_retry_overlay_records(path: Path | None = None) -> list[JsonRow]:
    """Read raw retry-overlay dicts from ``_RETRY_OVERLAYS_JSONL``.

    The runtime loader ``lawvm.finland.corrigendum._load_retry_overlays``
    returns wrapped frozen dataclasses; this reader returns the raw dicts
    so tools that aggregate or rewrite the overlay file (e.g. ``reextract
    --update`` merging new candidates) operate on the canonical schema.
    """
    target = Path(path) if path is not None else _RETRY_OVERLAYS_JSONL
    if not target.exists():
        return []
    records: list[JsonRow] = []
    with target.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_retry_overlay_records(records: list[JsonRow], path: Path | None = None) -> Path:
    """Persist retry-overlay records, normalised to ``_RETRY_OVERLAY_FIELDS``.

    Stable-ordered by ``stable_id`` for deterministic diffs. Drops records
    without a stable_id or with an empty ``patches`` list (matches the
    load-time validation in ``_load_retry_overlays`` — surfaces a typed
    diagnostic instead of silently dropping on the read side).
    """
    target = Path(path) if path is not None else _RETRY_OVERLAYS_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=lambda r: str(r.get("stable_id") or "")):
        stable_id = str(record.get("stable_id") or "").strip()
        if not stable_id:
            continue
        patches = record.get("patches") or []
        if not patches:
            continue
        row = {field: record.get(field) for field in _RETRY_OVERLAY_FIELDS}
        row["stable_id"] = stable_id
        row["patches"] = list(patches)
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def write_source_records(records: list[JsonRow], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else _SOURCES_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=_source_sort_key):
        row = {field: record.get(field) for field in _SOURCE_FIELDS}
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def load_oracle_override_records(path: Path | None = None) -> list[JsonRow]:
    """Read oracle-override records from ``_ORACLE_OVERRIDES_YAML``.

    Returns ``[]`` when the file is missing or contains an empty list — the
    override layer is optional, and downstream code (oracle comparison /
    SourceAdjudication projection) must produce honest output with or
    without overrides in flight. Each record is validated minimally: records
    without a ``rule_id`` or ``statute_id`` are dropped, with the dropped
    count surfaced via a single ``[oracle_override_load]`` summary row
    appended to the returned list status (the caller surfaces a typed
    diagnostic; we don't silently trim).
    """
    target = Path(path) if path is not None else _ORACLE_OVERRIDES_YAML
    if not target.exists():
        return []
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"oracle_overrides_fi.yaml expected a YAML list, got {type(raw)!r}"
        )
    records: list[JsonRow] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_id = str(entry.get("rule_id") or "").strip()
        statute_id = str(entry.get("statute_id") or "").strip()
        if not rule_id or not statute_id:
            # Drop silently here — the loader is in the records module; a
            # typed diagnostic emission belongs to the runtime tool that
            # consumes overrides (oracle_check), not the data loader. The
            # count is preserved via the loop so callers can compare
            # len(raw) vs len(records) to detect drops.
            continue
        records.append(entry)
    return records


def write_oracle_override_records(records: list[JsonRow], path: Path | None = None) -> Path:
    """Persist oracle-override records, normalised to ``_ORACLE_OVERRIDE_FIELDS``.

    Stable-ordered by ``(statute_id, target_address, rule_id)`` for
    deterministic diffs across operator edits. The schema is intentionally
    open (``override_kind`` / ``source_witness.kind`` are strings today);
    promoting them to typed enums is a follow-up once a corpus large
    enough to amplify review work has accumulated.
    """
    target = Path(path) if path is not None else _ORACLE_OVERRIDES_YAML
    target.parent.mkdir(parents=True, exist_ok=True)

    def _sort_key(r: JsonRow) -> tuple[str, str, str]:
        return (
            str(r.get("statute_id") or ""),
            str(r.get("target_address") or ""),
            str(r.get("rule_id") or ""),
        )

    normalized = [
        {field: record.get(field) for field in _ORACLE_OVERRIDE_FIELDS}
        for record in sorted(records, key=_sort_key)
    ]
    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            normalized,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    return target


def load_unresolvable_overrides(path: Path | None = None) -> list[JsonRow]:
    """Read unresolvable-corrigendum overlay records.

    Returns ``[]`` when the file is missing or empty — the layer is optional.
    Records without a stable_id or evidence.kind are dropped (the loader is
    in the records module and stays lenient; the count is preserved through
    the loop).
    """
    target = Path(path) if path is not None else _UNRESOLVABLE_YAML
    if not target.exists():
        return []
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"corrigendum_unresolvable_fi.yaml expected a YAML list, got {type(raw)!r}"
        )
    records: list[JsonRow] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        stable_id = str(entry.get("stable_id") or "").strip()
        evidence = entry.get("evidence") or {}
        if not stable_id or not isinstance(evidence, dict) or not str(
            evidence.get("kind") or ""
        ).strip():
            continue
        records.append(entry)
    return records


def write_unresolvable_overrides(records: list[JsonRow], path: Path | None = None) -> Path:
    """Persist unresolvable-corrigendum overlay records.

    Stable-ordered by ``(amendment_id, stable_id, rule_id)`` for deterministic
    diffs across operator edits.
    """
    target = Path(path) if path is not None else _UNRESOLVABLE_YAML
    target.parent.mkdir(parents=True, exist_ok=True)

    def _sort_key(r: JsonRow) -> tuple[str, str, str]:
        return (
            str(r.get("amendment_id") or ""),
            str(r.get("stable_id") or ""),
            str(r.get("rule_id") or ""),
        )

    normalized = [
        {field: record.get(field) for field in _UNRESOLVABLE_FIELDS}
        for record in sorted(records, key=_sort_key)
    ]
    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            normalized,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    return target
