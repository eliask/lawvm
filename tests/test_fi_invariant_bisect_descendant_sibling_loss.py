from __future__ import annotations


def test_fi_2017_646_descendant_sibling_loss_detector_stays_clean_after_fix() -> None:
    from lawvm.tools.invariant_bisect import build_invariant_bisect_bundle

    bundle = build_invariant_bisect_bundle(
        "2017/646",
        mode="legal_pit",
        detector="descendant_sibling_loss",
        target_path="section:2/subsection:1",
    )

    assert bundle["initial_clean"] is True
    assert bundle["first_bad_amendment"] == ""
    assert bundle["first_clean_amendment"] == "2021/1282"
    assert bundle["failure_count"] == 0
    assert bundle["steps"][0] == {
        "source_id": "2018/804",
        "clean": True,
        "violation_count": 0,
        "violations": [],
    }
    assert bundle["steps"][1]["source_id"] == "2021/1282"
    assert bundle["steps"][1]["clean"] is True
    assert bundle["steps"][1]["violation_count"] == 0
    assert bundle["first_bad_violations"] == []
