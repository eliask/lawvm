"""Authority-firewall gate for the fi_refs deterministic export.

AGENTS.md §1.11/§2.10, ``notes/LAWVM_PIPELINE_CONTRACT.md`` §7, and
``core/legal_surface_graph.py`` D7: deterministic reference extraction is a
SURFACE projection — surface_only by construction. A deterministic export row
records WHERE a reference was extracted from; it must NOT claim that a human
reviewed it or that replay is authorized.

This gate pins that every row produced by the deterministic projector
(``_DETERMINISTIC_ROW_EXTRAS`` / ``_augment_row`` / ``_project_refs_for_statute``)
carries surface-truthful authority values:

  * ``replay_authorized`` is falsy (NOT True),
  * ``review_status`` is NOT ``verified_manual`` (machine-produced → ``proposed``),
  * ``validator_status`` is NOT ``span_verified``/``entailment_verified``
    (no human validation → ``unvalidated``),
  * the positive surface fact ``deterministic_extraction`` is recorded.

The composer-derived NULL-slot-fill path (``_apply_null_slot_fills``) MAY raise
these — that is legitimately authority derived from a ClaimCompositionDecision,
not author-set at projection time — and is out of scope for this gate.
"""
from __future__ import annotations

from typing import Any, Dict

from lawvm.core.manual_claims.primitive import (
    _ProfileTagDeprecated as ProfileTag,
    ReviewStatus,
    ValidatorStatus,
)
from lawvm.tools.export_fi_refs import (
    _DETERMINISTIC_ROW_EXTRAS,
    _augment_row,
    _project_refs_for_statute,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_PROFILE = ProfileTag.DETERMINISTIC_ONLY

# A synthetic statute with an explicit cross-statute id cite + an internal § ref,
# so the deterministic projector yields >=1 reference row without needing the
# real corpus.
_STATUTE_ID = "999/2099"
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
    <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


class _DictStore:
    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._mapping = mapping

    def read_oracle(self, statute_id: str) -> bytes:
        return self._mapping[statute_id]


def _assert_no_authority(row: Dict[str, Any]) -> None:
    assert not row.get("replay_authorized"), (
        "deterministic export row claims replay_authorized="
        f"{row.get('replay_authorized')!r}; a surface projection carries no replay authority"
    )
    assert row.get("review_status") != ReviewStatus.VERIFIED_MANUAL.value, (
        "deterministic export row claims review_status=verified_manual; "
        "no human reviewed a machine-produced extraction row"
    )
    assert row.get("validator_status") not in (
        ValidatorStatus.SPAN_VERIFIED.value,
        ValidatorStatus.ENTAILMENT_VERIFIED.value,
    ), (
        "deterministic export row claims a human/validator-verified validator_status; "
        f"got {row.get('validator_status')!r}"
    )


def test_deterministic_row_extras_carry_no_replay_or_review_authority() -> None:
    """The author-set deterministic extras must not stamp replay/human-review authority."""
    assert _DETERMINISTIC_ROW_EXTRAS.get("replay_authorized") is False
    assert (
        _DETERMINISTIC_ROW_EXTRAS.get("review_status")
        != ReviewStatus.VERIFIED_MANUAL.value
    )
    assert (
        _DETERMINISTIC_ROW_EXTRAS.get("validator_status")
        != ValidatorStatus.SPAN_VERIFIED.value
    )
    # Positive surface fact recorded instead.
    assert _DETERMINISTIC_ROW_EXTRAS.get("deterministic_extraction") is True


def test_augment_row_deterministic_default_is_surface_truthful() -> None:
    """_augment_row with no explicit extras (deterministic default) is surface_only."""
    augmented = _augment_row({"source_statute_id": _STATUTE_ID}, _PROFILE)
    _assert_no_authority(augmented)
    assert augmented["deterministic_extraction"] is True
    assert augmented["emit_profile"] == _PROFILE.value


def test_projected_deterministic_rows_claim_no_authority() -> None:
    """End-to-end: the production deterministic projector emits surface_only rows."""
    store = _DictStore({_STATUTE_ID: _XML})
    rows, _diag = _project_refs_for_statute(_STATUTE_ID, store, _PROFILE)
    assert rows, "synthetic fixture should yield at least one reference row"
    for row in rows:
        _assert_no_authority(row)
        assert row.get("deterministic_extraction") is True
