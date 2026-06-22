"""Pins for the trustless pack checker (CHECKER_CONTRACT_V0.md §2 L0, §3 L1, §6).

Strategy: hand-construct a tiny synthetic-but-conforming pack (wrapped rows +
a ``PackManifest``) whose roots are computed with the P0 kernel so the GOOD pack
verifies ``VALID_CLEAN``, then corrupt one cell per fire drill and assert the
exact top-line verdict + that the violation appears in ``violations[]`` — driven
through the production ``check`` path (guard-liveness, memory
``feedback_witness_must_reach_production_consumer``).
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Mapping
from typing import cast

from lawvm.substrate.canonical_json import JsonValue, semantic_hash, wrap_row
from lawvm.substrate.checker import (
    CertificationVerdict,
    CheckLevel,
    CheckMode,
    Checker,
    IntegrityVerdict,
    Pack,
    PackLayerData,
    SourceAvailability,
    TopLineVerdict,
    ViolationCode,
    check_pack,
    fold_top_line,
)
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.roots import map_root, set_root

# --------------------------------------------------------------------------- #
# Fixture builders — a minimal conforming pack.
# --------------------------------------------------------------------------- #

_KNOWN_SCHEMAS = frozenset(
    {
        "lawvm.content_leaf.v1",
        "lawvm.node_version.v1",
        "lawvm.applicability_fact.v1",
        "lawvm.selection_row.v1",
        "lawvm.selection_candidate_set.v1",
        "lawvm.scope_predicate.v1",
        "lawvm.residual.v1",
    }
)


def _content_leaf(text: str) -> dict[str, JsonValue]:
    """A PURE text-only content leaf (body = {schema, text, content_leaf_hash}).

    Mirrors the exporter: ``content_leaf_hash`` is the text-only identity and the
    leaf carries NO per-work member (design §22.1), so the checker's L0.7
    accepts it.
    """
    from lawvm.substrate.roots import leaf_hash

    body: dict[str, JsonValue] = {"schema": "lawvm.content_leaf.v1", "text": text}
    body["content_leaf_hash"] = leaf_hash("content_leaf", dict(body))
    return body


def _candidate_set(
    cs_id: str,
    selection_key: str,
    candidates: list[Mapping[str, JsonValue]],
    complete: bool = True,
) -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.selection_candidate_set.v1",
        "candidate_set_id": cs_id,
        "selection_key": selection_key,
        "candidates": candidates,
        "complete": complete,
        "completion_basis": "derived_from_applicability_fact_root",
    }


def _selection_row(
    selection_key: str,
    *,
    status: str,
    effect_interval: list[JsonValue],
    selected_node_version_id: str | None,
    candidate_set_hash: str | None,
    address_id: str = "addr:section:7",
    block_reason: JsonValue = None,
) -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.selection_row.v1",
        "selection_key": selection_key,
        "work_id": "fi:act:301/2004",
        "query_profile_id": "lawvm.selection.governing_text.v1",
        "branch_id": "actual",
        "address_id": address_id,
        "scope_query_id": "scope:unspecified",
        "effect_interval": effect_interval,
        "account_interval": ["corpus:2026-06-21", None],
        "status": status,
        "selected_node_version_id": selected_node_version_id,
        "candidate_set_hash": candidate_set_hash,
        "required_scope_dimensions": [],
        "block_reason": block_reason,
    }


def _applicability_fact(
    fact_id: str,
    *,
    effect_interval: list[JsonValue],
    rail: str = "permanent",
    address_id: str = "addr:section:7",
) -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.applicability_fact.v1",
        "fact_id": fact_id,
        "work_id": "fi:act:301/2004",
        "address_id": address_id,
        "node_version_id": "nv:1",
        "branch_id": "actual",
        "effect_interval": effect_interval,
        "rail": rail,
        "scope_predicate_id": "sha256:scope_total",
    }


def _wrapped_layer(
    kind: str, domain: str, objects: list[Mapping[str, JsonValue]]
) -> PackLayerData:
    rows = tuple(wrap_row(obj) for obj in objects)
    hashes = [str(row["object_hash"]) for row in rows]
    root = set_root(domain, hashes)
    return PackLayerData(kind=kind, domain=domain, root_fn="SetRoot", root=root, rows=rows)


def _manifest_for(layers: Mapping[str, PackLayerData]) -> PackManifest:
    descriptors = tuple(
        PackLayer(
            kind=kind,
            path=f"{kind}/{kind}.jsonl",
            row_schema="lawvm.mixed",
            codec="identity",
            dict_id="",
            uncompressed_sha256="sha256:aa",
            storage_sha256="sha256:aa",
            root=data.root,
            root_fn=data.root_fn,
            row_count=len(data.rows),
        )
        for kind, data in layers.items()
    )
    return PackManifest(
        pack_kind="work_pack",
        work_ids=("fi:act:301/2004",),
        corpus_version="corpus:2026-06-21",
        identity_encoding="lawvm.canonical_json.v1",
        storage_codec="identity",
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={"lawvm.content_leaf.v1": "sha256:schema1"},
        layers=descriptors,
        roots={
            "materialization_root": "sha256:mat",
            "selection_index_root": "sha256:sel",
            "certificate_root": "sha256:cert",
            "source_bundle_root": "sha256:src",
        },
        required_layers_for_browse=("base", "state", "cert"),
        required_layers_for_audit=("base", "state", "trace", "proof", "cert"),
        optional_layers=("surface", "edges", "dict"),
        provenance=PackProvenance(
            lawvm_git_commit="abc123",
            engine_version="lawvm-0.1",
            source_policy_id="keeper_latest_semantic",
            checkable_source_bundle_policy="archival_exact",
            created_at="2026-06-22T00:00:00Z",
            dirty_tree=False,
        ),
    )


def good_pack() -> Pack:
    """A byte-perfect, legally-clean two-row pack (the fire-drill baseline).

    Two adjacent maximal-interval SELECTED rows on one selection key (no
    overlap), each backed by a complete candidate set; two applicability facts
    on one rail (non-overlapping); two content leaves referenced by the rows'
    selected node versions; a full universe MapRoot over the two keys.
    """
    key_a = "sha256:key_a"
    key_b = "sha256:key_b"
    leaf_a = semantic_hash(_content_leaf("text A"))
    leaf_b = semantic_hash(_content_leaf("text B"))

    cand_a: list[Mapping[str, JsonValue]] = [
        {
            "node_version_id": "nv:a",
            "rail": "permanent",
            "effect_interval": ["2010-01-01", "2014-01-01"],
            "scope_predicate_id": "sha256:scope_total",
            "eligible": True,
            "rejected_reason": None,
        }
    ]
    cand_b: list[Mapping[str, JsonValue]] = [
        {
            "node_version_id": "nv:b",
            "rail": "permanent",
            "effect_interval": ["2014-01-01", None],
            "scope_predicate_id": "sha256:scope_total",
            "eligible": True,
            "rejected_reason": None,
        }
    ]
    cs_a = _candidate_set("sha256:cs_a", key_a, cand_a)
    cs_b = _candidate_set("sha256:cs_b", key_b, cand_b)
    cs_a_hash = semantic_hash(cs_a)
    cs_b_hash = semantic_hash(cs_b)

    base = _wrapped_layer(
        "base",
        "content_leaf",
        [
            _content_leaf("text A"),
            _content_leaf("text B"),
        ],
    )
    state_objects: list[Mapping[str, JsonValue]] = [
        _applicability_fact("sha256:fact_a", effect_interval=["2010-01-01", "2014-01-01"]),
        _applicability_fact("sha256:fact_b", effect_interval=["2014-01-01", None]),
        cs_a,
        cs_b,
        _selection_row(
            key_a,
            status="selected",
            effect_interval=["2010-01-01", "2014-01-01"],
            selected_node_version_id="nv:a",
            candidate_set_hash=cs_a_hash,
        ),
        _selection_row(
            key_b,
            status="selected",
            effect_interval=["2014-01-01", None],
            selected_node_version_id="nv:b",
            candidate_set_hash=cs_b_hash,
        ),
    ]
    state = _wrapped_layer("state", "state_selection", state_objects)

    universe = {key_a: "sha256:rowhash_a", key_b: "sha256:rowhash_b"}
    universe_root = map_root("selection_universe", universe)

    layers = {"base": base, "state": state}
    manifest = _manifest_for(layers)
    return Pack(
        manifest=manifest,
        layers=layers,
        selection_universe=universe,
        selection_universe_root=universe_root,
        referenced_hashes={"leaf_a": leaf_a, "leaf_b": leaf_b},
        known_schemas=_KNOWN_SCHEMAS,
    )


def _clone(pack: Pack) -> Pack:
    """Deep-clone the mutable parts so a drill mutation never leaks."""
    layers = {
        kind: PackLayerData(
            kind=data.kind,
            domain=data.domain,
            root_fn=data.root_fn,
            root=data.root,
            rows=tuple(copy.deepcopy(dict(row)) for row in data.rows),
        )
        for kind, data in pack.layers.items()
    }
    return Pack(
        manifest=pack.manifest,
        layers=layers,
        selection_universe=dict(pack.selection_universe)
        if pack.selection_universe is not None
        else None,
        selection_universe_root=pack.selection_universe_root,
        referenced_hashes=dict(pack.referenced_hashes),
        source_availability=dict(pack.source_availability),
        audited_source_refs=pack.audited_source_refs,
        known_schemas=pack.known_schemas,
    )


def _sync_manifest(pack: Pack) -> Pack:
    """Rebuild the manifest from current layers so descriptor roots track them.

    The manifest layer descriptor's ``root`` is the authoritative claim L0.3
    verifies against. After any structural mutation the fixture must rebuild it,
    else every drill trivially fails at L0.3 and masks the targeted check. A
    real exporter re-derives the manifest from the layers the same way.
    """
    layers = dict(pack.layers)
    return Pack(
        manifest=_manifest_for(layers),
        layers=layers,
        selection_universe=dict(pack.selection_universe)
        if pack.selection_universe is not None
        else None,
        selection_universe_root=pack.selection_universe_root,
        referenced_hashes=dict(pack.referenced_hashes),
        source_availability=dict(pack.source_availability),
        audited_source_refs=pack.audited_source_refs,
        known_schemas=pack.known_schemas,
    )


def _replace_state(pack: Pack, new_state: PackLayerData) -> Pack:
    """Return a pack with its state layer swapped + manifest re-synced."""
    new_layers = {**dict(pack.layers), "state": new_state}
    return _sync_manifest(
        Pack(
            manifest=pack.manifest,
            layers=new_layers,
            selection_universe=dict(pack.selection_universe)
            if pack.selection_universe is not None
            else None,
            selection_universe_root=pack.selection_universe_root,
            referenced_hashes=dict(pack.referenced_hashes),
            source_availability=dict(pack.source_availability),
            audited_source_refs=pack.audited_source_refs,
            known_schemas=pack.known_schemas,
        )
    )


def _rebuild_state_root(pack: Pack) -> Pack:
    """Recompute the state layer root from its (mutated) rows + sync manifest."""
    state = pack.layers["state"]
    hashes = [str(row["object_hash"]) for row in state.rows]
    return _replace_state(
        pack,
        PackLayerData(
            kind=state.kind,
            domain=state.domain,
            root_fn=state.root_fn,
            root=set_root(state.domain, hashes),
            rows=state.rows,
        ),
    )


def _rewrap_state(pack: Pack) -> Pack:
    """Re-wrap every state row so object_hash matches its (mutated) body + sync."""
    state = pack.layers["state"]
    rewrapped = tuple(
        wrap_row(cast("Mapping[str, JsonValue]", row["object"])) for row in state.rows
    )
    hashes = [str(row["object_hash"]) for row in rewrapped]
    return _replace_state(
        pack,
        PackLayerData(
            kind=state.kind,
            domain=state.domain,
            root_fn=state.root_fn,
            root=set_root(state.domain, hashes),
            rows=rewrapped,
        ),
    )


def _mutate_state_row(
    pack: Pack,
    *,
    schema: str,
    key_field: str,
    key_value: str,
    updates: Mapping[str, JsonValue],
) -> None:
    """In-place mutate the first matching state-row body (cloned packs only).

    Rows in a cloned pack are deep-copied plain ``dict``s, so the ``cast`` to a
    mutable mapping is sound; it exists only to satisfy the static checker
    (``row["object"]`` is typed ``JsonValue``, a union that includes immutable
    forms). Centralizing the cast removes per-line ``type: ignore`` noise.
    """
    for row in pack.layers["state"].rows:
        body = row.get("object")
        if not isinstance(body, Mapping):
            continue
        mutable = cast("dict[str, JsonValue]", body)
        if mutable.get("schema") == schema and mutable.get(key_field) == key_value:
            for field_name, value in updates.items():
                mutable[field_name] = value
            return
    raise AssertionError(f"no state row matched {schema} {key_field}={key_value}")


def _state_row_hash(pack: Pack, *, schema: str, key_field: str, key_value: str) -> str:
    """The ``object_hash`` of the first matching state row (post-rewrap)."""
    for row in pack.layers["state"].rows:
        body = row.get("object")
        if not isinstance(body, Mapping):
            continue
        typed = cast("Mapping[str, JsonValue]", body)
        if typed.get("schema") == schema and typed.get(key_field) == key_value:
            return str(row["object_hash"])
    raise AssertionError(f"no state row matched {schema} {key_field}={key_value}")


# --------------------------------------------------------------------------- #
# Baseline.
# --------------------------------------------------------------------------- #


def test_good_pack_is_valid_clean() -> None:
    verdict = check_pack(good_pack())
    assert verdict.integrity is IntegrityVerdict.VALID
    assert verdict.certification is CertificationVerdict.VALID_CLEAN
    assert verdict.top_line_verdict is TopLineVerdict.VALID_CLEAN
    assert verdict.violations == ()
    assert verdict.checked_levels == ("L0", "L1")


# --------------------------------------------------------------------------- #
# The eight universe-completeness fire drills (contract §6.2).
# Each drives a CORRUPTED pack through the full production check path.
# --------------------------------------------------------------------------- #


def _row_body_get(row: Mapping[str, JsonValue], field_name: str) -> JsonValue | None:
    """Read ``row["object"][field_name]`` defensively (None if absent/non-map)."""
    body = row.get("object")
    if isinstance(body, Mapping):
        return cast("Mapping[str, JsonValue]", body).get(field_name)
    return None


def test_drill_1_remove_selection_row() -> None:
    # Remove a selection row → SHORTFALL vs universe → INVALID_SELECTION_UNIVERSE.
    pack = _clone(good_pack())
    state = pack.layers["state"]
    kept = tuple(
        row
        for row in state.rows
        if not (
            _row_body_get(row, "schema") == "lawvm.selection_row.v1"
            and _row_body_get(row, "selection_key") == "sha256:key_b"
        )
    )
    pack = _replace_state(
        pack,
        PackLayerData(
            kind=state.kind,
            domain=state.domain,
            root_fn=state.root_fn,
            root=set_root(state.domain, [str(r["object_hash"]) for r in kept]),
            rows=kept,
        ),
    )
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_SELECTION_UNIVERSE
    assert verdict.has_code(ViolationCode.INVALID_SELECTION_UNIVERSE)
    assert any("SHORTFALL" in v.detail for v in verdict.violations)


def test_drill_2_add_undeclared_selection_row() -> None:
    # Add an undeclared selection row → SURPLUS → INVALID_SELECTION_UNIVERSE.
    pack = _clone(good_pack())
    state = pack.layers["state"]
    extra = wrap_row(
        _selection_row(
            "sha256:key_phantom",
            status="absent",
            effect_interval=["2030-01-01", None],
            selected_node_version_id=None,
            candidate_set_hash=None,
        )
    )
    rows = state.rows + (extra,)
    pack = _replace_state(
        pack,
        PackLayerData(
            kind=state.kind,
            domain=state.domain,
            root_fn=state.root_fn,
            root=set_root(state.domain, [str(r["object_hash"]) for r in rows]),
            rows=rows,
        ),
    )
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_SELECTION_UNIVERSE
    assert any("SURPLUS" in v.detail for v in verdict.violations)


def test_drill_3_remove_content_leaf() -> None:
    # Remove a referenced content leaf → dangling ref → INVALID_MISSING_OBJECT.
    pack = _clone(good_pack())
    base = pack.layers["base"]
    kept = tuple(
        row
        for row in base.rows
        if _row_body_get(row, "text") != "text B"
    )
    new_base = PackLayerData(
        kind=base.kind,
        domain=base.domain,
        root_fn=base.root_fn,
        root=set_root(base.domain, [str(r["object_hash"]) for r in kept]),
        rows=kept,
    )
    new_layers = {**dict(pack.layers), "base": new_base}
    pack = _sync_manifest(
        Pack(
            manifest=pack.manifest,
            layers=new_layers,
            selection_universe=dict(pack.selection_universe)
            if pack.selection_universe is not None
            else None,
            selection_universe_root=pack.selection_universe_root,
            referenced_hashes=dict(pack.referenced_hashes),
            source_availability=dict(pack.source_availability),
            audited_source_refs=pack.audited_source_refs,
            known_schemas=pack.known_schemas,
        )
    )
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_MISSING_OBJECT
    assert verdict.has_code(ViolationCode.INVALID_MISSING_OBJECT)


def test_l0_7_content_leaf_with_per_work_member_is_invalid() -> None:
    """A content leaf carrying source_locators (the old bug) → INVALID_HASH (§22.1).

    Re-wrap the leaf body WITH a per-work member so its object_hash stays
    self-consistent (L0.2 passes); L0.7 must still reject it as a non-text-only
    leaf — the dedup-defeating member cannot hide behind a valid wrapper.
    """
    pack = _clone(good_pack())
    base = pack.layers["base"]
    new_rows = []
    for row in base.rows:
        body = dict(cast("Mapping[str, JsonValue]", row["object"]))
        if body.get("text") == "text A":
            body["source_locators"] = ["farchive:fi:work:301/2004"]
            new_rows.append(wrap_row(body))  # re-wrap → object_hash self-consistent
        else:
            new_rows.append(row)
    new_base = PackLayerData(
        kind=base.kind,
        domain=base.domain,
        root_fn=base.root_fn,
        root=set_root(base.domain, [str(r["object_hash"]) for r in new_rows]),
        rows=tuple(new_rows),
    )
    pack = _sync_manifest(
        Pack(
            manifest=pack.manifest,
            layers={**dict(pack.layers), "base": new_base},
            selection_universe=dict(pack.selection_universe)
            if pack.selection_universe is not None
            else None,
            selection_universe_root=pack.selection_universe_root,
            referenced_hashes=dict(pack.referenced_hashes),
            known_schemas=pack.known_schemas,
        )
    )
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.INVALID_HASH)
    assert any("non-text members" in v.detail for v in verdict.violations)


def test_drill_4_change_selected_node_version_id() -> None:
    # Mutate a selected_node_version_id WITHOUT re-wrapping → the row body no
    # longer matches its object_hash → INVALID_HASH (the minimal guaranteed
    # verdict per cross-check #5) + a non-empty violations[].
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.selection_row.v1",
        key_field="selection_key",
        key_value="sha256:key_a",
        updates={"selected_node_version_id": "nv:TAMPERED"},
    )
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_HASH
    assert verdict.has_code(ViolationCode.INVALID_HASH)
    assert verdict.violations  # non-empty


def test_drill_5_overlap_two_selected_rows() -> None:
    # Make the second selected row's interval overlap the first (same key group)
    # then re-wrap+re-root so it's byte-clean → only L1.6 fires.
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.selection_row.v1",
        key_field="selection_key",
        key_value="sha256:key_b",
        updates={"effect_interval": ["2012-01-01", None]},
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_SELECTION_OVERLAP
    assert verdict.has_code(ViolationCode.INVALID_SELECTION_OVERLAP)


def test_drill_6_flip_block_reason_row_to_selected() -> None:
    # A blocked row whose block_reason has no citing blocking residual → L1.5
    # citing failure. (Integrity stays clean; certification reflects blocked.)
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.selection_row.v1",
        key_field="selection_key",
        key_value="sha256:key_b",
        updates={
            "status": "blocked",
            "block_reason": "expiry_unverified",
            "selected_node_version_id": None,
        },
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.BLOCKED_ROW_UNCITED)
    # The row is blocked with no citing residual → certification VALID_BLOCKED.
    assert verdict.certification is CertificationVerdict.VALID_BLOCKED
    assert verdict.top_line_verdict is TopLineVerdict.VALID_BLOCKED


def test_drill_7_drop_candidate_from_candidate_set() -> None:
    # Drop the selected node_version from its complete candidate set → the
    # SELECTED row points at a node absent from candidates → L1.2 violation.
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.selection_candidate_set.v1",
        key_field="selection_key",
        key_value="sha256:key_a",
        updates={"candidates": []},
    )
    pack = _rewrap_state(pack)
    # The selection row still cites the OLD candidate_set_hash; repoint it to the
    # mutated set so the row→set join resolves and L1.2 sees the empty set.
    new_cs_hash = _state_row_hash(
        pack,
        schema="lawvm.selection_candidate_set.v1",
        key_field="selection_key",
        key_value="sha256:key_a",
    )
    _mutate_state_row(
        pack,
        schema="lawvm.selection_row.v1",
        key_field="selection_key",
        key_value="sha256:key_a",
        updates={"candidate_set_hash": new_cs_hash},
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.CANDIDATE_INCOMPLETE)


def test_drill_8_change_root_without_rederiving_manifest() -> None:
    # Forge the manifest's claimed state-layer root WITHOUT re-deriving it from
    # the rows → L0.3 recompute-vs-manifest-claim mismatch → INVALID_ROOT.
    pack = _clone(good_pack())
    forged_layers = tuple(
        dataclasses.replace(layer, root="sha256:FORGED_ROOT")
        if layer.kind == "state"
        else layer
        for layer in pack.manifest.layers
    )
    forged_manifest = dataclasses.replace(pack.manifest, layers=forged_layers)
    pack = Pack(
        manifest=forged_manifest,
        layers=pack.layers,
        selection_universe=pack.selection_universe,
        selection_universe_root=pack.selection_universe_root,
        referenced_hashes=pack.referenced_hashes,
        source_availability=pack.source_availability,
        audited_source_refs=pack.audited_source_refs,
        known_schemas=pack.known_schemas,
    )
    verdict = Checker().check(pack)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_ROOT
    assert verdict.has_code(ViolationCode.INVALID_ROOT)
    assert any(v.expected is not None and v.actual == "sha256:FORGED_ROOT" for v in verdict.violations)


# --------------------------------------------------------------------------- #
# Verdict algebra (contract §1.1 precedence fold) + axis orthogonality.
# --------------------------------------------------------------------------- #


def test_fold_integrity_dominates_certification() -> None:
    # A hard integrity failure dominates any certification state.
    assert (
        fold_top_line(IntegrityVerdict.INVALID_HASH, CertificationVerdict.VALID_BLOCKED)
        is TopLineVerdict.INVALID_HASH
    )


def test_fold_blocked_over_clean_integrity() -> None:
    assert (
        fold_top_line(IntegrityVerdict.VALID, CertificationVerdict.VALID_BLOCKED)
        is TopLineVerdict.VALID_BLOCKED
    )


def test_fold_unsupported_layers_rank() -> None:
    assert (
        fold_top_line(
            IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS, CertificationVerdict.VALID_CLEAN
        )
        is TopLineVerdict.VALID_WITH_UNSUPPORTED_LAYERS
    )


def test_fold_uncheckable_source_over_clean_integrity() -> None:
    assert (
        fold_top_line(
            IntegrityVerdict.VALID, CertificationVerdict.UNCHECKABLE_DIGEST_ONLY
        )
        is TopLineVerdict.UNCHECKABLE_MISSING_SOURCE
    )


def test_unsupported_schema_in_required_layer() -> None:
    # Rename a NON-universe-keyed row (an applicability fact) to an unknown
    # schema so UNSUPPORTED_SCHEMA fires without also tripping the universe-domain
    # check (which only counts selection_row keys).
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.applicability_fact.v1",
        key_field="fact_id",
        key_value="sha256:fact_a",
        updates={"schema": "lawvm.unknown_schema.v9"},
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.UNSUPPORTED_SCHEMA)
    assert verdict.top_line_verdict is TopLineVerdict.UNSUPPORTED_SCHEMA


# --------------------------------------------------------------------------- #
# Source availability — uncheckable, NEVER invalid (design §3.4 / §12).
# --------------------------------------------------------------------------- #


def test_audit_digest_only_source_is_uncheckable_not_invalid() -> None:
    base = good_pack()
    pack = Pack(
        manifest=base.manifest,
        layers=base.layers,
        selection_universe=base.selection_universe,
        selection_universe_root=base.selection_universe_root,
        referenced_hashes=base.referenced_hashes,
        source_availability={"sha256:src1": SourceAvailability.digest_only},
        audited_source_refs=("sha256:src1",),
        known_schemas=base.known_schemas,
    )
    verdict = Checker(mode=CheckMode.AUDIT).check(pack)
    assert verdict.integrity is IntegrityVerdict.VALID
    assert verdict.certification is CertificationVerdict.UNCHECKABLE_DIGEST_ONLY
    assert verdict.top_line_verdict is TopLineVerdict.UNCHECKABLE_MISSING_SOURCE
    # NEVER invalid.
    assert "INVALID" not in verdict.top_line_verdict.value


def test_keeper_locator_is_digest_only_offline() -> None:
    # available_from_keeper_at_locator → digest_only offline (RESOLVED 2026-06-22).
    base = good_pack()
    pack = Pack(
        manifest=base.manifest,
        layers=base.layers,
        selection_universe=base.selection_universe,
        selection_universe_root=base.selection_universe_root,
        referenced_hashes=base.referenced_hashes,
        source_availability={
            "sha256:src1": SourceAvailability.available_from_keeper_at_locator
        },
        audited_source_refs=("sha256:src1",),
        known_schemas=base.known_schemas,
    )
    verdict = Checker(mode=CheckMode.AUDIT).check(pack)
    assert verdict.certification is CertificationVerdict.UNCHECKABLE_DIGEST_ONLY


def test_browse_mode_does_not_require_source() -> None:
    # Browse mode never requires source bytes even if some are digest_only.
    base = good_pack()
    pack = Pack(
        manifest=base.manifest,
        layers=base.layers,
        selection_universe=base.selection_universe,
        selection_universe_root=base.selection_universe_root,
        referenced_hashes=base.referenced_hashes,
        source_availability={"sha256:src1": SourceAvailability.digest_only},
        audited_source_refs=("sha256:src1",),
        known_schemas=base.known_schemas,
    )
    verdict = Checker(mode=CheckMode.BROWSE).check(pack)
    assert verdict.certification is CertificationVerdict.VALID_CLEAN


# --------------------------------------------------------------------------- #
# L1 single-rail + scope marking.
# --------------------------------------------------------------------------- #


def test_single_rail_overlap_detected() -> None:
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.applicability_fact.v1",
        key_field="fact_id",
        key_value="sha256:fact_b",
        updates={"effect_interval": ["2012-01-01", None]},
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.SINGLE_RAIL_OVERLAP)


def test_ambiguous_scope_unwitnessed_flagged() -> None:
    # A row marked ambiguous_missing_scope with a single-scope candidate set has
    # no witnessing divergent pair → SCOPE_AMBIGUITY_UNWITNESSED.
    pack = _clone(good_pack())
    _mutate_state_row(
        pack,
        schema="lawvm.selection_row.v1",
        key_field="selection_key",
        key_value="sha256:key_a",
        updates={"status": "ambiguous_missing_scope", "selected_node_version_id": None},
    )
    pack = _rewrap_state(pack)
    verdict = Checker().check(pack)
    assert verdict.has_code(ViolationCode.SCOPE_AMBIGUITY_UNWITNESSED)


def test_verdict_to_canonical_dict_roundtrips() -> None:
    verdict = check_pack(good_pack())
    body = verdict.to_canonical_dict()
    # The wire format keys (RESOLVED cross-check).
    assert set(body) >= {
        "integrity",
        "certification",
        "top_line_verdict",
        "violations",
    }
    # Deterministic + canonical-JSON-encodable.
    assert semantic_hash(body) == semantic_hash(verdict.to_canonical_dict())


def test_level_l0_skips_l1() -> None:
    verdict = check_pack(good_pack(), level=CheckLevel.L0)
    assert verdict.checked_levels == ("L0",)
    assert verdict.integrity is IntegrityVerdict.VALID
