"""
Tests for the Judge Harness Protocol Contract.

The contract defines what a Judge Harness must produce so that
Saringan can accept or reject candidate harness result data
before using it as Judge Evidence.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# RED: tests that will fail until the contract module exists
# ---------------------------------------------------------------------------


def test_valid_harness_result_passes_validation() -> None:
    """A complete, well-formed harness result is accepted."""
    from saringan.judge_harness import validate_harness_result

    valid_result = {
        "summary": "The changes implement the feature correctly.",
        "scope_guard": {
            "verdict": "yes",
            "rationale": "All changed files map to the issue scope.",
        },
        "advisories": [
            {
                "kind": "debug_artifact",
                "file": "src/app.py",
                "line": 42,
                "snippet": "print('debug')",
            }
        ],
        "acceptance_criteria": [
            {
                "criterion": "Must handle empty input",
                "verdict": "yes",
                "rationale": "Input validation is present.",
            }
        ],
    }

    result = validate_harness_result(valid_result)

    assert result.summary == "The changes implement the feature correctly."
    assert result.scope_guard.verdict == "yes"
    assert result.scope_guard.rationale == "All changed files map to the issue scope."
    assert len(result.advisories) == 1
    assert result.advisories[0].kind == "debug_artifact"
    assert len(result.acceptance_criteria) == 1
    assert result.acceptance_criteria[0].verdict == "yes"


def test_minimal_harness_result_with_only_required_fields_passes() -> None:
    """A result with only summary + scope_guard (no advisories/criteria) is valid."""
    from saringan.judge_harness import validate_harness_result

    minimal_result = {
        "summary": "Minimal valid result.",
        "scope_guard": {
            "verdict": "idk",
            "rationale": "Unclear scope.",
        },
    }

    result = validate_harness_result(minimal_result)

    assert result.summary == "Minimal valid result."
    assert result.scope_guard.verdict == "idk"
    assert result.advisories == []
    assert result.acceptance_criteria == []


# ---------------------------------------------------------------------------
# Missing / invalid summary
# ---------------------------------------------------------------------------


def test_missing_summary_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="summary"):
        validate_harness_result(
            {
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
            }
        )


def test_empty_summary_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="summary"):
        validate_harness_result(
            {
                "summary": "",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
            }
        )


def test_summary_as_non_string_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="summary"):
        validate_harness_result(
            {
                "summary": 123,
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
            }
        )


# ---------------------------------------------------------------------------
# Missing / invalid scope_guard
# ---------------------------------------------------------------------------


def test_missing_scope_guard_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="scope_guard"):
        validate_harness_result({"summary": "ok"})


def test_scope_guard_as_non_dict_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="scope_guard"):
        validate_harness_result(
            {"summary": "ok", "scope_guard": "not-a-dict"}
        )


def test_invalid_scope_guard_verdict_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="verdict"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "maybe", "rationale": "hmm"},
            }
        )


def test_missing_scope_guard_verdict_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="verdict"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"rationale": "ok"},
            }
        )


def test_missing_scope_guard_rationale_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="rationale"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes"},
            }
        )


def test_empty_scope_guard_rationale_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="rationale"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "no", "rationale": ""},
            }
        )


# ---------------------------------------------------------------------------
# Malformed advisories
# ---------------------------------------------------------------------------


def test_advisories_as_non_list_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="advisories"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": "not-a-list",
            }
        )


def test_advisory_missing_kind_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="kind"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": [{"file": "x.py"}],
            }
        )


def test_advisory_with_empty_kind_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="kind"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": [{"kind": ""}],
            }
        )


def test_advisory_with_non_string_kind_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="kind"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": [{"kind": 42}],
            }
        )


def test_advisory_with_non_int_line_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="line"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": [{"kind": "style", "line": "not-an-int"}],
            }
        )


# ---------------------------------------------------------------------------
# Malformed acceptance criteria
# ---------------------------------------------------------------------------


def test_acceptance_criteria_as_non_list_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="acceptance_criteria"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": "not-a-list",
            }
        )


def test_criterion_missing_criterion_field_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="criterion"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [{"verdict": "yes", "rationale": "ok"}],
            }
        )


def test_criterion_with_empty_criterion_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="criterion"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {"criterion": "", "verdict": "yes", "rationale": "ok"}
                ],
            }
        )


def test_criterion_missing_verdict_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="verdict"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {"criterion": "test", "rationale": "ok"}
                ],
            }
        )


def test_criterion_with_invalid_verdict_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="verdict"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {"criterion": "test", "verdict": "nope", "rationale": "ok"}
                ],
            }
        )


def test_criterion_missing_rationale_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="rationale"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {"criterion": "test", "verdict": "yes"}
                ],
            }
        )


def test_criterion_with_empty_rationale_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError, match="rationale"):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {"criterion": "test", "verdict": "yes", "rationale": ""}
                ],
            }
        )


# ---------------------------------------------------------------------------
# Unknown / extra fields
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_is_rejected() -> None:
    """Extra fields at the top level should be rejected (strict contract)."""
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "changed_files": ["file.py"],  # domain evidence, not harness output
            }
        )


def test_unknown_field_in_scope_guard_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {
                    "verdict": "yes",
                    "rationale": "ok",
                    "confidence": 0.95,  # not in the contract
                },
            }
        )


def test_unknown_field_in_advisory_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "advisories": [
                    {
                        "kind": "style",
                        "severity": "high",  # not in the contract
                    }
                ],
            }
        )


def test_unknown_field_in_acceptance_criterion_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError):
        validate_harness_result(
            {
                "summary": "ok",
                "scope_guard": {"verdict": "yes", "rationale": "ok"},
                "acceptance_criteria": [
                    {
                        "criterion": "test",
                        "verdict": "yes",
                        "rationale": "ok",
                        "priority": 1,  # not in the contract
                    }
                ],
            }
        )


# ---------------------------------------------------------------------------
# Not a dict at all
# ---------------------------------------------------------------------------


def test_non_dict_input_is_rejected() -> None:
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    with pytest.raises(HarnessValidationError):
        validate_harness_result("not a dict")

    with pytest.raises(HarnessValidationError):
        validate_harness_result(None)

    with pytest.raises(HarnessValidationError):
        validate_harness_result(42)


# ---------------------------------------------------------------------------
# idk verdicts are valid
# ---------------------------------------------------------------------------


def test_idk_scope_guard_verdict_is_valid() -> None:
    from saringan.judge_harness import validate_harness_result

    result = validate_harness_result(
        {
            "summary": "Uncertain about scope.",
            "scope_guard": {"verdict": "idk", "rationale": "Not enough context."},
        }
    )

    assert result.scope_guard.verdict == "idk"


def test_idk_acceptance_criterion_verdict_is_valid() -> None:
    from saringan.judge_harness import validate_harness_result

    result = validate_harness_result(
        {
            "summary": "Uncertain.",
            "scope_guard": {"verdict": "yes", "rationale": "ok"},
            "acceptance_criteria": [
                {
                    "criterion": "Must handle edge cases",
                    "verdict": "idk",
                    "rationale": "Cannot determine from diff.",
                }
            ],
        }
    )

    assert result.acceptance_criteria[0].verdict == "idk"
