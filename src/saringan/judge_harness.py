"""
Judge Harness Protocol Contract.

Saringan-owned contract that defines what a Judge Harness must produce.
Saringan validates candidate harness result data against this contract before
accepting it as Judge Evidence.  Invalid, missing, or nonconforming output is
rejected as an Environment Failure with a clear message.

Domain-derived evidence (changed files, target paths, invocation timing,
Completion Score) stays outside this contract and remains under Saringan
control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


VALID_VERDICTS = frozenset({"yes", "no", "idk"})
ALLOWED_TOP_LEVEL_FIELDS = frozenset(
    {"summary", "scope_guard", "advisories", "acceptance_criteria"}
)
ALLOWED_SCOPE_GUARD_FIELDS = frozenset({"verdict", "rationale"})
ALLOWED_ADVISORY_FIELDS = frozenset({"kind", "file", "line", "snippet"})
ALLOWED_CRITERION_FIELDS = frozenset({"criterion", "verdict", "rationale"})


class HarnessValidationError(ValueError):
    """Raised when a candidate harness result fails validation.

    The message describes the specific validation failure (e.g. missing
    required field, invalid verdict value, malformed advisory) so that
    callers can surface it as an Environment Failure.
    """


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


@dataclass
class ScopeGuardVerdict:
    verdict: Literal["yes", "no", "idk"]
    rationale: str


@dataclass
class JudgeAdvisory:
    kind: str
    file: str | None = None
    line: int | None = None
    snippet: str | None = None


@dataclass
class AcceptanceCriterion:
    criterion: str
    verdict: Literal["yes", "no", "idk"]
    rationale: str


@dataclass
class JudgeHarnessResult:
    """The contract a Judge Harness must fulfil.

    This is the *only* data Saringan accepts from a harness.  Domain-derived
    evidence such as changed files, target paths, timing, and Completion
    Score is computed by Saringan *outside* the harness and is never part of
    this contract.
    """

    summary: str
    scope_guard: ScopeGuardVerdict
    advisories: list[JudgeAdvisory] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_type(value: Any, expected_type: type, field_path: str) -> None:
    if not isinstance(value, expected_type):
        raise HarnessValidationError(
            f"Invalid type for {field_path}: expected {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )


def _check_unknown_fields(data: dict[str, Any], allowed: frozenset[str], path: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise HarnessValidationError(
            f"Unknown field(s) in {path}: {', '.join(extra)}"
        )


def _validate_scope_guard(raw: Any) -> ScopeGuardVerdict:
    _check_type(raw, dict, "scope_guard")
    _check_unknown_fields(raw, ALLOWED_SCOPE_GUARD_FIELDS, "scope_guard")

    verdict = raw.get("verdict")
    if verdict is None:
        raise HarnessValidationError(
            "Missing required field 'verdict' in scope_guard"
        )
    if verdict not in VALID_VERDICTS:
        raise HarnessValidationError(
            f"Invalid scope_guard verdict: {verdict!r}. "
            f"Must be one of: yes, no, idk"
        )

    rationale = raw.get("rationale")
    if rationale is None:
        raise HarnessValidationError(
            "Missing required field 'rationale' in scope_guard"
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise HarnessValidationError(
            "scope_guard rationale must be a non-empty string"
        )

    return ScopeGuardVerdict(verdict=verdict, rationale=rationale)


def _validate_advisory(raw: Any, index: int) -> JudgeAdvisory:
    _check_type(raw, dict, f"advisories[{index}]")
    _check_unknown_fields(raw, ALLOWED_ADVISORY_FIELDS, f"advisories[{index}]")

    kind = raw.get("kind")
    if kind is None:
        raise HarnessValidationError(
            f"Missing required field 'kind' in advisories[{index}]"
        )
    if not isinstance(kind, str) or not kind.strip():
        raise HarnessValidationError(
            f"advisories[{index}].kind must be a non-empty string"
        )

    line = raw.get("line")
    if line is not None and not isinstance(line, int):
        raise HarnessValidationError(
            f"advisories[{index}].line must be an integer or null"
        )

    return JudgeAdvisory(
        kind=kind,
        file=raw.get("file"),
        line=line,
        snippet=raw.get("snippet"),
    )


def _validate_acceptance_criterion(raw: Any, index: int) -> AcceptanceCriterion:
    _check_type(raw, dict, f"acceptance_criteria[{index}]")
    _check_unknown_fields(raw, ALLOWED_CRITERION_FIELDS, f"acceptance_criteria[{index}]")

    criterion = raw.get("criterion")
    if criterion is None:
        raise HarnessValidationError(
            f"Missing required field 'criterion' in acceptance_criteria[{index}]"
        )
    if not isinstance(criterion, str) or not criterion.strip():
        raise HarnessValidationError(
            f"acceptance_criteria[{index}].criterion must be a non-empty string"
        )

    verdict = raw.get("verdict")
    if verdict is None:
        raise HarnessValidationError(
            f"Missing required field 'verdict' in acceptance_criteria[{index}]"
        )
    if verdict not in VALID_VERDICTS:
        raise HarnessValidationError(
            f"Invalid acceptance_criteria[{index}].verdict: {verdict!r}. "
            f"Must be one of: yes, no, idk"
        )

    rationale = raw.get("rationale")
    if rationale is None:
        raise HarnessValidationError(
            f"Missing required field 'rationale' in acceptance_criteria[{index}]"
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise HarnessValidationError(
            f"acceptance_criteria[{index}].rationale must be a non-empty string"
        )

    return AcceptanceCriterion(criterion=criterion, verdict=verdict, rationale=rationale)


def validate_harness_result(raw: Any) -> JudgeHarnessResult:
    """Validate candidate harness result data against the Judge Harness contract.

    Returns a validated :class:`JudgeHarnessResult` on success.

    Raises :class:`HarnessValidationError` with a descriptive message when
    the data is missing required fields, contains invalid values, or includes
    fields outside the contract.

    >>> validate_harness_result({
    ...     "summary": "ok",
    ...     "scope_guard": {"verdict": "yes", "rationale": "in scope"},
    ... })
    JudgeHarnessResult(summary='ok', ...)
    """
    if not isinstance(raw, dict):
        raise HarnessValidationError(
            f"Harness result must be a JSON object (dict), got {type(raw).__name__}"
        )

    _check_unknown_fields(raw, ALLOWED_TOP_LEVEL_FIELDS, "harness result")

    # --- summary ---
    summary = raw.get("summary")
    if summary is None:
        raise HarnessValidationError(
            "Missing required field 'summary' in harness result"
        )
    if not isinstance(summary, str) or not summary.strip():
        raise HarnessValidationError("summary must be a non-empty string")

    # --- scope_guard ---
    scope_guard_raw = raw.get("scope_guard")
    if scope_guard_raw is None:
        raise HarnessValidationError(
            "Missing required field 'scope_guard' in harness result"
        )
    scope_guard = _validate_scope_guard(scope_guard_raw)

    # --- advisories ---
    advisories: list[JudgeAdvisory] = []
    advisories_raw = raw.get("advisories")
    if advisories_raw is not None:
        if not isinstance(advisories_raw, list):
            raise HarnessValidationError("advisories must be a list")
        for i, item in enumerate(advisories_raw):
            advisories.append(_validate_advisory(item, i))

    # --- acceptance_criteria ---
    criteria: list[AcceptanceCriterion] = []
    criteria_raw = raw.get("acceptance_criteria")
    if criteria_raw is not None:
        if not isinstance(criteria_raw, list):
            raise HarnessValidationError("acceptance_criteria must be a list")
        for i, item in enumerate(criteria_raw):
            criteria.append(_validate_acceptance_criterion(item, i))

    return JudgeHarnessResult(
        summary=summary,
        scope_guard=scope_guard,
        advisories=advisories,
        acceptance_criteria=criteria,
    )
