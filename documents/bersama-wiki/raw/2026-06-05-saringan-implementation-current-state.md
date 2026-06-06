# Saringan — Current Implementation State (June 2026)

**Status**: Active, implemented, tested. This is the *latest* implementation and overrides all past designs, sketches, and earlier brain-dumps. Treat this as the source of truth for what Saringan is *today*.

---

## What is Saringan?

Saringan is the quality gate CLI in the Bersama SDLC ecosystem. It stands alone — orchestrator-agnostic. Rangkai may call it, CI may call it, humans may call it. It validates agent-produced code against two layers:

| Layer | Subcommand | Type | Blocks Merge? |
|-------|-----------|------|---------------|
| Deterministic Gate | `saringan validate` | Local static checks, tests, builds, security scans | Yes (blocking) |
| Contextual Judge Gate | `saringan judge` | LLM-as-a-Judge against issue context + diff | No (advisory) |

---

## Source Tree Layout

```
saringan/
├── src/saringan/
│   ├── __init__.py          # Minimal, exports nothing
│   ├── __main__.py          # raise SystemExit(main())
│   └── cli.py               # Everything — ~700 lines
├── tests/
│   └── test_cli.py          # ~2000+ lines, ~50+ tests
├── pyproject.toml           # setuptools build, [judge] extra
├── saringan.toml            # Self-check configuration
├── README.md                # Full public docs
├── CONTEXT.md               # Domain language glossary
├── docs/
│   ├── adr/0001-saringan-standalone-cli.md
│   ├── agents/domain.md
│   ├── agents/issue-tracker.md
│   ├── agents/triage-labels.md
│   └── integrations/rangkai.md
└── AGENTS.md                # Agent instructions
```

The whole CLI is a single-file module (`cli.py`). No framework, no DI, no async. Argparse for CLI, `subprocess.run` for check execution, `json` for serialization.

---

## `saringan validate` — Deterministic Gate

### Configuration (`saringan.toml`)

TOML-based, schema version 1. Declares an array of `[[checks]]`.

```
schema_version = 1
log_dir = ".saringan/logs"        # optional

[[checks]]
id = "secrets-scan"
type = "secrets_scan"
command = ["gitleaks", "detect", "--source=.", "--verbose", "--no-git"]

[[checks]]
id = "python-lint"
type = "python_lint"
command = ["ruff", "check", "src"]
depends_on = ["secrets-scan"]     # optional
advisory = true                   # optional, defaults to blocking
```

**Schema validation (in order):**

1. Only `schema_version`, `checks`, `log_dir` are allowed at top level.
2. `schema_version` must be an integer in `{1}`.
3. Every check must have a non-empty `id` (unique across the config).
4. `type` must be a known check type (or a deprecated kebab-case alias).
5. `command` must be an argument vector (array of strings), not a shell string.
6. Only `id`, `type`, `command`, `advisory`, `depends_on` are allowed per check.
7. Unknown fields → error.
8. `depends_on` must reference check ids declared elsewhere in the same config.

### Check Catalog — Stable Check IDs

| Stable ID | Deprecated Kebab Alias | Purpose |
|-----------|------------------------|---------|
| `command` | — | Generic command execution |
| `javascript_lint` | `javascript-lint` | JS/TS linter |
| `javascript_tests` | `javascript-tests` | JS test runner |
| `javascript_build` | `javascript-build` | JS build |
| `python_lint` | `python-lint` | Python linter |
| `python_typecheck` | `python-typecheck` | Python type checker |
| `python_tests` | `python-tests` | Python test runner |
| `secrets_scan` | `secrets-scan` | Secret/credential scanner |
| `environment_file_guard` | `environment-file-guard` | Env file commit guard |

All deprecated aliases are normalized to their stable ID on read.

### Execution Model

1. Parse config → validate schema → build `{id: check}` map.
2. Resolve `depends_on` — unknown dependency ids are fatal errors.
3. Iterate checks in declaration order.
4. For each check:
   - If any dependency has status != `"passed"` → mark as `"skipped"` with reason `"unsatisfied dependency"`.
   - Otherwise, execute via `subprocess.run(command, capture_output=True, cwd=target_path)`.
   - VIRTUAL_ENV is stripped from environment to avoid Python venv mixing.
5. On `OSError` (command not found) → status `"error"`, exit code 2.
6. Check output is bounded to 2000 chars each for stdout and stderr in evidence.
7. Full logs can be persisted to disk if `--log-dir` or `log_dir` config is set.

### Blocking vs Advisory

- Default: every check is **blocking** (failure → overall status `"failed"`, exit code 1).
- `advisory = true` → failure is reported but does not cause overall failure.
- `aggregate_validation_status`: if any blocking check failed → `"failed"`. If any error → `"error"`. Else `"passed"`.

### Edge Cases Handled

| Case | Behaviour |
|------|-----------|
| Missing target path | Error, exit 2 |
| Missing config file | Error, exit 2 |
| Invalid TOML | Error, exit 2 |
| No checks declared | Error, exit 2 |
| Empty checks list | Error, exit 2 |
| Duplicate check ids | Error, exit 2 |
| Unknown check type | Error, exit 2 |
| Non-array command | Error, exit 2 |
| Unknown fields | Error, exit 2 |
| Missing schema_version | Error, exit 2 |
| Unsupported schema_version | Error, exit 2 |
| Command binary not found | Error, exit 2 |
| Dependency on unknown id | Error, exit 2 |
| Advisory failures + blocking pass | Passed, exit 0 |
| Advisory failures + blocking fail | Failed, exit 1 |

### Output Contract

- **stdout**: always machine-readable JSON (Validation Result). Never anything else.
- **stderr**: human-readable progress lines (e.g. `"Validation passed: /path"`). Never parsed by callers.
- `--json` flag is a deprecated no-op (always JSON).

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All blocking checks passed |
| 1 | One or more blocking checks failed |
| 2 | Environment failure (config, missing files, unavailable tooling, etc.) |

---

## `saringan judge` — Contextual Judge Gate

### Purpose

Advisory LLM-based evaluation of a diff against issue context. Does NOT block merge eligibility.

### CLI Arguments

```
saringan judge <target_path> \
  --diff <path> \
  --issue <path> \
  --model <model> \
  [--conventions <path>]
```

All inputs must be existing files (fatal error otherwise).

### Architecture

```
                ┌──────────────────┐
                │  JudgeRequest    │
                │  target_path     │
                │  diff_path       │
                │  issue_path      │
                │  conventions_path│
                │  model           │
                └──────┬───────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ScopeGuard    JudgeClient    (output)
   (verdict       (LLM call
    yes/no/idk)    → structured
                   JSON)
```

**Flow:**

1. Validate all input paths exist.
2. Read diff, issue, conventions text from disk.
3. Extract changed file list from diff (`diff --git` lines → `b/` paths).
4. **Scope Guard**: LiteLLM call with `response_format: json_schema` → verdict `yes`/`no`/`idk` + rationale. Validated with Pydantic.
5. **Contextual Judge**: LiteLLM call with `response_format: json_schema` → structured response containing `summary`, `advisories[]`, `acceptance_criteria[]`.
6. Pydantic-validate the judge response.
7. Compute completion score.
8. Detect debug artifacts (`print(`, `console.log(`) from diff additions.
9. Return Validation Result with status `"passed"` (exit 0) on success, `"error"` (exit 2) on any failure.

### LLM Schema (Response Format)

Both Scope Guard and Judge use OpenAI-compatible `json_schema` structured output mode via LiteLLM.

**Scope Guard response:**
```json
{"verdict": "yes", "rationale": "..."}
```

**Judge response:**
```json
{
  "summary": "...",
  "advisories": [{"kind": "debug_artifact", "file": "...", "line": 1, "snippet": "..."}],
  "acceptance_criteria": [{"criterion": "...", "verdict": "yes|no|idk", "rationale": "..."}]
}
```

### Completion Score

Deterministic, not LLM-judged:
```
yes_count / (yes_count + no_count)
```
- `idk` verdicts are excluded from numerator and denominator.
- Empty or all-`idk` → 0.0.

### Debug Artifact Detection

Scans diff additions for `print(` and `console.log(` patterns (configurable via `DEBUG_ARTIFACT_PATTERNS` tuple). Line numbers are tracked per-file starting from the `diff --git` header.

### Judge Dependencies

Optional `[judge]` extra: `litellm` + `pydantic`. Without them, `saringan judge` returns exit 2 with a clear error message. The `SARINGAN_FORCE_MISSING_JUDGE_DEPS` env var exists for testing the missing-deps path.

### Error Cases (Judge Gate)

| Case | Exit Code |
|------|-----------|
| Missing input file | 2 |
| Judge deps not installed | 2 |
| LLM returns invalid JSON | 2 |
| LLM returns valid JSON but invalid schema | 2 |
| OSError/network failure during LLM call | 2 |

---

## Validation Result Schema (shared across validate & judge)

```json
{
  "status": "passed|failed|error",
  "check_outcomes": [
    {
      "id": "user-defined-check-id",
      "stable_check_id": "snake_case_type",
      "status": "passed|failed|skipped|error",
      "blocking": true|false,
      "evidence": { ... },     // check-specific
      "reason": "unsatisfied dependency",  // only for skipped
      "message": "..."          // only for contextual_judge
    }
  ],
  "target_path": "...",
  "config_path": "...|null",
  "started_at": "ISO 8601 UTC",
  "finished_at": "ISO 8601 UTC",
  "message": "error description|null"
}
```

---

## Key Design Decisions (from ADR-0001)

- **Standalone CLI with own config**: Saringan reads `saringan.toml`, not Rangkai config.
- **Explicit Configuration**: No heuristic auto-discovery. Every check is declared.
- **Declarative Configuration**: Checks name their type, parameters, and dependencies. No arbitrary scripting.
- **Argument Vector Commands**: Commands are arrays, not shell strings.
- **Aggregated Validation**: All runnable checks execute before a result is returned (no fail-fast).
- **Stable Check IDs**: `snake_case` identifiers used in config, dependencies, and results. Deprecated kebab aliases accepted as input.
- **Output Separation**: stdout = machine JSON, stderr = human text.

---

## What Does NOT Exist Yet

These were discussed in past designs but are NOT implemented and should be considered superseded:

1. **No parallel check execution** — checks run sequentially in declaration order.
2. **No `--help` output for subcommands** — only argparse defaults (which exist).
3. **No CI/CD pipeline integration** — the README documents the CLI contract; no GitHub Actions, no GitLab CI, no Jenkins pipelines.
4. **No test coverage for subprocess timeout** — a hanging check hangs the CLI indefinitely.
5. **No `judge` CLI integration test** — `test_judge_target_*` tests use injected fake clients, not the CLI entrypoint (`run_cli("judge", ...)`).
6. **No output schema version or output schema evolution plan**.
7. **No caching / incremental validation**.
8. **No `saringan.toml` schema generation or JSON Schema output**.
9. **No worktree integration** — that's Rangkai's domain.
10. **No binary distribution** — installed via pip from source only.

---

## Test Suite

- Framework: pytest 9.x
- Count: 50+ tests (some parametrized, so effective count is higher)
- Key patterns:
  - `run_cli()` helper wraps `subprocess.run([sys.executable, "-m", "saringan", ...])`
  - `assert_check_outcome()` helper for structured evidence assertion
  - `tmp_path` fixtures for isolated filesystem setup
  - Fake clients (FakeJudgeClient, FakeScopeGuardClient) for judge tests
  - `monkeypatch.setenv("SARINGAN_FORCE_MISSING_JUDGE_DEPS", "1")` for dep-missing tests
  - Timestamp/duration stripping comparator (`_payload_without_timestamps`)
- All tests pass on Python 3.14.

---

## Self-Check

The project's own `saringan.toml` runs `pytest -q` as the only check. This means the project validates itself via `saringan validate .` — a dogfooding pattern.

---

## Git History (landmarks)

- `28f095c` — PRD/20: advisory contextual judge gate (latest)
- `c3d0f63` — chore: add env path
- `ca8f048` — feat: always emit Validation Result JSON on stdout, progress on stderr
- `80bf437` — Fixes #12: remove fixture-only Validation Result config
- `58e02ad` — feat: canonicalize built-in Check Catalog Stable Check IDs

Earlier commits built up the typed checks, dependency resolution, and logging features.

---

*This document supersedes all earlier design notes, whiteboard sketches, and PRD drafts about Saringan's implementation. If something in older documents contradicts this, the code and this document win.*
