"""Cross-jurisdiction invariant-parity audit (registry row XP-06).

This module materializes the **invariant × frontend** parity matrix the audit
registry's XP-06 row and the "Completeness self-critique" bullet call the
*thinnest axis*: *"no lens systematically built the matrix invariant ×
{FI,UK,US,NZ,EE} to find where an invariant is enforced in one frontend and
silently absent in a sibling."* It applies the §0 doctrine — *make the silent
divergence a first-class object* — to cross-jurisdiction coverage ITSELF.

This is READ-MOSTLY ANALYSIS. It changes no gate behaviour and emits no
production output. It builds a VIEW (the matrix) and a typed finding population
(the divergences) derived from **real registrations**, not prose:

* The per-frontend :class:`~lawvm.core.apply_seam.ApplyProfile` gate fields —
  ``boundary_mode`` (LS-01), the ``authorization_resolver`` (EV-05),
  ``occupancy_resolver`` + ``occupancy_mode`` (LS-03), and
  ``provenance_resolver`` (AM-01) — are read directly out of each frontend's
  ``ApplyProfile(...)`` construction site by a static AST scan (the profiles are
  constructed inside closures over frontend-local materializers, so they cannot
  be imported as module constants without running the whole grafter; the AST
  scan reads the real keyword literals without executing any frontend code,
  mirroring how ``tests/test_ee_guard_liveness.py`` scans for emit sites).
  NZ has no ``ApplyProfile`` seam today, so its per-unit modes are recorded as
  ``absent`` instead of being collapsed into a sibling ``off``.
* FI is the reference UPPER BOUND: its per-op apply-authority battery
  (``finland/apply_resolved_op._enforce_per_op_apply_authority``) blocks on all
  four gates under strict mode. FI is never edited; it is read-only here as the
  ceiling every sibling is measured against.
* Carrier presence — the conserved-replay wrapper, per-op ``WriteReceipt``
  emission, and the shared cross-act same-moment detector — is derived from the
  actual code surface (``apply_<j>_ops_conserved`` / ``replay_<j>_ops_conserved``
  defs, ``WriteReceipt`` emitters, and ``detect_cross_act_same_moment_conflicts``
  call sites).

The real enforcement divergences this surfaces today: **EE flips BOTH LS-03
occupancy AND LS-01 mutation-boundary to ``block`` — the first two enforcing
apply-seam gates, each after measuring its corpus clean (occupancy-clean for
LS-03; boundary-clean after closing a chapter-nesting declaration artifact for
LS-01) — while every other tree frontend leaves them at the default no-op
``off``.** An invariant enforced in one frontend and only observed/off in its
siblings is exactly the silent-divergence object §0 makes first-class;
``classify_divergences`` lifts those, and the others, into typed
:class:`InvariantCoverageDivergence` rows.

API tier
--------
Analysis/view surface. Imports from ``core/apply_seam`` read-only; does not
import ``finland/`` or any frontend module (the AST scan reads source text).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional, Tuple, cast

# ── Vocabulary ────────────────────────────────────────────────────────────────

#: The known frontends, in the registry's canonical order. FI is the reference
#: upper bound (its own per-op battery, NOT an ``ApplyProfile``); NO/SE/EE/EU/UK/US
#: each construct an ``ApplyProfile`` fed to ``core/apply_seam.apply_op``. NZ is
#: a first-class frontend row but has no apply-seam profile today.
FrontendId = Literal["fi", "no", "se", "ee", "eu", "nz", "uk", "us"]

KNOWN_FRONTENDS: Tuple[FrontendId, ...] = ("fi", "no", "se", "ee", "eu", "nz", "uk", "us")

#: A gate's per-frontend enforcement mode.
#:
#: * ``block`` — the gate fires a blocking violation on a bad unit.
#: * ``observe`` — the gate runs and emits a non-blocking observation.
#: * ``off`` — the gate mechanism is wired but the disposition disables it.
#: * ``absent`` — no producer is wired for this gate in this frontend.
GateMode = Literal["block", "observe", "off", "absent"]

#: Same-moment ordering/detection path kind. This is intentionally more precise
#: than the legacy boolean carrier: UK uses the shared generic algorithm at the
#: effect plane, while NO/SE/EE/EU/NZ/US route lowered ops through ``order_ops``.
SameMomentPathKind = Literal[
    "op_ordering",
    "shared_generic_effect_adapter",
    "timeline_plane",
    "absent",
]

#: The four per-unit apply-seam invariants whose disposition is read from each
#: ``ApplyProfile`` (registry rows in parentheses).
PER_UNIT_INVARIANTS: Tuple[str, ...] = (
    "LS-01",  # per-op mutation boundary (boundary_mode)
    "EV-05",  # execution-authorization at apply (authorization_resolver)
    "LS-03",  # occupancy-transition gate (occupancy_resolver + occupancy_mode)
    "AM-01",  # provenance-acceptance gate (provenance_resolver)
)

#: The carrier presences (not per-unit gates — structural producers). Each is a
#: boolean PRESENT/absent per frontend rather than a {block,observe,off} mode.
CARRIER_PRESENCES: Tuple[str, ...] = (
    "conserved_wrapper",     # a conserving accepted/rejected replay partition
    "write_receipt",         # per-op WriteReceipt emission
    "same_moment_detector",  # shared cross-act same-moment conflict detector
)

#: Human-readable label per invariant id.
INVARIANT_LABEL: Dict[str, str] = {
    "LS-01": "per-op mutation boundary",
    "EV-05": "execution-authorization at apply",
    "LS-03": "occupancy-transition gate",
    "AM-01": "provenance-acceptance gate",
    "conserved_wrapper": "conserved replay wrapper",
    "write_receipt": "per-op WriteReceipt emission",
    "same_moment_detector": "shared cross-act same-moment detector",
}


# ── Source locations (read-only; for the AST scan) ────────────────────────────

#: Repo root: ``src/lawvm/core/cross_jurisdiction_parity.py`` -> repo root is
#: three parents up from this file's directory (core -> lawvm -> src -> root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"

#: For the ApplyProfile frontends: the module file whose ``ApplyProfile(...)``
#: construction site carries the real gate dispositions. NZ is omitted because
#: its chain replay does not yet use the apply seam; the row is populated
#: separately with ``absent`` modes.
_PROFILE_SOURCE: Dict[str, Path] = {
    "no": _SRC_ROOT / "norway" / "grafter.py",
    "se": _SRC_ROOT / "sweden" / "grafter.py",
    "ee": _SRC_ROOT / "estonia" / "grafter.py",
    "eu": _SRC_ROOT / "eu" / "pipeline.py",
    "uk": _SRC_ROOT / "uk_legislation" / "replay_executor.py",
    "us": _SRC_ROOT / "us_federal" / "apply_profile.py",
}

_APPLY_PROFILE_FRONTENDS: Tuple[FrontendId, ...] = ("no", "se", "ee", "eu", "uk", "us")


# ── The matrix model (frozen typed) ───────────────────────────────────────────


@dataclass(frozen=True)
class FrontendGates:
    """The four per-unit gate dispositions for one frontend (real registrations).

    ``modes`` maps each :data:`PER_UNIT_INVARIANTS` id to its :data:`GateMode`.
    ``carriers`` maps each :data:`CARRIER_PRESENCES` id to a present/absent bool.
    ``source`` is the file the profile registration was read from (or a marker
    for FI, whose battery is read by reference, not from an ``ApplyProfile``).
    """

    frontend: FrontendId
    modes: Dict[str, GateMode]
    carriers: Dict[str, bool]
    same_moment_path: SameMomentPathKind
    source: str

    def mode(self, invariant: str) -> GateMode:
        return self.modes[invariant]


@dataclass(frozen=True)
class ParityMatrix:
    """The frozen invariant × frontend parity matrix built from real code.

    ``rows`` is keyed by :data:`FrontendId`; every key in
    :data:`KNOWN_FRONTENDS` is present (the matrix is TOTAL over the known
    frontends). ``reference`` names the upper-bound frontend (FI).
    """

    rows: Dict[FrontendId, FrontendGates]
    reference: FrontendId = "fi"

    def is_total(self) -> bool:
        """True iff every known frontend has a row and every gate a mode."""
        if set(self.rows) != set(KNOWN_FRONTENDS):
            return False
        for gates in self.rows.values():
            if set(gates.modes) != set(PER_UNIT_INVARIANTS):
                return False
            if set(gates.carriers) != set(CARRIER_PRESENCES):
                return False
            if gates.same_moment_path not in (
                "op_ordering",
                "shared_generic_effect_adapter",
                "timeline_plane",
                "absent",
            ):
                return False
        return True

    def modes_for(self, invariant: str) -> Dict[FrontendId, GateMode]:
        """The per-frontend mode map for one per-unit invariant."""
        return {fe: self.rows[fe].modes[invariant] for fe in KNOWN_FRONTENDS}

    def carriers_for(self, carrier: str) -> Dict[FrontendId, bool]:
        """The per-frontend present/absent map for one carrier."""
        return {fe: self.rows[fe].carriers[carrier] for fe in KNOWN_FRONTENDS}

    def same_moment_paths(self) -> Dict[FrontendId, SameMomentPathKind]:
        """The precise same-moment routing path per frontend."""
        return {fe: self.rows[fe].same_moment_path for fe in KNOWN_FRONTENDS}


# ── Divergence finding (the typed first-class object) ─────────────────────────

#: The governed finding code this audit emits (registered in
#: ``core/observation_registry.py`` at role=observation). Re-exported for the
#: test + report so the string lives in one place.
INVARIANT_COVERAGE_DIVERGENCE_CODE = "AUDIT.INVARIANT_COVERAGE_DIVERGENCE"

#: How a frontend's mode diverges from the matrix's majority/reference baseline.
DivergenceKind = Literal[
    "enforced-here",  # this frontend BLOCKS where siblings only observe/off/absent
    "observe-here",   # this frontend observes where siblings block (or are absent)
    "absent-here",    # this carrier/gate is absent where siblings have it
]


@dataclass(frozen=True)
class InvariantCoverageDivergence:
    """One typed XP-06 ``INVARIANT_COVERAGE_DIVERGENCE`` finding.

    Each carries the invariant, the per-frontend mode map (the evidence), the
    divergence kind, the frontend(s) that diverge, the baseline the rest share,
    and a one-line ``rationale`` distinguishing a genuine should-fix from a
    justified jurisdiction difference.
    """

    invariant: str
    invariant_label: str
    mode_map: Dict[FrontendId, str]
    kind: DivergenceKind
    divergent_frontends: Tuple[FrontendId, ...]
    baseline_mode: str
    rationale: str
    finding_code: str = INVARIANT_COVERAGE_DIVERGENCE_CODE


# ── The AST scan (read the real ApplyProfile registrations) ───────────────────


@dataclass
class _ProfileLiterals:
    """Keyword literals harvested from one ``ApplyProfile(...)`` call site."""

    boundary_mode: Optional[str] = None
    occupancy_mode: Optional[str] = None
    has_authorization_resolver: bool = False
    has_occupancy_resolver: bool = False
    has_provenance_resolver: bool = False
    keywords_seen: Tuple[str, ...] = field(default_factory=tuple)


def _scan_apply_profile_call(source_path: Path) -> _ProfileLiterals:
    """Find the ``ApplyProfile(...)`` call in ``source_path`` and read its keywords.

    Reads the real registration as written. A keyword whose value is a string
    constant (``boundary_mode="off"``) is read verbatim; a keyword whose value
    is a resolver name (``occupancy_resolver=_ee_section_occupancy``) is recorded
    as "a non-default resolver is wired". A profile that omits a keyword inherits
    the ``ApplyProfile`` default — handled by the caller, not here.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    found: Optional[_ProfileLiterals] = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
        if name != "ApplyProfile":
            continue
        lits = _ProfileLiterals()
        seen: list[str] = []
        for kw in node.keywords:
            if kw.arg is None:
                continue
            seen.append(kw.arg)
            if kw.arg == "boundary_mode" and isinstance(kw.value, ast.Constant):
                lits.boundary_mode = str(kw.value.value)
            elif kw.arg == "occupancy_mode" and isinstance(kw.value, ast.Constant):
                lits.occupancy_mode = str(kw.value.value)
            elif kw.arg == "authorization_resolver":
                lits.has_authorization_resolver = True
            elif kw.arg == "occupancy_resolver":
                lits.has_occupancy_resolver = True
            elif kw.arg == "provenance_resolver":
                lits.has_provenance_resolver = True
        lits.keywords_seen = tuple(seen)
        if found is not None:
            raise ValueError(
                f"{source_path} has more than one ApplyProfile(...) site; "
                "the parity scan assumes exactly one per frontend module"
            )
        found = lits
    if found is None:
        raise ValueError(f"no ApplyProfile(...) construction site found in {source_path}")
    return found


_VALID_GATE_MODES: frozenset[str] = frozenset(("block", "observe", "off", "absent"))


def _as_mode(raw: Optional[str], default: GateMode) -> GateMode:
    """Narrow a scanned string literal to a :data:`GateMode` (default if None).

    Raises on an unknown literal — a profile that introduced a new
    ``boundary_mode``/``occupancy_mode`` value the audit does not model should
    fail loud here rather than be silently mis-classified.
    """
    if raw is None:
        return default
    if raw not in _VALID_GATE_MODES:
        raise ValueError(f"unrecognized gate mode literal {raw!r} in an ApplyProfile registration")
    return cast("GateMode", raw)


def _gates_from_literals(frontend: FrontendId, lits: _ProfileLiterals) -> Dict[str, GateMode]:
    """Map the harvested ``ApplyProfile`` literals to the four per-unit modes.

    The defaults mirror :class:`~lawvm.core.apply_seam.ApplyProfile`:
    ``boundary_mode`` defaults to ``"observe"``; the three resolvers default to
    no-op (the gate is wired but yields nothing → ``off`` for that profile);
    ``occupancy_mode`` defaults to ``"observe"`` but is only live when a real
    ``occupancy_resolver`` is supplied.
    """
    # LS-01 — boundary_mode read verbatim (default "observe" if omitted).
    boundary: GateMode = _as_mode(lits.boundary_mode, "observe")

    # EV-05 — the authorization gate is structurally OBSERVE-only for every
    # ApplyProfile frontend: the seam emits the non-blocking
    # ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` witness on an op with no proof,
    # and there is NO per-profile authorization-block disposition (the strict twin
    # ``EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED`` is FI-only, via FI's separate
    # battery — read in ``_fi_reference_gates``, not here). So wiring a real
    # ``authorization_resolver`` does NOT flip the mode to block; it only changes
    # WHICH ops emit the observation (an op carrying a real proof goes quiet),
    # i.e. it shrinks the measured firewall-hole residue without changing the
    # disposition. EE is the first to wire a real resolver (the proof carrier on
    # ``core/ir.LegalOperation``): it measures a near-zero residue while its
    # siblings (default no-op resolver) sit at the ~100% hole — a difference the
    # coarse {block,observe,off} vocab cannot express, recorded in
    # ``notes/PROOF_CARRIER_FINDINGS.md``. The honest mode for all six is
    # "observe". ``has_authorization_resolver`` stays scanned as the "real
    # resolver wired" signal but no longer drives the mode.
    authorization: GateMode = "observe"

    # LS-03 — the occupancy gate is a no-op unless a real occupancy_resolver is
    # supplied. With a resolver, occupancy_mode drives block/observe/off; without
    # one the gate models no occupancy -> "off" for this frontend.
    if lits.has_occupancy_resolver:
        occupancy: GateMode = _as_mode(lits.occupancy_mode, "observe")
    else:
        occupancy = "off"

    # AM-01 — provenance gate is a no-op unless a real provenance_resolver is
    # supplied (no profile supplies one today -> "off").
    provenance: GateMode = "observe" if lits.has_provenance_resolver else "off"

    return {
        "LS-01": boundary,
        "EV-05": authorization,
        "LS-03": occupancy,
        "AM-01": provenance,
    }


# ── Carrier presence (real code surface) ──────────────────────────────────────

#: ``apply_<j>_ops_conserved`` / ``replay_<j>_ops_conserved`` def per frontend,
#: or an equivalent typed accounting carrier for a frontend whose materialization
#: unit is not the shared IR op fold. NZ's chain replay exposes an accepted/skipped
#: transition wrapper over its own chain-op vocabulary; US exposes a dry-run
#: section-surface account over rows/refusals/agreement residuals. These are
#: conservation carriers, not replay-authorization claims.
_CONSERVED_WRAPPER_DEF: Dict[FrontendId, Optional[Tuple[Path, Optional[str]]]] = {
    "no": (_SRC_ROOT / "norway" / "grafter.py", "apply_no_ops_conserved"),
    "se": (_SRC_ROOT / "sweden" / "grafter.py", "apply_se_ops_conserved"),
    "ee": (_SRC_ROOT / "estonia" / "grafter.py", "apply_ee_ops_conserved"),
    "eu": (_SRC_ROOT / "eu" / "pipeline.py", "apply_eu_ops_conserved"),
    "nz": (_SRC_ROOT / "new_zealand" / "chain_replay.py", "apply_nz_transition_conserved"),
    "uk": (_SRC_ROOT / "uk_legislation" / "replay_conserved.py", "replay_uk_ops_conserved"),
    "us": (_SRC_ROOT / "us_federal" / "dry_run.py", "build_us_dry_run_conserved_account"),
    "fi": (_SRC_ROOT / "finland" / "oracle_comparison.py", None),  # FI conserves via its own fold
}

#: The ``WriteReceipt`` constructor name a frontend calls to emit a per-op
#: receipt. A frontend with ZERO ``WriteReceipt(...)`` construction sites in its
#: package emits no per-op receipt of its own (EE today: it sets the seam
#: ``emit_receipts=False`` and has no dedicated emitter).
_WRITE_RECEIPT_CTOR = "WriteReceipt"

#: The per-frontend package directory whose modules are scanned for a reference
#: to the SHARED same-moment detection path. A frontend delegates same-moment
#: detection either DIRECTLY (importing ``lawvm.core.cross_act_same_moment``, as
#: UK does) or TRANSITIVELY through the unified ordering kernel
#: (``lawvm.core.op_ordering.order_ops``, which wraps the cross-act detector — as
#: NO/SE/EE/EU do). So the carrier scan keys on EITHER shared module name, not a
#: single function. FI uses the timeline-plane same-moment (LS-15/16), routing
#: through NEITHER shared module; US wires neither.
_FRONTEND_PACKAGE: Dict[FrontendId, Path] = {
    "fi": _SRC_ROOT / "finland",
    "no": _SRC_ROOT / "norway",
    "se": _SRC_ROOT / "sweden",
    "ee": _SRC_ROOT / "estonia",
    "eu": _SRC_ROOT / "eu",
    "nz": _SRC_ROOT / "new_zealand",
    "uk": _SRC_ROOT / "uk_legislation",
    "us": _SRC_ROOT / "us_federal",
}

#: The shared modules that constitute the cross-act same-moment detection path:
#: the detector itself, and the unified ordering kernel that wraps it. A frontend
#: importing EITHER routes its ops through same-moment detection.
_SHARED_SAME_MOMENT_MODULES: Tuple[str, ...] = ("cross_act_same_moment", "op_ordering")

_SAME_MOMENT_PATH_DISPLAY: Dict[SameMomentPathKind, str] = {
    "op_ordering": "op-order",
    "shared_generic_effect_adapter": "effect-adapter",
    "timeline_plane": "timeline",
    "absent": "absent",
}


def _has_def(source_path: Path, def_name: Optional[str]) -> bool:
    """True iff ``source_path`` defines a function/method named ``def_name``.

    ``def_name is None`` means "presence is asserted by the path existing"
    (used for FI's own-fold conservation, which has no single canonical def
    name the scan keys on)."""
    if not source_path.exists():
        return False
    if def_name is None:
        return True
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == def_name:
            return True
    return False


def _module_tail_matches(dotted: str, module_names: Tuple[str, ...]) -> bool:
    """True iff the final component of a dotted import path is one of ``module_names``."""
    tail = dotted.rsplit(".", 1)[-1]
    return tail in module_names


def _package_imports_any_module(package_dir: Path, module_names: Tuple[str, ...]) -> bool:
    """True iff any module in ``package_dir`` imports one of ``module_names``.

    A real ``import``/``from ... import`` node whose dotted path's final
    component is one of ``module_names`` is the carrier-presence signal (e.g.
    ``from lawvm.core.op_ordering import order_ops``). Reads source via AST — no
    frontend code is executed, and docstring/comment mentions are ignored
    (they are not import nodes).
    """
    if not package_dir.is_dir():
        return False
    for py in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _module_tail_matches(node.module, module_names)
            ):
                return True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _module_tail_matches(alias.name, module_names):
                        return True
    return False


def _same_moment_path_for_frontend(frontend: FrontendId) -> SameMomentPathKind:
    """Return the precise shared same-moment path kind for one frontend.

    The boolean carrier answers "does this frontend participate in the shared
    cross-act same-moment family?". This typed path keeps the phase boundary
    visible: UK delegates the shared generic algorithm over ``UKEffectRecord``s,
    before op lowering, while the op frontends use ``op_ordering.order_ops``.
    FI's same-moment behavior is timeline-plane and intentionally outside this
    carrier.
    """
    if frontend == "fi":
        return "timeline_plane"
    package_dir = _FRONTEND_PACKAGE[frontend]
    if _package_imports_any_module(package_dir, ("op_ordering",)):
        return "op_ordering"
    if _package_calls_name(package_dir, "detect_same_moment_conflict_groups_generic"):
        return "shared_generic_effect_adapter"
    if _package_imports_any_module(package_dir, ("cross_act_same_moment",)):
        return "shared_generic_effect_adapter"
    return "absent"


def _package_calls_name(package_dir: Path, call_name: str) -> bool:
    """True iff any module in ``package_dir`` calls ``call_name(...)`` (by name).

    Used for the per-op ``WriteReceipt(...)`` constructor presence — keyed on the
    real construction site, not a hard-coded module path, so the carrier signal
    tracks the code. Reads source via AST; no frontend code is executed.
    """
    if not package_dir.is_dir():
        return False
    for py in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute)
                else None
            )
            if name == call_name:
                return True
    return False


def _carriers_for_frontend(frontend: FrontendId) -> Dict[str, bool]:
    conserved = _CONSERVED_WRAPPER_DEF[frontend]
    has_conserved = conserved is not None and _has_def(conserved[0], conserved[1])

    has_receipt = _package_calls_name(_FRONTEND_PACKAGE[frontend], _WRITE_RECEIPT_CTOR)

    same_moment_path = _same_moment_path_for_frontend(frontend)
    has_same_moment = same_moment_path in (
        "op_ordering",
        "shared_generic_effect_adapter",
    )

    return {
        "conserved_wrapper": has_conserved,
        "write_receipt": has_receipt,
        "same_moment_detector": has_same_moment,
    }


# ── FI reference upper bound ───────────────────────────────────────────────────


def _fi_reference_gates() -> Dict[str, GateMode]:
    """FI's per-op battery blocks on all four gates under strict mode.

    Read by reference from ``finland/apply_resolved_op._enforce_per_op_apply_authority``
    (LS-01/LS-03/EV-05 strict block + AM-01 provenance-acceptance block); the FI
    frontend is the registry's stated UPPER BOUND and is never edited. We
    confirm the battery exists at scan time rather than hard-coding blind.
    """
    fi_source = _SRC_ROOT / "finland" / "apply_resolved_op.py"
    battery = _has_def(fi_source, "_enforce_per_op_apply_authority")
    if not battery:
        raise ValueError(
            "FI reference battery _enforce_per_op_apply_authority not found; "
            "the parity audit's upper bound is unverifiable"
        )
    # All four FI gates block under strict (verified present by name below).
    for gate_def in (
        "_gate_mutation_boundary_at_op",
        "_gate_occupancy_transition_at_op",
        "_gate_execution_authorization_at_op",
        "_gate_provenance_acceptance_at_op",
    ):
        if not _has_def(fi_source, gate_def):
            raise ValueError(f"FI reference battery gate {gate_def} not found")
    return {inv: "block" for inv in PER_UNIT_INVARIANTS}


def _absent_apply_seam_gates() -> Dict[str, GateMode]:
    """Per-unit apply-seam modes for a frontend with no ApplyProfile seam."""
    return {inv: "absent" for inv in PER_UNIT_INVARIANTS}


# ── Public builders ────────────────────────────────────────────────────────────


def build_parity_matrix() -> ParityMatrix:
    """Build the invariant × frontend parity matrix from REAL registrations.

    FI is read as the reference upper bound (its per-op battery); the
    ApplyProfile frontends are read by AST-scanning their construction sites +
    the real carrier code surface. NZ has no ApplyProfile seam today, so it is
    represented explicitly as apply-seam ``absent`` while its carriers are still
    read from the real code surface. The returned matrix is total over
    :data:`KNOWN_FRONTENDS`.
    """
    rows: Dict[FrontendId, FrontendGates] = {}

    # FI — reference upper bound (battery, not an ApplyProfile).
    rows["fi"] = FrontendGates(
        frontend="fi",
        modes=_fi_reference_gates(),
        carriers=_carriers_for_frontend("fi"),
        same_moment_path=_same_moment_path_for_frontend("fi"),
        source="finland/apply_resolved_op.py::_enforce_per_op_apply_authority (reference)",
    )

    # The ApplyProfile frontends.
    for fe in _APPLY_PROFILE_FRONTENDS:
        source_path = _PROFILE_SOURCE[fe]
        lits = _scan_apply_profile_call(source_path)
        rows[fe] = FrontendGates(
            frontend=fe,
            modes=_gates_from_literals(fe, lits),
            carriers=_carriers_for_frontend(fe),
            same_moment_path=_same_moment_path_for_frontend(fe),
            source=str(source_path.relative_to(_REPO_ROOT)),
        )

    # NZ — first-class frontend, but no ApplyProfile apply seam.
    rows["nz"] = FrontendGates(
        frontend="nz",
        modes=_absent_apply_seam_gates(),
        carriers=_carriers_for_frontend("nz"),
        same_moment_path=_same_moment_path_for_frontend("nz"),
        source="new_zealand/chain_replay.py (no ApplyProfile seam)",
    )

    return ParityMatrix(rows=rows)


# ── Divergence classification ──────────────────────────────────────────────────

#: A coarse rank so we can speak of "stronger" / "weaker" enforcement.
_MODE_STRENGTH: Dict[str, int] = {"absent": 0, "off": 1, "observe": 2, "block": 3}

#: Per-invariant rationale: is a divergence a genuine should-fix, or a justified
#: jurisdiction difference? Keyed by (invariant, kind).
_RATIONALES: Dict[Tuple[str, str], str] = {
    ("LS-03", "enforced-here"): (
        "JUSTIFIED jurisdiction difference: EE models whole-section occupancy "
        "(kehtetu tombstone) and measured its corpus occupancy-clean before "
        "flipping to block; NO/SE model NO occupancy and correctly stay no-op. "
        "The path to parity is per-frontend measure-then-promote, not a uniform flip."
    ),
    ("EV-05", "observe-here"): (
        "PARTLY-CLOSED: the proof carrier on core/ir.LegalOperation now EXISTS "
        "(the framework change this row used to name as the prerequisite). EE "
        "wires a real authorization_resolver and mints proofs from each op's "
        "amending-act identity, so EE's measured firewall-hole residue is ~0% "
        "(notes/PROOF_CARRIER_FINDINGS.md); the other siblings still inherit the "
        "no-op resolver and sit at the ~100% hole. All six OBSERVE (no per-profile "
        "EV-05 block disposition; FI alone BLOCKS via its strict battery). Parity "
        "is now per-frontend: mint proofs, measure the residue, then promote."
    ),
    ("AM-01", "absent-here"): (
        "NOT-yet-a-fix: provenance-acceptance is FI-only today (typed OpProvenance "
        "is FI-owned); the seam hook exists but no sibling mints typed provenance, "
        "so the gate is a no-op there. Parity needs a per-frontend provenance rider."
    ),
    ("AM-01", "enforced-here"): (
        "JUSTIFIED first-mover (OBSERVE, not block): EE is the first non-FI "
        "frontend to wire a real provenance_resolver — it computes the "
        "core-neutral OpAcceptance from its OWN scope_confidence rung (inferred/"
        "fallback => Recovered/not-admitted; explicit/none => Parsed/admitted), "
        "without importing finland/. It OBSERVES (routes to AppliedOp.observations) "
        "and measured a real Recovered-op residue over its corpus "
        "(notes/PROOF_CARRIER_FINDINGS.md); the siblings stay off (no provenance "
        "rider). EE is not flipped to block — observe-first measure-then-promote, "
        "the same discipline as its LS-03/LS-01 flips."
    ),
    ("LS-01", "enforced-here"): (
        "JUSTIFIED jurisdiction difference: EE closed its boundary declaration "
        "artifact (the seam read chapter-nested writes of a flat-targeted "
        "whole-section op as out-of-boundary), measured its corpus "
        "boundary-clean, and flipped LS-01 to block — the SECOND enforcing "
        "apply-seam gate, after LS-03 occupancy. NO/SE cannot verify a real "
        "corpus in this environment and UK's recovery retarget is declared but "
        "not yet corpus-proven; the path to parity is per-frontend "
        "measure-then-promote, not a uniform flip."
    ),
    ("LS-01", "observe-here"): (
        "CONVERGED (observe-parity): every tree sibling runs boundary_mode='off' "
        "with the seam as the single always-on OBSERVER (B-enforcement inc 2). "
        "FI blocks (strict battery). The gap is the staged observe->block promotion, "
        "gated on a clean per-frontend bench — a roadmap step, not a silent hole."
    ),
    ("LS-01", "absent-here"): (
        "CONVERGED at the seam, NOT a hole: the profile field reads "
        "boundary_mode='off' for every tree sibling, but the seam runs the "
        "ALWAYS-ON mutation-boundary OBSERVER underneath (B-enforcement inc 2) "
        "and routes it to AppliedOp.observations. FI blocks (strict battery); "
        "the staged observe->block promotion is gated on a clean per-frontend "
        "bench — a roadmap step, not a silent divergence."
    ),
}


def _rationale(invariant: str, kind: DivergenceKind) -> str:
    return _RATIONALES.get(
        (invariant, kind),
        f"{INVARIANT_LABEL.get(invariant, invariant)}: mode divergence surfaced "
        "for review (no pre-classified rationale).",
    )


def classify_divergences(matrix: ParityMatrix) -> Tuple[InvariantCoverageDivergence, ...]:
    """Emit one :class:`InvariantCoverageDivergence` per real cross-frontend gap.

    A divergence is surfaced when, for a per-unit invariant, NOT every frontend
    shares the same mode. Outliers are classified relative to the majority
    baseline among all non-FI siblings, and stronger/weaker outliers become
    separate typed rows. That keeps NZ's no-ApplyProfile ``absent`` seam visible
    instead of merging it into EE's stronger ``block`` outlier. FI, the reference
    upper bound, is reported in the mode map but does not by itself constitute a
    divergence — it is the ceiling everything is measured against. Carrier
    absences (a sibling missing a carrier its peers have) are surfaced as
    ``absent-here`` rows.
    """
    out: list[InvariantCoverageDivergence] = []
    siblings: Tuple[FrontendId, ...] = tuple(
        fe for fe in KNOWN_FRONTENDS if fe != "fi"
    )

    # Per-unit gate divergences (among all non-FI siblings, including NZ's
    # explicit no-ApplyProfile row).
    for inv in PER_UNIT_INVARIANTS:
        modes = matrix.modes_for(inv)
        sibling_modes: Dict[str, GateMode] = {fe: modes[fe] for fe in siblings}
        distinct = set(sibling_modes.values())
        if len(distinct) <= 1:
            # All siblings agree; FI-vs-siblings is still informative when FI
            # blocks and siblings only observe/off — surface that as observe-here.
            sib_mode = next(iter(distinct))
            if modes["fi"] == "block" and sib_mode in ("observe", "off"):
                kind: DivergenceKind = "observe-here" if sib_mode == "observe" else "absent-here"
                out.append(
                    InvariantCoverageDivergence(
                        invariant=inv,
                        invariant_label=INVARIANT_LABEL[inv],
                        mode_map={fe: modes[fe] for fe in KNOWN_FRONTENDS},
                        kind=kind,
                        divergent_frontends=siblings,
                        baseline_mode="block (fi reference)",
                        rationale=_rationale(inv, kind),
                    )
                )
            continue

        # Siblings disagree: find the baseline (the strict majority mode) and
        # surface stronger/weaker outliers as separate rows.
        baseline = _majority_mode(sibling_modes)
        stronger = tuple(
            fe
            for fe in siblings
            if _MODE_STRENGTH[sibling_modes[fe]] > _MODE_STRENGTH[baseline]
        )
        weaker = tuple(
            fe
            for fe in siblings
            if _MODE_STRENGTH[sibling_modes[fe]] < _MODE_STRENGTH[baseline]
        )
        for kind, outliers in (
            (cast(DivergenceKind, "enforced-here"), stronger),
            (cast(DivergenceKind, "absent-here"), weaker),
        ):
            if not outliers:
                continue
            out.append(
                InvariantCoverageDivergence(
                    invariant=inv,
                    invariant_label=INVARIANT_LABEL[inv],
                    mode_map={fe: modes[fe] for fe in KNOWN_FRONTENDS},
                    kind=kind,
                    divergent_frontends=outliers,
                    baseline_mode=baseline,
                    rationale=_rationale(inv, kind),
                )
            )

    # Carrier absences: a sibling missing a carrier the majority of its peers have.
    for carrier in CARRIER_PRESENCES:
        present = matrix.carriers_for(carrier)
        sibling_present = {fe: present[fe] for fe in siblings}
        have = [fe for fe, p in sibling_present.items() if p]
        lack = tuple(fe for fe, p in sibling_present.items() if not p)
        if have and lack:
            out.append(
                InvariantCoverageDivergence(
                    invariant=carrier,
                    invariant_label=INVARIANT_LABEL[carrier],
                    mode_map={
                        fe: ("present" if present[fe] else "absent") for fe in KNOWN_FRONTENDS
                    },
                    kind="absent-here",
                    divergent_frontends=lack,
                    baseline_mode="present",
                    rationale=_carrier_rationale(carrier, lack),
                )
            )

    return tuple(out)


def _majority_mode(modes: Mapping[str, GateMode]) -> str:
    """The most common mode; ties broken toward the weaker (lower-strength) mode."""
    counts: Dict[str, int] = {}
    for m in modes.values():
        counts[m] = counts.get(m, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], -_MODE_STRENGTH[kv[0]]))
    return best[0]


def _carrier_rationale(carrier: str, lack: Tuple[FrontendId, ...]) -> str:
    if carrier == "conserved_wrapper" and set(lack) == {"us"}:
        return (
            "JUSTIFIED: US conserves via its dry-run AGREE/RESIDUAL lane (a "
            "char-span metric), not an IR conserved wrapper; the carrier shape "
            "differs by metric, conservation is present in a different form."
        )
    if carrier == "write_receipt" and set(lack) == {"ee"}:
        return (
            "REVIEW: EE runs the seam with emit_receipts=False and has no "
            "dedicated EE WriteReceipt emitter; receipts are absent for EE where "
            "every other frontend emits them — a genuine receipt-coverage gap."
        )
    if carrier == "same_moment_detector":
        return (
            "MIXED: NO/SE/EE/EU route same-moment detection through the shared "
            "path (op_ordering.order_ops wrapping the cross-act detector, or a "
            "direct import); FI uses the timeline-plane same-moment (LS-15/16); "
            f"{', '.join(lack)} wire neither. Whether {', '.join(lack)} need a "
            "cross-act detector depends on its op model — review."
        )
    return f"{INVARIANT_LABEL.get(carrier, carrier)} absent in {', '.join(lack)} — review."


# ── Rendering (for the report + note) ──────────────────────────────────────────


def render_matrix(matrix: ParityMatrix) -> str:
    """Render the parity matrix as a fixed-width text table (invariant × frontend)."""
    cols = list(KNOWN_FRONTENDS)
    header = "invariant".ljust(34) + "".join(c.upper().rjust(9) for c in cols)
    lines: list[str] = [header, "-" * len(header)]
    for inv in PER_UNIT_INVARIANTS:
        label = f"{inv} {INVARIANT_LABEL[inv]}"
        row = label.ljust(34) + "".join(matrix.rows[c].modes[inv].rjust(9) for c in cols)
        lines.append(row)
    lines.append("-" * len(header))
    for carrier in CARRIER_PRESENCES:
        label = INVARIANT_LABEL[carrier]
        if carrier == "same_moment_detector":
            row = label.ljust(34) + "".join(
                _SAME_MOMENT_PATH_DISPLAY[
                    matrix.rows[c].same_moment_path
                ].rjust(15) for c in cols
            )
        else:
            row = label.ljust(34) + "".join(
                ("present" if matrix.rows[c].carriers[carrier] else "absent").rjust(9) for c in cols
            )
        lines.append(row)
    return "\n".join(lines)


def render_divergences(divergences: Tuple[InvariantCoverageDivergence, ...]) -> str:
    """Render the divergence findings as a readable block."""
    lines: list[str] = []
    for d in divergences:
        lines.append(f"[{d.finding_code}] {d.invariant} ({d.invariant_label}) — {d.kind}")
        lines.append(f"    divergent: {', '.join(d.divergent_frontends)}  baseline: {d.baseline_mode}")
        lines.append(f"    modes: {d.mode_map}")
        lines.append(f"    {d.rationale}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_report() -> str:
    """One-call report: the rendered matrix + the classified divergences."""
    matrix = build_parity_matrix()
    divergences = classify_divergences(matrix)
    parts = [
        "Cross-jurisdiction invariant-parity matrix (registry XP-06)",
        "=" * 60,
        "",
        render_matrix(matrix),
        "",
        f"INVARIANT_COVERAGE_DIVERGENCE rows: {len(divergences)}",
        "-" * 60,
        render_divergences(divergences),
    ]
    return "\n".join(parts)


if __name__ == "__main__":  # pragma: no cover - manual report entry point
    print(build_report())
