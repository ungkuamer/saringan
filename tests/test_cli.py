from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import tomllib
import pytest
from saringan.cli import JudgeInput, JudgeRequest, ScopeGuardClient, judge_target

try:
    if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
        raise ImportError()
    import litellm
    import pydantic
    HAS_JUDGE_DEPS = True
except ImportError:
    HAS_JUDGE_DEPS = False


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = (
        src_path if not existing_pythonpath else f"{src_path}:{existing_pythonpath}"
    )
    return subprocess.run(
        [sys.executable, "-m", "saringan", *args],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
    )


def assert_check_outcome(
    payload: dict[str, object],
    *,
    expected_id: str,
    expected_stable_check_id: str,
    expected_status: str,
    expected_stdout: str,
    expected_stderr: str,
    expected_exit_code: int | None,
    expected_command: list[str],
    expected_working_directory: str,
) -> None:
    assert payload["check_outcomes"][0]["id"] == expected_id
    assert payload["check_outcomes"][0]["stable_check_id"] == expected_stable_check_id
    assert payload["check_outcomes"][0]["status"] == expected_status
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["stdout"] == expected_stdout
    assert evidence["stderr"] == expected_stderr
    assert evidence["exit_code"] == expected_exit_code
    assert evidence["command"] == expected_command
    assert evidence["working_directory"] == expected_working_directory
    assert evidence["duration_seconds"] >= 0


def test_validate_json_reports_passed_result_for_valid_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert payload["config_path"] == str((target / "saringan.toml").resolve())
    assert_check_outcome(
        payload,
        expected_id="fixture",
        expected_stable_check_id="command",
        expected_status="passed",
        expected_stdout="",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "exit 0"],
        expected_working_directory=str(target.resolve()),
    )
    assert payload["started_at"]
    assert payload["finished_at"]


class FakeJudgeClient:
    def __init__(self, response: object) -> None:
        self.response = response

    def evaluate(self, request: JudgeRequest, judge_input: JudgeInput) -> object:
        return self.response


class FakeScopeGuardClient:
    def __init__(self, response: object) -> None:
        self.response = response

    def evaluate_scope(
        self,
        request: JudgeRequest,
        judge_input: JudgeInput,
        changed_files: list[str],
    ) -> object:
        return self.response


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_judge_target_reports_validated_fake_client_result(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    conventions_path = tmp_path / "conventions.md"
    debug_line = "console.log('debug'); " + ("x" * 2500)
    diff_path.write_text(
        "\n".join(
            [
                "diff --git a/src/app.js b/src/app.js",
                "--- a/src/app.js",
                "+++ b/src/app.js",
                "@@ -1 +1,2 @@",
                "+print('debug')",
                f"+{debug_line}",
                "diff --git a/tests/test_app.py b/tests/test_app.py",
                "--- a/tests/test_app.py",
                "+++ b/tests/test_app.py",
                "@@ -1 +1 @@",
                "+assert True",
                "",
            ]
        )
    )
    issue_path.write_text("# Issue 21\n")
    conventions_path.write_text("# Conventions\n")

    result, exit_code = judge_target(
        JudgeRequest(
            target_path=target,
            diff_path=diff_path,
            issue_path=issue_path,
            conventions_path=conventions_path,
            model="gpt-5",
        ),
        judge_client=FakeJudgeClient(
            {
                "summary": "Contextual Judge Gate advisory skeleton executed.",
                "advisories": [
                    {
                        "kind": "debug_artifact",
                        "file": "src/app.js",
                        "line": 1,
                        "snippet": "print('debug')",
                    },
                    {
                        "kind": "debug_artifact",
                        "file": "src/app.js",
                        "line": 2,
                        "snippet": debug_line[:2000],
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "All changed files map to the issue scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert payload["started_at"]
    assert payload["finished_at"]
    assert len(payload["check_outcomes"]) == 1
    assert payload["check_outcomes"][0]["stable_check_id"] == "contextual_judge"
    assert payload["check_outcomes"][0]["status"] == "passed"
    assert payload["check_outcomes"][0]["blocking"] is False
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["diff_path"] == str(diff_path.resolve())
    assert evidence["issue_path"] == str(issue_path.resolve())
    assert evidence["conventions_path"] == str(conventions_path.resolve())
    assert evidence["model"] == "gpt-5"
    assert evidence["changed_files"] == ["src/app.js", "tests/test_app.py"]
    assert len(evidence["advisories"]) == 2
    assert evidence["advisories"][0]["kind"] == "debug_artifact"
    assert evidence["advisories"][0]["file"] == "src/app.js"
    assert evidence["advisories"][0]["line"] == 1
    assert evidence["advisories"][0]["snippet"] == "print('debug')"
    assert evidence["advisories"][1]["kind"] == "debug_artifact"
    assert evidence["advisories"][1]["line"] == 2
    assert len(evidence["advisories"][1]["snippet"]) == 2000
    assert evidence["scope_guard"]["verdict"] == "yes"
    assert evidence["scope_guard"]["rationale"] == "All changed files map to the issue scope."
    assert evidence["input"]["issue_text"] == "# Issue 21\n"
    assert evidence["input"]["diff_text"].startswith("diff --git a/src/app.js b/src/app.js")
    assert evidence["input"]["conventions_text"] == "# Conventions\n"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_judge_target_reports_environment_failure_for_invalid_structured_output(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 23\n")

    result, exit_code = judge_target(
        JudgeRequest(
            target_path=target,
            diff_path=diff_path,
            issue_path=issue_path,
            conventions_path=None,
            model="gpt-5",
        ),
        judge_client=FakeJudgeClient({"summary": "", "advisories": "not-an-array"}),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 2
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "error"
    assert payload["message"].startswith(
        "Judge model returned invalid structured output:"
    )


@pytest.mark.parametrize(
    ("missing_kind", "path_factory", "extra_args"),
    [
        (
            "target",
            lambda tmp_path: tmp_path / "missing-target",
            lambda tmp_path: [],
        ),
        (
            "diff",
            lambda tmp_path: tmp_path / "missing.diff",
            lambda tmp_path: [],
        ),
        (
            "issue",
            lambda tmp_path: tmp_path / "missing.md",
            lambda tmp_path: [],
        ),
        (
            "conventions",
            lambda tmp_path: tmp_path / "missing-conventions.md",
            lambda tmp_path: ["--conventions", str(tmp_path / "missing-conventions.md")],
        ),
    ],
)
def test_judge_reports_environment_failure_for_missing_inputs(
    tmp_path: Path,
    missing_kind: str,
    path_factory,
    extra_args,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 22\n")

    if missing_kind == "target":
        target_path = path_factory(tmp_path)
        expected_missing_path = target_path
    else:
        target_path = target
        expected_missing_path = path_factory(tmp_path)

    if missing_kind != "diff":
        diff_arg = str(diff_path)
    else:
        diff_arg = str(expected_missing_path)

    if missing_kind != "issue":
        issue_arg = str(issue_path)
    else:
        issue_arg = str(expected_missing_path)

    result = run_cli(
        "judge",
        str(target_path),
        "--diff",
        diff_arg,
        "--issue",
        issue_arg,
        "--model",
        "gpt-5",
        "--json",
        *extra_args(tmp_path),
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["target_path"] == str(target_path.resolve())
    assert payload["message"] == (
        f"Required judge input does not exist: {expected_missing_path.resolve()}"
    )


def test_judge_reports_environment_failure_when_judge_dependencies_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 23\n")

    monkeypatch.setenv("SARINGAN_FORCE_MISSING_JUDGE_DEPS", "1")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--model",
        "gpt-5",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == (
        "Judge dependencies are not installed. Reinstall with the 'judge' extra."
    )


def test_validate_json_reports_error_for_missing_target_path(tmp_path: Path) -> None:
    missing_target = tmp_path / "missing"

    result = run_cli("validate", str(missing_target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["target_path"] == str(missing_target.resolve())
    assert payload["config_path"] is None
    assert result.stderr


def test_validate_json_reports_failed_result_for_failing_check(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 1"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["check_outcomes"][0]["id"] == "smoke"
    assert payload["check_outcomes"][0]["status"] == "failed"


def test_pyproject_exposes_judge_extra_dependencies() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"

    payload = tomllib.loads(pyproject_path.read_text())

    judge_dependencies = payload["project"]["optional-dependencies"]["judge"]
    assert "litellm" in judge_dependencies
    assert "pydantic" in judge_dependencies


def test_validate_default_output_is_parseable_json_on_stdout(tmp_path: Path) -> None:
    """Without --json, stdout is still parseable Validation Result JSON."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target))

    assert result.returncode == 0
    # stdout must be parseable JSON (the Validation Result).
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    # Human-readable output goes to stderr.
    assert "Validation passed:" in result.stderr


def test_validate_json_writes_machine_output_to_stdout_and_progress_to_stderr(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert "Validating" in result.stderr


def test_validate_reports_error_when_config_file_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["config_path"] == str((target / "saringan.toml").resolve())


def test_validate_json_allows_overriding_config_path(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    custom_config = tmp_path / "custom.toml"
    custom_config.write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--config", str(custom_config), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert payload["config_path"] == str(custom_config.resolve())


def test_validate_reports_error_for_invalid_toml_config(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\n\n[[checks]]\n[')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["config_path"] == str((target / "saringan.toml").resolve())
    assert "Invalid configuration TOML" in payload["message"]


def test_validate_reports_error_when_schema_version_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        '[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["config_path"] == str((target / "saringan.toml").resolve())
    assert payload["message"] == "Missing required schema_version in configuration."


def test_validate_reports_error_when_schema_version_is_unsupported(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 2\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unsupported schema_version: 2"


def test_validate_reports_error_for_unknown_top_level_configuration_fields(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\nextra_field = true\n\n'
        '[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown top-level configuration fields: extra_field"


def test_validate_reports_error_when_check_id_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\ntype = "command"\ncommand = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Check is missing required id."


def test_validate_reports_error_for_duplicate_check_ids(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n\n[[checks]]\nid = "fixture"\n'
        'type = "command"\ncommand = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Duplicate check id: fixture"


def test_validate_reports_error_for_unsupported_check_type(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "unknown"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unsupported check type: unknown"


def test_validate_reports_error_when_command_is_not_argument_vector(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = "sh -c \'exit 0\'"\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Check 'fixture' command must be an argument vector."


def test_validate_reports_error_for_unknown_command_check_fields(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for command check: extra_field"


def test_validate_reports_failed_result_for_failing_command_check(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = ["sh", "-c", "printf fail-out; printf fail-err >&2; exit 9"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert_check_outcome(
        payload,
        expected_id="fixture",
        expected_stable_check_id="command",
        expected_status="failed",
        expected_stdout="fail-out",
        expected_stderr="fail-err",
        expected_exit_code=9,
        expected_command=["sh", "-c", "printf fail-out; printf fail-err >&2; exit 9"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_runs_checks_in_order_and_ignores_advisory_failures(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    order_file = target / "order.txt"
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "first"\ntype = "command"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"first\\\\n\\")"]\n\n'
        '[[checks]]\nid = "advisory-failure"\ntype = "command"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"second\\\\n\\"); raise SystemExit(7)"]\n\n'
        '[[checks]]\nid = "third"\ntype = "command"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"third\\\\n\\")"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert [outcome["id"] for outcome in payload["check_outcomes"]] == [
        "first",
        "advisory-failure",
        "third",
    ]
    assert [outcome["stable_check_id"] for outcome in payload["check_outcomes"]] == [
        "command",
        "command",
        "command",
    ]
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "passed",
        "failed",
        "passed",
    ]
    assert order_file.read_text() == "first\nsecond\nthird\n"


def test_validate_skips_checks_with_unsatisfied_dependencies(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "base"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 8"]\n\n'
        '[[checks]]\nid = "dependent"\ntype = "command"\ndepends_on = ["base"]\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "failed",
        "skipped",
    ]
    assert [outcome["stable_check_id"] for outcome in payload["check_outcomes"]] == [
        "command",
        "command",
    ]
    assert payload["check_outcomes"][1]["reason"] == "unsatisfied dependency"


def test_validate_reports_error_for_unknown_dependency_check_id(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "dependent"\ntype = "command"\ndepends_on = ["missing"]\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Check 'dependent' depends on unknown check id: missing"


def test_validate_supports_typed_javascript_lint_checks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-lint"\ntype = "javascript_lint"\n'
        'command = ["sh", "-c", "printf lint-ok"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="js-lint",
        expected_stable_check_id="javascript_lint",
        expected_status="passed",
        expected_stdout="lint-ok",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf lint-ok"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_accepts_deprecated_javascript_lint_alias(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-lint"\ntype = "javascript-lint"\n'
        'command = ["sh", "-c", "printf lint-ok"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="js-lint",
        expected_stable_check_id="javascript_lint",
        expected_status="passed",
        expected_stdout="lint-ok",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf lint-ok"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_runs_javascript_typed_checks_with_policy_and_dependencies(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    order_file = target / "js-order.txt"
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-lint"\ntype = "javascript_lint"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"lint\\\\n\\")"]\n\n'
        '[[checks]]\nid = "js-tests"\ntype = "javascript_tests"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"tests\\\\n\\"); raise SystemExit(4)"]\n\n'
        '[[checks]]\nid = "js-build"\ntype = "javascript_build"\ndepends_on = ["js-lint"]\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"build\\\\n\\")"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert [outcome["id"] for outcome in payload["check_outcomes"]] == [
        "js-lint",
        "js-tests",
        "js-build",
    ]
    assert [outcome["stable_check_id"] for outcome in payload["check_outcomes"]] == [
        "javascript_lint",
        "javascript_tests",
        "javascript_build",
    ]
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "passed",
        "failed",
        "passed",
    ]
    assert order_file.read_text() == "lint\ntests\nbuild\n"


def test_validate_reports_error_for_unknown_fields_on_javascript_typed_checks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-lint"\ntype = "javascript_lint"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for javascript_lint check: extra_field"


def test_validate_reports_error_when_javascript_tool_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-tests"\ntype = "javascript_tests"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="js-tests",
        expected_stable_check_id="javascript_tests",
        expected_status="error",
        expected_stdout="",
        expected_stderr=payload["check_outcomes"][0]["evidence"]["stderr"],
        expected_exit_code=None,
        expected_command=["command-that-does-not-exist"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_supports_typed_secrets_scan_checks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "secrets"\ntype = "secrets_scan"\n'
        'command = ["sh", "-c", "printf clean"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="secrets",
        expected_stable_check_id="secrets_scan",
        expected_status="passed",
        expected_stdout="clean",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf clean"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_accepts_deprecated_secrets_scan_alias(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "secrets"\ntype = "secrets-scan"\n'
        'command = ["sh", "-c", "printf clean"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="secrets",
        expected_stable_check_id="secrets_scan",
        expected_status="passed",
        expected_stdout="clean",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf clean"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_runs_repository_guard_checks_with_policy_and_dependencies(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    order_file = target / "guard-order.txt"
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "secrets"\ntype = "secrets_scan"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"secrets\\\\n\\")"]\n\n'
        '[[checks]]\nid = "env-advisory"\ntype = "environment_file_guard"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"env-advisory\\\\n\\"); raise SystemExit(6)"]\n\n'
        '[[checks]]\nid = "env-dependent"\ntype = "environment_file_guard"\ndepends_on = ["secrets"]\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"env-dependent\\\\n\\")"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert [outcome["id"] for outcome in payload["check_outcomes"]] == [
        "secrets",
        "env-advisory",
        "env-dependent",
    ]
    assert [outcome["stable_check_id"] for outcome in payload["check_outcomes"]] == [
        "secrets_scan",
        "environment_file_guard",
        "environment_file_guard",
    ]
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "passed",
        "failed",
        "passed",
    ]
    assert order_file.read_text() == "secrets\nenv-advisory\nenv-dependent\n"


def test_validate_reports_error_for_unknown_fields_on_secrets_scan_checks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "secrets"\ntype = "secrets_scan"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for secrets_scan check: extra_field"


def test_validate_reports_error_for_unknown_fields_on_environment_file_guard_checks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "env"\ntype = "environment_file_guard"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert (
        payload["message"]
        == "Unknown fields for environment_file_guard check: extra_field"
    )


def test_validate_accepts_deprecated_environment_file_guard_alias(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "env"\ntype = "environment-file-guard"\n'
        'command = ["sh", "-c", "printf ok"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="env",
        expected_stable_check_id="environment_file_guard",
        expected_status="passed",
        expected_stdout="ok",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf ok"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_reports_error_outcome_when_repository_guard_tool_is_missing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "env"\ntype = "environment_file_guard"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="env",
        expected_stable_check_id="environment_file_guard",
        expected_status="error",
        expected_stdout="",
        expected_stderr=payload["check_outcomes"][0]["evidence"]["stderr"],
        expected_exit_code=None,
        expected_command=["command-that-does-not-exist"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_reports_error_outcome_when_command_cannot_execute(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="fixture",
        expected_stable_check_id="command",
        expected_status="error",
        expected_stdout="",
        expected_stderr=payload["check_outcomes"][0]["evidence"]["stderr"],
        expected_exit_code=None,
        expected_command=["command-that-does-not-exist"],
        expected_working_directory=str(target.resolve()),
    )
    assert "No such file or directory" in payload["check_outcomes"][0]["evidence"]["stderr"]


def test_validate_supports_typed_python_lint_checks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "py-lint"\ntype = "python_lint"\n'
        'command = ["sh", "-c", "printf lint-ok"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="py-lint",
        expected_stable_check_id="python_lint",
        expected_status="passed",
        expected_stdout="lint-ok",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf lint-ok"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_accepts_deprecated_python_lint_alias(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "py-lint"\ntype = "python-lint"\n'
        'command = ["sh", "-c", "printf lint-ok"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert_check_outcome(
        payload,
        expected_id="py-lint",
        expected_stable_check_id="python_lint",
        expected_status="passed",
        expected_stdout="lint-ok",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "printf lint-ok"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_runs_python_typed_checks_with_policy_and_dependencies(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    order_file = target / "python-order.txt"
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "py-lint"\ntype = "python_lint"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"lint\\\\n\\")"]\n\n'
        '[[checks]]\nid = "py-tests"\ntype = "python_tests"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"tests\\\\n\\"); raise SystemExit(5)"]\n\n'
        '[[checks]]\nid = "py-typecheck"\ntype = "python_typecheck"\ndepends_on = ["py-lint"]\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"typecheck\\\\n\\")"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert [outcome["id"] for outcome in payload["check_outcomes"]] == [
        "py-lint",
        "py-tests",
        "py-typecheck",
    ]
    assert [outcome["stable_check_id"] for outcome in payload["check_outcomes"]] == [
        "python_lint",
        "python_tests",
        "python_typecheck",
    ]
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "passed",
        "failed",
        "passed",
    ]
    assert order_file.read_text() == "lint\ntests\ntypecheck\n"


def test_validate_reports_error_for_unknown_fields_on_python_typed_checks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "py-lint"\ntype = "python_lint"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for python_lint check: extra_field"


def test_validate_reports_error_when_python_tool_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "py-tests"\ntype = "python_tests"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="py-tests",
        expected_stable_check_id="python_tests",
        expected_status="error",
        expected_stdout="",
        expected_stderr=payload["check_outcomes"][0]["evidence"]["stderr"],
        expected_exit_code=None,
        expected_command=["command-that-does-not-exist"],
        expected_working_directory=str(target.resolve()),
    )


def test_validate_bounds_check_evidence_output_and_reports_duration(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    large_stdout = "x" * 5000
    large_stderr = "y" * 5000
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        f'command = ["python3", "-c", "import sys; print(\\"{large_stdout}\\", end=\'\'); '
        f'sys.stderr.write(\\"{large_stderr}\\")"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["stdout"] == large_stdout[:2000]
    assert evidence["stderr"] == large_stderr[:2000]
    assert evidence["duration_seconds"] >= 0


def test_validate_can_persist_full_check_logs_via_cli_flag(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    log_dir = tmp_path / "logs"
    large_stdout = "x" * 5000
    large_stderr = "y" * 5000
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "fixture"\ntype = "command"\n'
        f'command = ["python3", "-c", "import sys; print(\\"{large_stdout}\\", end=\'\'); '
        f'sys.stderr.write(\\"{large_stderr}\\")"]\n'
    )

    result = run_cli("validate", str(target), "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["stdout"] == large_stdout[:2000]
    assert evidence["stderr"] == large_stderr[:2000]
    assert evidence["log_path"] == str((log_dir / "fixture.log").resolve())
    assert Path(evidence["log_path"]).read_text() == large_stdout + large_stderr


def test_validate_can_persist_full_check_logs_via_configuration(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    large_stdout = "x" * 5000
    large_stderr = "y" * 5000
    (target / "saringan.toml").write_text(
        'schema_version = 1\nlog_dir = ".saringan/logs"\n\n'
        '[[checks]]\nid = "fixture"\ntype = "command"\n'
        f'command = ["python3", "-c", "import sys; print(\\"{large_stdout}\\", end=\'\'); '
        f'sys.stderr.write(\\"{large_stderr}\\"); raise SystemExit(7)"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["stdout"] == large_stdout[:2000]
    assert evidence["stderr"] == large_stderr[:2000]
    assert evidence["log_path"] == str((target / ".saringan" / "logs" / "fixture.log").resolve())
    assert Path(evidence["log_path"]).read_text() == large_stdout + large_stderr


def test_validate_rejects_fixture_status_field(tmp_path: Path) -> None:
    """fixture_status is no longer an allowed top-level field."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "passed"\n')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "Unknown top-level configuration fields: fixture_status" in payload["message"]


def test_validate_reports_error_when_no_checks_declared(tmp_path: Path) -> None:
    """A config with schema_version but no checks is an error."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\n')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "Configuration does not declare any checks" in payload["message"]


def test_validate_returns_error_for_empty_checks_list(tmp_path: Path) -> None:
    """An empty checks table is treated as missing checks."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nchecks = []\n')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "Configuration does not declare any checks" in payload["message"]


def test_validate_fixture_status_rejected_even_when_no_checks_present(
    tmp_path: Path,
) -> None:
    """fixture_status is rejected at config load time, before checks validation."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\nfixture_status = "passed"\n'
        '[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "Unknown top-level configuration fields: fixture_status" in payload["message"]


def test_validate_deprecated_alias_reports_error_for_unknown_fields(
    tmp_path: Path,
) -> None:
    """Error messages use canonical names even when deprecated alias was used as input."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "secrets"\ntype = "secrets-scan"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    # Error message must use canonical name, not the deprecated alias
    assert payload["message"] == "Unknown fields for secrets_scan check: extra_field"


def test_validate_depends_on_uses_user_ids_not_stable_ids(tmp_path: Path) -> None:
    """Dependency resolution uses user-defined check ids, not stable check IDs."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "base-check"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 8"]\n\n'
        '[[checks]]\nid = "dependent-check"\ntype = "command"\ndepends_on = ["base-check"]\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert [outcome["id"] for outcome in payload["check_outcomes"]] == [
        "base-check",
        "dependent-check",
    ]
    assert [outcome["status"] for outcome in payload["check_outcomes"]] == [
        "failed",
        "skipped",
    ]
    assert payload["check_outcomes"][1]["reason"] == "unsatisfied dependency"


def _payload_without_timestamps(payload: dict[str, object]) -> dict[str, object]:
    """Strip timestamps and duration fields so two invocations can be compared."""
    clean: dict[str, object] = {
        k: v
        for k, v in payload.items()
        if k not in ("started_at", "finished_at")
    }
    if isinstance(clean.get("check_outcomes"), list):
        clean["check_outcomes"] = [
            {
                **{ck: cv for ck, cv in outcome.items() if ck != "evidence"},
                "evidence": {
                    ek: ev
                    for ek, ev in outcome.get("evidence", {}).items()
                    if ek != "duration_seconds"
                },
            }
            for outcome in clean["check_outcomes"]
        ]
    return clean


def test_validate_json_flag_is_noop_for_passed_result(tmp_path: Path) -> None:
    """--json is a deprecated no-op: with/without it produce equivalent output."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 0"]\n'
    )

    with_json = run_cli("validate", str(target), "--json")
    without_json = run_cli("validate", str(target))

    assert with_json.returncode == 0
    assert without_json.returncode == 0
    assert _payload_without_timestamps(json.loads(with_json.stdout)) == _payload_without_timestamps(json.loads(without_json.stdout))


def test_validate_json_flag_is_noop_for_failed_result(tmp_path: Path) -> None:
    """--json is a deprecated no-op for failed validations too."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n[[checks]]\nid = "smoke"\ntype = "command"\n'
        'command = ["sh", "-c", "exit 1"]\n'
    )

    with_json = run_cli("validate", str(target), "--json")
    without_json = run_cli("validate", str(target))

    assert with_json.returncode == 1
    assert without_json.returncode == 1
    assert _payload_without_timestamps(json.loads(with_json.stdout)) == _payload_without_timestamps(json.loads(without_json.stdout))


def test_validate_json_flag_is_noop_for_error_result(tmp_path: Path) -> None:
    """--json is a deprecated no-op for error outcomes (e.g. missing config)."""
    target = tmp_path / "target"
    target.mkdir()

    with_json = run_cli("validate", str(target), "--json")
    without_json = run_cli("validate", str(target))

    assert with_json.returncode == 2
    assert without_json.returncode == 2
    assert _payload_without_timestamps(json.loads(with_json.stdout)) == _payload_without_timestamps(json.loads(without_json.stdout))


def _make_judge_request(
    target: Path,
    diff_path: Path,
    issue_path: Path,
    conventions_path: Path | None = None,
) -> JudgeRequest:
    return JudgeRequest(
        target_path=target,
        diff_path=diff_path,
        issue_path=issue_path,
        conventions_path=conventions_path,
        model="gpt-5",
    )


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_in_scope_verdict_as_advisory_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+// in scope change\n")
    issue_path.write_text("# Issue: Add feature to app.js\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {"summary": "All good.", "advisories": []}
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "Changed file matches issue scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["scope_guard"]["verdict"] == "yes"
    assert evidence["scope_guard"]["rationale"] == "Changed file matches issue scope."
    assert evidence["changed_files"] == ["src/app.js"]


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_out_of_scope_verdict_as_advisory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/unrelated.c b/src/unrelated.c\n+void main() {}\n")
    issue_path.write_text("# Issue: Add feature to app.js\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {"summary": "Concern noted.", "advisories": []}
        ),
        scope_guard_client=FakeScopeGuardClient(
            {
                "verdict": "no",
                "rationale": "Changed file unrelated.c is not mentioned in the issue.",
            }
        ),
    )

    # no verdict is advisory — exit 0
    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["scope_guard"]["verdict"] == "no"
    assert "unrelated.c" in evidence["scope_guard"]["rationale"]
    assert evidence["changed_files"] == ["src/unrelated.c"]


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_uncertain_verdict_as_advisory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/config.toml b/config.toml\n+timeout=30\n")
    issue_path.write_text("# Issue: Improve performance\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {"summary": "Unclear.", "advisories": []}
        ),
        scope_guard_client=FakeScopeGuardClient(
            {
                "verdict": "idk",
                "rationale": "Cannot determine if config change relates to performance.",
            }
        ),
    )

    # idk verdict is advisory — exit 0
    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["scope_guard"]["verdict"] == "idk"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_error_for_invalid_structured_output(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {"summary": "ok", "advisories": []}
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "maybe", "rationale": ""}
        ),
    )

    assert exit_code == 2
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "error"
    assert payload["message"].startswith(
        "Scope guard returned invalid structured output:"
    )


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_error_for_missing_verdict_field(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {"summary": "ok", "advisories": []}
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"rationale": "Some rationale."}
        ),
    )

    assert exit_code == 2
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "error"
    assert payload["message"].startswith(
        "Scope guard returned invalid structured output:"
    )


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_scope_guard_reports_error_when_dependencies_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    monkeypatch.setenv("SARINGAN_FORCE_MISSING_JUDGE_DEPS", "1")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--model",
        "gpt-5",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == (
        "Judge dependencies are not installed. Reinstall with the 'judge' extra."
    )


# ── QAG acceptance criteria verification tests ────────────────────────────


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_criteria_all_pass_yields_completion_score_one(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+function add() {}\n")
    issue_path.write_text(
        "# Issue: Add feature\n\n"
        "## Acceptance criteria\n"
        "- [ ] Add function implemented\n"
        "- [ ] Tests pass\n"
    )

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "All criteria satisfied.",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Add function implemented",
                        "verdict": "yes",
                        "rationale": "Found add() function in diff.",
                    },
                    {
                        "criterion": "Tests pass",
                        "verdict": "yes",
                        "rationale": "Test changes present.",
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert "acceptance_criteria" in evidence
    assert evidence["completion_score"] == 1.0
    assert len(evidence["acceptance_criteria"]) == 2
    assert evidence["acceptance_criteria"][0]["criterion"] == "Add function implemented"
    assert evidence["acceptance_criteria"][0]["verdict"] == "yes"
    assert evidence["acceptance_criteria"][1]["criterion"] == "Tests pass"
    assert evidence["acceptance_criteria"][1]["verdict"] == "yes"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_criteria_one_fail_yields_partial_completion_score(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+function add() {}\n")
    issue_path.write_text("# Issue: Add and test feature\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "Partial completion.",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Add function implemented",
                        "verdict": "yes",
                        "rationale": "Found add() function.",
                    },
                    {
                        "criterion": "Tests added",
                        "verdict": "no",
                        "rationale": "No test file changes detected.",
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["completion_score"] == 0.5
    assert len(evidence["acceptance_criteria"]) == 2
    assert evidence["acceptance_criteria"][0]["verdict"] == "yes"
    assert evidence["acceptance_criteria"][1]["verdict"] == "no"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_criteria_one_uncertain_excluded_from_denominator(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+function add() {}\n")
    issue_path.write_text("# Issue: Add feature\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "One uncertain criterion.",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Add function implemented",
                        "verdict": "yes",
                        "rationale": "Found add() function.",
                    },
                    {
                        "criterion": "Documentation updated",
                        "verdict": "idk",
                        "rationale": "Cannot determine from diff alone.",
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    # Only "yes" and "no" count; "idk" is excluded → 1 yes / 1 decided = 1.0
    assert evidence["completion_score"] == 1.0
    assert evidence["acceptance_criteria"][1]["verdict"] == "idk"


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_empty_criteria_yields_completion_score_zero(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+function add() {}\n")
    issue_path.write_text("# Issue: No criteria listed\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "No criteria found.",
                "advisories": [],
                "acceptance_criteria": [],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["completion_score"] == 0.0
    assert evidence["acceptance_criteria"] == []


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_all_uncertain_yields_completion_score_zero(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n+function add() {}\n")
    issue_path.write_text("# Issue: Ambiguous requirements\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "All uncertain.",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Some vague requirement",
                        "verdict": "idk",
                        "rationale": "Cannot determine.",
                    },
                    {
                        "criterion": "Another vague requirement",
                        "verdict": "idk",
                        "rationale": "Cannot determine.",
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    # All idk → no decided criteria → score 0.0
    assert evidence["completion_score"] == 0.0


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_reports_error_for_invalid_criteria_verdict(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "ok",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Something",
                        "verdict": "maybe",
                        "rationale": "Not a valid verdict.",
                    }
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 2
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "error"
    assert payload["message"].startswith(
        "Judge model returned invalid structured output:"
    )


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_reports_error_for_missing_criterion_field(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "ok",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "verdict": "yes",
                        "rationale": "Missing criterion text.",
                    }
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    assert exit_code == 2
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "error"
    assert payload["message"].startswith(
        "Judge model returned invalid structured output:"
    )


@pytest.mark.skipif(not HAS_JUDGE_DEPS, reason="Requires 'judge' extra dependencies")
def test_qag_acceptance_criteria_are_advisory_only(
    tmp_path: Path,
) -> None:
    """All criteria failing should still exit 0 because findings are advisory."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.js b/src/app.js\n// empty diff\n")
    issue_path.write_text("# Issue: Big feature\n")

    result, exit_code = judge_target(
        _make_judge_request(target, diff_path, issue_path),
        judge_client=FakeJudgeClient(
            {
                "summary": "Nothing done.",
                "advisories": [],
                "acceptance_criteria": [
                    {
                        "criterion": "Implement feature X",
                        "verdict": "no",
                        "rationale": "No implementation found.",
                    },
                    {
                        "criterion": "Add tests for X",
                        "verdict": "no",
                        "rationale": "No tests found.",
                    },
                    {
                        "criterion": "Update docs for X",
                        "verdict": "no",
                        "rationale": "No doc changes.",
                    },
                ],
            }
        ),
        scope_guard_client=FakeScopeGuardClient(
            {"verdict": "yes", "rationale": "In scope."}
        ),
    )

    # All criteria failing — still advisory, exit 0
    assert exit_code == 0
    payload = json.loads(json.dumps(result, default=lambda value: value.__dict__))
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["completion_score"] == 0.0


# ── Judge Harness execution tests (Issue #44) ─────────────────────────────


def _make_fake_harness_script(code: str, tmp_path: Path) -> Path:
    """Write a fake harness Python script to tmp_path and return its path."""
    harness_path = tmp_path / "fake_harness.py"
    harness_path.write_text(code)
    return harness_path


def test_judge_harness_writes_valid_result_artifact_reports_passed(
    tmp_path: Path,
) -> None:
    """A harness that writes a valid Judge Harness result artifact produces
    a passed advisory Contextual Judge Gate Check Outcome."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n"
        "+print('debug')\n"
    )
    issue_path.write_text("# Issue 44: Implement judge harness\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
# Produce a valid harness result artifact
artifact = {
    "summary": "Harness executed successfully.",
    "scope_guard": {
        "verdict": "yes",
        "rationale": "All changed files relate to issue scope."
    },
    "advisories": [
        {"kind": "debug_artifact", "file": "src/app.py", "line": 2, "snippet": "print('debug')"}
    ],
    "acceptance_criteria": [
        {
            "criterion": "Harness writes valid artifact",
            "verdict": "yes",
            "rationale": "Artifact conforms to contract."
        }
    ]
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert len(payload["check_outcomes"]) == 1
    outcome = payload["check_outcomes"][0]
    assert outcome["id"] == "contextual_judge"
    assert outcome["stable_check_id"] == "contextual_judge"
    assert outcome["status"] == "passed"
    assert outcome["blocking"] is False
    assert outcome["message"] == "Harness executed successfully."
    evidence = outcome["evidence"]
    assert evidence["diff_path"] == str(diff_path.resolve())
    assert evidence["issue_path"] == str(issue_path.resolve())
    assert evidence["changed_files"] == ["src/app.py"]
    assert evidence["scope_guard"]["verdict"] == "yes"
    assert evidence["scope_guard"]["rationale"] == "All changed files relate to issue scope."
    assert len(evidence["advisories"]) == 1
    assert evidence["advisories"][0]["kind"] == "debug_artifact"
    assert len(evidence["acceptance_criteria"]) == 1
    assert evidence["acceptance_criteria"][0]["verdict"] == "yes"
    assert evidence["completion_score"] == 1.0
    assert evidence["harness_command"] == f"python3 {fake_harness}"
    assert "input" in evidence
    assert evidence["input"]["issue_text"] == "# Issue 44: Implement judge harness\n"
    assert evidence["input"]["diff_text"].startswith("diff --git a/src/app.py")
    assert payload["started_at"]
    assert payload["finished_at"]


def test_judge_harness_with_minimal_required_fields_reports_passed(
    tmp_path: Path,
) -> None:
    """A harness that writes only required fields (summary + scope_guard)
    produces a passed outcome."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Minimal valid result.",
    "scope_guard": {"verdict": "idk", "rationale": "Not enough context."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    outcome = payload["check_outcomes"][0]
    assert outcome["status"] == "passed"
    assert outcome["message"] == "Minimal valid result."
    evidence = outcome["evidence"]
    assert evidence["scope_guard"]["verdict"] == "idk"
    assert evidence["advisories"] == []
    assert evidence["acceptance_criteria"] == []
    assert evidence["completion_score"] == 0.0


def test_judge_harness_exits_nonzero_reports_error(
    tmp_path: Path,
) -> None:
    """A harness that exits with non-zero code produces status: error."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import json, sys
input_data = json.load(sys.stdin)
sys.exit(3)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"].startswith("Judge harness exited with code 3")
    assert "stderr" in payload["message"] or "exit" in payload["message"]


def test_judge_harness_writes_invalid_output_reports_error(
    tmp_path: Path,
) -> None:
    """A harness that writes invalid JSON (non-conforming to contract)
    produces status: error."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
# Write output missing required scope_guard field
artifact = {"summary": "ok"}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "scope_guard" in payload["message"]


def test_judge_harness_omits_artifact_file_reports_error(
    tmp_path: Path,
) -> None:
    """A harness that exits 0 but does not write the result artifact
    produces status: error."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import json, sys
# Read stdin but write nothing - just exit 0
input_data = json.load(sys.stdin)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "result artifact" in payload["message"]


def test_judge_harness_writes_unparseable_json_reports_error(
    tmp_path: Path,
) -> None:
    """A harness that writes a file but with unparseable JSON
    produces status: error."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import os, sys, json
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
with open(result_path, "w") as f:
    f.write("not valid json {{{{{")
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "JSON" in payload["message"] or "json" in payload["message"].lower() or "parse" in payload["message"].lower()


def test_judge_harness_receives_full_context_via_stdin(
    tmp_path: Path,
) -> None:
    """The harness receives diff_text, issue_text, conventions_text, model,
    and target_path in the stdin input."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    conventions_path = tmp_path / "conventions.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new code\n")
    issue_path.write_text("# Issue 44\n")
    conventions_path.write_text("# Conventions\nUse snake_case.\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
# Validate all expected context fields are present
assert input_data["target_path"] is not None
assert "diff --git" in input_data["diff_text"]
assert "# Issue 44" in input_data["issue_text"]
assert "snake_case" in input_data["conventions_text"]
assert input_data["model"] == "gpt-5"
artifact = {
    "summary": "Context received correctly.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--conventions",
        str(conventions_path),
        "--model",
        "gpt-5",
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"


def test_judge_harness_respects_conventions_from_stdin(
    tmp_path: Path,
) -> None:
    """When --conventions is provided, the conventions text is passed to the
    harness via stdin."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    conventions_path = tmp_path / "conventions.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")
    conventions_path.write_text("# Project Conventions\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
assert input_data["conventions_text"] == "# Project Conventions\\n"
artifact = {
    "summary": "Conventions received.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--conventions",
        str(conventions_path),
        "--model",
        "gpt-5",
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"


def test_judge_harness_without_conventions_passes_none(
    tmp_path: Path,
) -> None:
    """When --conventions is omitted, conventions_text is None/null in harness input."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
assert input_data["conventions_text"] is None, f"Expected None, got {input_data['conventions_text']!r}"
artifact = {
    "summary": "No conventions needed.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"


# ── Judge Harness Configuration (Issue #45) ─────────────────────────────────


def _make_judge_config_toml(tmp_path: Path, content: str) -> Path:
    """Write a judge.toml to tmp_path and return its path."""
    config_path = tmp_path / "judge.toml"
    config_path.write_text(content)
    return config_path


def test_judge_with_config_uses_named_harness(tmp_path: Path) -> None:
    """When --judge-config is provided and --harness names a harness,
    the named harness command is executed and its name/model appear in evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Named harness worked.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "my-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--harness",
        "my-harness",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["harness_name"] == "my-harness"
    assert evidence["provider"] == "litellm"
    assert evidence["model"] == "gpt-5"


def test_judge_with_config_uses_default_harness_when_none_specified(
    tmp_path: Path,
) -> None:
    """When --judge-config has a default_harness and --harness is omitted,
    the default harness is used."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Default harness used.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
default_harness = "default-h"

[[harnesses]]
name = "default-h"
provider = "openai"
model = "gpt-4"
command = ["python3", "{fake_harness}"]
timeout = 180
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["harness_name"] == "default-h"
    assert evidence["provider"] == "openai"
    assert evidence["model"] == "gpt-4"


def test_judge_provider_override_cli(tmp_path: Path) -> None:
    """--provider overrides the harness provider in evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Provider overridden.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "my-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--harness",
        "my-harness",
        "--provider",
        "anthropic",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["provider"] == "anthropic"
    assert evidence["model"] == "gpt-5"  # unchanged


def test_judge_model_override_cli(tmp_path: Path) -> None:
    """--model overrides the harness model in evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Model overridden.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "my-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--harness",
        "my-harness",
        "--model",
        "claude-sonnet-4",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["provider"] == "litellm"  # unchanged
    assert evidence["model"] == "claude-sonnet-4"


def test_judge_env_var_overrides_harness_and_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment variables SARINGAN_JUDGE_HARNESS and SARINGAN_JUDGE_PROVIDER
    override the defaults."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Env overrides applied.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
default_harness = "default-h"

[[harnesses]]
name = "default-h"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300

[[harnesses]]
name = "env-harness"
provider = "openai"
model = "gpt-4"
command = ["python3", "{fake_harness}"]
timeout = 180
""",
    )

    monkeypatch.setenv("SARINGAN_JUDGE_CONFIG", str(judge_config))
    monkeypatch.setenv("SARINGAN_JUDGE_HARNESS", "env-harness")
    monkeypatch.setenv("SARINGAN_JUDGE_PROVIDER", "anthropic")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["harness_name"] == "env-harness"
    assert evidence["provider"] == "anthropic"  # overridden by env
    assert evidence["model"] == "gpt-4"  # from harness


def test_judge_env_var_model_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SARINGAN_JUDGE_MODEL overrides the harness model."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Model env override.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "my-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    monkeypatch.setenv("SARINGAN_JUDGE_CONFIG", str(judge_config))
    monkeypatch.setenv("SARINGAN_JUDGE_HARNESS", "my-harness")
    monkeypatch.setenv("SARINGAN_JUDGE_MODEL", "gemini-pro")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["model"] == "gemini-pro"


def test_judge_cli_overrides_take_precedence_over_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI flags override environment variable values."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "CLI wins.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "env-h"
provider = "openai"
model = "gpt-4"
command = ["python3", "{fake_harness}"]
timeout = 180

[[harnesses]]
name = "cli-h"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    monkeypatch.setenv("SARINGAN_JUDGE_CONFIG", str(judge_config))
    monkeypatch.setenv("SARINGAN_JUDGE_HARNESS", "env-h")
    monkeypatch.setenv("SARINGAN_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("SARINGAN_JUDGE_MODEL", "gpt-4")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        "cli-h",
        "--provider",
        "anthropic",
        "--model",
        "claude-3",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["harness_name"] == "cli-h"
    assert evidence["provider"] == "anthropic"
    assert evidence["model"] == "claude-3"


def test_judge_unknown_harness_reports_error(tmp_path: Path) -> None:
    """Selecting a harness name not in config produces an Environment Failure."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    judge_config = _make_judge_config_toml(
        tmp_path,
        """\
[[harnesses]]
name = "known-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "some_harness"]
timeout = 300
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--harness",
        "unknown-harness",
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "unknown-harness" in payload["message"]


def test_judge_invalid_config_reports_error(tmp_path: Path) -> None:
    """An invalid judge config TOML produces an Environment Failure."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    judge_config = tmp_path / "judge.toml"
    judge_config.write_text("this is not valid toml {{{[")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "TOML" in payload["message"] or "toml" in payload["message"].lower()


def test_judge_raw_command_harness_still_works(tmp_path: Path) -> None:
    """When --harness looks like a raw command (not a named harness in config),
    it is executed directly (backward compat)."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Raw command harness.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["harness_command"] == f"python3 {fake_harness}"


def test_judge_no_config_no_harness_uses_builtin_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --judge-config and without --harness, built-in LLM path
    is used (backward compat). This requires judge deps."""
    if not HAS_JUDGE_DEPS:
        pytest.skip("Requires 'judge' extra dependencies")

    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--model",
        "gpt-5",
        "--json",
    )

    # This won't actually call the LLM (no API key in test),
    # but the error should be from the built-in LLM path, not
    # a config/harness error.
    assert result.returncode in (0, 2)


def test_judge_harness_receives_provider_via_stdin(tmp_path: Path) -> None:
    """When a named harness is used, the provider and model are passed via stdin."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/file b/file\n")
    issue_path.write_text("# Issue 45\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
assert input_data["provider"] == "litellm", f"Expected 'litellm', got {input_data['provider']!r}"
assert input_data["model"] == "gpt-5", f"Expected 'gpt-5', got {input_data['model']!r}"
artifact = {
    "summary": "Provider received.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."}
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    judge_config = _make_judge_config_toml(
        tmp_path,
        f"""\
[[harnesses]]
name = "my-harness"
provider = "litellm"
model = "gpt-5"
command = ["python3", "{fake_harness}"]
timeout = 300
""",
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--judge-config",
        str(judge_config),
        "--harness",
        "my-harness",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["provider"] == "litellm"
    assert evidence["model"] == "gpt-5"


# ── Issue #47: Consolidated harness result tests ───────────────────────────


def test_harness_with_mixed_acceptance_criteria_excludes_idk_from_score(
    tmp_path: Path,
) -> None:
    """A harness result with yes/no/idk verdicts computes completion score
    excluding idk from the denominator (2 yes / 3 decided = 0.667)."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new feature\n")
    issue_path.write_text("# Issue 47: Mix of criteria\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Mixed verdicts.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."},
    "acceptance_criteria": [
        {"criterion": "Criterion A", "verdict": "yes", "rationale": "Done."},
        {"criterion": "Criterion B", "verdict": "yes", "rationale": "Done."},
        {"criterion": "Criterion C", "verdict": "no", "rationale": "Missing."},
        {"criterion": "Criterion D", "verdict": "idk", "rationale": "Unclear."},
        {"criterion": "Criterion E", "verdict": "idk", "rationale": "Ambiguous."},
    ]
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert len(evidence["acceptance_criteria"]) == 5
    assert evidence["acceptance_criteria"][0]["verdict"] == "yes"
    assert evidence["acceptance_criteria"][1]["verdict"] == "yes"
    assert evidence["acceptance_criteria"][2]["verdict"] == "no"
    assert evidence["acceptance_criteria"][3]["verdict"] == "idk"
    assert evidence["acceptance_criteria"][4]["verdict"] == "idk"
    # 2 yes / (2 yes + 1 no) = 0.666..., idk excluded
    assert evidence["completion_score"] == pytest.approx(2 / 3)


def test_harness_with_all_idk_criteria_yields_completion_score_zero(
    tmp_path: Path,
) -> None:
    """A harness result where all acceptance criteria are idk returns
    completion score 0.0 (no decided criteria)."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new feature\n")
    issue_path.write_text("# Issue 47: All uncertain\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "All idk.",
    "scope_guard": {"verdict": "yes", "rationale": "In scope."},
    "acceptance_criteria": [
        {"criterion": "Criterion A", "verdict": "idk", "rationale": "Unclear."},
        {"criterion": "Criterion B", "verdict": "idk", "rationale": "Ambiguous."},
    ]
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["completion_score"] == 0.0
    assert len(evidence["acceptance_criteria"]) == 2
    assert all(c["verdict"] == "idk" for c in evidence["acceptance_criteria"])


def test_harness_with_no_scope_guard_verdict_in_evidence(
    tmp_path: Path,
) -> None:
    """A harness result with scope guard verdict 'no' is included in
    Check Evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/unrelated.c b/src/unrelated.c\n+code\n")
    issue_path.write_text("# Issue 47: Add feature to app.py\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Out of scope.",
    "scope_guard": {
        "verdict": "no",
        "rationale": "Changed file unrelated.c is outside issue scope."
    }
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["scope_guard"]["verdict"] == "no"
    assert "unrelated.c" in evidence["scope_guard"]["rationale"]
    assert evidence["changed_files"] == ["src/unrelated.c"]


def test_harness_with_idk_scope_guard_verdict_in_evidence(
    tmp_path: Path,
) -> None:
    """A harness result with scope guard verdict 'idk' is included in
    Check Evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/config.toml b/config.toml\n+timeout=30\n")
    issue_path.write_text("# Issue 47: Improve performance\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Scope uncertain.",
    "scope_guard": {
        "verdict": "idk",
        "rationale": "Cannot determine if config change relates to performance."
    }
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    evidence = payload["check_outcomes"][0]["evidence"]
    assert evidence["scope_guard"]["verdict"] == "idk"
    assert "config" in evidence["scope_guard"]["rationale"]
    assert evidence["changed_files"] == ["config.toml"]


def test_harness_with_mixed_criteria_includes_all_in_evidence(
    tmp_path: Path,
) -> None:
    """A harness result with yes, no, and idk acceptance criteria plus
    advisories includes all verdicts in Check Evidence."""
    target = tmp_path / "target"
    target.mkdir()
    diff_path = tmp_path / "changes.diff"
    issue_path = tmp_path / "issue.md"
    diff_path.write_text("diff --git a/src/app.py b/src/app.py\n+new feature\n")
    issue_path.write_text("# Issue 47: Full coverage\n")

    fake_harness = _make_fake_harness_script(
        '''import json, os, sys
input_data = json.load(sys.stdin)
result_path = os.environ["SARINGAN_RESULT_PATH"]
artifact = {
    "summary": "Full harness result.",
    "scope_guard": {"verdict": "yes", "rationale": "Changes match issue scope."},
    "advisories": [
        {"kind": "style", "file": "src/app.py", "line": 10, "snippet": "use camelCase"}
    ],
    "acceptance_criteria": [
        {"criterion": "Feature implemented", "verdict": "yes", "rationale": "Found implementation."},
        {"criterion": "Tests added", "verdict": "no", "rationale": "No test changes."},
        {"criterion": "Docs updated", "verdict": "idk", "rationale": "Cannot tell from diff."}
    ]
}
with open(result_path, "w") as f:
    json.dump(artifact, f)
''',
        tmp_path,
    )

    result = run_cli(
        "judge",
        str(target),
        "--diff",
        str(diff_path),
        "--issue",
        str(issue_path),
        "--harness",
        f"python3 {fake_harness}",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    outcome = payload["check_outcomes"][0]
    assert outcome["message"] == "Full harness result."
    evidence = outcome["evidence"]
    # Scope guard present
    assert evidence["scope_guard"]["verdict"] == "yes"
    # Advisories present
    assert len(evidence["advisories"]) == 1
    assert evidence["advisories"][0]["kind"] == "style"
    # Acceptance criteria with all three verdicts present
    assert len(evidence["acceptance_criteria"]) == 3
    verdicts = [c["verdict"] for c in evidence["acceptance_criteria"]]
    assert "yes" in verdicts
    assert "no" in verdicts
    assert "idk" in verdicts
    # Completion score: 1 yes / 2 decided = 0.5
    assert evidence["completion_score"] == 0.5
    # Changed files computed deterministically
    assert evidence["changed_files"] == ["src/app.py"]
    # Harness command present
    assert evidence["harness_command"] == f"python3 {fake_harness}"
