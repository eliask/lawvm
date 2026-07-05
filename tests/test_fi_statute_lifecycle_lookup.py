"""Tests for the registry-sourced ``LifecycleLookup`` + runnable dangling scan.

Exercises ``references.statute_lifecycle_lookup`` end-to-end WITHOUT any
``legal_pit`` replay: the lifecycle window is sourced from the statute-name
registry (corpus XML / the pre-built artifact) and the scan runs the CHEAP
``detect_statute_lifecycle_broken`` over a corpus slice.

The load-bearing assertions:
  * a citation to a REPEALED target act is flagged (target_statute_repealed);
  * a citation to a LIVE target act is NOT flagged;
  * an unknown lifecycle is UNVERIFIABLE, never a false BROKEN (fail-loud).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from lawvm.finland.references.broken_detection import (
    BrokenReason,
    StatuteLifecycle,
    StatuteLifecycleFinding,
)
from lawvm.finland.references.statute_lifecycle_lookup import (
    LifecycleCache,
    default_lifecycle_lookup,
    oracle_lifecycle_lookup,
    registry_artifact_lifecycle_lookup,
    scan_dangling_citations,
)

# A citing statute (2022/711) whose body has one <ref> to another act's § 5.
# The target act id in AKN URI order /YEAR/NUMBER resolves to statute_id
# "2010/500".
_CITING_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<body><section eId=\"sec_1\"><num>1 \xc2\xa7</num>"
    b'<p>Viitataan <ref href="/akn/fi/act/statute/2010/500/!main#sec_5">'
    b"toiseen lakiin</ref>.</p>"
    b"</section></body></akomaNtoso>"
)

# A source XML carrying an enactment date (FRBRWork dateIssued).
_SOURCE_2010 = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<act><meta><identification><FRBRWork>"
    b'<FRBRdate name="dateIssued" date="2010-06-01"/>'
    b"</FRBRWork></identification></meta>"
    b"<preface><docTitle>Testilaki</docTitle></preface></act></akomaNtoso>"
)

# A consolidated oracle carrying a finlex:repealedBy block (the repeal date =
# the date the repealing act entered into force = exclusive valid_to).
_ORACLE_REPEALED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
    b'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
    b"<act><finlex:repealedBy><finlex:statuteReference><finlex:inForce>"
    b'<finlex:dateEntryIntoForce date="2015-04-01"/>'
    b"</finlex:inForce></finlex:statuteReference></finlex:repealedBy>"
    b"<body/></act></akomaNtoso>"
)


def _lifecycle_table(table: dict[str, StatuteLifecycle]):
    """A LifecycleLookup over a dict; unknown ids -> known=False (fail-loud)."""

    def _lookup(statute_id: str) -> StatuteLifecycle:
        return table.get(
            statute_id, StatuteLifecycle(valid_from=None, valid_to=None, known=False)
        )

    return _lookup


class _CitingStore:
    """Minimal store: one citing body (as its oracle), no source/amendment."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies

    def read_oracle(self, sid: str) -> Optional[bytes]:
        return self._bodies.get(sid)

    def read_source(self, sid: str) -> Optional[bytes]:
        return None

    def read_amendment(self, sid: str) -> Optional[bytes]:
        return None


# ---------------------------------------------------------------------------
# The registry-sourced LifecycleCache reads windows from corpus XML (no replay)
# ---------------------------------------------------------------------------


def test_lifecycle_cache_reads_repeal_date_from_oracle() -> None:
    """valid_from from source dateIssued; valid_to from oracle repealedBy — no replay."""

    class _S:
        def read_source(self, sid):
            return _SOURCE_2010 if sid == "2010/500" else None

        def read_amendment(self, sid):
            return None

        def read_oracle(self, sid):
            return _ORACLE_REPEALED if sid == "2010/500" else None

    cache = LifecycleCache(_S())  # ty: ignore[invalid-argument-type]
    lc = cache.get("2010/500")
    assert lc.known is True
    assert lc.valid_from == date(2010, 6, 1)
    assert lc.valid_to == date(2015, 4, 1)
    # Second read is a cache hit (window read at most once per act).
    cache.get("2010/500")
    assert cache.hits == 1
    assert cache.misses == 1


def test_lifecycle_cache_unknown_when_no_corpus_xml() -> None:
    """No source/amendment/oracle for an act -> known=False (fail-loud)."""

    class _Empty:
        def read_source(self, sid):
            return None

        def read_amendment(self, sid):
            return None

        def read_oracle(self, sid):
            return None

    lookup = oracle_lifecycle_lookup(_Empty())  # ty: ignore[invalid-argument-type]
    assert lookup("999/1999").known is False


# ---------------------------------------------------------------------------
# The artifact-backed lookup serves windows from disk (the full-corpus perf
# path), with the oracle fallback for ids the artifact does not carry.
# ---------------------------------------------------------------------------


def test_artifact_lifecycle_lookup_serves_windows_from_disk(tmp_path) -> None:
    from lawvm.finland.references.registries.statute_name import (
        StatuteNameEntry,
        serialize_entries,
    )

    path = tmp_path / "registry.jsonl"
    serialize_entries(
        [
            StatuteNameEntry(
                statute_id="2010/500",
                canonical_title="Testilaki",
                valid_from=date(2010, 6, 1),
                valid_to=date(2015, 4, 1),
            ),
            StatuteNameEntry(
                statute_id="2018/9",
                canonical_title="Elava laki",
                valid_from=date(2018, 1, 1),
                valid_to=None,  # still in force (open)
            ),
        ],
        path,
    )
    lookup = registry_artifact_lifecycle_lookup(path)

    dead = lookup("2010/500")
    assert dead.known is True and dead.valid_to == date(2015, 4, 1)
    live = lookup("2018/9")
    assert live.known is True and live.valid_to is None  # open = in force

    # An id absent from the artifact, with no fallback, is unverifiable.
    assert lookup("404/1999").known is False


def test_artifact_lookup_delegates_absent_id_to_oracle_fallback(tmp_path) -> None:
    """An orphan-oracle id (not in the artifact) is served by the oracle fallback."""
    from lawvm.finland.references.registries.statute_name import (
        StatuteNameEntry,
        serialize_entries,
    )

    path = tmp_path / "registry.jsonl"
    serialize_entries(
        [StatuteNameEntry(statute_id="2018/9", canonical_title="Elava laki")], path
    )

    class _OracleOnly:
        def read_source(self, sid):
            return None

        def read_amendment(self, sid):
            return None

        def read_oracle(self, sid):
            return _ORACLE_REPEALED if sid == "2010/500" else None

    fallback = oracle_lifecycle_lookup(_OracleOnly())  # ty: ignore[invalid-argument-type]
    lookup = registry_artifact_lifecycle_lookup(path, fallback=fallback)
    dead = lookup("2010/500")  # absent from artifact -> oracle fallback
    assert dead.known is True and dead.valid_to == date(2015, 4, 1)


# ---------------------------------------------------------------------------
# The runnable corpus scan: a repealed target is flagged; a live one passes.
# ---------------------------------------------------------------------------


def test_scan_flags_citation_to_repealed_act() -> None:
    """A live text citing an act repealed before the citing anchor -> a finding."""
    store = _CitingStore({"2022/711": _CITING_BODY})
    # Target 2010/500 was repealed 2015-04-01; the citing anchor (now) is later.
    lifecycle = _lifecycle_table(
        {
            "2010/500": StatuteLifecycle(
                valid_from=date(2010, 6, 1), valid_to=date(2015, 4, 1)
            )
        }
    )
    report = scan_dangling_citations(
        ["2022/711"],
        store,  # ty: ignore[invalid-argument-type]
        lifecycle_of=lifecycle,
        current_as_of=date(2024, 1, 1),
    )
    assert report.statutes_scanned == 1
    assert report.statutes_errored == []
    assert report.statutes_with_findings == 1
    assert report.total_findings == 1
    assert report.reason_counts == {"target_statute_repealed": 1}
    f = report.findings[0]
    assert isinstance(f, StatuteLifecycleFinding)
    assert f.reason is BrokenReason.TARGET_STATUTE_REPEALED
    assert f.target.statute_id == "2010/500"
    assert f.cited_on == date(2024, 1, 1)
    assert f.target_window == (date(2010, 6, 1), date(2015, 4, 1))


def test_scan_does_not_flag_citation_to_live_act() -> None:
    """The SAME cite to a target still in force at the anchor -> no finding."""
    store = _CitingStore({"2022/711": _CITING_BODY})
    # Same target act, but its window is open (still in force) at the anchor.
    lifecycle = _lifecycle_table(
        {"2010/500": StatuteLifecycle(valid_from=date(2010, 6, 1), valid_to=None)}
    )
    report = scan_dangling_citations(
        ["2022/711"],
        store,  # ty: ignore[invalid-argument-type]
        lifecycle_of=lifecycle,
        current_as_of=date(2024, 1, 1),
    )
    assert report.statutes_scanned == 1
    assert report.statutes_with_findings == 0
    assert report.total_findings == 0
    assert report.reason_counts == {}
    # A live target in force is neither a finding nor unverifiable.
    assert report.unverifiable_count == 0


def test_scan_unknown_lifecycle_is_unverifiable_never_broken() -> None:
    """An unknown target lifecycle -> counted unverifiable, never a false BROKEN."""
    store = _CitingStore({"2022/711": _CITING_BODY})
    report = scan_dangling_citations(
        ["2022/711"],
        store,  # ty: ignore[invalid-argument-type]
        lifecycle_of=_lifecycle_table({}),  # nothing known
        current_as_of=date(2024, 1, 1),
    )
    assert report.total_findings == 0
    assert report.unverifiable_count == 1
    assert report.mentions_checked == 1


def test_scan_default_lifecycle_lookup_reads_from_store_no_replay(
    tmp_path, monkeypatch
) -> None:
    """The DEFAULT (uninjected) lookup sources the window from the store XML — no replay.

    Proves the end-to-end wiring: with no ``lifecycle_of`` injected, the scan
    builds ``default_lifecycle_lookup`` over the store, which reads the target's
    source (valid_from) + oracle repealedBy (valid_to) and flags the dangling cite.
    Points the data root at an empty temp dir so no real pre-built artifact is
    picked up — the lookup then uses the per-act oracle path over the store alone.
    """
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))

    class _FullStore:
        def read_oracle(self, sid):
            if sid == "2022/711":
                return _CITING_BODY  # the citing body (as its oracle)
            if sid == "2010/500":
                return _ORACLE_REPEALED  # the repealed target's oracle
            return None

        def read_source(self, sid):
            return _SOURCE_2010 if sid == "2010/500" else None

        def read_amendment(self, sid):
            return None

    report = scan_dangling_citations(
        ["2022/711"],
        _FullStore(),  # ty: ignore[invalid-argument-type]
        current_as_of=date(2024, 1, 1),
    )
    assert report.total_findings == 1
    assert report.reason_counts == {"target_statute_repealed": 1}
    assert report.findings[0].target.statute_id == "2010/500"


def test_default_lifecycle_lookup_builds_without_error(tmp_path, monkeypatch) -> None:
    """``default_lifecycle_lookup`` returns a callable lookup over any store."""
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))

    class _Empty:
        def read_oracle(self, sid):
            return None

        def read_source(self, sid):
            return None

        def read_amendment(self, sid):
            return None

    lookup = default_lifecycle_lookup(_Empty())  # ty: ignore[invalid-argument-type]
    # An id with no corpus XML -> unverifiable (known=False), never a guess.
    assert lookup("123/1900").known is False
