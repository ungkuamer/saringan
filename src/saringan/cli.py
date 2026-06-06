from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Protocol


EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
SUPPORTED_SCHEMA_VERSIONS = {1}
ALLOWED_TOP_LEVEL_FIELDS = {"schema_version", "checks", "log_dir"}
ALLOWED_EXECUTABLE_CHECK_FIELDS = {"id", "type", "command", "advisory", "depends_on"}

STABLE_CHECK_IDS = {
    "command",
    "javascript_lint",
    "javascript_tests",
    "javascript_build",
    "python_lint",
    "python_typecheck",
    "python_tests",
    "secrets_scan",
    "environment_file_guard",
}

DEPRECATED_TYPE_ALIASES: dict[str, str] = {
    "javascript-lint": "javascript_lint",
    "javascript-tests": "javascript_tests",
    "javascript-build": "javascript_build",
    "python-lint": "python_lint",
    "python-typecheck": "python_typecheck",
    "python-tests": "python_tests",
    "secrets-scan": "secrets_scan",
    "environment-file-guard": "environment_file_guard",
}

CHECK_FIELDS_BY_TYPE = {check_id: ALLOWED_EXECUTABLE_CHECK_FIELDS for check_id in STABLE_CHECK_IDS}
SUPPORTED_CHECK_TYPES = set(CHECK_FIELDS_BY_TYPE) | set(DEPRECATED_TYPE_ALIASES)
MAX_EVIDENCE_OUTPUT_LENGTH = 2000
DEBUG_ARTIFACT_PATTERNS = ("print(", "console.log(")


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


@dataclass
class JudgeRequest:
    target_path: Path
    diff_path: Path
    issue_path: Path
    conventions_path: Path | None
    model: str


@dataclass
class JudgeInput:
    diff_text: str
    issue_text: str
    conventions_text: str | None


class JudgeDependencyError(Exception):
    pass


class JudgeStructuredOutputError(Exception):
    pass


class JudgeClient(Protocol):
    def evaluate(self, request: JudgeRequest, judge_input: JudgeInput) -> object: ...


class ScopeGuardClient(Protocol):
    def evaluate_scope(
        self,
        request: JudgeRequest,
        judge_input: JudgeInput,
        changed_files: list[str],
    ) -> object: ...


class JudgeStructuredOutputModel(Protocol):
    advisories: list[object]


def load_judge_models() -> tuple[type[object], type[object]]:
    try:
        if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
            raise ImportError("forced missing judge dependencies")
        from pydantic import BaseModel, Field, ValidationError
    except ImportError as error:
        raise JudgeDependencyError(
            "Judge dependencies are not installed. Reinstall with the 'judge' extra."
        ) from error

    class JudgeAdvisoryModel(BaseModel):
        kind: str
        file: str | None = None
        line: int | None = None
        snippet: str | None = None

    class QagCriterionModel(BaseModel):
        criterion: str = Field(min_length=1)
        verdict: str = Field(pattern=r"^(yes|no|idk)$")
        rationale: str = Field(min_length=1)

    class JudgeResponseModel(BaseModel):
        summary: str = Field(min_length=1)
        advisories: list[JudgeAdvisoryModel] = Field(default_factory=list)
        acceptance_criteria: list[QagCriterionModel] = Field(default_factory=list)

    return JudgeResponseModel, ValidationError


def load_scope_guard_models() -> tuple[type[object], type[object]]:
    try:
        if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
            raise ImportError("forced missing judge dependencies")
        from pydantic import BaseModel, Field, ValidationError
    except ImportError as error:
        raise JudgeDependencyError(
            "Judge dependencies are not installed. Reinstall with the 'judge' extra."
        ) from error

    class ScopeGuardVerdictModel(BaseModel):
        verdict: str = Field(pattern=r"^(yes|no|idk)$")
        rationale: str = Field(min_length=1, max_length=500)

    return ScopeGuardVerdictModel, ValidationError


class LiteLLMJudgeClient:
    def __init__(self) -> None:
        try:
            if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
                raise ImportError("forced missing judge dependencies")
            from litellm import completion
        except ImportError as error:
            raise JudgeDependencyError(
                "Judge dependencies are not installed. Reinstall with the 'judge' extra."
            ) from error
        self._completion = completion

    def evaluate(self, request: JudgeRequest, judge_input: JudgeInput) -> object:
        response = self._completion(
            model=request.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the Saringan Contextual Judge Gate. Return JSON with "
                        "a summary string, an advisories array, and an "
                        "acceptance_criteria array. Extract acceptance criteria from "
                        "the issue and verify each against the diff as yes, no, or idk."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "diff_text": judge_input.diff_text,
                            "issue_text": judge_input.issue_text,
                            "conventions_text": judge_input.conventions_text,
                        }
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "saringan_contextual_judge",
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "summary": {"type": "string"},
                            "advisories": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "kind": {"type": "string"},
                                        "file": {"type": ["string", "null"]},
                                        "line": {"type": ["integer", "null"]},
                                        "snippet": {"type": ["string", "null"]},
                                    },
                                    "required": ["kind"],
                                },
                            },
                            "acceptance_criteria": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "criterion": {"type": "string"},
                                        "verdict": {
                                            "type": "string",
                                            "enum": ["yes", "no", "idk"],
                                        },
                                        "rationale": {"type": "string"},
                                    },
                                    "required": ["criterion", "verdict", "rationale"],
                                },
                            },
                        },
                        "required": ["summary", "advisories"],
                    },
                },
            },
        )

        content = response["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return json.loads(content)


def build_judge_client() -> JudgeClient:
    return LiteLLMJudgeClient()


def validate_judge_response(raw_response: object) -> object:
    JudgeResponseModel, ValidationError = load_judge_models()
    try:
        return JudgeResponseModel.model_validate(raw_response)
    except ValidationError as error:
        raise JudgeStructuredOutputError(
            f"Judge model returned invalid structured output: {error}"
        ) from error


class LiteLLMScopeGuardClient:
    def __init__(self) -> None:
        try:
            if os.environ.get("SARINGAN_FORCE_MISSING_JUDGE_DEPS") == "1":
                raise ImportError("forced missing judge dependencies")
            from litellm import completion
        except ImportError as error:
            raise JudgeDependencyError(
                "Judge dependencies are not installed. Reinstall with the 'judge' extra."
            ) from error
        self._completion = completion

    def evaluate_scope(
        self,
        request: JudgeRequest,
        judge_input: JudgeInput,
        changed_files: list[str],
    ) -> object:
        response = self._completion(
            model=request.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the Saringan Scope Guard. Your job is to judge whether "
                        "the changed files in a diff stay within the intended scope of "
                        "an issue specification. Return JSON with a verdict (yes, no, or "
                        "idk) and a concise rationale."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "changed_files": changed_files,
                            "diff_text": judge_input.diff_text,
                            "issue_text": judge_input.issue_text,
                            "conventions_text": judge_input.conventions_text,
                        }
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "saringan_scope_guard",
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "verdict": {
                                "type": "string",
                                "enum": ["yes", "no", "idk"],
                            },
                            "rationale": {"type": "string"},
                        },
                        "required": ["verdict", "rationale"],
                    },
                },
            },
        )

        content = response["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return json.loads(content)


def build_scope_guard_client() -> ScopeGuardClient:
    return LiteLLMScopeGuardClient()


def validate_scope_guard_response(raw_response: object) -> object:
    ScopeGuardVerdictModel, ValidationError = load_scope_guard_models()
    try:
        return ScopeGuardVerdictModel.model_validate(raw_response)
    except ValidationError as error:
        raise JudgeStructuredOutputError(
            f"Scope guard returned invalid structured output: {error}"
        ) from error


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

            canonical_type = DEPRECATED_TYPE_ALIASES.get(str(check_type), str(check_type))
            check["type"] = canonical_type

            unknown_fields = sorted(set(check) - CHECK_FIELDS_BY_TYPE[canonical_type])
            if unknown_fields:
                field_list = ", ".join(unknown_fields)
                label = "command" if canonical_type == "command" else canonical_type
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


def read_judge_input(request: JudgeRequest) -> JudgeInput:
    return JudgeInput(
        diff_text=request.diff_path.read_text(),
        issue_text=request.issue_path.read_text(),
        conventions_text=(
            request.conventions_path.read_text()
            if request.conventions_path is not None
            else None
        ),
    )


def extract_changed_files(diff_text: str) -> list[str]:
    changed_files: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        candidate = parts[3]
        if candidate.startswith("b/"):
            candidate = candidate[2:]
        if candidate not in changed_files:
            changed_files.append(candidate)
    return changed_files


def compute_completion_score(criteria: list[object]) -> float:
    """Deterministic completion score from acceptance criteria verdicts.

    Score = yes_count / (yes_count + no_count).  Criteria with verdict
    ``idk`` are excluded from the denominator and reported as advisory.
    An empty or all-``idk`` criteria list yields a score of 0.0.
    """
    verdicts = [c.verdict for c in criteria]
    decided = [v for v in verdicts if v != "idk"]
    if not decided:
        return 0.0
    yes_count = sum(1 for v in decided if v == "yes")
    return yes_count / len(decided)


def compute_completion_score_from_raw(criteria: list[dict[str, object]]) -> float:
    """Compute completion score from raw dict acceptance criteria (harness output)."""
    if not criteria:
        return 0.0
    verdicts = [c.get("verdict") for c in criteria]
    decided = [v for v in verdicts if v != "idk"]
    if not decided:
        return 0.0
    yes_count = sum(1 for v in decided if v == "yes")
    return yes_count / len(decided)


def detect_debug_artifacts(diff_text: str) -> list[dict[str, object]]:
    advisories: list[dict[str, object]] = []
    current_file: str | None = None
    added_line_number = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current_file = None
            added_line_number = 0
            if len(parts) >= 4:
                current_file = parts[3][2:] if parts[3].startswith("b/") else parts[3]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_line_number += 1
        snippet = line[1:]
        if not any(pattern in snippet for pattern in DEBUG_ARTIFACT_PATTERNS):
            continue
        advisories.append(
            {
                "kind": "debug_artifact",
                "file": current_file,
                "line": added_line_number,
                "snippet": bound_output(snippet),
            }
        )
    return advisories


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
    stable_check_id = str(check["type"])
    started_at = time.perf_counter()
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=target_path,
            env=env,
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
            "stable_check_id": stable_check_id,
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
        "stable_check_id": stable_check_id,
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
        "stable_check_id": str(check["type"]),
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

    declared_checks_list = config_data.get("checks")
    if not declared_checks_list:
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=str(resolved_config_path.resolve()),
                started_at=started_at,
                finished_at=finished_at,
                message="Configuration does not declare any checks. A saringan.toml must declare at least one [[checks]] entry.",
            ),
            EXIT_ERROR,
        )

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


class HarnessExecutionError(Exception):
    """Raised when a judge harness execution fails.

    Carries diagnostic evidence from the failed harness run so that
    callers can include it in Check Evidence.
    """

    def __init__(
        self,
        message: str,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        result_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.result_path = result_path


def execute_judge_harness(
    harness_command: str,
    request: JudgeRequest,
    judge_input: JudgeInput,
    result_path: Path,
    provider: str | None = None,
    timeout: int | None = None,
) -> tuple[dict[str, object], dict[str, object] | None, str, str, int]:
    """Execute a judge harness command with the given context.

    Returns:
        (harness_input, result_artifact_dict, stdout, stderr, exit_code)
        on success.  The result_artifact_dict is the raw JSON object written
        by the harness.

    Raises:
        OSError if the harness command cannot be started.
        HarnessExecutionError if the harness exits non-zero, does not write
            a result artifact, writes invalid JSON, or fails schema validation.
    """
    from saringan.judge_harness import HarnessValidationError, validate_harness_result

    harness_args = shlex.split(harness_command)
    if not harness_args:
        raise OSError("Empty harness command")

    harness_input: dict[str, object] = {
        "target_path": str(request.target_path.resolve()),
        "diff_text": judge_input.diff_text,
        "issue_text": judge_input.issue_text,
        "conventions_text": judge_input.conventions_text,
        "model": request.model,
        "provider": provider,
    }

    harness_input_json = json.dumps(harness_input)

    env = os.environ.copy()
    env["SARINGAN_RESULT_PATH"] = str(result_path)
    env.pop("VIRTUAL_ENV", None)

    try:
        completed = subprocess.run(
            harness_args,
            input=harness_input_json,
            capture_output=True,
            text=True,
            cwd=request.target_path,
            env=env,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise HarnessExecutionError(
            f"Judge harness timed out after {timeout} seconds",
            stdout=error.stdout.decode() if error.stdout else "",
            stderr=error.stderr.decode() if error.stderr else "",
            exit_code=None,
            result_path=str(result_path) if result_path.exists() else None,
        ) from error

    harness_stdout = completed.stdout
    harness_stderr = completed.stderr
    harness_exit_code = completed.returncode

    if completed.returncode != 0:
        raise HarnessExecutionError(
            f"Judge harness exited with code {completed.returncode}",
            stdout=harness_stdout,
            stderr=harness_stderr,
            exit_code=harness_exit_code,
            result_path=str(result_path) if result_path.exists() else None,
        )

    if not result_path.exists():
        raise HarnessExecutionError(
            "Judge harness did not write result artifact",
            stdout=harness_stdout,
            stderr=harness_stderr,
            exit_code=harness_exit_code,
            result_path=None,
        )

    try:
        raw_artifact = json.loads(result_path.read_text())
    except json.JSONDecodeError as error:
        raise HarnessExecutionError(
            f"Judge harness wrote invalid JSON result artifact: {error}",
            stdout=harness_stdout,
            stderr=harness_stderr,
            exit_code=harness_exit_code,
            result_path=str(result_path),
        ) from error

    # Validate against the Judge Harness Protocol Contract
    try:
        validated = validate_harness_result(raw_artifact)
    except HarnessValidationError as error:
        raise HarnessExecutionError(
            str(error),
            stdout=harness_stdout,
            stderr=harness_stderr,
            exit_code=harness_exit_code,
            result_path=str(result_path),
        ) from error
    artifact_dict: dict[str, object] = {
        "summary": validated.summary,
        "scope_guard": {
            "verdict": validated.scope_guard.verdict,
            "rationale": validated.scope_guard.rationale,
        },
        "advisories": [
            {"kind": adv.kind, "file": adv.file, "line": adv.line, "snippet": adv.snippet}
            for adv in validated.advisories
        ],
        "acceptance_criteria": [
            {"criterion": c.criterion, "verdict": c.verdict, "rationale": c.rationale}
            for c in validated.acceptance_criteria
        ],
    }

    return harness_input, artifact_dict, harness_stdout, harness_stderr, harness_exit_code


def judge_target(
    request: JudgeRequest,
    judge_client: JudgeClient | None = None,
    scope_guard_client: ScopeGuardClient | None = None,
    harness_command: str | None = None,
    harness_provider: str | None = None,
    harness_name: str | None = None,
    harness_timeout: int | None = None,
    use_legacy_adapter: bool = False,
    legacy_provider: str | None = None,
) -> tuple[ValidationResult, int]:
    started_at = iso_now()
    resolved_target_path = str(request.target_path.resolve())
    required_paths = [
        request.target_path,
        request.diff_path,
        request.issue_path,
        request.conventions_path,
    ]

    missing_path = next(
        (path for path in required_paths if path is not None and not path.exists()),
        None,
    )
    if missing_path is not None:
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
                message=f"Required judge input does not exist: {missing_path.resolve()}",
            ),
            EXIT_ERROR,
        )

    judge_input = read_judge_input(request)
    changed_files = extract_changed_files(judge_input.diff_text)

    # ── Legacy adapter path ────────────────────────────────────────────────
    if use_legacy_adapter:
        from saringan.judge_harness import HarnessValidationError, validate_harness_result
        from saringan.legacy_adapter import run_legacy_adapter

        resolved_legacy_name = harness_name or "legacy-litellm"
        resolved_legacy_provider = legacy_provider or harness_provider or "litellm"

        try:
            harness_artifact = run_legacy_adapter(
                request,
                judge_input,
                judge_client=judge_client,
                scope_guard_client=scope_guard_client,
                changed_files=changed_files,
            )
            validated = validate_harness_result(harness_artifact)
        except (JudgeDependencyError, JudgeStructuredOutputError, OSError, HarnessValidationError) as error:
            finished_at = iso_now()
            return (
                ValidationResult(
                    status="error",
                    target_path=resolved_target_path,
                    config_path=None,
                    started_at=started_at,
                    finished_at=finished_at,
                    message=str(error),
                ),
                EXIT_ERROR,
            )

        debug_advisories = detect_debug_artifacts(judge_input.diff_text)
        scope_guard_dict = harness_artifact["scope_guard"]
        advisories = list(harness_artifact.get("advisories", []))
        acceptance_criteria = list(harness_artifact.get("acceptance_criteria", []))
        completion_score = compute_completion_score_from_raw(acceptance_criteria)

        finished_at = iso_now()
        return (
            ValidationResult(
                status="passed",
                check_outcomes=[
                    {
                        "id": "contextual_judge",
                        "stable_check_id": "contextual_judge",
                        "status": "passed",
                        "blocking": False,
                        "message": validated.summary,
                        "evidence": {
                            "target_path": resolved_target_path,
                            "diff_path": str(request.diff_path.resolve()),
                            "issue_path": str(request.issue_path.resolve()),
                            "conventions_path": (
                                str(request.conventions_path.resolve())
                                if request.conventions_path is not None
                                else None
                            ),
                            "model": request.model,
                            "harness_name": resolved_legacy_name,
                            "provider": resolved_legacy_provider,
                            "changed_files": changed_files,
                            "scope_guard": scope_guard_dict,
                            "advisories": advisories,
                            "acceptance_criteria": acceptance_criteria,
                            "completion_score": completion_score,
                            "input": {
                                "diff_text": bound_output(judge_input.diff_text),
                                "issue_text": bound_output(judge_input.issue_text),
                                "conventions_text": (
                                    bound_output(judge_input.conventions_text)
                                    if judge_input.conventions_text is not None
                                    else None
                                ),
                            },
                        },
                    }
                ],
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
            ),
            EXIT_PASSED,
        )

    # ── Harness execution path ──────────────────────────────────────────────
    if harness_command is not None:
        harness_result_dir = tempfile.mkdtemp(prefix="saringan-harness-")
        harness_result_path = Path(harness_result_dir) / "result.json"
        try:
            harness_input_data, harness_artifact, harness_stdout, harness_stderr, harness_exit_code = execute_judge_harness(
                harness_command,
                request,
                judge_input,
                harness_result_path,
                provider=harness_provider,
                timeout=harness_timeout,
            )
        except HarnessExecutionError as error:
            finished_at = iso_now()
            error_evidence: dict[str, object] = {
                "target_path": resolved_target_path,
                "diff_path": str(request.diff_path.resolve()),
                "issue_path": str(request.issue_path.resolve()),
                "conventions_path": (
                    str(request.conventions_path.resolve())
                    if request.conventions_path is not None
                    else None
                ),
                "model": request.model,
                "harness_command": harness_command,
                "harness_name": harness_name,
                "provider": harness_provider,
                "changed_files": changed_files,
                "harness_stdout": bound_output(error.stdout),
                "harness_stderr": bound_output(error.stderr),
                "harness_exit_code": error.exit_code,
            }
            if error.result_path is not None:
                error_evidence["result_artifact_path"] = error.result_path
            return (
                ValidationResult(
                    status="error",
                    check_outcomes=[
                        {
                            "id": "contextual_judge",
                            "stable_check_id": "contextual_judge",
                            "status": "error",
                            "blocking": False,
                            "message": error.message,
                            "evidence": error_evidence,
                        }
                    ],
                    target_path=resolved_target_path,
                    config_path=None,
                    started_at=started_at,
                    finished_at=finished_at,
                ),
                EXIT_ERROR,
            )
        except OSError as error:
            finished_at = iso_now()
            return (
                ValidationResult(
                    status="error",
                    check_outcomes=[
                        {
                            "id": "contextual_judge",
                            "stable_check_id": "contextual_judge",
                            "status": "error",
                            "blocking": False,
                            "message": str(error),
                            "evidence": {
                                "target_path": resolved_target_path,
                                "harness_command": harness_command,
                                "harness_name": harness_name,
                                "provider": harness_provider,
                            },
                        }
                    ],
                    target_path=resolved_target_path,
                    config_path=None,
                    started_at=started_at,
                    finished_at=finished_at,
                ),
                EXIT_ERROR,
            )
        finally:
            shutil.rmtree(harness_result_dir, ignore_errors=True)

        debug_advisories = detect_debug_artifacts(judge_input.diff_text)
        scope_guard = harness_artifact["scope_guard"]
        advisories = list(harness_artifact.get("advisories", []))
        acceptance_criteria = list(harness_artifact.get("acceptance_criteria", []))
        completion_score = compute_completion_score_from_raw(acceptance_criteria)

        finished_at = iso_now()
        return (
            ValidationResult(
                status="passed",
                check_outcomes=[
                    {
                        "id": "contextual_judge",
                        "stable_check_id": "contextual_judge",
                        "status": "passed",
                        "blocking": False,
                        "message": harness_artifact["summary"],
                        "evidence": {
                            "target_path": resolved_target_path,
                            "diff_path": str(request.diff_path.resolve()),
                            "issue_path": str(request.issue_path.resolve()),
                            "conventions_path": (
                                str(request.conventions_path.resolve())
                                if request.conventions_path is not None
                                else None
                            ),
                            "model": request.model,
                            "harness_command": harness_command,
                            "harness_name": harness_name,
                            "provider": harness_provider,
                            "changed_files": changed_files,
                            "scope_guard": scope_guard,
                            "advisories": advisories,
                            "acceptance_criteria": acceptance_criteria,
                            "completion_score": completion_score,
                            "harness_stdout": bound_output(harness_stdout),
                            "harness_stderr": bound_output(harness_stderr),
                            "harness_exit_code": harness_exit_code,
                            "result_artifact_path": str(harness_result_path.resolve()),
                            "input": {
                                "diff_text": bound_output(judge_input.diff_text),
                                "issue_text": bound_output(judge_input.issue_text),
                                "conventions_text": (
                                    bound_output(judge_input.conventions_text)
                                    if judge_input.conventions_text is not None
                                    else None
                                ),
                            },
                        },
                    }
                ],
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
            ),
            EXIT_PASSED,
        )

    # ── Built-in LLM client path ────────────────────────────────────────────

    scope_guard_verdict: dict[str, str] | None = None
    try:
        sg_client = (
            scope_guard_client
            if scope_guard_client is not None
            else build_scope_guard_client()
        )
        raw_scope = sg_client.evaluate_scope(request, judge_input, changed_files)
        validated_scope = validate_scope_guard_response(raw_scope)
        scope_guard_verdict = validated_scope.model_dump()
    except (
        JudgeDependencyError,
        JudgeStructuredOutputError,
        OSError,
        json.JSONDecodeError,
    ) as error:
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
                message=str(error),
            ),
            EXIT_ERROR,
        )

    try:
        client = judge_client if judge_client is not None else build_judge_client()
        raw_response = client.evaluate(request, judge_input)
        validated_response = validate_judge_response(raw_response)
    except (JudgeDependencyError, JudgeStructuredOutputError, OSError, json.JSONDecodeError) as error:
        finished_at = iso_now()
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=None,
                started_at=started_at,
                finished_at=finished_at,
                message=str(error),
            ),
            EXIT_ERROR,
        )

    finished_at = iso_now()

    return (
        ValidationResult(
            status="passed",
            check_outcomes=[
                {
                    "id": "contextual_judge",
                    "stable_check_id": "contextual_judge",
                    "status": "passed",
                    "blocking": False,
                    "message": validated_response.summary,
                    "evidence": {
                        "target_path": resolved_target_path,
                        "diff_path": str(request.diff_path.resolve()),
                        "issue_path": str(request.issue_path.resolve()),
                        "conventions_path": (
                            str(request.conventions_path.resolve())
                            if request.conventions_path is not None
                            else None
                        ),
                        "model": request.model,
                        "changed_files": changed_files,
                        "scope_guard": scope_guard_verdict,
                        "advisories": [
                            advisory.model_dump() for advisory in validated_response.advisories
                        ],
                        "acceptance_criteria": [
                            criterion.model_dump()
                            for criterion in validated_response.acceptance_criteria
                        ],
                        "completion_score": compute_completion_score(
                            validated_response.acceptance_criteria
                        ),
                        "input": {
                            "diff_text": bound_output(judge_input.diff_text),
                            "issue_text": bound_output(judge_input.issue_text),
                            "conventions_text": (
                                bound_output(judge_input.conventions_text)
                                if judge_input.conventions_text is not None
                                else None
                            ),
                        },
                    },
                }
            ],
            target_path=resolved_target_path,
            config_path=None,
            started_at=started_at,
            finished_at=finished_at,
        ),
        EXIT_PASSED,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saringan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("target_path")
    validate_parser.add_argument("--config", dest="config_path")
    validate_parser.add_argument("--log-dir", dest="log_dir")
    validate_parser.add_argument("--json", action="store_true", dest="json_mode")

    judge_parser = subparsers.add_parser("judge")
    judge_parser.add_argument("target_path")
    judge_parser.add_argument("--diff", required=True, dest="diff_path")
    judge_parser.add_argument("--issue", required=True, dest="issue_path")
    judge_parser.add_argument("--conventions", dest="conventions_path")
    judge_parser.add_argument("--model", dest="model", default=None)
    judge_parser.add_argument("--harness", dest="harness_command", default=None)
    judge_parser.add_argument("--judge-config", dest="judge_config_path", default=None)
    judge_parser.add_argument("--provider", dest="provider", default=None)
    judge_parser.add_argument("--timeout", dest="timeout", type=int, default=None)
    judge_parser.add_argument("--json", action="store_true", dest="json_mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        config_path = Path(args.config_path) if args.config_path else None
        log_dir = Path(args.log_dir) if args.log_dir else None
        result, exit_code = validate_target(
            Path(args.target_path),
            config_path=config_path,
            log_dir=log_dir,
        )
        print(f"Validating {result.target_path}", file=sys.stderr)
        print(f"Validation {result.status}: {result.target_path}", file=sys.stderr)
    elif args.command == "judge":
        # Resolve judge config path: CLI overrides env var
        judge_config_path_str = args.judge_config_path or os.environ.get(
            "SARINGAN_JUDGE_CONFIG"
        )

        # Resolve harness: CLI overrides env var
        harness_arg = args.harness_command or os.environ.get(
            "SARINGAN_JUDGE_HARNESS"
        )

        # Resolve provider override: CLI overrides env var
        provider_override = args.provider or os.environ.get(
            "SARINGAN_JUDGE_PROVIDER"
        ) or None

        # Resolve model override: CLI overrides env var, only if explicitly provided
        model_cli = args.model  # None if not passed
        model_env = os.environ.get("SARINGAN_JUDGE_MODEL")
        model_override = model_cli or model_env  # None if neither provided

        # Load judge config if available
        judge_config = None
        if judge_config_path_str:
            from saringan.judge_config import (
                ConfigError,
                HarnessNotFoundError,
                load_judge_config,
                resolve_harness,
            )

            config_path = Path(judge_config_path_str)
            if not config_path.exists():
                finished_at = iso_now()
                result = ValidationResult(
                    status="error",
                    target_path=str(Path(args.target_path).resolve()),
                    config_path=None,
                    started_at=finished_at,
                    finished_at=finished_at,
                    message=(
                        f"Judge configuration file does not exist: "
                        f"{config_path.resolve()}"
                    ),
                )
                payload = json.dumps(asdict(result))
                print(result.message, file=sys.stderr)
                print(payload)
                return EXIT_ERROR

            loaded = load_judge_config(config_path)
            if isinstance(loaded, ConfigError):
                finished_at = iso_now()
                result = ValidationResult(
                    status="error",
                    target_path=str(Path(args.target_path).resolve()),
                    config_path=str(config_path.resolve()),
                    started_at=finished_at,
                    finished_at=finished_at,
                    message=loaded.message,
                )
                payload = json.dumps(asdict(result))
                print(result.message, file=sys.stderr)
                print(payload)
                return EXIT_ERROR

            judge_config = loaded

        # Determine harness command and name
        resolved_harness_command: str | None = None
        resolved_harness_name: str | None = None
        resolved_provider: str | None = provider_override
        resolved_model: str = model_override or "gpt-5"
        resolved_timeout: int | None = None

        if harness_arg is not None and judge_config is not None:
            # Try named harness lookup
            try:
                provider, model, command, timeout = resolve_harness(
                    judge_config,
                    harness_name=harness_arg,
                    provider_override=provider_override,
                    model_override=model_override,
                    timeout_override=args.timeout
                    or int(tout)
                    if (tout := os.environ.get("SARINGAN_JUDGE_TIMEOUT"))
                    else None,
                )
                resolved_harness_command = " ".join(
                    shlex.quote(part) for part in command
                )
                resolved_harness_name = harness_arg
                resolved_provider = provider
                resolved_model = model
                resolved_timeout = timeout
            except HarnessNotFoundError as error:
                finished_at = iso_now()
                result = ValidationResult(
                    status="error",
                    target_path=str(Path(args.target_path).resolve()),
                    config_path=str(config_path.resolve()),
                    started_at=finished_at,
                    finished_at=finished_at,
                    message=str(error),
                )
                payload = json.dumps(asdict(result))
                print(result.message, file=sys.stderr)
                print(payload)
                return EXIT_ERROR
        elif harness_arg is not None and judge_config is None:
            # No judge config: treat harness_arg as raw command (backward compat)
            resolved_harness_command = harness_arg
        elif harness_arg is None and judge_config is not None:
            # Use default harness from config
            try:
                provider, model, command, timeout = resolve_harness(
                    judge_config,
                    harness_name=None,
                    provider_override=provider_override,
                    model_override=model_override,
                    timeout_override=args.timeout
                    or int(tout)
                    if (tout := os.environ.get("SARINGAN_JUDGE_TIMEOUT"))
                    else None,
                )
                resolved_harness_command = " ".join(
                    shlex.quote(part) for part in command
                )
                resolved_harness_name = judge_config.default_harness
                resolved_provider = provider
                resolved_model = model
                resolved_timeout = timeout
            except HarnessNotFoundError as error:
                finished_at = iso_now()
                result = ValidationResult(
                    status="error",
                    target_path=str(Path(args.target_path).resolve()),
                    config_path=str(config_path.resolve()),
                    started_at=finished_at,
                    finished_at=finished_at,
                    message=str(error),
                )
                payload = json.dumps(asdict(result))
                print(result.message, file=sys.stderr)
                print(payload)
                return EXIT_ERROR
        # else: no harness_arg, no judge_config → use built-in LLM (backward compat)

        request = JudgeRequest(
            target_path=Path(args.target_path),
            diff_path=Path(args.diff_path),
            issue_path=Path(args.issue_path),
            conventions_path=(
                Path(args.conventions_path) if args.conventions_path else None
            ),
            model=resolved_model,
        )
        result, exit_code = judge_target(
            request,
            harness_command=resolved_harness_command,
            harness_provider=resolved_provider,
            harness_name=resolved_harness_name,
            harness_timeout=resolved_timeout,
        )
        print(f"Judging {result.target_path}", file=sys.stderr)
        print(f"Judge {result.status}: {result.target_path}", file=sys.stderr)
    else:
        parser.error("unknown command")

    payload = json.dumps(asdict(result))
    if result.message:
        print(result.message, file=sys.stderr)
    # Machine-readable Validation Result JSON always goes to stdout.
    print(payload)
    return exit_code
