"""Pins for ``lawvm.corpus_totality.v0`` (design §23.x level A; CORPUS_TOTALITY_CERTIFICATE_V0.md).

The level-A corpus totality object: the work-universe (which works are in scope)
+ the relativity claim (``closed_world_claim``). Tests pin the keystone
``work_inventory_root`` MapRoot behavior (a missing / surplus work changes the
root), the typed-exclusion discipline (no silent non-inclusion), and the honest
``closed_world_claim=false`` default for an observed crawl.
"""

from __future__ import annotations

import pytest

from lawvm.substrate.corpus_totality import (
    CorpusTotalityError,
    IncludedMember,
    WorkInventoryRow,
    build_corpus_totality,
)


def _ct(**kw):
    return build_corpus_totality(
        corpus_id="fi:corpus:2026-06-22",
        jurisdiction="fi",
        included=[
            IncludedMember("fi:act:301/2004", "sha256:p1"),
            IncludedMember("fi:act:39/1889", "sha256:p2"),
        ],
        **kw,
    )


def test_observed_crawl_is_not_closed_world() -> None:
    """The honest v0 default: an observed crawl makes NO jurisdiction-totality claim."""
    ct = _ct()
    body = ct.to_canonical_dict()
    assert body["universe"]["universe_kind"] == "observed_crawl"
    assert body["universe"]["closed_world_claim"] is False


def test_closed_world_on_observed_crawl_is_rejected() -> None:
    """A crawl cannot license closed_world_claim=true (design §23.x relativity)."""
    with pytest.raises(CorpusTotalityError):
        build_corpus_totality(
            corpus_id="fi:corpus:x",
            jurisdiction="fi",
            included=[IncludedMember("fi:act:1/1", "sha256:p")],
            universe_kind="observed_crawl",
            closed_world_claim=True,
        )


def test_work_inventory_root_detects_missing_work() -> None:
    """Dropping a work changes the keystone root (a missing work is detectable)."""
    full = _ct()
    fewer = build_corpus_totality(
        corpus_id="fi:corpus:2026-06-22",
        jurisdiction="fi",
        included=[IncludedMember("fi:act:301/2004", "sha256:p1")],
    )
    assert full.work_inventory_root != fewer.work_inventory_root


def test_work_universe_root_is_alias_of_inventory_root() -> None:
    """``work_universe_root`` (design §23.x name) == ``work_inventory_root`` (spec name)."""
    ct = _ct()
    assert ct.work_universe_root == ct.work_inventory_root


def test_counts_tally_by_status() -> None:
    ct = build_corpus_totality(
        corpus_id="fi:corpus:x",
        jurisdiction="fi",
        included=[IncludedMember("fi:act:1/1", "sha256:p")],
        excluded=[
            WorkInventoryRow("fi:act:2/2", inventory_status="superseded", reason_detail="replaced"),
            WorkInventoryRow(
                "fi:act:3/3", inventory_status="unavailable_source", reason_detail="no bytes"
            ),
        ],
    )
    counts = ct.counts()
    assert counts["included"] == 1
    assert counts["unavailable_source"] == 1
    assert counts["total"] == 3


def test_included_row_requires_pack_id() -> None:
    with pytest.raises(CorpusTotalityError):
        WorkInventoryRow("fi:act:1/1", inventory_status="included")  # no pack_id


def test_nonincluded_row_requires_reason() -> None:
    """A non-included work must OWN its exclusion — no silent non-inclusion."""
    with pytest.raises(CorpusTotalityError):
        WorkInventoryRow("fi:act:1/1", inventory_status="withdrawn")  # no reason_detail


def test_nonincluded_row_must_not_carry_pack_id() -> None:
    with pytest.raises(CorpusTotalityError):
        WorkInventoryRow(
            "fi:act:1/1", inventory_status="withdrawn", reason_detail="r", pack_id="sha256:p"
        )


def test_duplicate_work_id_rejected() -> None:
    with pytest.raises(CorpusTotalityError):
        build_corpus_totality(
            corpus_id="fi:corpus:x",
            jurisdiction="fi",
            included=[
                IncludedMember("fi:act:1/1", "sha256:p1"),
                IncludedMember("fi:act:1/1", "sha256:p2"),
            ],
        )


def test_corpus_totality_id_is_deterministic() -> None:
    assert _ct().corpus_totality_id == _ct().corpus_totality_id
    assert _ct().corpus_totality_id.startswith("sha256:")
