"""European Union (EU) believed_spec catalog — the discovered-spec hypotheses, one per rule.

This is a standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS``
and ``spec_ledger_ee_catalog.py``'s ``_EE_RULE_SPECS``. The main ledger's EU
adapter can guard-import ``_EU_RULE_SPECS`` once its dispatch is generalized;
nothing here imports the EU frontend, so it carries no heavy deps and stays
conflict-free with parallel ``spec_ledger.py`` edits.

Voice and contract (see ``spec_ledger.py``, ``spec_ledger_uk_catalog.py``,
``spec_ledger_ee_catalog.py``, and ``spec_ledger_se_catalog.py``):
each entry is a one-line, falsifiable hypothesis about the *legal-amendment
semantics* the witness rule encodes — the believed spec the compiler is
testing against the authoritative EUR-Lex consolidation. EU replays as
consistency verification against the authoritative consolidated law, so these
describe genuine amendment semantics and manual-compilation frontier states,
not editorial conventions. Grounded in the rule-emitting code under
``src/lawvm/eu/`` (pipeline.py, ops_parser.py, cellar.py, reul_bridge.py).

Anti-drift-guarded by ``tests/test_spec_ledger_eu_catalog.py``: every statically
discoverable EU rule-id literal must have a non-empty entry, and every key here
must map to a real literal in the EU source (no dead entries).

Honest scope note — what is and is not statically enumerable.

* EU call sites pass ``rule_id="..."`` / ``kind="..."`` / ``reason_code="..."``
  where the string is a literal, so the id *is* statically enumerable and is
  catalogued here.

* Dynamic construction families (f-string ``lane`` prefixes like
  ``eu_cellar_{notice_type}_{notice_format}``) are NOT believed-spec hypotheses
  — they identify a specific acquisition-attempt instance, not a believed-spec
  hypothesis, so the ``eu_cellar_`` prefix is documented as a dynamic
  exclusion rather than fabricated per-instance.

* Metadata / summary key strings (``eu_doc_refs``,
  ``eu_replay_applied_op_count``, ``eu_replay_skipped_op_count``) are NOT rule
  ids — they are dict keys carrying replay statistics, not falsifiable
  believed-spec hypotheses; they are excluded from the rule-id denominator.

Every other ``"eu_…"`` literal maps to exactly one believed-spec hypothesis here.
"""
from __future__ import annotations

from typing import Dict

# Believed-spec hypothesis per EU witness_rule_id.  Keys are the literal rule-id
# strings bound to ``_EU_*_RULE`` constants (or passed inline as ``rule_id=`` /
# ``kind=`` / ``reason_code=``) across ``src/lawvm/eu/``.  Each value is a
# falsifiable one-line claim about the law.
_EU_RULE_SPECS: Dict[str, str] = {
    # --- Cellar acquisition (manifestation + manifest request) --------------------------
    # A cellar manifestation option was skipped because the expression / link / URI
    # was unusable (missing language, missing URI node, empty URI value); a typed
    # diagnostic is emitted so the skip is observable downstream, never silent.
    "eu_cellar_manifestation_option_skipped": "An EU Cellar manifestation option whose expression language, manifestation link URI, or URI value is missing is a typed acquisition skip, not a silent drop.",
    "eu_cellar_manifest_request_failed": "An EU Cellar manifest request HTTP/URL failure surfaces as a typed source-pathology diagnostic (blocking) carrying the notice URL + accept header + error_type, not a silent empty buffer; source-lane-selection evidence records the attempt and the no-source-lane-selected fallback.",

    # --- Affecting-act discovery (cellar search) ---------------------------------------
    "eu_affecting_candidate_celex_rejected": "An EU Cellar affecting-act candidate whose CELEX is invalid (leading-zero / non-digit prefix) OR points back to the affected act itself is a typed source-pathology diagnostic, not a silent filter; the rejection is observable with the candidate CELEX and relation tag.",
    "eu_affecting_discovery_failed": "An exception during EU Cellar affecting-act discovery is a typed acquisition diagnostic (with exception), not a silent empty list that conflates failure with no-affecting-acts.",

    # --- Amendment-text fetch -----------------------------------------------------------
    "eu_amendment_text_fetch_failed": "An exception while fetching an EU amending act's XHTML manifestation is a typed acquisition diagnostic, not a silent empty text buffer that lets replay proceed against an empty amendment.",
    "eu_amendment_text_empty": "An EU affecting act that produced empty amendment text without a prior fetch-failed diagnostic is a typed source-pathology (the discovered source lane is not an operative-content-free lane), not a silent no-content pass.",

    # --- FMX4 amendment grammar (structural-instruction recognition) --------------------
    # Each ``EU_FMX4.*`` witness_rule_id names a recognized amendment-instruction op
    # (those constants travel on the op's ``witness_rule_id`` and are the ledger's
    # uncataloged grammar frontier). The ``eu_fmx4_grammar_*`` ids below are the
    # *typed diagnostics* the same grammar emits when an instruction was recognized
    # as operative but could not be lowered into an op — each a falsifiable claim
    # that "this shape of amending source does not deterministically specify a
    # structural effect", surfaced as evidence rather than silently dropped.
    "eu_fmx4_grammar_not_xml": "EU amending-act bytes that do not parse as XML are a typed source-pathology diagnostic (with the parse error + byte excerpt), not a silent empty op stream.",
    "eu_fmx4_grammar_envelope_no_enacting_terms": "An EU manifestation whose root carries no ACT, no ANNEX and no enacting terms (a metadata-only publication envelope) yields a typed instruction-free residual, not a crash or a silent zero.",
    "eu_fmx4_grammar_no_enacting_terms": "An EU amending act with no ENACTING.TERMS element is a typed source-pathology diagnostic, not a silent no-instruction pass.",
    "eu_fmx4_grammar_annex_root_no_number": "An ANNEX-rooted EU manifestation that exposes no 'ANNEX <N>' title to resolve the target annex number is a typed extraction-gap diagnostic, not a silent whole-annex replace against an unknown target.",
    "eu_fmx4_grammar_point_replace_missing_payload": "An EU point-level REPLACE instruction with neither a QUOT block nor inline quoted replacement text is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_point_insert_missing_payload": "An EU point-level INSERT instruction with neither a QUOT block nor inline quoted new text is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_subparagraph_replace_missing_quoted_block": "An EU subparagraph REPLACE instruction with no QUOT block payload is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_indent_replace_missing_payload": "An EU indent-level REPLACE instruction with neither a QUOT block nor inline quoted replacement text is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_annex_as_set_out_payload_separate": "An indirect annex amendment ('as set out in the Annex to this Regulation') whose replacement body ships as a separate ANNEX manifestation absent from the main FMX4 lowers the structural target with a typed separate-manifestation payload-origin diagnostic, not a silent empty annex body.",
    "eu_fmx4_grammar_annex_replace_missing_quoted_block": "An EU whole-annex REPLACE instruction with no QUOT block payload is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_replace_missing_quoted_block": "An EU sub-article / whole-article REPLACE instruction with no QUOT block payload is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_insert_missing_quoted_block": "An EU whole-article INSERT instruction with no QUOT block payload is a typed grammar diagnostic, not a silent empty-payload op.",
    "eu_fmx4_grammar_corrigendum_empty_for_read": "An EU corrigendum whose 'for … read …' formula resolves to empty for-text or read-text is a typed corrigendum diagnostic, not a silent no-op patch.",
    "eu_fmx4_grammar_corrigendum_no_structural_target": "An EU corrigendum 'for/read' formula that names no Article target is a typed residual — an act-wide text patch is not addressable in the IR coordinate system — not a silent whole-act mutation.",
    "eu_fmx4_grammar_uncovered_instruction": "An EU amendment instruction that matched none of the covered grammar families (whole/sub-article replace, article insert/repeal, point repeal, annex replace, for/read corrigenda) is a typed uncovered-instruction residual — the grammar's coverage frontier — not a silent drop.",

    # --- Ops parser (clause-segment lowering) ------------------------------------------
    "eu_ops_parser_unsupported_action_segment": "An operative-looking EU amendment segment whose action verb is not in the supported verb set is a typed parser diagnostic (blocking, with source_excerpt), not a silent skip that lets the segment disappear.",
    "eu_ops_parser_unknown_operative_segment": "An EU amendment segment that looks operative but matches no known verb family is a typed parser diagnostic (blocking, with source_excerpt), not a silent pass into the lowered op stream.",
    "eu_ops_parser_segment_unparsed": "An EU amendment segment that the parser could not structure is a typed parser residual (with source_excerpt), not a silent drop that the parser absorbs without a witness.",
    "eu_ops_parser_corrigendum_target_missing": "A corrigendum whose target CELEX is missing in the corrigendum payload is a typed parser diagnostic, not a silent no-target pass.",

    # --- Replay apply lane (per-op skip adjudications) ---------------------------------
    "eu_replay_unsupported_action": "An op whose action is not in the EU replay-supported set is a typed replay-skip adjudication, not a silent pass.",
    "eu_replay_unknown_action": "An op whose action is not recognized by the EU replay dispatcher is a typed replay-skip adjudication, not a silent drop.",
    "eu_replay_text_payload_missing": "A REPLACE/INSERT op whose text payload is missing or wrong-kind is a typed replay-skip adjudication, not a silent default to an empty payload.",
    "eu_replay_target_not_found": "An op whose target section does not exist in the baseline is a typed replay-skip adjudication, not a silent no-op.",
    "eu_replay_parent_not_found": "An INSERT op whose parent scope does not resolve is a typed replay-skip adjudication, not a silent no-op.",
    "eu_replay_insert_parent_scope_unresolved": "An INSERT op whose declared parent scope cannot be resolved (e.g. intra-section vs inter-section ambiguity) is a typed replay-skip adjudication, not a silent fallback to whole-section insertion.",
    "eu_replay_tree_invariant_violation": "A post-op tree-invariant violation (illegal edge / duplicate label / structural inconsistency) is a typed blocking replay adjudication, not a silent pass that lets the broken tree reach the consumer.",

    # --- Apply-fold conservation (§1.8 receipt ledger) ---------------------------------
    "eu_replay_skipped_unspecified": "An op skipped by EU replay whose adjudication ledger carries no typed reason is classified with this reason_code (a §1.10 fail-loud gap), not a silent drop — the unspecified skip must remain observable as evidence.",

    # --- REUL bridge (retained-law URI resolution) --------------------------------------
    "eu_reul_uri_resolution_failed": "An EU REUL retained-law URI that the bridge cannot resolve against the EU statute tree is a typed blocking lowering diagnostic (with uri + statute_id + reason_code), not a silent None return that lets the citation disappear.",

    # --- Apply-fold orchestration failure (pipeline.py production caller) ---------------
    # iter3 W3 (silent-failure review HIGH #2): when ``apply_eu_ops_conserved`` raises
    # mid-fold, the production caller ``EUReplayPipeline.replay_statute`` catches
    # broadly (``except Exception as e:``) and appends a non-blocking
    # ``eu_replay_apply_raise`` orchestration adjudication per §1.10 (embedding
    # ``exception_type`` / ``exception`` / ``clause_text`` via
    # ``diagnostic_detail``) before returning a typed ``EUReplayResult`` with
    # ``replayed``/``timelines``/``apply_filter_result`` left ``None`` and
    # ``error = f"Failed to apply ops: {e}"`` (the blocking gate — mirrors
    # EE/NO). The adjudication is a WITNESS, not the gate; bare-apply's partial
    # witnesses (appended in place before the raise by the conserved wrapper)
    # persist on the returned result's ``adjudications`` — §1.0
    # evidence-not-silently-destroyed contract. Mirrors the NO/EE/SE precedent
    # (silent-failure review HIGH #1-3).
    "eu_replay_apply_raise": "An apply-fold exception raised mid-``apply_eu_ops_conserved`` is a non-blocking typed orchestration adjudication carrying the exception type/exception/clause_text snippet per §1.10 (the blocking gate stays on ``EUReplayResult.error``); bare-apply's partial witnesses persist on the production result's adjudication ledger — §1.0 evidence-not-silently-destroyed contract.",

    # --- Apply receipt contract (§4 WriteReceipt divergence-naming) ----------------------
    # Mirrors SE's ``se_renumber_relabel`` (``sweden/grafter.py:4145``),
    # NO's ``no_section_renumber_relabel`` (``norway/grafter.py:4367``), and
    # EE's ``_EE_SECTION_SEQUENCE_RENUMBER_RULE`` (``estonia/peg.py:1225``) —
    # a RENUMBER op mints an identity migration (bound source label → landed
    # destination label) that the §1.6 unstated-migration invariant MUST carry
    # with a named rule id. Stamped on the per-op ``WriteReceipt`` (mirrors
    # SE at ``sweden/grafter.py:4157`` and NO at ``norway/grafter.py:4448``)
    # by ``_eu_emit_one_op_receipt`` so the bound→landed divergence audits
    # as ``qualified`` (named-rule-explained divergence) in
    # ``build_observed_write_audit``, not as ``violation`` (unexplained) —
    # the §1.6 unstated-migration invariant risks being violated if the
    # receipt omits this rule id.
    #
    # Forward-registered ahead of the EU bare apply landing RENUMBER support
    # (renumber is in the EU bare variant's unsupported-action set today);
    # the receipt helper's RENUMBER branch is named-and-witnessed so the
    # rule id is auditable the moment RENUMBER apply support ships.
    "eu_renumber_relabel": "An EU RENUMBER op's bound_target_path (source label) vs landed_primary_path (destination label) divergence is the typed named migration for a section relabel/renumber — receipt-audited as ``qualified`` (named-rule-explained divergence), not a ``violation`` (unexplained); the §1.6 unstated-migration invariant risks being violated if the receipt omits this rule id.",

    # --- EV-05 execution-authorization proof carrier (the firewall waist) -----------------
    # Mirrors SE's ``se_affecting_act_authorizes_apply`` and NO's
    # ``no_affecting_act_authorizes_apply``. The concrete
    # ``authorization_rule_id`` appends the amending act CELEX
    # (``eu_amending_act:<celex>``, a per-instance f-string prefix excluded from
    # the rule-id denominator in the catalog test); this constant is the rule
    # *family* the minted proof stamps into its ``detail``.
    "eu_amending_act_authorizes_apply": "An EU op's execution authority is its source amending act: a typed ExecutionAuthorization proof is minted from the op's amending-act CELEX identity (``eu_amending_act:<celex>``) so the EV-05 observe gate stays quiet on authorized ops; an op with no amending-act identity carries UNKNOWN authority and no proof is fabricated, so the gate fires honestly on the real unauthorized residue.",
}
