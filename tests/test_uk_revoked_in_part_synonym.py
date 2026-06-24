"""`revoked in part` is an OPC drafting synonym for `repealed in part`. Both
describe a partial repeal of a structural unit; the verb alone differs.
Per AGENTS.md §1.11 (no surface predicate authorizes legal state) we add the
synonym to the closed `_UK_EFFECT_TYPE_ACTIONS` dictionary, not to a free-text
predicate lane."""
from __future__ import annotations

from lawvm.uk_legislation.lowering_actions import _uk_effect_type_action


class TestRevokedInPartSynonym:
    def test_revoked_in_part_lowers_to_replace(self) -> None:
        assert _uk_effect_type_action("revoked in part") == "replace"

    def test_revoked_in_part_matches_repealed_in_part(self) -> None:
        # the synonym must behave exactly like the canonical "repealed in part"
        assert _uk_effect_type_action("revoked in part") == _uk_effect_type_action("repealed in part")

    def test_case_insensitive(self) -> None:
        assert _uk_effect_type_action("Revoked in part") == "replace"
        assert _uk_effect_type_action("REVOKED IN PART") == "replace"

    def test_word_revoked_alone_not_inferred(self) -> None:
        # Negative: bare "revoked" without "in part" must NOT silently acquire an action.
        # Bare "revoked" is ambiguous (could be a whole-provision repeal, a cesser,
        # or a status-only observation); routing it through a free-text synonym would
        # be the forbidden §1.11 surface predicate. It must resolve to None until
        # claimed explicitly.
        assert _uk_effect_type_action("revoked") is None

    def test_unrelated_verb_unaffected(self) -> None:
        assert _uk_effect_type_action("modified") is None
        assert _uk_effect_type_action("applied") is None
