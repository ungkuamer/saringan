# Judge Harness Protocol

The Judge Harness is the execution boundary for the **Contextual Judge Gate**. Saringan delegates LLM-based evaluation to a standalone harness process that receives judge context, produces a structured result artifact, and returns diagnostics. Saringan validates the artifact against the Judge Harness Protocol Contract before accepting it as **Judge Evidence** in the **Validation Result**.

This design keeps Saringan orchestrator-agnostic—the `saringan judge` **Saringan Invocation** and the **Validation Result Contract** remain stable regardless of which harness or model provider executes the evaluation.

## Protocol Contract

### Overview

The Judge Harness Protocol defines three concerns:

| Concern | Owner | Description |
|---------|-------|-------------|
| **Judge Opinions** | Harness | Summary, Scope Guard verdict, Judge Advisories, Acceptance Criteria Evaluation |
| **Domain Evidence** | Saringan | Changed files, target paths, input paths, invocation timing, Completion Score |
| **Validation Result Assembly** | Saringan | Saringan owns Validation Result assembly: aggregating harness output + domain evidence into the final **Validation Result** |

A harness must produce a dedicated machine-readable result artifact. Saringan validates that artifact against the protocol contract before accepting it. **Invalid, missing, or nonconforming output is rejected as an Environment Failure.**

### Prompt and Context Inputs

The harness receives a JSON payload on stdin containing:

| Field | Type | Description |
|-------|------|-------------|
| `target_path` | `string` | Absolute path to the target repository directory |
| `diff_text` | `string` | Full `git diff` output to evaluate |
| `issue_text` | `string` | Issue specification text |
| `conventions_text` | `string \| null` | Optional project conventions document |
| `model` | `string` | Configured model identifier |
| `provider` | `string \| null` | Configured provider identifier |

The harness writes its result to the path specified by the `SARINGAN_RESULT_PATH` environment variable.

### Result Artifact Schema

The result artifact must be a JSON object with the following fields:

```json
{
  "summary": "string (required, non-empty)",
  "scope_guard": {
    "verdict": "yes | no | idk (required)",
    "rationale": "string (required, non-empty)"
  },
  "advisories": [
    {
      "kind": "string (required, non-empty)",
      "file": "string | null",
      "line": "integer | null",
      "snippet": "string | null"
    }
  ],
  "acceptance_criteria": [
    {
      "criterion": "string (required, non-empty)",
      "verdict": "yes | no | idk (required)",
      "rationale": "string (required, non-empty)"
    }
  ]
}
```

- `summary`, `scope_guard` — **required**
- `advisories`, `acceptance_criteria` — optional (default to empty arrays)
- All three verdict fields accept exactly `"yes"`, `"no"`, or `"idk"`
- No extra fields beyond those listed above are permitted
- Saringan validates the artifact with `validate_harness_result()` and raises `HarnessValidationError` for any violation

### Diagnostics: stdout, stderr, and Transcript References

The harness's stdout and stderr are treated as **diagnostics**, not as the authoritative judge result:

- stdout and stderr are bounded to 2000 characters before inclusion in Check Evidence
- The result artifact path is recorded in Check Evidence when available
- Raw harness logs remain separate from the accepted artifact
- Diagnostic evidence is preserved for both successful and failing runs

### Exit Behavior and Error Handling

| Condition | Outcome |
|-----------|---------|
| Harness exits 0 **and** writes valid artifact | `status: "passed"` — Judge Evidence accepted |
| Harness exits nonzero | `status: "error"` — Environment Failure |
| Harness times out | `status: "error"` — Environment Failure with timeout message |
| Harness does not write result artifact | `status: "error"` — missing artifact |
| Harness writes invalid JSON | `status: "error"` — JSON parse error |
| Harness writes valid JSON that fails schema validation | `status: "error"` — Schema mismatch via `HarnessValidationError` |
| Harness exits 0 with valid artifact but no criteria met | `status: "passed"` — Completion Score 0.0 |

**The Contextual Judge Gate is advisory-first.** Exit code `0` means the judge completed successfully, not that the code is approved. The `status: "passed"` / `status: "error"` judgment is about harness execution correctness, not code quality.

### Schema Enforcement

Saringan enforces the result artifact schema at the harness boundary via `validate_harness_result()`. The validator checks:

1. The artifact is a JSON object (dict)
2. No unknown top-level fields are present
3. `summary` is present and non-empty
4. `scope_guard` is present with valid `verdict` and non-empty `rationale`
5. `advisories` (if present) is a list of advisory objects with required `kind`
6. `acceptance_criteria` (if present) is a list of criterion objects with `criterion`, `verdict`, `rationale`
7. All verdicts are `"yes"`, `"no"`, or `"idk"`
8. No extra fields in scope_guard, advisory, or criterion sub-objects

Any violation raises `HarnessValidationError` with a descriptive message. Saringan surfaces this as an Environment Failure in the Validation Result.

### Minimal Valid Example

The smallest valid harness result contains only the two required top-level fields:

```json
{
  "summary": "Code changes evaluated.",
  "scope_guard": {
    "verdict": "yes",
    "rationale": "All changed files map to the issue scope."
  }
}
```

---

## Configuration

Judge Harness configuration uses a **Rangkai-like** shape: a map of named harnesses, an optional default harness, and per-harness provider, model, command template, and timeout.

### Configuration File

The configuration file is a TOML file (default path via `--judge-config` or `SARINGAN_JUDGE_CONFIG`). It is **not** part of `saringan.toml` — it is a separate, Saringan-owned file that lives outside the target repository.

### Example: Multi-Harness Configuration

```toml
default_harness = "pi"

[[harnesses]]
name = "pi"
provider = "anthropic"
model = "claude-sonnet-4"
command = ["python3", "-m", "pi_harness", "--provider", "{provider}", "--model", "{model}"]
timeout = 300

[[harnesses]]
name = "codex-headless"
provider = "openai"
model = "gpt-5"
command = ["node", "codex-runner.js", "--model", "{model}"]
timeout = 600

[[harnesses]]
name = "legacy-litellm"
provider = "litellm"
model = "gpt-5"
command = ["python3", "-m", "saringan.legacy_adapter_runner", "--provider", "{provider}"]
timeout = 120
```

### Configuration Fields

**Top-level**

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `default_harness` | No | `string` | Harness name used when `--harness` is not specified and `SARINGAN_JUDGE_HARNESS` is not set. Must match a declared harness name. |
| `[[harnesses]]` | **Yes** | array of tables | At least one harness must be declared. |

**Per-harness** (all fields required)

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Unique identifier for this harness |
| `provider` | `string` | Provider identifier (e.g., `"anthropic"`, `"openai"`, `"litellm"`). Recorded in Check Evidence. |
| `model` | `string` | Model identifier (e.g., `"claude-sonnet-4"`, `"gpt-5"`). Recorded in Check Evidence. |
| `command` | `[string]` | Argument vector command to execute the harness. Supports `{provider}` and `{model}` template substitution. |
| `timeout` | `integer` | Maximum seconds before the harness is terminated as timed out. |

### Argument Template Substitution

The `{provider}` and `{model}` placeholders in `command` are replaced with the resolved provider and model values before execution. This lets the same harness command template work with different providers and models:

```toml
command = ["python3", "-m", "my_harness", "--provider", "{provider}", "--model", "{model}"]
```

With `provider = "anthropic"` and `model = "claude-sonnet-4"`, this becomes:

```
["python3", "-m", "my_harness", "--provider", "anthropic", "--model", "claude-sonnet-4"]
```

### Harness Selection Precedence

Harness selection follows this order:

1. Named harness from `--harness` CLI flag
2. Named harness from `SARINGAN_JUDGE_HARNESS` environment variable
3. `default_harness` from the judge configuration file
4. If none of the above: legacy built-in LLM client (backward compatible, requires `litellm`)

---

## CLI and Environment Overrides

### CLI Flags (`saringan judge`)

| Flag | Environment Variable | Description |
|------|---------------------|-------------|
| `--harness <name>` | `SARINGAN_JUDGE_HARNESS` | Named harness to use (resolved from judge config). If no judge config, treated as raw harness command (backward compat). |
| `--judge-config <path>` | `SARINGAN_JUDGE_CONFIG` | Path to the judge harness configuration TOML file. |
| `--provider <name>` | `SARINGAN_JUDGE_PROVIDER` | Override the harness-defined provider. |
| `--model <name>` | `SARINGAN_JUDGE_MODEL` | Override the harness-defined model. |
| `--timeout <seconds>` | `SARINGAN_JUDGE_TIMEOUT` | Override the harness-defined timeout. |

### Environment Variables

All overrides are also available as environment variables, enabling non-interactive configuration by CI, wrappers, and **Rangkai Integration**:

```bash
export SARINGAN_JUDGE_CONFIG=/etc/saringan/judge.toml
export SARINGAN_JUDGE_HARNESS=pi
export SARINGAN_JUDGE_PROVIDER=anthropic
export SARINGAN_JUDGE_MODEL=claude-sonnet-4
export SARINGAN_JUDGE_TIMEOUT=300
saringan judge /path/to/repo --diff /tmp/changes.diff --issue /tmp/issue.md
```

### Combined Override Example

CLI flags take precedence over environment variables. This lets a wrapper set defaults via env vars while allowing local overrides:

```bash
# CI sets defaults
export SARINGAN_JUDGE_CONFIG=/etc/saringan/judge.toml
export SARINGAN_JUDGE_HARNESS=pi

# Local override: use a different harness for this run
saringan judge . --harness codex-headless --diff changes.diff --issue issue.md
```

### Configuration Errors

The following produce clear Environment Failure results:

- Config file does not exist → `status: "error"`
- Invalid TOML → `status: "error"`
- No harnesses declared → `status: "error"`
- `default_harness` references unknown harness → `status: "error"`
- Duplicate harness names → `status: "error"`
- Harness not found → `status: "error"`
- Missing required harness fields → `status: "error"`
- Unknown top-level or harness fields → `status: "error"`

---

## Rangkai Integration

Saringan remains **orchestrator-agnostic**. Rangkai may invoke Saringan as an optional post-implementation validation step via **Rangkai Integration**, but Rangkai does not own Saringan's checks, configuration, or Validation Result assembly.

The Judge Harness configuration borrows Rangkai's proven pattern of named harnesses, default harness, provider/model separation, and argument templates. This keeps orchestration concepts consistent across the Bersama ecosystem while preserving Saringan's independent CLI boundary.

### What Rangkai Does Not Own

- Saringan's `saringan.toml` configuration
- The **Check Catalog** and **Stable Check IDs**
- The **Validation Result Contract** and **Check Outcome** shape
- **Completion Score** computation (deterministic, Saringan-owned)
- **Changed files** extraction (computed by Saringan from the diff)
- The Judge Harness Protocol Contract

### What Rangkai Can Configure

- Which harness Saringan uses (via `--harness` or `SARINGAN_JUDGE_HARNESS`)
- Provider and model overrides (via `--provider`, `--model`, or env vars)
- Timeout (via `--timeout` or `SARINGAN_JUDGE_TIMEOUT`)
- Judge configuration path (via `--judge-config` or `SARINGAN_JUDGE_CONFIG`)

### LiteLLM Independence

Saringan no longer assumes LiteLLM as the only model execution path. The Contextual Judge Gate can run through any harness that implements the Judge Harness Protocol Contract, including:

- **Pi headless agent** — via `pi_harness`
- **Codex headless** — via `codex-runner.js`
- **Legacy LiteLLM adapter** — via `saringan.legacy_adapter_runner` (migration-safe fallback)
- **Custom harnesses** — any executable that reads the input contract from stdin and writes the result artifact to `SARINGAN_RESULT_PATH`

The legacy adapter wraps the existing LiteLLM-powered judge and scope guard clients behind the same Judge Harness Protocol Contract, making it available as a selectable harness mode while keeping it separate from external harness execution. When `--harness` specifies the legacy adapter (or when no harness and no config are provided), the legacy path runs and produces output validated through the same `validate_harness_result()` boundary.

---

## Ownership Boundaries

Saringan owns Validation Result assembly and all deterministic evidence. The harness supplies only judge opinions. This separation ensures domain-derived evidence (changed files, target paths, invocation timing, Completion Score) stays under Saringan control, never influenced by harness output.

```
┌─────────────────────────────────────────────────────────┐
│                    Saringan Owns                         │
│                                                         │
│  • Validation Result assembly                            │
│  • Completion Score (deterministic computation)          │
│  • Changed files (extracted from diff)                   │
│  • Target path, input paths, invocation timing           │
│  • Schema enforcement (validate_harness_result)          │
│  • Check Outcome status and blocking policy              │
│  • Diagnostic evidence bounding                          │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                  Harness Supplies                         │
│                                                         │
│  • summary (judge narrative)                             │
│  • scope_guard (verdict + rationale)                     │
│  • advisories (review findings)                          │
│  • acceptance_criteria (criterion-by-criterion eval)     │
│                                                         │
├─────────────────────────────────────────────────────────┤
│              Harness Diagnostics Only                     │
│                                                         │
│  • stdout (bounded, human-readable)                      │
│  • stderr (bounded, human-readable)                      │
│  • Exit code                                             │
│  • Result artifact path                                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## API Reference

### `saringan.judge_harness`

The contract module that defines what a Judge Harness must produce.

```python
from saringan.judge_harness import (
    JudgeHarnessResult,
    ScopeGuardVerdict,
    JudgeAdvisory,
    AcceptanceCriterion,
    HarnessValidationError,
    validate_harness_result,
)
```

- `validate_harness_result(raw: Any) -> JudgeHarnessResult` — validates raw harness output against the contract
- `HarnessValidationError` — raised when a candidate harness result fails validation

### `saringan.judge_config`

Configuration loading and harness resolution.

```python
from saringan.judge_config import (
    JudgeConfig,
    JudgeHarnessConfig,
    ConfigError,
    HarnessNotFoundError,
    load_judge_config,
    resolve_harness,
)
```

- `load_judge_config(path: Path) -> JudgeConfig | ConfigError` — loads and validates a judge harness configuration
- `resolve_harness(config, harness_name, *, provider_override, model_override, timeout_override) -> tuple` — resolves harness selection to (provider, model, command, timeout)

### `saringan.legacy_adapter`

The legacy LiteLLM adapter that wraps the existing judge clients behind the Judge Harness Protocol Contract.

```python
from saringan.legacy_adapter import run_legacy_adapter
```

- `run_legacy_adapter(request, judge_input, *, judge_client, scope_guard_client, changed_files) -> dict` — executes the legacy path and returns a harness-contract-compliant result dict

---

## See Also

- [ADR 0001: Saringan is a standalone CLI with its own configuration](adr/0001-saringan-standalone-cli.md)
- [Saringan Domain Vocabulary](../../CONTEXT.md)
- [PRD #42: Swappable Judge Harness for Contextual Judge Gate](https://github.com/ungkuamer/saringan/issues/42)
