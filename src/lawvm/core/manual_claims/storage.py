"""Storage layer for manual claims.

Storage layout (§10 of design memo v2.2):

  data/fi/v1/manual_claims/
    objects/sha256/<CLAIM_ID>.json        # immutable claim (authoritative)
    events.jsonl                          # append-only event log (authoritative)
    states/current/<CLAIM_ID>.json        # materialized current state (regenerable)
    by-kind/<CLAIM_KIND>/<CLAIM_ID>.json  # convenience symlinks (regenerable)
    claim_precedence.yaml                 # operator-authored config (not read here)

authoritative = objects/sha256/ + events.jsonl
regenerable   = states/current/, by-kind/

Design:
  - Load-time hash verification: recompute claim_id from payload, reject mismatch.
  - Event log is append-only; existing lines are never modified.
  - State files are regenerable projections of the event log.
  - No silent drops: storage errors are raised, not logged-and-swallowed.
  - AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterator, Optional, Sequence, Tuple

from lawvm.core.manual_claims.hashing import verify_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimCompositionDecision,
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    _ProfileTagDeprecated as ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _to_json_value(v: object) -> object:
    """Convert a Python value to a JSON-serializable form."""
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if hasattr(v, "value"):  # Enum
        return v.value
    if isinstance(v, (tuple, list)):
        return [_to_json_value(x) for x in v]
    if is_dataclass(v) and not isinstance(v, type):
        return {field.name: _to_json_value(getattr(v, field.name)) for field in fields(v)}
    raise TypeError(f"Cannot serialize {type(v).__name__!r}")


def _claim_to_dict(claim: ManualCompilationClaim) -> Dict[str, object]:
    return {field.name: _to_json_value(getattr(claim, field.name)) for field in fields(claim)}


def _state_to_dict(state: ClaimState) -> Dict[str, object]:
    return {field.name: _to_json_value(getattr(state, field.name)) for field in fields(state)}


def _event_to_dict(event: ClaimStateEvent) -> Dict[str, object]:
    return {field.name: _to_json_value(getattr(event, field.name)) for field in fields(event)}


def _composition_to_dict(dec: ClaimCompositionDecision) -> Dict[str, object]:
    return {field.name: _to_json_value(getattr(dec, field.name)) for field in fields(dec)}


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------


def _parse_optional_date(v: Optional[str]) -> Optional[date]:
    return date.fromisoformat(v) if v else None


def _parse_datetime(v: str) -> datetime:
    return datetime.fromisoformat(v)


def _parse_producer(d: Dict) -> Producer:
    return Producer(
        producer_kind=d["producer_kind"],
        handle=d.get("handle"),
        model_id=d.get("model_id"),
        timestamp=_parse_datetime(d["timestamp"]),
        environment=d.get("environment"),
    )


def _parse_source_locator(d: Dict) -> SourceLocator:
    return SourceLocator(
        artifact_kind=d["artifact_kind"],
        statute_id=d.get("statute_id"),
        he_id=d.get("he_id"),
        version_id=d.get("version_id"),
    )


def _parse_claim_scope(d: Dict) -> ClaimScope:
    return ClaimScope(
        statute_id=d["statute_id"],
        provision_ref=d.get("provision_ref"),
        valid_at_start=_parse_optional_date(d.get("valid_at_start")),
        valid_at_end=_parse_optional_date(d.get("valid_at_end")),
    )


def _parse_tuple_pairs(v: object) -> Tuple[Tuple[str, object], ...]:
    """Parse [[k,v], ...] or [[k,v]] list into tuple of (k,v) pairs."""
    return tuple(_parse_pair_sequence(v))


def _parse_str_pairs(v: object) -> Tuple[Tuple[str, str], ...]:
    """Parse [[k,v], ...] into string-valued tuple pairs."""
    pairs = _parse_pair_sequence(v)
    out: list[tuple[str, str]] = []
    for key, value in pairs:
        if not isinstance(value, str):
            raise TypeError("string tuple-pair entry value must be a string")
        out.append((key, value))
    return tuple(out)


def _parse_pair_sequence(v: object) -> list[tuple[str, object]]:
    if not isinstance(v, Sequence) or isinstance(v, (str, bytes, bytearray)):
        raise TypeError("tuple-pair field must be a sequence")
    out: list[tuple[str, object]] = []
    for item in v:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            raise TypeError("tuple-pair entries must be 2-item sequences")
        if len(item) != 2:
            raise ValueError("tuple-pair entries must have exactly two items")
        key = item[0]
        if not isinstance(key, str):
            raise TypeError("tuple-pair entry key must be a string")
        out.append((key, item[1]))
    return out


def _dict_to_claim(d: Dict) -> ManualCompilationClaim:
    valid_at_raw = d["valid_at"]
    # valid_at is [start, end_or_null]
    valid_at: Tuple[date, Optional[date]] = (
        date.fromisoformat(valid_at_raw[0]),
        date.fromisoformat(valid_at_raw[1]) if valid_at_raw[1] else None,
    )
    return ManualCompilationClaim(
        claim_id=d["claim_id"],
        schema_version=d["schema_version"],
        jurisdiction=d["jurisdiction"],
        claim_kind=d["claim_kind"],
        claim_layer=ClaimLayer(d["claim_layer"]),
        claim_scope=_parse_claim_scope(d["claim_scope"]),
        target=_parse_tuple_pairs(d["target"]),
        value=_parse_tuple_pairs(d["value"]),
        source_witness_type=SourceWitnessType(d["source_witness_type"]),
        producer=_parse_producer(d["producer"]),
        cited_source_locator=_parse_source_locator(d["cited_source_locator"]),
        cited_source_span=(d["cited_source_span"][0], d["cited_source_span"][1]),
        cited_source_hash=d["cited_source_hash"],
        dependency_fingerprint=_parse_str_pairs(d["dependency_fingerprint"]),
        valid_at=valid_at,
        supersedes=tuple(d.get("supersedes", [])),
        supersession_delta_reason=d.get("supersession_delta_reason"),
        disputes=tuple(d.get("disputes", [])),
        requested_profiles=tuple(ProfileTag(p) for p in d.get("requested_profiles", [])),
        rationale=d["rationale"],
    )


def _dict_to_state(d: Dict) -> ClaimState:
    return ClaimState(
        claim_id=d["claim_id"],
        claim_state_status=ClaimStatus(d["claim_state_status"]),
        review_status=ReviewStatus(d["review_status"]),
        validator_status=ValidatorStatus(d["validator_status"]),
        confidence=ClaimConfidence(d["confidence"]),
        last_updated=_parse_datetime(d["last_updated"]),
    )


def _dict_to_event(d: Dict) -> ClaimStateEvent:
    return ClaimStateEvent(
        claim_id=d["claim_id"],
        event_kind=d["event_kind"],
        timestamp=_parse_datetime(d["timestamp"]),
        producer=_parse_producer(d["producer"]),
        old_status=d.get("old_status"),
        new_status=d.get("new_status"),
        reason=d["reason"],
    )


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------


class ClaimStore:
    """Read/write interface to the manual_claims/ storage tree.

    authoritative:  objects/sha256/*.json  + events.jsonl
    regenerable:    states/current/*.json  + by-kind/*/*
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._objects_dir = base_dir / "objects" / "sha256"
        self._events_path = base_dir / "events.jsonl"
        self._states_dir = base_dir / "states" / "current"
        self._by_kind_dir = base_dir / "by-kind"

    # --- directory management ---

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        self._objects_dir.mkdir(parents=True, exist_ok=True)
        self._states_dir.mkdir(parents=True, exist_ok=True)
        self._by_kind_dir.mkdir(parents=True, exist_ok=True)

    # --- claim storage ---

    def write_claim(self, claim: ManualCompilationClaim) -> Path:
        """Write immutable claim JSON to objects/sha256/<CLAIM_ID>.json.

        Does not overwrite an existing object with the same ID (idempotent).
        Returns the path to the written file.
        """
        self.ensure_dirs()
        path = self._objects_dir / f"{claim.claim_id}.json"
        if not path.exists():
            data = _claim_to_dict(claim)
            path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def read_claim(self, claim_id: str) -> ManualCompilationClaim:
        """Read and verify a claim from objects/sha256/.

        Raises FileNotFoundError if absent.
        Raises ValueError on load-time hash mismatch (tamper detection).
        """
        path = self._objects_dir / f"{claim_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Claim not found: {claim_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        claim = _dict_to_claim(data)
        verify_claim_id(claim)
        return claim

    def claim_exists(self, claim_id: str) -> bool:
        return (self._objects_dir / f"{claim_id}.json").exists()

    # --- event log ---

    def append_event(self, event: ClaimStateEvent) -> None:
        """Append one event to events.jsonl.

        Existing lines are NEVER modified (append-only invariant).
        """
        self.ensure_dirs()
        line = json.dumps(_event_to_dict(event), sort_keys=True) + "\n"
        with open(self._events_path, "a", encoding="utf-8") as f:
            f.write(line)

    def read_events(self, claim_id: Optional[str] = None) -> Iterator[ClaimStateEvent]:
        """Yield all events from events.jsonl, optionally filtered by claim_id."""
        if not self._events_path.exists():
            return
        with open(self._events_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if claim_id is None or d.get("claim_id") == claim_id:
                    yield _dict_to_event(d)

    def read_all_events(self) -> list[ClaimStateEvent]:
        return list(self.read_events())

    # --- state files ---

    def write_state(self, state: ClaimState) -> Path:
        """Write current state to states/current/<CLAIM_ID>.json."""
        self.ensure_dirs()
        path = self._states_dir / f"{state.claim_id}.json"
        path.write_text(
            json.dumps(_state_to_dict(state), indent=2, sort_keys=True), encoding="utf-8"
        )
        return path

    def read_state(self, claim_id: str) -> Optional[ClaimState]:
        """Read current state, or None if not materialized."""
        path = self._states_dir / f"{claim_id}.json"
        if not path.exists():
            return None
        return _dict_to_state(json.loads(path.read_text(encoding="utf-8")))

    def list_state_ids(self) -> Tuple[str, ...]:
        """Return claim_ids for all materialized state files."""
        if not self._states_dir.exists():
            return ()
        return tuple(
            p.stem for p in self._states_dir.iterdir() if p.suffix == ".json"
        )

    # --- by-kind convenience views ---

    def write_by_kind(self, claim: ManualCompilationClaim) -> Path:
        """Write claim JSON to by-kind/<CLAIM_KIND>/<CLAIM_ID>.json."""
        self.ensure_dirs()
        kind_dir = self._by_kind_dir / claim.claim_kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        path = kind_dir / f"{claim.claim_id}.json"
        if not path.exists():
            data = _claim_to_dict(claim)
            path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def list_claims_by_kind(self, claim_kind: str) -> Tuple[str, ...]:
        """Return claim_ids for all claims of the given kind."""
        kind_dir = self._by_kind_dir / claim_kind
        if not kind_dir.exists():
            return ()
        return tuple(p.stem for p in kind_dir.iterdir() if p.suffix == ".json")

    def list_all_claim_ids(self) -> Tuple[str, ...]:
        """Return all claim_ids from objects/sha256/."""
        if not self._objects_dir.exists():
            return ()
        return tuple(p.stem for p in self._objects_dir.iterdir() if p.suffix == ".json")
