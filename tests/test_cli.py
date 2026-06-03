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
