"""Regression: invariant-bisect must replay authoritatively (chapter-seeding).

§136a of 1958/370 is inserted by 1973/589 as ``INSERT 15 luku 136a §``.  The
full replay seeds chapter 15 so the section lands; a lightweight fold that
skips seeding loses §136a and then reports false structural/occupancy
violations for every later amendment that touches it (e.g. the 1992/1167
repeal of §136a, the 1987/979 repeal of §136 momentti 2).  invariant-bisect
drives the authoritative ``replay_xml`` path, so those false alarms must not
appear.
"""
from __future__ import annotations


def test_invariant_bisect_no_false_occupancy_for_seeded_section() -> None:
    from lawvm.tools.invariant_bisect import build_invariant_bisect_bundle

    bundle = build_invariant_bisect_bundle(
        "1958/370",
        mode="legal_pit",
        detector="mixed_hierarchy",
    )

    # No step may report a violation that mentions §136a — the section is
    # materialized by the authoritative replay (chapter-15 seeding), so any
    # "136a is absent" / "section not found" message would be a false alarm
    # from a lightweight fold that skipped seeding.
    all_messages = list(bundle["initial_violations"])
    for step in bundle["steps"]:
        all_messages.extend(step["violations"])
    offending = [m for m in all_messages if "136a" in m]
    assert offending == [], f"unexpected §136a violations: {offending}"


def test_invariant_bisect_still_reports_genuine_mixed_hierarchy() -> None:
    """The genuine 152-alongside-part:4 mixed_hierarchy must still fire.

    This violation is present in the authoritative full-replay tree, so the
    authoritative-replay rewrite must NOT silence it — proof we aligned
    fidelity rather than suppressing detections.
    """
    from lawvm.tools.invariant_bisect import build_invariant_bisect_bundle

    bundle = build_invariant_bisect_bundle(
        "1958/370",
        mode="legal_pit",
        detector="mixed_hierarchy",
    )

    assert bundle["initial_clean"] is False
    genuine = [
        m
        for m in bundle["initial_violations"]
        if "section:152" in m and "part:4" in m
    ]
    assert genuine, (
        "expected the genuine section:152/part:4 mixed_hierarchy violation "
        f"to still fire; saw: {bundle['initial_violations']}"
    )
