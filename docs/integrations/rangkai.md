# Rangkai Integration

Saringan can be invoked by Rangkai as an external Quality Gate before Rangkai creates an Integration Pull Request.

Saringan should remain decoupled from Rangkai internals: Rangkai calls the `saringan` CLI or a wrapper script, and Saringan emits machine-readable Validation Result JSON on stdout.

## Saringan commands used by Rangkai

Deterministic Gate:

```bash
saringan validate <target_path> --json
```

Contextual Judge Gate:

```bash
saringan judge <target_path> \
  --diff <diff_path> \
  --issue <issue_path> \
  --conventions <conventions_path> \
  --model <model> \
  --json
```

The Judge Gate requires the optional judge dependencies:

```bash
uv pip install -e ".[judge]"
```

## Recommended Rangkai shape

Rangkai should invoke Saringan through an external wrapper script rather than importing Saringan Python modules.

The wrapper should:

1. receive Rangkai context such as worktree path, issue number, PRD branch, and implementation branch
2. generate a diff file for `saringan judge`
3. fetch or render the issue text into an issue file
4. concatenate repo conventions/context into a conventions file
5. run `saringan validate <worktree> --json`
6. only run `saringan judge` if deterministic validation passes
7. print the final Validation Result JSON to stdout

The Rangkai-side wrapper and detailed setup instructions live in the Rangkai repository:

```text
/home/ungku/programming/rangkai/scripts/saringan-quality-gate.sh
/home/ungku/programming/rangkai/docs/quality-gate/saringan-judge-gate.md
```

## Model/API configuration

`Saringan judge` calls models through LiteLLM, so API keys and endpoint configuration must be present in the environment inherited by Rangkai and the wrapper.

Example OpenAI-compatible configuration:

```bash
export SARINGAN_JUDGE_MODEL=gpt-4o-mini
export OPENAI_API_KEY=...
```

Example custom OpenAI-compatible endpoint:

```bash
export SARINGAN_JUDGE_MODEL="openai/your-model-name"
export OPENAI_API_BASE="https://your-endpoint.example.com/v1"
export OPENAI_API_KEY="your-key-or-dummy-if-not-required"
```

Some environments may require `OPENAI_BASE_URL` instead of, or in addition to, `OPENAI_API_BASE`.

## Blocking policy

Saringan's Contextual Judge Gate is currently advisory-first. A completed judge run returns a successful Validation Result and includes judge evidence such as:

- scope guard verdict
- advisories
- acceptance criteria verdicts
- completion score

If Rangkai wants judge findings to block Integration Pull Request creation, that policy should live in Rangkai or its wrapper and be documented as orchestration policy. Saringan itself should continue to expose structured evidence without owning Rangkai lifecycle decisions.
