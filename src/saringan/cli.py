from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import sys
import tomllib


EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


@dataclass
class ValidationResult:
    status: str
    check_outcomes: list[dict[str, object]] = field(default_factory=list)
    target_path: str = ""
    config_path: str | None = None
    started_at: str = ""
    finished_at: str = ""
    message: str | None = None


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def load_fixture_status(config_path: Path) -> str:
    data = tomllib.loads(config_path.read_text())
    return str(data.get("fixture_status", "passed"))


def validate_target(target_path: Path) -> tuple[ValidationResult, int]:
    started_at = iso_now()
    config_path = target_path / "saringan.toml"
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

    if not config_path.exists():
        finished_at = iso_now()
        resolved_config_path = str(config_path.resolve())
        return (
            ValidationResult(
                status="error",
                target_path=resolved_target_path,
                config_path=resolved_config_path,
                started_at=started_at,
                finished_at=finished_at,
                message=f"Configuration file does not exist: {resolved_config_path}",
            ),
            EXIT_ERROR,
        )

    status = load_fixture_status(config_path)
    finished_at = iso_now()
    result = ValidationResult(
        status=status,
        check_outcomes=[{"id": "fixture", "status": status}],
        target_path=resolved_target_path,
        config_path=str(config_path.resolve()),
        started_at=started_at,
        finished_at=finished_at,
    )
    exit_code = EXIT_PASSED if status == "passed" else EXIT_FAILED
    return result, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saringan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("target_path")
    validate_parser.add_argument("--json", action="store_true", dest="json_mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "validate":
        parser.error("unknown command")

    result, exit_code = validate_target(Path(args.target_path))
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
