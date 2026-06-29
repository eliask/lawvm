"""One-shot migration: split corrigendum_manual.yaml into two typed carriers.

Background
----------
``data/finland/corrigendum_manual.yaml`` historically conflated two distinct
phenomena in one file:

1. **Upstream-corrigenda extraction retries** — a Finlex corrigendum PDF
   *exists* (sha256'd in ``corrigendum_sources_fi.jsonl``, classified into
   ``corrigendum_official_fi.jsonl``), but the LLM's extracted ``wrong_text``
   did not byte-match in the source XML (ellipsis, multi-block collapse, …).
   Humans/agentic LLMs produced verified byte-exact anchors instead.

2. **LawVM-authored source-defect patches** — NO upstream corrigendum exists.
   The base source XML itself is defective (OCR, glued § token, line-end
   hyphenation, wrong section eId, …) and LawVM authored a repair with a
   non-corrigendum witness.

These are different proof problems with different authority relationships
(AGENTS.md §0, §2.10). Conflating them produced:

- The dangerous "first entry REPLACES all DB-derived patches for amendment_id"
  semantics, which silently wipes legitimate sibling patches in the same
  amendment when ANY retry lands.
- No typed rule_id / family / witness fields — the discriminator
  ``no_official_corrigendum_found`` lives only in free-text ``notes``.

This script is the mechanical split — it preserves every ``wrong_text`` /
``correct_text`` byte verbatim (behavior-preserving at the patch-content
level) and emits two new files:

- ``data/finland/source_defect_fixes_fi.yaml`` — the 49 LawVM-authored
  source-defect entries, schema-identical to today's entries (additive typed
  fields landed in step 4).
- ``data/finland/corrigendum_retry_overlays_fi.jsonl`` — the 5
  upstream-corrigenda retries, restructured as per-``stable_id`` overlay
  records with a ``patches`` list (one overlay may emit multiple byte-exact
  patches against the same upstream corrigendum stable_id).

Idempotent: re-running with ``--in-place`` overwrites the outputs from a
fresh read of the source manual.yaml.

Run once:
    uv run python scripts/migrations/split_corrigendum_manual.py --in-place
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANUAL_YAML = _REPO_ROOT / "data" / "finland" / "corrigendum_manual.yaml"
_OFFICIAL_JSONL = _REPO_ROOT / "data" / "finland" / "corrigendum_official_fi.jsonl"
_SOURCES_JSONL = _REPO_ROOT / "data" / "finland" / "corrigendum_sources_fi.jsonl"

_SOURCE_DEFECT_YAML = _REPO_ROOT / "data" / "finland" / "source_defect_fixes_fi.yaml"
_RETRY_OVERLAYS_JSONL = (
    _REPO_ROOT / "data" / "finland" / "corrigendum_retry_overlays_fi.jsonl"
)

# Amendments whose manual.yaml entries are upstream-corrigenda retries —
# i.e. each has an actual sk*.pdf corrigendum in corrigendum_sources_fi.jsonl
# and at least one classified row in corrigendum_official_fi.jsonl.
# Every other amendment in manual.yaml is a LawVM-authored source-defect fix
# (citing "no_official_corrigendum_found" in its notes, and absent from
# both upstream files).
_UPSTREAM_CORRIGENDUM_AMENDMENTS = {"991/2012", "442/2016", "541/2018"}


# ---------------------------------------------------------------------------
# Source-defect family classifier (Step 4 + Step 5 — typed ownership).
#
# Each family carries a stable ``rule_id`` and a ``source_witness`` typed dict.
# Per AGENTS.md §2.1, families are LAND labels (not a heavy taxonomy); per
# §2.10, the witness does NOT lean on the consolidated Finlex oracle as truth
# where an intrinsic witness is defensible.
#
# Witness kinds:
#   typographic_impossibility
#     The defect is intrinsically ill-formed (e.g. a glued § token where the
#     Finlex drafting grammar never permits it). No oracle citation needed;
#     the witness is the drafting grammar itself.
#
#   ordinal_structure
#     The section/chapter label is wrong; the section's position in its
#     chapter sequence + downstream references intrinsic-confirm the true
#     ordinal. No oracle citation needed; the witness is the statute's
#     own ordinal structure.
#
#   multi_acquisition_corroboration
#     The defect is a substantive text defect (OCR substitution, line-end
#     hyphenation, ASCII hyphen where en-dash is required). Multiple
#     independent acquisitions agree on the correct text; the corroborators
#     are listed as a list (today one — the cached consolidated XML — but
#     the structure admits more as additional acquisition lanes come online).
#
#   finlex_publication_backfill
#     The base source XML is missing structural content (e.g. an empty
#     section shell or missing table rows). A consolidated witness tagged
#     with finlex:vsId or sec_eId linkage corroborates the missing content.
#     Treated as corroboration, not as oracle-as-truth, because the
#     consolidated witness is used to find the missing piece whose source
#     attribution is to the base act — not to assert that the consolidated
#     text is the legal truth.
# ---------------------------------------------------------------------------

_FAMILY_RULES = {
    "glued_pykala_token": (
        "FINLAND.SOURCE_DEFECT.GLUED_PYKALA_TOKEN",
        "presentation_cleanup",
        "typographic_impossibility",
    ),
    "glued_period_token": (
        "FINLAND.SOURCE_DEFECT.GLUED_PERIOD_TOKEN",
        "presentation_cleanup",
        "typographic_impossibility",
    ),
    "dittographic_duplicate": (
        "FINLAND.SOURCE_DEFECT.DITTOGRAPHIC_DUPLICATE",
        "presentation_cleanup",
        "typographic_impossibility",
    ),
    "missing_compound_hyphen": (
        "FINLAND.SOURCE_DEFECT.MISSING_COMPOUND_HYPHEN",
        "source_pathology_ocr",
        "multi_acquisition_corroboration",
    ),
    "ocr_char_substitution": (
        "FINLAND.SOURCE_DEFECT.OCR_CHAR_SUBSTITUTION",
        "source_pathology_ocr",
        "multi_acquisition_corroboration",
    ),
    "line_end_hyphenation": (
        "FINLAND.SOURCE_DEFECT.LINE_END_HYPHENATION",
        "presentation_cleanup",
        "multi_acquisition_corroboration",
    ),
    "ascii_hyphen_for_range_dash": (
        "FINLAND.SOURCE_DEFECT.ASCII_HYPHEN_FOR_RANGE_DASH",
        "presentation_cleanup",
        "multi_acquisition_corroboration",
    ),
    "escaped_transport_marker": (
        "FINLAND.SOURCE_DEFECT.ESCAPED_TRANSPORT_MARKER",
        "presentation_cleanup",
        "multi_acquisition_corroboration",
    ),
    "missing_table_rows": (
        "FINLAND.SOURCE_DEFECT.MISSING_TABLE_ROWS",
        "table_completeness",
        "finlex_publication_backfill",
    ),
    "section_eid_mislabel": (
        "FINLAND.SOURCE_DEFECT.SECTION_EID_MISLABEL",
        "structural_label_mislabel",
        "ordinal_structure",
    ),
    "empty_section_shell": (
        "FINLAND.SOURCE_DEFECT.EMPTY_SECTION_SHELL",
        "structural_label_mislabel",
        "finlex_publication_backfill",
    ),
    "other_source_defect": (
        "FINLAND.SOURCE_DEFECT.UNCATEGORIZED",
        "source_pathology",
        "multi_acquisition_corroboration",
    ),
}


def _classify_source_defect(notes: str) -> tuple[str, str, str]:
    """Map a notes string to (rule_id, family, witness_kind).

    The notes are the legacy discriminator; for typed ownership each mapped
    entry carries a stable ``rule_id``, a family label, and a witness
    ``kind``. This is the §2.1 + §2.10 discipline that the legacy free-text
    discriminator was missing.
    """
    nl = notes.lower() if notes else ""
    if "glued pykälä" in nl or "glued pykala" in nl:
        return _FAMILY_RULES["glued_pykala_token"]
    if "glued period" in nl or "'uusi.3'" in nl or "uusi.3" in nl:
        return _FAMILY_RULES["glued_period_token"]
    if "dittographic" in nl:
        return _FAMILY_RULES["dittographic_duplicate"]
    if "missing compound hyphen" in nl or "compound hyphen" in nl:
        return _FAMILY_RULES["missing_compound_hyphen"]
    if "line-end hyphenation" in nl or "broken line-end" in nl:
        return _FAMILY_RULES["line_end_hyphenation"]
    if "ascii hyphen" in nl or "ascii hyphen in" in nl:
        return _FAMILY_RULES["ascii_hyphen_for_range_dash"]
    if "escaped" in nl and ("transport" in nl or "stray marker" in nl or "&gt;" in notes or "&lt;" in notes):
        return _FAMILY_RULES["escaped_transport_marker"]
    if "missing" in nl and "table" in nl and "row" in nl:
        return _FAMILY_RULES["missing_table_rows"]
    if "empty section" in nl or "empty §" in nl or "shell" in nl:
        return _FAMILY_RULES["empty_section_shell"]
    if (
        "labels" in nl
        or "eId" in nl
        or "structural identity" in nl
        or "chp_" in nl
        or "sec_" in notes
    ):
        return _FAMILY_RULES["section_eid_mislabel"]
    if "ocr" in nl or "cached consolidated" in nl or "consolidated finlex" in nl:
        return _FAMILY_RULES["ocr_char_substitution"]
    return _FAMILY_RULES["other_source_defect"]


def _corroborator_locator(notes: str) -> str:
    """Extract the cached consolidated XML locator (e.g. fin@20080137) cited
    in the legacy notes, if present.

    Used as the corroborator for ``multi_acquisition_corroboration`` /
    ``finlex_publication_backfill`` witnesses. Carried forward under a
    field that admits a list (so future acquisition lanes append; today
    one corroborator each).
    """
    import re as _re
    m = _re.search(r"fin@\d+", notes or "")
    return m.group(0) if m else ""


def _build_source_defect_entries(manual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate the (c) entries with typed family/``rule_id``/
    ``source_witness`` fields per family.

    Field shape (additive over the legacy schema — the loader reads these
    via dict.get with sane defaults; tests stay green because every field is
    optional-level metadata, not load-bearing for apply):

      rule_id: e.g. ``FINLAND.SOURCE_DEFECT.GLUED_PYKALA_TOKEN``
      family: e.g. ``presentation_cleanup`` (one of the AGENTS §2.1
              family tags where categorisation is established)
      source_witness:
        kind: typographic_impossibility | ordinal_structure |
              multi_acquisition_corroboration | finlex_publication_backfill
        detail: family-specific human-readable summary
        corroborators: list[locator str] — present when kind=~corroboration;
                       empty for intrinsic witnesses (typographic / ordinal)
    """
    out: list[dict[str, Any]] = []
    for entry in manual:
        aid = _v(entry.get("amendment_id"))
        if aid in _UPSTREAM_CORRIGENDUM_AMENDMENTS:
            continue
        notes = _v(entry.get("notes"))
        rule_id, family, witness_kind = _classify_source_defect(notes)
        witness: dict[str, Any] = {"kind": witness_kind}
        if witness_kind in ("multi_acquisition_corroboration", "finlex_publication_backfill"):
            corroborator = _corroborator_locator(notes)
            witness["corroborators"] = [corroborator] if corroborator else []
            witness["detail"] = (
                "Cached consolidated Finlex acquisition corroborates the corrected text. "
                "This is one corroborator — the structure admits a list; additional "
                "acquisition lanes (Finlex HTML, alternative manifestations) append here."
            )
        elif witness_kind == "ordinal_structure":
            witness["detail"] = (
                "The statute's own ordinal structure (chapter sequence + downstream "
                "references) intrinsic-confirms the corrected label. No oracle citation."
            )
        elif witness_kind == "typographic_impossibility":
            witness["detail"] = (
                "The Finlex drafting grammar never permits the glued form; the source "
                "XML artifact is intrinsically ill-formed at this token."
            )
        out.append(
            {
                "amendment_id": aid,
                "rule_id": rule_id,
                "family": family,
                "source_witness": witness,
                "wrong_text": entry.get("wrong_text", ""),
                "correct_text": entry.get("correct_text", ""),
                "correction_type": _v(entry.get("correction_type")) or "johtolause",
                "notes": notes,
                "verified": _v(entry.get("verified")),
                # Default cardinality: each entry targets exactly one byte span.
                # N>1 patches opt in explicitly when added.
                "expected_apply_count": 1,
            }
        )
    return out


def _load_official_rows_by_amendment() -> dict[str, list[dict[str, Any]]]:
    """Index corrigendum_official_fi.jsonl by NUM/YEAR amendment_id."""
    by_amendment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with _OFFICIAL_JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            aid = str(row.get("amendment_id") or "").strip()
            if aid:
                by_amendment[aid].append(row)
    return by_amendment


def _stable_id_for_retry(
    amendment_id: str,
    official_rows: list[dict[str, Any]],
    manual_correction_type: str,
) -> str:
    """Pick the official-row stable_id that this retry-overlay should overlay.

    Heuristic: prefer the official row whose ``correction_type`` matches the
    manual entry's routing (``johtolause`` → johtolause row; ``body_text``
    → prose/footnote/metadata row). Falls back to the first official row
    for the amendment.
    """
    body_types = {"prose", "footnote", "metadata", "sami_translation"}
    wants_body = manual_correction_type in body_types | {"body_text"}
    candidates = [
        r
        for r in official_rows
        if (str(r.get("correction_type") or "").strip() in body_types) == wants_body
    ]
    if not candidates:
        candidates = list(official_rows)
    return str(candidates[0].get("stable_id") or "").strip()


def _v(yaml_value: Any) -> str:
    """Coerce a YAML value to a stripped string, preserving None as empty."""
    if yaml_value is None:
        return ""
    return str(yaml_value).strip()


def _build_retry_overlay_records(
    manual: list[dict[str, Any]],
    official_rows_by_amendment: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Restructure the 5 (a) entries into per-stable_id overlay records.

    One overlay per amendment_id, carrying a ``patches`` list of byte-exact
    ``(wrong_text, correct_text)`` pairs that together realise the upstream
    corrigendum effect. The overlay *targets* one official row's stable_id;
    at load time, that row's auto-extracted wrong_text/correct_text is
    skipped and the overlay patches are emitted in its place.
    """
    by_amendment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in manual:
        aid = _v(entry.get("amendment_id"))
        if aid not in _UPSTREAM_CORRIGENDUM_AMENDMENTS:
            continue
        by_amendment[aid].append(entry)

    overlays: list[dict[str, Any]] = []
    for aid, entries in by_amendment.items():
        official_rows = official_rows_by_amendment.get(aid, [])
        if not official_rows:
            print(
                f"WARN: amendment {aid} has retry entries but no upstream "
                f"corrigendum rows in corrigendum_official_fi.jsonl — skipping",
                file=sys.stderr,
            )
            continue
        # All manual entries for one amendment share the same routing today;
        # use the first entry's correction_type to pick the overlay target.
        first_corr_type = _v(entries[0].get("correction_type")) or "johtolause"
        stable_id = _stable_id_for_retry(aid, official_rows, first_corr_type)
        if not stable_id:
            print(
                f"WARN: amendment {aid} retry overlay could not resolve a "
                f"target stable_id — skipping",
                file=sys.stderr,
            )
            continue
        # Derive the source-PDF witness from the targeted official row.
        source_pdf_witness = ""
        for r in official_rows:
            if str(r.get("stable_id") or "") == stable_id:
                source_pdf_witness = _v(r.get("source_pdf"))
                break
        # Aggregate verified_at: take the latest verified string across
        # the entries (they were all verified together per the URL comments).
        verified_at = max((_v(e.get("verified")) for e in entries), default="")
        # The manual entries' correction_type drives routing (johtolause vs
        # body_text). Preserve it under correction_type so the loader routes
        # the overlay patches the same way it would route the manual entries.
        overlay = {
            "stable_id": stable_id,
            "rule_id": "FINLAND.CORR.EXTRACTION_RETRY",
            "family": _infer_retry_family(entries),
            "amendment_id": aid,
            "source_pdf_witness": source_pdf_witness,
            "correction_type": first_corr_type,
            "span_verified": True,
            "verified_at": verified_at,
            "patches": [
                {
                    "wrong_text": e.get("wrong_text", ""),
                    "correct_text": e.get("correct_text", ""),
                }
                for e in entries
            ],
        }
        overlays.append(overlay)
    return overlays


def _infer_retry_family(entries: list[dict[str, Any]]) -> str:
    """Best-effort label for the retry family.

    The family is a label only (per AGENTS.md §2.1 — not a heavy taxonomy
    at this step); the discriminator lives in the LLM-extraction-failure
    shape. Common shapes seen in the existing 5 retries:
      - LLM collapsed multi-block text with ellipsis "..."
      - SD body truncated vs full consolidated text
    """
    notes_blob = " ".join(_v(e.get("notes")) for e in entries).lower()
    if "ellipsis" in notes_blob or "collapsed" in notes_blob:
        return "llm_ellipsis_span"
    if "truncated" in notes_blob:
        return "truncated_body"
    return "extraction_retry"


class _MultilineStrDumper(yaml.SafeDumper):
    """YAML dumper that emits multi-line strings as ``|-`` block scalars.

    Preserves human readability for the XML-fragment ``wrong_text`` entries
    (e.g. 91/1982 sec_131 → sec_1 relabel) in diff review.
    """


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_MultilineStrDumper.add_representer(str, _str_representer)


def _dump_source_defect_yaml(entries: list[dict[str, Any]], path: Path) -> None:
    """Write the source-defect YAML with a top-of-file doctrine header."""
    header = (
        "# LawVM-authored source-defect patches (no upstream corrigendum).\n"
        "#\n"
        "# Each entry repairs a defect in the BASE source XML itself — OCR\n"
        "# substitutions, glued tokens, line-end hyphenation, wrong section\n"
        "# eIds — where NO official Finlex corrigendum exists. The witness\n"
        "# for each is a typed source_witness (kind: typographic_impossibility |\n"
        "# ordinal_structure | multi_acquisition_corroboration |\n"
        "# finlex_publication_backfill); the consolidated Finlex oracle is NEVER\n"
        "# used as truth where an intrinsic witness is defensible — that is the\n"
        "# AGENTS.md §2.10 plane-collapse smell this typed carrier closes.\n"
        "#\n"
        "# This file REPLACES legacy corrigendum_manual.yaml for source-defect\n"
        "# patches. Upstream-corrigenda extraction retries live in\n"
        "# corrigendum_retry_overlays_fi.jsonl as per-stable_id overlays.\n"
        "# See notes/manual_claims/MANUAL_COMPILATION_CLAIMS.md and\n"
        "# AGENTS.md §0 (no silent mutation) / §2.1 (rule family ownership) /\n"
        "# §2.10 (planes stay type-distinct).\n"
        "#\n"
        "# Schema:\n"
        "#   - amendment_id: \"NUM/YEAR\"        (e.g. 41/1965)\n"
        "#     rule_id:    FINLAND.SOURCE_DEFECT.* stable identifier\n"
        "#     family:     presentation_cleanup | source_pathology_ocr |\n"
        "#                 structural_label_mislabel | table_completeness |\n"
        "#                 source_pathology       (label only — AGENTS §2.1)\n"
        "#     source_witness:\n"
        "#       kind: typographic_impossibility | ordinal_structure |\n"
        "#            multi_acquisition_corroboration |\n"
        "#            finlex_publication_backfill\n"
        "#       detail: <family-specific reason summary>\n"
        "#       corroborators: [<locator>, ...]   # only for corroboration kinds\n"
        "#     wrong_text:    exact bytes in source XML\n"
        "#     correct_text:  corrected bytes\n"
        "#     correction_type: johtolause | body_text   (routing flag)\n"
        "#     expected_apply_count: N        # default 1; ≥1 expected byte-spans\n"
        "#     notes:                          human-readable explanation\n"
        "#     verified: \"YYYY-MM-DD\"\n\n"
    )
    body = yaml.dump(
        entries,
        Dumper=_MultilineStrDumper,
        allow_unicode=True,
        sort_keys=False,
        width=120,
        default_flow_style=False,
    )
    path.write_text(header + body, encoding="utf-8")


def _dump_retry_overlays_jsonl(overlays: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in overlays:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="write to the canonical data/finland/ paths (otherwise dry-run to stdout)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="write both outputs under this directory (overrides --in-place for testing)",
    )
    args = parser.parse_args(argv)

    if not _MANUAL_YAML.exists():
        print(f"ERROR: source manual.yaml not found at {_MANUAL_YAML}", file=sys.stderr)
        return 2

    manual = yaml.safe_load(_MANUAL_YAML.read_text(encoding="utf-8")) or []
    if not isinstance(manual, list):
        print(f"ERROR: expected top-level YAML list, got {type(manual)!r}", file=sys.stderr)
        return 2

    official_rows_by_amendment = _load_official_rows_by_amendment()

    source_defect_entries = _build_source_defect_entries(manual)
    retry_overlays = _build_retry_overlay_records(manual, official_rows_by_amendment)

    # Sanity: every manual entry migrated.
    migrated = len(source_defect_entries) + sum(len(o["patches"]) for o in retry_overlays)
    if migrated != len(manual):
        print(
            f"ERROR: migration count mismatch — manual has {len(manual)} entries, "
            f"migrated {migrated} ({len(source_defect_entries)} source-defect + "
            f"{sum(len(o['patches']) for o in retry_overlays)} retry-overlay patches)",
            file=sys.stderr,
        )
        return 3

    if args.out_dir is not None:
        out_dir = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        _dump_source_defect_yaml(source_defect_entries, out_dir / _SOURCE_DEFECT_YAML.name)
        _dump_retry_overlays_jsonl(retry_overlays, out_dir / _RETRY_OVERLAYS_JSONL.name)
    elif args.in_place:
        _dump_source_defect_yaml(source_defect_entries, _SOURCE_DEFECT_YAML)
        _dump_retry_overlays_jsonl(retry_overlays, _RETRY_OVERLAYS_JSONL)
    else:
        print("=== source_defect_fixes_fi.yaml (dry run) ===")
        out_path = Path("/tmp/_source_defect_fixes_fi.yaml")
        _dump_source_defect_yaml(source_defect_entries, out_path)
        sys.stdout.write(out_path.read_text(encoding="utf-8"))
        print("\n=== corrigendum_retry_overlays_fi.jsonl (dry run) ===")
        for record in retry_overlays:
            sys.stdout.write(json.dumps(record, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")

    print(
        f"migrated: {len(source_defect_entries)} source-defect entries + "
        f"{len(retry_overlays)} retry overlays "
        f"({sum(len(o['patches']) for o in retry_overlays)} patches total) "
        f"out of {len(manual)} manual.yaml entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())