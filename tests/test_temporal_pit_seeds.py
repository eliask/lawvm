"""Bitemporal PIT seam regression seeds for downstream proof consumers (MeVM).

These tests pin two LawVM behaviors that a downstream consumer stakes
bitemporal proofs on, exercised through the public provision-state seam
(`lawvm.provision_state.resolve_provision_state`, schema
`lawvm.provision_state.v1`) — the same seam the consumer reads.

Both tests require the real Finland corpus (`data/finlex.farchive`) and are
skipped when it is absent.

CASE 1 — fixed-term whole-law validity / expiry (laki 482/2024).
  482/2024 is "Laki väliaikaisista toimenpiteistä välineellistetyn
  maahantulon torjumiseksi". Its §7 voimaantulosäännös fixes the law's
  validity: it was extended (HE 18/2025 -> enacted amending act 2025/368,
  effective 2025-07-01) so §7 now reads "on voimassa 31 päivään joulukuuta
  2026" — the whole law is in force only through 31.12.2026.

  Findings (verified against the corpus):
    (a) The EXTENSION is visible: §7 has a version effective 2025-07-01 from
        amendment 2025/368, and its text reflects the extended 31.12.2026
        date. This is asserted as correct.
    (b) The fixed-term EXPIRY itself is NOT modeled. A provision-state query
        as-of 2027-01-01 (after 31.12.2026) returns status="selected",
        content_state="live", expires="" and the full §7 text — identical to
        a query as-of 2026-06-01. The whole-law validity bound stated in §7's
        prose is never lifted into a machine-readable temporal bound, so the
        seam reports the law as live forever. The xfail below pins this defect
        without asserting a (currently absent) correct shape.

CASE 2 — version selection across amendment boundaries (hankintalaki
  1397/2016). Verified correct: the version effective strictly before a
  boundary is returned the day before, the new version is returned on the
  boundary date itself (effective <= as_of), with no off-by-one.
    §141 boundary 2024-06-01 (amendment 2024/164): "kuuden kuukauden" time
      limit -> "12 kuukauden".
    §163 boundary 2021-07-01 (amendment 2021/656): heading
      "Valitusperusteeseen perustuva muutoksenhakukielto" -> "Muutoksenhakukielto",
      body gains a reference to hyvinvointialueesta annetun lain (611/2021).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_CORPUS = Path("data/finlex.farchive")

pytestmark = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus PIT seed tests",
)


def _state(statute_id: str, provision: str, as_of: str) -> dict[str, Any]:
    from lawvm.provision_state import resolve_provision_state

    return resolve_provision_state(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# CASE 1 — fixed-term whole-law validity / expiry (laki 482/2024 §7)
# ---------------------------------------------------------------------------


def test_case1_extension_act_2025_368_is_visible() -> None:
    """The HE 18/2025 extension (enacted act 2025/368) is reflected in §7.

    This part of the behavior IS correct: the amending act that extended the
    validity is in the corpus, selected as the governing version, and its text
    carries the extended 31.12.2026 date.
    """
    state = _state("2024/482", "section:7", "2026-06-01")

    assert state["status"] == "selected"
    version = state["version"]
    # Extension act 2025/368 took effect 2025-07-01.
    assert version["effective"] == "2025-07-01"
    assert version["enacted"] == "2025-06-27"

    locator = state["source_locator"]["document_uri"]
    assert "2025/368" in locator, locator

    text = state["text"]["rendered"]
    # The extended validity date is present in the consolidated §7 text.
    assert "31 päivään joulukuuta 2026" in text, text


@pytest.mark.xfail(
    strict=False,
    reason=(
        "DEFECT: LawVM does not model fixed-term whole-law expiry. Laki "
        "482/2024 §7 fixes validity to 31.12.2026, but a provision-state query "
        "as-of 2027-01-01 returns status='selected', content_state='live', "
        "expires='' and full text — identical to a pre-expiry query. The "
        "31.12.2026 bound lives only as prose inside §7 and is never lifted "
        "into a machine-readable temporal validity bound, so the seam reports "
        "the law as live after it has expired. A downstream bitemporal proof "
        "staked on this seam would treat the expired law as in force."
    ),
)
def test_case1_whole_law_expiry_after_term_is_modeled() -> None:
    """As-of after 31.12.2026, §7 should NOT resolve as a live, selected provision.

    Expected-correct shape (currently failing): the seam should signal expiry
    via the version envelope (e.g. an `expires` bound of 2026-12-31, a
    non-"live" content_state, or a non-"selected"/absent status) rather than
    returning the text as live. This xfail pins the defect; flip to plain
    assertions if/when whole-law fixed-term expiry is modeled.
    """
    before = _state("2024/482", "section:7", "2026-06-01")
    after = _state("2024/482", "section:7", "2027-01-01")

    # Sanity: in force before the term ends.
    assert before["status"] == "selected"
    assert before["version"]["content_state"] == "live"

    after_version = after["version"]
    # If expiry were modeled, AT LEAST ONE of these would hold after the term.
    expiry_signalled = (
        after["status"] != "selected"
        or after_version["content_state"] != "live"
        or after_version["expires"] not in ("", None)
    )
    assert expiry_signalled, (
        "Law 482/2024 is valid only to 31.12.2026 (§7), yet as-of 2027-01-01 "
        f"the seam returned status={after['status']!r}, "
        f"content_state={after_version['content_state']!r}, "
        f"expires={after_version['expires']!r} — no expiry signal."
    )


# ---------------------------------------------------------------------------
# CASE 2 — version selection across amendment boundaries (hankintalaki 1397/2016)
# ---------------------------------------------------------------------------

_HANKINTALAKI = "2016/1397"
_SEC_141 = "part:4/chapter:15/section:141"
_SEC_163 = "part:4/chapter:16/section:163"


def test_case2_section141_boundary_2024_06_01() -> None:
    """§141: 2024-05-31 -> original version; 2024-06-01 -> amendment 2024/164.

    No off-by-one: the new version (effective 2024-06-01) is selected ON the
    boundary date, not the day before. The substantive change is the
    KKV-claim time limit: "kuuden kuukauden" -> "12 kuukauden".
    """
    before = _state(_HANKINTALAKI, _SEC_141, "2024-05-31")
    on = _state(_HANKINTALAKI, _SEC_141, "2024-06-01")

    assert before["status"] == "selected"
    assert on["status"] == "selected"

    # Pre-boundary: original/background version, served from the base statute.
    assert before["version"]["effective"] == "0000-00-00"
    assert before["source_locator"]["document_uri"] == (
        "finlex://sd/2016/1397/fin/main.xml"
    )

    # On the boundary: amended version from 2024/164.
    assert on["version"]["effective"] == "2024-06-01"
    assert on["version"]["enacted"] == "2024-04-12"
    assert "2024/164" in on["source_locator"]["document_uri"], (
        on["source_locator"]["document_uri"]
    )

    before_text = before["text"]["rendered"]
    on_text = on["text"]["rendered"]
    assert before_text != on_text
    assert "kuuden kuukauden" in before_text
    assert "kuuden kuukauden" not in on_text
    assert "12 kuukauden" in on_text


def test_case2_section163_boundary_2021_07_01() -> None:
    """§163: 2021-06-30 -> 2019/844 version; 2021-07-01 -> 2021/656 version.

    No off-by-one. The post-boundary version renames the heading and adds a
    reference to the hyvinvointialue reform (611/2021).
    """
    before = _state(_HANKINTALAKI, _SEC_163, "2021-06-30")
    on = _state(_HANKINTALAKI, _SEC_163, "2021-07-01")

    assert before["status"] == "selected"
    assert on["status"] == "selected"

    # Pre-boundary: version effective 2020-01-01 from amendment 2019/844.
    assert before["version"]["effective"] == "2020-01-01"
    assert "2019/844" in before["source_locator"]["document_uri"], (
        before["source_locator"]["document_uri"]
    )

    # On the boundary: version effective 2021-07-01 from amendment 2021/656.
    assert on["version"]["effective"] == "2021-07-01"
    assert "2021/656" in on["source_locator"]["document_uri"], (
        on["source_locator"]["document_uri"]
    )

    before_text = before["text"]["rendered"]
    on_text = on["text"]["rendered"]
    assert before_text != on_text
    # Heading change across the boundary.
    assert "Valitusperusteeseen perustuva muutoksenhakukielto" in before_text
    assert "Valitusperusteeseen perustuva" not in on_text
    # New reference introduced by the hyvinvointialue reform.
    assert "611/2021" not in before_text
    assert "611/2021" in on_text
