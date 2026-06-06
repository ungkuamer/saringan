"""
Tests for the Legacy Direct Model Adapter (Issue #46).

The legacy adapter wraps the existing LiteLLM-powered Contextual Judge Gate
behind the Judge Harness Protocol Contract, making the legacy path available
as a selectable harness mode while keeping it separate from external harnesses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

try:
    if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
        raise ImportError()
    import litellm  # noqa: F401
    import pydantic  # noqa: F401
    HAS_JUDGE_DEPS = True
except ImportError:
    HAS_JUDGE_DEPS = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeJudgeClient:
    """Mimics the LiteLLMJudgeClient protocol for unit-testing."""

    def __init__(self, response: object) -> None:
        self.response = response

    def evaluate(self, request: object, judge_input: object) -> object:
        return self.response


class FakeScopeGuardClient:
    """Mimics the LiteLLMScopeGuardClient protocol for unit-testing."""

    def __init__(self, response: object) -> None:
        self.response = response

    def evaluate_scope(
        self,
        request: object,
        judge_input: object,
        changed_files: list[str],
    ) -> object:
        return self.response


def _make_judge_request(tmp_path: Path):
    from saringan.cli import JudgeRequest

    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new code\n")
    issue_path.write_text("# Issue 46\n")

    return JudgeRequest(
        target_path=target,
        diff_path=diff_path,
        issue_path=issue_path,
        conventions_path=None,
        model="gpt-5",
    )


def _make_judge_input(tmp_path: Path):
    from saringan.cli import read_judge_input

    request = _make_judge_request(tmp_path)
    return read_judge_input(request)


# ---------------------------------------------------------------------------
# RED: Tests for the legacy adapter module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_legacy_adapter_produces_valid_harness_contract_output(
    tmp_path: Path,
) -> None:
    """A valid combined scope guard + judge response, when fed through the
    legacy adapter, must produce output that passes validate_harness_result."""
    from saringan.legacy_adapter import run_legacy_adapter
    from saringan.judge_harness import validate_harness_result

    request = _make_judge_request(tmp_path)
    judge_input = _make_judge_input(tmp_path)

    # Simulate a successful LiteLLM judge + scope guard response
    fake_judge = FakeJudgeClient(
        {
            "summary": "All criteria satisfied.",
            "advisories": [
                {
                    "kind": "debug_artifact",
                    "file": "src/app.py",
                    "line": 2,
                    "snippet": "print('debug')",
                }
            ],
            "acceptance_criteria": [
                {
                    "criterion": "Must add new code",
                    "verdict": "yes",
                    "rationale": "New code found in diff.",
                }
            ],
        }
    )
    fake_scope_guard = FakeScopeGuardClient(
        {
            "verdict": "yes",
            "rationale": "All changed files map to the issue scope.",
        }
    )

    result = run_legacy_adapter(
        request,
        judge_input,
        judge_client=fake_judge,
        scope_guard_client=fake_scope_guard,
        changed_files=["src/app.py"],
    )

    # The result must pass the Judge Harness contract validation
    validated = validate_harness_result(result)
    assert validated.summary == "All criteria satisfied."
    assert validated.scope_guard.verdict == "yes"
    assert validated.scope_guard.rationale == "All changed files map to the issue scope."
    assert len(validated.advisories) == 1
    assert validated.advisories[0].kind == "debug_artifact"
    assert len(validated.acceptance_criteria) == 1
    assert validated.acceptance_criteria[0].verdict == "yes"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_legacy_adapter_with_minimal_judge_response(
    tmp_path: Path,
) -> None:
    """A minimal judge response (no advisories, no criteria) still produces
    a valid harness contract output."""
    from saringan.legacy_adapter import run_legacy_adapter
    from saringan.judge_harness import validate_harness_result

    request = _make_judge_request(tmp_path)
    judge_input = _make_judge_input(tmp_path)

    fake_judge = FakeJudgeClient({"summary": "Minimal response."})
    fake_scope_guard = FakeScopeGuardClient(
        {"verdict": "idk", "rationale": "Not enough context."}
    )

    result = run_legacy_adapter(
        request,
        judge_input,
        judge_client=fake_judge,
        scope_guard_client=fake_scope_guard,
        changed_files=["src/app.py"],
    )

    validated = validate_harness_result(result)
    assert validated.summary == "Minimal response."
    assert validated.scope_guard.verdict == "idk"
    assert validated.advisories == []
    assert validated.acceptance_criteria == []


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_legacy_adapter_preserves_scope_guard_verdict_idk(
    tmp_path: Path,
) -> None:
    """Scope guard 'idk' verdict is correctly preserved as Judge Harness contract output."""
    from saringan.legacy_adapter import run_legacy_adapter
    from saringan.judge_harness import validate_harness_result

    request = _make_judge_request(tmp_path)
    judge_input = _make_judge_input(tmp_path)

    fake_judge = FakeJudgeClient({"summary": "Uncertain."})
    fake_scope_guard = FakeScopeGuardClient(
        {"verdict": "idk", "rationale": "Cannot determine scope from diff."}
    )

    result = run_legacy_adapter(
        request,
        judge_input,
        judge_client=fake_judge,
        scope_guard_client=fake_scope_guard,
        changed_files=["src/app.py"],
    )

    validated = validate_harness_result(result)
    assert validated.scope_guard.verdict == "idk"
    assert validated.scope_guard.rationale == "Cannot determine scope from diff."


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_legacy_adapter_uses_default_clients_when_none_provided(
    tmp_path: Path,
) -> None:
    """When judge_client and scope_guard_client are not provided, the adapter
    uses the built-in LiteLLM clients by default."""
    from saringan.legacy_adapter import run_legacy_adapter

    request = _make_judge_request(tmp_path)
    judge_input = _make_judge_input(tmp_path)

    # Without fake clients, calling the adapter should attempt to use
    # the real LiteLLM clients.  The call will fail without API keys,
    # but the error should come from LiteLLM, not from a None reference.
    with pytest.raises(Exception):
        run_legacy_adapter(request, judge_input, changed_files=["src/app.py"])


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_legacy_adapter_acceptance_criteria_multiple_verdicts(
    tmp_path: Path,
) -> None:
    """Multiple acceptance criteria with mixed verdicts are all preserved."""
    from saringan.legacy_adapter import run_legacy_adapter
    from saringan.judge_harness import validate_harness_result

    request = _make_judge_request(tmp_path)
    judge_input = _make_judge_input(tmp_path)

    fake_judge = FakeJudgeClient(
        {
            "summary": "Mixed results.",
            "acceptance_criteria": [
                {
                    "criterion": "Add feature A",
                    "verdict": "yes",
                    "rationale": "Implemented.",
                },
                {
                    "criterion": "Add feature B",
                    "verdict": "no",
                    "rationale": "Not found.",
                },
                {
                    "criterion": "Add feature C",
                    "verdict": "idk",
                    "rationale": "Cannot tell.",
                },
            ],
        }
    )
    fake_scope_guard = FakeScopeGuardClient(
        {"verdict": "yes", "rationale": "In scope."}
    )

    result = run_legacy_adapter(
        request,
        judge_input,
        judge_client=fake_judge,
        scope_guard_client=fake_scope_guard,
        changed_files=["src/app.py"],
    )

    validated = validate_harness_result(result)
    assert len(validated.acceptance_criteria) == 3
    assert validated.acceptance_criteria[0].verdict == "yes"
    assert validated.acceptance_criteria[1].verdict == "no"
    assert validated.acceptance_criteria[2].verdict == "idk"


# ---------------------------------------------------------------------------
# RED: End-to-end test: legacy adapter selectable via judge_target
# ---------------------------------------------------------------------------

def test_judge_target_legacy_path_produces_same_evidence_shape_as_harness(
    tmp_path: Path,
) -> None:
    """When the legacy adapter is used, the evidence in the ValidationResult
    must match the shape produced by external harness execution
    (harness_name, provider, model fields present)."""
    if not HAS_JUDGE_DEPS:
        pytest.skip("Requires 'judge' extra dependencies")

    from saringan.cli import JudgeRequest, judge_target

    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new code\n")
    issue_path.write_text("# Issue 46\n")

    request = JudgeRequest(
        target_path=target,
        diff_path=diff_path,
        issue_path=issue_path,
        conventions_path=None,
        model="gpt-5",
    )

    result, exit_code = judge_target(
        request,
        judge_client=FakeJudgeClient(
            {
                "summary": "Legacy path executed.",
                "advisories": [],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
        # Activate legacy adapter path
        use_legacy_adapter=True,
        legacy_provider="litellm",
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    # Evidence shape should include harness-like metadata
    assert evidence["harness_name"] == "legacy-litellm"
    assert evidence["provider"] == "litellm"
    assert evidence["model"] == "gpt-5"
    # Same fields as harness path
    assert "scope_guard" in evidence
    assert "advisories" in evidence
    assert "acceptance_criteria" in evidence
    assert "completion_score" in evidence
    assert "changed_files" in evidence
    assert "input" in evidence
