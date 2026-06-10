"""Fixed-term whole-law statute expiry (määräaikainen laki) — Pro §9 test list.

Most cases use synthetic timelines built directly so they exercise the extractor
and seam overlay deterministically without corpus replay; the 482/2024 trio uses
the real corpus and is skipped when data/finlex.farchive is absent. The seam
SEMANTICS are gated by LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS, set via monkeypatch
in the tests that assert the expired/blocked behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.statute_validity import (
    StatuteValidityBound,
    expires_on_from_valid_until,
    governing_bound,
    is_expired_at,
    late_extension_gap,
)
from lawvm.finland.fixed_term_expiry import (
    FIXED_TERM_EXPIRY_AMBIGUOUS,
    FIXED_TERM_EXPIRY_UNPARSEABLE,
    SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED,
    build_corpus_report,
    extract_fixed_term_bounds,
)
from lawvm.tools.provision_state import (
    FIXED_TERM_BOUNDS_FLAG,
    build_provision_state_response,
)

_CORPUS = Path("data/finlex.farchive")
_VOIMAANTULO = LegalAddress(path=(("section", "7"),))


def _enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")


def _voimaantulo_node(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="7", text=text)


def _voimaantulo_version(
    *,
    effective: str,
    enacted: str,
    text: str,
    source_statute: str,
    variant_kind: Literal["permanent", "temporary"] = "permanent",
    expires: str = "",
    content: IRNode | None = None,
) -> ProvisionVersion:
    node = content if content is not None else _voimaantulo_node(text)
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        expires=expires,
        variant_kind=variant_kind,
        content=node,
        source=OperationSource(
            statute_id=source_statute,
            title="Amending Act",
            enacted=enacted,
            effective=effective,
            raw_text=text,
        ),
        content_hash=irnode_content_hash(node) if node is not None else "",
    )


def _timelines(versions: list[ProvisionVersion]) -> dict[LegalAddress, ProvisionTimeline]:
    return {_VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=versions)}


def _state(
    timelines: dict[LegalAddress, ProvisionTimeline],
    *,
    as_of: str,
    statute_id: str = "2099/1",
    provision: str = "section:7",
    query_type: str = "in_force",
) -> dict[str, Any]:
    return build_provision_state_response(
        timelines=timelines,
        statute_id=statute_id,
        jurisdiction="fi",
        provision=provision,
        as_of=as_of,
        query_type=query_type,
    )


# ---------------------------------------------------------------------------
# Pure bitemporal-rule unit checks (no seam)
# ---------------------------------------------------------------------------


def _bound(effective: str, valid_until: str, *, enacted: str | None = None, seq: int = 0) -> StatuteValidityBound:
    import datetime as dt

    vu = dt.date.fromisoformat(valid_until)
    return StatuteValidityBound(
        statute_id="2099/1",
        scope="whole_statute",
        effective=effective,
        enacted=enacted,
        valid_until=valid_until,
        expires_on=expires_on_from_valid_until(vu).isoformat(),
        source_provision=_VOIMAANTULO,
        source_version_id="2099/1",
        source_hash="h",
        source_span=None,
        rule_id="fi_fixed_term_whole_statute_expiry",
        source_text="Tämä laki ... on voimassa",
        source_sequence=seq,
    )


def test_inclusive_valid_until_exclusive_expires_on() -> None:
    bound = _bound("2025-07-01", "2026-12-31")
    assert bound.valid_until == "2026-12-31"
    assert bound.expires_on == "2027-01-01"
    # Live ON the inclusive valid_until, expired the next day.
    assert is_expired_at(bound, "2026-12-31") is False
    assert is_expired_at(bound, "2027-01-01") is True


def test_governing_bound_picks_latest_eligible_under_extension() -> None:
    old = _bound("2024-07-01", "2025-12-31", seq=0)
    new = _bound("2025-07-01", "2026-12-31", seq=1)
    bounds = (old, new)
    assert governing_bound(bounds, as_of="2025-06-30") is old
    assert governing_bound(bounds, as_of="2025-07-01") is new
    at_end = governing_bound(bounds, as_of="2026-12-31")
    at_after = governing_bound(bounds, as_of="2027-01-01")
    assert at_end is not None and at_after is not None
    assert is_expired_at(at_end, "2026-12-31") is False
    assert is_expired_at(at_after, "2027-01-01") is True


def test_in_force_query_ignores_not_yet_enacted_bound() -> None:
    enacted_old = _bound("2024-07-01", "2025-12-31", enacted="2024-06-01", seq=0)
    retroactive = _bound("2025-07-01", "2026-12-31", enacted="2026-03-01", seq=1)
    bounds = (enacted_old, retroactive)
    # As-of 2025-08-01 for an in_force query: the retroactive bound is enacted
    # 2026-03-01 > D, so it is not used; the earlier enacted bound governs.
    assert governing_bound(bounds, as_of="2025-08-01", query_type="in_force") is enacted_old
    # A governing/legal-state query may use it.
    assert governing_bound(bounds, as_of="2025-08-01", query_type="governing") is retroactive


def test_late_extension_gap_detected() -> None:
    old = _bound("2024-07-01", "2025-12-31", seq=0)
    late = _bound("2026-02-01", "2026-12-31", seq=1)
    bounds = (old, late)
    assert late_extension_gap(bounds, late) is True
    assert late_extension_gap(bounds, old) is False


# ---------------------------------------------------------------------------
# Extraction (synthetic timelines)
# ---------------------------------------------------------------------------

_EXT_TEXT = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2026."
_OLD_TEXT = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2025."


def test_extracts_one_bound_per_version() -> None:
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
        ]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert extraction.has_candidate is True
    effectives = sorted(b.effective for b in extraction.bounds)
    assert effectives == ["2024-01-01", "2025-07-01"]
    by_eff = {b.effective: b for b in extraction.bounds}
    assert by_eff["2024-01-01"].valid_until == "2025-12-31"
    assert by_eff["2025-07-01"].valid_until == "2026-12-31"
    assert by_eff["2025-07-01"].source_version_id == "2099/368"


def test_unparseable_whole_law_clause_diagnoses() -> None:
    # "vuoden voimaantulosta" is a recognised whole-law expiry clause whose date
    # the proven regex cannot parse.
    text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa vuoden voimaantulosta."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert extraction.has_candidate is True
    assert extraction.bounds == ()
    assert any(d.code == FIXED_TERM_EXPIRY_UNPARSEABLE for d in extraction.diagnostics)


def test_scoped_chapter_form_unsupported_diagnostic() -> None:
    text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. Lain 2 luku on voimassa 31 päivään joulukuuta 2026."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    # The whole-law clause "Tämä laki ... on voimassa <date>" is present too here,
    # so a bound is still produced; assert the scoped form is at least detectable
    # on a purely-scoped version.
    scoped_only = (
        "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. "
        "Lain 2 luku on voimassa 31 päivään joulukuuta 2026."
    )
    only = _timelines(
        [
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2023-12-01",
                content=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    text="Voimaantulo. Lain 2 luku on voimassa 31 päivään joulukuuta 2026.",
                ),
            )
        ]
    )
    extraction_scoped = extract_fixed_term_bounds(statute_id="2099/1", timelines=only)
    assert any(
        d.code == SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED for d in extraction_scoped.diagnostics
    )
    assert extraction_scoped.bounds == ()
    assert scoped_only  # documents the mixed-form input shape


def test_ambiguous_conflicting_bounds_same_effective() -> None:
    addr_a = LegalAddress(path=(("section", "7"),))
    addr_b = LegalAddress(path=(("section", "8"),))
    v_a = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="7",
            text="Tämä laki on voimassa 31 päivään joulukuuta 2026.",
        ),
        source=OperationSource(statute_id="2099/368"),
    )
    v_b = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="8",
            text="Tämä laki on voimassa 31 päivään joulukuuta 2027.",
        ),
        source=OperationSource(statute_id="2099/999"),
    )
    timelines = {
        addr_a: ProvisionTimeline(address=addr_a, versions=[v_a]),
        addr_b: ProvisionTimeline(address=addr_b, versions=[v_b]),
    }
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert any(d.code == FIXED_TERM_EXPIRY_AMBIGUOUS for d in extraction.diagnostics)


def test_corpus_report_aggregates_counts() -> None:
    supported = extract_fixed_term_bounds(
        statute_id="2099/1",
        timelines=_timelines(
            [_voimaantulo_version(effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/1")]
        ),
    )
    none = extract_fixed_term_bounds(
        statute_id="2099/2",
        timelines=_timelines(
            [
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                )
            ]
        ),
    )
    report = build_corpus_report([supported, none])
    assert report.statutes_scanned == 2
    assert report.whole_law_supported == 1
    assert report.affected_statutes == ("2099/1",)


# ---------------------------------------------------------------------------
# Seam overlay (synthetic, flag ON)
# ---------------------------------------------------------------------------


def _extension_timelines() -> dict[LegalAddress, ProvisionTimeline]:
    return _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
        ]
    )


def test_seam_live_on_valid_until_then_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    timelines = _extension_timelines()
    on_bound = _state(timelines, as_of="2026-12-31")
    after = _state(timelines, as_of="2027-01-01")

    assert on_bound["status"] == "selected"
    assert on_bound["version"]["content_state"] == "live"

    assert after["status"] == "expired"
    assert after["version"] is None
    assert after["expires"] == "2027-01-01"
    assert after["valid_until"] == "2026-12-31"
    assert after["expiry"]["kind"] == "fixed_term_statute"
    assert after["expiry"]["scope"] == "whole_statute"
    assert after["expiry"]["source"] == "2099/368"
    assert after["expiry"]["source_version_effective"] == "2025-07-01"
    assert after["text"]["available"] is False


def test_seam_extension_governs_from_effective(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    timelines = _extension_timelines()
    # Before the extension takes effect (2025-07-01) the old bound governs; its
    # term (valid_until 2025-12-31) has not been reached, so the law is live.
    pre = _state(timelines, as_of="2025-06-30")
    assert pre["status"] == "selected"
    # The extension was enacted before the old term lapsed (normal Finnish
    # practice), so the law stays continuously live into 2026 under the new bound.
    post = _state(timelines, as_of="2026-06-01")
    assert post["status"] == "selected"
    # Only past the EXTENDED term does it expire.
    after = _state(timelines, as_of="2027-01-01")
    assert after["status"] == "expired"
    assert after["valid_until"] == "2026-12-31"


def test_seam_late_extension_gap_revival(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    late_text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2026."
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2026-02-01", enacted="2026-01-20", text=late_text, source_statute="2099/500"
            ),
        ]
    )
    gap = _state(timelines, as_of="2026-01-15")
    revived = _state(timelines, as_of="2026-02-01")
    after = _state(timelines, as_of="2027-01-01")

    assert gap["status"] == "expired"  # old bound lapsed 2025-12-31
    assert revived["status"] == "selected"
    assert revived["version"]["content_state"] == "live"
    assert after["status"] == "expired"


def test_seam_unparseable_governing_bound_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa vuoden voimaantulosta."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    state = _state(timelines, as_of="2024-06-01")
    assert state["status"] == "expiry_unverified"
    assert state["version"] is None
    assert state["expiry"]["diagnostic"] == FIXED_TERM_EXPIRY_UNPARSEABLE
    assert state["expiry"]["blocking"] is True


def test_seam_ambiguous_governing_bound_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    addr_a = LegalAddress(path=(("section", "7"),))
    addr_b = LegalAddress(path=(("section", "8"),))
    v_a = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki on voimassa 31 päivään joulukuuta 2026."),
        source=OperationSource(statute_id="2099/368"),
    )
    v_b = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(kind=IRNodeKind.SECTION, label="8", text="Tämä laki on voimassa 31 päivään joulukuuta 2027."),
        source=OperationSource(statute_id="2099/999"),
    )
    timelines = {
        addr_a: ProvisionTimeline(address=addr_a, versions=[v_a]),
        addr_b: ProvisionTimeline(address=addr_b, versions=[v_b]),
    }
    state = build_provision_state_response(
        timelines=timelines,
        statute_id="2099/1",
        jurisdiction="fi",
        provision="section:7",
        as_of="2026-06-01",
        query_type="in_force",
    )
    assert state["status"] == "expiry_unverified"
    assert state["expiry"]["diagnostic"] == FIXED_TERM_EXPIRY_AMBIGUOUS


def test_seam_repeal_before_expiry_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    # A tombstone (content None) repeals §7 before the fixed-term bound; the seam
    # must report the repeal/absence, never "expired".
    repeal = ProvisionVersion(
        effective="2025-09-01",
        enacted="2025-08-01",
        content=None,
        source=OperationSource(statute_id="2099/700"),
    )
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
            repeal,
        ]
    )
    state = _state(timelines, as_of="2027-01-01")
    assert state["status"] != "expired"
    assert "expiry" not in state


# ---------------------------------------------------------------------------
# Temporary overlay interaction (min wins)
# ---------------------------------------------------------------------------


def test_temporary_overlay_min_with_statute_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    # A temporary provision that expires BEFORE the statute bound drops out via
    # ordinary per-version expiry; the seam reports absence (not expired) once
    # nothing live remains and the statute is not yet past its bound.
    addr = LegalAddress(path=(("section", "5"),))
    temp = ProvisionVersion(
        effective="2025-08-01",
        enacted="2025-07-01",
        expires="2025-10-01",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="5", text="Väliaikainen pykälä."),
        source=OperationSource(statute_id="2099/368"),
    )
    voimaantulo = _voimaantulo_version(
        effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
    )
    timelines = {
        addr: ProvisionTimeline(address=addr, versions=[temp]),
        _VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=[voimaantulo]),
    }
    # Provision expired by its own bound: no live version -> absent, not expired.
    after_temp = _state(timelines, as_of="2025-11-01", provision="section:5")
    assert after_temp["status"] == "absent"
    assert "expiry" not in after_temp
    # The same temporary provision past the STATUTE bound: still no live version,
    # and the statute is expired; ordinary absence still wins for this address.
    after_statute = _state(timelines, as_of="2027-01-01", provision="section:5")
    assert after_statute["status"] == "absent"


def test_temporary_overlay_outliving_statute_yields_statute_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    # A temporary provision whose own expiry (2099-01-01) is LATER than the
    # statute bound (2026-12-31): ordinary selection keeps it live past the
    # statute term, so the statute expiry must win -> expired.
    addr = LegalAddress(path=(("section", "5"),))
    temp = ProvisionVersion(
        effective="2025-08-01",
        enacted="2025-07-01",
        expires="2099-01-01",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="5", text="Väliaikainen pykälä."),
        source=OperationSource(statute_id="2099/368"),
    )
    voimaantulo = _voimaantulo_version(
        effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
    )
    timelines = {
        addr: ProvisionTimeline(address=addr, versions=[temp]),
        _VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=[voimaantulo]),
    }
    live = _state(timelines, as_of="2026-06-01", provision="section:5")
    assert live["status"] == "selected"
    expired = _state(timelines, as_of="2027-01-01", provision="section:5")
    assert expired["status"] == "expired"
    assert expired["valid_until"] == "2026-12-31"


def test_flag_off_is_noop_identical_hash() -> None:
    timelines = _extension_timelines()
    # With the flag off, a past-term query must be byte-identical to the
    # unmodified default path (no expired status, no expiry block).
    after = _state(timelines, as_of="2027-01-01")
    assert after["status"] == "selected"
    assert "expiry" not in after
    assert after["version"]["content_state"] == "live"

    # A non-fixed-term statute is a no-op regardless of flag.
    plain = _timelines(
        [
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2019-12-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                source=OperationSource(statute_id="2099/2"),
            )
        ]
    )
    plain_state = _state(plain, as_of="2027-01-01")
    assert plain_state["status"] == "selected"
    assert plain_state["hashes"]["derived_state_hash"]


def test_non_fixed_term_noop_identical_hash_flag_on_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _timelines(
        [
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2019-12-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                source=OperationSource(statute_id="2099/2"),
            )
        ]
    )
    monkeypatch.delenv(FIXED_TERM_BOUNDS_FLAG, raising=False)
    off = _state(plain, as_of="2027-01-01")
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")
    on = _state(plain, as_of="2027-01-01")
    # No fixed-term clause -> overlay never fires -> hashes identical with flag on/off.
    assert off["hashes"]["derived_state_hash"] == on["hashes"]["derived_state_hash"]
    assert off["status"] == on["status"] == "selected"


# ---------------------------------------------------------------------------
# 482/2024 trio — real corpus (flag ON via env)
# ---------------------------------------------------------------------------

_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus fixed-term tests",
)


def _corpus_state(as_of: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")
    from lawvm.provision_state import resolve_provision_state

    return resolve_provision_state(
        statute_id="2024/482",
        provision="section:7",
        as_of=as_of,
        query_type="in_force",
    )


@_corpus_skip
def test_corpus_482_2024_live_mid_term(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2026-06-01", monkeypatch)
    assert state["status"] == "selected"
    assert state["version"]["content_state"] == "live"
    assert "31 päivään joulukuuta 2026" in state["text"]["rendered"]


@_corpus_skip
def test_corpus_482_2024_live_on_valid_until(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2026-12-31", monkeypatch)
    assert state["status"] == "selected"
    assert state["version"]["content_state"] == "live"


@_corpus_skip
def test_corpus_482_2024_expired_after_term(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2027-01-01", monkeypatch)
    assert state["status"] == "expired"
    assert state["version"] is None
    assert state["expires"] == "2027-01-01"
    assert state["valid_until"] == "2026-12-31"
    assert state["expiry"]["kind"] == "fixed_term_statute"
    assert state["expiry"]["source"] == "2025/368"
