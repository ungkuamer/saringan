"""
Tests for Judge Harness Configuration.

Tests the judge_config module that loads named harnesses, provider, model,
command templates, and timeout from a TOML configuration file.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# RED: tests that will fail until the judge_config module exists
# ---------------------------------------------------------------------------


def test_loads_single_harness(tmp_path: Path) -> None:
    """A judge config with one named harness is loaded correctly."""
    from saringan.judge_config import load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
default_harness = "litellm"

[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)

    assert config.default_harness == "litellm"
    assert len(config.harnesses) == 1
    h = config.harnesses[0]
    assert h.name == "litellm"
    assert h.provider == "litellm"
    assert h.model == "gpt-5"
    assert h.command == ["python3", "-m", "my_harness"]
    assert h.timeout == 300


def test_loads_multiple_harnesses_with_default(tmp_path: Path) -> None:
    """Multiple harnesses with a default are loaded in order."""
    from saringan.judge_config import load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
default_harness = "openai"

[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "litellm_harness"]
timeout = 300

[[harnesses]]
name = "openai"
provider = "openai"
model = "gpt-4"
command = ["python3", "-m", "openai_harness"]
timeout = 180
"""
    )

    config = load_judge_config(config_path)

    assert config.default_harness == "openai"
    assert len(config.harnesses) == 2
    assert config.harnesses[0].name == "litellm"
    assert config.harnesses[1].name == "openai"
    assert config.harnesses[1].provider == "openai"
    assert config.harnesses[1].model == "gpt-4"


def test_default_harness_is_optional(tmp_path: Path) -> None:
    """default_harness may be omitted (None)."""
    from saringan.judge_config import load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)

    assert config.default_harness is None
    assert len(config.harnesses) == 1


def test_reports_error_for_unknown_default_harness(tmp_path: Path) -> None:
    """default_harness referencing a non-existent harness is a config error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
default_harness = "nonexistent"

[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "default_harness" in result.message
    assert "nonexistent" in result.message


def test_reports_error_for_duplicate_harness_names(tmp_path: Path) -> None:
    """Duplicate harness names produce a config error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "harness"]
timeout = 300

[[harnesses]]
name = "litellm"
provider = "openai"
model = "gpt-4"
command = ["python3", "-m", "other_harness"]
timeout = 180
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "Duplicate" in result.message
    assert "litellm" in result.message


def test_reports_error_for_missing_required_harness_fields(tmp_path: Path) -> None:
    """Harnesses require name, provider, model, command, timeout."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
# missing model, command, timeout
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "model" in result.message or "command" in result.message or "timeout" in result.message


def test_reports_error_for_invalid_toml(tmp_path: Path) -> None:
    """Unparseable TOML produces a config error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text("this is not valid toml {{{")

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "TOML" in result.message or "toml" in result.message.lower()


def test_reports_error_for_command_not_list(tmp_path: Path) -> None:
    """command must be an argument vector (list of strings)."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = "python3 -m my_harness"
timeout = 300
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "command" in result.message


def test_reports_error_for_unknown_top_level_fields(tmp_path: Path) -> None:
    """Unknown top-level fields produce a config error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
unknown_field = true

[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "unknown" in result.message.lower() or "Unknown" in result.message


def test_reports_error_for_unknown_harness_fields(tmp_path: Path) -> None:
    """Unknown fields within a harness definition produce a config error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
extra_field = true
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "extra_field" in result.message


def test_reports_error_for_empty_harnesses_list(tmp_path: Path) -> None:
    """A config with no harnesses is an error."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text("default_harness = \"litellm\"\n")

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "harness" in result.message.lower()


def test_reports_error_for_non_integer_timeout(tmp_path: Path) -> None:
    """timeout must be an integer."""
    from saringan.judge_config import ConfigError, load_judge_config

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = "not-a-number"
"""
    )

    result = load_judge_config(config_path)
    assert isinstance(result, ConfigError)
    assert "timeout" in result.message


def test_resolve_harness_by_name(tmp_path: Path) -> None:
    """resolve_harness finds a named harness and returns its config."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "openai"
provider = "openai"
model = "gpt-4"
command = ["python3", "-m", "openai_harness"]
timeout = 180
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, "openai", provider_override=None, model_override=None, timeout_override=None
    )

    assert provider == "openai"
    assert model == "gpt-4"
    assert command == ["python3", "-m", "openai_harness"]
    assert timeout == 180


def test_resolve_harness_returns_default_when_name_is_none(tmp_path: Path) -> None:
    """When harness_name is None, resolve_harness returns the default harness."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
default_harness = "litellm"

[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "litellm_harness"]
timeout = 300

[[harnesses]]
name = "openai"
provider = "openai"
model = "gpt-4"
command = ["python3", "-m", "openai_harness"]
timeout = 180
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, harness_name=None, provider_override=None, model_override=None, timeout_override=None
    )

    assert provider == "litellm"
    assert model == "gpt-5"
    assert command == ["python3", "-m", "litellm_harness"]


def test_resolve_harness_with_provider_override(tmp_path: Path) -> None:
    """provider_override replaces the harness provider."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness", "--provider", "{provider}"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, "litellm", provider_override="openai", model_override=None, timeout_override=None
    )

    assert provider == "openai"
    assert model == "gpt-5"
    # {provider} template should be substituted
    assert command == ["python3", "-m", "my_harness", "--provider", "openai"]


def test_resolve_harness_with_model_override(tmp_path: Path) -> None:
    """model_override replaces the harness model."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness", "--model", "{model}"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, "litellm", provider_override=None, model_override="claude-sonnet-4", timeout_override=None
    )

    assert provider == "litellm"
    assert model == "claude-sonnet-4"
    assert command == ["python3", "-m", "my_harness", "--model", "claude-sonnet-4"]


def test_resolve_harness_with_timeout_override(tmp_path: Path) -> None:
    """timeout_override replaces the harness timeout."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, "litellm", provider_override=None, model_override=None, timeout_override=600
    )

    assert timeout == 600


def test_resolve_harness_raises_for_unknown_name(tmp_path: Path) -> None:
    """resolve_harness raises ValueError for unknown harness name."""
    from saringan.judge_config import HarnessNotFoundError, load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)

    with pytest.raises(HarnessNotFoundError, match="unknown-harness"):
        resolve_harness(
            config, "unknown-harness", provider_override=None, model_override=None, timeout_override=None
        )


def test_resolve_harness_raises_when_no_default_and_name_is_none(tmp_path: Path) -> None:
    """resolve_harness raises ValueError when no name and no default."""
    from saringan.judge_config import HarnessNotFoundError, load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "my_harness"]
timeout = 300
"""
    )

    config = load_judge_config(config_path)

    with pytest.raises(HarnessNotFoundError, match="No harness"):
        resolve_harness(
            config, harness_name=None, provider_override=None, model_override=None, timeout_override=None
        )


def test_resolve_harness_template_substitutes_both_provider_and_model(tmp_path: Path) -> None:
    """Both {provider} and {model} templates are substituted in command."""
    from saringan.judge_config import load_judge_config, resolve_harness

    config_path = tmp_path / "judge.toml"
    config_path.write_text(
        """\
[[harnesses]]
name = "custom"
provider = "anthropic"
model = "claude-3"
command = ["python3", "-m", "harness", "--provider", "{provider}", "--model", "{model}"]
timeout = 120
"""
    )

    config = load_judge_config(config_path)
    provider, model, command, timeout = resolve_harness(
        config, "custom",
        provider_override="openai",
        model_override="gpt-4",
        timeout_override=None,
    )

    assert provider == "openai"
    assert model == "gpt-4"
    assert command == ["python3", "-m", "harness", "--provider", "openai", "--model", "gpt-4"]
