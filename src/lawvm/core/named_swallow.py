"""named_swallow — owned fail-loud primitive for the silent-exception swallow pattern.

AGENTS.md §1.10 forbids broad exception swallowing: every silent-exception
default must instead emit a distinct named diagnostic stating the concrete fix.
The pattern

    try:
        ...computational work...
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        <swallow silently to default>

landed independently at multiple sites across the codebase
(``finland/ops.py``, ``finland/graph.py`` x4, ``finland/frontend_observations.py``,
``finland/transparent_store.py``, ``finland/corpus.py``,
``finland/consolidated_artifacts.py``, ``estonia/spec_ledger_adapter.py``,
``new_zealand/dry_run.py``, ``tools/_worker_pool.py`` x2, ``sweden/grafter.py``).
Per AGENTS.md §2.6 (rule of three), it is overdue for crystallisation into a
single owned primitive. This module provides that primitive.

``named_swallow`` is a ``@contextmanager`` that:

1. Re-raises ``NameError``, ``TypeError``, ``AttributeError`` — these signal a
   real bug in the call site or its dependencies and MUST NOT be swallowed.
2. On any other ``Exception``: constructs a typed ``Finding`` carrying
   ``rule_id`` (the caller-stable id naming the swallow site),
   ``exception_type``, ``exception_message``, ``op_id`` (when applicable),
   ``clause_text`` (truncated to ~400 chars — AGENTS.md §1.10),
   ``source_artifact``, ``jurisdiction``, then emits it through the
   caller-supplied ``emit`` callable OR appends it to the caller-supplied
   ``findings_out`` list sink. Either channel is acceptable; the caller picks.
3. Suppresses the exception and yields ``default`` so the caller's
   ``as value`` binding is the default on swallowed failure.
4. Fail-loud ITSELF: if neither ``emit`` nor ``findings_out`` is wired and a
   swallow fires, raises :class:`NamedSwallowNonEmittingSinkError` — silent
   swallow of an unflushed swallowed exception is itself a §1.10 violation.

This is the proposed/computed+ owned primitive behind §1.10 (no-silent-default
ladder) and §2.6 (rule-of-three crystallisation). Findings emitted through
``named_swallow`` use the registered kind ``UNEXPECTED_PHASE_FAILURE``
(role="obligation", blocking=True, strict_fail enforcement); the per-site
``rule_id`` distinguishes the (currently 10+) migrated call sites.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding

__all__ = [
    "NAMED_SWALLOW_FINDING_KIND",
    "NamedSwallowNonEmittingSinkError",
    "build_named_swallow_finding",
    "log_emitter",
    "named_swallow",
    "swallow_call",
]

_logger = logging.getLogger(__name__)

#: Maximum chars of source text retained on a swallowed-exception diagnostic
#: (AGENTS.md §1.10: ~300-400 chars; values larger than this are truncated).
_CLAUSE_TEXT_MAX_CHARS = 400

#: The single Finding kind used to witness every ``named_swallow`` emission.
#: Registered in ``lawvm.core.observation_registry.FINDING_REGISTRY`` with
#: role="obligation", blocking=True, strict_fail enforcement.
NAMED_SWALLOW_FINDING_KIND = "UNEXPECTED_PHASE_FAILURE"

# Programming-bug exception classes that ``named_swallow`` MUST NOT swallow:
# these signal a real bug in the call site or its dependencies and must surface
# to the developer rather than be carried silently through the finding ledger.
_PROGRAMMING_BUG_EXCEPTIONS: tuple[type[BaseException], ...] = (
    NameError,
    TypeError,
    AttributeError,
)


T = TypeVar("T")


class NamedSwallowNonEmittingSinkError(RuntimeError):
    """A ``named_swallow`` swallow fired with no sink wired.

    The silencing of an exception without an owned typed emission is itself a
    §1.10 violation — silent swallow of an unflushed swallowed exception. The
    fix is for the caller to wire ``emit=`` or ``findings_out=`` so the typed
    Finding reaches its owner phase. Raised INSTEAD of returning the default in
    that case (never silent).

    Carries the typed Finding that could not be emitted as ``unemitted_finding``
    so a wrapping test or top-level error sink can still capture it.
    """

    def __init__(self, *, rule_id: str, unemitted_finding: Finding) -> None:
        self.rule_id = rule_id
        self.unemitted_finding = unemitted_finding
        super().__init__(
            f"named_swallow rule_id={rule_id!r} swallowed an unexpected "
            f"exception but neither emit= nor findings_out= was wired; the "
            f"typed Finding could not reach a sink and silent swallow is a "
            f"§1.10 violation. Wire emit=<Callable[[Finding], None]> or "
            f"findings_out=<list[Finding]> at this call site."
        )


def _truncate_clause_text(text: str | None) -> str:
    """Truncate the source-text snippet to ~400 chars with a marker when longer."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= _CLAUSE_TEXT_MAX_CHARS:
        return text
    return text[:_CLAUSE_TEXT_MAX_CHARS] + "…[truncated]"


def build_named_swallow_finding(
    *,
    rule_id: str,
    exception: BaseException,
    op_id: str | None = None,
    clause_text: str | None = None,
    source_artifact: str | None = None,
    jurisdiction: str | None = None,
    source_statute: str = "",
) -> Finding:
    """Construct the typed ``Finding`` for one swallowed exception.

    Detail mapping carries: ``rule_id`` (the caller-supplied stable id naming
    the swallow site — distinguishes the 10+ migrated sites), ``exception_type``
    (the concrete swallowed class), ``exception_message``
    (``str(exception)``), ``op_id`` (when applicable), ``clause_text``
    (truncated to ~400 chars — AGENTS.md §1.10), ``source_artifact``,
    ``jurisdiction``. The ``message`` field states the concrete fix in prose.

    All optional parameters default to ``None`` (omitted from the detail
    mapping when not supplied) so a swallow at a layer without an op_id /
    source_artifact in scope can still construct a typed witness carrying the
    fields it does have.
    """
    detail: dict[str, Any] = {
        "message": (
            f"named_swallow[{rule_id}] caught an unexpected "
            f"{type(exception).__name__} and swallowed the exception to its "
            f"declared default. The original exception_type and the offending "
            f"clause_text (truncated ~400 chars) are embedded so triage does "
            f"not require re-running extraction. Fix: route this residual to "
            f"the rule_id-named source-pathology family."
        ),
        "rule_id": rule_id,
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        "clause_text": _truncate_clause_text(clause_text),
    }
    if op_id is not None:
        detail["op_id"] = op_id
    if source_artifact is not None:
        detail["source_artifact"] = source_artifact
    if jurisdiction is not None:
        detail["jurisdiction"] = jurisdiction
    spec = get_finding_spec(NAMED_SWALLOW_FINDING_KIND)
    if spec is None:
        raise ValueError(
            f"Finding.kind={NAMED_SWALLOW_FINDING_KIND!r} is not registered "
            f"in lawvm.core.observation_registry.FINDING_REGISTRY; add the "
            f"FindingSpec so named_swallow can construct typed findings."
        )
    # ``FindingSpec.role`` is the wider ``FindingRegistryRole`` (includes "barrier"
    # for strict-mode taxonomy metadata), but ``Finding.role`` is the narrower
    # ``FindingRole`` (excludes "barrier" — barrier kinds belong only on the
    # registry/verdict rails, never as runtime Findings). NAMED_SWALLOW_FINDING_KIND
    # is registered with role="obligation", a member of both, so the cast is
    # sound at runtime — the narrowing is purely a static-type boundary.
    if spec.role == "barrier":
        raise ValueError(
            f"Finding.kind={NAMED_SWALLOW_FINDING_KIND!r} has registry role="
            f"'barrier' but named_swallow constructs a runtime Finding, which "
            f"cannot be a barrier-kind (barrier kinds have no runtime Finding.role)."
        )
    return Finding(
        kind=NAMED_SWALLOW_FINDING_KIND,
        role=spec.role,
        stage=spec.phase,
        blocking=True,
        source_statute=source_statute,
        detail=detail,
    )


def _emit_finding(
    finding: Finding,
    *,
    rule_id: str,
    emit: Callable[[Finding], None] | None,
    findings_out: list[Finding] | None,
) -> None:
    """Emit ``finding`` through ``emit`` OR a list sink — fail loud if neither.

    Either channel is acceptable; the caller picks. If neither sink is wired,
    the swallow cannot stay silent (§1.10): raise
    :class:`NamedSwallowNonEmittingSinkError` carrying the typed Finding that
    could not be emitted, so a wrapping test or top-level error sink can still
    capture it.
    """
    if emit is not None:
        emit(finding)
        return
    if findings_out is not None:
        findings_out.append(finding)
        return
    raise NamedSwallowNonEmittingSinkError(
        rule_id=rule_id, unemitted_finding=finding
    )


@contextmanager
def named_swallow(
    *,
    rule_id: str,
    default: T,
    op_id: str | None = None,
    clause_text: str | None = None,
    source_artifact: str | None = None,
    jurisdiction: str | None = None,
    source_statute: str = "",
    emit: Callable[[Finding], None] | None = None,
    findings_out: list[Finding] | None = None,
) -> Iterator[T]:
    """Fail-loud, owned context manager for the silent-exception swallow pattern.

    Programming bugs (``NameError``/``TypeError``/``AttributeError``) re-raise
    — never silent. Any other ``Exception`` is swallowed: a typed
    ``Finding(kind="UNEXPECTED_PHASE_FAILURE", blocking=True)`` is constructed
    with ``rule_id``, ``exception_type``, ``exception_message``, ``op_id``,
    ``clause_text`` (truncated ~400 chars), ``source_artifact``,
    ``jurisdiction``, ``source_statute`` embedded in ``detail``, and emitted
    via ``emit`` or appended to ``findings_out``. Either sink channel is
    acceptable; the caller picks. If neither sink is wired and a swallow fires,
    raises :class:`NamedSwallowNonEmittingSinkError` — silent swallow of an
    unflushed swallowed exception is itself a §1.10 violation (never silent).

    The yielded value is ``default``, so a caller doing:

        with named_swallow(rule_id="...", default=X, emit=...) as value:
            value = may_raise()  # if raises, value stays at X

    will see ``value == X`` on swallow. Callers that don't need value-binding
    may simply ignore the ``as`` clause and use the body to reassign an outer
    local initialised to the default — same semantics.

    Args:
        rule_id: stable id naming the swallow site (distinguishes the 10+
            migrated sites; embedded in Finding.detail).
        default: value yielded to the caller's ``as`` binding; on swallow the
            body's reassignment either does not run or is suppressed, so the
            binding stays at this default.
        op_id: when the swallow site is inside a per-op apply path, the op id
            being worked on (embedded in detail).
        clause_text: the verbatim source text / locator / op description that
            triggered the swallow (embedded, truncated ~400 chars; AGENTS.md
            §1.10: triaging a residual must never require re-running extraction).
        source_artifact: the source artifact id (e.g. farchive path) where the
            swallow fired.
        jurisdiction: the jurisdiction frontend that owns this swallow site
            ("fi", "ee", "uk", "no", "se", "nz", "us", "eu").
        source_statute: the source statute being processed when the swallow
            fired (forwarded to Finding.source_statute).
        emit: callable that receives the constructed Finding; alternative to
            ``findings_out``. Caller picks one.
        findings_out: list sink the constructed Finding is appended to;
            alternative to ``emit``. Caller picks one.

    Yields:
        ``default`` — the caller's ``as value`` binding.
    """
    try:
        yield default
    except _PROGRAMMING_BUG_EXCEPTIONS:
        # Programming bugs surface to the developer — never silent.
        raise
    except Exception as exc:
        finding = build_named_swallow_finding(
            rule_id=rule_id,
            exception=exc,
            op_id=op_id,
            clause_text=clause_text,
            source_artifact=source_artifact,
            jurisdiction=jurisdiction,
            source_statute=source_statute,
        )
        _emit_finding(
            finding,
            rule_id=rule_id,
            emit=emit,
            findings_out=findings_out,
        )
        _logger.debug(
            "named_swallow[%s] swallowed %s: %s",
            rule_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        # Suppress: the `as value` binding (if used) retains the `default`
        # yielded above; if the body raised before re-assigning, the caller's
        # local stays at its prior value (which should be the default-shaped
        # initial value the caller chose).
        return


def swallow_call(
    fn: Callable[[], T],
    *,
    rule_id: str,
    default: T,
    op_id: str | None = None,
    clause_text: str | None = None,
    source_artifact: str | None = None,
    jurisdiction: str | None = None,
    source_statute: str = "",
    emit: Callable[[Finding], None] | None = None,
    findings_out: list[Finding] | None = None,
) -> T:
    """Higher-order form of ``named_swallow`` for ``return X``-style swallows.

    Some migrated sites (e.g. ``finland/corpus.py:list_cached_consolidated_locators``,
    ``finland/consolidated_artifacts.py:extract_consolidated_xml_identity``,
    ``estonia/spec_ledger_adapter.py:_ee_resolve_as_of``,
    ``sweden/grafter.py:_pdf_to_text``) had the shape
    ``try: ...; except Exception: return <default>``
    — a contextmanager that suppresses but does not return-from-caller is
    awkward there. This helper runs ``fn`` inside ``named_swallow`` and returns
    either ``fn()``'s result OR ``default`` on swallow, with the same typed
    Finding emission.

    Equivalent to::

        with named_swallow(default=default, ...) as value:
            value = fn()
        return value
    """
    with named_swallow(
        rule_id=rule_id,
        default=default,
        op_id=op_id,
        clause_text=clause_text,
        source_artifact=source_artifact,
        jurisdiction=jurisdiction,
        source_statute=source_statute,
        emit=emit,
        findings_out=findings_out,
    ) as value:
        value = fn()
    return value


def log_emitter(
    *,
    logger: logging.Logger | None = None,
    level: int = logging.WARNING,
) -> Callable[[Finding], None]:
    """Build an ``emit`` callback that writes the typed Finding to a logger.

    Use this at utility sites that have no caller-supplied ``findings_out``
    sink — corpus/graph/store/adapter modules where the finding ledger is not
    in scope. The typed Finding is constructed and EVIDENCED (not silently
    swallowed), and ``WARNING`` level guarantees stderr visibility during
    normal runs (see AGENTS.md §1.10: "distinct named diagnostic"). Finding
    fields are emitted as keyword-style key=value pairs so a downstream
    structured logger can parse them; the truncated ``clause_text`` is also
    written so triaging the residual never requires re-running extraction.

    Callers that DO have an audit sink should pass ``findings_out=<list>``
    OR a typed ``emit=<callable>`` directly. This helper is the fallback for
    IO/utility sites with no other channel.
    """
    log = logger or _logger

    def _emit(finding: Finding) -> None:
        detail = finding.detail
        log.log(
            level,
            "named_swallow Finding: kind=%s rule_id=%s exception_type=%s "
            "exception_message=%s op_id=%s source_artifact=%s jurisdiction=%s "
            "clause_text=%r",
            finding.kind,
            detail.get("rule_id", ""),
            detail.get("exception_type", ""),
            detail.get("exception_message", ""),
            detail.get("op_id", ""),
            detail.get("source_artifact", ""),
            detail.get("jurisdiction", ""),
            detail.get("clause_text", ""),
        )

    return _emit
