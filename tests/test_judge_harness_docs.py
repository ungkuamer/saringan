"""
Tests for Judge Harness Protocol documentation (Issue #49).

These tests verify that the documentation covers every acceptance criterion
from issue #49 and remains consistent with the Saringan domain vocabulary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib


@pytest.fixture
def doc_path() -> Path:
    path = Path(__file__).resolve().parents[1] / "docs" / "judge-harness-protocol.md"
    if not path.exists():
        pytest.skip("Documentation file does not exist yet (RED)")
    return path


@pytest.fixture
def doc_text(doc_path: Path) -> str:
    return doc_path.read_text()


# ---------------------------------------------------------------------------
# Acceptance criterion 1: Protocol explanation
# ---------------------------------------------------------------------------


def test_doc_explains_judge_harness_protocol(doc_text: str) -> None:
    """Documentation explains the Judge Harness Protocol, including
    prompt/context inputs, result artifact, diagnostics, exit behavior,
    and schema enforcement."""
    # Check for protocol section/heading
    assert re.search(
        r"##\s+(Judge\s+Harness\s+Protocol|Protocol\s+Contract)", doc_text
    ), "Must have a Judge Harness Protocol section"

    # Verify sub-topics are covered
    topics = [
        (r"prompt|context|input", "prompt/context inputs"),
        (r"result\s+artifact|artifact\s+path", "result artifact"),
        (r"diagnostic|stdout|stderr", "diagnostics"),
        (r"exit\s+(code|behavior)|nonzero|timeout", "exit behavior"),
        (r"schema\s+enforcement|validate_harness_result|HarnessValidationError",
         "schema enforcement"),
    ]
    for pattern, label in topics:
        assert re.search(pattern, doc_text, re.IGNORECASE), (
            f"Documentation must cover {label}"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 2: Configuration example
# ---------------------------------------------------------------------------


def test_doc_shows_configuration_example(doc_text: str, doc_path: Path) -> None:
    """Documentation shows a Rangkai-like Judge Harness configuration example
    with named harnesses, default harness, provider, model, argument template,
    and timeout."""
    # Find TOML code blocks
    toml_blocks = re.findall(r"```toml\s*\n(.*?)```", doc_text, re.DOTALL)
    assert len(toml_blocks) > 0, "Must contain at least one TOML code block"

    config_example_found = False
    for block in toml_blocks:
        if "[[harnesses]]" in block:
            config_example_found = True
            # Validate the TOML parse
            parsed = tomllib.loads(block)
            assert "harnesses" in parsed, "Config example must have harnesses"
            harnesses = parsed["harnesses"]
            assert isinstance(harnesses, list)
            assert len(harnesses) >= 1, "Must declare at least one harness"

            # Check all required fields
            for h in harnesses:
                assert "name" in h
                assert "provider" in h
                assert "model" in h
                assert "command" in h
                assert "timeout" in h

            # Check for argument template ({provider}, {model})
            block_text = block
            assert "{provider}" in block_text or "{model}" in block_text, (
                "Example should show argument template substitution"
            )

            # Check for timeout
            assert any(h["timeout"] for h in harnesses), (
                "Example should include timeout values"
            )
            break

    assert config_example_found, (
        "Documentation must contain a [[harnesses]] TOML example"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 3: CLI and environment overrides
# ---------------------------------------------------------------------------


def test_doc_explains_cli_overrides(doc_text: str) -> None:
    """Documentation explains CLI overrides for harness, provider, model,
    and judge configuration path."""
    cli_override_terms = [
        "--harness",
        "--judge-config",
        "--provider",
        "--model",
    ]
    for term in cli_override_terms:
        assert term in doc_text, (
            f"Documentation must mention CLI override {term}"
        )


def test_doc_explains_env_overrides(doc_text: str) -> None:
    """Documentation explains environment variable overrides for harness,
    provider, model, and judge configuration path."""
    env_var_terms = [
        "SARINGAN_JUDGE_HARNESS",
        "SARINGAN_JUDGE_CONFIG",
        "SARINGAN_JUDGE_PROVIDER",
        "SARINGAN_JUDGE_MODEL",
        "SARINGAN_JUDGE_TIMEOUT",
    ]
    for term in env_var_terms:
        assert term in doc_text, (
            f"Documentation must mention env var {term}"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 4: Rangkai Integration guidance
# ---------------------------------------------------------------------------

# Note: This is tested via the README update, not the protocol doc

def test_readme_rangkai_integration_section_exists() -> None:
    """README has a Rangkai Integration section that references the Judge
    Harness without assuming LiteLLM is the only path."""
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    text = readme_path.read_text()

    # Should mention Rangkai Integration or the judge harness
    assert re.search(r"Rangkai|Judge\s+Harness|harness", text, re.IGNORECASE), (
        "README should mention Rangkai Integration or Judge Harness"
    )

    # Should not claim LiteLLM is the only path
    # (search for sentences that say LiteLLM is the sole/only/exclusive way to run the judge)
    exclusive_pattern = re.compile(
        r"(?:only|sole|exclusively)\s+(?:way|path|option|method).*litellm|"
        r"litellm.*(?:is|as)\s+the\s+(?:only|sole|exclusive)",
        re.IGNORECASE,
    )
    assert not exclusive_pattern.search(text), (
        "README should not claim LiteLLM is the only model execution path"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 5: Saringan owns Validation Result assembly
# ---------------------------------------------------------------------------


def test_doc_states_saringan_owns_validation_result(doc_text: str) -> None:
    """Documentation states that Saringan owns Validation Result assembly
    and deterministic evidence, while the harness supplies judge opinions."""
    ownership_patterns = [
        r"Saringan\s+owns.*[Vv]alidation\s+[Rr]esult",
        r"harness\s+supplies.*opinions",
        r"Saringan.*assembl.*[Vv]alidation\s+[Rr]esult",
        r"deterministic\s+evidence.*Saringan",
    ]
    found_any = any(
        re.search(pattern, doc_text) for pattern in ownership_patterns
    )
    assert found_any, (
        "Documentation must state that Saringan owns Validation Result assembly"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 6: Consistent with standalone CLI ADR
# ---------------------------------------------------------------------------


def test_doc_consistent_with_standalone_cli_adr(doc_text: str) -> None:
    """Documentation remains consistent with the standalone CLI ADR
    and Saringan domain vocabulary."""
    # Check domain vocabulary from CONTEXT.md is used correctly
    domain_terms = [
        "Validation Result",
        "Check Outcome",
        "Contextual Judge Gate",
        "Saringan Invocation",
        "Environment Failure",
        "Judge Evidence",
        "Completion Score",
        "Scope Guard",
    ]
    for term in domain_terms:
        # At least one reference in the doc (case-insensitive)
        assert term.lower() in doc_text.lower(), (
            f"Documentation must use domain term: {term}"
        )

    # Must reference orchestrator-agnostic or standalone nature
    assert re.search(
        r"orchestrator.agnostic|standalone|independent",
        doc_text,
        re.IGNORECASE,
    ), "Documentation must mention orchestrator-agnostic or standalone nature"

    # Must not suggest Rangkai owns Saringan's configuration
    assert not re.search(
        r"Rangkai.*(owns|controls|defines).*Saringan.*(config|check)",
        doc_text,
        re.IGNORECASE,
    ), "Documentation must not suggest Rangkai owns Saringan configuration"


# ---------------------------------------------------------------------------
# Judge Harness Protocol Contract schema tests
# ---------------------------------------------------------------------------


def test_doc_documents_required_harness_result_fields(doc_text: str) -> None:
    """Documentation lists the required fields in a Judge Harness result
    artifact (summary, scope_guard)."""
    assert "summary" in doc_text.lower(), (
        "Documentation must mention the summary field"
    )
    assert "scope_guard" in doc_text.lower() or "scope guard" in doc_text.lower(), (
        "Documentation must mention scope_guard"
    )


def test_doc_documents_valid_verdict_values(doc_text: str) -> None:
    """Documentation explains valid verdict values (yes, no, idk)."""
    assert re.search(r"\"(yes|no|idk)\"|`(yes|no|idk)`|yes.*no.*idk", doc_text), (
        "Documentation must explain valid verdict values: yes, no, idk"
    )


def test_doc_documents_optional_harness_result_fields(doc_text: str) -> None:
    """Documentation lists optional fields (advisories, acceptance_criteria)."""
    assert "advisories" in doc_text.lower() or "advisory" in doc_text.lower(), (
        "Documentation must mention advisories"
    )
    # acceptance_criteria with underscore or space
    assert "acceptance_criteria" in doc_text.lower() or re.search(
        r"acceptance\s+criteria", doc_text, re.IGNORECASE
    ), "Documentation must mention acceptance criteria"


# ---------------------------------------------------------------------------
# Legacy adapter documentation
# ---------------------------------------------------------------------------


def test_doc_mentions_legacy_adapter(doc_text: str) -> None:
    """Documentation covers the legacy direct-model adapter as a migration
    option."""
    assert re.search(
        r"legacy|migration|backward.compat", doc_text, re.IGNORECASE
    ), "Documentation should mention legacy adapter or migration path"
