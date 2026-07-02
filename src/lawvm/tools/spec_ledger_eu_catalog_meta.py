"""EU spec-ledger rule metadata: the S/P sort (§3.5) and per-rule falsifier (§3.2(4)).

READ-ONLY / ADDITIVE (mirrors the FI/UK meta sidecars). The EU catalog
(``spec_ledger_eu_catalog._EU_RULE_SPECS``) holds ``eu_*`` typed diagnostics; almost all
of them are P (compiler / acquisition / grammar-gap survival policies), which is exactly
why the analysis calls the EU grammar the ledger's blind-spot frontier: the genuine
``EU_FMX4.*`` amendment-semantics S-rules are not yet enumerated as prose (they fire as
uncatalogued "·" rows). The two catalogued entries that ARE law-hypotheses are marked S.

See ``notes_internal/FABLE_SPEC_RECONSTRUCTION.md`` §3.5.
"""
from __future__ import annotations

from typing import Dict, Literal

RuleRole = Literal["S", "P"]

# The two catalogued EU ids that state a hypothesis ABOUT EU AMENDMENT LAW (S); every
# other catalogued eu_* id is a typed compiler/acquisition-survival diagnostic (P).
_EU_S_RULES = frozenset(
    {
        "eu_amending_act_authorizes_apply",  # amend authority => apply is warranted
        "eu_renumber_relabel",               # RENUMBER = named source->dest migration
    }
)


def build_eu_rule_roles(
    catalog_ids: "Dict[str, str] | frozenset[str] | set[str]",
) -> Dict[str, RuleRole]:
    """S for the two amendment-semantics ids, P for every other catalogued diagnostic."""
    return {rid: ("S" if rid in _EU_S_RULES else "P") for rid in catalog_ids}


_S_FALSIFIER_TEMPLATE = (
    "An EUR-Lex consolidation article where {rid} fired but the consolidated text does "
    "not exhibit the amendment semantics its believed_spec asserts."
)
_P_FALSIFIER_TEMPLATE = (
    "A rate of EU replay cases where {rid}'s acquisition/grammar-gap/replay-tolerance "
    "diagnostic fired yet the consolidation shows the article was in fact "
    "deterministically specified (the gap was ours to close), exceeding tolerance."
)

_EU_RULE_FALSIFIERS_SEED: Dict[str, str] = {
    "eu_amending_act_authorizes_apply": (
        "An EUR-Lex consolidation where an amending act LawVM treated as authorizing "
        "apply produced an article state the consolidation contradicts (the authority "
        "read was wrong)."
    ),
    "eu_renumber_relabel": (
        "An EU RENUMBER whose source->destination relabel migration is unnamed or "
        "misnamed versus the consolidated article numbering."
    ),
}


def build_eu_rule_falsifiers(
    catalog_ids: "Dict[str, str] | frozenset[str] | set[str]",
) -> Dict[str, str]:
    """Falsifier per catalogued EU rule id: hand-authored seed, else layer template."""
    out: Dict[str, str] = {}
    for rid in catalog_ids:
        if rid in _EU_RULE_FALSIFIERS_SEED:
            out[rid] = _EU_RULE_FALSIFIERS_SEED[rid]
            continue
        template = (
            _S_FALSIFIER_TEMPLATE
            if rid in _EU_S_RULES
            else _P_FALSIFIER_TEMPLATE
        )
        out[rid] = template.format(rid=rid)
    return out
