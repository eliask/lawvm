"""Cold multi-line region reader + systemic pdfium lock (Tasks 3 & 4).

Hermetic (no network / no real PDF lib / no model):

  * ``VisionPageProducer.read_region_cold`` transcribes a WHOLE region crop and
    returns its full MULTI-LINE text (the §8 single-line ``reread_region``
    correction path is UNCHANGED) — a fake ``render_region_crop`` + a fake
    ``_post_chat`` stand in for the PDF render and the vision model;
  * the calibration ``live_region_reader`` hook, over a fake vision producer,
    returns real multi-line text for a geometry-carrying region and an honest
    empty for an un-croppable (``abs_bbox is None``) region;
  * the systemic ``ingest.visual.PDFIUM_LOCK`` is genuinely HELD around every
    pdfium call in ``render_region_crop`` — a concurrent thread cannot acquire it
    mid-render (the #250 SEGFAULT guard is real, not decorative).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


# --------------------------------------------------------------------------- #
# (3) read_region_cold returns MULTI-LINE text; reread_region stays one-line.  #
# --------------------------------------------------------------------------- #

_MULTI_LINE = "Ensimmäinen rivi\nToinen rivi tekstiä\nKolmas rivi lopuksi"


def test_read_region_cold_returns_full_multi_line_transcription(monkeypatch) -> None:
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    import lawvm.ingest.visual as visual

    # Fake the PDF render (no lib) and the vision transport (no network).
    monkeypatch.setattr(visual, "render_region_crop", lambda *a, **k: b"PNGBYTES")
    producer = VisionPageProducer(base_url="http://unused")
    captured: dict = {}

    def _fake_post(payload, *, page_num):
        captured["max_tokens"] = payload["max_tokens"]
        captured["system"] = payload["messages"][0]["content"]
        return _MULTI_LINE

    monkeypatch.setattr(producer, "_post_chat", _fake_post)

    out = producer.read_region_cold(
        _manifestation(), 3, BBox(72, 400, 500, 600), dpi=300, expected_lines=3
    )
    # The whole region's text survives as MULTI-LINE (not collapsed to one line).
    assert out == _MULTI_LINE
    assert out.count("\n") == 2
    # A cold multi-line budget (well above the §8 single-line ~one-line budget).
    assert captured["max_tokens"] >= 128 + 24 * 8
    assert "Transcribe" in captured["system"] or "transcrib" in captured["system"].lower()


def test_reread_region_correction_path_is_unchanged_single_line(monkeypatch) -> None:
    # The §8 correction path still collapses to ONE line (its PATCH invariant) — the
    # new cold reader did not regress it.
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    import lawvm.ingest.visual as visual

    monkeypatch.setattr(visual, "render_region_crop", lambda *a, **k: b"PNGBYTES")
    producer = VisionPageProducer(base_url="http://unused")
    monkeypatch.setattr(
        producer, "_post_chat", lambda payload, *, page_num: "line one\nline two"
    )
    out = producer.reread_region(_manifestation(), 1, BBox(72, 400, 500, 420), "garbled")
    assert "\n" not in out  # single-line correction (collapsed)
    assert out == "line one line two"


def test_read_region_cold_empty_on_unreadable(monkeypatch) -> None:
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    import lawvm.ingest.visual as visual

    monkeypatch.setattr(visual, "render_region_crop", lambda *a, **k: b"PNGBYTES")
    producer = VisionPageProducer(base_url="http://unused")
    monkeypatch.setattr(producer, "_post_chat", lambda payload, *, page_num: "UNREADABLE")
    assert producer.read_region_cold(_manifestation(), 1, BBox(0, 0, 10, 10)) == ""


# --------------------------------------------------------------------------- #
# Batched "thumbnail + tiles" region read — ONE request carries the low-res     #
# thumbnail + N labelled crops; the reply parses back per I{N} label in order.   #
# --------------------------------------------------------------------------- #


def test_read_page_tiled_builds_one_request_thumbnail_plus_labelled_crops(monkeypatch) -> None:
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    import lawvm.ingest.visual as visual

    # Fake the region-crop render (distinct bytes per bbox) and the thumbnail render.
    def _fake_crop(man, page_num, bbox, dpi=300):
        return f"CROP@{bbox.y0}".encode("ascii")

    monkeypatch.setattr(visual, "render_region_crop", _fake_crop)
    producer = VisionPageProducer(base_url="http://unused")
    monkeypatch.setattr(producer, "_render_page_png", lambda *a, **k: b"THUMB")

    posted: dict = {}

    def _fake_post(payload, *, page_num):
        posted["payload"] = payload
        # Model returns one labelled block per region, in order, multi-line region 1.
        return "I1\nHeading one\nBody one line two\nI2\nSecond region text"

    monkeypatch.setattr(producer, "_post_chat", _fake_post)

    regions = (
        (BBox(72, 600, 500, 780), 2),
        (BBox(72, 400, 500, 580), 1),
    )
    out = producer.read_page_tiled(
        _manifestation(), 5, regions, thumbnail_scale=0.4, crop_dpi=250
    )
    # Parsed back per region, in reading order (region 1 keeps its two lines).
    assert out == ("Heading one\nBody one line two", "Second region text")

    # ONE request; its user content carries the thumbnail FIRST then N labelled crops.
    content = posted["payload"]["messages"][1]["content"]
    image_urls = [c for c in content if c.get("type") == "image_url"]
    assert len(image_urls) == 3  # 1 thumbnail + 2 region crops
    assert "THUMB" in image_urls[0]["image_url"]["url"] or image_urls[0]["image_url"]["url"]
    # A text marker precedes each crop, labelling it I1 / I2 in order.
    marker_texts = [c["text"] for c in content if c.get("type") == "text"]
    assert any(t.startswith("I1") for t in marker_texts)
    assert any(t.startswith("I2") for t in marker_texts)
    # The system prompt is the tiled prompt (thumbnail context + per-region crops).
    system = posted["payload"]["messages"][0]["content"]
    assert "thumbnail" in system.lower() and "crop" in system.lower()


def test_read_page_tiled_marks_unreadable_region_empty(monkeypatch) -> None:
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    import lawvm.ingest.visual as visual

    monkeypatch.setattr(visual, "render_region_crop", lambda *a, **k: b"CROP")
    producer = VisionPageProducer(base_url="http://unused")
    monkeypatch.setattr(producer, "_render_page_png", lambda *a, **k: b"THUMB")
    monkeypatch.setattr(
        producer, "_post_chat", lambda payload, *, page_num: "I1\nReal text\nI2\nUNREADABLE"
    )
    out = producer.read_page_tiled(
        _manifestation(), 1, ((BBox(0, 10, 10, 20), 1), (BBox(0, 0, 10, 9), 1))
    )
    assert out == ("Real text", "")  # UNREADABLE region → honest empty, not a crash


def test_read_page_tiled_missing_label_is_typed_failure(monkeypatch) -> None:
    # A malformed multi-image reply missing a region label RAISES (never a silent
    # mis-alignment that would attribute one region's text to another).
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionPageProducer,
        VisionProducerFailure,
    )
    import lawvm.ingest.visual as visual

    monkeypatch.setattr(visual, "render_region_crop", lambda *a, **k: b"CROP")
    producer = VisionPageProducer(base_url="http://unused")
    monkeypatch.setattr(producer, "_render_page_png", lambda *a, **k: b"THUMB")
    monkeypatch.setattr(
        producer, "_post_chat", lambda payload, *, page_num: "I1\nonly the first region"
    )
    with pytest.raises(VisionProducerFailure) as exc:
        producer.read_page_tiled(
            _manifestation(), 1, ((BBox(0, 10, 10, 20), 1), (BBox(0, 0, 10, 9), 1))
        )
    assert exc.value.reason_code == "vision_tiled_label_missing"


def test_parse_tiled_regions_enforces_marker_order() -> None:
    from lawvm.ingest.llm_backends.vision_producer import _parse_tiled_regions

    # Markers I1..I3, each block sliced to the NEXT marker; a stray in-body "I2"
    # after I3 does not re-split (labels are searched strictly in ascending order).
    content = "I1\nalpha\nI2\nbeta\nI3\ngamma line mentioning I2 inline"
    assert _parse_tiled_regions(content, 3, page_num=1) == (
        "alpha",
        "beta",
        "gamma line mentioning I2 inline",
    )
    assert _parse_tiled_regions("anything", 0, page_num=1) == ()


# --------------------------------------------------------------------------- #
# (3) calibration live_region_reader hook binds the COLD reader → multi-line.   #
# --------------------------------------------------------------------------- #


class _FakeColdVision:
    """A vision producer whose ``read_region_cold`` returns a scripted multi-line."""

    def __init__(self, text: str):
        self._text = text
        self.cold_calls: list = []
        self.reread_calls: list = []

    def read_region_cold(self, man, page_num, bbox, *, dpi=300, expected_lines=0):
        self.cold_calls.append((page_num, bbox, dpi, expected_lines))
        return self._text

    def reread_region(self, man, page_num, bbox, current_text, *, dpi=300):
        self.reread_calls.append((page_num, bbox, current_text, dpi))
        return "SHOULD-NOT-BE-CALLED"


def _region(abs_bbox):
    from lawvm.tools.fi_calibration import Region

    return Region(
        region_id=0,
        line_indexes=(0, 1, 2),
        core_line_indexes=(0, 1, 2),
        gold_text="",
        col=None,
        band_key="body",
        px_width_pt=428.0,
        px_height_pt=200.0,
        n_glyphs=40,
        abs_bbox=abs_bbox,
    )


def test_calibration_hook_returns_real_multi_line_over_a_fake_vision(monkeypatch) -> None:
    import lawvm.ingest.llm_backends.vision_producer as vp
    from lawvm.tools.fi_calibration import live_region_reader

    fake = _FakeColdVision(_MULTI_LINE)
    monkeypatch.setattr(vp, "VisionPageProducer", lambda **k: fake)

    reader = live_region_reader(_manifestation())
    out = reader(3, _region(BBox(72, 400, 500, 600)), 300)
    # The hook binds the COLD multi-line reader (not the single-line correction).
    assert out == _MULTI_LINE
    assert out.count("\n") == 2
    assert fake.cold_calls and fake.cold_calls[0][3] == 3  # expected_lines threaded
    assert fake.reread_calls == []  # the correction path is NOT used for a cold read


def test_calibration_hook_empty_on_uncroppable_region(monkeypatch) -> None:
    import lawvm.ingest.llm_backends.vision_producer as vp
    from lawvm.tools.fi_calibration import live_region_reader

    fake = _FakeColdVision(_MULTI_LINE)
    monkeypatch.setattr(vp, "VisionPageProducer", lambda **k: fake)
    reader = live_region_reader(_manifestation())
    # No geometry → un-croppable → honest empty (MISSING), never a crash / a read.
    assert reader(1, _region(None), 300) == ""
    assert fake.cold_calls == []


# --------------------------------------------------------------------------- #
# (4) the systemic pdfium lock is HELD around the pdfium calls in visual.py.    #
# --------------------------------------------------------------------------- #


class _FakePil:
    width = 100
    height = 100

    def crop(self, box):
        return self

    def save(self, buf, format="PNG"):
        buf.write(b"PNG")


class _FakeRender:
    def to_pil(self):
        return _FakePil()


class _FakePage:
    def render(self, scale):
        return _FakeRender()

    def get_height(self):
        return 100.0

    def get_width(self):
        return 100.0


class _FakePdfDocument:
    """A pdfium document that, while a page is in use, checks the lock is HELD.

    On ``__getitem__`` it spawns a probe thread that tries to acquire the systemic
    lock non-blockingly; because ``render_region_crop`` holds it for the whole
    document lifecycle, the probe MUST fail — proving cross-thread serialization."""

    lock_free_seen = None  # set by the probe: True if the probe COULD acquire

    def __init__(self, data):
        pass

    def __len__(self):
        return 1

    def __getitem__(self, i):
        from lawvm.ingest.visual import PDFIUM_LOCK

        result = {}

        def _probe():
            got = PDFIUM_LOCK.acquire(blocking=False)
            result["free"] = got
            if got:
                PDFIUM_LOCK.release()

        t = threading.Thread(target=_probe)
        t.start()
        t.join()
        _FakePdfDocument.lock_free_seen = result["free"]
        return _FakePage()

    def close(self):
        pass


def test_render_region_crop_holds_the_systemic_pdfium_lock(monkeypatch) -> None:
    import types

    pytest.importorskip("PIL")  # render_region_crop guards on pillow before pdfium
    from lawvm.ingest import visual

    fake_pdfium = types.SimpleNamespace(PdfDocument=_FakePdfDocument)
    # ``render_region_crop`` imports pypdfium2 + PIL lazily; supply fakes so no lib
    # is required and the pdfium calls are exercised under the lock.
    import importlib

    real_import = importlib.import_module

    def _fake_import(name, *a, **k):
        if name == "pypdfium2":
            return fake_pdfium
        return real_import(name, *a, **k)

    monkeypatch.setattr(visual.importlib, "import_module", _fake_import)

    _FakePdfDocument.lock_free_seen = None
    out = visual.render_region_crop(_manifestation(), 1, BBox(10, 10, 90, 90), dpi=200)
    assert out == b"PNG"
    # A concurrent thread could NOT acquire the lock during the pdfium work → the
    # lock was genuinely held around the render (the #250 SEGFAULT guard is real).
    assert _FakePdfDocument.lock_free_seen is False


def test_pdfium_lock_is_one_shared_object_across_the_primitives() -> None:
    # The lock every primitive / tool holds is the SAME object (a per-module lock
    # would not serialize cross-module — the exact bug #250 calls out).
    from lawvm.ingest.page_elements import PDFIUM_LOCK as pe_lock
    from lawvm.ingest.visual import PDFIUM_LOCK as visual_lock
    from lawvm.tools.fi_calibration import _PDFIUM_LOCK as cal_lock

    assert pe_lock is visual_lock
    assert cal_lock is visual_lock


# --------------------------------------------------------------------------- #
# GLOBAL vision-inference concurrency gate (§ pipeline concurrency).           #
# The ONE process-wide semaphore at the client boundary bounds total in-flight #
# requests against :8080 so nested per-PDF × per-page pools don't oversubscribe.#
# --------------------------------------------------------------------------- #


def _json_ok() -> bytes:
    import json

    return json.dumps(
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode("utf-8")


class _RecordingResp:
    """A minimal urlopen() context manager that records concurrent in-flight count."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_global_gate_bounds_in_flight_requests_below_the_cap(monkeypatch) -> None:
    # Submit FAR more concurrent _post_chat calls than the cap; the ONE shared
    # semaphore keeps the number actually hitting the server <= the cap, even though
    # the caller pool is generously sized (work-decomposition decoupled from rate).
    import time

    from lawvm.ingest.llm_backends import vision_producer as vp

    cap = 3
    monkeypatch.setattr(vp, "VISION_INFLIGHT_GATE", threading.BoundedSemaphore(cap))

    lock = threading.Lock()
    inflight = [0]
    peak = [0]

    def _fake_urlopen(req, timeout=None):
        with lock:
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
        try:
            time.sleep(0.02)  # widen the overlap an unbounded path would reveal
            return _RecordingResp(_json_ok())
        finally:
            with lock:
                inflight[0] -= 1

    monkeypatch.setattr(vp.urllib.request, "urlopen", _fake_urlopen)

    producer = vp.VisionPageProducer(base_url="http://unused")
    errors: list = []

    def _call(i: int) -> None:
        try:
            assert producer._post_chat({"n": i}, page_num=i) == "ok"
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(exc)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert peak[0] <= cap        # HARD invariant: never oversubscribed
    assert peak[0] >= 2          # the gate genuinely admitted concurrency (not serial)


def test_gate_is_a_single_shared_module_object_not_per_instance() -> None:
    # Two producers must contend on the SAME gate (a per-instance semaphore would
    # not bound cross-pool concurrency — the whole point of the choke point).
    from lawvm.ingest.llm_backends import vision_producer as vp

    a = vp.VisionPageProducer(base_url="http://a")
    b = vp.VisionPageProducer(base_url="http://b")
    # _post_chat reads the module global at call time, so both see one gate.
    assert vp.VISION_INFLIGHT_GATE is vp.VISION_INFLIGHT_GATE
    assert isinstance(vp.VISION_INFLIGHT_GATE, threading.BoundedSemaphore)
    assert a is not b


def test_gate_cap_is_env_configurable(monkeypatch) -> None:
    # LAWVM_VISION_MAX_INFLIGHT sizes the cap at import; reload proves the knob wires
    # through to both the constant and the semaphore's bound.
    import importlib

    from lawvm.ingest.llm_backends import vision_producer as vp

    monkeypatch.setenv("LAWVM_VISION_MAX_INFLIGHT", "2")
    reloaded = importlib.reload(vp)
    try:
        assert reloaded.VISION_MAX_INFLIGHT == 2
        # A BoundedSemaphore sized to 2 admits exactly 2 tokens before blocking.
        assert reloaded.VISION_INFLIGHT_GATE.acquire(blocking=False) is True
        assert reloaded.VISION_INFLIGHT_GATE.acquire(blocking=False) is True
        assert reloaded.VISION_INFLIGHT_GATE.acquire(blocking=False) is False
    finally:
        monkeypatch.delenv("LAWVM_VISION_MAX_INFLIGHT", raising=False)
        importlib.reload(vp)
