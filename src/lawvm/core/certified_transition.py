"""Certified tree-transition projection from landed write receipts.

This module is the narrow producer-side bridge between the semantic apply
waist and certificate traces: a ``WriteReceipt`` records the landed write, and
these helpers project that receipt into CertifiedTreeTransition core rows
(CERTIFIED_TREE_TRANSITION_TRACE_V0.md §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

from lawvm.core.write_receipt import WriteReceipt, receipt_address_string

CertifiedTreeTransitionAction = Literal["set_subtree", "delete_subtree"]
SourceAnchor = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CertifiedTreeTransitionCore:
    """Certified-core fields hashed into a transition leaf.

    Display annotations such as legal-op summaries intentionally do not live
    here; the certificate spec hashes only this core field set.
    """

    transition_id: str
    sequence: int
    effective_date: str
    action: CertifiedTreeTransitionAction
    target_address: str
    pre_hash: str
    post_hash: str
    payload_hash: str
    source_refs: tuple[str, ...] = ()
    source_anchors: tuple[SourceAnchor, ...] = ()

    def to_jsonable_dict(self) -> dict[str, object]:
        """Return the trace-spec row core with list-valued arrays."""
        return {
            "transition_id": self.transition_id,
            "sequence": self.sequence,
            "effective_date": self.effective_date,
            "action": self.action,
            "target_address": self.target_address,
            "pre_hash": self.pre_hash,
            "post_hash": self.post_hash,
            "payload_hash": self.payload_hash,
            "source_refs": list(self.source_refs),
            "source_anchors": [dict(anchor) for anchor in self.source_anchors],
        }


def certified_tree_transitions_from_receipt(
    receipt: WriteReceipt,
    *,
    effective_date: str,
    sequence_start: int = 1,
    source_refs: Iterable[str] = (),
    source_anchors: Sequence[SourceAnchor] = (),
) -> tuple[CertifiedTreeTransitionCore, ...]:
    """Project one landed-write receipt into v0 transition-core rows.

    A receipt must carry a complete pre/post hash pair for every declared
    footprint address. Missing hashes are a producer bug; extra hashes are also
    rejected because they would certify an undeclared write.
    """
    if not effective_date:
        raise ValueError("effective_date is required for certified transitions")
    if sequence_start < 1:
        raise ValueError("sequence_start must be positive")

    addresses = _declared_addresses(receipt)
    if not addresses:
        raise ValueError("receipt has no declared footprint for certified transition projection")
    _validate_hash_coverage(receipt, addresses)

    refs = tuple(source_refs)
    anchors = tuple(source_anchors)
    rows: list[CertifiedTreeTransitionCore] = []
    sequence = sequence_start
    for address in addresses:
        pre = receipt.pre_hashes[address]
        post = receipt.post_hashes[address]
        if pre == post:
            raise ValueError(
                f"declared receipt footprint {address!r} has no transition: pre_hash == post_hash"
            )
        action: CertifiedTreeTransitionAction = "delete_subtree" if post == "" else "set_subtree"
        rows.append(
            CertifiedTreeTransitionCore(
                transition_id=f"t{sequence:06d}:{effective_date}:{address}",
                sequence=sequence,
                effective_date=effective_date,
                action=action,
                target_address=address,
                pre_hash=_trace_hash(pre, field_name="pre_hash"),
                post_hash=_trace_hash(post, field_name="post_hash"),
                payload_hash=_trace_hash(post, field_name="payload_hash") if action == "set_subtree" else "",
                source_refs=refs,
                source_anchors=anchors,
            )
        )
        sequence += 1
    return tuple(rows)


def _declared_addresses(receipt: WriteReceipt) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            receipt_address_string(path)
            for path in receipt.declared_footprint
        )
    )


def _validate_hash_coverage(receipt: WriteReceipt, addresses: tuple[str, ...]) -> None:
    declared = set(addresses)
    pre_keys = set(receipt.pre_hashes)
    post_keys = set(receipt.post_hashes)
    missing_pre = sorted(declared - pre_keys)
    missing_post = sorted(declared - post_keys)
    extra_pre = sorted(pre_keys - declared)
    extra_post = sorted(post_keys - declared)
    problems: list[str] = []
    if missing_pre:
        problems.append(f"missing pre_hashes for {missing_pre}")
    if missing_post:
        problems.append(f"missing post_hashes for {missing_post}")
    if extra_pre:
        problems.append(f"undeclared pre_hashes for {extra_pre}")
    if extra_post:
        problems.append(f"undeclared post_hashes for {extra_post}")
    if problems:
        raise ValueError("; ".join(problems))


def _trace_hash(value: str, *, field_name: str) -> str:
    if value == "":
        return ""
    if value.startswith("sha256:"):
        digest = value.removeprefix("sha256:")
        if _is_lower_hex_sha256(digest):
            return value
        raise ValueError(f"{field_name} has invalid sha256 digest: {value!r}")
    if _is_lower_hex_sha256(value):
        return f"sha256:{value}"
    raise ValueError(f"{field_name} must be empty or a lowercase sha256 digest")


def _is_lower_hex_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
