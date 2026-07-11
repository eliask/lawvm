"""Dual-registration completeness gates (Lane E).

Several LawVM registries are "register X in two places": a central registry dict
plus per-jurisdiction registration calls made as an import side effect. When the
two drift — a jurisdiction ships a bench but forgets to register its comparator,
or registers an oracle normalizer but not the presentation detector — the gap is
silent (a ``.get(...)`` falls back to a default, or a ``KeyError`` only surfaces
on the one code path that dispatches it).

This module makes the two genuinely *unguarded* dual registrations
self-evidencing (the other registries are either single-source/derived or
already guarded — see ``notes/DISCIPLINE_GATES.md`` for the full audit table):

1. **Bench comparator registry** — every jurisdiction that ships a bench
   comparator module must register a comparator, and the registered set must be
   exactly the expected jurisdiction set (no missing, no surprise).

2. **Projection detector registries** — the oracle-text-normalizer and
   presentation-structural-diff-detector registries are registered together by
   the same jurisdiction (Finland); the gate pins that they do not drift apart.

The gates import the jurisdiction modules explicitly so the registration import
side effects fire deterministically, regardless of collection order.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. Bench comparator registry.
# ---------------------------------------------------------------------------

#: Every jurisdiction that ships a bench comparator. Adding a comparator without
#: adding it here (or vice versa) fails the gate — the single edit that keeps the
#: dual registration honest.
_EXPECTED_BENCH_JURISDICTIONS = frozenset(
    {"fi", "us", "uk", "nz", "ee", "se", "eu", "no"}
)


def _import_all_bench_modules() -> None:
    """Trigger each jurisdiction's ``register_bench_comparator`` import side effect."""
    import lawvm.eu.eu_oracle_divergence  # noqa: F401  (eu)
    import lawvm.tools.bench  # noqa: F401  (fi)
    import lawvm.tools.ee_bench  # noqa: F401
    import lawvm.tools.no_bench  # noqa: F401
    import lawvm.tools.nz_bench  # noqa: F401
    import lawvm.tools.se_bench  # noqa: F401
    import lawvm.tools.uk_bench  # noqa: F401
    import lawvm.us_federal.bench  # noqa: F401


def test_every_expected_jurisdiction_has_a_bench_comparator() -> None:
    """No jurisdiction in the expected set is missing its comparator registration."""
    _import_all_bench_modules()
    from lawvm.core.bench_comparator_registry import registered_jurisdictions

    registered = set(registered_jurisdictions())
    missing = sorted(_EXPECTED_BENCH_JURISDICTIONS - registered)
    assert not missing, (
        f"jurisdiction(s) {missing} ship a bench but registered no comparator — "
        "the registry and the bench module drifted (a dispatch on these keys would "
        "KeyError, or worse, be skipped)"
    )


def test_no_surprise_bench_comparator_registrations() -> None:
    """Every registered comparator is an expected jurisdiction (no silent extras)."""
    _import_all_bench_modules()
    from lawvm.core.bench_comparator_registry import registered_jurisdictions

    registered = set(registered_jurisdictions())
    extra = sorted(registered - _EXPECTED_BENCH_JURISDICTIONS)
    assert not extra, (
        f"unexpected bench comparator registration(s) {extra} — update "
        "_EXPECTED_BENCH_JURISDICTIONS if this is intentional, otherwise a stray "
        "registration drifted in"
    )


def test_registered_comparators_are_callable_and_keyed() -> None:
    """Each registered jurisdiction resolves to a callable comparator (fail-loud key)."""
    _import_all_bench_modules()
    from lawvm.core.bench_comparator_registry import get_bench_comparator

    for j in sorted(_EXPECTED_BENCH_JURISDICTIONS):
        fn = get_bench_comparator(j)
        assert callable(fn), (j, fn)


def test_bench_comparator_missing_key_fails_loud() -> None:
    """A jurisdiction with no comparator must KeyError, not silently default."""
    import pytest

    from lawvm.core.bench_comparator_registry import get_bench_comparator

    with pytest.raises(KeyError, match="no bench comparator registered"):
        get_bench_comparator("no_such_jurisdiction")


# ---------------------------------------------------------------------------
# 2. Projection detector registries.
# ---------------------------------------------------------------------------

#: The jurisdictions expected to register each projection detector. Finland is
#: the only jurisdiction with a Finlex oracle, so it owns all three; the gate
#: pins that the oracle-text-normalizer and presentation-detector registries do
#: not drift apart (a jurisdiction registering one but not the other).
_EXPECTED_ORACLE_NORMALIZER_JURISDICTIONS = frozenset({"fi"})
_EXPECTED_PRESENTATION_DETECTOR_JURISDICTIONS = frozenset({"fi"})
_EXPECTED_INLINE_REPEAL_STUB_JURISDICTIONS = frozenset({"fi"})


def _import_projection_registrants() -> None:
    import lawvm.finland.inline_repeal_stub  # noqa: F401
    import lawvm.finland.oracle_comparison  # noqa: F401


def test_projection_detector_registries_do_not_drift() -> None:
    """Each projection detector registry has exactly its expected jurisdiction set."""
    _import_projection_registrants()
    from lawvm.semantic import projection

    oracle = set(projection._ORACLE_TEXT_NORMALIZERS)
    presentation = set(projection._PRESENTATION_STRUCTURAL_DIFF_DETECTORS)
    inline_repeal = set(projection._INLINE_REPEAL_STUB_DETECTORS)

    assert oracle == set(_EXPECTED_ORACLE_NORMALIZER_JURISDICTIONS), oracle
    assert presentation == set(_EXPECTED_PRESENTATION_DETECTOR_JURISDICTIONS), presentation
    assert inline_repeal == set(_EXPECTED_INLINE_REPEAL_STUB_JURISDICTIONS), inline_repeal

    # The oracle-normalizer and presentation-detector registries are registered
    # together (same jurisdiction owns both); pin that they stay in lockstep so a
    # future jurisdiction cannot register one and silently omit the other.
    assert oracle == presentation, (
        "oracle-text-normalizer and presentation-structural-diff-detector "
        f"registries drifted: oracle={sorted(oracle)} presentation={sorted(presentation)}"
    )
