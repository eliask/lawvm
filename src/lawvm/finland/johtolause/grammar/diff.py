"""diff — canonical SurfaceClause serialization + the parser differential harness.

The forcing function for the rewrite. A new recognizer family is "correct" only
when ``compare_surface_parsers(text, surface_parse, candidate)`` reports no delta
across the characterization golden corpus. This is the gate the old
fallback-and-regex lane never had.

What it normalizes away vs. what it compares:
  * AWAY: Python object identity, the ``SurfaceWitness`` wrapper object, and any
    ledger scratch — none of which is part of the observable contract.
  * COMPARES: the ENTIRE frozen model — verb-group order + verb codes, every node
    field, witness ``rule_id`` + ``source_span``, ``source_text`` and
    ``consumed_count``. We diff the whole model, not a hand-picked subset, so the
    field most likely to silently diverge (``consumed_count`` — see the contract)
    can never slip through an omission.

The canonical form is produced by ``dataclasses.asdict`` (so new node types are
captured with zero per-node maintenance) with enums rendered as ``Type.NAME`` and
tuples as lists, leaving a JSON-safe value comparable with ``==`` and writable to
the golden.

See ``notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md`` for the contract this
harness enforces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from lawvm.finland.johtolause.surface_model import SurfaceClause

# A surface parser conforms to the contract entry point:
#   parse(tokens, jolloin_renumber_pairs=...) -> SurfaceClause
SurfaceParser = Callable[..., SurfaceClause]


# ---------------------------------------------------------------------------
# Canonicalization.
# ---------------------------------------------------------------------------
def _jsonify(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return f"{type(obj).__name__}.{obj.name}"
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(x) for x in obj]
    return obj


def canonicalize_surface_model(model: SurfaceClause) -> dict[str, Any]:
    """A JSON-safe, identity-free canonical form of a parsed clause.

    Captures the entire frozen model so a differential compares the full
    observable contract, not a subset that silently omits the field that
    diverged. Enums become ``Type.NAME`` and tuples become lists, so the result
    is comparable with ``==`` and serializable for the golden.
    """
    return _jsonify(asdict(model))


# ---------------------------------------------------------------------------
# Delta report.
# ---------------------------------------------------------------------------
@dataclass
class ParserDeltaReport:
    """The structural difference between two canonical models.

    ``deltas`` is a sorted list of ``path: a != b`` strings (empty == identical).
    """

    deltas: list[str] = field(default_factory=list)

    @property
    def equal(self) -> bool:
        return not self.deltas

    def summary(self, limit: int = 20) -> str:
        if self.equal:
            return "no delta"
        head = self.deltas[:limit]
        body = "\n".join(f"  {d}" for d in head)
        more = len(self.deltas) - len(head)
        if more > 0:
            body += f"\n  … (+{more} more)"
        return f"{len(self.deltas)} delta(s):\n{body}"


def _diff(path: str, a: Any, b: Any, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: <absent> != {b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k}: {a[k]!r} != <absent>")
            else:
                _diff(f"{path}.{k}", a[k], b[k], out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i in range(min(len(a), len(b))):
            _diff(f"{path}[{i}]", a[i], b[i], out)
        return
    if a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def compare_canonical(a: dict[str, Any], b: dict[str, Any]) -> ParserDeltaReport:
    out: list[str] = []
    _diff("model", a, b, out)
    return ParserDeltaReport(deltas=out)


def compare_surface_models(a: SurfaceClause, b: SurfaceClause) -> ParserDeltaReport:
    return compare_canonical(canonicalize_surface_model(a), canonicalize_surface_model(b))


def _is_witness_span_delta(delta: str) -> bool:
    """True iff a delta line is *only* a differing witness ``source_span`` endpoint.

    Shape: ``model.verb_groups[2].nodes[5].witness.source_span[1]: 115 != 109``.
    Such a delta means the two models attribute the same text to nodes with a
    different per-node span boundary — replay-neutral when every other field
    (labels, kinds, sub_targets, rule_ids, verb-group structure, consumed_count,
    source_text) is identical.
    """
    return ".witness.source_span" in delta and " != " in delta


def compare_surface_models_structural(a: SurfaceClause, b: SurfaceClause) -> ParserDeltaReport:
    """Like :func:`compare_surface_models` but tolerant of *purely* witness
    ``source_span`` endpoint differences.

    Returns a report whose ``deltas`` exclude witness-span-only lines; if the two
    models differ ONLY in witness spans, the returned report has ``equal == True``.
    Use this to certify replay-neutral span-attribution differences as owned (see
    the ``witness_span_normalized`` census class). All structural fields must
    still match exactly for the result to be equal.
    """
    full = compare_surface_models(a, b)
    if full.equal:
        return full
    structural = [d for d in full.deltas if not _is_witness_span_delta(d)]
    return ParserDeltaReport(deltas=structural)


# ---------------------------------------------------------------------------
# Running a candidate parser on equal footing with the authority.
# ---------------------------------------------------------------------------
def parse_text_with(text: str, parser: SurfaceParser) -> SurfaceClause:
    """Build the filtered token stream the contract requires, then run ``parser``.

    Mirrors ``api.parse_clause``'s front matter (``tokenize`` ->
    ``apply_annotations_with_jolloin_pairs``) and passes ``jolloin_renumber_pairs``
    exactly as the authoritative path does, so a candidate recognizer is driven
    over real corpus text on identical footing with ``surface_parse.parse``.
    """
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    return parser(tokens, jolloin_renumber_pairs=jolloin if jolloin else None)


def compare_surface_parsers(
    text: str, parse_a: SurfaceParser, parse_b: SurfaceParser
) -> ParserDeltaReport:
    """Run two parsers over the same filtered tokens; diff their canonical models.

    Both parsers receive the identical token stream, so ``source_text``
    (reconstructed from filtered tokens) matches by construction and any delta is
    a genuine parser disagreement.
    """
    a = parse_text_with(text, parse_a)
    b = parse_text_with(text, parse_b)
    return compare_surface_models(a, b)
