# Saringan

Saringan is the quality gate context in the Bersama ecosystem. It defines the language for validating agent-produced code before that code is eligible to merge.

## Language

**Saringan**:
The quality gate in the Bersama ecosystem that evaluates agent-produced code before it is eligible to merge. It is a standalone tool that can be invoked by Rangkai or run independently.
_Avoid_: Hakim, judge layer

**Deterministic Gate**:
The first Saringan validation layer: a local, non-LLM gate made of repeatable static checks, tests, builds, and security scans.
_Avoid_: Static checks script, Layer 1 script

**Contextual Judge Gate**:
The future Saringan validation layer that evaluates code changes against issue context and project conventions using LLM-based judgement.
_Avoid_: Layer 2 in v1, deterministic check

**Rangkai Integration**:
The optional use of Saringan by the Rangkai orchestrator as a post-implementation validation step. Rangkai invokes Saringan and reacts to its result, but does not define Saringan's checks.
_Avoid_: Embedded gate, internal validator

**Human Escalation**:
The handoff state used when Saringan failures cannot be resolved automatically. In the current issue workflow, Human Escalation uses the `ready-for-human` label rather than a Saringan-specific failure label.
_Avoid_: checks-failed label, custom failure label

**Saringan Invocation**:
The command-line contract for running Saringan as a standalone tool. It produces stable exit codes, machine-readable results on stdout, and human-readable terminal output on stderr.
_Avoid_: Log scraping, ad hoc script call

**Saringan CLI**:
The primary command-line tool used to invoke Saringan against a target project.
_Avoid_: Static checks script, shell-only runner

**Target Repository State**:
The checked-out project directory that Saringan validates in the Deterministic Gate, including its files, dependencies, and test/build behavior at a specific point in time.
_Avoid_: Git diff, patch

**Validation Result**:
The structured outcome returned by a Saringan invocation, including whether blocking checks passed, failed, or could not run.
_Avoid_: Raw logs, terminal transcript

**Validation Result Contract**:
The stable shape of a Validation Result, including the overall status, check outcomes, target path, config path, and invocation timing.
_Avoid_: Orchestrator metadata, issue metadata

**Validation Result Status**:
The normalized invocation-level status of a Validation Result: `passed`, `failed`, or `error`.
_Avoid_: Overall skipped status, free-form result state

**Check Outcome**:
The result of one declared Saringan check within a Validation Result.
_Avoid_: Command output, log section

**Check Outcome Status**:
The normalized status of a Check Outcome: `passed`, `failed`, `skipped`, or `error`.
_Avoid_: Free-form status text, overloaded failure states

**Check Evidence**:
The bounded execution details attached to a Check Outcome, such as stdout, stderr, exit code, duration, command, working directory, and any referenced log path.
_Avoid_: Unbounded terminal transcript, caller-only evidence

**Aggregated Validation**:
The rule that Saringan should collect outcomes from all runnable declared checks before returning a Validation Result.
_Avoid_: Fail-fast validation, single-error validation

**Blocking Check**:
A validation check whose failure means the agent-produced code is not eligible to merge.
_Avoid_: Critical warning, hard advisory

**Advisory Check**:
A validation check whose failure is reported but does not prevent merge eligibility.
_Avoid_: Soft failure, optional failure

**Environment Failure**:
A Saringan invocation outcome where validation could not run because required tooling, configuration, or repository state was unavailable.
_Avoid_: Validation failure, code failure

**Target Toolchain**:
The set of linters, test runners, build tools, and security tools owned by the target repository and invoked by Saringan during validation.
_Avoid_: Saringan-managed dependencies, bundled toolchain

**Check Catalog**:
The standard vocabulary of validation checks that Saringan understands across target projects.
_Avoid_: Hard-coded script steps, project commands

**Stable Check ID**:
The canonical identifier for a Typed Check within the Check Catalog. Stable Check IDs are snake_case names such as `secrets_scan` and are the identifiers used for dependencies, configuration, and results.
_Avoid_: Hyphenated check id, display label, ad hoc step name

**Deprecated Check Alias**:
A non-canonical check name accepted only as temporary configuration input. Deprecated Check Aliases are normalized to Stable Check IDs before dependency resolution and result reporting.
_Avoid_: Alternate stable id, result id alias, permanent synonym

**Typed Check**:
A declared Saringan check whose behavior is identified by a Stable Check ID from the Check Catalog and configured through type-specific fields. Built-in Typed Checks are the primary Saringan model; generic command checks are escape hatches for target-specific validation.
_Avoid_: Generic named command, untyped step

**Argument Vector Command**:
A command declared as an ordered array of executable and arguments rather than a shell-interpreted string.
_Avoid_: Shell string, inline shell pipeline

**Declared Check Command**:
The Argument Vector Command supplied by the target repository for a Typed Check. Saringan standardizes the check's meaning, while the target repository declares the exact tool invocation.
_Avoid_: Inferred command, default command, toolchain guess

**Target Configuration**:
The target project's declaration of which Saringan checks run, how they run, and whether each check is blocking or advisory. It lives in the repository being validated.
_Avoid_: Global defaults, hard-coded paths

**Saringan Configuration**:
The canonical `saringan.toml` file in a target repository that declares Saringan's Target Configuration.
_Avoid_: Rangkai config, embedded orchestrator config

**Declarative Configuration**:
The definition of Saringan behavior through structured configuration that names checks, parameters, thresholds, and policies without making arbitrary scripting the primary model.
_Avoid_: Imperative-only setup, free-form shell as configuration

**Explicit Configuration**:
The rule that Saringan runs only from declared `saringan.toml` configuration and does not infer validation behavior from repository heuristics.
_Avoid_: Auto-discovery, guess-based validation

**Declared Check Set**:
The set of Typed Checks explicitly declared in `saringan.toml` for a Target Repository State. Validation Results must be derived from executing or skipping this declared set, not from fixture-only status fields.
_Avoid_: Fixture-only result, synthetic pass/fail, undeclared check
