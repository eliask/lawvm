---- MODULE LawVMTemporalOverlay ----
EXTENDS Integers, Sequences, FiniteSets, TLC

(***************************************************************************)
(* LawVMTemporalOverlay                                                    *)
(*                                                                         *)
(* A small TLC-sized temporal model of LawVM's overlay semantics.          *)
(*                                                                         *)
(* What is modeled                                                         *)
(*   - per-address provision timelines                                     *)
(*   - permanent versions                                                  *)
(*   - temporary overlays with expiry                                      *)
(*   - expiry extension chains                                             *)
(*   - commencement / enacted-vs-effective distinction                     *)
(*   - two query modes: "governing" and "in_force"                        *)
(*   - PIT visibility after ancestor masking and parent-newer suppression  *)
(*                                                                         *)
(* What is intentionally abstracted away                                   *)
(*   - label normalization                                                 *)
(*   - body-level vs chapter-qualified alias repair                        *)
(*   - IR tree surgery / exact container insertion order                   *)
(*   - repeal placeholder bias in tie-breaking                             *)
(*                                                                         *)
(* Abstraction map to the Python implementation                            *)
(*   timelines[a]                  ~ ProvisionTimeline.versions            *)
(*   variant = "temporary"         ~ ProvisionVersion.variant_kind         *)
(*   origExpires + expiryChain     ~ OperationSource.expires_original      *)
(*                                   + OperationSource.expiry_chain        *)
(*   ResolvedExpiry(v)             ~ runtime expiry used by selection      *)
(*   SelectedIdx / Materialized    ~ select_* + materialize_pit            *)
(*                                                                         *)
(* The model is purposely finite.  Edit OpStream to check other scenarios. *)
(***************************************************************************)

CONSTANT Dummy \* TLC requires at least one constant in some toolchains.

MaxDate == 4
Dates == 0..MaxDate
INF == MaxDate + 1
Modes == {"governing", "in_force"}
ABSENT == "ABSENT"
Tomb == "Tomb"
NoDate == -1
NoContent == "NoContent"

Addresses == {
  <<"S1">>,
  <<"S1", "M1">>,
  <<"S2">>,
  <<"S3">>
}

RootAddresses == { a \in Addresses : Len(a) = 1 }

Contents == {
  "BaseS1",
  "BaseS1M1",
  "BaseS2",
  "PermS1",
  "TempS1",
  "ChildCommenced",
  "RetroS2",
  "TempInsertS3",
  Tomb
}

BaseVersion(c) ==
  [ variant     |-> "permanent",
    effective   |-> 0,
    enacted     |-> 0,
    origExpires |-> INF,
    expiryChain |-> <<>>,
    content     |-> c ]

PermVersion(eff, enc, c) ==
  [ variant     |-> "permanent",
    effective   |-> eff,
    enacted     |-> enc,
    origExpires |-> INF,
    expiryChain |-> <<>>,
    content     |-> c ]

TempVersion(eff, enc, exp, c) ==
  [ variant     |-> "temporary",
    effective   |-> eff,
    enacted     |-> enc,
    origExpires |-> exp,
    expiryChain |-> <<>>,
    content     |-> c ]

TombstoneVersion(eff, enc) == PermVersion(eff, enc, Tomb)

NoVersion ==
  [ variant     |-> "none",
    effective   |-> INF,
    enacted     |-> INF,
    origExpires |-> INF,
    expiryChain |-> <<>>,
    content     |-> ABSENT ]

InitTimelines ==
  [ a \in Addresses |->
      CASE a = <<"S1">>       -> << BaseVersion("BaseS1") >>
        [] a = <<"S1", "M1">> -> << BaseVersion("BaseS1M1") >>
        [] a = <<"S2">>       -> << BaseVersion("BaseS2") >>
        [] OTHER               -> << >> ]

(***************************************************************************)
(* Operation stream                                                        *)
(*                                                                         *)
(* The build machine compiles a prefix of this globally effective-sorted    *)
(* stream, mirroring compile_timelines(sorted(ops, key=_op_date)).         *)
(*                                                                         *)
(* Scenario coverage in this default stream:                               *)
(*   1. S1 gets a temporary overlay, then a later permanent background.    *)
(*   2. S1.M1 gets a commenced child version.                              *)
(*   3. S3 is a temporary insert with no base background.                  *)
(*   4. S2 gets a retroactive permanent version, then a repeal.            *)
(*   5. S1's temporary expiry is extended by a later act.                  *)
(***************************************************************************)

Op(kind, addr, eff, enc, exp, targetEff, content) ==
  [ kind      |-> kind,
    addr      |-> addr,
    eff       |-> eff,
    enc       |-> enc,
    exp       |-> exp,
    targetEff |-> targetEff,
    content   |-> content ]

OpStream == <<
  Op("temp",     <<"S1">>,        1, 1, 3,     NoDate, "TempS1"),
  Op("temp",     <<"S3">>,        1, 1, 2,     NoDate, "TempInsertS3"),
  Op("perm",     <<"S2">>,        1, 3, INF,   NoDate, "RetroS2"),
  Op("perm",     <<"S1">>,        2, 2, INF,   NoDate, "PermS1"),
  Op("commence", <<"S1", "M1">>, 2, 0, INF,   NoDate, "ChildCommenced"),
  Op("extend",   <<"S1">>,        2, 2, 4,     1,      NoContent),
  Op("repeal",   <<"S2">>,        4, 4, INF,   NoDate, Tomb)
>>

(***************************************************************************)
(* Structural helpers                                                      *)
(***************************************************************************)

IsAncestor(p, a) ==
  /\ Len(p) < Len(a)
  /\ SubSeq(a, 1, Len(p)) = p

Ancestors(a) == { p \in Addresses : IsAncestor(p, a) }

ResolvedExpiry(v) ==
  IF v.variant = "temporary"
  THEN LET n == Len(v.expiryChain)
       IN IF n = 0 THEN v.origExpires ELSE v.expiryChain[n]
  ELSE INF

ReplaceSeq(seq, idx, val) ==
  [ i \in DOMAIN seq |-> IF i = idx THEN val ELSE seq[i] ]

AppendVersion(tls, a, v) ==
  [tls EXCEPT ![a] = Append(@, v)]

ExtendVersion(v, newExp) ==
  [v EXCEPT !.expiryChain = Append(@, newExp)]

MatchingTempIdxs(tls, a, targetEff) ==
  { i \in DOMAIN tls[a] :
      /\ tls[a][i].variant = "temporary"
      /\ tls[a][i].effective = targetEff }

FindTempIdx(tls, a, targetEff) ==
  LET S == MatchingTempIdxs(tls, a, targetEff)
  IN IF S = {}
     THEN 0
     ELSE CHOOSE i \in S : \A j \in S : i >= j

ApplyOp(tls, op) ==
  CASE op.kind = "perm" ->
         AppendVersion(tls, op.addr, PermVersion(op.eff, op.enc, op.content))
    [] op.kind = "commence" ->
         AppendVersion(tls, op.addr, PermVersion(op.eff, op.enc, op.content))
    [] op.kind = "temp" ->
         AppendVersion(tls, op.addr, TempVersion(op.eff, op.enc, op.exp, op.content))
    [] op.kind = "repeal" ->
         AppendVersion(tls, op.addr, TombstoneVersion(op.eff, op.enc))
    [] op.kind = "extend" ->
         LET idx == FindTempIdx(tls, op.addr, op.targetEff)
         IN IF idx = 0
            THEN tls
            ELSE [tls EXCEPT ![op.addr] = ReplaceSeq(@, idx, ExtendVersion(@[idx], op.exp))]
    [] OTHER -> tls

RECURSIVE CompiledPrefix(_)
CompiledPrefix(n) ==
  IF n = 0
  THEN InitTimelines
  ELSE ApplyOp(CompiledPrefix(n - 1), OpStream[n])

VARIABLES timelines, pc, phase, now

vars == << timelines, pc, phase, now >>

(***************************************************************************)
(* Selection algebra                                                       *)
(***************************************************************************)

Eligible(v, d, mode) ==
  /\ v.effective <= d
  /\ d < ResolvedExpiry(v)
  /\ (mode = "governing" \/ v.enacted <= d)

LaterOrEqual(v1, i1, v2, i2) ==
  \/ v1.effective > v2.effective
  \/ /\ v1.effective = v2.effective
     /\ \/ v1.enacted > v2.enacted
        \/ /\ v1.enacted = v2.enacted
           /\ i1 >= i2

LatestIdx(seq, idxs) ==
  IF idxs = {}
  THEN 0
  ELSE CHOOSE i \in idxs : \A j \in idxs : LaterOrEqual(seq[i], i, seq[j], j)

TempIdx(a, d, mode) ==
  LET S == { i \in DOMAIN timelines[a] :
               /\ timelines[a][i].variant = "temporary"
               /\ Eligible(timelines[a][i], d, mode) }
  IN LatestIdx(timelines[a], S)

BgIdx(a, d, mode) ==
  LET S == { i \in DOMAIN timelines[a] :
               /\ timelines[a][i].variant = "permanent"
               /\ Eligible(timelines[a][i], d, mode) }
  IN LatestIdx(timelines[a], S)

SelectedIdx(a, d, mode) ==
  IF TempIdx(a, d, mode) # 0
  THEN TempIdx(a, d, mode)
  ELSE BgIdx(a, d, mode)

HasSelection(a, d, mode) == SelectedIdx(a, d, mode) # 0

SelectedVersion(a, d, mode) ==
  IF SelectedIdx(a, d, mode) = 0
  THEN NoVersion
  ELSE timelines[a][SelectedIdx(a, d, mode)]

ParentNewer(a, d, mode) ==
  /\ HasSelection(a, d, mode)
  /\ \E p \in Ancestors(a) :
        /\ HasSelection(p, d, mode)
        /\ SelectedVersion(p, d, mode).effective > SelectedVersion(a, d, mode).effective

MaskedByTemporaryAncestor(a, d, mode) ==
  /\ HasSelection(a, d, mode)
  /\ SelectedVersion(a, d, mode).variant = "permanent"
  /\ \E p \in Ancestors(a) :
        /\ HasSelection(p, d, mode)
        /\ SelectedVersion(p, d, mode).variant = "temporary"

Visible(a, d, mode) ==
  /\ HasSelection(a, d, mode)
  /\ ~ParentNewer(a, d, mode)
  /\ ~MaskedByTemporaryAncestor(a, d, mode)
  /\ SelectedVersion(a, d, mode).content # Tomb

MaterializedContent(a, d, mode) ==
  IF Visible(a, d, mode)
  THEN SelectedVersion(a, d, mode).content
  ELSE ABSENT

PIT(d, mode) == [ a \in Addresses |-> MaterializedContent(a, d, mode) ]

(***************************************************************************)
(* Build/query state machine                                               *)
(***************************************************************************)

Init ==
  /\ timelines = InitTimelines
  /\ pc = 1
  /\ phase = "build"
  /\ now = 0

ApplyNextOp ==
  /\ phase = "build"
  /\ pc <= Len(OpStream)
  /\ timelines' = ApplyOp(timelines, OpStream[pc])
  /\ pc' = pc + 1
  /\ UNCHANGED << phase, now >>

BeginQuery ==
  /\ phase = "build"
  /\ pc = Len(OpStream) + 1
  /\ phase' = "query"
  /\ UNCHANGED << timelines, pc, now >>

AdvanceNow ==
  /\ phase = "query"
  /\ now < MaxDate
  /\ now' = now + 1
  /\ UNCHANGED << timelines, pc, phase >>

Next == ApplyNextOp \/ BeginQuery \/ AdvanceNow

Spec ==
  Init /\ [][Next]_vars
       /\ WF_vars(ApplyNextOp)
       /\ WF_vars(BeginQuery)
       /\ WF_vars(AdvanceNow)

(***************************************************************************)
(* Safety invariants                                                       *)
(***************************************************************************)

VersionOK(v) ==
  /\ v.variant \in {"permanent", "temporary"}
  /\ v.effective \in Dates
  /\ v.enacted \in Dates
  /\ v.origExpires \in Dates \cup {INF}
  /\ v.expiryChain \in Seq(Dates)
  /\ v.content \in Contents

TypeOK ==
  /\ DOMAIN timelines = Addresses
  /\ pc \in 1..(Len(OpStream) + 1)
  /\ phase \in {"build", "query"}
  /\ now \in Dates
  /\ \A a \in Addresses : \A i \in DOMAIN timelines[a] : VersionOK(timelines[a][i])

Inv_CompilerRefinesPrefix == timelines = CompiledPrefix(pc - 1)

Inv_TimelinesSorted ==
  \A a \in Addresses :
    \A i, j \in DOMAIN timelines[a] :
      i < j =>
        \/ timelines[a][i].effective < timelines[a][j].effective
        \/ /\ timelines[a][i].effective = timelines[a][j].effective
           /\ timelines[a][i].enacted <= timelines[a][j].enacted

Inv_NoAmbiguousPermanentPrecedence ==
  \A a \in Addresses :
    \A i, j \in DOMAIN timelines[a] :
      /\ i < j
      /\ timelines[a][i].variant = "permanent"
      /\ timelines[a][j].variant = "permanent"
      => ~(/\ timelines[a][i].effective = timelines[a][j].effective
           /\ timelines[a][i].enacted = timelines[a][j].enacted)

Inv_TemporaryWellFormed ==
  \A a \in Addresses :
    \A i \in DOMAIN timelines[a] :
      timelines[a][i].variant = "temporary" =>
        /\ timelines[a][i].origExpires \in Dates
        /\ timelines[a][i].effective < ResolvedExpiry(timelines[a][i])

IntervalsOverlap(v1, v2) ==
  /\ v1.effective < ResolvedExpiry(v2)
  /\ v2.effective < ResolvedExpiry(v1)

Inv_NoOverlappingTemporaries ==
  \A a \in Addresses :
    \A i, j \in DOMAIN timelines[a] :
      /\ i < j
      /\ timelines[a][i].variant = "temporary"
      /\ timelines[a][j].variant = "temporary"
      => ~IntervalsOverlap(timelines[a][i], timelines[a][j])

StrictlyIncreasing(seq) ==
  \A i \in 1..(Len(seq) - 1) : seq[i] < seq[i + 1]

Inv_ExpiryChainMonotone ==
  \A a \in Addresses :
    \A i \in DOMAIN timelines[a] :
      LET v == timelines[a][i] IN
        v.variant = "temporary" =>
          /\ StrictlyIncreasing(v.expiryChain)
          /\ (Len(v.expiryChain) = 0 \/ v.origExpires < v.expiryChain[1])

Inv_TwoRailSelection ==
  \A a \in Addresses :
    \A d \in Dates :
      \A mode \in Modes :
        /\ (TempIdx(a, d, mode) # 0 => SelectedIdx(a, d, mode) = TempIdx(a, d, mode))
        /\ (TempIdx(a, d, mode) = 0 => SelectedIdx(a, d, mode) = BgIdx(a, d, mode))

Inv_InForceOnlyUsesEnacted ==
  \A a \in Addresses :
    \A d \in Dates :
      LET i == SelectedIdx(a, d, "in_force") IN
        i = 0 \/ timelines[a][i].enacted <= d

Inv_NoBackgroundLeakThroughActiveTempAncestor ==
  \A a \in Addresses :
    \A d \in Dates :
      \A mode \in Modes :
        MaskedByTemporaryAncestor(a, d, mode) => MaterializedContent(a, d, mode) = ABSENT

Inv_NoOlderChildLeaksThroughNewerParent ==
  \A a \in Addresses :
    \A d \in Dates :
      \A mode \in Modes :
        ParentNewer(a, d, mode) => MaterializedContent(a, d, mode) = ABSENT

Inv_NoBackgroundNoOverlayMeansAbsent ==
  \A a \in Addresses :
    \A d \in Dates :
      \A mode \in Modes :
        /\ TempIdx(a, d, mode) = 0
        /\ BgIdx(a, d, mode) = 0
        => MaterializedContent(a, d, mode) = ABSENT

(***************************************************************************)
(* Bounded liveness / eventuality                                          *)
(*                                                                         *)
(* For TLC on a finite date lattice, this is more useful than an           *)
(* unrestricted temporal liveness formula.                                 *)
(***************************************************************************)

DormantVersion(v) == v.enacted < v.effective

SupersededByThen(a, i, d) ==
  \E j \in DOMAIN timelines[a] :
    /\ j # i
    /\ Eligible(timelines[a][j], d, "in_force")
    /\ LaterOrEqual(timelines[a][j], j, timelines[a][i], i)

AllOpsApplied == pc = Len(OpStream) + 1

Bounded_CommencedVersionsResolve ==
  AllOpsApplied =>
    \A a \in Addresses :
      \A i \in DOMAIN timelines[a] :
        DormantVersion(timelines[a][i]) =>
          \E d \in Dates :
            /\ d >= timelines[a][i].effective
            /\ (SelectedIdx(a, d, "in_force") = i \/ SupersededByThen(a, i, d))

(***************************************************************************)
(* Scenario-specific checks for the default OpStream                        *)
(*                                                                         *)
(* These are not universal laws of the model; they validate that the       *)
(* sample stream actually exhibits the target phenomena.                   *)
(***************************************************************************)

Sample_ExpectedOutcomes ==
  AllOpsApplied =>
    /\ MaterializedContent(<<"S1">>,        2, "governing") = "TempS1"
    /\ MaterializedContent(<<"S1">>,        4, "governing") = "PermS1"
    /\ MaterializedContent(<<"S1", "M1">>, 2, "governing") = ABSENT
    /\ MaterializedContent(<<"S1", "M1">>, 4, "governing") = "ChildCommenced"
    /\ MaterializedContent(<<"S3">>,        1, "governing") = "TempInsertS3"
    /\ MaterializedContent(<<"S3">>,        2, "governing") = ABSENT
    /\ MaterializedContent(<<"S2">>,        2, "governing") = "RetroS2"
    /\ MaterializedContent(<<"S2">>,        2, "in_force")  = "BaseS2"
    /\ MaterializedContent(<<"S2">>,        4, "governing") = ABSENT

=============================================================================
