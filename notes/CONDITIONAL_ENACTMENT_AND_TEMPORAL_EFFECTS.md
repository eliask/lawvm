# Conditional Commencement Architecture

Living spec note.
Status: **accepted direction**, not yet implemented.

Key decisions: typed activation rules (not boolean contingent), resolution facts,
coverage certificates, per-effect temporal status, no silent enacted-date fallback.

See also: `SPEC_INDEX.md`, `.tmp/VIEWER_TIMELINE_FEATURE_PLAN.md`

---

## Bottom line

For **fixed future commencement** — “comes into force on 1 January 2027” — LawVM’s architecture is already close enough.

For **deferred / decree-set / conditional commencement** — “comes into force at a time to be set by decree” — the current architecture is **not yet sufficient as a true operational model**.

It has the beginnings of the right pieces:

* parse-layer `EffectIntent.Commencement(is_contingent=True)`,
* transitional `TemporalEvent(contingent=True)`,
* finding codes like `TIME.CONTINGENT_EFFECTIVE_DATE`,
* explicit `_ex` query/materialization APIs for some degraded states,

but it still lacks the core things that make these cases first-class:

* a **typed activation rule** instead of a boolean `contingent`,
* a **resolution fact** model for the later decree / trigger,
* a **coverage certificate** that distinguishes “no decree exists” from “source coverage is incomplete,”
* explicit **temporal-degraded query results**,
* a real model for **multiple competing contingent events** on the same provision.

Current support boundary:

> **Current LawVM can notice these cases, and a frontend may special-case them correctly, but the shared architecture does not yet fully model them.**

And the ideal architecture should.

---

# What This Phenomenon Is

## It is not “a third temporal axis”

The best way to think about it is **not**:

* publication time
* legal effect time
* unknown-decree time as a third axis

It is this instead:

* **publication / enactment time** is known,
* **legal effect time** is the important legal axis,
* but the legal effect axis is **not always a plain date**.

Sometimes the legal effect axis is a **rule**:

* immediate,
* fixed future date,
* when a decree is issued,
* when condition X is met.

So the key architectural statement is:

> **LawVM should treat legal effect as a possibly-conditional activation rule, not as a date field that is always known.**

That keeps the model bitemporal in the right sense, while admitting that one axis is sometimes **rule-valued**, not just date-valued.

---

# The Crucial Distinction

There are actually **two different kinds of uncertainty** here.

## 1. Legal contingency

The statute itself says:

* this repeal / insertion / replacement is **not in force yet**,
* and it will become in force only if a later legal trigger happens.

Example:

* “comes into force at a time to be set by decree.”

That is a property of the **law**.

## 2. Epistemic incompleteness

LawVM may not know whether the trigger later happened because:

* the relevant decree sources are incomplete,
* the relevant commencement instruments are incomplete,
* the decree cannot be mapped cleanly,
* or the condition is not machine-resolved.

That is a property of **our knowledge**.

This distinction matters a lot.

Because these are different situations:

* “The repeal is contingent, and source coverage certifies that no decree has yet issued.”
  → The repeal is **not active**.

* “The repeal is contingent, and source coverage does not establish whether a decree issued.”
  → The repeal is **unresolved**, and PIT should be degraded.

So **“PendingDecree” alone is not enough**.

---

# Does the current architecture handle this?

## Short answer

**Not fully.**

## What current core can do

From the current code:

* `EffectIntent.Commencement` can mark `is_contingent=True`.
* `TemporalEvent` has a `contingent: bool`.
* `PhaseResult` can auto-lower `EffectIntent` into `TemporalEvent`.
* The finding plane can emit `TIME.CONTINGENT_EFFECTIVE_DATE`.
* `select_active_version_ex()` / `materialize_pit_ex()` already know how to return explicit degraded results for **missing applicability scope**.

So current core can **represent that something contingent exists** and **surface a warning/finding**.

## What current core cannot yet do correctly as architecture

It still cannot, in a first-class way:

* say **what kind** of contingency this is,
* represent the **later decree / trigger resolution**,
* distinguish:

  * fixed future inactive,
  * contingent but certified untriggered,
  * contingent and unresolved,
* compute PIT truthfully when trigger coverage is incomplete,
* or reason cleanly about **multiple independent contingent events** touching one provision.

## The sharp current flaw

This is the most important practical problem in current core:

`compile_timelines()` still falls back to `OperationSource.effective` / `OperationSource.enacted`.

So if a contingent temporal event has:

* no `effective_from`,
* but the structural op still carries `enacted`,

the current runtime path can still drift toward **applying it too early**, unless a frontend suppresses or reshapes it beforehand.

That means:

> **Current core does not guarantee correct deferred-commencement behavior.**

If current Finland behavior happens to look correct in a deferred-commencement case, that is not yet because the shared temporal architecture has truly solved the problem.

---

# Decisions for Ideal LawVM

These decisions define the intended temporal architecture.

## 1. Keep LawVM bitemporal, but make legal effect rule-valued

LawVM should continue to think in terms of:

* enactment / publication time,
* legal effect time,
* optionally observation time for reproducibility.

But legal effect time should be modeled as:

* either a resolved date,
* or a trigger rule awaiting resolution.

Do **not** add a new global “third axis” for decree-set commencement.

Instead:

> **The legal-effect lane is rule-valued and sometimes unresolved until another legal source resolves it.**

## 2. `EffectIntent` stays parse-layer only

No change of direction here.

* `EffectIntent` is clause-surface output.
* It is not runtime authority.

## 3. `TemporalEvent` remains the operational lifecycle authority — but must be richer

Keep `TemporalEvent` as the main executable lifecycle object, but remove the idea that `contingent: bool` is enough.

The event must carry a typed activation rule.

## 4. Add a separate trigger-resolution fact layer

A later decree is not “just more provenance”.

It is a new legal fact that resolves when the earlier contingent event becomes effective.

So LawVM should add a distinct resolution object.

## 5. Add coverage / completeness certificates for trigger sources

The system must be able to say:

* “all commencement instruments up to D were checked; none resolved this trigger,”
* versus
* “source coverage does not establish whether resolution exists.”

That is the only way to avoid both fail-open and fail-closed lies.

## 6. Temporal status is per effect, not per provision

A provision does **not** have one commencement status.

Instead, **each effect on the provision** has its own activation rule.

This is the core answer for multiple competing commencement instruments.

Yes — and they must be modeled per effect.

---

# Ideal Object Model

LawVM should move toward this object model.

## A. Activation rule

Use a tagged sum, not a boolean bag.

```python
@dataclass(frozen=True)
class ImmediateActivation:
    pass

@dataclass(frozen=True)
class FixedDateActivation:
    date: str  # YYYY-MM-DD

@dataclass(frozen=True)
class ExternalInstrumentActivation:
    trigger_id: str
    instrument_ref: str = ""
    description: str = ""

@dataclass(frozen=True)
class PredicateActivation:
    trigger_id: str
    description: str

TemporalActivation = (
    ImmediateActivation
    | FixedDateActivation
    | ExternalInstrumentActivation
    | PredicateActivation
)
```

This cleanly distinguishes:

* immediate,
* future fixed,
* by decree / external instrument,
* by condition / predicate.

## B. Temporal event

```python
@dataclass(frozen=True)
class TemporalEvent:
    event_id: str
    kind: Literal["commence", "expire", "suspend", "revive", "set_applicability"]
    scope: TemporalScope
    activation: TemporalActivation
    provenance: OperationProvenance
    group_id: str | None = None
```

This is the executable lifecycle rule.

## C. Resolution fact

```python
@dataclass(frozen=True)
class TemporalResolutionFact:
    resolution_id: str
    trigger_id: str
    resolved_effective_from: str = ""
    resolved_effective_to: str = ""
    provenance: OperationProvenance = ...
```

This says that a contingent trigger was actually resolved by a later legal source.

For decree-set commencement, this is the decree fact.

## D. Coverage certificate

```python
@dataclass(frozen=True)
class TriggerCoverageCertificate:
    authority_family: str   # e.g. "finland_commencement_decrees"
    as_of: str
    status: Literal["complete", "partial", "unknown"]
    detail: Mapping[str, JsonValue] = FrozenDict()
```

This tells the runtime whether absence of a resolution fact means:

* “not triggered as far as authoritative coverage can tell,”
* or merely “unknown.”

## E. Evaluation result

Do not collapse this into plain `Optional[ProvisionVersion]`.

```python
@dataclass(frozen=True)
class TemporalEvaluation:
    status: Literal[
        "active",
        "inactive_future_fixed",
        "inactive_certified_untriggered",
        "unresolved_missing_trigger_fact",
        "unresolved_temporal_precedence",
    ]
    active_from: str = ""
    active_to: str = ""
    trigger_id: str = ""
    required_sources: tuple[str, ...] = ()
```

Then `select_active_version_ex()` and `materialize_pit_ex()` can surface this truthfully.

---

# Evaluated Temporal States

Not per provision. Per temporal effect, the meaningful evaluated states are:

1. **active**

   * the effect is in force at `as_of`

2. **inactive_future_fixed**

   * the effect has a fixed future commencement date, and that date has not arrived yet

3. **inactive_certified_untriggered**

   * the effect depends on an external trigger, and authoritative trigger coverage shows that trigger has not yet happened by `as_of`

4. **unresolved_missing_trigger_fact**

   * the effect depends on an external trigger, and source coverage cannot certify whether it has happened

5. **unresolved_temporal_precedence**

   * multiple active/resolved effects touch the same scope and the system cannot order them deterministically

That is the right list.

It is much better than a single coarse enum like:

* Commenced
* FutureFixed
* PendingDecree
* PendingCondition

because it separates:

* legal rule,
* actual resolution,
* epistemic certainty.

---

# How LawVM should evaluate these events

## Step 1: work per effect, not per provision

Each structural/text effect is linked to its activation.

So a provision can have:

1. a live current version,
2. a pending decree-set repeal,
3. a future fixed-date subsection replacement,
4. an applicability restriction effective on a different trigger.

These do not collapse into one status.

## Step 2: evaluate each temporal event at `as_of`

For each event:

* if activation is `ImmediateActivation`, it is active from enactment / explicit immediate rule,
* if activation is `FixedDateActivation(date)`, it is active iff `date <= as_of`,
* if activation is contingent on `trigger_id`,

  * and a `TemporalResolutionFact` exists with resolved date <= `as_of`,

    * active
  * and no resolution fact exists, but trigger coverage is complete,

    * inactive_certified_untriggered
  * and no resolution fact exists, and trigger coverage is incomplete/unknown,

    * unresolved_missing_trigger_fact

## Step 3: apply only active events

Only active events should change PIT state.

## Step 4: degrade when unresolved events could affect the answer

If unresolved contingent events intersect the address/scope being materialized, the authoritative result should be degraded.

Not guessed.

---

# Multiple competing commencement cases

Yes, they can absolutely stack.

## The right model

Not:

* one `CommencementStatus` per provision

But:

* **one activation rule per effect**

## Example

Provision §X has these edges:

1. Amendment A (2020): replace §X

   * activation: fixed date 2020-06-01
   * active today

2. Amendment B (2022): repeal §X

   * activation: by decree
   * unresolved

3. Amendment C (2024): replace §X moment 2

   * activation: fixed date 2027-01-01
   * inactive today, active in 2028

Then at different dates:

### Today

* A active
* B unresolved
* C inactive_future_fixed

Result:

* today’s PIT uses A
* but if trigger coverage for B is incomplete, the result is degraded for §X

### In 2028, no decree exists and coverage is complete

* A active
* B inactive_certified_untriggered
* C active

Result:

* A + C overlay

### In 2028, decree exists resolving B at 2028-05-01

* A active
* B active from 2028-05-01
* C active from 2027-01-01

Result:

* from 2027-01-01 until 2028-05-01, A + C
* from 2028-05-01 onward, B repeal dominates whole-section state

This is exactly why the model must be per effect.

---

# How conflicts should be handled

## Pending events do not conflict just because they exist

Two pending decree-set effects touching the same provision are not yet a live conflict.

They become a real issue only when:

* they are both resolved active at the same `as_of`,
* or one unresolved trigger makes the current state unknowable.

## Active conflict resolution

For active events, LawVM should use normal executable precedence:

1. effective date,
2. enacted/publication date,
3. source sequence / explicit intra-act order,
4. structural specificity via overlay semantics,
5. if still not deterministically orderable, degrade.

That final branch should be explicit:

* `TIME.UNRESOLVED_TEMPORAL_PRECEDENCE`
* authoritative PIT result degraded

LawVM should **not** invent jurisprudential priority rules that are not already encoded in text-state authority.

---

# What Finlex disagreements should mean

In a deferred-commencement disagreement, an editorial consolidation may show the repeal as already applied while LawVM keeps the old live text.

The ideal interpretation is:

## If LawVM can certify no trigger occurred

Then the correct classification is:

* **oracle anticipatory commencement**
* or **oracle editorially anticipates deferred commencement**

This is not a replay bug.

It is not source pathology.

It is a witness/editorial choice.

## If LawVM cannot certify trigger coverage

Then the correct classification is:

* **temporal non-commensurability / unresolved contingent commencement**

This is not “oracle wrong” yet.

It is a truthful unresolved state.

So the architecture decision is:

> **LawVM must never mutate legal PIT semantics just to match anticipatory oracle/editorial consolidation.**

An oracle-like editorial projection is a separate product, not legal PIT.

---

# What should happen to the current APIs

## `select_active_version_ex()`

It should grow beyond omitted-scope ambiguity.

Today it can return:

* selected
* absent
* ambiguous_missing_scope

It should also be able to return something like:

* `ambiguous_unresolved_temporal_trigger`
* `ambiguous_temporal_precedence`

## `materialize_pit_ex()`

Same story.

Today it degrades for missing scope.

It should also degrade for unresolved temporal trigger states that affect the selected statute/addresses.

## The plain wrappers

The plain wrappers should remain compatibility-only and clearly second-class.

The truthful APIs are the `_ex` ones.

---

# Current Core Decisions

These are the concrete decisions for current core.

## Decision 1

**Replace `TemporalEvent.contingent: bool` with a typed activation model.**

The bool is too weak.

## Decision 2

**Add `TemporalResolutionFact` as a first-class object.**

A later decree is not just provenance.

It is a legal resolution fact.

## Decision 3

**Add trigger coverage certificates.**

Without them, LawVM cannot distinguish:

* “not yet triggered”
* from
* “unknown whether triggered.”

## Decision 4

**Make deferred commencement authoritative in `_ex` APIs.**

Do not keep omitted-scope as the only degraded temporal state.

## Decision 5

**Do not let `compile_timelines()` silently apply contingent events by enacted-date fallback.**

This is probably the most urgent near-term correction.

If an effect has contingent activation and no resolution fact, core must not quietly treat enacted date as effective date.

## Decision 6

**Treat contingent commencement per effect, not per provision.**

This is a non-negotiable modeling decision.

## Decision 7

**Add dedicated finding codes.**

At minimum:

* `TIME.UNRESOLVED_COMMENCEMENT_TRIGGER`
* `TIME.TRIGGER_COVERAGE_INCOMPLETE`
* `TIME.UNRESOLVED_TEMPORAL_PRECEDENCE`

And on the comparison/evidence side:

* `EVID.ORACLE_ANTICIPATORY_COMMENCEMENT`

Keep `TIME.CONTINGENT_EFFECTIVE_DATE` as a coarse legacy umbrella/alias, but it is too blunt by itself.

## Decision 8

**Keep legal PIT and editorial anticipation separate forever.**

LawVM should never “match Finlex” by prematurely applying a contingent repeal in the legal PIT product.

---

# How this fits the ideal architecture

This lines up well with the ideal LawVM guide.

The one thing the ideal guide needs to be sharpened on is:

* `TemporalEvent` should not just have `effective_from`, `effective_to`, `contingent`
* it should have a typed activation rule and a resolution path

Ideal temporal story:

> `EffectIntent` → `TemporalEvent` (rule) + `TemporalResolutionFact` (later resolution) + `TriggerCoverageCertificate` (epistemic completeness) → timeline / PIT execution

That is the clean end-state.

---

# What current core truthfully supports right now

So the honest answer to “does our architecture currently handle such cases?” is:

## Yes, in these limited ways

* it can parse and mark contingent commencement,
* it can emit a coarse temporal finding,
* it can handle ordinary fixed-date future commencement,
* a frontend may special-case a contingent repeal correctly.

## No, not yet in the way it should

* it does not model the trigger type,
* it does not model later resolution as first-class authority,
* it does not certify untriggered vs unknown,
* it does not expose contingent-trigger degradation through the authoritative query/materialization APIs,
* it does not model multiple competing contingent events cleanly,
* and core can still drift toward wrong application because of date fallback behavior.

So architecturally:

> **the class is recognized, but not solved.**

---

# Near-Term Implementation Order

1. **Stop silent enacted-date fallback for contingent temporal events.**
   This is the highest-value safety fix.

2. **Add typed activation to `TemporalEvent`.**
   Keep `contingent` only as a compatibility shadow during migration.

3. **Add `TemporalResolutionFact`.**

4. **Extend `_ex` selection/materialization results with unresolved temporal statuses.**

5. **Add trigger coverage certificates.**

6. **Teach comparison/evidence/publication layers the dedicated temporal disagreement taxonomy.**

7. **Only then retire `OperationSource.effective/expires` as operational authority.**

---

# The simplest correct statement of the domain model

Compressed architectural rule:

> A legal effect is not always “effective on date D”; sometimes it is “effective when legal trigger T is resolved,” and LawVM must model both the trigger rule and our knowledge of whether T has been resolved.

That is the core architectural decision.
