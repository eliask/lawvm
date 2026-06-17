"""Consonant gradation --- cluster-realization RULES + small lexical flags.

Gradation alternates the *strong* grade (in NOM / open-syllable forms) with the
*weak* grade (before a suffix that closes the final syllable, e.g. GEN -n, INE
-ssa, ELA, ADE, ABL, ALL, the singular genitive-based oblique stem).

Two things are irreducibly lexical and therefore stored as flags on the
:class:`~lawvm.finland.morphology.api.MorphEntry`:

1.  Whether gradation *occurs* at all (``gradation`` boolean).  ``virasto`` and
    ``direktiivi`` do not gradate; ``lautakunta`` does.
2.  The realization of a *single* ``k`` (``k -> zero | v | j``), which is
    lexically conditioned (``Turku -> Turun`` zero; ``laki -> lain`` zero).

Everything else --- the quantitative clusters (kk/pp/tt) and the assimilative
clusters (nt->nn, lt->ll, rt->rr, mp->mm, nk->ng, t->d, p->v) --- is a pure rule
keyed on the final consonant cluster of the stem.
"""

from __future__ import annotations

# Quantitative + assimilative clusters: strong-grade ending -> weak-grade ending.
# Ordered longest-first so two-consonant clusters win over the bare single stop.
_CLUSTER_RULES: tuple[tuple[str, str], ...] = (
    ("kk", "k"),
    ("pp", "p"),
    ("tt", "t"),
    ("nt", "nn"),
    ("lt", "ll"),
    ("rt", "rr"),
    ("mp", "mm"),
    ("nk", "ng"),
    ("t", "d"),
    ("p", "v"),
)


def weaken_stem(stem: str, *, gradation: bool, single_k: str | None) -> str:
    """Return the weak-grade form of ``stem`` (the part before the vowel).

    ``stem`` is the bare consonant-bearing stem ending in its final cluster, e.g.
    ``"lautakunt"`` -> ``"lautakunn"``, ``"Helsink"`` -> ``"Helsing"``.

    A single ``k`` is handled first via the lexical ``single_k`` flag (the only
    place we consult stored data); all other clusters fall through to the pure
    rule table.  If ``gradation`` is False the stem is returned unchanged.
    """
    if not gradation:
        return stem

    # Single-k realization is lexically conditioned -> consult the flag.
    if single_k is not None and _ends_in_single_k(stem):
        base = stem[:-1]
        if single_k == "zero":
            return base
        if single_k == "v":
            return base + "v"
        if single_k == "j":
            return base + "j"
        msg = f"unknown single_k realization {single_k!r}"
        raise ValueError(msg)

    for strong, weak in _CLUSTER_RULES:
        if stem.endswith(strong):
            # Guard kk/pp/tt against matching the bare single-stop rule.
            return stem[: -len(strong)] + weak

    return stem


def _ends_in_single_k(stem: str) -> bool:
    """True if ``stem`` ends in a single (non-geminate) ``k``."""
    return stem.endswith("k") and not stem.endswith("kk")


__all__ = ["weaken_stem"]
