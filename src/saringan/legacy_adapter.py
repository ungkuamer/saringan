"""
Legacy Direct Model Adapter (Issue #46).

Wraps the existing LiteLLM-powered Contextual Judge Gate behind the
Judge Harness Protocol Contract. This gives maintainers a migration-safe
fallback while keeping the legacy direct-model path as a selectable
harness mode separate from external harness execution.

The adapter internally calls the existing LiteLLM clients for both
scope guard and contextual judge, then maps the responses into the
Judge Harness result contract so that the output passes
:func:`validate_harness_result`.
"""

from __future__ import annotations

from typing import Protocol


# ---------------------------------------------------------------------------
# Protocol types (compatible with cli.JudgeClient / ScopeGuardClient)
# ---------------------------------------------------------------------------


class JudgeClientProtocol(Protocol):
    def evaluate(self, request: object, judge_input: object) -> object: ...


class ScopeGuardClientProtocol(Protocol):
    def evaluate_scope(
        self,
        request: object,
        judge_input: object,
        changed_files: list[str],
    ) -> object: ...


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def run_legacy_adapter(
    request: object,
    judge_input: object,
    *,
    judge_client: JudgeClientProtocol | None = None,
    scope_guard_client: ScopeGuardClientProtocol | None = None,
    changed_files: list[str] | None = None,
) -> dict[str, object]:
    """Execute the legacy direct-model path through the Judge Harness contract.

    Internally calls the existing LiteLLM clients for scope guard and
    contextual judge evaluation, then maps the responses into the
    ``JudgeHarnessResult`` contract shape so that the output can be
    validated by :func:`saringan.judge_harness.validate_harness_result`.

    Parameters:
        request: A :class:`~saringan.cli.JudgeRequest`.
        judge_input: A :class:`~saringan.cli.JudgeInput`.
        judge_client: Optional fake judge client for testing.  When *None*,
            the default :func:`~saringan.cli.build_judge_client` is used.
        scope_guard_client: Optional fake scope guard client for testing.
        changed_files: Optional pre-computed list of changed files.  When
            *None*, the adapter computes changed files from the diff text
            in ``judge_input``.

    Returns:
        A dict with keys ``summary``, ``scope_guard``, ``advisories``, and
        ``acceptance_criteria`` conforming to the Judge Harness Protocol
        Contract.

    Raises:
        JudgeDependencyError: When required judge dependencies are not installed.
        JudgeStructuredOutputError: When model output fails schema validation.
    """
    from saringan.cli import (
        JudgeInput,
        JudgeRequest,
        build_judge_client,
        build_scope_guard_client,
        extract_changed_files,
        validate_judge_response,
        validate_scope_guard_response,
    )

    # Resolve clients
    jc: object = (
        judge_client if judge_client is not None else build_judge_client()
    )
    sgc: object = (
        scope_guard_client
        if scope_guard_client is not None
        else build_scope_guard_client()
    )

    # Compute changed files if not provided
    resolved_changed_files: list[str]
    if changed_files is not None:
        resolved_changed_files = list(changed_files)
    else:
        resolved_changed_files = extract_changed_files(
            getattr(judge_input, "diff_text", "")
        )

    # --- Scope Guard ---
    raw_scope = getattr(sgc, "evaluate_scope")(
        request, judge_input, resolved_changed_files
    )
    validated_scope = validate_scope_guard_response(raw_scope)
    scope_guard_dict = getattr(validated_scope, "model_dump")()

    # --- Contextual Judge ---
    raw_judge = getattr(jc, "evaluate")(request, judge_input)
    validated_judge = validate_judge_response(raw_judge)
    judge_dict = getattr(validated_judge, "model_dump")()

    # --- Map to Judge Harness contract ---
    advisories = judge_dict.get("advisories", [])
    acceptance_criteria = judge_dict.get("acceptance_criteria", [])

    # Normalise advisory fields for harness contract compatibility
    normalised_advisories: list[dict[str, object]] = []
    for adv in advisories:
        normalised_advisories.append(
            {
                "kind": adv.get("kind", ""),
                "file": adv.get("file"),
                "line": adv.get("line"),
                "snippet": adv.get("snippet"),
            }
        )

    return {
        "summary": judge_dict.get("summary", ""),
        "scope_guard": {
            "verdict": scope_guard_dict.get("verdict", "idk"),
            "rationale": scope_guard_dict.get("rationale", ""),
        },
        "advisories": normalised_advisories,
        "acceptance_criteria": acceptance_criteria,
    }
