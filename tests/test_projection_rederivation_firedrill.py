"""Fire-drill for D9 ``PROJECTION.REDERIVATION_DRIFT`` (guard-liveness, task #104).

A guard-liveness fire-drill drives the DECIDING guard into its firing state from
a production-representative path and proves the guard EMITS its registered
finding — not a hand-built ``Observation``. For D9 the deciding guard is
``core.projection_rederivation_audit.assert_projection_rows_rederivable``: every
committed projection row's committed ``projection_hash`` must recompute from its
own committed ``projection_payload`` under the dossier's pinned §3.4 hash view.

The audit's natural production projection-row PRODUCER is the certificate dossier
writer ``tools.certificate_bundle.build_certificate_bundle``: it emits the seam
projection family (``lawvm.provision_state.v1``) as wrapper rows
(``{projection_payload, certificate: {projection_hash, ...}, universe}``) to
``projections/seam_rows.jsonl`` — exactly the row shape the audit consumes. This
drill drives the audit over the REAL committed seam rows that producer emits:

  * GREEN ARM (production-representative): build a real certificate bundle for a
    corpus statute, read the committed seam wrapper rows, and drive the audit
    over them under the dossier's pinned ``hash_excluded_members``. The audit
    must EMIT NO finding — every committed row re-derives from its committed
    payload (the writer's ``verify_bundle`` self-check pins the same recompute).

  * RED ARM (the firing state): hand-edit ONE committed row's payload (a stale /
    externally-edited / hand-inserted row whose committed hash no longer matches
    its payload) and drive the SAME audit. It must EMIT exactly one
    ``PROJECTION.REDERIVATION_DRIFT`` finding carrying the fixed-shape evidence
    (row id, expected vs actual hash, the pinned exclusion list). If the audit
    were silently disconnected from this projection-row producer, this drill goes
    red — closing the guard-liveness loop (NO_FIRE_DRILL_YET -> drilled).

The committed-bundle build needs the canonical Finlex corpus; the drill skips
cleanly when it is absent (mirroring the certificate-bundle corpus gate) so a
corpus-less checkout stays green without faking liveness.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from lawvm.core.projection_rederivation_audit import (
    PROJECTION_REDERIVATION_DRIFT,
    assert_projection_rows_rederivable,
)
from lawvm.tools.certificate_bundle import SEAM_HASH_EXCLUDED_MEMBERS


def _canonical_farchive() -> Path:
    """Resolve the canonical Finlex corpus the bundle writer materializes from."""
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return Path(root) / "data" / "finlex.farchive"


_corpus_skip = pytest.mark.skipif(
    not _canonical_farchive().exists(),
    reason="canonical Finlex farchive not present; skipping real-corpus D9 drill",
)

# A small corpus statute that builds a non-trivial seam projection (multiple
# provision-state rows across timeline boundaries) so the drill is discriminating
# rather than the empty-projection constant. 482/2024 is the same statute the
# certificate-bundle corpus gate pins.
_DRILL_STATUTE = "482/2024"


def _committed_seam_rows(tmp_path: Path) -> List[Dict[str, Any]]:
    """Build a REAL certificate bundle and read its committed seam wrapper rows.

    These are the production projection rows ``build_certificate_bundle`` emits
    to ``projections/seam_rows.jsonl`` — the exact carriers the D9 audit consumes
    (``projection_payload`` + ``certificate.projection_hash`` + ``universe``).
    """
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    out = tmp_path / "bundle"
    build_certificate_bundle(
        _DRILL_STATUTE, out, graph_store_root=tmp_path / "provenance_graph"
    )
    seam_path = out / "projections" / "seam_rows.jsonl"
    return [
        json.loads(line)
        for line in seam_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@_corpus_skip
def test_projection_rederivation_audit_clean_over_committed_bundle(
    tmp_path: Path,
) -> None:
    """GREEN ARM: every committed seam row re-derives; the audit stays silent.

    Drives the deciding guard over the REAL committed projection rows the dossier
    writer emits. On a faithfully-written bundle the audit must emit no finding —
    this is the production-representative clean arm the RED arm below is measured
    against (so the firing in that arm is the hand-edit, not a flaky producer).
    """
    rows = _committed_seam_rows(tmp_path)
    assert rows, (
        f"certificate bundle for {_DRILL_STATUTE} emitted no seam rows; the D9 "
        "drill would be vacuous"
    )

    findings = assert_projection_rows_rederivable(
        rows,
        hash_excluded_members=SEAM_HASH_EXCLUDED_MEMBERS,
        source_statute=_DRILL_STATUTE,
    )
    assert findings == (), (
        "PROJECTION.REDERIVATION_DRIFT: the D9 audit fired over a faithfully "
        f"written certificate bundle for {_DRILL_STATUTE}: "
        f"{[f.detail for f in findings]}"
    )


@_corpus_skip
def test_projection_rederivation_audit_emits_on_hand_edited_committed_row(
    tmp_path: Path,
) -> None:
    """RED ARM: a hand-edited committed payload makes the D9 audit FIRE.

    Drives the deciding guard into its firing state from the production
    projection-row producer: one committed row's payload is hand-edited (its
    ``provision_status`` is tampered) while its committed ``projection_hash`` is
    left intact — the signature of a stale / externally-edited / hand-inserted
    row. The audit must EMIT exactly one ``PROJECTION.REDERIVATION_DRIFT`` finding
    for that one row, carrying the fixed-shape triage evidence.
    """
    rows = _committed_seam_rows(tmp_path)
    assert rows

    tampered = copy.deepcopy(rows)
    # Hand-edit the payload of the first row but keep its committed hash — the
    # row's lineage is now opaque (the committed hash no longer re-derives).
    payload = tampered[0]["projection_payload"]
    assert isinstance(payload, dict)
    payload["provision_status"] = "HAND-EDITED-AFTER-COMMIT"

    findings = assert_projection_rows_rederivable(
        tampered,
        hash_excluded_members=SEAM_HASH_EXCLUDED_MEMBERS,
        source_statute=_DRILL_STATUTE,
    )

    assert len(findings) == 1, (
        "the D9 re-derivation audit must emit exactly one finding for one "
        f"hand-edited committed row; got {len(findings)}"
    )
    finding = findings[0]
    assert finding.kind == PROJECTION_REDERIVATION_DRIFT
    assert finding.source_statute == _DRILL_STATUTE
    detail = finding.detail
    # expected = committed hash; actual = fresh recompute from the tampered
    # payload — they disagree exactly when the row's lineage is opaque.
    assert detail["expected_hash"] != detail["actual_hash"]
    assert tuple(detail["hash_excluded_members"]) == tuple(SEAM_HASH_EXCLUDED_MEMBERS)
    assert detail["reason"] == (
        "committed_projection_hash_does_not_recompute_from_payload"
    )
    assert detail["row_id"]["row_index"] == 0

    # The un-tampered rows still re-derive cleanly (the perturbation is scoped to
    # the one hand-edited copy, not the producer).
    clean = assert_projection_rows_rederivable(
        rows,
        hash_excluded_members=SEAM_HASH_EXCLUDED_MEMBERS,
        source_statute=_DRILL_STATUTE,
    )
    assert clean == ()
