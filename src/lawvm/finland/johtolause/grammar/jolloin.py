"""jolloin — the consequence-renumber (``jolloin``) emission family.

A *driver-level* family of the combinator-based replacement for
``surface_parse.py``. Unlike the structural-target families (``sections``,
``insertions``, …) there is no context-free recognizer here: the ``jolloin``
data is not read off the cursor at all. It is pre-extracted by ``scan.py`` into
the ``jolloin_renumber_pairs`` map and threaded into ``parse()`` as a side
channel.

A clause like::

    Muutetaan … 5 §, jolloin nykyinen 6 § siirtyy 7 §:ksi

carries a ``JOLLOIN_MOVE`` sentinel token whose position keys into
``jolloin_renumber_pairs`` ``{token_pos: [(src, dst, pair_kind), …]}`` where
``pair_kind`` ∈ {``"M"`` = momentti, else a structural kind code ``P``/``L``/
``O``/``N``/``A`` per :class:`TargetKind`}. The old parser, after walking all
source-order verb groups, builds ONE synthetic SIIRTAA verb group from the
``JOLLOIN_MOVE`` positions it consumed and **prepends** it at index 0 (before
every source-order group), with every node carrying the witness
``fi.jolloin_renumber``.

This module is the standalone *builder* for that prepended group. The driver
wiring (calling it inside ``parse()`` and prepending the result) is done later
at integration — see :func:`build_jolloin_group` for the exact interface.

Why this is driver-level, not a recognizer
-------------------------------------------
The ``"M"`` (momentti) pairs render as a section target whose label/chapter is
the *anchor section* — the last section reference seen in the verb group the
``JOLLOIN_MOVE`` trails. That anchor is cross-verb-group / cross-batch context
the driver tracks as it parses; it is NOT in the ``pairs`` map. The old parser
captures it per consumed ``JOLLOIN_MOVE`` position as
``(context_section, context_chapter)`` (its ``consumed_jolloin_contexts``). This
builder therefore takes the consumed positions + their captured contexts as
inputs and performs ONLY the pure node construction — byte-identical to the old
parser's prepend block.

Witness ``rule_id`` emitted here (the closed set for this family):
``fi.jolloin_renumber``.
"""

from __future__ import annotations

from lawvm.finland.johtolause.jolloin_pair import JolloinRenumberPair
from lawvm.finland.johtolause.surface_model import (
    SurfaceNode,
    SurfaceRenumberTail,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)

# Per-consumed-position anchor context: (context_section, context_chapter).
JolloinContext = tuple[str, str]


def _surface_target_kind_for_pair_kind(pair_kind: str) -> TargetKind:
    """Map the pair-kind code from scan annotations to a surface TargetKind.

    Faithful port of ``surface_parse._surface_target_kind_for_pair_kind``: an
    unknown code falls back to SECTION (the dominant structural kind) rather than
    raising — the old parser is lenient here.
    """
    try:
        return TargetKind.from_code(pair_kind)
    except ValueError:
        return TargetKind.SECTION


def build_jolloin_nodes(
    consumed_positions: list[int],
    jolloin_renumber_pairs: dict[int, list[JolloinRenumberPair]],
    jolloin_contexts: dict[int, JolloinContext] | None = None,
) -> list[SurfaceNode]:
    """Build the ordered SurfaceTargetRef + SurfaceRenumberTail node list.

    The pure node-construction half of the prepended jolloin group, byte-identical
    to ``surface_parse.parse``'s native-jolloin block. For each consumed
    ``JOLLOIN_MOVE`` position (in driver-consumption order), each
    pair emits:

      * a ``SurfaceTargetRef`` —
          - for a ``"M"`` (momentti) pair: a SECTION target at the anchor section
            (``context_section`` / ``context_chapter`` for that position) with a
            single ``SurfaceSubRef(momentti=int(src))`` and ``notes=("renumber_clause",)``.
            **Skipped entirely (target AND tail) when there is no anchor section**
            — exactly as the old parser ``continue``s past an M pair with empty
            ``context_section``.
          - otherwise: a bare target of the pair's structural kind, label=``src``,
            ``notes=("renumber_clause",)`` (no chapter, no sub-refs).
      * a ``SurfaceRenumberTail(new_label=dst)``.

    Every node's witness is ``SurfaceWitness(rule_id="fi.jolloin_renumber")`` with
    ``source_span=None`` (the old parser attaches no span to these synthetic
    nodes).

    Args:
        consumed_positions: ``JOLLOIN_MOVE`` filtered-stream positions the driver
            consumed, in consumption order (the old parser's
            ``consumed_jolloin_positions``).
        jolloin_renumber_pairs: the ``scan.py`` map ``{pos: [(src, dst, kind), …]}``.
        jolloin_contexts: per-position anchor context ``{pos: (section, chapter)}``
            (the old parser's ``consumed_jolloin_contexts`` flattened to a map).
            A position absent from the map (or a None map) is treated as the empty
            context ``("", "")`` — matching the old ``.get(jm_pos, ("", ""))``.

    Returns:
        The flat node list (possibly empty, e.g. all-M pairs with no anchor).
    """
    context_map = jolloin_contexts or {}
    nodes: list[SurfaceNode] = []
    for jm_pos in consumed_positions:
        pairs = jolloin_renumber_pairs.get(jm_pos, [])
        context_section, context_chapter = context_map.get(jm_pos, ("", ""))
        for pair in pairs:
            if pair.kind == "M":
                if not context_section:
                    # No anchor section for this momentti renumber: the old parser
                    # drops the whole pair (target and its tail).
                    continue
                nodes.append(
                    SurfaceTargetRef(
                        kind=TargetKind.SECTION,
                        label=context_section,
                        chapter=context_chapter,
                        sub_refs=(SurfaceSubRef(momentti=int(pair.source_label)),),
                        notes=("renumber_clause",),
                        witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                    )
                )
            else:
                target_kind = _surface_target_kind_for_pair_kind(pair.kind)
                nodes.append(
                    SurfaceTargetRef(
                        kind=target_kind,
                        label=pair.source_label,
                        notes=("renumber_clause",),
                        renumber_dest_chapter=pair.destination_chapter,
                        renumber_dest_part=pair.destination_part,
                        witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                    )
                )
            nodes.append(
                SurfaceRenumberTail(
                    new_label=pair.destination_label,
                    witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                )
            )
    return nodes


def build_jolloin_group(
    consumed_positions: list[int],
    jolloin_renumber_pairs: dict[int, list[JolloinRenumberPair]],
    jolloin_contexts: dict[int, JolloinContext] | None = None,
) -> SurfaceVerbGroup | None:
    """Build the synthetic SIIRTAA verb group the old parser prepends, or None.

    Thin wrapper over :func:`build_jolloin_nodes`. Returns a
    ``SurfaceVerbGroup(verb=VerbKind.SIIRTAA, nodes=…)`` when at least one node was
    produced, else ``None`` — matching the old parser's ``if renumber_nodes:``
    guard (no empty group is prepended).

    Driver wiring (the integration step done later in ``grammar/parser.py``)
    ---------------------------------------------------------------------------
    ``parse()`` currently raises ``OutOfScope`` whenever ``jolloin_renumber_pairs``
    is truthy. To wire this family in, replace that guard with the same
    consume-then-prepend protocol the old parser uses:

      1. Stop raising ``OutOfScope`` on ``jolloin_renumber_pairs``. Initialise two
         accumulators (only when ``jolloin_renumber_pairs is not None``)::

             consumed_jolloin_positions: list[int] = []
             consumed_jolloin_contexts: dict[int, tuple[str, str]] = {}

      2. In the verb-group / target-list loop, when the cursor is at a
         ``JOLLOIN_MOVE`` token at position ``jm_pos`` (today these fall through as
         benign trailing trivia — they are in ``_BENIGN_TAIL_CATS``), if
         ``jolloin_renumber_pairs is not None and jm_pos in jolloin_renumber_pairs``:
         record ``jm_pos`` in ``consumed_jolloin_positions`` (in consumption order),
         and capture the anchor context for ``jm_pos`` from the just-parsed batch
         (the last SECTION-bearing node's ``(label, chapter)`` — the old parser's
         ``_extract_section_context_from_nodes`` over ``last_batch or all_nodes``;
         port that extractor narrowed to the wired families). Store it as
         ``consumed_jolloin_contexts[jm_pos] = (context_section, context_chapter)``.
         Advance past the ``JOLLOIN_MOVE`` token (it contributes to
         ``consumed_count``, as it does today).

      3. After ALL source-order verb groups are collected, build and prepend::

             jolloin_vg = build_jolloin_group(
                 consumed_jolloin_positions,
                 jolloin_renumber_pairs,
                 consumed_jolloin_contexts,
             )
             if jolloin_vg is not None:
                 verb_groups = [jolloin_vg] + verb_groups

         (Index-0 prepend, before the totality / ``consumed_count`` checks.)

    Note that the ``"M"``-pair anchor context is genuine cross-batch/cross-verb
    state the driver must thread — which is why this family is driver-level and
    the builder takes the contexts as an explicit argument rather than reading the
    cursor. A position with no captured context renders as the empty context
    ``("", "")``, which drops its ``"M"`` pairs (no anchor) exactly as the old
    parser does.
    """
    nodes = build_jolloin_nodes(consumed_positions, jolloin_renumber_pairs, jolloin_contexts)
    if not nodes:
        return None
    return SurfaceVerbGroup(verb=VerbKind.SIIRTAA, nodes=tuple(nodes))
