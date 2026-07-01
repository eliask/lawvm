"""European Union (EU) adapter for the witness-attribution spec-discovery ledger.

This is the EU sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral core,
and of the FI / UK / EE / NO / US / NZ adapters. It reuses that core read-only
(``DivergenceRow`` -> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and
turns EU's replay-vs-consolidation surface (``eu_oracle_divergence``) into neutral
ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("eu", ...)`` and
the ``-j eu`` CLI dispatch through the registry without the core importing this package.

EU reconstructs a point-in-time body by applying amending acts' ops to the base, and the
EUR-Lex SECTOR-0 consolidation is the Office's editorial rendering of the same PIT. They
CHECK each other (``eu_oracle_divergence.compare_replay_to_consolidation``): agreement
corroborates, per-article divergence is a first-class finding, never auto-repaired. The
consolidation has "no legal value" (EUR-Lex) — the ``authoritative oracle ≠ correct``
regime — so an article the consolidation carries that replay has not reconstructed is a
``manual_frontier`` (``missing_source``), not our bug.

Firings come from two surfaces:

* the compiled ops' ``witness_rule_id`` — the ``EU_FMX4.*`` grammar rules that produced
  each op. These are NOT in ``_EU_RULE_SPECS`` (that catalog holds the ``eu_*`` typed
  diagnostics), so they render as loud uncataloged ``·`` rows — the ledger's blind-spot
  frontier (the grammar-rule spec is not yet enumerated as prose);
* the per-op **adjudications** (``eu_replay_*`` reason codes / grammar diagnostics),
  each a named, cataloged hypothesis.

Divergences come from the per-article ``ArticleDivergence`` ledger; each non-agreement
kind maps to a witness disposition through the frontend's own corpus divergence-class
vocabulary (``eu_oracle_divergence._KIND_TO_CLASS``).

EU replay + consolidation acquisition go through the EUR-Lex Cellar REST lane, so a
statute whose replay or consolidation cannot be acquired (offline / 5xx / parse failure)
is SKIPPED and counted as an error by ``run_ledger`` — the dispatch never raises. When
the Cellar lane is reachable, the full per-article ledger is produced.

Run:  uv run python -m lawvm.tools.spec_ledger -j eu 32001R0044 [more celexes ...]
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterator, List, Mapping

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# The fixed as-of for the EU replay-vs-consolidation comparison. The consolidation
# oracle is acquired for this PIT; a recent date requests the current consolidation.
_EU_COMPARE_AS_OF = "2024-01-01"

# EU per-article divergence CORPUS CLASS -> witness disposition. The per-article
# ``ArticleDivergence.kind`` is projected through the frontend's own
# ``eu_oracle_divergence._KIND_TO_CLASS`` (text_divergence -> text_diff etc.), and this
# map translates that corpus class into the neutral disposition. Discipline mirrors the
# UK/EE precedent: ``deterministic_gap`` (replay knows an article the editorial
# consolidation omits) is ``lawvm_wrong`` (our replay surplus / the falsifying case);
# ``manual_frontier`` (the consolidation carries an article replay has not reconstructed)
# is ``missing_source`` (the source did not deterministically specify it); ``text_diff``
# (both carry the article, text differs) stays loud ``unknown`` pending per-text
# analysis; ``oracle_suspect`` (a caller-flagged known editorial artifact) is
# ``oracle_suspect``. ``agreement`` never reaches a divergence row.
_EU_CLASS_DISPOSITION: Dict[str, WitnessDisposition] = {
    "agreement": "unknown",  # filtered before disposition (corroboration, not divergence)
    "text_diff": "unknown",
    "deterministic_gap": "lawvm_wrong",
    "manual_frontier": "missing_source",
    "oracle_suspect": "oracle_suspect",
}


def _load_eu_rule_specs() -> Dict[str, str]:
    """Believed-spec catalog authored by a sibling agent; {} if absent.

    Holds the ``eu_*`` typed-diagnostic hypotheses. The ``EU_FMX4.*`` grammar
    witness_rule_ids are deliberately NOT here — they render as loud uncataloged ``·``
    rows (the grammar-rule spec frontier).
    """
    try:
        from lawvm.tools.spec_ledger_eu_catalog import _EU_RULE_SPECS
    except ImportError:
        return {}
    return dict(_EU_RULE_SPECS)


_EU_RULE_SPECS: Dict[str, str] = _load_eu_rule_specs()


def _kind_to_class() -> Mapping[str, str]:
    """The frontend's per-article kind -> corpus divergence class table."""
    from lawvm.eu.eu_oracle_divergence import _KIND_TO_CLASS

    return _KIND_TO_CLASS


def eu_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn EU's replay-vs-consolidation surface into neutral ledger inputs.

    ``sids`` are base-act CELEX ids. ``mode`` is accepted for signature parity; the EU
    oracle-check compares the native replay against the sector-0 consolidation at the
    fixed compare as-of. Firings come from ``op.witness_rule_id`` over the compiled ops
    plus the per-op adjudication reason codes; divergences come from the per-article
    ``compare_replay_to_consolidation`` ledger. A CELEX whose replay or consolidation
    cannot be acquired is skipped (counted as an error by ``run_ledger``).
    """
    from lawvm.eu.eu_consolidation_oracle import build_consolidation_oracle
    from lawvm.eu.pipeline import EUReplayPipeline

    kind_to_class = _kind_to_class()
    pipeline = EUReplayPipeline()
    fetch_consolidation = _make_consolidation_fetcher()

    for sid in sids:
        try:
            replay = pipeline.replay_statute(sid, cutoff_date=_EU_COMPARE_AS_OF)
        except Exception:
            continue  # Cellar acquisition / replay failure: caller counts errors
        if replay.error or replay.replayed is None:
            continue

        firings: Dict[str, int] = defaultdict(int)
        for op in replay.ops:
            rid = getattr(op, "witness_rule_id", "") or ""
            if rid:
                firings[rid] += 1
        for adjudication in replay.adjudications:
            kind = _eu_adjudication_kind(adjudication)
            if kind:
                firings[kind] += 1

        try:
            comparison = build_consolidation_oracle(
                replay.replayed,
                base_celex=sid,
                as_of=_EU_COMPARE_AS_OF,
                fetch_consolidation=fetch_consolidation,
            )
        except Exception:
            # No consolidation oracle acquired (offline / 5xx / parse failure): emit
            # the firings without divergences rather than dropping the whole statute,
            # so the grammar-rule firing account is still recorded.
            yield StatuteLedgerInput(
                sid=sid, rule_firings=dict(firings), divergences=[]
            )
            continue

        divergences: List[DivergenceRow] = []
        for article in comparison.divergences:
            if article.agrees:
                continue
            corpus_class = kind_to_class.get(article.kind, article.kind)
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=f"article:{article.article_label}",
                    diagnosis=corpus_class,
                    disposition=disposition_for(corpus_class, _EU_CLASS_DISPOSITION),
                    rule_id=None,  # per-article divergence not pinned to a witness op
                    blame_source="",  # EU consolidation is the oracle, not a blamed source
                )
            )
        yield StatuteLedgerInput(
            sid=sid, rule_firings=dict(firings), divergences=divergences
        )


def _eu_adjudication_kind(adjudication: Any) -> str:
    if isinstance(adjudication, Mapping):
        return str(adjudication.get("kind") or adjudication.get("reason_code") or "")
    return str(getattr(adjudication, "kind", "") or getattr(adjudication, "reason_code", "") or "")


def _make_consolidation_fetcher():
    """Build ``fetch_consolidation(consolidated_celex) -> fmx4_bytes`` over the Cellar
    REST lane. Raises on any acquisition failure (the ``build_consolidation_oracle``
    contract wraps it into a typed failure; the caller skips the statute)."""

    def _fetch(consolidated_celex: str) -> bytes:
        from lawvm.eu import cellar

        notice = cellar.NoticeRequest(
            celex=consolidated_celex,
            notice_format="xml",
            notice_type="branch",
            decode_language="eng",
        )
        data, _meta = cellar._request_notice(notice)
        return data

    return _fetch


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="eu",
        ledger_inputs=eu_ledger_inputs,
        catalog=_EU_RULE_SPECS,
    )
)
