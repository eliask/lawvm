"""``lawvm.corpus_totality.v0`` — the CORPUS-level totality object (design §23.x, §24.1).

The within-work totality LENS (:mod:`lawvm.substrate.totality`) certifies a
single pack is a complete account of one work's own declared universe. This
module adds the **corpus level**: which WORKS are in scope, and is that a
complete account of a *declared universe of works* (level A — the work-universe,
"what turns a collection into a corpus").

**The relativity principle is load-bearing (design §23.x).** Totality is NEVER
"this contains all law." It is "this contains all works matching declared
universe U, per declared enumeration source E, under declared admission policy P,
with every omission / exclusion accounted." A corpus totality certificate is only
as strong as its enumeration source:

* Finlex / ORK signing the enumeration root → strong *official* totality;
* LawVM's own observed crawl → "complete relative to LawVM's observed collection."

The ``closed_world_claim: bool`` field carries this honesty. It is ``true`` ONLY
when the enumeration source is itself closed-world (an official signed registry
snapshot / a static manifest listing every file); ``false`` for crawls /
research-slices / "all the PDFs we found" — still a valid totality, but a UI must
render it as "complete for the declared observed collection, not for the
jurisdiction." For our observed Finlex crawl we emit ``closed_world_claim=false``
(honest — we hold no signed Finlex enumeration).

**This is the LEVEL-A object only** (the build scope of design §24.1.3); the
``work_universe_root = MapRoot("work_universe", {work_id → WorkInventoryRow hash})``
is the keystone — adding, dropping, or renaming a work changes the root, so a
missing or surplus work is detectable exactly as the within-work
``selection_universe_root`` makes a missing row detectable. Signatures (the
SEPARATE axis, :mod:`lawvm.substrate.signature_attestation`) sign this root; PKI
is deferred.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, map_root

# Schema names follow the landed normative spec ``CORPUS_TOTALITY_CERTIFICATE_V0.md``
# (``lawvm.corpus_totality.v0`` / ``lawvm.work_inventory_row.v0``). The design
# §23.x sketch named the keystone ``work_universe_root`` and the spec names it
# ``work_inventory_root``; both are exposed (the spec name is authoritative, the
# design-sketch name is a stable alias) — see ``work_inventory_root`` /
# ``work_universe_root`` below.
_SCHEMA_CORPUS_TOTALITY = "lawvm.corpus_totality.v0"
_SCHEMA_WORK_INVENTORY_ROW = "lawvm.work_inventory_row.v0"

_DOMAIN_CORPUS_TOTALITY = "corpus_totality"
_DOMAIN_WORK_INVENTORY_ROW = "work_inventory_row"
# The spec's domain separation: ``lawvm.corpus_totality.v0.work_inventory``.
_DOMAIN_WORK_INVENTORY = "lawvm.corpus_totality.v0.work_inventory"


class CorpusTotalityError(ValueError):
    """A corpus-totality object violates a v0 schema invariant."""


# --------------------------------------------------------------------------- #
# Closed vocabularies (design §23.x level A; §24.1 work-inventory statuses).   #
# --------------------------------------------------------------------------- #

# A work's membership status in the corpus universe (design §23.x level A).
# ``included`` is the only status whose work contributes a member pack; every
# other status is a TYPED account of WHY a work matching the universe is not a
# clean member — the corpus equivalent of a within-work typed non-selection
# reason. A work matching universe U with no inventory row = a silent gap.
WORK_INVENTORY_STATUSES: frozenset[str] = frozenset(
    {
        "included",
        "excluded_by_policy",
        "unavailable_source",
        "unsupported_format",
        "duplicate_alias",
        "superseded",
        "withdrawn",
        "outside_scope",
        "blocked_unclassified",
    }
)

# The enumeration-source / universe kind (design §23.x — what U *is*).
# ``observed_crawl`` is our honest v0 case (closed_world_claim=false);
# ``official_signed_registry`` / ``static_manifest`` would license a true claim.
UNIVERSE_KINDS: frozenset[str] = frozenset(
    {
        "observed_crawl",
        "static_manifest",
        "official_signed_registry",
        "curated_slice",
    }
)


# --------------------------------------------------------------------------- #
# WorkInventoryRow — one work's membership account (the MapRoot leaf).         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WorkInventoryRow:
    """``lawvm.work_inventory_row.v1`` — one work's typed membership (design §23.x A).

    ``status`` is a closed :data:`WORK_INVENTORY_STATUSES` value; a non-``included``
    work MUST carry a ``reason_detail`` so the exclusion is owned, never silent
    (the corpus analogue of the within-work "no silent drop"). ``pack_id`` is set
    iff ``status == "included"`` (it points at the member pack); for any other
    status it is ``None`` (there is no member pack).
    """

    work_id: str
    status: str
    reason_detail: str = ""
    pack_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in WORK_INVENTORY_STATUSES:
            raise CorpusTotalityError(
                f"WorkInventoryRow.status must be one of {sorted(WORK_INVENTORY_STATUSES)!r}, "
                f"got {self.status!r}"
            )
        if self.status == "included" and not self.pack_id:
            raise CorpusTotalityError(
                f"an 'included' work ({self.work_id!r}) must carry its member pack_id"
            )
        if self.status != "included" and self.pack_id:
            raise CorpusTotalityError(
                f"a non-included ({self.status!r}) work must not carry a pack_id "
                f"(there is no member pack to point at)"
            )
        if self.status != "included" and not self.reason_detail:
            raise CorpusTotalityError(
                f"a non-included ({self.status!r}) work ({self.work_id!r}) must carry a "
                f"reason_detail so the exclusion is OWNED, never a silent omission"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_WORK_INVENTORY_ROW,
            "work_id": self.work_id,
            "status": self.status,
            "reason_detail": nfc(self.reason_detail),
            "pack_id": self.pack_id,
        }

    @property
    def row_id(self) -> str:
        return leaf_hash(_DOMAIN_WORK_INVENTORY_ROW, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# CorpusTotality — the level-A object.                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CorpusTotalityUniverse:
    """The ``universe`` block — what U is + the relativity claim (design §23.x)."""

    universe_kind: str
    enumeration_source_refs: tuple[str, ...]
    enumeration_policy_id: str
    closed_world_claim: bool

    def __post_init__(self) -> None:
        if self.universe_kind not in UNIVERSE_KINDS:
            raise CorpusTotalityError(
                f"universe_kind must be one of {sorted(UNIVERSE_KINDS)!r}, got {self.universe_kind!r}"
            )
        if self.closed_world_claim and self.universe_kind == "observed_crawl":
            raise CorpusTotalityError(
                "closed_world_claim=true is invalid for an observed_crawl universe — a "
                "crawl is not a closed-world enumeration (design §23.x); a true claim "
                "requires a static_manifest / official_signed_registry source"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "universe_kind": self.universe_kind,
            "enumeration_source_refs": list(self.enumeration_source_refs),
            "enumeration_policy_id": self.enumeration_policy_id,
            "closed_world_claim": self.closed_world_claim,
        }


@dataclass(frozen=True, slots=True)
class CorpusTotality:
    """``lawvm.corpus_totality.v0`` — the corpus-level totality object (design §23.x).

    Mirrors the substrate object pattern: a frozen dataclass with a
    ``to_canonical_dict()`` (the hashed body, NFC-normalized, **without** its own
    id) and a computed ``corpus_totality_id``. The ``work_universe_root`` is the
    keystone MapRoot over ``{work_id → WorkInventoryRow row_id}`` — that is what
    makes a missing or surplus work detectable.

    Only the LEVEL-A roots are populated in v0 (``work_universe_root``,
    ``included_work_root``, the typed-exclusion sub-roots); the level-B..E roots
    (source-bundle / address-tree / timeline / overlay) are reserved as members
    so the schema is forward-stable but unset in v0.
    """

    corpus_id: str
    jurisdiction: str
    universe: CorpusTotalityUniverse
    work_inventory: tuple[WorkInventoryRow, ...]
    source_policy_id: str = ""
    history_policy_id: str = ""
    structure_policy_id: str = ""

    def __post_init__(self) -> None:
        work_ids = [r.work_id for r in self.work_inventory]
        if len(set(work_ids)) != len(work_ids):
            raise CorpusTotalityError(
                "CorpusTotality has duplicate work_id rows; each work must appear once"
            )

    # -- roots ---------------------------------------------------------------- #

    @property
    def work_inventory_root(self) -> str:
        """``MapRoot(work_id → row_id)`` — the keystone (spec §5.3; design §23.x A).

        Spec-authoritative name. ``work_universe_root`` is the design §23.x alias
        of this same root (both compute identically).
        """
        return map_root(
            _DOMAIN_WORK_INVENTORY,
            {r.work_id: r.row_id for r in self.work_inventory},
        )

    @property
    def work_universe_root(self) -> str:
        """Design §23.x alias of :attr:`work_inventory_root` (identical value)."""
        return self.work_inventory_root

    def _root_over(self, status: str, domain: str) -> str:
        """MapRoot over the subset of works carrying ``status`` (typed partition)."""
        return map_root(
            domain,
            {r.work_id: r.row_id for r in self.work_inventory if r.status == status},
        )

    def roots(self) -> dict[str, JsonValue]:
        """The level-A roots populated in v0 + the reserved level-B..E roots.

        Reserved roots are ``None`` (omit-when-absent in spirit, but kept as
        explicit members of the hashed body so omission of a whole level is
        itself committed to — mirroring the state-selection empty-sub-root
        convention).
        """
        included = {
            r.work_id: r.row_id for r in self.work_inventory if r.status == "included"
        }
        nonincluded = {
            r.work_id: r.row_id for r in self.work_inventory if r.status != "included"
        }
        return {
            "work_inventory_root": self.work_inventory_root,
            "included_work_root": map_root(
                "lawvm.corpus_totality.v0.included_work", included
            ),
            "nonincluded_work_root": map_root(
                "lawvm.corpus_totality.v0.nonincluded_work", nonincluded
            ),
            # Level B..E reserved (spec §3, design §23.x) — not computed in v0.
            "source_bundle_coverage_root": None,
            "structure_coverage_root": None,
            "state_selection_coverage_root": None,
            "surface_totality_root": None,
            "work_pack_manifest_root": None,
            "per_work_certificate_root": None,
            "residual_root": None,
            "finding_root": None,
            "signature_attestation_root": None,
        }

    def counts(self) -> dict[str, JsonValue]:
        tally: dict[str, int] = {status: 0 for status in WORK_INVENTORY_STATUSES}
        for row in self.work_inventory:
            tally[row.status] += 1
        return {
            "included": tally["included"],
            "excluded_by_policy": tally["excluded_by_policy"],
            "unavailable_source": tally["unavailable_source"],
            "unsupported_format": tally["unsupported_format"],
            "blocked_unclassified": tally["blocked_unclassified"],
            "total": len(self.work_inventory),
        }

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CORPUS_TOTALITY,
            "corpus_id": self.corpus_id,
            "jurisdiction": self.jurisdiction,
            "universe": self.universe.to_canonical_dict(),
            "policy": {
                "source_policy_id": self.source_policy_id,
                "history_policy_id": self.history_policy_id,
                "structure_policy_id": self.structure_policy_id,
            },
            "roots": self.roots(),
            "counts": self.counts(),
        }

    @property
    def corpus_totality_id(self) -> str:
        return leaf_hash(_DOMAIN_CORPUS_TOTALITY, self.to_canonical_dict())


# --------------------------------------------------------------------------- #
# Builder — assemble a CorpusTotality over pack-corpus members (level A).      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IncludedMember:
    """One included member of a pack-corpus: its work_id + pack_id."""

    work_id: str
    pack_id: str


def build_corpus_totality(
    *,
    corpus_id: str,
    jurisdiction: str,
    included: Sequence[IncludedMember],
    excluded: Sequence[WorkInventoryRow] = (),
    universe_kind: str = "observed_crawl",
    enumeration_source_refs: Sequence[str] = (),
    enumeration_policy_id: str = "lawvm.enumeration.observed_crawl.v0",
    closed_world_claim: bool = False,
    source_policy_id: str = "archival_exact",
    history_policy_id: str = "full_history",
    structure_policy_id: str = "law_shaped_admission",
) -> CorpusTotality:
    """Build a level-A :class:`CorpusTotality` over pack-corpus members (design §24.1.3).

    ``included`` are the works that contributed member packs (each → an
    ``included`` :class:`WorkInventoryRow`); ``excluded`` are any pre-built typed
    non-inclusion rows. Defaults reflect our honest v0 posture: an
    ``observed_crawl`` universe with ``closed_world_claim=false`` (we hold no
    signed Finlex enumeration).
    """
    rows: list[WorkInventoryRow] = [
        WorkInventoryRow(work_id=m.work_id, status="included", pack_id=m.pack_id)
        for m in included
    ]
    rows.extend(excluded)
    universe = CorpusTotalityUniverse(
        universe_kind=universe_kind,
        enumeration_source_refs=tuple(enumeration_source_refs),
        enumeration_policy_id=enumeration_policy_id,
        closed_world_claim=closed_world_claim,
    )
    return CorpusTotality(
        corpus_id=corpus_id,
        jurisdiction=jurisdiction,
        universe=universe,
        work_inventory=tuple(rows),
        source_policy_id=source_policy_id,
        history_policy_id=history_policy_id,
        structure_policy_id=structure_policy_id,
    )
