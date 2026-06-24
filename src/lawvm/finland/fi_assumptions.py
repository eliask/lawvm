"""Finland declared-assumption register — the hand-curated, root-committed set.

WHAT THIS ENABLES. The Finland compiler's declared NON-guarantees, as typed
:class:`~lawvm.core.assumption_register.AssumptionRegister` objects rather than
prose scattered across ``xfail`` reasons and STATUS docs. Each entry is
content-addressed; :func:`build_fi_assumption_register` returns the full set, and
:func:`lawvm.core.assumption_register.assumption_register_root` over it yields one
checkable root for the Finland declared-assumption set.

WHAT THIS DOES **NOT** YET DO (honesty boundary — see the core module docstring):
this is a HAND-CURATED v0 — it does not auto-discover entries from the suite's
``xfail`` markers, it does not verify the assumptions are true/minimal/complete,
``expires_when`` is human-readable not machine-evaluable, and the root is not yet
wired into the pack manifest / compile dossier.
"""

from __future__ import annotations

from lawvm.core.assumption_register import AssumptionRegister


def build_fi_assumption_register() -> tuple[AssumptionRegister, ...]:
    """The Finland declared non-guarantees, hand-curated for v0.

    The concrete customer is the **B2 source-body-over-prior-repeal scope fork**
    (``tests/test_fi_compile_group_scope_recovery.py::
    test_source_body_scope_overrides_prior_repeal_reinstatement_address``), which
    is ``xfail(strict=True)`` because the hypothesised override now has a
    real-corpus pull from 1993/1501 via 2016/773, but is still contradicted by
    the pinned 1973/36 Finlex oracle, and no bench-clean compile-time
    discriminator has been landed. We do not "fail" here — we have not asserted
    the override; we have declined to, and that decision is recorded as a typed,
    root-committed assumption.
    """
    return (
        AssumptionRegister(
            kind="doctrine_unresolved",
            scope=(
                "FI compile-group scope recovery: whether a corroborated source-body "
                "scope (e.g. chapter 13a) OVERRIDES the prior-repeal carry-forward "
                "reinstatement address (e.g. chapter 14) for a recovered INSERT. "
                "Covers test_fi_compile_group_scope_recovery::"
                "test_source_body_scope_overrides_prior_repeal_reinstatement_address."
            ),
            effect="qualifies",
            expires_when=(
                "a compile-time discriminator distinguishes the 1993/1501 "
                "source-body-wins anchor (2016/773 §148) from the 1973/36 §27 "
                "carry-forward-wins oracle (pinned_replay "
                "test_replay_xml_1973_36_materializes_live_missing_sections), "
                "and full-bench validation shows no broad Levenshtein regression."
            ),
            public_message=(
                "LawVM does NOT guarantee that a recovered insert's source-body "
                "chapter scope overrides the prior-repeal carry-forward address. "
                "Production deliberately keeps the live/carry-forward chapter, "
                "matching the official Finlex 1973/36 oracle. The source-body "
                "override remains a DECLARED non-guarantee even with the 1993/1501 "
                "counter-anchor: no landed signal distinguishes it from the "
                "1973/36 case without broad bench regressions, so we decline to "
                "claim it rather than risk contradicting the real oracle."
            ),
            witness_rule_id="fi_reinstated_section_scope_from_prior_repeal_address",
            finding_refs=(
                "tests/test_fi_compile_group_scope_recovery.py::"
                "test_source_body_scope_overrides_prior_repeal_reinstatement_address",
                "fi_live_stem_scope_overridden_by_corroborated_source_body",
            ),
        ),
    )
