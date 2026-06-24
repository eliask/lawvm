"""Cross-repo consumer-contract pins for LawVM provision-state.

Downstream consumers can pin LawVM's `provision-state` resolution as:
verdict CONFIRMED + `derived_state_hash`.  The contract is one-directional:
LawVM may fail loudly anywhere, but it must NEVER return the WRONG text-state
under a CONFIRMED verdict with a stable hash.

The current fixture set was extracted from two MeVM fact-packs (read-only):
  ../mevm/research/state_map/TRACK2_GROUNDING_FACTPACK_2026-06-10.md
  ../mevm/research/state_map/E1_LAWVM_ROUTE_GROUNDING_HANKINTALAKI.md

Each pin re-runs the exact (statute, address, as-of, query-type) query the
consumer used and asserts the `derived_state_hash` reproduces. All pins were
minted with `--as-of 2026-06-10 --query-type in_force -j fi` against the default
(latest cached editorial) oracle selector — the same path the seam exposes.

A divergence here is NOT a test bug to paper over: it is either a real
consolidation update (oracle text moved) or replay instability (same text,
different version metadata / hash). Both are exactly what this test exists to
catch. Known-diverging pins are marked xfail with the observed-vs-pinned hashes
inline so the divergence is auditable and a re-convergence flips the xfail to an
unexpected pass (XPASS) loudly.

If the Finland corpus (data/finlex.farchive) is unavailable, the whole module
skips rather than fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# --- corpus availability gate -------------------------------------------------
_LAWVM_ROOT = Path(__file__).resolve().parents[1]
_FINLEX_FARCHIVE = _LAWVM_ROOT / "data" / "finlex.farchive"
_CORPUS_AVAILABLE = _FINLEX_FARCHIVE.exists()

pytestmark = [
    pytest.mark.consumer_contract,
    pytest.mark.skipif(
        not _CORPUS_AVAILABLE,
        reason=f"Finland corpus not available at {_FINLEX_FARCHIVE}",
    ),
]

# Pin shape: (statute_id, provision, as_of, query_type, derived_state_hash).
# as_of/query_type are uniform across both fact-packs (2026-06-10 / in_force).
_AS_OF = "2026-06-10"
_QT = "in_force"

# Pins that reproduce on the current build (CONFIRMED / status==selected at mint).
_PINS: list[tuple[str, str, str, str, str]] = [
    # --- TRACK2_GROUNDING_FACTPACK_2026-06-10.md ---
    ("2011/805", "chapter:3/section:1", _AS_OF, _QT,
     "ff1cea4df3163a3edb8492e270949ebcd847f2521b53766037fa4e1195134a74"),  # R1 esitutkintalaki 3 luku 1 §; re-pinned after same-parent subsection relabel ordering stopped skipping the 2026/269 2→3, 3→4 chain before inserting new 2 mom. Text remains source-owned by 2026/269 and oracle-perfect; prior pin captured the unstable relabel fold.
    ("2009/273", "section:6", _AS_OF, _QT,
     "68b1110d5f48a2cf3ad40585fb220135ae697baec96fc3399c58acbd92765eed"),  # R3 vaalirahoituslaki 6 §
    ("2009/273", "section:6a", _AS_OF, _QT,
     "0d4b37c2f1313e7cf29563f08a56e4717ff954615071f3d3ffb5e57c6c0a36cc"),  # R4 vaalirahoituslaki 6 a § (survival witness)
    ("2009/273", "section:10", _AS_OF, _QT,
     "7efdaefb6e5175d42fb047c8c83023a00ae85d3ea384ad5e8a98325a1cada1fd"),  # R5 vaalirahoituslaki 10 §; stale carried subsection-2 text removed
    ("2024/482", "section:7", _AS_OF, _QT,
     "36b895f84d205ec6c6982363972866f7a59c2c50da14a1933c2eb5c31b02b8fd"),  # R7 laki 482/2024 7 § (voimaantulo)
    # --- E1_LAWVM_ROUTE_GROUNDING_HANKINTALAKI.md (hankintalaki 1397/2016 + kilpailulaki 948/2011) ---
    ("2016/1397", "section:141", _AS_OF, _QT,
     "36db41b746b5c4954ab0c00404207d88e61e2b31a9e1802e9477c44110c89263"),  # § 141 (2024 amendment version)
    ("2016/1397", "section:163", _AS_OF, _QT,
     "9f2f3e81c8b8650ef570dfbb969363d832f227d11c188fd2b2c5cc084080d443"),  # § 163 (2021 amendment version)
    ("2011/948", "section:30a", _AS_OF, _QT,
     "11bb9ad6db2d85872e15bbbd3a2dd6bbf9bb798c1a618a03c01a9ba86c1e8f45"),  # kilpailulaki 30a §; re-pinned after 2021/546 chapter-start migration evidence was corrected from five bogus moves to one owned move; text/content hash and selected version stayed stable.
    ("2011/948", "section:30b", _AS_OF, _QT,
     "d6e62f59a4dca2a20ea07acaf2fdd4a456f75db7ae0b098cca6affdcfdc7523c"),  # kilpailulaki 30b §; same 2011/948 lineage-count re-pin, no text-state drift.
    ("2011/948", "section:30c", _AS_OF, _QT,
     "b33f8201311c1e4a9702d1c830e48e6f326a268b6e402f62be86b31f6306d85a"),  # kilpailulaki 30c §; same 2011/948 lineage-count re-pin, no text-state drift.
    # --- RE-CONVERGED original-enactment-base pins (see history note below) ---
    # These were a single instability class: every pin whose governing version is
    # the ORIGINAL-ENACTMENT BASE. The build briefly seeded base-provision
    # `enacted` from the 0000-00-00 effective date instead of the statute's
    # enactment (FRBR signature) date, so `enacted` reported 0000-00-00 and the
    # `enacted`-derived hash diverged even though no text/consolidation changed
    # (content_hash was byte-identical throughout). Restoring base-provision
    # `enacted` from the Finland FRBR signature date re-converged every hash to
    # its mint value, so these are back in the reproduce set.
    #   - hankintalaki 1397/2016 §§75/124/132/133/134/139/146/154: enacted 2016-12-29
    #   - asiakastietolaki 703/2023 §17: enacted 2023-04-14
    #   - laki 482/2024 §4: enacted 2024-07-16
    #   - hallintoprosessilaki 808/2019 §7: enacted 2019-07-05
    ("2016/1397", "section:75", _AS_OF, _QT,
     "c6f65ea384dd7574aa849fae84a17d37b75b45792f9c5b5fa155b53958eeb7a4"),  # base, enacted 2016-12-29
    ("2016/1397", "section:124", _AS_OF, _QT,
     "90eb3b25ac8a9d8c6681e3dae7844f051581ee5c3cd16e26fba571ed720e5dcc"),  # base, enacted 2016-12-29
    ("2016/1397", "section:132", _AS_OF, _QT,
     "08a40ac776240a587cb6cc13fa623288b4987909c10aa205d4a306e680fe9e62"),  # base, enacted 2016-12-29
    ("2016/1397", "section:133", _AS_OF, _QT,
     "2b94b2bcfdcf6b8a0725494d2da8a7f812feb51ac87e784b90aa49c2f952c2cc"),  # base, enacted 2016-12-29
    ("2016/1397", "section:134", _AS_OF, _QT,
     "e4fb1b4d968ebc2d629487eaba015c14487085b13542c1fb904ea32865b2cb6b"),  # base, enacted 2016-12-29
    ("2016/1397", "section:139", _AS_OF, _QT,
     "8813f0ea0d64c8c20f3d76f99c6bf5d0edfadde1282e30f81446e95bb5e19c1b"),  # base, enacted 2016-12-29
    ("2016/1397", "section:146", _AS_OF, _QT,
     "004ff4a8b450af8b0a33f23a85f59253e7a366e96fe8d1d31a0f1d669b2b3ec2"),  # base, enacted 2016-12-29
    ("2016/1397", "section:154", _AS_OF, _QT,
     "4d9e2e72d0d318f6ad47991f94f871a0b67d117ded94dd16dcd510b67616df72"),  # base, enacted 2016-12-29
    ("2023/703", "section:17", _AS_OF, _QT,
     "37fbd0c9796bca479b4af9e0d2123a7c0750cce23dc3a6eac3a3ac51a4f49e03"),  # base, enacted 2023-04-14; re-pinned after provision-state resolution returned canonical container-scoped address part:1/chapter:4/section:17 for the section-only query.
    ("2024/482", "section:4", _AS_OF, _QT,
     "90c93604d37d7c989e53f0b6a7d74339fa503bbffe4d7e2f76eb110acab2d8f7"),  # base, enacted 2024-07-16
    ("2019/808", "section:7", _AS_OF, _QT,
     "090f887d35e156ab3537c20ab81d52643994f64be0e3ab108208e1f1e2503aef"),  # base, enacted 2019-07-05; re-pinned after provision-state resolution returned canonical container-scoped address chapter:2/section:7 for the section-only query.
]

# KNOWN DIVERGENCES (xfail). Empty: the original-enactment-base date class has
# re-converged and moved into _PINS above. New divergences (real consolidation
# updates or fresh replay instability) get added here as strict xfail with the
# observed-vs-pinned hashes inline, so a later re-convergence flips to XPASS.
_KNOWN_DIVERGENT: list[tuple[str, str, str, str, str, str]] = [
    # (statute, provision, as_of, qt, pinned_hash, reason)
]


@pytest.fixture(scope="module")
def provision_state_runtime_for_statute():
    from lawvm.provision_state import compile_provision_state_runtime

    runtimes = {}

    def runtime_for(statute_id: str):
        runtime = runtimes.get(statute_id)
        if runtime is None:
            runtime = compile_provision_state_runtime(statute_id=statute_id)
            runtimes[statute_id] = runtime
        return runtime

    return runtime_for


def _resolve_hash(statute_id: str, provision: str, as_of: str, query_type: str) -> tuple[str, str, str]:
    """Re-run the consumer's exact seam path; return (status, derived_hash, content_hash)."""
    from lawvm.provision_state import resolve_provision_state

    payload = resolve_provision_state(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        jurisdiction="fi",
    )
    hashes = payload["hashes"]
    return payload["provision_status"], hashes["derived_state_hash"], hashes["content_hash"]


def _assert_source_locator_span(payload: dict) -> None:
    """Consumer pins need source footing, not only a stable derived state hash."""
    locator = payload.get("source_locator") or {}
    detail = locator.get("detail") or {}
    assert payload.get("source_locator_status") == "canonical_document_locator"
    assert locator.get("artifact_digest")
    assert locator.get("artifact_digest_algorithm") == "sha256"
    assert locator.get("char_span"), "source_locator must expose an XML character span"
    assert locator.get("byte_span"), "source_locator must expose an XML byte span"
    assert detail.get("hash_role") == "excluded_from_derived_state_hash"
    artifact_kind = locator.get("artifact_kind")
    if artifact_kind == "base_statute_xml":
        assert detail.get("source_xml_span_status") == "available"
        assert detail.get("char_span_status") == "finlex_raw_xml_eid_element_scan"
    elif artifact_kind == "operation_source_statute_xml":
        assert detail.get("operation_source_xml_span_status") == "available"
        assert str(detail.get("char_span_status") or "").startswith("operation_source_raw_xml_")
        witness = detail.get("source_witness") or {}
        assert witness.get("artifact_span_status") == detail.get("char_span_status")
        assert witness.get("artifact_char_span") == locator.get("char_span")
        assert witness.get("artifact_byte_span") == locator.get("byte_span")
    else:
        raise AssertionError(f"unexpected source locator artifact_kind: {artifact_kind!r}")


@pytest.mark.parametrize(
    "statute_id,provision,as_of,query_type,pinned_hash",
    _PINS,
    ids=[f"{p[0]}:{p[1]}@{p[2]}" for p in _PINS],
)
def test_provision_state_consumer_pin_reproduces(
    provision_state_runtime_for_statute,
    statute_id: str,
    provision: str,
    as_of: str,
    query_type: str,
    pinned_hash: str,
) -> None:
    """A CONFIRMED consumer pin must reproduce its hash on the current build."""
    payload = provision_state_runtime_for_statute(statute_id).resolve(
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        jurisdiction="fi",
    )
    hashes = payload["hashes"]
    status = payload["provision_status"]
    derived_hash = hashes["derived_state_hash"]
    # The consumer only mints CONFIRMED from a resolved/selected state.
    assert status == "selected", (
        f"{statute_id} {provision}: expected status 'selected' (CONFIRMED-eligible), got {status!r}"
    )
    _assert_source_locator_span(payload)
    assert derived_hash == pinned_hash, (
        f"{statute_id} {provision} @{as_of}/{query_type}: derived_state_hash DIVERGED.\n"
        f"  pinned : {pinned_hash}\n"
        f"  current: {derived_hash}\n"
        f"This is a consolidation update or replay instability — investigate, do not re-pin blindly."
    )


@pytest.mark.parametrize(
    "statute_id,provision,as_of,query_type,pinned_hash,reason",
    _KNOWN_DIVERGENT,
    ids=[f"{p[0]}:{p[1]}@{p[2]}" for p in _KNOWN_DIVERGENT],
)
@pytest.mark.xfail(reason="known derived_state_hash divergence; see inline reason", strict=True)
def test_provision_state_consumer_pin_known_divergent(
    statute_id: str, provision: str, as_of: str, query_type: str, pinned_hash: str, reason: str
) -> None:
    """Pins known to diverge on the current build (xfail, strict).

    Strict xfail means an unexpected PASS (re-convergence) fails the suite loudly,
    forcing a deliberate move back into _PINS.
    """
    status, derived_hash, _content_hash = _resolve_hash(statute_id, provision, as_of, query_type)
    assert status == "selected"
    assert derived_hash == pinned_hash, f"{reason}: pinned={pinned_hash} current={derived_hash}"


# --- base-version enacted-date regression -------------------------------------
# Direct guard for the root cause behind the re-converged pins above: an
# un-amended provision (governing version == ORIGINAL-ENACTMENT BASE) must report
# a populated `enacted` equal to the statute's enactment (FRBR signature) date,
# not the 0000-00-00 effective sentinel. `enacted` feeds derived_state_hash, so a
# regression here silently breaks every downstream grounding pin for un-amended
# provisions. `effective` intentionally stays 0000-00-00 on the base version
# (the governing rail checks effective only and must be unaffected).
_BASE_ENACTED_CASES: list[tuple[str, str, str]] = [
    ("2016/1397", "section:75", "2016-12-29"),   # hankintalaki, un-amended base
    ("2024/482", "section:4", "2024-07-16"),     # laki 482/2024, un-amended base
    ("2019/808", "section:7", "2019-07-05"),     # hallintoprosessilaki, un-amended base
]


@pytest.mark.parametrize(
    "statute_id,provision,expected_enacted",
    _BASE_ENACTED_CASES,
    ids=[f"{c[0]}:{c[1]}" for c in _BASE_ENACTED_CASES],
)
def test_base_version_reports_populated_enacted_date(
    provision_state_runtime_for_statute,
    statute_id: str,
    provision: str,
    expected_enacted: str,
) -> None:
    """Un-amended provisions must report the statute enactment date as `enacted`."""
    payload = provision_state_runtime_for_statute(statute_id).resolve(
        provision=provision,
        as_of=_AS_OF,
        query_type=_QT,
        jurisdiction="fi",
    )
    assert payload["provision_status"] == "selected", (
        f"{statute_id} {provision}: expected status 'selected', got {payload['status']!r}"
    )
    version = payload.get("version") or {}
    enacted = version.get("enacted")
    assert enacted == expected_enacted, (
        f"{statute_id} {provision}: base-version enacted date regressed.\n"
        f"  expected: {expected_enacted}\n"
        f"  observed: {enacted!r}\n"
        f"A 0000-00-00 enacted on an un-amended base version silently changes "
        f"derived_state_hash for every downstream grounding pin."
    )
    # effective stays the 0000-00-00 sentinel on the base version by design:
    # the governing rail keys on effective only and must remain unaffected.
    assert version.get("effective") == "0000-00-00", (
        f"{statute_id} {provision}: base-version effective unexpectedly changed to "
        f"{version.get('effective')!r}; the enacted fix must not touch effective."
    )
