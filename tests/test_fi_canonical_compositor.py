"""Hermetic tests for the Level-2 canonical compositor.

Exercises the exactness discipline end to end with NO live backend (the LLM is an
injected ``Callable[[str], str]``):

  * deterministic line-break dehyphenation is applied AND ledgered;
  * the inert whitespace/format quotient is applied AND ledgered;
  * a content-preserving LLM cleanup (joining a hard wrap) is ACCEPTED + ledgered
    ``proposed_by="llm"``, ``verified=True``;
  * a HALLUCINATING LLM cleanup (adds/changes a char) is REJECTED by the safety
    gate and NEVER applied;
  * the transform ledger round-trips (raw is exactly reconstructable);
  * the XML-reference no-op path yields an empty ledger.
"""
from __future__ import annotations

from lawvm.finland.canonical_compositor import (
    CanonicalUnit,
    ContentPreservationVerdict,
    Transform,
    TransformKind,
    canonical_from_reference,
    compose_canonical,
    reconstruct_raw,
    verify_content_preserving,
)

# The U+FFFE discretionary glyph pypdfium2 emits at a soft line break (see
# ``page_elements.dehyphenate``); a genuine soft break fuses to one word.
_DISCRETIONARY_GLYPH = "￾"


# --------------------------------------------------------------------------- #
# Deterministic dehyphenation lane                                            #
# --------------------------------------------------------------------------- #
class TestDeterministicDehyphenation:
    def test_soft_hyphen_line_break_is_fused_and_ledgered(self) -> None:
        raw = f"kriisinrat{_DISCRETIONARY_GLYPH}kaisusta"
        unit = compose_canonical(raw)
        assert unit.clean_text == "kriisinratkaisusta"
        assert unit.provenance == "pdf_derived"
        kinds = [t.kind for t in unit.transforms]
        assert TransformKind.DEHYPHENATE_LINEBREAK in kinds
        dehyph = next(t for t in unit.transforms if t.kind == TransformKind.DEHYPHENATE_LINEBREAK)
        assert dehyph.proposed_by == "deterministic"
        assert dehyph.verified is True
        # The ledgered edit is a pure removal of the artifact glyph.
        assert dehyph.before == _DISCRETIONARY_GLYPH
        assert dehyph.after == ""

    def test_real_compound_hyphen_is_preserved(self) -> None:
        # ETA<FFFE>sopimus is a real compound hyphen that fell at a line break — kept.
        raw = f"ETA{_DISCRETIONARY_GLYPH}sopimus"
        unit = compose_canonical(raw)
        assert unit.clean_text == "ETA-sopimus"

    def test_clean_text_with_no_artifacts_produces_empty_ledger(self) -> None:
        raw = "yksinkertainen teksti"
        unit = compose_canonical(raw)
        assert unit.clean_text == raw
        assert unit.transforms == ()


# --------------------------------------------------------------------------- #
# Deterministic whitespace/format quotient lane                               #
# --------------------------------------------------------------------------- #
class TestWhitespaceNormalize:
    def test_whitespace_runs_collapse_and_are_ledgered(self) -> None:
        raw = "sana   toinen\n\n   kolmas"
        unit = compose_canonical(raw)
        assert unit.clean_text == "sana toinen kolmas"
        kinds = [t.kind for t in unit.transforms]
        assert TransformKind.WHITESPACE_NORMALIZE in kinds
        ws = next(t for t in unit.transforms if t.kind == TransformKind.WHITESPACE_NORMALIZE)
        assert ws.proposed_by == "deterministic"
        assert ws.verified is True

    def test_reconstruct_raw_after_deterministic_stages(self) -> None:
        raw = f"kriisinrat{_DISCRETIONARY_GLYPH}kaisusta   jatkuu\n\ntähän"
        unit = compose_canonical(raw)
        assert reconstruct_raw(unit) == raw


# --------------------------------------------------------------------------- #
# The content-preservation safety gate                                        #
# --------------------------------------------------------------------------- #
class TestVerifyContentPreserving:
    def test_pure_whitespace_join_is_preserving(self) -> None:
        verdict = verify_content_preserving("rivi yksi\nrivi kaksi", "rivi yksi rivi kaksi")
        assert isinstance(verdict, ContentPreservationVerdict)
        assert verdict.preserving is True
        assert verdict.added_or_changed == ()

    def test_numeric_mutation_is_rejected(self) -> None:
        verdict = verify_content_preserving("summa 2500 markkaa", "summa 2600 markkaa")
        assert verdict.preserving is False
        assert "6" in verdict.added_or_changed

    def test_inserted_word_is_rejected(self) -> None:
        verdict = verify_content_preserving("laki tulee voimaan", "laki tulee heti voimaan")
        assert verdict.preserving is False
        assert verdict.added_or_changed  # the interpolated "heti" chars are unmatched

    def test_substituted_letter_is_rejected(self) -> None:
        verdict = verify_content_preserving("kunta", "kanta")
        assert verdict.preserving is False

    def test_artifact_hyphen_removal_is_preserving(self) -> None:
        raw = f"rat{_DISCRETIONARY_GLYPH}kaisu"
        verdict = verify_content_preserving(raw, "ratkaisu")
        assert verdict.preserving is True


# --------------------------------------------------------------------------- #
# The distrusted LLM lane (accept-verified / reject-hallucinated)             #
# --------------------------------------------------------------------------- #
class TestLLMLane:
    def test_content_preserving_llm_wrap_join_is_accepted_and_ledgered(self) -> None:
        # After the deterministic quotient the text is already single-spaced; the LLM
        # proposes removing the space between two fragments (a wrap join) — pure removal.
        raw = "menettelys"

        def proposer(current: str) -> str:
            # Join "menettelys" onto a trailing continuation by removing a space.
            return current.replace("menet telys", "menettelys")

        # Feed a text with an errant mid-word space so the proposer has work to do.
        unit = compose_canonical("menet telys jatkuu", llm_proposer=proposer)
        assert unit.clean_text == "menettelys jatkuu"
        llm = [t for t in unit.transforms if t.kind == TransformKind.LLM_CLEANUP]
        assert len(llm) == 1
        assert llm[0].proposed_by == "llm"
        assert llm[0].verified is True
        # Round-trips back to the raw extraction through the full ledger.
        assert reconstruct_raw(unit) == "menet telys jatkuu"
        assert raw in unit.clean_text

    def test_hallucinating_llm_cleanup_is_rejected_and_not_applied(self) -> None:
        raw = "korvaus 2500 euroa"

        def hallucinator(current: str) -> str:
            return current.replace("2500", "2600")  # invents a digit

        unit = compose_canonical(raw, llm_proposer=hallucinator)
        # The proposal is discarded: clean stays at the deterministic form, no LLM ledger.
        assert unit.clean_text == "korvaus 2500 euroa"
        assert all(t.kind != TransformKind.LLM_CLEANUP for t in unit.transforms)

    def test_word_inserting_llm_cleanup_is_rejected(self) -> None:
        raw = "laki tulee voimaan"

        def inserter(current: str) -> str:
            return current.replace("tulee voimaan", "tulee heti voimaan")

        unit = compose_canonical(raw, llm_proposer=inserter)
        assert unit.clean_text == raw
        assert all(t.proposed_by != "llm" for t in unit.transforms)

    def test_noop_llm_proposal_adds_no_ledger_entry(self) -> None:
        raw = "muuttumaton teksti"
        unit = compose_canonical(raw, llm_proposer=lambda current: current)
        assert unit.transforms == ()
        assert unit.clean_text == raw


# --------------------------------------------------------------------------- #
# The XML-reference no-op / confirmation path                                 #
# --------------------------------------------------------------------------- #
class TestXmlReference:
    def test_reference_path_is_a_confirmation_noop(self) -> None:
        clean = "puhdas XML-kanoninen teksti"
        unit = canonical_from_reference(clean)
        assert isinstance(unit, CanonicalUnit)
        assert unit.clean_text == clean
        assert unit.raw_text == clean
        assert unit.transforms == ()
        assert unit.provenance == "xml_reference"
        assert reconstruct_raw(unit) == clean


# --------------------------------------------------------------------------- #
# Ledger schema / serialization                                               #
# --------------------------------------------------------------------------- #
class TestLedgerSchema:
    def test_transform_and_unit_are_jsonable(self) -> None:
        unit = compose_canonical(f"a{_DISCRETIONARY_GLYPH}b   c")
        blob = unit.to_jsonable()
        assert blob["provenance"] == "pdf_derived"
        serialized_transforms = blob["transforms"]
        assert isinstance(serialized_transforms, list)
        assert len(serialized_transforms) == len(unit.transforms)
        for transform in unit.transforms:
            assert isinstance(transform, Transform)
            serialized: dict[str, object] = transform.to_jsonable()
            assert serialized["kind"] == transform.kind.value
            assert serialized["span"] == list(transform.span)

    def test_verdict_is_jsonable(self) -> None:
        verdict = verify_content_preserving("2500", "2600")
        blob = verdict.to_jsonable()
        assert blob["preserving"] is False
        added = blob["added_or_changed"]
        assert isinstance(added, list)
        assert "6" in added
