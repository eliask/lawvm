"""Bulk parse driver: bounded per-PDF concurrency + lane routing (hermetic).

Exercises ``parse_attachments_into_store``'s ThreadPoolExecutor path with the LLM
+ farchive seams faked: correct hit/parsed/failure aggregation across workers, a
per-worker store connection opened + closed inside each task (Farchive is
thread-affine — one connection per thread), typed failure of a bad attachment
(never a crash that sinks the pool), and struct-vs-flat lane routing. No server,
no farchive, no PDF lib.
"""
from __future__ import annotations

from lawvm.finland.source_document.parsed_store import ParsedRecord
from lawvm.tools import fi_parse_attachments as fpa


class _Spec:
    pipeline_id = "adjudicated_vision"
    version = "vX"

    def __init__(self, modality: str) -> None:
        self.transcription_modality = modality


class _FakeStore:
    """Stands in for ParsedIrStore (get/put/put_image/close)."""

    def __init__(self, path: str = "") -> None:
        self.closed = False

    def get(self, loc):
        return None

    def put(self, loc, record):
        return "digest"

    def put_image(self, loc, data, **kw):
        return "digest"

    def close(self) -> None:
        self.closed = True


def _wire(monkeypatch, locs, *, modality: str, parse_fn=None, struct_fn=None):
    import lawvm.finland.source_document.pdf_profiles as pp

    monkeypatch.setattr(fpa, "resolve_pipeline", lambda **kw: _Spec(modality))
    monkeypatch.setattr(fpa, "ParsedIrStore", _FakeStore)
    monkeypatch.setattr(fpa, "iter_finlex_pdf_locators", lambda p, *, kind="all": iter(locs))
    # ``_parse_one`` imports this inside; make the "manifestation" just be the locator.
    monkeypatch.setattr(pp, "load_manifestation_from_farchive", lambda loc, **kw: loc)
    if parse_fn is not None:
        monkeypatch.setattr(fpa, "parse_and_cache", parse_fn)
    if struct_fn is not None:
        monkeypatch.setattr(fpa, "parse_struct_and_cache", struct_fn)


def test_concurrent_run_aggregates_hits_parses_and_typed_failures(monkeypatch) -> None:
    locs = [(f"finlex://x/{i}.pdf", "attachment") for i in range(10)]

    def parse_fn(m, store, *, spec, force=False):
        if m.endswith("/3.pdf"):
            raise ValueError("boom")  # a bad attachment → typed failure, not a crash
        return ParsedRecord(ir={}, manifest={}, cache_hit=m.endswith("/2.pdf"))

    _wire(monkeypatch, locs, modality="full_transcription", parse_fn=parse_fn)
    report = fpa.parse_attachments_into_store(modality="full_transcription", workers=4)

    assert report.scanned == 10
    assert report.failed == 1
    assert report.cache_hits == 1  # only /2.pdf
    assert report.parsed == 8
    assert len(report.failures) == 1 and "ValueError" in report.failures[0]


def test_struct_modality_routes_to_struct_and_cache(monkeypatch) -> None:
    locs = [("finlex://x/0.pdf", "attachment")]
    called = {"flat": 0, "struct": 0}

    def parse_fn(m, store, *, spec, force=False):
        called["flat"] += 1
        return ParsedRecord(ir={}, manifest={}, cache_hit=False)

    def struct_fn(m, store, *, spec, force=False):
        called["struct"] += 1
        return ParsedRecord(ir={}, manifest={}, cache_hit=False)

    _wire(monkeypatch, locs, modality="struct_span", parse_fn=parse_fn, struct_fn=struct_fn)
    report = fpa.parse_attachments_into_store(modality="struct_span", workers=2)

    assert report.parsed == 1
    assert called["struct"] == 1 and called["flat"] == 0  # struct lane, not flat


def test_limit_caps_scanned(monkeypatch) -> None:
    locs = [(f"finlex://x/{i}.pdf", "attachment") for i in range(50)]

    def parse_fn(m, store, *, spec, force=False):
        return ParsedRecord(ir={}, manifest={}, cache_hit=False)

    _wire(monkeypatch, locs, modality="full_transcription", parse_fn=parse_fn)
    report = fpa.parse_attachments_into_store(modality="full_transcription", limit=7, workers=4)
    assert report.scanned == 7 and report.parsed == 7


def test_worker_opens_and_closes_its_own_store_per_task(monkeypatch) -> None:
    # Farchive is thread-affine: each worker task opens its own store connection
    # and closes it in a finally (the in-memory record outlives the close).
    locs = [(f"finlex://x/{i}.pdf", "attachment") for i in range(4)]
    opened: list = []

    class _TrackStore(_FakeStore):
        def __init__(self, path: str = "") -> None:
            super().__init__(path)
            opened.append(self)

    def parse_fn(m, store, *, spec, force=False):
        assert store.closed is False  # store is live during the parse
        return ParsedRecord(ir={}, manifest={}, cache_hit=False)

    _wire(monkeypatch, locs, modality="full_transcription", parse_fn=parse_fn)
    monkeypatch.setattr(fpa, "ParsedIrStore", _TrackStore)
    report = fpa.parse_attachments_into_store(modality="full_transcription", workers=3)

    assert report.parsed == 4
    assert len(opened) == 4  # one store per task
    assert all(s.closed for s in opened)  # each closed in its finally
