#!/usr/bin/env -S uv run python
"""Reproducible build for the pack-native relation-graph + transclusion viewer.

Produces, under ``viewer/data/`` (gitignored — only this script, the manifests,
and the viewer source are tracked), the two demo packs that make the
"hypercodex of law" visible end to end:

1. ``fi-1050-2018`` — the Finnish data-protection act **tietosuojalaki**
   (engine id ``2018/1050``), packed with ``lawvm pack-work``. The FI exporter
   already emits an ``edges/`` layer of ``lawvm.legal_relation_edge.v0`` rows
   for the act's body cross-references — including its 84 GDPR article cites,
   but as **opaque** ``celex:32016R0679/<art>[/<kohta>]`` targets.

2. ``eu-gdpr`` — the EU **General Data Protection Regulation** (CELEX
   ``32016R0679``) ingested from its consolidated Formex into an addressable
   substrate work whose article/paragraph nodes carry stable
   ``entity:celex:32016R0679#006.001`` ids — the cross-work transclusion target.

**Resolution wiring (the judgment call).** ``pack-work`` does NOT resolve the
opaque ``celex:`` targets into GDPR entity nodes — that resolution lives only in
the ``eu_ingest`` e2e path (``resolve_fi_eu_edge``), never in the exporter. Per
the build brief we do the resolution **here in the build script** rather than
touching the shared exporter, and we keep the canonical ``pack-work`` pack
byte-for-byte intact (so ``check-pack`` stays VALID). The resolved edges + the
anchor metadata the viewer needs (which provision each citation surface lives
in, and the surface text) are written as a **viewer sidecar** next to the pack:

* ``fi-1050-2018/edges-resolved.jsonl`` — the same edge rows the exporter emits,
  but with every fully-resolvable ``celex:`` target rewritten to its GDPR
  ``entity:`` node id (``status=resolved``, ``registry_resolved`` on the
  ``surface`` plane — matrix-legal, asserted by ``resolve_fi_eu_edge``). Opaque
  targets that do not resolve are kept verbatim (no fabrication).
* ``fi-1050-2018/edge-anchors.json`` — ``{edge_id: {address, surface_text,
  byte_offset, byte_len}}`` so the viewer can paint each edge as an interlink at
  the exact provision it is cited from.

The sidecar is NOT part of the certified pack; it is a presentation overlay the
viewer reads in addition to the certified layers. The certified pack and its
manifest are untouched, so the ``check-pack`` verdict is over the engine's own
output, not over anything this script synthesised.

Run::

    viewer/build-graph-demo.py            # build both packs + sidecar + verify

Idempotent; rebuilds in place. Wrap under systemd-run for the memory cap when
running the heavy FI replay (the script itself does not, so the caller controls
the cap)::

    systemd-run --user --scope -p MemoryMax=18G -p MemorySwapMax=0 \
        viewer/build-graph-demo.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "data"

FI_WORK_ENGINE_ID = "2018/1050"  # year-major; tietosuojalaki 1050/2018
FI_PACK = DATA / "fi-1050-2018"
EU_PACK = DATA / "eu-gdpr"
# A heavily-amended statute for the TIME lens (Ulkomaalaislaki, ~93 change
# dates / ~2400 transitions). No EU transclusion — a pure point-in-time demo.
FI_TIME_WORK_ENGINE_ID = "2004/301"
FI_TIME_PACK = DATA / "fi-301-2004"
GDPR_CELEX = "32016R0679"
GDPR_FORMEX = REPO / ".tmp" / "eulex" / "gdpr_fi_formex_plain.xml"
# The acquired Formex lives in the AUTH checkout's .tmp (not symlinked into a
# fresh worktree); fall back to the canonical path when the local one is absent.
GDPR_FORMEX_FALLBACK = Path(
    "<DATA_ROOT>/.tmp/eulex/gdpr_fi_formex_plain.xml"
)


def _log(msg: str) -> None:
    print(f"[build-graph-demo] {msg}", flush=True)


def _run_lawvm(args: list[str]) -> None:
    cmd = [sys.executable, "-m", "lawvm.tools.cli", *args]
    _log("$ lawvm " + " ".join(args))
    subprocess.run(cmd, check=True, cwd=REPO)


def _formex_path() -> Path:
    if GDPR_FORMEX.exists():
        return GDPR_FORMEX
    if GDPR_FORMEX_FALLBACK.exists():
        return GDPR_FORMEX_FALLBACK
    raise SystemExit(
        f"GDPR Formex not found at {GDPR_FORMEX} or {GDPR_FORMEX_FALLBACK}; "
        "acquire it first (eu_ingest e2e prerequisite)."
    )


# --------------------------------------------------------------------------- #
# Step 1 — pack the Finnish act (engine replay → certified pack)              #
# --------------------------------------------------------------------------- #


def build_fi_pack() -> None:
    _log(f"packing FI work {FI_WORK_ENGINE_ID} -> {FI_PACK}")
    _run_lawvm(["-j", "fi", "pack-work", FI_WORK_ENGINE_ID, "--out", str(FI_PACK)])


def build_fi_time_pack() -> None:
    """Pack the heavily-amended Ulkomaalaislaki for the time lens."""
    _log(f"packing FI time-lens work {FI_TIME_WORK_ENGINE_ID} -> {FI_TIME_PACK}")
    _run_lawvm(
        ["-j", "fi", "pack-work", FI_TIME_WORK_ENGINE_ID, "--out", str(FI_TIME_PACK)]
    )


# --------------------------------------------------------------------------- #
# Step 2 — ingest GDPR into an addressable substrate work                     #
# --------------------------------------------------------------------------- #


def build_eu_pack() -> object:
    """Ingest the GDPR consolidated Formex; return the IngestedEuWork index."""
    from lawvm.substrate.eu_ingest import export_eu_regulation_pack

    formex = _formex_path()
    _log(f"ingesting GDPR ({GDPR_CELEX}) from {formex} -> {EU_PACK}")
    res, work = export_eu_regulation_pack(
        formex,
        celex=GDPR_CELEX,
        out_dir=EU_PACK,
        title="Yleinen tietosuoja-asetus (GDPR)",
        # Pin created_at so the pack_id is reproducible across rebuilds.
        created_at="2026-06-22T00:00:00+00:00",
    )
    _log(
        f"GDPR pack: {res.n_articles} articles, {res.n_parags} paragraphs, "
        f"{res.n_divisions} divisions; pack_id {res.pack_id}"
    )
    return work


# --------------------------------------------------------------------------- #
# Step 3 — resolve the FI->GDPR edges + emit the viewer sidecar               #
# --------------------------------------------------------------------------- #


def _corpus_version() -> str:
    """The FI pack's corpus_version (the edge layer's resolution scope key)."""
    manifest_row = json.loads((FI_PACK / "manifest.json").read_text(encoding="utf-8"))
    body = manifest_row.get("object", manifest_row)
    return str(body["corpus_version"])


def _norm(s: str) -> str:
    """NFC + NBSP-flatten so a surface phrase matches the rendered provision text
    regardless of the non-breaking spaces the source uses between number+word.
    """
    import unicodedata

    return unicodedata.normalize("NFC", s).replace("\xa0", " ")


def _provision_text_index() -> list[tuple[str, str]]:
    """``[(address_path, normalised_text)]`` for every addressable provision.

    The citation surface ("6 artiklan 1 kohdan a alakohdassa") is verbatim text
    of the provision it is written in, so the most reliable anchor is the
    provision whose content_leaf text CONTAINS the surface phrase — far more
    precise than the work-level ``source_provision_ref`` the EU extractor emits.
    """
    base = [
        json.loads(line).get("object", {})
        for line in (FI_PACK / "base" / "base.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    state = [
        json.loads(line).get("object", {})
        for line in (FI_PACK / "state" / "state.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    leaves = {
        o["content_leaf_hash"]: o.get("text", "")
        for o in base
        if o.get("schema") == "lawvm.content_leaf.v1"
    }
    addr_by_id = {
        o["struct_node_id"]: o["address_path"]
        for o in base
        if o.get("schema") == "lawvm.address_node.v1"
    }
    index: list[tuple[str, str]] = []
    for o in state:
        if o.get("schema") != "lawvm.applicability_fact.v1":
            continue
        addr = addr_by_id.get(o.get("address_id"))
        if not addr:
            continue
        index.append((addr, _norm(leaves.get(o.get("content_leaf_hash"), ""))))
    return index


def _anchor_address(surface_text: str, text_index: list[tuple[str, str]]) -> str | None:
    """Anchor a citation surface to the provision whose text contains it."""
    needle = _norm(surface_text).strip()
    if len(needle) < 3:
        return None
    for addr, text in text_index:
        if needle and needle in text:
            return addr
    return None


def build_sidecar(work: object) -> dict[str, int]:
    """Re-derive the folded FI edges WITH anchor metadata, resolve their GDPR
    targets against the ingested work, and write the viewer sidecar files.
    """
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.finland.ref_mention_extractor import extract_all_reference_mentions
    from lawvm.finland.references.reference_sets import fold_reference_set
    from lawvm.substrate.canonical_json import wrap_row
    from lawvm.substrate.eu_ingest import resolve_fi_eu_edge
    from lawvm.substrate.relation_edge_bridge import reference_set_to_relation_edge

    cv = _corpus_version()
    text_index = _provision_text_index()

    store = get_corpus_store()
    xml_bytes = store.read_oracle(FI_WORK_ENGINE_ID)
    result = extract_all_reference_mentions(xml_bytes, FI_WORK_ENGINE_ID)

    # Group flattened mentions by their written surface, exactly as the exporter
    # does (one range/coordination folds into ONE set / ONE edge).
    groups: dict[tuple[str, object], list[object]] = {}
    order: list[tuple[str, object]] = []
    for idx, mention in enumerate(result.mentions):
        span = mention.source_span
        if mention.surface_text and span is not None:
            key: tuple[str, object] = (
                mention.surface_text,
                f"{span.source_file}:{span.byte_offset}:{span.byte_len}",
            )
        else:
            key = ("\x00solo", idx)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(mention)

    resolved_rows: list[dict[str, object]] = []
    anchors: dict[str, dict[str, object]] = {}
    n_celex = n_resolved = 0

    for key in order:
        members = groups[key]
        folded = fold_reference_set(members, corpus_version=cv, branch="actual")
        edge = reference_set_to_relation_edge(
            expression=folded.expression,
            resolution=folded.resolution,
            corpus_version=cv,
            branch_id="actual",
        )
        is_celex = any(str(t).startswith("celex:") for t in edge["target_set"])
        if is_celex:
            n_celex += 1
        er = resolve_fi_eu_edge(edge, work, corpus_version=cv)  # type: ignore[arg-type]
        out_edge = er.edge
        if er.rewritten:
            n_resolved += 1

        # Anchor metadata: the provision whose rendered text contains the
        # citation surface (precise), falling back to the work-level
        # source_provision_ref the extractor carries.
        src = members[0].source_provision_ref
        addr = _anchor_address(members[0].surface_text or "", text_index)
        span = members[0].source_span
        anchors[str(out_edge["edge_id"])] = {
            "address": addr,
            "surface_text": members[0].surface_text or "",
            "source_provision": src.serialized() if src is not None else "",
            "byte_offset": span.byte_offset if span is not None else None,
            "byte_len": span.byte_len if span is not None else None,
        }
        resolved_rows.append(wrap_row(out_edge))

    # Write the resolved-edges JSONL (NDJSON of {object_hash, object}).
    resolved_path = FI_PACK / "edges-resolved.jsonl"
    with resolved_path.open("w", encoding="utf-8") as fh:
        for row in resolved_rows:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    (FI_PACK / "edge-anchors.json").write_text(
        json.dumps(anchors, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    n_anchored = sum(1 for a in anchors.values() if a["address"])
    _log(
        f"sidecar: {len(resolved_rows)} edges ({n_celex} celex, {n_resolved} "
        f"resolved to GDPR entity nodes); {n_anchored} anchored to a provision"
    )
    return {
        "edges": len(resolved_rows),
        "celex": n_celex,
        "resolved": n_resolved,
        "anchored": n_anchored,
    }


# --------------------------------------------------------------------------- #
# Step 4 — verify both packs                                                   #
# --------------------------------------------------------------------------- #


def verify_packs() -> None:
    for pack in (FI_PACK, EU_PACK, FI_TIME_PACK):
        _log(f"check-pack {pack}")
        _run_lawvm(["check-pack", str(pack)])


# --------------------------------------------------------------------------- #
# Step 5 — write the viewer manifest                                           #
# --------------------------------------------------------------------------- #


def write_manifest(stats: dict[str, int]) -> None:
    manifest = [
        {
            "pack_id": "fi-1050-2018",
            "title": "Tietosuojalaki (1050/2018)",
            "subtitle": "Suomen kansallinen tietosuojalaki",
            "lang": "fi",
            "jurisdiction": "fi",
            "pack": "data/fi-1050-2018",
            "resolved_edges": "data/fi-1050-2018/edges-resolved.jsonl",
            "anchors": "data/fi-1050-2018/edge-anchors.json",
            "transclude_packs": {"32016R0679": "data/eu-gdpr"},
            "edge_count": stats["edges"],
            "gdpr_resolved_count": stats["resolved"],
        },
        {
            "pack_id": "fi-301-2004",
            "title": "Ulkomaalaislaki (301/2004)",
            "subtitle": "Vahvasti muutettu — aikalinssin demo",
            "lang": "fi",
            "jurisdiction": "fi",
            "pack": "data/fi-301-2004",
        },
    ]
    (HERE / "law-graph-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _log("wrote law-graph-manifest.json")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    build_fi_pack()
    build_fi_time_pack()
    work = build_eu_pack()
    stats = build_sidecar(work)
    verify_packs()
    write_manifest(stats)
    _log("done.")


if __name__ == "__main__":
    main()
