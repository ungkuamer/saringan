from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
    expected_status: str,
    expected_stdout: str,
    expected_stderr: str,
    expected_exit_code: int | None,
    expected_command: list[str],
    expected_working_directory: str,
) -> None:
    assert payload["check_outcomes"][0]["id"] == expected_id
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
        expected_status="passed",
        expected_stdout="",
        expected_stderr="",
        expected_exit_code=0,
        expected_command=["sh", "-c", "exit 0"],
        expected_working_directory=str(target.resolve()),
    )
    assert payload["started_at"]
    assert payload["finished_at"]


def test_validate_json_reports_error_for_missing_target_path(tmp_path: Path) -> None:
    missing_target = tmp_path / "missing"

    result = run_cli("validate", str(missing_target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["target_path"] == str(missing_target.resolve())
    assert payload["config_path"] is None
    assert result.stderr


def test_validate_json_reports_failed_result_for_failed_fixture(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "failed"\n')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"


def test_validate_default_output_is_human_readable(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "passed"\n')

    result = run_cli("validate", str(target))

    assert result.returncode == 0
    assert "Validation passed:" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_validate_json_writes_machine_output_to_stdout_and_progress_to_stderr(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "passed"\n')

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
    custom_config.write_text('schema_version = 1\nfixture_status = "passed"\n')

    result = run_cli("validate", str(target), "--config", str(custom_config), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert payload["config_path"] == str(custom_config.resolve())


def test_validate_reports_error_for_invalid_toml_config(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "passed"\n[')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["config_path"] == str((target / "saringan.toml").resolve())
    assert "Invalid configuration TOML" in payload["message"]


def test_validate_reports_error_when_schema_version_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('fixture_status = "passed"\n')

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
    (target / "saringan.toml").write_text('schema_version = 2\nfixture_status = "passed"\n')

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
        'schema_version = 1\nfixture_status = "passed"\nextra_field = true\n'
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
        '[[checks]]\nid = "js-lint"\ntype = "javascript-lint"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"lint\\\\n\\")"]\n\n'
        '[[checks]]\nid = "js-tests"\ntype = "javascript-tests"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"tests\\\\n\\"); raise SystemExit(4)"]\n\n'
        '[[checks]]\nid = "js-build"\ntype = "javascript-build"\ndepends_on = ["js-lint"]\n'
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
        '[[checks]]\nid = "js-lint"\ntype = "javascript-lint"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for javascript-lint check: extra_field"


def test_validate_reports_error_when_javascript_tool_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "js-tests"\ntype = "javascript-tests"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="js-tests",
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
        '[[checks]]\nid = "secrets"\ntype = "secrets-scan"\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").write_text(\\"secrets\\\\n\\")"]\n\n'
        '[[checks]]\nid = "env-advisory"\ntype = "environment-file-guard"\nadvisory = true\n'
        'command = ["python3", "-c", "from pathlib import Path; '
        f'Path(\\"{order_file.name}\\").open(\\"a\\").write(\\"env-advisory\\\\n\\"); raise SystemExit(6)"]\n\n'
        '[[checks]]\nid = "env-dependent"\ntype = "environment-file-guard"\ndepends_on = ["secrets"]\n'
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
        '[[checks]]\nid = "secrets"\ntype = "secrets-scan"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["message"] == "Unknown fields for secrets-scan check: extra_field"


def test_validate_reports_error_for_unknown_fields_on_environment_file_guard_checks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "env"\ntype = "environment-file-guard"\n'
        'command = ["sh", "-c", "exit 0"]\nextra_field = true\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert (
        payload["message"]
        == "Unknown fields for environment-file-guard check: extra_field"
    )


def test_validate_reports_error_outcome_when_repository_guard_tool_is_missing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text(
        'schema_version = 1\n\n'
        '[[checks]]\nid = "env"\ntype = "environment-file-guard"\n'
        'command = ["command-that-does-not-exist"]\n'
    )

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert_check_outcome(
        payload,
        expected_id="env",
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
        expected_status="error",
        expected_stdout="",
        expected_stderr=payload["check_outcomes"][0]["evidence"]["stderr"],
        expected_exit_code=None,
        expected_command=["command-that-does-not-exist"],
        expected_working_directory=str(target.resolve()),
    )
    assert "No such file or directory" in payload["check_outcomes"][0]["evidence"]["stderr"]


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
