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
     "5728007325c9d1c64a7aea0a821d73d6a7f0f5f21b566a4c57d08ad062beea5b"),  # R1 esitutkintalaki 3 luku 1 §; re-pinned after same-parent subsection relabel ordering stopped skipping the 2026/269 2→3, 3→4 chain before inserting new 2 mom. Text remains source-owned by 2026/269 and oracle-perfect; prior pin captured the unstable relabel fold.
    ("2009/273", "section:6", _AS_OF, _QT,
     "dc091ad938ef246a20540eba93330ab175aa97f03670b0af9972aba189a6d5ac"),  # R3 vaalirahoituslaki 6 §
    ("2009/273", "section:6a", _AS_OF, _QT,
     "f0aae8ddd0a36fd69a53b2b7da5a9bfb2ae5ca8d20a4f2604d5192bff86538a7"),  # R4 vaalirahoituslaki 6 a § (survival witness)
    ("2009/273", "section:10", _AS_OF, _QT,
     "84f47ddc3cbe0787e134be3ce67a4831e0955e1797df569f667589ced7ca9612"),  # R5 vaalirahoituslaki 10 §; stale carried subsection-2 text removed
    ("2024/482", "section:7", _AS_OF, _QT,
     "5ceafd2fe47777760c7177e14f6cbd06810d7aa851ad595ce83a50e8f91608ad"),  # R7 laki 482/2024 7 §; re-pinned after payload-local fixed-term validity became typed temporal metadata (text/content hash stable; expires 2027-01-01 from "voimassa 31 päivään joulukuuta 2026").
    # --- E1_LAWVM_ROUTE_GROUNDING_HANKINTALAKI.md (hankintalaki 1397/2016 + kilpailulaki 948/2011) ---
    ("2011/948", "section:30a", _AS_OF, _QT,
     "30b540140108da7c8dc57482fb9411f2fbe436e44ed4a6bd965251379e8c562e"),  # kilpailulaki 30a §; re-pinned after 2021/546 chapter-start migration evidence was corrected from five bogus moves to one owned move; text/content hash and selected version stayed stable.
    ("2011/948", "section:30b", _AS_OF, _QT,
     "18f22106a672aeecd242bb2d2f227f94cf482ca29a3f7d56e0628f3f1111d900"),  # kilpailulaki 30b §; same 2011/948 lineage-count re-pin, no text-state drift.
    ("2011/948", "section:30c", _AS_OF, _QT,
     "e54944fd6d6c298664c87a977ed16421a48f0441f45ca99caae71db2df57569b"),  # kilpailulaki 30c §; same 2011/948 lineage-count re-pin, no text-state drift.
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
    ("2023/703", "section:17", _AS_OF, _QT,
     "cd032a684aeaa2525aed2e1bd829541243981f3cbb67252cd89f7473315b9c1a"),  # base, enacted 2023-04-14; re-pinned after provision-state resolution returned canonical container-scoped address part:1/chapter:4/section:17 for the section-only query.
    ("2024/482", "section:4", _AS_OF, _QT,
     "d78b126296f4b85a919684df9c89a03a35d0306613fc395fbd4644db7bab9725"),  # base, enacted 2024-07-16
    ("2019/808", "section:7", _AS_OF, _QT,
     "c6d31fb964f89bf6f33b23f154b237fcfad9d3291a8c901ec2aa4f5e3fde8536"),  # base, enacted 2019-07-05; re-pinned after provision-state resolution returned canonical container-scoped address chapter:2/section:7 for the section-only query.
]

# KNOWN DIVERGENCES (xfail). New divergences (real consolidation updates or
# fresh replay instability) get added here as strict xfail with the
# observed-vs-pinned hashes inline, so a later re-convergence flips to XPASS.
_KNOWN_DIVERGENT: list[tuple[str, str, str, str, str, str]] = [
    # (statute, provision, as_of, qt, pinned_hash, reason)
    (
        "2016/1397",
        "section:141",
        _AS_OF,
        _QT,
        "3fc5276b4c9956e9968898440e8a6b943f42a6cd450360e4cae7efe66ba6af0e",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "8ecc4836a49e190cda92d77150480c0873e7f8e0a94bea5cac73dcfd11bb17a9, "
        "content b1cba16bb1455fc16228f11ad988f589b213500c3a82b4b55d56a789dd4d47a4",
    ),
    (
        "2016/1397",
        "section:75",
        _AS_OF,
        _QT,
        "2fb2545240a170a101a93d9bad62ddae6a63554efcfd6841047071a8b69ef0dd",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "4e2321c8a6d20e31ce83f28d40998a1c9f00bcbedadda6f4cb223506162d3af2, "
        "content e44ad114b98ecafe7b8426d71ceea8494cbd7612ef55f85334082649a219cea1",
    ),
    (
        "2016/1397",
        "section:124",
        _AS_OF,
        _QT,
        "aff9cbef8805548c72d661afa94caf3588ea40723936e48830c6f79b333452c9",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "51e8e9568b3a70c2db0b00e8525d29114c2cf0a2d7ce0856e098e5f9390e0982, "
        "content c1e0a7bcde9f528cf7cbf60ec84a16817c6f1c5f85a4e9f330889fd3f5f6c2f8",
    ),
    (
        "2016/1397",
        "section:132",
        _AS_OF,
        _QT,
        "081408162630503e3c3801d1b71a414272639e848a9d39fe892617640721f3ca",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "4e6f7f562c28b4f2c58ffe25d8b1d75af0f2d27f701de7bcf5fc73bfc8cb7c10, "
        "content 6608993ffe57d78825f72553eb4bdf48f7f7539cbea56eefcfc47c580c2f76a0",
    ),
    (
        "2016/1397",
        "section:133",
        _AS_OF,
        _QT,
        "d6b1a51856203763501a6a6384c9a0ad769d36df87e40ec8c7353dabc64754be",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "f165007540d0af23c631a793c6ba5f2fe38aea00786596649a1872b5716d70a8, "
        "content 938215ea1ac2e9dafb33cec66b39f32ebece5cc4a3696921f772371102edda7c",
    ),
    (
        "2016/1397",
        "section:134",
        _AS_OF,
        _QT,
        "f7f179fd9bcecd864adc162cb81b13762f5dc38acbd2ff57a41b2d10e46e00f8",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "f3bcc2b83ee154e22832510810419e6d83977ea1dcc31dadfc6bf319a730e522, "
        "content 2d7abf9aeca8dc0f29519d7f9869e61545f2fe8c12a09522ec8183a0b6a8e327",
    ),
    (
        "2016/1397",
        "section:139",
        _AS_OF,
        _QT,
        "ce097e6e1e30d0d0ecd5c5a0231926e43c2f448938cf7c7a0ee5d364e9188cee",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "d0b8aa6dda3b82050c0c46475cc80ef552a8cec8744684375e6e0f020339af1c, "
        "content 60c63c1d0e8645d23523527f8bce00d26d3c4d49cbcf6dc64a34e61caad6e551",
    ),
    (
        "2016/1397",
        "section:146",
        _AS_OF,
        _QT,
        "276be539bc5e011590904ee2c7f5197ae01b0af6f948a580bfa56b7e09e37f43",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "d2e8869d702541e5d3b461446616149d57071ababb015ffa7e959d6d259746ff, "
        "content fd9ed7c58dec3907f22409c812f751f737861d953389f2fb1b93080b87cbe349",
    ),
    (
        "2016/1397",
        "section:154",
        _AS_OF,
        _QT,
        "35175973efa427ad627b220893f2737d3b8ddb5c74214c84c6ad626bc7ec5d9d",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "7fda697436b3f47a24df5f1c9c9185dd4bcf5d112ac0207dfebbea4c6ef5449e, "
        "content 3e4bf3e3e8fa4b52a0fdfa2f3565829a2a5a5992e559d492f02b6cb437b440e5",
    ),
    (
        "2016/1397",
        "section:163",
        _AS_OF,
        _QT,
        "e2a1d2cbec86b96adb8a04c0d30735f0c46fe6fc82cfbb990b8d62fd1bb52127",
        "current-corpus divergence reproduced on clean HEAD; current derived "
        "12ef8cfb9f6f571d5e524e4dacedf652b339ce88cda3e13caaf64d0f1a582eaf, "
        "content c46909116264f92b855c8db8ee9044f101e5cd36580c3e45b720f14c0311ccea",
    ),
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
