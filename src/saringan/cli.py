from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
import time
import tomllib


EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
SUPPORTED_SCHEMA_VERSIONS = {1}
ALLOWED_TOP_LEVEL_FIELDS = {"schema_version", "fixture_status", "checks", "log_dir"}
ALLOWED_EXECUTABLE_CHECK_FIELDS = {"id", "type", "command", "advisory", "depends_on"}
CHECK_FIELDS_BY_TYPE = {
    "command": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "javascript-lint": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "javascript-tests": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "javascript-build": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "python-lint": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "python-typecheck": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "python-tests": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "secrets-scan": ALLOWED_EXECUTABLE_CHECK_FIELDS,
    "environment-file-guard": ALLOWED_EXECUTABLE_CHECK_FIELDS,
}
SUPPORTED_CHECK_TYPES = set(CHECK_FIELDS_BY_TYPE)
MAX_EVIDENCE_OUTPUT_LENGTH = 2000


@dataclass
class ValidationResult:
    status: str
    check_outcomes: list[dict[str, object]] = field(default_factory=list)
    target_path: str = ""
    config_path: str | None = None
    started_at: str = ""
    finished_at: str = ""
    message: str | None = None


@dataclass
class ConfigError:
    message: str


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def load_config(config_path: Path) -> dict[str, object] | ConfigError:
    try:
        data = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as error:
        return ConfigError(message=f"Invalid configuration TOML: {error}")

    unknown_fields = sorted(set(data) - ALLOWED_TOP_LEVEL_FIELDS)
    if unknown_fields:
        field_list = ", ".join(unknown_fields)
        return ConfigError(message=f"Unknown top-level configuration fields: {field_list}")

    if "schema_version" not in data:
        return ConfigError(message="Missing required schema_version in configuration.")

    schema_version = data["schema_version"]
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return ConfigError(message=f"Unsupported schema_version: {schema_version}")

    checks = data.get("checks")
    if checks is not None:
        seen_check_ids: set[str] = set()
        for check in checks:
            check_id = check.get("id")
            if not isinstance(check_id, str) or not check_id.strip():
                return ConfigError(message="Check is missing required id.")

            if check_id in seen_check_ids:
                return ConfigError(message=f"Duplicate check id: {check_id}")
            seen_check_ids.add(check_id)

            check_type = check.get("type")
            if check_type not in SUPPORTED_CHECK_TYPES:
                return ConfigError(message=f"Unsupported check type: {check_type}")

            unknown_fields = sorted(set(check) - CHECK_FIELDS_BY_TYPE[check_type])
            if unknown_fields:
                field_list = ", ".join(unknown_fields)
                label = "command" if check_type == "command" else check_type
                return ConfigError(message=f"Unknown fields for {label} check: {field_list}")

            command = check.get("command")
            if not isinstance(command, list) or any(
                not isinstance(part, str) for part in command
            ):
                return ConfigError(
                    message=f"Check '{check_id}' command must be an argument vector."
                )

            depends_on = check.get("depends_on")
            if depends_on is not None and (
                not isinstance(depends_on, list)
                or any(not isinstance(dep, str) or not dep.strip() for dep in depends_on)
            ):
                return ConfigError(
                    message=f"Check '{check_id}' depends_on must be an array of check ids."
                )

    return data


def bound_output(output: str) -> str:
    return output[:MAX_EVIDENCE_OUTPUT_LENGTH]


def persist_check_log(
    log_dir: Path | None,
    check_id: str,
    stdout: str,
    stderr: str,
) -> str | None:
    if log_dir is None:
        return None

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{check_id}.log"
    log_path.write_text(stdout + stderr)
    return str(log_path.resolve())


def execute_command_check(
    check: dict[str, object],
    target_path: Path,
    log_dir: Path | None = None,
) -> dict[str, object]:
    command = list(check["command"])
    check_id = str(check["id"])
    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=target_path,
            check=False,
        )
    except OSError as error:
        duration_seconds = time.perf_counter() - started_at
        stderr = str(error)
        log_path = persist_check_log(log_dir, check_id, "", stderr)
        evidence = {
            "stdout": "",
            "stderr": bound_output(stderr),
            "exit_code": None,
            "duration_seconds": duration_seconds,
            "command": command,
            "working_directory": str(target_path.resolve()),
        }
        if log_path is not None:
            evidence["log_path"] = log_path
        return {
            "id": check_id,
            "status": "error",
            "evidence": evidence,
        }

    duration_seconds = time.perf_counter() - started_at
    status = "passed" if completed.returncode == 0 else "failed"
    log_path = persist_check_log(log_dir, check_id, completed.stdout, completed.stderr)
    evidence = {
        "stdout": bound_output(completed.stdout),
        "stderr": bound_output(completed.stderr),
        "exit_code": completed.returncode,
        "duration_seconds": duration_seconds,
        "command": command,
        "working_directory": str(target_path.resolve()),
    }
    if log_path is not None:
        evidence["log_path"] = log_path
    return {
        "id": check_id,
        "status": status,
        "evidence": evidence,
    }


def dependency_ids(check: dict[str, object]) -> list[str]:
    depends_on = check.get("depends_on")
    if not depends_on:
        return []
    return [str(dep) for dep in depends_on]


def aggregate_validation_status(check_outcomes: list[dict[str, object]]) -> str:
    if any(outcome["status"] == "error" for outcome in check_outcomes):
        return "error"

    for outcome in check_outcomes:
        if outcome["status"] != "failed":
            continue
        if outcome.get("blocking", True):
            return "failed"

    return "passed"


def build_skipped_outcome(check: dict[str, object], reason: str) -> dict[str, object]:
    return {
        "id": str(check["id"]),
        "status": "skipped",
        "reason": reason,
        "blocking": not bool(check.get("advisory", False)),
    }


def validate_target(
    target_path: Path,
    config_path: Path | None = None,
    log_dir: Path | None = None,
) -> tuple[ValidationResult, int]:
    started_at = iso_now()
    resolved_config_path = config_path if config_path is not None else target_path / "saringan.toml"
    resolved_target_path = str(target_path.resolve())

    if not target_path.exists():
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
                message=f"Target path does not exist: {resolved_target_path}",
            ),
            EXIT_ERROR,
        )

    if not resolved_config_path.exists():
        finished_at = iso_now()
        resolved_config_path_str = str(resolved_config_path.resolve())
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=resolved_config_path_str,
                started_at=started_at,
                finished_at=finished_at,
                message=f"Configuration file does not exist: {resolved_config_path_str}",
            ),
            EXIT_ERROR,
        )

    config_data = load_config(resolved_config_path)
    if isinstance(config_data, ConfigError):
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=str(resolved_config_path.resolve()),
                started_at=started_at,
                finished_at=finished_at,
                message=config_data.message,
            ),
            EXIT_ERROR,
        )

    if "checks" in config_data:
        configured_log_dir = config_data.get("log_dir")
        if log_dir is None and isinstance(configured_log_dir, str) and configured_log_dir.strip():
            log_dir = target_path / configured_log_dir
        declared_checks = {str(check["id"]): check for check in config_data["checks"]}
        check_outcomes = []
        for check in config_data["checks"]:
            missing_dependency = next(
                (
                    dependency_id
                    for dependency_id in dependency_ids(check)
                    if dependency_id not in declared_checks
                ),
                None,
            )
            if missing_dependency is not None:
                finished_at = iso_now()
                return (
                    ValidationResult(
                        status="error",
                        target_path=resolved_target_path,
                        config_path=str(resolved_config_path.resolve()),
                        started_at=started_at,
                        finished_at=finished_at,
                        message=(
                            f"Check '{check['id']}' depends on unknown check id: {missing_dependency}"
                        ),
                    ),
                    EXIT_ERROR,
                )

        outcome_by_id: dict[str, dict[str, object]] = {}
        for check in config_data["checks"]:
            dependency_outcomes = [outcome_by_id[dep] for dep in dependency_ids(check)]
            if any(outcome["status"] != "passed" for outcome in dependency_outcomes):
                outcome = build_skipped_outcome(check, "unsatisfied dependency")
                outcome_by_id[str(check["id"])] = outcome
                check_outcomes.append(outcome)
                continue

            outcome = execute_command_check(check, target_path, log_dir=log_dir)
            outcome["blocking"] = not bool(check.get("advisory", False))
            outcome_by_id[str(check["id"])] = outcome
            check_outcomes.append(outcome)
        status = aggregate_validation_status(check_outcomes)
    else:
        status = str(config_data.get("fixture_status", "passed"))
        check_outcomes = [{"id": "fixture", "status": status}]

    finished_at = iso_now()
    result = ValidationResult(
        status=status,
        check_outcomes=check_outcomes,
        target_path=resolved_target_path,
        config_path=str(resolved_config_path.resolve()),
        started_at=started_at,
        finished_at=finished_at,
    )
    if status == "passed":
        exit_code = EXIT_PASSED
    elif status == "failed":
        exit_code = EXIT_FAILED
    else:
        exit_code = EXIT_ERROR
    return result, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saringan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("target_path")
    validate_parser.add_argument("--config", dest="config_path")
    validate_parser.add_argument("--log-dir", dest="log_dir")
    validate_parser.add_argument("--json", action="store_true", dest="json_mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "validate":
        parser.error("unknown command")

    config_path = Path(args.config_path) if args.config_path else None
    log_dir = Path(args.log_dir) if args.log_dir else None
    result, exit_code = validate_target(
        Path(args.target_path),
        config_path=config_path,
        log_dir=log_dir,
    )
    payload = json.dumps(asdict(result))
    progress_line = f"Validating {result.target_path}"
    if args.json_mode:
        print(progress_line, file=sys.stderr)
        if result.message:
            print(result.message, file=sys.stderr)
        print(payload)
    else:
        print(progress_line)
        print(f"Validation {result.status}: {result.target_path}")
        if result.message:
            print(result.message)
    return exit_code
