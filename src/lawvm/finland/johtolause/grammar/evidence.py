"""evidence — the sidecar EvidenceLedger and token-accounting invariant.

Two jobs the old parser did badly and this layer does explicitly:

1. **Witness attachment without rebuild churn.** The old parser reconstructed
   frozen ``Surface*`` dataclasses 77 times just to attach a ``witness=``. Here,
   recognizers and the discourse transducer record witness facts in a ledger
   keyed by span; the compatibility emitter reads the ledger once at the end to
   populate the frozen nodes. Build drafts, freeze once.

2. **Token accounting (a hard invariant).** Every token in the filtered stream
   must have a NAMED disposition — consumed by a node, trivia, a provenance span,
   explicitly ignored by a named rule, or surfaced as a diagnostic. No silent
   drops. ``unaccounted()`` returns any token with no disposition, so a test /
   the parse verdict can refuse to invent or lose meaning.

Neither structure carries legal meaning; they are bookkeeping the emitter and
the totality checker consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from lawvm.finland.johtolause.grammar.combinators import Span


class Disposition(str, Enum):
    """Why a token range is accounted for (the token-accounting taxonomy)."""

    NODE = "node"  # consumed into a produced surface node
    TRIVIA = "trivia"  # separators / glue (comma, conjunction, dash between targets)
    PROVENANCE = "provenance"  # a "sellaisena kuin …" provenance/citation span
    IGNORED = "ignored"  # deliberately skipped by a NAMED rule (e.g. end sentinel)
    DIAGNOSTIC = "diagnostic"  # surfaced as a residual/diagnostic, not silently dropped


@dataclass(frozen=True, slots=True)
class WitnessFact:
    """A recorded witness: a rule fired over a span. Read by the emitter."""

    rule_id: str
    span: Span


@dataclass(frozen=True, slots=True)
class DispositionFact:
    """A token range's named disposition, for the accounting invariant."""

    span: Span
    disposition: Disposition
    rule: str  # the named rule responsible (never anonymous)


@dataclass
class EvidenceLedger:
    """Mutable during a parse; read once by the emitter + totality checker.

    Internal scratch — never part of the public ``parse()`` output. Surfaced only
    via the private ``parse_with_evidence`` API for characterization/debug.
    """

    n_tokens: int
    witnesses: list[WitnessFact] = field(default_factory=list)
    dispositions: list[DispositionFact] = field(default_factory=list)

    def witness(self, rule_id: str, span: Span) -> None:
        self.witnesses.append(WitnessFact(rule_id=rule_id, span=span))

    def account(self, span: Span, disposition: Disposition, rule: str) -> None:
        """Record that ``span`` is accounted for by a named rule."""
        self.dispositions.append(DispositionFact(span=span, disposition=disposition, rule=rule))

    def accounted_indices(self) -> set[int]:
        out: set[int] = set()
        for d in self.dispositions:
            out.update(range(d.span.start, d.span.end))
        return out

    def unaccounted(self) -> list[int]:
        """Token indices with no named disposition — the token-accounting check."""
        acc = self.accounted_indices()
        return [i for i in range(self.n_tokens) if i not in acc]

    def witnesses_for(self, span: Span) -> list[str]:
        """Witness rule_ids whose span is contained in ``span`` (for the emitter)."""
        return [
            w.rule_id
            for w in self.witnesses
            if w.span.start >= span.start and w.span.end <= span.end
        ]
