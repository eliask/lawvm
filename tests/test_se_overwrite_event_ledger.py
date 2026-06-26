"""Tests for the SE archive-write monotonicity ledger (KNOW-01 + §1.6).

Brings SE's ``--force-reextract`` overwrite path into the "every external
update creates a new manifestation record" invariant (pro-note §8 KNOW-01).
Mirrors the discipline of the sibling evidence-plane dossier types
(AssumptionRegister, coverage_universe): a typed per-overwrite-event record
captures prior + new content hashes, so the cached source-footing's
identity-mutation is auditable, not silent.

See src/lawvm/sweden/se_overwrite_event_ledger.py.
"""

from __future__ import annotations


import pytest

from lawvm.sweden.se_overwrite_event_ledger import (
    SEOverwriteEvent,
    se_overwrite_event_root,
    se_store_with_overwrite_event,
)


class _MemoryArchive:
    """Tiny in-memory store implementing the _ArchiveLike.store/get/has shape."""

    def __init__(self) -> None:
        self.cells: dict[str, bytes] = {}

    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:  # noqa: ARG002
        self.cells[locator] = data
        return locator

    def get(self, locator: str) -> bytes | None:
        return self.cells.get(locator)

    def has(self, locator: str, *, max_age_hours: float = 0.0) -> bool:  # noqa: ARG002
        return locator in self.cells


def _entry(
    sfs_id: str = "2026:1",
    locator: str = "se://sfs/2026:1/official.pdf.txt",
    *,
    prior: str = "",
    new: str = "",
    trigger: str = "force_reextract",
) -> SEOverwriteEvent:
    return SEOverwriteEvent(
        sfs_id=sfs_id,
        locator=locator,
        prior_bytes_sha256=prior,
        new_bytes_sha256=new,
        source_trigger=trigger,
    )


def _sha256(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Event construction.                                                          #
# --------------------------------------------------------------------------- #


def test_event_is_well_formed() -> None:
    e = _entry(prior="sha256:priorhash", new="sha256:newhash")
    assert e.sfs_id == "2026:1"
    assert e.rule_id == "se_official_artifacts_force_reextract_overwrite"
    assert e.source_trigger == "force_reextract"


def test_event_unknown_trigger_raises_loud() -> None:
    """§1.10 fail-loud: a trigger not in the closed set raises ValueError."""
    with pytest.raises(ValueError, match="not in the closed set"):
        _entry(trigger="unknown_future_trigger")


def test_event_blank_locator_raises_loud() -> None:
    """§1.10 fail-loud: a blank locator is folklore, not a record."""
    with pytest.raises(ValueError, match="locator must be non-empty"):
        _entry(locator="")


def test_event_blank_sfs_id_raises_loud() -> None:
    """§1.10 fail-loud: a blank sfs_id is folklore, not a record."""
    with pytest.raises(ValueError, match="sfs_id must be non-empty"):
        _entry(sfs_id="")


def test_event_id_is_stable_for_same_record() -> None:
    e = _entry(prior="sha256:p", new="sha256:n")
    assert e.event_id == _entry(prior="sha256:p", new="sha256:n").event_id
    assert e.event_id.startswith("sha256:")


def test_event_id_distinguishes_distinct_priors() -> None:
    """Two overwrite events at the same locator with different prior hashes
    must produce distinct event_ids — the prior-manifestation hash IS identity."""
    a = _entry(prior="sha256:prior_a", new="sha256:new")
    b = _entry(prior="sha256:prior_b", new="sha256:new")
    assert a.event_id != b.event_id


def test_event_id_distinguishes_distinct_new_hashes() -> None:
    """Two overwrite events at the same locator with different new hashes (e.g.
    the same prior text re-extracted at different times with parser-coercion
    updates producing different cleaned text) must produce distinct event_ids."""
    a = _entry(prior="sha256:prior", new="sha256:new_a")
    b = _entry(prior="sha256:prior", new="sha256:new_b")
    assert a.event_id != b.event_id


# --------------------------------------------------------------------------- #
# Wrapper behavior — the production archive.store adapter.                    #
# --------------------------------------------------------------------------- #


def test_wrapper_stores_data_through_to_archive() -> None:
    """The wrapper MUST actually call archive.store — not silently no-op the
    write (§1.10 fail-loud: a wrapper that pretends to audit but doesn't write
    is a worse failure mode than the original silent overwrite)."""
    archive = _MemoryArchive()
    events: list[SEOverwriteEvent] = []
    se_store_with_overwrite_event(
        archive,
        locator="se://sfs/2026:1/official.pdf.txt",
        new_data=b"new text",
        sfs_id="2026:1",
        source_trigger="force_reextract",
        events_out=events,
        storage_class="text",
    )
    assert archive.get("se://sfs/2026:1/official.pdf.txt") == b"new text"
    assert len(events) == 1


def test_wrapper_records_prior_bytes_hash_on_overwrite() -> None:
    """KNOW-01 load-bearing: when prior bytes exist, the event's
    prior_bytes_sha256 MUST be the prior content hash — not blank."""
    archive = _MemoryArchive()
    archive.cells["se://sfs/2026:1/official.pdf.txt"] = b"old text"
    events: list[SEOverwriteEvent] = []
    se_store_with_overwrite_event(
        archive,
        locator="se://sfs/2026:1/official.pdf.txt",
        new_data=b"new text",
        sfs_id="2026:1",
        source_trigger="force_reextract",
        events_out=events,
        storage_class="text",
    )
    assert events[0].prior_bytes_sha256 == _sha256(b"old text")
    assert events[0].new_bytes_sha256 == _sha256(b"new text")


def test_wrapper_leaves_prior_bytes_sha256_blank_on_first_write() -> None:
    """First-time write emits an event with empty prior_bytes_sha256 — the
    event records "a new manifestation was created", not "matter was mutated"."""
    archive = _MemoryArchive()
    events: list[SEOverwriteEvent] = []
    se_store_with_overwrite_event(
        archive,
        locator="se://sfs/2026:1/official.pdf.txt",
        new_data=b"first",
        sfs_id="2026:1",
        source_trigger="manual_reingest",
        events_out=events,
        storage_class="text",
    )
    assert events[0].prior_bytes_sha256 == ""
    assert events[0].new_bytes_sha256 == _sha256(b"first")


def test_wrapper_handles_no_accumulator_caller_path() -> None:
    """When the caller does NOT pass an accumulator, the wrapper behaves as a
    passthrough: stores + emits a no-op event (the write is un-audited, by
    design — call sites opt-in by passing events_out)."""
    archive = _MemoryArchive()
    archive.cells["se://sfs/2026:1/official.pdf.txt"] = b"prior"
    event = se_store_with_overwrite_event(
        archive,
        locator="se://sfs/2026:1/official.pdf.txt",
        new_data=b"new",
        sfs_id="2026:1",
        source_trigger="force_reextract",
        events_out=None,  # no accumulator
        storage_class="text",
    )
    assert archive.get("se://sfs/2026:1/official.pdf.txt") == b"new"
    assert event.prior_bytes_sha256 == ""
    assert event.new_bytes_sha256 == ""


# --------------------------------------------------------------------------- #
# Ledger root.                                                                 #
# --------------------------------------------------------------------------- #


def test_root_is_deterministic_across_argument_orders() -> None:
    """The committed ledger root is order-independent: any future duplicate
    accumulator walks the same set produces the same root."""
    events_a = [_entry(locator="A"), _entry(locator="B")]
    events_b = [_entry(locator="B"), _entry(locator="A")]
    assert se_overwrite_event_root(events_a) == se_overwrite_event_root(events_b)


def test_root_changes_when_event_added() -> None:
    a = se_overwrite_event_root([_entry(locator="A")])
    b = se_overwrite_event_root([_entry(locator="A"), _entry(locator="B")])
    assert a != b


def test_root_empty_set_is_well_defined() -> None:
    assert se_overwrite_event_root([]).startswith("sha256:")


# --------------------------------------------------------------------------- #
# Wired through fetch_se_official_artifacts.                                  #
# --------------------------------------------------------------------------- #


def test_fetch_se_official_artifacts_with_overwrite_events_still_runs() -> None:
    """Guard-liveness (§2.9): the new overwrite_events_out parameter is in the
    production signature — we don't drive a real PDF fetch here (that needs
    @network), but the smoke check asserts the function accepts the parameter
    without raising TypeErrors."""
    import inspect

    from lawvm.sweden.fetch import fetch_se_official_artifacts

    sig = inspect.signature(fetch_se_official_artifacts)
    assert "overwrite_events_out" in sig.parameters, (
        "fetch_se_official_artifacts must accept overwrite_events_out — the "
        "KNOW-01 ledger is only useful when callers can pass an accumulator."
    )
