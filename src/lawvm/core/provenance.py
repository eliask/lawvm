"""Shared provenance carriers for core timeline and replay surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, FrozenSet, Literal, Tuple

from lawvm.core.branch_authority import AuthorityLayer, BranchContext, COMMENCED_STATUS, ENACTED_AUTHORITY, LegalStatus

if TYPE_CHECKING:
    from lawvm.core.ir import LegalAddress
    from lawvm.core.mutation_boundary import TreePath


@dataclass(frozen=True, slots=True)
class ExpiryOverride:
    """One link in a temporary amendment's expiry extension chain."""

    source_statute_id: str
    source_title: str = ""
    enacted: str = ""
    effective: str = ""
    new_expires: str = ""
    section_labels: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        labels = frozenset(self.section_labels)
        if not all(isinstance(label, str) for label in labels):
            raise ValueError("ExpiryOverride.section_labels must contain strings")
        object.__setattr__(self, "section_labels", labels)


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """A byte-level anchor into the RAW source bytes of an amendment clause.

    This is the source-side anchor required by the certified-transition trace
    spec (§5.1/§7): a half-open byte ``[byte_offset, byte_offset + byte_len)``
    span over the *raw* source artifact bytes (``span_unit=byte``), plus a
    ``quote_hash`` = sha256 of those exact raw bytes. It lets a certificate
    point at the verbatim source clause that drove a write, instead of carrying
    only diff-derived state with no provenance.

    It is distinct from :class:`lawvm.core.span_anchor.SpanAnchor` (a
    content-addressed anchor into the RESULT tree) and mirrors the source-side
    byte-span vocabulary of :class:`lawvm.core.interlinks.InterlinkSourceSpan`.

    The anchor is only ever constructed when a verbatim contiguous byte span
    is genuinely present in the raw artifact. When the clause text cannot be
    located verbatim in the raw bytes (e.g. it spans tag boundaries after
    text-flattening), NO anchor is produced — the caller keeps the fail-loud
    ``SOURCE_ANCHOR_UNAVAILABLE`` path. An anchor is never fabricated.
    """

    source_artifact_id: str
    byte_offset: int
    byte_len: int
    quote_hash: str  # "sha256:" + hexdigest of raw_bytes[byte_offset:byte_offset+byte_len]

    def __post_init__(self) -> None:
        if not self.source_artifact_id:
            raise ValueError("SourceAnchor.source_artifact_id must be non-empty")
        if self.byte_offset < 0:
            raise ValueError("SourceAnchor.byte_offset must be >= 0")
        if self.byte_len <= 0:
            raise ValueError("SourceAnchor.byte_len must be > 0")
        if not self.quote_hash.startswith("sha256:"):
            raise ValueError("SourceAnchor.quote_hash must be a 'sha256:'-prefixed digest")

    def as_jsonable(self) -> dict[str, object]:
        """Stable JSON projection for certificate transition rows (§5.1)."""
        return {
            "source_artifact_id": self.source_artifact_id,
            "span_unit": "byte",
            "byte_offset": self.byte_offset,
            "byte_len": self.byte_len,
            "quote_hash": self.quote_hash,
        }


def compute_source_anchor(
    *,
    source_artifact_id: str,
    raw_bytes: bytes,
    clause_text: str,
) -> "SourceAnchor | None":
    """Locate ``clause_text`` verbatim in ``raw_bytes`` and build a SourceAnchor.

    Returns ``None`` (fail-loud, never fabricate) when:

    * either input is empty, or
    * the clause text does not appear as a single contiguous verbatim byte
      substring of the raw artifact (the common case after text-flattening
      across XML tag boundaries), or
    * the clause text appears more than once (ambiguous — we cannot certify
      WHICH occurrence drove the write).

    A returned anchor satisfies the invariant
    ``raw_bytes[off:off+len] == clause_text.encode('utf-8')`` and carries the
    sha256 of those exact bytes, so a verifier can re-derive it independently.
    """
    import hashlib

    if not source_artifact_id or not raw_bytes or not clause_text:
        return None
    needle = clause_text.encode("utf-8")
    first = raw_bytes.find(needle)
    if first < 0:
        return None
    # Ambiguous if it occurs more than once — refuse rather than guess.
    if raw_bytes.find(needle, first + 1) >= 0:
        return None
    quote_hash = "sha256:" + hashlib.sha256(needle).hexdigest()
    return SourceAnchor(
        source_artifact_id=source_artifact_id,
        byte_offset=first,
        byte_len=len(needle),
        quote_hash=quote_hash,
    )


@dataclass(frozen=True, slots=True)
class OperationSource:
    """Provenance for a legal operation.

    This carrier records source-side timing and textual provenance. Executable
    temporal authority lives on `TemporalEvent` / `ProvisionVersion`, not here.
    """

    statute_id: str
    title: str = ""
    enacted: str = ""  # when the amending act was created
    effective: str = ""  # source-side effective date carried into lowering
    # Both expiry fields use the kernel's EXCLUSIVE cutoff convention (first day
    # NOT in force = prose-inclusive valid_until + 1 day), same as
    # ProvisionVersion.expires; writers convert prose dates at the stamp site.
    expires: str = ""  # source-side expiry provenance carried into lowering
    expires_original: str = ""  # original temporary-act expiry before extensions
    expiry_chain: Tuple[ExpiryOverride, ...] = ()  # audit trail of expiry overrides
    raw_text: str = ""  # original amendment language
    source_anchor: "SourceAnchor | None" = None  # byte span of the clause in raw source bytes
    corrected_by: str = ""  # corrigendum ID that patched this source (e.g. "corr/984/2018/1")
    # UK commencement provenance: text-writing act vs force-activating SI
    commencement_source: str = ""  # SI/order that brings this into force
    commencement_title: str = ""  # title of the commencement instrument
    # Authority/branch provenance: default empty branch is enacted/current law.
    authority_layer: AuthorityLayer = ENACTED_AUTHORITY
    legal_status: LegalStatus = COMMENCED_STATUS
    branch_id: str = ""
    scenario_id: str = ""
    # Apply-cardinality invariant (corrigendum/source-defect retry patches).
    # Expected exact-occurrence count when this op is applied to its source XML
    # fragment. Default 1: the patch should match exactly one byte span. The
    # apply loop at lawvm.finland.corrigendum.patch_source_xml checks
    # `count == expected_apply_count` and emits typed findings otherwise
    # (``ambiguous`` when count > expected, ``under_applied`` when count <
    # expected — both first-class residuals per AGENTS.md §1.8). Patches that
    # legitimately target N>1 occurrences (e.g. table-row fixes that repeat)
    # opt in by setting this explicitly; default behaviour is unchanged.
    expected_apply_count: int = 1

    def __post_init__(self) -> None:
        expiry_chain = tuple(self.expiry_chain)
        if not all(isinstance(override, ExpiryOverride) for override in expiry_chain):
            raise ValueError("OperationSource.expiry_chain must contain ExpiryOverride records")
        object.__setattr__(self, "expiry_chain", expiry_chain)
        BranchContext(
            authority_layer=self.authority_layer,
            legal_status=self.legal_status,
            branch_id=self.branch_id,
            scenario_id=self.scenario_id,
        )


@dataclass(frozen=True, slots=True)
class MigrationEvent:
    """Address continuity through an explicit migration event."""

    event_id: str
    kind: Literal["renumber", "move", "split", "merge"]
    from_address: "LegalAddress"
    to_address: "LegalAddress"
    effective: str = ""
    source_statute: str = ""
    witness: object | None = None

    def __post_init__(self) -> None:
        from lawvm.core.ir import LegalAddress

        if not self.event_id:
            raise ValueError("MigrationEvent.event_id must be non-empty")
        if self.kind not in {"renumber", "move", "split", "merge"}:
            raise ValueError(f"unsupported MigrationEvent.kind: {self.kind!r}")
        if not isinstance(self.from_address, LegalAddress):
            raise ValueError("MigrationEvent.from_address must be a LegalAddress")
        if not isinstance(self.to_address, LegalAddress):
            raise ValueError("MigrationEvent.to_address must be a LegalAddress")


@dataclass(frozen=True, order=True, slots=True)
class MigrationEventSortKey:
    """Deterministic canonical ordering key for lineage migration waves."""

    effective: str
    from_depth: int
    from_path: "TreePath"
    to_path: "TreePath"
    source_statute: str
    event_id: str

    def as_tuple(self) -> tuple[str, int, "TreePath", "TreePath", str, str]:
        return (
            self.effective,
            self.from_depth,
            self.from_path,
            self.to_path,
            self.source_statute,
            self.event_id,
        )


def migration_event_sort_key(
    event: MigrationEvent,
) -> MigrationEventSortKey:
    """Return the deterministic canonical ordering key for lineage waves."""
    return MigrationEventSortKey(
        effective=event.effective,
        from_depth=len(event.from_address.path),
        from_path=event.from_address.path,
        to_path=event.to_address.path,
        source_statute=event.source_statute,
        event_id=event.event_id,
    )
