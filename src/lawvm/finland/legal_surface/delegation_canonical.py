"""Canonical token-native forward-grant delegation parser (SHADOW).

The single canonical construction parser for the Finnish delegation FORWARD
grant family (``asetuksenantovaltuus``) — the standing "one canonical parser per
surface-fact family" terminal. It supersedes the two historical rival forward
recognizers:

  * **B** — :func:`lawvm.finland.references.delegation.recognize_delegation_frames`
    (token-native over a :class:`TokenTape`; ``DelegationFrame`` +
    ``DelegationResidual``; feeds the LSG ``delegation_frame`` nodes); and
  * **C** — :func:`lawvm.finland.legal_surface.delegation_parse.parse_delegation_sentence`
    (text/clause-window construction; ``DelegationCore`` + total-ownership
    ``assert_total_ownership``; feeds the LSG ``delegated_instrument`` nodes).

This module is **SHADOW ONLY**: it is NOT wired into any lens, producer, census,
or replay path. It exists so the later cutover lanes (B-calls-canonical,
C-forward-calls-canonical, LSG cut, A flip) have a single construction to call.
The reverse authority-basis recognizer
(:func:`...delegation_parse.extract_authority_bases`) is already construction-
owned and is OUT OF SCOPE — kept native.

Substrate decision (Codex ``DELEGATION-UNIFY-VERDICT`` Q1)
=========================================================
**B's token-native TokenTape wins.** A forward grant is a surface construction
over legal tokens (actor phrases, instrument nouns, power verbs, clause bounds,
reference tails) — that belongs in the token/forest substrate, not text windows.
This parser is therefore token-native: it consumes a :class:`TokenTape` and
emits whole-token-aligned spans.

What it absorbs from each rival (the adjudicated union of their CORRECT behavior)
================================================================================
From **B** (substrate + breadth):
  * token-native clause bounds (token-index windows between terminator tokens);
  * the FULL instrument breadth — ``asetus`` / ``määräys`` / ``ohje`` / ``päätös``
    matched as instrument NOUN tokens (B caught ``…ministeriön päätöksellä
    määrätään`` grants C's two-anchor model — keyed only on ``asetuksella`` +
    ``määräyksiä``/``ohjeita`` — silently missed);
  * the cross-reference / postposition / demonstrative guards that reject
    NON-delegating instrument mentions (``valtioneuvoston asetuksen 34 §:n …
    säädetään`` is a cross-reference to an EXISTING decree, NOT a grant — B's
    guard rejects it; the old B nonetheless emitted these as FALSE POSITIVES
    because it keyed off the bare ``asetuksen`` genitive — see below).

From **C** (totality + holder semantics + instrument anchor):
  * the **holder-underspecified-never-absent** rule. The old B residualized
    ``Asetuksella säädetään …`` / ``Opetusministeriön asetuksella säädetään``
    (an actor surface B's narrow registry/role list did not carry) as
    ``delegation_without_actor`` and emitted NO frame — losing 285 genuine
    grants on the 1500-statute (min_year=2000) sample. A bare/impersonal
    ``asetuksella säädetään`` DOES grant the power to issue a decree; the issuer
    is left UNFIXED by the text, not absent. The canonical parser emits the
    grant with ``holder_underspecified=True`` rather than dropping it.
  * the broadened power-verb set (``vahvistaa`` / ``vahvistetaan`` /
    ``määritellään`` / ``säätää`` / ``määrätä`` …) — the old B's verb set lacked
    ``vahvistetaan`` / ``määritellään``, so ``…asetuksella vahvistetaan …`` /
    ``…määritellään …asetuksella`` declined;
  * total-ownership over the grant span (every char of each emitted grant's span
    is a cue / holder / instrument / basis / explicit-residual span — no silent
    drop), adapted to token spans;
  * the precise instrument-anchor span (so ``delegated_instrument`` is no longer
    a SECOND parser's product);
  * reuse of the reference sub-grammar (:func:`parse_body_provision_tail_spanned`)
    for the ``nojalla`` / ``mukaan`` provision-basis tail.

ONE closed instrument / verb / holder table (no per-shape regex).

SAFETY BOUNDARY (mirrors both rivals): SURFACE FACTS ONLY. A grant records WHO
is empowered to issue WHAT subordinate instrument, with which binding strength,
as a syntactic surface relation — never a legal conclusion (no "valid
delegation", no "power", no "discretion", no "ultra vires").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lawvm.core.legal_surface_tokens import Token, TokenTape
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    DELEGATION_ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import expand_role_actor_phrases
from lawvm.finland.references.token_actor_match import TokenActorMatcher

# Reuse the references provision-tail recognizer for the ``nojalla`` basis (do
# NOT re-implement section/momentti recognition). Unguarded import = fail loud.
from lawvm.finland.references.sections import parse_body_provision_tail_spanned

# ---------------------------------------------------------------------------
# Closed-list issuer KINDS — surface classification of the issuing authority,
# mirroring the production ``delegation_type`` vocabulary (census-comparable).
# ---------------------------------------------------------------------------
KIND_VN_ASETUS = "VN_ASETUS"      # valtioneuvoston asetus (Government decree)
KIND_MIN_ASETUS = "MIN_ASETUS"    # ministeriön asetus (Ministerial decree)
KIND_PRES_ASETUS = "PRES_ASETUS"  # tasavallan presidentin asetus
KIND_AGENCY = "AGENCY"            # viranomaisen määräys/ohje (agency regulation)
KIND_ASETUS = "ASETUS"            # generic asetus, unclassified issuer

#: Canonical instrument kinds (the lower instrument the power issues).
INSTRUMENT_ASETUS = "asetus"
INSTRUMENT_MAARAYS = "määräys"
INSTRUMENT_OHJE = "ohje"
INSTRUMENT_PAATOS = "päätös"

#: Binding strength, read off the modal surface ONLY (a SURFACE classification of
#: the modal token, NOT a legal-force assertion): voidaan/voi → may; else must.
_MAY_MODALS: frozenset[str] = frozenset({"voidaan", "voi"})

# ---------------------------------------------------------------------------
# Closed vocabularies (NORMATIVE)
# ---------------------------------------------------------------------------

#: Power-verb surfaces that, with an instrument noun, mark a delegation grant.
#: Union of B's ``_DELEGATION_VERBS`` and C's ``_POWER_VERBS`` (so neither
#: rival's accepted shape declines). Matched as exact ``word`` tokens.
#:
#: ``säädetä`` is the CONNEGATIVE of ``säätää`` ("(does not) provide"). It marks
#: the negative-RESERVATION grant ``jollei [issuer] asetuksella toisin säädetä``
#: ("unless otherwise provided BY decree") — a (negative) decree delegation the
#: production A regex (``_PAT_BARE_ASETUS``) already treats as a grant. It is
#: ADMITTED here ONLY when a forward decree anchor (``asetuksella`` /
#: ``asetuksen … nojalla``) binds: a bare ``jollei muualla laissa toisin
#: säädetä`` / ``jollei tässä asetuksessa toisin säädetä`` is a back-reference or
#: a self-reference, NOT a decree grant, and is residualized by the dedicated
#: negative-reservation guard (:func:`_is_negative_reservation_without_anchor`).
#:
#: ``rajoittaa`` / ``kieltää`` / ``rajoitetaan`` / ``kielletään`` are the
#: RESTRICTION power verbs production A carries in ``_PAT_DECREE_INVERTED``
#: (``Valtioneuvoston asetuksella voidaan rajoittaa ilmailua tai kieltää se`` —
#: the power to issue a decree that RESTRICTS / PROHIBITS an activity is a genuine
#: decree grant). The old canonical verb set lacked them, residualizing the clause
#: as ``instrument_without_power_verb`` (fail-loud, not silent) and MISSING the
#: grant — the lone A-ONLY drop the differential found (2009/1194 §8.1). Added so
#: the canonical recall covers A's restriction-decree shape.
_POWER_VERBS: frozenset[str] = frozenset(
    {
        "säädetään",
        "säätää",
        "säädetä",
        "säädettävä",
        "annetaan",
        "antaa",
        "annettava",
        "määrätään",
        "määrätä",
        "päättää",
        "päätetään",
        "vahvistetaan",
        "vahvistaa",
        "määritellään",
        "rajoittaa",
        "rajoitetaan",
        "kieltää",
        "kielletään",
    }
)

#: Instrument-noun surfaces (the noun naming the lower instrument). CLOSED. Keyed
#: as exact ``word`` tokens (B's instrument-noun breadth — includes the genitive
#: ``…n`` and adessive ``…lla/…llä`` cases C's two-anchor model lacked for
#: ``päätös`` / object cases for ``ohje``).
_INSTRUMENT_SURFACE_TO_KIND: tuple[tuple[str, str], ...] = (
    ("asetuks", INSTRUMENT_ASETUS),
    ("asetus", INSTRUMENT_ASETUS),
    ("määräy", INSTRUMENT_MAARAYS),
    ("ohje", INSTRUMENT_OHJE),
    ("päätö", INSTRUMENT_PAATOS),
)

_INSTRUMENT_NOUNS: frozenset[str] = frozenset(
    {
        "asetuksella",
        "asetuksen",
        "asetus",
        "määräyksiä",
        "määräyksen",
        "määräykset",
        "määräys",
        "ohjeita",
        "ohjeet",
        "ohjeen",
        "ohje",
        "päätöksellä",
        "päätöksen",
        "päätös",
    }
)


def _instrument_kind_for_surface(surface: str) -> str | None:
    low = surface.lower()
    for root, kind in _INSTRUMENT_SURFACE_TO_KIND:
        if low.startswith(root):
            return kind
    return None


#: Postposition surfaces that take a genitive complement. An instrument noun in
#: the genitive immediately FOLLOWED by one of these is the complement of the
#: postposition phrase (the enacting preamble ``päätöksen mukaisesti säädetään``
#: / the ``… nojalla`` authority basis), NOT a delegated instrument.
_POSTPOSITIONS: frozenset[str] = frozenset(
    {"mukaisesti", "mukaan", "nojalla", "perusteella", "estämättä"}
)

#: Demonstrative determiners heading a SELF-/CROSS-reference to an EXISTING
#: instrument (``tällä asetuksella`` = the enacting decree's OWN power; ``tämän
#: asetuksen`` / ``tässä asetuksessa`` = a decree that already exists). An
#: instrument noun immediately PRECEDED by one of these does NOT grant a new
#: lower instrument.
_DEMONSTRATIVES: frozenset[str] = frozenset(
    {
        "tätä",
        "tämän",
        "tässä",
        "tästä",
        "tähän",
        "tällä",
        "tuota",
        "tuon",
        "tuossa",
        "sitä",
        "sen",
        "siinä",
        "siihen",
    }
)

#: Maximum subject-span length (chars) captured as the trailing subject surface.
_MAX_SUBJECT_SPAN = 200

# ---------------------------------------------------------------------------
# Over-recognition guards (CLOSED) — two grant-SHAPED-but-not-a-grant shapes the
# bare instrument-noun + power-verb co-occurrence test mints as FALSE POSITIVES.
# Both are SURGICAL: they fire only on a tightly-specified surface frame and
# never on a forward decree/agency grant anchor.
# ---------------------------------------------------------------------------

#: Anaphoric connectives that head an "as provided in …" back-reference. When one
#: governs ``säädetään`` (the power verb is a BACK-reference to where a matter is
#: ALREADY regulated, not a forward grant), the clause is an anaphoric reference,
#: NOT a delegation — PROVIDED no forward decree anchor (``asetuksella`` /
#: ``asetuksen … nojalla``) also sits in the clause (``siten kuin asetuksella
#: tarkemmin säädetään`` IS a genuine decree grant and must survive).
_ANAPHORIC_CONNECTIVE_SURFACES: tuple[tuple[str, ...], ...] = (
    ("siten", "kuin"),
    ("sen", "mukaan", "kuin"),
    ("mukaan", "kuin"),
    ("noudattaen", "soveltuvin", "osin"),
)

#: A bare ``mitä …`` / ``, mitä …`` anaphor (``ei sovelleta, mitä … säädetään`` /
#: ``ottaen huomioon, mitä … säädetään``) also heads a back-reference.
_ANAPHOR_PRONOUN = "mitä"

#: The power verbs that, under an anaphoric connective, are BACK-references (the
#: matter is provided FOR elsewhere). Closed: only the ``säätää`` family — an
#: anaphoric ``annetaan`` is handled by the subject-collision guard, not here.
_ANAPHORIC_BACKREF_VERBS: frozenset[str] = frozenset(
    {"säädetään", "säädetä", "säädetty", "säädetyn"}
)

#: Forward decree anchors. Their presence in an anaphoric clause means a decree
#: power IS granted (``siten kuin asetuksella säädetään``) — guard 1 must NOT fire.
_DECREE_ANCHOR_RE = re.compile(
    r"\basetuksella\b|\basetuksen\b[^.;:]{0,80}\bnojalla\b", re.IGNORECASE
)

#: Nominative-SINGULAR instrument surfaces. As the clause SUBJECT of a passive /
#: copular predicate (``Päätös annetaan …`` / ``Määräys on annettava …`` /
#: ``Ohje voidaan antaa …``) the instrument word is the REGULATED subject, not the
#: delegated object — a subject-NP collision, NOT a grant. CLOSED.
_NOMINATIVE_SINGULAR_INSTRUMENTS: frozenset[str] = frozenset(
    {"asetus", "määräys", "ohje", "päätös"}
)

#: Passive / copular predicate heads that, with a nominative-singular instrument
#: SUBJECT, mark the subject-collision shape (``annetaan`` passive "is given";
#: ``on`` + a ``-tava/-ttava`` necessitive; ``voidaan`` + an infinitive). An
#: ACTIVE ``antaa`` taking the instrument as OBJECT is a genuine grant and is NOT
#: in this set (``Ohjeet … antaa viranomainen``).
_PASSIVE_PREDICATE_VERBS: frozenset[str] = frozenset(
    {
        "annetaan",
        "tehdään",
        "pannaan",
        "vahvistetaan",
        "julkaistaan",
        "ratkaistaan",
    }
)
#: Copular / necessitive auxiliaries leading a passive obligation (``on pantava
#: täytäntöön`` / ``on annettava tiedoksi`` / ``on oltava``).
_COPULAR_AUX = frozenset({"on", "voidaan"})

#: ACTIVE grant verbs — a 3rd-person active ``antaa`` / ``vahvistaa`` taking the
#: instrument as OBJECT is a genuine forward grant (``määräyksen antaa
#: viranomainen noudattaen, mitä … säädetään`` — the ``noudattaen mitä …
#: säädetään`` is mere MANNER; the grant ``antaa määräyksen`` stands). Their
#: presence makes the anaphoric guard STAND DOWN so such grants survive.
_ACTIVE_GRANT_VERBS: frozenset[str] = frozenset(
    {"antaa", "antavat", "vahvistaa", "vahvistavat", "määrää", "määräävät", "hyväksyy"}
)

#: Necessitive participles ("must be issued/made/drawn up"). With a leading ``on``
#: auxiliary and an instrument OBJECT (``päätös`` / ``määräys`` / ``ohje``) they
#: mark a one-off PROCEDURAL DUTY to issue that instrument in a single case
#: (``hakemukseen on annettava kirjallinen päätös`` = "a written decision must be
#: issued on the application"; ``Luvassa on annettava tarpeelliset määräykset`` =
#: "the permit must contain the necessary conditions") — NOT a delegated power to
#: MAKE general subordinate rules. CLOSED. (``säädettävä`` is here because the
#: ``säätää``-family is rule-MAKING; a necessitive ``on säädettävä asetuksella``
#: still grants — the guard excludes the ``asetus`` instrument, so that survives.)
_NECESSITIVE_PARTICIPLES: frozenset[str] = frozenset(
    {"annettava", "tehtävä", "laadittava", "säädettävä"}
)

#: The instrument kinds that, as the OBJECT of a necessitive duty, are a one-off
#: procedural duty rather than a rule-MAKING delegation. ``asetus`` is DELIBERATELY
#: absent: ``[säännökset] on annettava asetuksella`` (provisions must be given BY
#: decree) IS a genuine decree grant — the decree is the MEANS, not the object — so
#: an ``asetus`` instrument never fires this guard.
_PROCEDURAL_DUTY_OBJECT_INSTRUMENTS: frozenset[str] = frozenset(
    {INSTRUMENT_MAARAYS, INSTRUMENT_OHJE, INSTRUMENT_PAATOS}
)

#: Issuance verbs that, with a ``päätös`` OBJECT, mark a ONE-OFF decision issuance
#: (guard 4). The passive-present ``annetaan`` ("is issued") and the active
#: ``antaa`` under a ``voidaan`` / ``voi`` modal ("may be issued") express the
#: single-case decision the necessitive ``annettava`` (guard 3) expresses with the
#: obligation modality. The same one-off-vs-rule-making distinction holds.
#: ``päättää`` / ``päätetään`` ("decide(s)") and ``tehdä`` ("make") govern a
#: ``päätös`` OBJECT to make a single-case DECISION (``voi erikseen päättää …
#: julkaisemisesta``; ``voidaan tehdä päätös … aluejaosta``; ``presidentti päättää
#: agrementin pyytämisestä``) — a one-off administrative decision, never a
#: rule-MAKING delegation. Added alongside ``annetaan`` / ``antaa`` in guard 4.
_DECISION_ISSUANCE_VERBS: frozenset[str] = frozenset(
    {"annetaan", "antaa", "päättää", "päätetään", "päättävät", "tehdä", "tehdään"}
)

#: ``päätös`` instrument surfaces that are the issued OBJECT of a decision (the
#: nominative ``päätös`` and the genitive/accusative ``päätöksen``). The
#: INSTRUMENTAL ``päätöksellä`` is DELIBERATELY absent: ``[tarkemmat määräykset]
#: annetaan … päätöksellä`` / ``määrätään ministeriön päätöksellä`` is the genuine
#: decision-as-MEANS rule-making grant (the historical ministerial päätös decree),
#: exactly parallel to the ``asetus`` exclusion in guard 3 — the päätöksellä is the
#: instrument the power issues BY, not the one-off decision being issued.
_DECISION_OBJECT_SURFACES: frozenset[str] = frozenset({"päätös", "päätöksen"})

# ---------------------------------------------------------------------------
# AGENCY-family over-recognition guards (CLOSED) — the bare instrument-noun +
# power-verb co-occurrence test mints ~half of its ``määräys`` / ``ohje`` /
# ``päätös`` AGENCY edges as FALSE POSITIVES: a court/tribunal exercising an
# adjudicative power, a penal offence clause referencing a norm, a single-case
# administrative order, an appeal cross-reference, and a norm PROVIDED IN an
# internal bylaw (työjärjestys / ohjesääntö / johtosääntö / taloussääntö) rather
# than delegated as a statutory decree / agency rule. Each guard below is SURGICAL
# (a tightly-specified surface frame) and stands down on any genuine rule-making
# anchor. A genuine agency grant is ``[viranomainen] (voi) antaa (tarkempia)
# määräyksiä / ohjeita [aiheesta]`` — never matched by these frames.
# ---------------------------------------------------------------------------

#: Court / tribunal ISSUER head surfaces. A clause whose subject is a court
#: ``antaa määräyksen`` / ``antaa päätöksen`` / ``määrää`` exercises an
#: ADJUDICATIVE power in a case, NOT a delegated power to MAKE general subordinate
#: rules. Closed list of the Finnish court nouns (the ``-oikeus`` court family +
#: the generic ``tuomioistuin``). Matched as a ``word`` token whose lowercase form
#: equals / ends in one of these (so the inessive/illative inflections of an
#: APPEAL target — ``hallinto-oikeuteen`` / ``korkeimpaan oikeuteen`` — are caught
#: by the dedicated appeal guard, not here; this set is the NOMINATIVE issuer).
_COURT_ISSUER_SURFACES: frozenset[str] = frozenset(
    {
        "tuomioistuin",
        "oikeus",
        "hovioikeus",
        "käräjäoikeus",
        "vakuutusoikeus",
        "markkinaoikeus",
        "työtuomioistuin",
        "hallinto-oikeus",
        "korkein",  # "korkein oikeus" / "korkein hallinto-oikeus"
    }
)

#: Adjudicative verbs a court exercises (issue a ruling/decision/order, annul,
#: refer back, prohibit). With a court issuer + a ``päätös`` / ``määräys``
#: instrument they mark an in-case adjudication, not a rule-making delegation.
_ADJUDICATIVE_VERBS: frozenset[str] = frozenset(
    {
        "määrätä",
        "määrää",
        "poistaa",
        "antaa",
        "antaisi",
        "tehdä",
        "ratkaista",
        "ratkaisee",
        "kieltää",
        "velvoittaa",
    }
)

#: Penal-offence predicate surfaces. A clause headed by the relative ``Joka``
#: ("whoever") that culminates in one of these PUNISHES a person; any ``määräys``
#: it names (``määräyksen vastaisesti`` / ``rikkoo … määräyksiä``) is the norm
#: VIOLATED, not a delegated rule-making power. CLOSED.
_PENAL_PREDICATE_SURFACES: frozenset[str] = frozenset(
    {
        "tuomittava",
        "rangaistava",
        "rangaistaan",
        "sakkoon",
        "vankeuteen",
        "vankeutta",
        "rangaistus",
        "sakkoa",
    }
)

#: Bylaw / internal-instrument INESSIVE noun surfaces. A norm ``annetaan`` /
#: ``voidaan antaa määräyksiä`` IN one of these (``työjärjestyksessä`` /
#: ``ohjesäännössä`` / ``johtosäännössä`` / ``taloussäännössä`` …) is provided in
#: an internal bylaw — NOT delegated as a statutory decree or an agency rule. The
#: instrument-bearing LOCUS is the bylaw, so the ``määräys`` / ``ohje`` is the
#: bylaw's own content, not a delegated rule-making power. CLOSED; matched as a
#: ``word`` token whose lowercase form ends in one of these inessive heads.
_BYLAW_INSTRUMENT_INESSIVE_SURFACES: tuple[str, ...] = (
    "työjärjestyksessä",
    "ohjesäännössä",
    "johtosäännössä",
    "taloussäännössä",
    "tutkintosäännössä",
    "työjärjestyksellä",
    "ohjesäännöllä",
    "johtosäännöllä",
    "taloussäännöllä",
    "tutkintosäännöllä",
)

#: ``julkais-`` PUBLISHING verb prefix. A clause where the ``määräys`` / ``ohje``
#: is PUBLISHED (``määräykset julkaistaan säädöskokoelmassa`` / a
#: ``julkaisemismääräys`` about ``… julkaisemisesta``) regulates the PUBLICATION of
#: norms, NOT the power to MAKE them — the norms already exist and are merely
#: published. A ``word`` token whose lowercase form starts with this prefix marks
#: the publishing frame. (Witnessed across the Säädöskokoelma publishing law,
#: 2000/188 / 2000/189.)
_PUBLISHING_VERB_PREFIX = "julkais"

#: Single-case marker phrase. ``antaa … yksittäisessä tapauksessa koskevia
#: määräyksiä`` directs an authority IN A SINGLE CASE — a one-off direction, not a
#: general subordinate rule. Fires only when NO rule-making quantifier
#: (``yleisiä`` / ``tarkempia`` …) heads the object: ``voi antaa YLEISIÄ
#: määräyksiä … ja päättää … yksittäisessä tapauksessa`` IS a genuine general
#: rule-making grant (the single-case clause is a SEPARATE conjunct) and survives.
_SINGLE_CASE_MARKER: tuple[str, str] = ("yksittäisessä", "tapauksessa")

#: Appeal-reference verb surfaces. ``saa(vat) valittaa päätöksestä …
#: hallinto-oikeuteen`` / ``on oikeus valittaa viraston päätöksestä`` cite a RIGHT
#: TO APPEAL an existing decision to a court — the ``päätös`` is the appealed
#: object, never a delegated rule-making instrument. CLOSED.
_APPEAL_VERB_SURFACES: frozenset[str] = frozenset(
    {"valittaa", "valitetaan", "hakea"}
)

# ---------------------------------------------------------------------------
# Shared token-native actor matcher (registry phrases UNION closed role actors).
# ---------------------------------------------------------------------------


def _build_actor_phrases() -> tuple[str, ...]:
    phrases = set(REGISTRY.all_phrases_longest_first())
    phrases.update(expand_role_actor_phrases(_ROLE_ACTORS))
    return tuple(sorted(phrases, key=len, reverse=True))


_ACTOR_MATCHER = TokenActorMatcher(_build_actor_phrases())

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

#: The canonical grammar owned the grant (in-scope, no silent drop).
DELEGATION_LANE_CANONICAL_OWNED = "delegation_canonical_owned"

#: Residual classes: a grant-SHAPED clause the parser SEES but does NOT emit as a
#: grant (never silent, never guessed). Closed set.
ResidualKind = (
    "self_reference_instrument",        # ``tällä asetuksella säädetään`` (own power)
    "cross_reference_instrument",       # ``asetuksen 34 §:ssä säädetään`` (existing)
    "postposition_complement",          # ``päätöksen mukaisesti säädetään``
    "instrument_without_power_verb",    # an instrument noun, no delegation verb
    "anaphoric_reference",              # ``siten kuin hallintolaissa säädetään``
    "subject_np_collision",            # ``Päätös annetaan tiedoksi …``
    "procedural_duty_object",          # ``hakemukseen on annettava päätös`` (one-off duty)
    "decision_issuance_object",        # ``hakemukseen annetaan kielteinen päätös`` (one-off)
    "negative_reservation",            # ``jollei muualla laissa toisin säädetä`` (no anchor)
    "commencement_clause",             # ``Tämän lain voimaantulosta säädetään asetuksella``
    "court_power",                     # ``tuomioistuin voi … määrätä`` (adjudication)
    "penal_clause_reference",          # ``Joka … määräyksen vastaisesti … on tuomittava``
    "cause_to_suspect_reference",      # ``antaa aiheen epäillä … määräyksistä`` (idiom)
    "noncompliance_reference",         # ``jättää noudattamatta … määräyksiä`` (violation)
    "single_case_order",               # ``määräyksen antaneelle …`` (one-off order ref)
    "appeal_reference",                # ``saa valittaa päätöksestä … oikeuteen``
    "bylaw_provided_norm",             # ``annetaan työjärjestyksessä`` (internal bylaw)
    "published_norm_reference",        # ``määräykset julkaistaan säädöskokoelmassa``
    "single_case_direction",           # ``antaa … yksittäisessä tapauksessa … määräyksiä``
    "benign_uninterpreted_prose",       # totality filler between owned spans
)


@dataclass(frozen=True, slots=True)
class GrantResidual:
    """An explicit unowned/declined span (no-silent-drop typed residue).

    Self-evidencing: ``surface_text`` embeds the verbatim offending fragment and
    ``reason`` names the closed residual class.
    """

    kind: str
    char_start: int
    char_end: int
    surface_text: str
    reason: str


@dataclass(frozen=True, slots=True)
class DelegationGrant:
    """One canonical forward-grant core. SURFACE FACT ONLY.

    Records WHO is empowered (the holder, or an underspecified issuer) to issue
    WHAT lower instrument under which binding strength, optionally citing a
    provision basis. Spans are whole-token-aligned char offsets into the parsed
    text (sentence-local when parsed per sentence; tape-relative otherwise).

    Attributes:
        kind:             Issuer SURFACE class (VN/MIN/PRES/AGENCY/generic ASETUS).
        instrument:       Canonical instrument kind (asetus/määräys/ohje/päätös).
        binding_strength: "must" / "may", read off the modal surface ONLY.
        cue:              The power-verb anchor surface (verbatim).
        cue_start/cue_end: Char span of the power-verb anchor.
        instrument_surface: The instrument-noun anchor surface (verbatim).
        instrument_start/instrument_end: Char span of the instrument anchor.
        holder_surface:   The bound authority-holder NP surface, or "" when
                          underspecified.
        holder_start/holder_end: Char span of the holder NP, or None when
                          underspecified.
        holder_underspecified: True when no overt issuer NP binds (the bare /
                          impersonal register). NOT "absent" — the issuer exists
                          in the grant, left unfixed by the text.
        frame_start/frame_end: Whole-frame (clause) span the grant lives in.
        subject_start/subject_end: Trailing subject SURFACE span (or None).
        basis_start/basis_end: ``… nojalla`` / ``… mukaan`` provision-basis window
                          (or None), reused from the references sub-grammar.
        basis_targets:    references-recognized provision target labels in the
                          basis window (empty when no basis / none recognized).
        rule_id:          The recognizer rule that fired.
    """

    kind: str
    instrument: str
    binding_strength: str
    cue: str
    cue_start: int
    cue_end: int
    instrument_surface: str
    instrument_start: int
    instrument_end: int
    holder_surface: str
    holder_start: int | None
    holder_end: int | None
    holder_underspecified: bool
    frame_start: int
    frame_end: int
    subject_start: int | None
    subject_end: int | None
    basis_start: int | None
    basis_end: int | None
    basis_targets: tuple[str, ...]
    rule_id: str


@dataclass(frozen=True, slots=True)
class DelegationGrantScan:
    """The full canonical scan of one text: typed grants + typed residuals."""

    grants: tuple[DelegationGrant, ...]
    residuals: tuple[GrantResidual, ...] = field(default_factory=tuple)


_RULE_ID = "fi.delegation.canonical.v0"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _is_terminator(tok: Token) -> bool:
    """A token bounding a clause window (token-side mirror of ``.;:`` / newline)."""
    if tok.category == "punct" and tok.text in ".;:":
        return True
    if tok.category == "whitespace" and "\n" in tok.text:
        return True
    return False


def _clause_token_bounds(tokens: tuple[Token, ...], idx: int) -> tuple[int, int]:
    """(lo, hi) token-index bounds of the clause containing token ``idx``.

    Bounded by the nearest clause-terminator tokens (terminator excluded);
    leading/trailing whitespace trimmed.
    """
    lo = idx
    while lo > 0 and not _is_terminator(tokens[lo - 1]):
        lo -= 1
    hi = idx
    n = len(tokens)
    while hi < n and not _is_terminator(tokens[hi]):
        hi += 1
    while lo < hi and tokens[lo].category == "whitespace":
        lo += 1
    while hi > lo and tokens[hi - 1].category == "whitespace":
        hi -= 1
    return (lo, hi)


def _prev_word_token(tokens: tuple[Token, ...], idx: int) -> Token | None:
    j = idx - 1
    while j >= 0:
        if tokens[j].category == "word":
            return tokens[j]
        if tokens[j].category != "whitespace":
            return None
        j -= 1
    return None


def _next_word_token(tokens: tuple[Token, ...], idx: int) -> Token | None:
    n = len(tokens)
    j = idx + 1
    while j < n:
        if tokens[j].category == "word":
            return tokens[j]
        if tokens[j].category != "whitespace":
            return None
        j += 1
    return None


def _next_token_is_section_path(tokens: tuple[Token, ...], idx: int) -> bool:
    """True when the instrument noun is followed by a CROSS-REFERENCE tail.

    Two existing-instrument cross-reference surfaces (NOT a granted instrument):

      * ``valtioneuvoston asetuksen 34 §:n 2 momentissa säädetään`` — the genitive
        instrument noun is immediately followed by a ``number`` token leading into
        a ``§`` / ``colon_suffix`` section path (the dominant old-B false
        positive); and
      * ``… annetun asetuksen (575/1988) 1―22 §:ssä säädetään`` — the genitive
        instrument noun is immediately followed by a parenthesized ``(NUM/YEAR)``
        statute id, then a section path. Citing an EXISTING decree by id is never
        a forward grant.
    """
    n = len(tokens)
    j = idx + 1
    while j < n and tokens[j].category == "whitespace":
        j += 1
    if j >= n:
        return False
    # Case 1: directly a section number ``N §``.
    if tokens[j].category == "number":
        k = j + 1
        while k < n and tokens[k].category == "whitespace":
            k += 1
        if k < n and tokens[k].category in ("section_mark", "colon_suffix"):
            return True
        return False
    # Case 2: a parenthesized statute id ``(NUM/YEAR)`` follows the genitive.
    if tokens[j].category == "punct" and tokens[j].text == "(":
        # scan a bounded window for ``NUM / YEAR )`` shape
        seg = "".join(t.text for t in tokens[j : min(n, j + 8)])


        if re.match(r"\(\s*\d{1,5}\s*/\s*\d{2,4}\s*\)", seg):
            return True
    return False


def _classify_kind(holder_surface: str, instrument: str) -> str:
    """Issuer SURFACE class from the bound holder + instrument.

    Mirrors the production / C classifier precedence: a genitive valtioneuvoston /
    ministeriön / presidentin issuer → VN/MIN/PRES asetus; a määräys/ohje
    instrument → AGENCY; bare asetus → generic ASETUS. ``päätös`` instruments
    follow the holder's genitive class (a ministerial ``päätöksellä``) and fall
    to AGENCY when no decree-issuer genitive binds.
    """
    if instrument in (INSTRUMENT_MAARAYS, INSTRUMENT_OHJE):
        return KIND_AGENCY
    t = holder_surface.lower()
    if "valtioneuvoston" in t:
        return KIND_VN_ASETUS
    if "ministeriön" in t:
        return KIND_MIN_ASETUS
    if "presidentin" in t:
        return KIND_PRES_ASETUS
    if instrument == INSTRUMENT_PAATOS:
        # A ministerial/agency decision instrument with no decree-issuer genitive.
        return KIND_AGENCY
    return KIND_ASETUS


def _capture_subject_span(
    tokens: tuple[Token, ...], after_index: int, clause_hi: int
) -> tuple[int, int] | None:
    i = after_index
    while i < clause_hi and tokens[i].category == "whitespace":
        i += 1
    if i >= clause_hi:
        return None
    char_start = tokens[i].char_start
    limit = char_start + _MAX_SUBJECT_SPAN
    last_nonspace_end: int | None = None
    j = i
    while j < clause_hi:
        tok = tokens[j]
        if tok.char_start >= limit:
            break
        if tok.category != "whitespace":
            last_nonspace_end = tok.char_end
        j += 1
    if last_nonspace_end is None:
        return None
    return (char_start, last_nonspace_end)


def _first_power_verb_index(
    tokens: tuple[Token, ...], lo: int, hi: int
) -> int | None:
    for j in range(lo, hi):
        tok = tokens[j]
        if tok.category == "word" and tok.text in _POWER_VERBS:
            return j
    return None


def _clause_has_may_modal(tokens: tuple[Token, ...], lo: int, hi: int) -> bool:
    for j in range(lo, hi):
        tok = tokens[j]
        if tok.category == "word" and tok.text in _MAY_MODALS:
            return True
    return False


def _word_tokens(tokens: tuple[Token, ...], lo: int, hi: int) -> list[Token]:
    return [tokens[j] for j in range(lo, hi) if tokens[j].category == "word"]


def _clause_has_anaphoric_connective(words: list[Token]) -> bool:
    """True iff a closed anaphoric connective heads a back-reference in the clause.

    Matches the multi-word ``siten kuin`` / ``sen mukaan kuin`` / ``noudattaen
    soveltuvin osin`` connectives over consecutive WORD tokens, or a bare ``mitä``
    relative pronoun (``ei sovelleta, mitä … säädetään``).
    """
    lowered = [w.text.lower() for w in words]
    if _ANAPHOR_PRONOUN in lowered:
        return True
    for phrase in _ANAPHORIC_CONNECTIVE_SURFACES:
        n = len(phrase)
        for j in range(0, len(lowered) - n + 1):
            if tuple(lowered[j : j + n]) == phrase:
                return True
    return False


def _clause_has_decree_anchor(clause_text: str) -> bool:
    """True iff a FORWARD decree power anchor sits in the clause.

    ``asetuksella`` (adessive "by decree") or ``asetuksen … nojalla`` — the
    presence of either means a decree power IS granted even under an anaphoric
    connective (``siten kuin asetuksella säädetään``), so guard 1 must stand down.
    """
    return _DECREE_ANCHOR_RE.search(clause_text) is not None


def _clause_has_active_grant_verb(words: list[Token]) -> bool:
    """True iff a 3rd-person ACTIVE grant verb (``antaa``/``vahvistaa``/…) is in the
    clause — the signal of a GENUINE forward grant whose ``noudattaen mitä …
    säädetään`` is mere manner. Makes the anaphoric guard stand down."""
    return any(w.text.lower() in _ACTIVE_GRANT_VERBS for w in words)


def _is_anaphoric_reference_clause(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    verb_idx: int,
    clause_text: str,
) -> bool:
    """Guard 1: the clause's only grant signal is an anaphoric ``… säädetään``.

    A clause's grant was minted on its first power verb, but the actual semantics
    is a BACK-reference: ``siten kuin / sen mukaan kuin / noudattaen … mitä …
    säädetään`` cites where the matter is ALREADY provided for. Fires only when
    ALL hold:
      * a ``säätää``-family back-reference verb sits in the clause (the matched
        first power verb may be the subject's passive ``annetaan`` — the anaphoric
        ``säädetään`` follows it);
      * a closed anaphoric connective heads the back-reference;
      * NO forward decree anchor (``asetuksella`` / ``asetuksen … nojalla``) — the
        genuine ``siten kuin asetuksella säädetään`` decree grant is preserved; and
      * NO active grant verb (``antaa``/``vahvistaa``/…) — a genuine ``määräyksen
        antaa viranomainen noudattaen, mitä … säädetään`` grant is preserved.
    """
    words = _word_tokens(tokens, clause_lo, clause_hi)
    has_backref_verb = (
        tokens[verb_idx].text.lower() in _ANAPHORIC_BACKREF_VERBS
        or any(w.text.lower() in _ANAPHORIC_BACKREF_VERBS for w in words)
    )
    if not has_backref_verb:
        return False
    if not _clause_has_anaphoric_connective(words):
        return False
    if _clause_has_decree_anchor(clause_text):
        return False
    if _clause_has_active_grant_verb(words):
        return False
    return True


def _is_subject_np_collision(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
) -> bool:
    """Guard 2: the instrument word is the clause SUBJECT, not the delegated object.

    Fires only when the triggering instrument is a NOMINATIVE-SINGULAR surface
    (``Päätös`` / ``Määräys`` / ``Ohje`` / ``Asetus``) heading the clause SUBJECT
    NP — either the first word token, or preceded ONLY by genitive modifier words
    (``Ministeriön päätös`` / ``Viranhaltijan päätös``) — governing a PASSIVE /
    copular-necessitive predicate (``annetaan`` / ``on annettava`` / ``voidaan
    antaa`` / ``on pantava``). An ACTIVE ``antaa`` taking the instrument as object
    (a genuine grant) makes the guard stand down; a non-subject instrument is not
    matched.
    """
    if tokens[inst_idx].text.lower() not in _NOMINATIVE_SINGULAR_INSTRUMENTS:
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    if not words:
        return False
    # Locate the instrument among the clause word tokens.
    inst_pos = next(
        (k for k, w in enumerate(words) if w.char_start == tokens[inst_idx].char_start),
        None,
    )
    if inst_pos is None:
        return False
    # The instrument must HEAD the subject NP: every preceding word is a genitive
    # modifier (``…n``) — i.e. the subject is ``[X:n] päätös``, not a deep-clause
    # object. The clause-initial instrument (inst_pos == 0) trivially qualifies.
    if any(not words[k].text.lower().endswith("n") for k in range(inst_pos)):
        return False
    # A genuine active grant (``antaa``/``vahvistaa``) anywhere → not a collision.
    if _clause_has_active_grant_verb(words):
        return False
    # Walk the WORD tokens after the instrument; the predicate head is a passive
    # verb or a copular/necessitive auxiliary leading an obligation.
    for w in words[inst_pos + 1 :]:
        low = w.text.lower()
        if low in _PASSIVE_PREDICATE_VERBS or low in _COPULAR_AUX:
            return True
    return False


def _is_procedural_duty_object(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    verb_idx: int,
    clause_text: str,
) -> bool:
    """Guard 3: the instrument is the OBJECT of a necessitive procedural DUTY.

    ``Palkkaturvahakemukseen on annettava kirjallinen päätös`` ("a written
    decision MUST BE ISSUED on the application") / ``Luvassa on annettava
    tarpeelliset määräykset`` ("the permit must contain the necessary conditions")
    are one-off procedural duties to issue an instrument in a single case — NOT a
    delegated power to MAKE general subordinate rules. The bare instrument-noun +
    power-verb co-occurrence test mints them because the necessitive ``annettava``
    is in :data:`_POWER_VERBS` and ``päätös`` / ``määräys`` / ``ohje`` is the
    object. Fires only when ALL hold:

      * the matched power verb is a necessitive participle
        (``annettava`` / ``tehtävä`` / ``laadittava`` / ``säädettävä``) governed by
        a leading ``on`` auxiliary (the ``on annettava`` necessitive frame);
      * the triggering instrument is a procedural-duty OBJECT kind
        (``määräys`` / ``ohje`` / ``päätös``) — ``asetus`` is excluded so the
        genuine ``[säännökset] on annettava asetuksella`` decree-by-means grant
        survives; and
      * NO genuine rule-MAKING signal stands the guard down: no forward decree
        anchor (``asetuksella`` / ``asetuksen … nojalla``), and no ACTIVE grant
        verb (``antaa`` / ``vahvistaa`` / ``määrää`` …) distinct from the
        necessitive participle (``Ohjeet … antaa viranomainen`` is a genuine
        grant, not a duty).
    """
    if tokens[verb_idx].text.lower() not in _NECESSITIVE_PARTICIPLES:
        return False
    instrument = _instrument_kind_for_surface(tokens[inst_idx].text)
    if instrument not in _PROCEDURAL_DUTY_OBJECT_INSTRUMENTS:
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    # A leading ``on`` auxiliary must govern the necessitive participle (the
    # ``on annettava`` frame). An ``on`` anywhere before the participle suffices —
    # Finnish allows intervening adverbials (``on viran puolesta annettava``).
    verb_char = tokens[verb_idx].char_start
    has_on_aux = any(
        w.text.lower() == "on" and w.char_start < verb_char for w in words
    )
    if not has_on_aux:
        return False
    # Stand-down 1: a forward decree anchor → a decree power IS granted.
    if _clause_has_decree_anchor(clause_text):
        return False
    # Stand-down 2: an ACTIVE grant verb distinct from the necessitive participle
    # (the participle ``annettava`` is NOT in _ACTIVE_GRANT_VERBS, so this only
    # fires on a separate active ``antaa``/``vahvistaa``/… → genuine grant).
    if _clause_has_active_grant_verb(words):
        return False
    return True


def _is_decision_issuance_object(
    tokens: tuple[Token, ...],
    inst_idx: int,
    verb_idx: int,
    clause_text: str,
) -> bool:
    """Guard 4: a ``päätös`` OBJECT is a one-off decision ISSUANCE, not a grant.

    The passive-present / modal counterpart of guard 3 (the necessitive
    ``annettava`` duty). ``Muuttamisesta annetaan pyynnöstä päätös`` ("a decision
    IS ISSUED on request"), ``hakemukseen annetaan kielteinen päätös`` ("a negative
    decision is issued on the application"), ``Perittävää määrää koskeva päätös
    voidaan antaa sen jälkeen`` ("the decision MAY BE ISSUED thereafter") are all
    one-off administrative decisions issued in a single case — NOT a delegated power
    to MAKE general subordinate rules. The bare instrument-noun + power-verb
    co-occurrence test mints them because ``annetaan`` / ``antaa`` is in
    :data:`_POWER_VERBS` and ``päätös`` is the issued object. Fires only when ALL
    hold:

      * the matched power verb is a decision-issuance verb (``annetaan`` passive
        present, or an ``antaa`` under a ``voidaan`` / ``voi`` modal);
      * the triggering instrument is a ``päätös`` OBJECT surface (``päätös`` /
        ``päätöksen``) — the INSTRUMENTAL ``päätöksellä`` is excluded so the genuine
        ``[tarkemmat määräykset] annetaan … päätöksellä`` decision-as-MEANS grant
        survives (parallel to guard 3's ``asetus`` exclusion); and
      * NO forward decree anchor (``asetuksella`` / ``asetuksen … nojalla``) stands
        the guard down (``päätös … annetaan asetuksella`` IS a decree grant).

    Scoped to ``päätös`` DELIBERATELY: a ``päätös`` (a single decision) is, in the
    issued-object position, essentially never a rule-MAKING instrument — whereas
    ``määräys`` / ``ohje`` objects under ``antaa`` are overwhelmingly genuine agency
    rule-making grants (``viranomainen voi antaa tarkempia määräyksiä``) the guard
    must NOT touch.
    """
    if tokens[verb_idx].text.lower() not in _DECISION_ISSUANCE_VERBS:
        return False
    if tokens[inst_idx].text.lower() not in _DECISION_OBJECT_SURFACES:
        return False
    # A forward decree anchor → a decree power IS granted; guard stands down.
    if _clause_has_decree_anchor(clause_text):
        return False
    return True


#: Negative-reservation connective heads. A ``jollei`` / ``ellei`` ("unless") that
#: heads the clause's ``säädetä`` connegative marks a negative RESERVATION. With a
#: forward decree anchor (``asetuksella``) it IS a grant (``jollei asetuksella
#: toisin säädetä``); WITHOUT one it is a cross-/back-reference reservation
#: (``jollei muualla laissa toisin säädetä`` / ``jollei tässä asetuksessa toisin
#: säädetä``) that does NOT grant a new decree power.
_NEGATIVE_RESERVATION_HEADS: frozenset[str] = frozenset({"jollei", "ellei"})


#: Commencement / entry-into-force noun PREFIXES. A ``voimaantulo`` /
#: ``voimaanpano`` noun (in any case: ``voimaantulosta`` / ``voimaanpanosta`` /
#: ``voimaantulopäivästä`` …) GOVERNING the ``säätää`` family marks the standard
#: commencement section ``Tämän lain voimaantulosta säädetään … asetuksella`` —
#: "the entry into force of this Act is provided by decree". Production A FILTERS
#: this exact shape (``_PAT_NEGATIVE`` ``fi_delegation_commencement_reference_
#: filtered`` = ``voimaan(tulosta|panosta)\s+säädetään``); it is a recurring,
#: one-per-statute clause that is not a substantive subordinate-rule-making
#: delegation. CLOSED prefixes (so ``voimaansaattamisesta`` — bringing OTHER
#: regulations into force, which A does NOT filter and keeps as a grant — is
#: DELIBERATELY excluded; only the act's OWN commencement is filtered).
_COMMENCEMENT_NOUN_PREFIXES: tuple[str, ...] = ("voimaantulo", "voimaanpano")


def _is_commencement_clause(
    tokens: tuple[Token, ...],
    inst_idx: int,
    verb_idx: int,
) -> bool:
    """Guard (commencement): ``voimaantulosta säädetään … asetuksella``, not a grant.

    The standard commencement section ``Tämän lain voimaantulosta säädetään
    [valtioneuvoston / tasavallan presidentin] asetuksella`` ("the entry into force
    of this Act is provided by decree") appears in almost every statute. Production
    A FILTERS it (``_PAT_NEGATIVE`` ``fi_delegation_commencement_reference_
    filtered`` / ``fi_delegation_commencement_decree_filtered``); the canonical
    parser must too, or the flip would inflate StatuteGraph forward grants by ~1 FP
    per statute's commencement clause. Mirrors A's exact filter: fires only when a
    commencement noun (``voimaantulo*`` / ``voimaanpano*``) immediately PRECEDES a
    ``säätää``-family power verb (``säädetään`` / ``säätää`` / ``säädetä``) — the
    ``[commencement-noun] säädetään`` government A's ``voimaan(tulosta|panosta)\\s+
    säädetään`` regex keys on. ``voimaansaattamisesta`` (bringing OTHER regulations
    into force) is DELIBERATELY excluded (A keeps it as a grant), as is any
    NON-``säätää`` verb. Scoped to the ``asetus`` instrument (the commencement
    clause's decree); a ``määräys`` / ``ohje`` is not a commencement instrument.
    """
    instrument = _instrument_kind_for_surface(tokens[inst_idx].text)
    if instrument != INSTRUMENT_ASETUS:
        return False
    # Mirror A: the säätää-family verb whose IMMEDIATELY-preceding word token is a
    # commencement noun (the ``voimaantulosta säädetään`` government).
    verb_low = tokens[verb_idx].text.lower()
    if verb_low not in ("säädetään", "säätää", "säädetä"):
        return False
    prev = _prev_word_token(tokens, verb_idx)
    if prev is None:
        return False
    plow = prev.text.lower()
    return any(plow.startswith(pre) for pre in _COMMENCEMENT_NOUN_PREFIXES)


def _is_negative_reservation_without_anchor(
    verb_idx: int,
    tokens: tuple[Token, ...],
    clause_text: str,
) -> bool:
    """Guard 5: a ``säädetä`` connegative reservation lacking a decree anchor.

    ``säädetä`` (the connegative of ``säätää``) marks the negative reservation
    ``jollei … toisin säädetä``. Production A (``_PAT_BARE_ASETUS``) treats ONLY the
    decree-anchored form ``asetuksella … toisin säädetä`` as a grant; the bare
    forms ``jollei muualla laissa toisin säädetä`` (a back-reference to other law)
    and ``jollei tässä asetuksessa toisin säädetä`` (a self-reference) /
    ``ministeriön asetuksen liitteen … toisin säädetä`` (a cross-reference to an
    existing decree) are NOT decree grants. Fires only when the matched power verb
    is the ``säädetä`` connegative and NO forward decree anchor (``asetuksella`` /
    ``asetuksen … nojalla``) sits in the clause — so the genuine ``jollei
    asetuksella toisin säädetä`` reservation grant A catches is preserved.
    """
    if tokens[verb_idx].text.lower() != "säädetä":
        return False
    if _clause_has_decree_anchor(clause_text):
        return False
    return True


# ---------------------------------------------------------------------------
# AGENCY-family guards 6–10 (court power / penal clause / single-case order /
# appeal reference / bylaw-provided norm). Each fires ONLY on a ``määräys`` /
# ``ohje`` / ``päätös`` instrument (the AGENCY family) and never on an ``asetus``
# instrument or a forward decree anchor — so the decree grant lanes are untouched.
# ---------------------------------------------------------------------------

#: A genuine rule-MAKING object cue: a quantifier (``tarkempia`` / ``tarkemmat`` /
#: ``yleisiä`` / ``teknisiä`` / ``tarvittavat``) immediately heading the granted
#: ``määräyksiä`` / ``ohjeita`` object. Its presence is a strong genuine-grant
#: signal that makes the court / single-case / bylaw guards STAND DOWN (a court
#: ``antaa tarkempia määräyksiä [aiheesta]`` would be a rule-making delegation, not
#: an in-case order). CLOSED.
_RULEMAKING_QUANTIFIERS: frozenset[str] = frozenset(
    {
        "tarkempia",
        "tarkemmat",
        "tarkempaa",
        "yleisiä",
        "teknisiä",
        "hallinnollisia",
        "tarvittavat",
        "tarvittavia",
    }
)


def _clause_has_court_issuer(words: list[Token]) -> bool:
    """True iff a NOMINATIVE court issuer (``tuomioistuin`` / ``…oikeus`` family)
    heads the clause subject — the signal of an adjudicative power."""
    for w in words:
        low = w.text.lower()
        if low in _COURT_ISSUER_SURFACES:
            return True
        # the ``-oikeus`` court family in the nominative (``hovioikeus`` /
        # ``markkinaoikeus`` / ``vakuutusoikeus``); EXCLUDE inflected appeal targets
        # (``hallinto-oikeuteen`` / ``oikeuttaan``) — only the bare nominative head.
        if low.endswith("oikeus") and low not in ("oikeus",):
            return True
    return False


def _is_court_power(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    verb_idx: int,
) -> bool:
    """Guard 6: an in-case ADJUDICATIVE power, not a rule-making delegation.

    ``tuomioistuin voi … määrätä, ettei päätöstä saa panna täytäntöön`` /
    ``vakuutusoikeus voi … poistaa päätöksen ja määrätä asian uudelleen
    käsiteltäväksi`` — a court exercises a power over a CASE (issue / annul a
    decision, refer back). The instrument noun is the adjudicated object, never a
    delegated general rule. Fires only when ALL hold:

      * the triggering instrument is a ``päätös`` / ``määräys`` (the AGENCY family
        a court touches — never ``asetus``/``ohje``);
      * a NOMINATIVE court issuer heads the clause subject; and
      * the matched power verb is an adjudicative verb; and
      * NO rule-making quantifier heads the object (a hypothetical court ``antaa
        tarkempia määräyksiä`` would be rule-making — stand down) and NO forward
        decree anchor binds.
    """
    instrument = _instrument_kind_for_surface(tokens[inst_idx].text)
    if instrument not in (INSTRUMENT_MAARAYS, INSTRUMENT_PAATOS):
        return False
    if tokens[verb_idx].text.lower() not in _ADJUDICATIVE_VERBS:
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    if not _clause_has_court_issuer(words):
        return False
    if any(w.text.lower() in _RULEMAKING_QUANTIFIERS for w in words):
        return False
    return True


def _is_penal_clause_reference(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
) -> bool:
    """Guard 7: a penal OFFENCE clause referencing a norm, not a grant.

    ``Joka tahallaan … rikkoo … määräyksiä …, on tuomittava … sakkoon tai
    vankeuteen`` — the relative ``Joka`` ("whoever") heads an offence definition;
    any ``määräys`` it names is the norm VIOLATED, never a delegated rule-making
    power. Fires only when BOTH hold:

      * a clause word is the relative pronoun ``joka`` (``Joka …`` offence head);
        and
      * a closed penal predicate surface (``tuomittava`` / ``rangaistaan`` /
        ``sakkoon`` / ``vankeuteen`` …) sits in the clause.
    """
    words = _word_tokens(tokens, clause_lo, clause_hi)
    lowered = [w.text.lower() for w in words]
    if "joka" not in lowered:
        return False
    return any(low in _PENAL_PREDICATE_SURFACES for low in lowered)


def _is_cause_to_suspect_reference(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    verb_idx: int,
) -> bool:
    """Guard (idiom): ``antaa aiheen epäillä …``, an idiom — not a grant.

    ``… ei ole … osoittanut sellaista yleistä piittaamattomuutta säännöksistä tai
    määräyksistä, että se antaa aiheen epäillä …`` — the verb ``antaa`` heads the
    fixed idiom ``antaa aihe(en) [epäillä]`` ("gives [reasonable] cause [to
    suspect]"); the ``määräyksistä`` is the ELATIVE norm referenced (what the
    person was indifferent ABOUT), never a delegated rule-making instrument. The
    bare instrument-noun + power-verb co-occurrence test mints it because ``antaa``
    is in :data:`_POWER_VERBS` and ``määräyksistä`` is an instrument noun. Fires
    only when ALL hold:

      * the triggering instrument is a ``määräys`` / ``ohje`` (the AGENCY family;
        never ``asetus``); and
      * the matched power verb is ``antaa`` / ``antavat`` immediately FOLLOWED by
        ``aiheen`` / ``aihetta`` (the ``antaa aiheen`` idiom head); and
      * NO rule-making quantifier (``tarkempia`` / ``yleisiä`` …) heads the object
        (a genuine ``antaa tarkempia määräyksiä`` grant survives).

    Witnessed 2009/1194 §105, §149.
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) not in (
        INSTRUMENT_MAARAYS,
        INSTRUMENT_OHJE,
    ):
        return False
    if tokens[verb_idx].text.lower() not in ("antaa", "antavat"):
        return False
    nxt = _next_word_token(tokens, verb_idx)
    if nxt is None or nxt.text.lower() not in ("aiheen", "aihetta"):
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    if any(w.text.lower() in _RULEMAKING_QUANTIFIERS for w in words):
        return False
    return True


#: ``jättää`` family heads of the norm-VIOLATION idiom ``jättää noudattamatta …
#: määräyksiä`` ("fails to comply with … orders"). The instrument is the norm
#: VIOLATED, not a delegated rule-making power. CLOSED.
_NONCOMPLIANCE_VERB_SURFACES: frozenset[str] = frozenset(
    {"jättää", "jättävät", "jätti", "jättänyt"}
)


def _is_noncompliance_reference(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
) -> bool:
    """Guard (idiom): ``jättää noudattamatta … määräyksiä``, a violation — not a grant.

    ``Jos … luvan haltija jättää noudattamatta … hyväksynnän ehtoja tai muita
    määräyksiä …`` — the norm-violation idiom ``jättää noudattamatta [X]`` ("fails
    to comply with [X]"); the ``määräyksiä`` is the norm being VIOLATED, never a
    delegated rule-making power. A sibling of the penal guard (guard 7) but with no
    ``Joka`` / penal predicate, so guard 7 misses it. The clause's matched power
    verb is often an unrelated ``annettava`` (from the section heading
    ``Organisaatiolle annettava huomautus``), so this guard keys on the idiom
    DIRECTLY, not on the matched verb. Fires only when ALL hold:

      * the triggering instrument is a ``määräys`` / ``ohje`` (never ``asetus``);
        and
      * a ``noudattamatta`` token sits in the clause governed by a ``jättää``-family
        verb preceding it; and
      * NO rule-making quantifier heads the object (a genuine grant survives).

    Witnessed 2009/1194 §153.
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) not in (
        INSTRUMENT_MAARAYS,
        INSTRUMENT_OHJE,
    ):
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    lowered = [w.text.lower() for w in words]
    if "noudattamatta" not in lowered:
        return False
    nc_pos = lowered.index("noudattamatta")
    has_jattaa_before = any(
        lowered[k] in _NONCOMPLIANCE_VERB_SURFACES for k in range(nc_pos)
    )
    if not has_jattaa_before:
        return False
    if any(low in _RULEMAKING_QUANTIFIERS for low in lowered):
        return False
    return True


def _is_single_case_order(
    tokens: tuple[Token, ...],
    inst_idx: int,
) -> bool:
    """Guard 8: a single-case order BACK-reference, not a rule-making grant.

    ``Valituskirjelmä voidaan antaa myös määräyksen antaneelle …`` — the genitive
    ``määräyksen`` modifies the participle ``antaneelle`` ("to the one who ISSUED
    the order"), a back-reference to a one-off order already given; the granted
    object is ``Valituskirjelmä``, not the ``määräys``. Fires only when the
    triggering instrument is the genitive ``määräyksen`` / ``päätöksen`` /
    ``ohjeen`` immediately followed by a participle of ``antaa`` (``antaneelle`` /
    ``antanut`` / ``antaman`` / ``antama``).
    """
    if tokens[inst_idx].text.lower() not in (
        "määräyksen",
        "päätöksen",
        "ohjeen",
    ):
        return False
    nxt = _next_word_token(tokens, inst_idx)
    if nxt is None:
        return False
    low = nxt.text.lower()
    return low.startswith("antan") or low.startswith("antam")


def _is_appeal_reference(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
) -> bool:
    """Guard 9: an appeal cross-reference, not a rule-making grant.

    ``saa(vat) valittaa päätöksestä … korkeimpaan hallinto-oikeuteen`` / ``on
    oikeus valittaa viraston päätöksestä`` cite a RIGHT TO APPEAL an existing
    decision to a court; the ``päätös`` is the appealed object, never a delegated
    rule-making instrument. Fires only when the triggering instrument is a
    ``päätös`` AND an appeal verb (``valittaa`` / ``valitetaan`` / ``hakea``
    [muutosta]) sits in the clause.
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) != INSTRUMENT_PAATOS:
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    return any(w.text.lower() in _APPEAL_VERB_SURFACES for w in words)


def _is_bylaw_provided_norm(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    clause_text: str,
) -> bool:
    """Guard 10: a norm PROVIDED IN an internal bylaw, not a decree/agency grant.

    ``Tarkemmat määräykset … annetaan työjärjestyksessä`` / ``voidaan antaa muita …
    määräyksiä [oikeuskanslerin vahvistamassa] työjärjestyksessä`` / ``annetaan …
    ohjesäännössä`` — the instrument-bearing LOCUS is an internal bylaw
    (työjärjestys / ohjesääntö / johtosääntö / taloussääntö), so the ``määräys`` /
    ``ohje`` is the bylaw's OWN content, not a statutory decree or an agency rule
    delegated by this law. Fires only when ALL hold:

      * the triggering instrument is a ``määräys`` / ``ohje`` (never ``asetus``);
        and
      * a closed bylaw inessive/adessive noun (``työjärjestyksessä`` /
        ``ohjesäännössä`` / ``johtosäännössä`` / ``taloussäännössä`` …) sits in the
        clause; and
      * NO forward decree anchor binds (``asetuksella`` — a decree grant survives).
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) not in (
        INSTRUMENT_MAARAYS,
        INSTRUMENT_OHJE,
    ):
        return False
    if _clause_has_decree_anchor(clause_text):
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    for w in words:
        low = w.text.lower()
        if any(low.endswith(suf) for suf in _BYLAW_INSTRUMENT_INESSIVE_SURFACES):
            return True
    return False


def _is_permit_condition(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    clause_text: str,
) -> bool:
    """Guard (single-case): a permit CONDITION attached to a one-off authorization.

    ``lupa voidaan antaa määräajaksi ja siihen on liitettävä … tarpeelliset
    määräykset`` ("the necessary CONDITIONS must be ATTACHED to the permit") — the
    ``määräykset`` are the individual conditions of a one-off permit, NOT a
    delegated power to MAKE general subordinate rules. The clause's first power verb
    is the early ``antaa`` (granting the PERMIT), so guard 3's necessitive check
    (which keys on the matched verb) misses it; this guard keys on the structure
    directly. Fires only when ALL hold:

      * the triggering instrument is a ``määräys`` object (never ``asetus``); and
      * a ``liitettävä`` ("must be attached") token precedes the instrument in the
        clause, governed by a leading ``on`` auxiliary (the ``on liitettävä``
        necessitive frame); and
      * NO forward decree anchor binds.
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) != INSTRUMENT_MAARAYS:
        return False
    if _clause_has_decree_anchor(clause_text):
        return False
    inst_char = tokens[inst_idx].char_start
    words = _word_tokens(tokens, clause_lo, clause_hi)
    liit = next(
        (w for w in words if w.text.lower() == "liitettävä" and w.char_start < inst_char),
        None,
    )
    if liit is None:
        return False
    return any(
        w.text.lower() == "on" and w.char_start < liit.char_start for w in words
    )


def _is_published_norm_reference(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    clause_text: str,
) -> bool:
    """Guard (publishing): a clause regulating the PUBLICATION of norms.

    ``Viranomaisen määräykset julkaistaan … säädöskokoelmassa`` / ``voi … päättää,
    että … määräykset julkaistaan …`` / ``julkaisemismääräys annetaan … määräyksen
    … julkaisemisesta`` regulate WHERE existing norms are PUBLISHED, not the power
    to MAKE them. Fires only when ALL hold:

      * the triggering instrument is a ``määräys`` / ``ohje`` (never ``asetus``);
        and
      * a ``julkais-`` publishing verb (``julkaistaan`` / ``julkaiseminen`` /
        ``julkaisemisesta`` / ``julkaista``) sits in the clause; and
      * NO forward decree anchor binds.
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) not in (
        INSTRUMENT_MAARAYS,
        INSTRUMENT_OHJE,
    ):
        return False
    if _clause_has_decree_anchor(clause_text):
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    return any(
        w.text.lower().startswith(_PUBLISHING_VERB_PREFIX) for w in words
    )


def _is_single_case_direction(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
) -> bool:
    """Guard (single-case): a direction given IN A SINGLE CASE, not a general rule.

    ``antaa edustuston toimintaa yksittäisessä tapauksessa koskevia määräyksiä ja
    ohjeita`` directs an authority in a one-off case — not a delegated power to MAKE
    general subordinate rules. Fires only when ALL hold:

      * the triggering instrument is a ``määräys`` / ``ohje`` (never ``asetus``);
        and
      * the closed ``yksittäisessä tapauksessa`` marker sits over consecutive WORD
        tokens in the clause; and
      * NO rule-making quantifier (``yleisiä`` / ``tarkempia`` …) heads the object
        — ``voi antaa YLEISIÄ määräyksiä … ja päättää … yksittäisessä tapauksessa``
        is a genuine general grant (the single-case clause is a separate conjunct).
    """
    if _instrument_kind_for_surface(tokens[inst_idx].text) not in (
        INSTRUMENT_MAARAYS,
        INSTRUMENT_OHJE,
    ):
        return False
    words = _word_tokens(tokens, clause_lo, clause_hi)
    lowered = [w.text.lower() for w in words]
    has_marker = any(
        lowered[j : j + 2] == list(_SINGLE_CASE_MARKER)
        for j in range(len(lowered) - 1)
    )
    if not has_marker:
        return False
    if any(low in _RULEMAKING_QUANTIFIERS for low in lowered):
        return False
    return True


#: Generic ISSUER-HEAD suffixes (the morphological tail of an institutional
#: issuer surface the actor registry does not carry as a verbatim inflected
#: phrase). A ``word`` token whose lowercase form ENDS in one of these is an
#: institutional issuer head — the genitive ``…ministeriön`` / ``…presidentin`` /
#: the agency family (``…virasto`` / ``…keskus`` / ``…laitos`` …). This is the
#: token-native form of C's ``_HOLDER_RE`` fallback: it lets the canonical parser
#: BIND + CLASSIFY the issuer of an ``Opetusministeriön asetuksella säädetään``
#: grant whose exact inflected surface the registry lacks (the dominant reason
#: the OLD B residualized these as ``delegation_without_actor``).
_ISSUER_HEAD_SUFFIXES: tuple[str, ...] = (
    "ministeriön",
    "ministeriö",
    "presidentin",
    "presidentti",
    "virasto",
    "viraston",
    "keskus",
    "keskuksen",
    "laitos",
    "laitoksen",
    "hallinto",
    "valvonta",
    "lautakunta",
    "lautakunnan",
    "neuvosto",
    "neuvoston",
    "viranomainen",
    "valtioneuvosto",
    "valtioneuvoston",
)


def _is_issuer_head(tok: Token) -> bool:
    if tok.category != "word":
        return False
    low = tok.text.lower()
    return any(low.endswith(suf) for suf in _ISSUER_HEAD_SUFFIXES)


def _adjacent_issuer_before(
    tokens: tuple[Token, ...], clause_lo: int, inst_idx: int
) -> tuple[str, int | None, int | None]:
    """The issuer NP ending IMMEDIATELY before the instrument anchor, or none.

    Walks left from the anchor over whitespace, then takes the contiguous run of
    ``word`` tokens that form the genitive issuer surface (the actor-matcher span
    if it ends adjacent, else the issuer-head run). Returns ("", None, None) when
    no issuer is immediately adjacent — the asetus is then GENERIC / underspecified
    (old C ``adjacent_only`` rule).
    """
    # The token immediately before the anchor (skipping whitespace).
    j = inst_idx - 1
    while j >= clause_lo and tokens[j].category == "whitespace":
        j -= 1
    if j < clause_lo or tokens[j].category != "word":
        return "", None, None
    # Prefer a registry/role actor phrase whose END is exactly this adjacent word.
    matches = _ACTOR_MATCHER.find_in_window(tokens, clause_lo, inst_idx)
    adj_end = tokens[j].char_end
    adjacent = [m for m in matches if m.char_end == adj_end]
    if adjacent:
        chosen = max(adjacent, key=lambda m: m.char_start)
        return chosen.surface, chosen.char_start, chosen.char_end
    # Else: the adjacent word itself must be an issuer head (``…ministeriön`` /
    # ``valtioneuvoston`` / an agency head); absorb a leading ``tasavallan``.
    if not _is_issuer_head(tokens[j]):
        return "", None, None
    start_idx = j
    prev = _prev_word_token(tokens, j)
    if prev is not None and prev.text.lower() in ("tasavallan",):
        k = j - 1
        while k >= clause_lo and tokens[k].category != "word":
            k -= 1
        if k >= clause_lo:
            start_idx = k
    cs = tokens[start_idx].char_start
    ce = tokens[j].char_end
    return _surface_for(tokens, cs, ce), cs, ce


def _resolve_holder(
    tokens: tuple[Token, ...],
    clause_lo: int,
    clause_hi: int,
    inst_idx: int,
    *,
    adjacent_only: bool = False,
) -> tuple[str, int | None, int | None]:
    """Bind the authority-holder NP to the instrument anchor.

    Two tiers, in order:
      1. the shared token-native actor matcher (registry phrases UNION role
         actors) — the AUTHORITATIVE, vocabulary-controlled binding; then
      2. a generic ISSUER-HEAD fallback (a ``word`` token ending in an
         institutional suffix, :data:`_ISSUER_HEAD_SUFFIXES`) — the token-native
         form of C's ``_HOLDER_RE``, so an issuer the registry does not carry
         verbatim (``Opetusministeriön`` / ``tasavallan presidentin`` / a generic
         ``…virasto``) still BINDS rather than residualizing.

    Returns (surface, char_start, char_end) for the issuer nearest the instrument
    anchor. When NEITHER tier binds, the holder is UNDERSPECIFIED — ("", None,
    None) — NOT a decline (C's holder-never-absent rule).

    ``adjacent_only`` (the ASETUS instrumental shape ``[issuer-genitive]
    asetuksella``, ported from old C ``_holder_span_in_clause``): the genitive
    issuer of an ``asetuksella`` decree immediately PRECEDES the anchor (only
    whitespace between). When set, ONLY an issuer ending immediately before the
    anchor binds — a holder that merely sits NEAREST but across a coordinator
    (``annetaan asetuksella, ympäristöministeriön päätöksellä …``: the bare
    ``asetuksella`` is a GENERIC asetus, the ``ympäristöministeriön`` genitive
    binds ``päätöksellä``) does NOT bind the asetus, which stays underspecified.
    """
    inst_char = tokens[inst_idx].char_start
    if adjacent_only:
        return _adjacent_issuer_before(tokens, clause_lo, inst_idx)
    matches = _ACTOR_MATCHER.find_in_window(tokens, clause_lo, clause_hi)
    if matches:
        chosen = min(matches, key=lambda m: abs(m.char_start - inst_char))
        return chosen.surface, chosen.char_start, chosen.char_end

    # Tier 2: generic issuer-head fallback. Prefer the head nearest the anchor;
    # absorb an immediately-preceding ``tasavallan`` / genitive modifier word so
    # ``tasavallan presidentin`` binds as one surface.
    best: tuple[int, int] | None = None
    best_dist = 1 << 30
    for j in range(clause_lo, clause_hi):
        if not _is_issuer_head(tokens[j]):
            continue
        start_idx = j
        prev = _prev_word_token(tokens, j)
        if prev is not None and prev.text.lower() in ("tasavallan",):
            # find prev token index
            k = j - 1
            while k >= clause_lo and tokens[k].category != "word":
                k -= 1
            if k >= clause_lo:
                start_idx = k
        cs = tokens[start_idx].char_start
        ce = tokens[j].char_end
        dist = abs(cs - inst_char)
        if dist < best_dist:
            best_dist = dist
            best = (cs, ce)
    if best is not None:
        return _surface_for(tokens, best[0], best[1]), best[0], best[1]
    return "", None, None


def _surface_for(tokens: tuple[Token, ...], cs: int, ce: int) -> str:
    parts: list[str] = []
    for t in tokens:
        if t.char_start >= cs and t.char_end <= ce:
            parts.append(t.text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Authority-basis adjacency guard + amendment-interjection strip (ported VERBATIM
# from the old C ``delegation_parse._basis_span`` so the canonical basis lane does
# not REGRESS the long-range-false-basis / amending-id precision C already had).
# ---------------------------------------------------------------------------
_BASIS_TAIL_TOKEN = (
    r"(?:§|:n|momentin|momentti|momentissa|kohdan|kohta|kohdassa"
    r"|mukaisen|ja|sekä|tai|\d{1,4}|[a-zäö](?![\wäö])"
    r"|[\s.,:()/–-]++)"
)
#: The provision PATH (a ``(NUM/YEAR)`` id or a ``N §`` section) must DIRECTLY
#: precede the terminal, with ONLY provision-tail vocabulary in between — not
#: arbitrary prose. Possessive whitespace branch is perf-gate safe.
_BASIS_PATH_BEFORE_TERMINAL_RE = re.compile(
    r"(?:\(\d{1,5}\s*/\s*\d{2,4}\)|\b\d{1,4}\s*[a-zäö]?\s*§)"
    + _BASIS_TAIL_TOKEN
    + r"*\Z",
    re.IGNORECASE,
)
#: A ``, sellaisena kuin se on … (NNN/YYYY),`` amendment-version interjection: the
#: inner ids are the AMENDING acts (metadata), not the basis. Blanked (equal-length)
#: before the adjacency guard so the prose does not defeat it and the amending ids
#: are not bound.
_INTERJECTION_RE = re.compile(
    r",\s*sellaise\w*\s+kuin\s+(?:se|ne)\s+(?:on|ovat|oli|olivat)\b"
    r"[^(,]*(?:\([^)]*\)|\d{1,5}/\d{2,4})",
    re.IGNORECASE,
)


def _strip_amendment_interjections(window: str) -> str:
    """Blank every ``, sellaisena kuin … ,`` amendment-version interjection.

    Equal-length space replacement preserves window-local char offsets while
    removing the amending-act ids (metadata) from view. A cheap ``"sellaise"``
    prefilter skips the scan for the no-interjection common case.
    """
    if "sellaise" not in window:
        return window
    return _INTERJECTION_RE.sub(lambda m: " " * (m.end() - m.start()), window)


def _basis_for_clause(
    text: str, clause_char_start: int, clause_char_end: int
) -> tuple[int | None, int | None, tuple[str, ...]]:
    """Find a ``… nojalla`` / ``… mukaan`` provision-basis window in the clause.

    Token-native parsers still take the basis tail as TEXT into the references
    sub-grammar (``parse_body_provision_tail_spanned``) — the canonical reuse the
    verdict mandates. Returns (basis_start, basis_end, target_labels) or
    (None, None, ()). Conservative: only fires when a ``(NUM/YEAR)`` id or ``N §``
    path directly precedes the terminal inside this clause.
    """
    clause = text[clause_char_start:clause_char_end]
    low = clause.lower()
    term_pos = -1
    term_len = 0
    for cue in ("nojalla", "mukaan"):
        p = low.find(cue)
        if p != -1 and (term_pos == -1 or p < term_pos):
            term_pos = p
            term_len = len(cue)
    if term_pos == -1:
        return None, None, ()
    window = clause[:term_pos]
    # Blank ``, sellaisena kuin se on laissa NNN/YYYY,`` amendment interjections
    # (inner ids are amending acts, not the basis) — ported from old C.
    window = _strip_amendment_interjections(window)
    # require a provision-id signal in the window
    if not re.search(r"\(\d{1,5}\s*/\s*\d{2,4}\)|\b\d{1,4}[a-z]?\s?§", window):
        return None, None, ()
    # ADJACENCY GUARD (ported from old C ``_basis_span`` — preserved across the
    # cutover): the provision PATH must DIRECTLY precede the terminal. Rejects the
    # long-range FALSE basis (an unrelated earlier ``(NUM/YEAR) §`` far left of an
    # anaphoric bare ``sen nojalla`` separated by prose).
    if not _BASIS_PATH_BEFORE_TERMINAL_RE.search(window):
        return None, None, ()
    # Feed the references sub-grammar the tail AFTER each ``(NUM/YEAR)`` id (so it
    # sees ``N §:n``, not the act-name prose before the id), distributing one
    # ``nojalla`` over coordinated conjuncts — the same conjunct-distribution C's
    # ``_basis_span`` uses. A window with no id is parsed whole (a bare ``N §:n``).
    id_matches = list(re.finditer(r"\(\d{1,5}\s*/\s*\d{2,4}\)\s*", window))
    labels: list[str] = []
    if id_matches:
        for i, idm in enumerate(id_matches):
            tail_end = (
                id_matches[i + 1].start() if i + 1 < len(id_matches) else len(window)
            )
            tail = window[idm.end() : tail_end]
            parsed = parse_body_provision_tail_spanned(tail)
            labels.extend(t.section_label for t in parsed.targets if t.section_label)
    else:
        parsed = parse_body_provision_tail_spanned(window)
        labels.extend(t.section_label for t in parsed.targets if t.section_label)
    seen: set[str] = set()
    targets = tuple(x for x in labels if not (x in seen or seen.add(x)))
    if not targets:
        return None, None, ()
    return (
        clause_char_start,
        clause_char_start + term_pos + term_len,
        targets,
    )


# ---------------------------------------------------------------------------
# The canonical scan
# ---------------------------------------------------------------------------


def _scan_tape(tape: TokenTape, source_text: str) -> DelegationGrantScan:
    tokens = tape.tokens
    grants: list[DelegationGrant] = []
    residuals: list[GrantResidual] = []
    consumed_instrument_idx: set[int] = set()

    for inst_idx, inst_tok in enumerate(tokens):
        if inst_idx in consumed_instrument_idx:
            continue
        if inst_tok.category != "word" or inst_tok.text.lower() not in _INSTRUMENT_NOUNS:
            # Case-insensitive: a SENTENCE-INITIAL ``Asetuksella säädetään`` is a
            # genuine bare grant; the old token-verbatim membership test (B) missed
            # the capitalized form, the dominant remaining gap. The instrument
            # vocabulary is a closed lowercase set; the surface case is irrelevant.
            continue
        instrument = _instrument_kind_for_surface(inst_tok.text)
        if instrument is None:
            continue  # impossible by construction

        # --- guards: reject grant-SHAPED-but-not-a-grant instrument mentions ---
        prev = _prev_word_token(tokens, inst_idx)
        nxt = _next_word_token(tokens, inst_idx)

        # self-/cross-reference demonstrative: ``tällä asetuksella`` / ``tämän
        # asetuksen`` — the decree's OWN power or an existing instrument.
        if prev is not None and prev.text.lower() in _DEMONSTRATIVES:
            kind = (
                "self_reference_instrument"
                if prev.text.lower() in ("tällä", "tämän", "tässä")
                else "cross_reference_instrument"
            )
            residuals.append(
                _residual(
                    kind, tokens, inst_idx, instrument, prev.text, source_text
                )
            )
            continue

        # postposition complement: ``päätöksen mukaisesti`` / ``… nojalla`` — the
        # noun is the complement of the postposition, not a delegated instrument.
        if nxt is not None and nxt.text.lower() in _POSTPOSITIONS:
            residuals.append(
                _residual(
                    "postposition_complement",
                    tokens,
                    inst_idx,
                    instrument,
                    nxt.text,
                    source_text,
                )
            )
            continue

        # section-path / statute-id cross-reference: ``asetuksen 34 §:n …
        # säädetään`` / ``annetun asetuksen (575/1988) 1―22 §:ssä säädetään`` cite
        # an EXISTING instrument by its section/id (the dominant old-B FALSE
        # POSITIVE). This applies ONLY to a GENITIVE instrument surface (``…n``:
        # asetuksen / määräyksen / päätöksen / ohjeen) — the form that names an
        # existing instrument. An OBJECT / instrumental form (``määräyksiä 14
        # §:ssä …`` / ``ohjeita 8 §:ssä …`` / ``asetuksella``) takes the section
        # as its SUBJECT (the order is ABOUT section N), so it is a genuine grant,
        # never an existing-instrument cross-reference.
        _is_genitive_instrument = inst_tok.text.lower() in (
            "asetuksen",
            "määräyksen",
            "päätöksen",
            "ohjeen",
        )
        if _is_genitive_instrument and _next_token_is_section_path(tokens, inst_idx):
            residuals.append(
                _residual(
                    "cross_reference_instrument",
                    tokens,
                    inst_idx,
                    instrument,
                    "<section-path>",
                    source_text,
                )
            )
            continue

        clause_lo, clause_hi = _clause_token_bounds(tokens, inst_idx)
        if clause_lo >= clause_hi:
            continue
        clause_char_start = tokens[clause_lo].char_start
        clause_char_end = tokens[clause_hi - 1].char_end
        clause_text = source_text[clause_char_start:clause_char_end]

        verb_idx = _first_power_verb_index(tokens, clause_lo, clause_hi)
        if verb_idx is None:
            # an instrument noun with no delegation verb is not a grant (a bare
            # cross-reference to ``asetuksen 3 §``). Typed residue, never a guess.
            residuals.append(
                _residual(
                    "instrument_without_power_verb",
                    tokens,
                    inst_idx,
                    instrument,
                    "",
                    source_text,
                )
            )
            continue

        # --- over-recognition guards (CLOSED): grant-SHAPED but NOT a grant ---
        # Guard 1 — anaphoric BACK-reference: ``siten kuin … säädetään`` /
        # ``mitä … säädetään`` cites where a matter is ALREADY provided for; not a
        # forward grant (unless a decree anchor ``asetuksella`` also binds).
        if _is_anaphoric_reference_clause(
            tokens, clause_lo, clause_hi, verb_idx, clause_text
        ):
            residuals.append(
                _residual(
                    "anaphoric_reference",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # Guard 2 — subject-NP collision: ``Päätös annetaan tiedoksi …`` — the
        # instrument noun is the clause SUBJECT of a passive predicate, not the
        # delegated object.
        if _is_subject_np_collision(tokens, clause_lo, clause_hi, inst_idx):
            residuals.append(
                _residual(
                    "subject_np_collision",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # Guard 3 — procedural-duty object: ``hakemukseen on annettava päätös`` /
        # ``Luvassa on annettava määräykset`` — the instrument is the OBJECT of a
        # one-off necessitive duty to ISSUE it, not a delegated rule-MAKING power.
        if _is_procedural_duty_object(
            tokens, clause_lo, clause_hi, inst_idx, verb_idx, clause_text
        ):
            residuals.append(
                _residual(
                    "procedural_duty_object",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # Guard 4 — decision-issuance object: ``hakemukseen annetaan kielteinen
        # päätös`` / ``päätös voidaan antaa sen jälkeen`` — a ``päätös`` OBJECT
        # issued in a single case by passive-present ``annetaan`` / modal ``voidaan
        # antaa``, NOT a delegated rule-MAKING power (the passive/modal counterpart
        # of guard 3's necessitive duty).
        if _is_decision_issuance_object(tokens, inst_idx, verb_idx, clause_text):
            residuals.append(
                _residual(
                    "decision_issuance_object",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # Guard 5 — negative reservation without a decree anchor: a ``säädetä``
        # connegative ``jollei muualla laissa toisin säädetä`` / ``jollei tässä
        # asetuksessa toisin säädetä`` cites/reserves to OTHER law, not a new decree
        # grant. The anchored ``jollei asetuksella toisin säädetä`` reservation IS a
        # grant (anchor present → guard stands down), matching production A.
        if _is_negative_reservation_without_anchor(verb_idx, tokens, clause_text):
            residuals.append(
                _residual(
                    "negative_reservation",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # Guard (commencement) — ``Tämän lain voimaantulosta säädetään …
        # asetuksella``: the standard commencement-by-decree section, NOT a
        # substantive rule-making delegation. Production A FILTERS it; the canonical
        # must too, or the flip inflates StatuteGraph forward grants by ~1 FP per
        # statute. Mirrors A's exact ``voimaan(tulosta|panosta) säädetään`` filter
        # (so ``voimaansaattamisesta säädetään`` — which A KEEPS — survives).
        if _is_commencement_clause(tokens, inst_idx, verb_idx):
            residuals.append(
                _residual(
                    "commencement_clause",
                    tokens,
                    inst_idx,
                    instrument,
                    tokens[verb_idx].text,
                    source_text,
                )
            )
            continue
        # --- AGENCY-family precision guards (6–10) — reject the ~half of
        # määräys/ohje/päätös AGENCY mints that are NOT a rule-making delegation.
        # Guard 6 — court power: ``tuomioistuin voi … määrätä …`` (in-case
        # adjudication, not a delegated general rule).
        if _is_court_power(tokens, clause_lo, clause_hi, inst_idx, verb_idx):
            residuals.append(
                _residual(
                    "court_power",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard 7 — penal clause reference: ``Joka … määräyksen vastaisesti …, on
        # tuomittava …`` (offence definition referencing a norm, not a grant).
        if _is_penal_clause_reference(tokens, clause_lo, clause_hi):
            residuals.append(
                _residual(
                    "penal_clause_reference",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard (idiom) — cause-to-suspect: ``antaa aiheen epäillä … määräyksistä``
        # (the fixed ``antaa aiheen`` idiom; the elative ``määräyksistä`` is the norm
        # referenced, not a delegated rule-making power).
        if _is_cause_to_suspect_reference(
            tokens, clause_lo, clause_hi, inst_idx, verb_idx
        ):
            residuals.append(
                _residual(
                    "cause_to_suspect_reference",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard (idiom) — non-compliance: ``jättää noudattamatta … määräyksiä`` (the
        # norm-violation idiom; the määräykset are VIOLATED, not delegated). Sibling
        # of the penal guard with no ``Joka`` / penal predicate.
        if _is_noncompliance_reference(tokens, clause_lo, clause_hi, inst_idx):
            residuals.append(
                _residual(
                    "noncompliance_reference",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard 8 — single-case order reference: ``… määräyksen antaneelle …`` (a
        # back-reference to a one-off order already given, not a grant).
        if _is_single_case_order(tokens, inst_idx):
            residuals.append(
                _residual(
                    "single_case_order",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard 9 — appeal reference: ``saa valittaa päätöksestä … oikeuteen`` (a
        # right to appeal an existing decision, not a rule-making instrument).
        if _is_appeal_reference(tokens, clause_lo, clause_hi, inst_idx):
            residuals.append(
                _residual(
                    "appeal_reference",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard 10 — bylaw-provided norm: ``annetaan työjärjestyksessä`` /
        # ``ohjesäännössä`` (the norm is in an internal bylaw, not a decree/agency
        # rule delegated by this law).
        if _is_bylaw_provided_norm(
            tokens, clause_lo, clause_hi, inst_idx, clause_text
        ):
            residuals.append(
                _residual(
                    "bylaw_provided_norm",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard (permit condition) — ``lupa … on liitettävä … määräykset``: the
        # määräykset are the one-off permit's CONDITIONS, not a rule-making grant
        # (the clause's first power verb is the permit's own ``antaa``).
        if _is_permit_condition(
            tokens, clause_lo, clause_hi, inst_idx, clause_text
        ):
            residuals.append(
                _residual(
                    "single_case_order",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard (publishing) — ``määräykset julkaistaan säädöskokoelmassa``: the
        # clause regulates WHERE existing norms are PUBLISHED, not the power to make
        # them.
        if _is_published_norm_reference(
            tokens, clause_lo, clause_hi, inst_idx, clause_text
        ):
            residuals.append(
                _residual(
                    "published_norm_reference",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        # Guard (single-case) — ``antaa … yksittäisessä tapauksessa … määräyksiä``:
        # a one-off direction in a single case, not a general rule (stands down on a
        # rule-making quantifier ``yleisiä`` / ``tarkempia``).
        if _is_single_case_direction(tokens, clause_lo, clause_hi, inst_idx):
            residuals.append(
                _residual(
                    "single_case_direction",
                    tokens, inst_idx, instrument, tokens[verb_idx].text, source_text,
                )
            )
            continue
        consumed_instrument_idx.add(inst_idx)

        binding = "may" if _clause_has_may_modal(tokens, clause_lo, clause_hi) else "must"

        holder_surface, h_start, h_end = _resolve_holder(
            tokens,
            clause_lo,
            clause_hi,
            inst_idx,
            # The ASETUS instrumental issuer is the genitive immediately preceding
            # the anchor (``[issuer] asetuksella``); a non-adjacent nearest actor
            # across a coordinator binds a DIFFERENT instrument (old C rule).
            adjacent_only=(instrument == INSTRUMENT_ASETUS),
        )
        underspec = h_start is None
        kind = _classify_kind(holder_surface, instrument)

        verb_tok = tokens[verb_idx]
        subject_after = max(inst_idx, verb_idx) + 1
        subj = _capture_subject_span(tokens, subject_after, clause_hi)

        b_start, b_end, basis_targets = _basis_for_clause(
            source_text, clause_char_start, clause_char_end
        )

        grants.append(
            DelegationGrant(
                kind=kind,
                instrument=instrument,
                binding_strength=binding,
                cue=verb_tok.text,
                cue_start=verb_tok.char_start,
                cue_end=verb_tok.char_end,
                instrument_surface=inst_tok.text,
                instrument_start=inst_tok.char_start,
                instrument_end=inst_tok.char_end,
                holder_surface=holder_surface,
                holder_start=h_start,
                holder_end=h_end,
                holder_underspecified=underspec,
                frame_start=clause_char_start,
                frame_end=clause_char_end,
                subject_start=subj[0] if subj else None,
                subject_end=subj[1] if subj else None,
                basis_start=b_start,
                basis_end=b_end,
                basis_targets=basis_targets,
                rule_id=_RULE_ID,
            )
        )
        _ = clause_text  # owned via frame span; kept for clarity / future totality

    # A demonstrative / cross-reference instrument mention can sit INSIDE a clause
    # that ALSO yields a genuine grant (``antaa ohjeita tämän asetuksen
    # soveltamisesta`` — the ``tämän asetuksen`` is the SUBJECT of the granted
    # ``ohje``, not a separate declined instrument). Such a mention is owned by the
    # grant's frame span; emitting it ALSO as a residual would double-own the span
    # (totality violation). Drop every residual whose span overlaps a grant frame.
    grant_spans = [(g.frame_start, g.frame_end) for g in grants]
    kept_residuals = [
        r
        for r in residuals
        if not any(r.char_start < ge and gs < r.char_end for gs, ge in grant_spans)
    ]
    return DelegationGrantScan(grants=tuple(grants), residuals=tuple(kept_residuals))


def _residual(
    kind: str,
    tokens: tuple[Token, ...],
    inst_idx: int,
    instrument: str,
    trigger: str,
    source_text: str,
) -> GrantResidual:
    lo, hi = _clause_token_bounds(tokens, inst_idx)
    if lo >= hi:
        cs, ce = tokens[inst_idx].char_start, tokens[inst_idx].char_end
    else:
        cs, ce = tokens[lo].char_start, tokens[hi - 1].char_end
    surface = source_text[cs:ce]
    return GrantResidual(
        kind=kind,
        char_start=cs,
        char_end=ce,
        surface_text=surface,
        reason=(
            f"instrument {instrument!r} in a {kind} shape "
            f"(trigger={trigger!r}); no grant emitted: {surface!r}"
        ),
    )


def parse_delegation_grants(
    tape_or_text: TokenTape | str, source_text: str | None = None
) -> DelegationGrantScan:
    """Parse forward delegation GRANTS over a :class:`TokenTape` (canonical).

    Token-native. Emits one :class:`DelegationGrant` per recognized forward grant
    (whole-frame span, instrument anchor, holder span or typed underspecification,
    cue span, instrument kind, issuer class, binding strength, basis) and a typed
    :class:`GrantResidual` for every grant-SHAPED-but-not-a-grant instrument
    mention (self-/cross-reference, postposition complement, instrument without a
    power verb). Nothing grant-shaped is ever silently dropped.

    Accepts a :class:`TokenTape` (the lens path) or a raw ``str`` (tokenized
    internally — convenient for tests / the freeze probe).
    """
    if isinstance(tape_or_text, str):
        from lawvm.finland.legal_surface.tokenize import build_token_tape

        text = tape_or_text
        tape = build_token_tape("delegation_canonical", text)
    else:
        tape = tape_or_text
        if source_text is None:
            source_text = "".join(t.text for t in tape.tokens)
        text = source_text
    return _scan_tape(tape, text)


def assert_total_ownership(scan: DelegationGrantScan, text: str) -> None:
    """Checkable postcondition: each grant's owned spans + residuals partition.

    For each grant, the union of its cue / holder / instrument / basis spans is
    contained in its frame span; the frame spans and the residual spans together
    must leave no grant-shaped instrument mention unaccounted for. We verify the
    weaker, sound invariant the SHADOW stage needs: every emitted owned span lies
    inside ``[0, len(text))`` and inside its frame, and no grant span overlaps a
    residual span (a clause is either a grant OR a declined residue, never both).
    Raises ``AssertionError`` on violation.

    (Full char-partition totality — the C ``assert_total_ownership`` form — is a
    later-stage gate once the canonical parser is the sole producer over a fixed
    segment; at the shadow stage the grant span IS the owned partition.)
    """
    n = len(text)
    for g in scan.grants:
        for s, e in (
            (g.cue_start, g.cue_end),
            (g.instrument_start, g.instrument_end),
            (g.frame_start, g.frame_end),
        ):
            if not (0 <= s <= e <= n):
                raise AssertionError(
                    f"span out of bounds [{s},{e}) for text len {n}: {g!r}"
                )
        if g.holder_start is not None and g.holder_end is not None:
            if not (0 <= g.holder_start <= g.holder_end <= n):
                raise AssertionError(f"holder span out of bounds: {g!r}")
        # owned spans must lie within the frame
        for s, e in (
            (g.cue_start, g.cue_end),
            (g.instrument_start, g.instrument_end),
        ):
            if not (g.frame_start <= s and e <= g.frame_end):
                raise AssertionError(
                    f"owned span [{s},{e}) escapes frame "
                    f"[{g.frame_start},{g.frame_end}): {g!r}"
                )
    grant_spans = [(g.frame_start, g.frame_end) for g in scan.grants]
    for r in scan.residuals:
        for gs, ge in grant_spans:
            if r.char_start < ge and gs < r.char_end:
                raise AssertionError(
                    f"residual [{r.char_start},{r.char_end}) overlaps grant "
                    f"frame [{gs},{ge}): a clause cannot be both a grant and a "
                    f"declined residue: {r!r}"
                )


def projection_grant_keys(scan: DelegationGrantScan) -> set[str]:
    """Census-comparable forward-grant key set, ``grant:{kind}:{instrument}``.

    Same identity form the production oracle and the legacy C census use, so the
    canonical projection is directly comparable in the differential gate.
    """
    return {f"grant:{g.kind}:{g.instrument}" for g in scan.grants}
