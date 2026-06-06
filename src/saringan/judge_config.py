"""
Judge Harness Configuration.

Loads named harness definitions from a TOML configuration file.
Each harness specifies a provider, model, command template, and timeout.
The resolved harness selection supports CLI and environment overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


ALLOWED_TOP_LEVEL_FIELDS = frozenset({"default_harness", "harnesses"})
ALLOWED_HARNESS_FIELDS = frozenset({"name", "provider", "model", "command", "timeout"})
REQUIRED_HARNESS_FIELDS = frozenset({"name", "provider", "model", "command", "timeout"})


class ConfigError(Exception):
    """Raised when judge configuration is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HarnessNotFoundError(ValueError):
    """Raised when a requested harness name cannot be found."""


@dataclass
class JudgeHarnessConfig:
    """A single named harness definition."""

    name: str
    provider: str
    model: str
    command: list[str]
    timeout: int


@dataclass
class JudgeConfig:
    """Loaded judge harness configuration."""

    default_harness: str | None
    harnesses: list[JudgeHarnessConfig] = field(default_factory=list)


def _check_unknown_fields(data: dict, allowed: frozenset[str], path: str) -> ConfigError | None:
    extra = sorted(set(data) - allowed)
    if extra:
        return ConfigError(
            f"Unknown field(s) in {path}: {', '.join(extra)}"
        )
    return None


def load_judge_config(config_path: Path) -> JudgeConfig | ConfigError:
    """Load and validate a judge harness configuration from a TOML file.

    Returns a :class:`JudgeConfig` on success, or a :class:`ConfigError`
    with a descriptive message on failure.
    """
    try:
        data = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as error:
        return ConfigError(message=f"Invalid configuration TOML: {error}")

    err = _check_unknown_fields(data, ALLOWED_TOP_LEVEL_FIELDS, "judge configuration")
    if err is not None:
        return err

    harnesses_raw = data.get("harnesses")
    if not harnesses_raw:
        return ConfigError(
            message="Configuration does not declare any harnesses. "
            "A judge configuration must declare at least one [[harnesses]] entry."
        )

    if not isinstance(harnesses_raw, list):
        return ConfigError(message="harnesses must be a list")

    seen_names: set[str] = set()
    harnesses: list[JudgeHarnessConfig] = []

    for i, raw in enumerate(harnesses_raw):
        if not isinstance(raw, dict):
            return ConfigError(
                message=f"harnesses[{i}] must be a table (dict), "
                f"got {type(raw).__name__}"
            )

        err = _check_unknown_fields(raw, ALLOWED_HARNESS_FIELDS, f"harnesses[{i}]")
        if err is not None:
            return err

        missing = REQUIRED_HARNESS_FIELDS - set(raw)
        if missing:
            return ConfigError(
                message=f"harnesses[{i}] is missing required field(s): "
                f"{', '.join(sorted(missing))}"
            )

        name = raw["name"]
        if not isinstance(name, str) or not name.strip():
            return ConfigError(
                message=f"harnesses[{i}].name must be a non-empty string"
            )

        if name in seen_names:
            return ConfigError(
                message=f"Duplicate harness name: {name}"
            )
        seen_names.add(name)

        provider = raw["provider"]
        if not isinstance(provider, str) or not provider.strip():
            return ConfigError(
                message=f"harnesses[{i}].provider must be a non-empty string"
            )

        model = raw["model"]
        if not isinstance(model, str) or not model.strip():
            return ConfigError(
                message=f"harnesses[{i}].model must be a non-empty string"
            )

        command = raw["command"]
        if not isinstance(command, list) or any(
            not isinstance(part, str) for part in command
        ):
            return ConfigError(
                message=f"harnesses[{i}].command must be an argument vector "
                f"(list of strings)"
            )

        timeout = raw["timeout"]
        if not isinstance(timeout, int):
            return ConfigError(
                message=f"harnesses[{i}].timeout must be an integer"
            )

        harnesses.append(
            JudgeHarnessConfig(
                name=name,
                provider=provider,
                model=model,
                command=list(command),
                timeout=timeout,
            )
        )

    default_harness = data.get("default_harness")
    if default_harness is not None:
        if not isinstance(default_harness, str) or not default_harness.strip():
            return ConfigError(
                message="default_harness must be a non-empty string"
            )
        if default_harness not in seen_names:
            return ConfigError(
                message=f"default_harness '{default_harness}' does not match "
                f"any declared harness name"
            )

    return JudgeConfig(
        default_harness=default_harness,
        harnesses=harnesses,
    )


def resolve_harness(
    config: JudgeConfig,
    harness_name: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    timeout_override: int | None = None,
) -> tuple[str, str, list[str], int]:
    """Resolve a harness selection to its (provider, model, command, timeout).

    If *harness_name* is given, the harness with that name is selected.
    Otherwise, the *config.default_harness* is used.

    *provider_override*, *model_override*, and *timeout_override* replace
    the harness definition values when provided.

    Template substitution: ``{provider}`` and ``{model}`` in each command
    argument are replaced with the resolved provider and model values.

    Raises :class:`HarnessNotFoundError` if the requested harness name
    does not exist, or when no harness name is specified and there is no
    default.
    """
    if harness_name is not None:
        harness = next(
            (h for h in config.harnesses if h.name == harness_name), None
        )
        if harness is None:
            raise HarnessNotFoundError(
                f"Harness '{harness_name}' not found in configuration. "
                f"Available harnesses: {', '.join(h.name for h in config.harnesses)}"
            )
    elif config.default_harness is not None:
        harness = next(
            (h for h in config.harnesses if h.name == config.default_harness), None
        )
        if harness is None:
            raise HarnessNotFoundError(
                f"Default harness '{config.default_harness}' not found"
            )
    else:
        raise HarnessNotFoundError(
            "No harness specified and no default_harness configured"
        )

    provider = provider_override if provider_override is not None else harness.provider
    model = model_override if model_override is not None else harness.model
    timeout = timeout_override if timeout_override is not None else harness.timeout

    # Template substitution
    command = [
        arg.replace("{provider}", provider).replace("{model}", model)
        for arg in harness.command
    ]

    return provider, model, command, timeout
