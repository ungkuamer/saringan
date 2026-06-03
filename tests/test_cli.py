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


def test_validate_json_reports_passed_result_for_valid_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "saringan.toml").write_text('schema_version = 1\nfixture_status = "passed"\n')

    result = run_cli("validate", str(target), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["target_path"] == str(target.resolve())
    assert payload["config_path"] == str((target / "saringan.toml").resolve())
    assert isinstance(payload["check_outcomes"], list)
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
