"""StageResult WAIST #1 — Finland source-identity staged read conversion.

``TransparentCorpusStore.read_source_staged`` returns ``StageResult[bytes]``
carrying:
  * ``value`` — the source bytes (byte-identical to ``read_source``);
  * ``evidence`` — the content-addressed ``SourceWitness`` (sha256 over the
    ACTUAL bytes, never derived from ``sid``) — the un-severed witness;
  * ``authority`` — a ``SourceBundleAdmission`` from the conservative Farchive
    policy, firewalled (``replay_authorized`` stays False).

The fire-drill proves the witness DIGEST reaches the certificate
``source_bundle_root`` (derived from the READ, not reconstructed from ``sid``),
i.e. the witness is no longer severed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lawvm.corpus_store import CorpusStore
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.stage_result import StageResult

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus source-identity tests",
)


def _present_source_id(corpus: CorpusStore) -> str:
    ids = corpus.list_statute_ids()
    for sid in ids:
        if corpus.read_source(sid) is not None:
            return sid
    pytest.skip("no source-bearing statute id in corpus")


@pytest.fixture(scope="module")
def corpus() -> CorpusStore:
    from lawvm.finland.corpus import _get_corpus_store

    return _get_corpus_store()


@_corpus_skip
def test_staged_value_is_byte_identical_to_read_source(corpus: CorpusStore) -> None:
    sid = _present_source_id(corpus)
    staged = corpus.read_source_staged(sid)
    assert isinstance(staged, StageResult)
    assert staged.value == corpus.read_source(sid)


@_corpus_skip
def test_evidence_digest_is_content_sha256_not_from_sid(corpus: CorpusStore) -> None:
    sid = _present_source_id(corpus)
    staged = corpus.read_source_staged(sid)
    assert staged is not None
    assert len(staged.evidence.witnesses) == 1
    witness = staged.evidence.witnesses[0]
    assert isinstance(witness, SourceWitness)
    assert isinstance(witness.digest, DigestWitness)
    content_sha = hashlib.sha256(staged.value).hexdigest()
    assert witness.digest.digest == content_sha
    # NOT derived from the id.
    assert witness.digest.digest != hashlib.sha256(sid.encode("utf-8")).hexdigest()


@_corpus_skip
def test_authority_is_firewalled_and_admission_carried(corpus: CorpusStore) -> None:
    sid = _present_source_id(corpus)
    staged = corpus.read_source_staged(sid)
    assert staged is not None
    # Firewall: source footing is never replay authority.
    assert staged.authority.replay_authorized is False
    admission = staged.authority.source_admission
    assert admission is not None
    assert admission.admitted is True
    assert admission.admission_status == "source_bundle_admitted"
    # Coverage: a single-artifact read has nothing to partition (identity).
    assert staged.coverage.is_partition()
    assert staged.coverage.total == 0
    assert staged.residuals == ()


@_corpus_skip
def test_absent_read_returns_none(corpus: CorpusStore) -> None:
    # Preserve the read_source protocol: absent -> None (ESCALATE-1 decision).
    assert corpus.read_source_staged("9999/99999") is None


@_corpus_skip
def test_witness_methods_round_trip_against_staged(corpus: CorpusStore) -> None:
    sid = _present_source_id(corpus)
    staged = corpus.read_source_staged(sid)
    assert staged is not None
    witnessed = corpus.read_source_witness(sid)
    assert witnessed is not None
    data, witness = witnessed
    assert data == staged.value
    assert witness.digest is not None
    staged_witness = staged.evidence.witnesses[0]
    assert isinstance(staged_witness, SourceWitness)
    assert staged_witness.digest is not None
    assert witness.digest.digest == staged_witness.digest.digest


# ---------------------------------------------------------------------------
# Fire-drill: the witness DIGEST reaches the certificate source_bundle_root,
# derived from the READ (the un-severed proof).
# ---------------------------------------------------------------------------


@_corpus_skip
def test_witness_digest_reaches_certificate_source_bundle_root(
    tmp_path: Path, corpus: CorpusStore
) -> None:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    statute_id = "482/2024"
    out = tmp_path / "cert"
    build_certificate_bundle(
        statute_id, out, graph_store_root=tmp_path / "provenance_graph"
    )

    # Every committed source identity's raw_source_hash must equal the digest a
    # content WITNESS read computes for that artifact (read_source_witness for
    # enacted text, read_amendment_witness for amending text). This proves the
    # source_bundle_root hash flowed from the witnessed read, not an independent
    # _sha256_rendered — the un-severed property.
    sources = json.loads(
        (out / "sources" / "source_artifacts.json").read_text(encoding="utf-8")
    )
    assert sources, "certificate emitted no source identities"

    verified = 0
    for row in sources:
        num, year = str(row["canonical_id"]).split("/")
        sid = f"{year}/{num}"
        if row["source_role"] == "enacted_text":
            witnessed = corpus.read_source_witness(sid)
        else:
            witnessed = corpus.read_amendment_witness(sid)
        assert witnessed is not None, f"witness read absent for {sid}"
        _, witness = witnessed
        assert witness.digest is not None
        expected_hash = f"sha256:{witness.digest.digest}"
        assert row["raw_source_hash"] == expected_hash, (
            f"committed raw_source_hash for {sid} ({row['raw_source_hash']}) does "
            f"not match the content witness digest ({expected_hash}) — the cert "
            "source identity is not derived from the witnessed read"
        )
        verified += 1
    assert verified > 0, "no source identities verified against the witness surface"


# ---------------------------------------------------------------------------
# Divergent-witness pipeline fire-drill (WAIST #1 behavioral guard).
#
# The production amendment pipeline (`process_muutoslaki` ->
# `_verify_staged_source_identity` at process_pipeline.py:~337) raises on a
# content-witness divergence / un-admitted lane, but no test drove it: the green
# corpus has matching digests, so DELETING the call passed the whole suite. These
# drills inject a divergent / un-admitted staged read and assert the production
# path raises — RED on the call deletion (the anti-sever property).
# ---------------------------------------------------------------------------

_PARENT_ID = "2024/482"
_AMENDMENT_ID = "2025/368"  # an amendment 2024/482 actually processes


class _DivergentWitnessStore(CorpusStore):
    """Delegates to the real store but tampers ONE amendment's staged-read witness.

    ``mode="digest"`` returns the real bytes with a DELIBERATELY WRONG
    ``DigestWitness`` (the silent content-divergence the witness exists to catch);
    ``mode="unadmitted"`` returns a staged read whose ``SourceBundleAdmission`` is
    NOT admitted. Both must make ``_verify_staged_source_identity`` raise.
    """

    def __init__(self, inner: CorpusStore, *, target_id: str, mode: str) -> None:
        self._inner = inner
        self._target_id = target_id
        self._mode = mode

    def read_source(self, sid: str) -> bytes | None:
        return self._inner.read_source(sid)

    def read_oracle(self, sid: str) -> bytes | None:
        return self._inner.read_oracle(sid)

    def read_media(self, sid: str, filename: str) -> bytes | None:
        return self._inner.read_media(sid, filename)

    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        return self._inner.read_corrigendum_media(sid, filename)

    def list_statute_ids(self) -> list[str]:
        return self._inner.list_statute_ids()

    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        return self._inner.oracle_path_index(**kwargs)

    def read_locator(self, locator: str) -> bytes | None:
        return self._inner.read_locator(locator)

    def read_source_staged(self, sid: str):  # type: ignore[override]
        staged = self._inner.read_source_staged(sid)
        if staged is None or sid != self._target_id:
            return staged
        from dataclasses import replace

        from lawvm.core.source_acquisition import SourceBundleAdmission
        from lawvm.core.stage_result import (
            AuthoritySurface,
            EvidenceBundle,
        )

        if self._mode == "digest":
            witness = staged.evidence.witnesses[0]
            assert isinstance(witness, SourceWitness)
            assert witness.digest is not None
            wrong = DigestWitness(
                digest_algorithm="sha256",
                digest="0" * 64,  # NOT sha256(bytes) — a divergent witness
            )
            tampered_witness = replace(witness, digest=wrong)
            return replace(staged, evidence=EvidenceBundle((tampered_witness,)))

        # mode == "unadmitted": carry a NON-admitted source lane.
        unadmitted = SourceBundleAdmission(
            assertion_id=f"fire-drill:{sid}",
            admitted=False,
            admission_status="source_bundle_rejected",
            policy_id="fire-drill.policy",
            source_lane="amendment_source_xml",
        )
        return replace(staged, authority=AuthoritySurface(source_admission=unadmitted))


def _drive_amendment(store: CorpusStore) -> None:
    from lxml import etree

    from lawvm.finland.helpers import _fi_label_postprocessor
    from lawvm.finland.process_pipeline import process_muutoslaki
    from lawvm.finland.process_request import ProcessAmendmentRequest
    from lawvm.finland.process_result_builder import ProcessAmendmentSinks
    from lawvm.finland.statute import ReplayState, StatuteContext

    parent_xml = store.read_source(_PARENT_ID)
    assert parent_xml is not None
    ctx = StatuteContext.from_xml(parent_xml, _fi_label_postprocessor)
    import copy

    state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    process_muutoslaki(
        ProcessAmendmentRequest(
            amendment_id=_AMENDMENT_ID,
            state=state,
            ctx=ctx,
            replay_mode="legal_pit",
            parent_id=_PARENT_ID,
            corpus=store,
        ),
        ProcessAmendmentSinks(),
    )
    # silence unused import warning when lxml is needed only transitively
    del etree


@_corpus_skip
def test_divergent_witness_digest_fails_the_pipeline(corpus: CorpusStore) -> None:
    # A staged read whose witness digest does NOT match the source bytes must make
    # the production pipeline fail loud (the silent content-divergence the witness
    # catches). RED if the `_verify_staged_source_identity` call is deleted.
    store = _DivergentWitnessStore(corpus, target_id=_AMENDMENT_ID, mode="digest")
    with pytest.raises(ValueError, match="diverges from the source model digest"):
        _drive_amendment(store)


@_corpus_skip
def test_unadmitted_lane_fails_the_pipeline(corpus: CorpusStore) -> None:
    # A staged read whose source lane is NOT admitted must make the pipeline fail
    # loud (a typed boundary fact, not a silent acceptance).
    store = _DivergentWitnessStore(corpus, target_id=_AMENDMENT_ID, mode="unadmitted")
    with pytest.raises(ValueError, match="not admitted to the bundle"):
        _drive_amendment(store)


@_corpus_skip
def test_matching_witness_does_not_raise_baseline(corpus: CorpusStore) -> None:
    # 0-delta control: the UNtampered staged read drives the same path without
    # raising on the source-identity boundary (proves the drills above isolate the
    # divergence, not an unrelated pipeline error).
    _drive_amendment(corpus)
