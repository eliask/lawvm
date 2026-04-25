from __future__ import annotations

from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore


def test_capture_store_load_orders_amendments_numerically(tmp_path) -> None:
    db_path = tmp_path / "pipeline_gold.db"
    store = CaptureStore(str(db_path))
    store.save_batch([
        AmendmentCapture(statute_id="1958/370", amendment_id="2017/1000"),
        AmendmentCapture(statute_id="1958/370", amendment_id="2017/794"),
        AmendmentCapture(statute_id="1958/370", amendment_id="2016/12"),
    ])

    loaded = store.load("1958/370")

    assert [capture.amendment_id for capture in loaded] == [
        "2016/12",
        "2017/794",
        "2017/1000",
    ]
