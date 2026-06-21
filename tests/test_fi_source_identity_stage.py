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
