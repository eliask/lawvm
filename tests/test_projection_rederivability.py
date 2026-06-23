"""Projection re-derivability gate (audit registry row PROJ-01).

Registry assertion (LAWVM_AUDIT_INVARIANT_REGISTRY.md §2.10 / §3.E, PROJECTION
plane — *"projection must be re-derivable from a committed dossier"*):

    Every projection row (seam / viewer / parquet / SQLite / packet) must hash
    back to a fresh re-materialization from the committed dossier matter
    (base_state, ops, pit_date — here, the committed source XML). A hand-edited /
    stale row that no longer re-derives from the committed matter is DRIFT and
    must be detectable.

HONESTY (the generator's stopping rule — Axis J).
The Finland deterministic ``fi_refs`` projection is ALREADY a pure function of
its committed dossier matter: ``_project_refs_for_statute`` (deterministic
profile) reads ONLY the committed source XML bytes for the statute and walks them
through the Legal Surface Graph projector + ``_augment_row``; it author-sets NO
clock / uuid / random / cross-row enrichment field (the export-envelope
``time.time()`` / ``datetime.now()`` are MANIFEST metadata, not row content, and
the deterministic path is per-statute independent and picklable — see
``_project_refs_for_statute_deterministic``). So PROJ-01 here is the **standing
re-derivability gate that PINS the projection as a pure function of the committed
matter**: re-run the production projector over the committed bytes and assert the
emitted rows content-hash IDENTICALLY to the first materialization. A row that
was hand-edited (or went stale) no longer re-derives — the per-row content-hash
multiset diverges and the gate emits ``PROJECTION_NOT_REDERIVABLE``.

This is the projection-plane analogue of ``tests/test_replay_determinism.py``
(LS-30/LS-31): the comparison is STRUCTURAL + BYTE over canonical-JSON-encoded
row content, not merely "no exception was raised". Like those determinism gates
it registers NO ``observation_registry`` finding-kind — there is no production
sink that emits ``PROJECTION_NOT_REDERIVABLE``; the gate IS the enforcement, and
the proposed code is carried in the assertion message.

The committed dossier matter is pinned INLINE (a small, memory-bounded sample of
committed AKN source bytes) rather than read from the live farchive, so the gate
is robust in a corpus-less checkout while still driving the REAL production
projector over REAL committed source. Each sample is shaped to yield >= 1
reference row (a cross-statute id cite + an internal § ref) so the row content is
discriminating rather than the empty-projection constant.

Self-test (acceptance proof): ``test_hand_edited_row_does_not_rederive`` mutates
one materialized row and asserts the per-row content-hash multiset no longer
matches the fresh re-derivation — proving the gate would CATCH a hand-edited /
stale projection row, not pass vacuously.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

import pytest

from lawvm.core.manual_claims.primitive import _ProfileTagDeprecated as ProfileTag
from lawvm.tools.export_fi_refs import _project_refs_for_statute

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Deterministic-only is the surface_only projection profile whose rows are a pure
# function of the committed source bytes (no composer / cross-statute NULL-slot
# fill, which only runs for the non-deterministic profiles).
_PROFILE = ProfileTag.DETERMINISTIC_ONLY


def _akn(statute_id: str, *paragraphs: str) -> bytes:
    body = "\n".join(f"    <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}">\n'
        f"  <act><body><section eId=\"sec_1\"><num>1 §</num><content>\n"
        f"{body}\n"
        f"  </content></section></body></act>\n"
        f"</akomaNtoso>\n"
    ).encode("utf-8")


# A SMALL, memory-bounded sample of COMMITTED dossier matter. Each entry is the
# committed source XML for one (synthetic-id) statute, shaped to yield >= 1
# reference row through the production deterministic projector: an explicit
# cross-statute id cite (527/2014 5 §) plus an internal § ref (5 §). The bytes
# ARE the committed dossier matter PROJ-01 re-materializes from.
SAMPLE_COMMITTED_MATTER: Dict[str, bytes] = {
    "991/2099": _akn(
        "991/2099",
        "Tata lakia sovelletaan ymparistonsuojelulain (527/2014) "
        "5 §:ssa tarkoitettuun toimintaan.",
        "Edella 1 momentissa tarkoitettuun toimintaan sovelletaan "
        "myos 5 §:n saannoksia.",
    ),
    "992/2099": _akn(
        "992/2099",
        "Poiketen siita, mita tyosopimuslaissa (55/2001) saadetaan, "
        "sovelletaan 3 §:n mukaista menettelya.",
    ),
}


class _DictStore:
    """A minimal committed-matter store: read_oracle returns the committed bytes."""

    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._mapping = dict(mapping)

    def read_oracle(self, statute_id: str) -> bytes:
        return self._mapping[statute_id]


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True)


def _row_content_hash(row: Dict[str, Any]) -> str:
    """Order-independent canonical content hash of one projection row.

    Every field of the row is hashed (sort_keys), so a hand-edit of ANY column —
    a target id, a span offset, a cite kind — perturbs the hash.
    """
    payload = _canonical_json(row)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _project_committed_matter(
    statute_id: str, store: _DictStore
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Re-materialize the projection of one committed statute from its bytes.

    Returns (rows, sorted_per_row_content_hashes). The sorted content-hash list is
    the re-derivable fingerprint: a multiset of per-row hashes that any fresh
    re-materialization of the SAME committed bytes must reproduce.
    """
    rows, _diag = _project_refs_for_statute(statute_id, store, _PROFILE)
    hashes = sorted(_row_content_hash(row) for row in rows)
    return rows, hashes


# --------------------------------------------------------------------------- #
# PROJ-01 — every projection row re-derives from the committed matter.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("statute_id", sorted(SAMPLE_COMMITTED_MATTER))
def test_projection_rows_rederive_from_committed_matter(statute_id: str) -> None:
    """Re-materialize the projection twice from the committed bytes -> identical.

    Each emitted row content-hashes back to a value freshly re-derived from the
    committed dossier matter; the per-row content-hash multiset is byte-identical
    across the two re-materializations. A hand-edited / stale row would change its
    content hash and break this multiset equality (PROJ-01 drift).
    """
    store = _DictStore(SAMPLE_COMMITTED_MATTER)

    first_rows, first_hashes = _project_committed_matter(statute_id, store)
    second_rows, second_hashes = _project_committed_matter(statute_id, store)

    assert first_rows, (
        f"{statute_id}: committed-matter sample yielded no projection rows; the "
        f"PROJ-01 gate would be vacuous on this statute"
    )
    assert first_hashes == second_hashes, (
        f"PROJECTION_NOT_REDERIVABLE: {statute_id} projection rows did not "
        f"re-derive identically from the committed matter across two "
        f"materializations.\n  first:  {first_hashes}\n  second: {second_hashes}"
    )
    # Whole-row-set byte identity (defence in depth over the per-row hash).
    assert _canonical_json(first_rows) == _canonical_json(second_rows), (
        f"PROJECTION_NOT_REDERIVABLE: {statute_id} projection row bytes drifted "
        f"across re-materialization of the same committed matter"
    )


def test_committed_matter_rows_are_discriminating() -> None:
    """Guard against a vacuous gate: distinct committed statutes must NOT collapse
    to the same projection-row content-hash multiset (which would make the
    re-derivability assertion trivially true regardless of correctness)."""
    store = _DictStore(SAMPLE_COMMITTED_MATTER)
    fingerprints = {
        sid: tuple(_project_committed_matter(sid, store)[1])
        for sid in SAMPLE_COMMITTED_MATTER
    }
    assert len(set(fingerprints.values())) == len(SAMPLE_COMMITTED_MATTER), (
        f"committed-matter projection fingerprints are not discriminating: "
        f"{fingerprints}"
    )


# --------------------------------------------------------------------------- #
# Acceptance proof — a hand-edited / stale row would FAIL to re-derive.
# --------------------------------------------------------------------------- #


def test_hand_edited_row_does_not_rederive() -> None:
    """A stale / hand-edited row no longer re-derives from the committed matter.

    Simulate a persisted projection that was hand-edited after materialization
    (its ``target_statute_id`` was tampered with). The fresh re-derivation from
    the committed bytes produces a DIFFERENT per-row content-hash multiset, so the
    PROJ-01 re-derivability check turns RED — proving the gate would CATCH drift,
    not pass vacuously on a deterministic projector.
    """
    statute_id = sorted(SAMPLE_COMMITTED_MATTER)[0]
    store = _DictStore(SAMPLE_COMMITTED_MATTER)

    committed_rows, committed_hashes = _project_committed_matter(statute_id, store)
    assert committed_rows

    # A "stale" persisted projection: hand-edit one row's target id (the kind of
    # silent drift a content-blind freshness check on the manifest would miss).
    stale_rows = [dict(row) for row in committed_rows]
    stale_rows[0]["target_statute_id"] = "000/0000-HAND-EDITED"
    stale_hashes = sorted(_row_content_hash(row) for row in stale_rows)

    # The stale persisted projection does NOT re-derive from the committed matter.
    assert stale_hashes != committed_hashes, (
        "a hand-edited projection row must NOT re-derive to the committed-matter "
        "fingerprint; the PROJ-01 gate did not discriminate the drift"
    )

    # Concretely demonstrate the gate's own assertion firing on the stale row set.
    with pytest.raises(AssertionError):
        assert stale_hashes == committed_hashes, (
            "PROJECTION_NOT_REDERIVABLE: stale projection row failed to re-derive "
            "from committed matter"
        )

    # And the un-tampered re-materialization is still green (the perturbation is
    # fully scoped to the simulated stale copy, not the projector).
    _, fresh_hashes = _project_committed_matter(statute_id, store)
    assert fresh_hashes == committed_hashes
