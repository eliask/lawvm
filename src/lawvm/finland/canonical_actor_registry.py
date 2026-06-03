"""Finnish canonical actor registry with lifecycle versioning.

Provides the authoritative mapping from actor phrase variants to canonical
identifiers for Finnish institutional actors (ministries, agencies, levels
of government).

Lifecycle versioning is load-bearing: a phrase like "Evira" in a 2017
provision resolves to fi.agency.ruokavirasto via a LifecycleActorObservation,
not by silent renaming.

Registry structure per ACTOR_MENTION_EXTRACTION.md §Canonical actor registry:
  - canonical_id: stable ID, e.g. 'fi.agency.ruokavirasto'
  - show_as: canonical display string
  - actor_type: ministry | agency | government_level | institution
  - level: state | municipal | regional | eu
  - lifecycle: list of LifecyclePeriod (active phrase variants per date range)

Design discipline (AGENTS.md §1.1, §1.6, §1.9):
  - Typed dataclasses, not dicts.
  - Predecessor phrases resolve via lifecycle, not silent aliasing.
  - Ambiguous (multi-match) phrases are NOT silently picked; callers
    must emit AmbiguousActorMention.
  - Only a SINGLE lookup table is built at module load; no dynamic
    construction in loops.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Registry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecyclePeriod:
    """A date-bounded window during which phrase_variants are active for an actor.

    Attributes:
        active_from:      Date from which these phrase variants are valid.
        active_until:     Date until which they are valid; None = currently active.
        phrase_variants:  Tuple of phrase strings that map to this actor in this period.
        predecessor_id:   If this period represents a pre-merger entity, the
                          canonical_id of the merged-in predecessor.  None for
                          the current/successor entry itself.
        successor_id:     If a predecessor period, the canonical_id of the successor.
                          None for current-period entries.
    """

    active_from: date
    active_until: Optional[date]
    phrase_variants: Tuple[str, ...]
    predecessor_id: Optional[str] = None
    successor_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "phrase_variants", tuple(self.phrase_variants))

    def is_active_at(self, query_date: Optional[date]) -> bool:
        """Return True if this period is active at query_date.

        If query_date is None, only currently-active periods (active_until=None) match.
        """
        if query_date is None:
            return self.active_until is None
        if query_date < self.active_from:
            return False
        if self.active_until is not None and query_date >= self.active_until:
            return False
        return True


@dataclass(frozen=True)
class CanonicalActor:
    """One entry in the canonical actor registry.

    Attributes:
        canonical_id:  Stable ID, e.g. 'fi.agency.ruokavirasto'.
        show_as:       Canonical display string, e.g. 'Ruokavirasto'.
        actor_type:    'ministry' | 'agency' | 'government_level' | 'institution'.
        level:         'state' | 'municipal' | 'regional' | 'eu'.
        lifecycle:     Ordered list of LifecyclePeriod, oldest first.
        parent_id:     canonical_id of the parent ministry/body; or None.
        akn_org_id:    AKN ontology organization ID if known, e.g.
                       '/akn/ontology/organization/fi.ministry-of-social-affairs-and-health'.
    """

    canonical_id: str
    show_as: str
    actor_type: str
    level: str
    lifecycle: Tuple[LifecyclePeriod, ...]
    parent_id: Optional[str] = None
    akn_org_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lifecycle", tuple(self.lifecycle))

    def phrase_variants_at(self, query_date: Optional[date]) -> Tuple[str, ...]:
        """Return phrase variants active at query_date (all periods if None)."""
        out: List[str] = []
        for period in self.lifecycle:
            if query_date is None or period.is_active_at(query_date):
                out.extend(period.phrase_variants)
        return tuple(out)

    def is_lifecycle_predecessor_phrase(self, phrase: str) -> bool:
        """Return True if phrase appears in a predecessor (non-current) period."""
        for period in self.lifecycle:
            if period.successor_id is not None and phrase in period.phrase_variants:
                return True
        return False

    def lifecycle_observation_for(
        self, phrase: str
    ) -> Optional[Tuple[str, str, date]]:
        """Return (predecessor_id, successor_id, lifecycle_date) for a predecessor phrase.

        Returns None if phrase is not a predecessor phrase for this actor.
        """
        for period in self.lifecycle:
            if period.successor_id is not None and phrase in period.phrase_variants:
                succ = period.successor_id
                lc_date = period.active_until
                if lc_date is not None:
                    return (self.canonical_id, succ, lc_date)
        return None


# ---------------------------------------------------------------------------
# Registry seed data
# ---------------------------------------------------------------------------
# Sorted: ministries first, then state agencies, then government levels.
# Lifecycle: oldest period first; active_until=None means still current.
#
# Phrase-variant discipline:
#  - Include nominative and genitive forms (Ruokavirasto, Ruokaviraston)
#    to maximize prose coverage without false positives.
#  - Avoid overly-generic fragments that cause ambiguity.
#  - Predecessor periods (active_until != None) carry successor_id.
#  - Current periods carry predecessor_id if they absorbed a predecessor.


_REGISTRY_SEED: List[CanonicalActor] = [
    # -----------------------------------------------------------------------
    # Ministries
    # -----------------------------------------------------------------------
    CanonicalActor(
        canonical_id="fi.ministry.stm",
        show_as="Sosiaali- ja terveysministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1993, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Sosiaali- ja terveysministerio",
                    "STM",
                    "sosiaali- ja terveysministerion",
                    "sosiaali- ja terveysministerio",
                    # Generic short forms registered here to create deliberate ambiguity
                    # when a bare 'Ministerio'/'ministerio' appears without qualifier.
                    # This encodes the domain fact that unqualified 'ministerio' in Finnish
                    # legal prose is always ambiguous -- any of the 12 ministries could
                    # be meant. We register the generic forms in two ministry entries
                    # to ensure REGISTRY.lookup() returns multiple candidates.
                    "Ministerio",
                    "ministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-social-affairs-and-health",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.sm",
        show_as="Sisaministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Sisaministerio",
                    "SM",
                    "sisaministerion",
                    "sisaministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-the-interior",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.lvm",
        show_as="Liikenne- ja viestintaministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2000, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Liikenne- ja viestintaministerio",
                    "LVM",
                    "liikenne- ja viestintaministerion",
                    "liikenne- ja viestintaministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-transport-and-communications",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.mmm",
        show_as="Maa- ja metsatalousministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1995, 4, 1),
                active_until=None,
                phrase_variants=(
                    "Maa- ja metsatalousministerio",
                    "MMM",
                    "maa- ja metsatalousministerion",
                    "maa- ja metsatalousministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-agriculture-and-forestry",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.okm",
        show_as="Opetus- ja kultturiministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2010, 5, 1),
                active_until=None,
                phrase_variants=(
                    "Opetus- ja kultturiministerio",
                    "OKM",
                    "opetus- ja kultturiministerion",
                    "opetus- ja kultturiministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-education-and-culture",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.vm",
        show_as="Valtiovarainministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Valtiovarainministerio",
                    "VM",
                    "valtiovarainministerion",
                    "valtiovarainministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-finance",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.tem",
        show_as="Tyo- ja elinkeinoministério",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2008, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Tyo- ja elinkeinoministerio",
                    "TEM",
                    "tyo- ja elinkeinoministerion",
                    "tyo- ja elinkeinoministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-economic-affairs-and-employment",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.ym",
        show_as="Ymparistoministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1983, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Ymparistoministerio",
                    "YM",
                    "ymparistoministerion",
                    "ymparistoministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-the-environment",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.om",
        show_as="Oikeusministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Oikeusministerio",
                    "OM",
                    "oikeusministerion",
                    "oikeusministerio",
                    # Generic short forms also registered here (second entry) so that
                    # REGISTRY.lookup('ministerio') returns >= 2 candidates -> AMBIGUOUS.
                    "Ministerio",
                    "ministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-justice",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.plm",
        show_as="Puolustusministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Puolustusministerio",
                    "PLM",
                    "puolustusministerion",
                    "puolustusministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-defence",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.um",
        show_as="Ulkoministerio",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Ulkoministerio",
                    "UM",
                    "ulkoministerion",
                    "ulkoministerio",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.ministry-of-foreign-affairs",
    ),
    CanonicalActor(
        canonical_id="fi.ministry.vnk",
        show_as="Valtioneuvoston kanslia",
        actor_type="ministry",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1918, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Valtioneuvoston kanslia",
                    "VNK",
                    "valtioneuvoston kanslian",
                    "valtioneuvoston kanslia",
                ),
            ),
        ),
        akn_org_id="/akn/ontology/organization/fi.prime-ministers-office",
    ),
    # -----------------------------------------------------------------------
    # State agencies (with lifecycle transitions)
    # -----------------------------------------------------------------------
    CanonicalActor(
        canonical_id="fi.agency.ruokavirasto",
        show_as="Ruokavirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.mmm",
        lifecycle=(
            # Predecessor: Elintarviketurvallisuusvirasto (Evira), active until 2019-01-01
            LifecyclePeriod(
                active_from=date(2006, 5, 1),
                active_until=date(2019, 1, 1),
                phrase_variants=("Evira", "Eviran", "Elintarviketurvallisuusvirasto"),
                predecessor_id="fi.agency.ruokavirasto",
                successor_id="fi.agency.ruokavirasto",
            ),
            # Current: Ruokavirasto, from 2019-01-01
            LifecyclePeriod(
                active_from=date(2019, 1, 1),
                active_until=None,
                phrase_variants=("Ruokavirasto", "Ruokaviraston"),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.traficom",
        show_as="Liikenne- ja viestintavirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.lvm",
        lifecycle=(
            # Predecessor: Liikenteen turvallisuusvirasto (Trafi), until 2019-01-01
            LifecyclePeriod(
                active_from=date(2010, 1, 1),
                active_until=date(2019, 1, 1),
                phrase_variants=(
                    "Liikenteen turvallisuusvirasto",
                    "Trafi",
                    "Trafin",
                ),
                predecessor_id="fi.agency.traficom",
                successor_id="fi.agency.traficom",
            ),
            # Current: Liikenne- ja viestintavirasto (Traficom), from 2019-01-01
            LifecyclePeriod(
                active_from=date(2019, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Liikenne- ja viestintavirasto",
                    "Traficom",
                    "liikenne- ja viestintaviraston",
                    "Liikenne- ja viestintaviraston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.stuk",
        show_as="Sateilyturvakeskus",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.stm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1983, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Sateilyturvakeskus",
                    "STUK",
                    "Sateilyturvakeskuksen",
                    "sateilyturvakeskuksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.valvira",
        show_as="Sosiaali- ja terveysalan lupa- ja valvontavirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.stm",
        lifecycle=(
            # Predecessor: Terveydenhuollon oikeusturvakeskus (TEO) + Laakehallitus merged 2009
            LifecyclePeriod(
                active_from=date(2009, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Valvira",
                    "Valviran",
                    "Sosiaali- ja terveysalan lupa- ja valvontavirasto",
                    "sosiaali- ja terveysalan lupa- ja valvontaviraston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.fimea",
        show_as="Laakealan turvallisuus- ja kehittamiskeskus",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.stm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2009, 11, 1),
                active_until=None,
                phrase_variants=(
                    "Fimea",
                    "Fimean",
                    "Laakealan turvallisuus- ja kehittamiskeskus",
                    "laakealan turvallisuus- ja kehittamiskeskuksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.kkv",
        show_as="Kilpailu- ja kuluttajavirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.tem",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2013, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Kilpailu- ja kuluttajavirasto",
                    "KKV",
                    "kilpailu- ja kuluttajaviraston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.verohallinto",
        show_as="Verohallinto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.vm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1990, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Verohallinto",
                    "verohallinnon",
                    "Verohallinnon",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.tilastokeskus",
        show_as="Tilastokeskus",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.vm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1865, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Tilastokeskus",
                    "Tilastokeskuksen",
                    "tilastokeskuksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.maanmittauslaitos",
        show_as="Maanmittauslaitos",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.mmm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1812, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Maanmittauslaitos",
                    "Maanmittauslaitoksen",
                    "maanmittauslaitoksen",
                    "MML",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.poliisihallitus",
        show_as="Poliisihallitus",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.sm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2010, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Poliisihallitus",
                    "Poliisihallituksen",
                    "poliisihallituksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.suojelupoliisi",
        show_as="Suojelupoliisi",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.sm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1949, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Suojelupoliisi",
                    "Supon",
                    "Supo",
                    "suojelupoliisin",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.rajavartiolaitos",
        show_as="Rajavartiolaitos",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.sm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1919, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Rajavartiolaitos",
                    "Rajavartiolaitoksen",
                    "rajavartiolaitoksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.migri",
        show_as="Maahanmuuttovirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.sm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2008, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Maahanmuuttovirasto",
                    "Migri",
                    "maahanmuuttoviraston",
                    "Maahanmuuttoviraston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.kela",
        show_as="Kansanelakelaitos",
        actor_type="institution",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1937, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Kansanelakelaitos",
                    "Kela",
                    "Kelan",
                    "kansanelakelaitos",
                    "kansanelakela",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.energiavirasto",
        show_as="Energiavirasto",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.tem",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2013, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Energiavirasto",
                    "energiaviraston",
                    "Energiaviraston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.prh",
        show_as="Patentti- ja rekisterihallitus",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.tem",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1992, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Patentti- ja rekisterihallitus",
                    "PRH",
                    "patentti- ja rekisterihallituksen",
                    "Patentti- ja rekisterihallituksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.ttl",
        show_as="Tyoterveyslaitos",
        actor_type="institution",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1945, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Tyoterveyslaitos",
                    "TTL",
                    "tyoterveyslaitoksen",
                    "Tyoterveyslaitoksen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.agency.thl",
        show_as="Terveyden ja hyvinvoinnin laitos",
        actor_type="agency",
        level="state",
        parent_id="fi.ministry.stm",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2009, 1, 1),
                active_until=None,
                phrase_variants=(
                    "Terveyden ja hyvinvoinnin laitos",
                    "THL",
                    "terveyden ja hyvinvoinnin laitoksen",
                    "Terveyden ja hyvinvoinnin laitoksen",
                ),
            ),
        ),
    ),
    # -----------------------------------------------------------------------
    # Government levels
    # -----------------------------------------------------------------------
    CanonicalActor(
        canonical_id="fi.gov.valtioneuvosto",
        show_as="valtioneuvosto",
        actor_type="government_level",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1917, 12, 6),
                active_until=None,
                phrase_variants=(
                    "valtioneuvosto",
                    "Valtioneuvosto",
                    "valtioneuvoston",
                    "Valtioneuvoston",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.gov.eduskunta",
        show_as="eduskunta",
        actor_type="government_level",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1906, 1, 1),
                active_until=None,
                phrase_variants=(
                    "eduskunta",
                    "Eduskunta",
                    "eduskunnan",
                    "Eduskunnan",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.gov.kunta",
        show_as="kunta",
        actor_type="government_level",
        level="municipal",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1865, 1, 1),
                active_until=None,
                phrase_variants=(
                    "kunta",
                    "Kunta",
                    "kunnan",
                    "Kunnan",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.gov.hyvinvointialue",
        show_as="hyvinvointialue",
        actor_type="government_level",
        level="regional",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(2021, 7, 1),
                active_until=None,
                phrase_variants=(
                    "hyvinvointialue",
                    "Hyvinvointialue",
                    "hyvinvointialueen",
                    "Hyvinvointialueen",
                ),
            ),
        ),
    ),
    CanonicalActor(
        canonical_id="fi.gov.valtio",
        show_as="valtio",
        actor_type="government_level",
        level="state",
        lifecycle=(
            LifecyclePeriod(
                active_from=date(1917, 12, 6),
                active_until=None,
                phrase_variants=(
                    "valtio",
                    "Valtio",
                    "valtion",
                    "Valtion",
                ),
            ),
        ),
    ),
]


# ---------------------------------------------------------------------------
# Compiled lookup tables (built once at module load)
# ---------------------------------------------------------------------------


class ActorRegistry:
    """Compiled registry of canonical Finnish actors.

    Provides O(1) phrase lookups for the extractor hot path.
    Built once from _REGISTRY_SEED; immutable after construction.

    The registry distinguishes:
      - CURRENT phrase variants (active_until=None periods)
      - PREDECESSOR phrase variants (active_until!=None, predecessor periods)

    Ambiguous matches (phrase in multiple actors) are flagged explicitly.
    """

    def __init__(self, actors: List[CanonicalActor]) -> None:
        self._actors: Dict[str, CanonicalActor] = {a.canonical_id: a for a in actors}

        # phrase -> list[canonical_id] (may be multiple = ambiguous)
        # Built from ALL lifecycle periods (current + predecessor).
        self._phrase_to_ids: Dict[str, List[str]] = {}
        for actor in actors:
            for period in actor.lifecycle:
                for phrase in period.phrase_variants:
                    if phrase not in self._phrase_to_ids:
                        self._phrase_to_ids[phrase] = []
                    if actor.canonical_id not in self._phrase_to_ids[phrase]:
                        self._phrase_to_ids[phrase].append(actor.canonical_id)

        # All known phrases sorted longest-first for greedy matching in prose
        self._all_phrases_longest_first: Tuple[str, ...] = tuple(
            sorted(self._phrase_to_ids.keys(), key=len, reverse=True)
        )

    def lookup(
        self, phrase: str
    ) -> Tuple[Optional[str], List[str]]:
        """Look up a phrase.

        Returns (canonical_id, candidate_ids).
          - If exactly one match: canonical_id = that ID, candidate_ids = [id]
          - If ambiguous: canonical_id = None, candidate_ids = all matching IDs
          - If no match:  canonical_id = None, candidate_ids = []
        """
        candidates = self._phrase_to_ids.get(phrase, [])
        if len(candidates) == 1:
            return candidates[0], candidates
        if len(candidates) > 1:
            return None, candidates
        return None, []

    def get_actor(self, canonical_id: str) -> Optional[CanonicalActor]:
        return self._actors.get(canonical_id)

    def all_phrases_longest_first(self) -> Tuple[str, ...]:
        """All registered phrase variants, longest first.

        Used by the prose scanner to prefer longer matches.
        """
        return self._all_phrases_longest_first

    def is_predecessor_phrase_for(
        self, phrase: str, canonical_id: str
    ) -> bool:
        """Return True if phrase is a predecessor (lifecycle) phrase for canonical_id."""
        actor = self._actors.get(canonical_id)
        if actor is None:
            return False
        return actor.is_lifecycle_predecessor_phrase(phrase)

    def lifecycle_observation_for(
        self, phrase: str, canonical_id: str
    ) -> Optional[Tuple[str, str, date]]:
        """Return (predecessor_id, successor_id, lifecycle_date) if phrase is a predecessor.

        Returns None if phrase is not a lifecycle predecessor for canonical_id.
        """
        actor = self._actors.get(canonical_id)
        if actor is None:
            return None
        return actor.lifecycle_observation_for(phrase)


# ---------------------------------------------------------------------------
# Module-level singleton registry (built at import time)
# ---------------------------------------------------------------------------

REGISTRY: ActorRegistry = ActorRegistry(_REGISTRY_SEED)
