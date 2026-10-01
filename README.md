<div align="center">

<img src="assets/codex-reviewer-cover.png" alt="Codex Reviewer — A second opinion for your code. Read-only, evidence-first, scope-bound code review." width="100%" />

# Codex Reviewer

### A second opinion for your code.

An independent, read-only Codex CLI review of your changes.<br />
**Clear scope. Actionable findings. Results you can hand off.**

[![Release](https://img.shields.io/github/v/release/BIGWOO/codex-reviewer?style=flat-square&color=79C9AD&label=release)](https://github.com/BIGWOO/codex-reviewer/releases)
[![Tests](https://img.shields.io/github/actions/workflow/status/BIGWOO/codex-reviewer/test.yml?branch=main&style=flat-square&label=tests)](https://github.com/BIGWOO/codex-reviewer/actions/workflows/test.yml)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-343B43?style=flat-square)
![Codex CLI 0.159.3+](https://img.shields.io/badge/Codex_CLI-0.159.3%2B-343B43?style=flat-square)

**English** · [繁體中文](README.zh-TW.md)<br />
[Why install?](#why-install) · [Install](#install) · [Quick start](#quick-start) · [Presets](#presets) · [Documentation](#documentation)

</div>

---

## Give your changes a second look

After your main agent finishes an implementation, let a separate Codex CLI process review it. The reviewer returns findings and evidence; your main agent verifies them and acts within your authorization.

Review uncommitted changes, a branch diff, a single commit, or specific files and code ranges before you ship.

| What you need | What the skill provides |
|---|---|
| **Keep the working tree intact** | Read-only review; no commits, pushes, merges, or deployments |
| **Get actionable feedback** | Concrete triggers, impact, file/line evidence, and a minimal fix |
| **Stay within scope** | Explicit review targets; bounded mode accepts only a prepared packet and prohibits tools |
| **Keep a usable record** | Structured findings, a JSON result envelope, and quality-gate status |

> A second opinion still needs verification. No findings does not mean tests have passed.

<a id="why-install"></a>

## Why install if Codex already reviews code?

**For an occasional diff review, the official `/review` is a good starting point.** Install this skill when you want repeatable rules for scope, models, output, and failure handling across reviews.

| Workflow | Without this skill | With Codex Reviewer |
|---|---|---|
| One-off review | Use official `/review` or `codex review` directly | Invoke `$codex-reviewer`; your agent chooses the appropriate mode |
| Consistent review policy | Maintain your own prompts, configuration, or scripts | Shared criteria, presets, and preflight checks are packaged together |
| Only selected code and test evidence | Prepare and constrain the input yourself | Bounded packets validate scope and evidence, with a zero-tool policy |
| Results for automation | Build output parsing and failure handling | Result envelopes, gate states, and `--enforce-gate` exit codes |
| Execution lifecycle | Manage overlapping runs, timeouts, and cancellation | Shared repository locks, process cleanup, and terminal-event validation |

Installing adds a local Python helper and a CLI compatibility requirement to maintain. Reviews still use your configured Codex service and its usage allowance. **Read-only refers to workspace permissions; it does not mean code stays off the model service.** Installation does not guarantee more bugs found, lower usage, or a replacement for tests.

## How is it different from official Codex?

**This is a community-maintained wrapper around the official Codex CLI, not an OpenAI product.** Official review already provides a separate review pass and prioritized findings. This skill packages additional workflow constraints and result checks around those capabilities.

| Area | Official Codex | What this skill packages |
|---|---|---|
| Code review | CLI `/review` handles uncommitted changes, commits, base branches, and custom instructions | A consistent entry point and mode selection; native mode keeps the official review rubric |
| Read-only controls | Official review preserves the working tree; the CLI provides sandbox and configuration controls | Explicit read-only and ephemeral settings, disabled hooks, and no delegation in ordinary reviews |
| Structured output | `codex exec --output-schema` supports a specified response format | A bundled findings schema, result envelope, local validation, and quality-gate evaluation |
| Precise input | Git review targets and prompt-defined scope | Validated code/range/diff packets with evidence and no child tools in bounded mode |
| GitHub PR reviews | Official integration supports `@codex review` and automatic reviews | Local and main-agent workflows; installing this skill does not enable a PR bot |

**Choose official review for a quick check. Choose this skill for a review workflow you want to repeat and hand off.** You can build similar workflows with the official CLI yourself; this repository saves you from maintaining that wrapper.

Sources: [Official CLI documentation](https://learn.chatgpt.com/docs/codex/cli), [official structured-review example](https://github.com/openai/openai-cookbook/blob/main/examples/codex/build_code_review_with_codex_sdk.md), and [official GitHub integration](https://learn.chatgpt.com/docs/third-party/github). Implementation details here target CLI `0.159.3`; see the [compatibility reference](references/codex_cli_reference.md) (Traditional Chinese).

<a id="install"></a>

## 01 / Install

You need **Python 3.10+, Git, and Codex CLI authentication**. The minimum stable CLI version is `0.159.3`; the helper installs or upgrades a missing or outdated CLI by default. Your account must support the default model, `gpt-6.1-sol`.

### Install the skill

Use `~/.agents/skills` as the recommended single source:

```bash
git clone https://github.com/BIGWOO/codex-reviewer.git \
  "$HOME/.agents/skills/codex-reviewer"
```

Already installed? Check for local changes first, then update with `git pull --ff-only` from the skill directory. Preserve and resolve local changes or branch divergence before updating.

### Check your environment

```bash
SKILL_DIR="$HOME/.agents/skills/codex-reviewer"
python3 "$SKILL_DIR/scripts/codex_review.py" doctor
```

`doctor` checks the CLI, configuration, stored credentials, and model catalog without running a model review. Valid stored credentials do not prove remote model access.

Use `$codex-reviewer` in your next Codex message. If your host only discovers skills under `~/.codex/skills`, use the compatibility setup below.

<details>
<summary><strong>Host only reads ~/.codex/skills?</strong></summary>

On macOS/Linux, link the same installation into the host's skills directory. Only create the link if the destination does not exist; inspect an existing installation or link first.

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$HOME/.agents/skills/codex-reviewer" \
  "$HOME/.codex/skills/codex-reviewer"
```

For Windows paths and process management, see the [user guide](references/usage-guide.md#windows-執行相容性) (Traditional Chinese).

</details>

<a id="quick-start"></a>

## 02 / Run your first review

### In Codex, say what you want reviewed

```text
$codex-reviewer Review my uncommitted changes with the standard preset.
Report only actionable, evidence-backed issues introduced by this change.
Do not modify files.
```

Or target a branch or a focused change:

```text
$codex-reviewer Compare this branch against main.
Focus on compatibility and edge cases.
```

```text
$codex-reviewer Review only the payment-flow changes with the deep preset.
```

The skill selects a suitable mode such as bounded or structured review. Shared criteria prioritize side effects, compatibility, edge cases, performance, and security.

### In your terminal, save the result

```bash
SKILL_DIR="$HOME/.agents/skills/codex-reviewer"
python3 "$SKILL_DIR/scripts/codex_review.py" structured-review \
  --cd /path/to/your/repo \
  --uncommitted \
  --preset standard \
  --result-json /tmp/codex-review-result.json
```

Replace `/path/to/your/repo` with your project path. For a branch review, replace `--uncommitted` with `--base main`; for a single commit, use `--commit <SHA>`.

> Preview the command first: add `--dry-run --no-update-check`. No model review is run.

<a id="presets"></a>

## 03 / Pick the review depth

**Start with `standard`.** Use `deep` for complex or high-value changes and `quick` for initial triage.

| Preset | Model | Reasoning | Best for |
|---|---|---|---|
| `quick` | GPT-6.1 Sol | `medium` | Initial triage, not a formal quality gate |
| **`standard`** | **GPT-6.1 Sol** | **`high`** | **Everyday review; the default** |
| `deep` | GPT-6.1 Sol | `xhigh` | Complex or high-value changes |
| `ultra` | GPT-6.1 Sol | `ultra` | Explicit opt-in to generic review with delegation |

All presets use `gpt-6.1-sol` and validate capabilities against the model catalog. If the model or required reasoning level is unavailable, the helper reports the reason instead of silently switching models. Other valid models require an explicit `--model`; retired models are rejected.

All reviews disable hooks. Ordinary reviews prohibit delegation; only generic `ultra` permits it, with an agent concurrency limit of 2. Native and bounded modes do not support ultra.

<details>
<summary><strong>Advanced catalog, max, and context limits</strong></summary>

The helper first tries to refresh the catalog. A bundled catalog may be used with a warning if refresh fails, but it does not guarantee account access. `--full-context` restores plugins/apps only; it does not change hooks or delegation restrictions.

`max` is not a preset. Explicit `--reasoning-effort max` requires a fully sized, single-repository scope of at most 15 changed files and 1,200 changed lines. `--allow-large-diff` cannot bypass that limit.

</details>

<a id="modes"></a>

## 04 / Choose the right mode

| Goal | Mode |
|---|---|
| Stable JSON findings | `structured-review` |
| Selected code and evidence only, with no reviewer tools | `bounded-review` |
| Official Codex review rubric | `native-review` |
| Custom criteria or security, performance, and architecture checks | `custom`, `security`, `performance`, `architecture`, and other generic modes |
| Environment and installation checks | `doctor` |

Native mode uses the official rubric; it cannot apply the same custom prompt, output schema, images, or live search. See [mode and operation details](references/operation-details.md) (Traditional Chinese).

<a id="bounded-review-contract"></a>

### Bounded Review Contract

Your main agent runs the necessary tests first, then packages the exact code scope and actual check results. The reviewer receives only that packet and does not rerun tests. Missing evidence is reported as a gap.

<details>
<summary><strong>Minimal scope, evidence, and command example</strong></summary>

`/tmp/review-scope.json`:

```json
{
  "version": 1,
  "kind": "commit_snapshot",
  "commit": "HEAD",
  "files": [
    {"path": "src/example.ts", "ranges": [{"start": 1, "end": 40}]}
  ]
}
```

The file and range must exist at that commit. `commit_diff` and `uncommitted_diff` are also supported.

`/tmp/review-evidence.json` is optional. Record only checks you actually performed:

```json
{
  "version": 1,
  "checks": [
    {"name": "targeted tests", "status": "not_run"}
  ]
}
```

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" bounded-review \
  --cd /path/to/your/repo \
  --bounded-scope /tmp/review-scope.json \
  --evidence-json /tmp/review-evidence.json \
  --preset standard \
  --enforce-gate \
  --result-json /tmp/codex-bounded-result.json
```

Scope and evidence use strict validation; bounded mode enforces zero tools. See the [full bounded contract](references/usage-guide.md#bounded-review-contract) (Traditional Chinese) for limits and supported fields.

</details>

## Read the result, then verify it

A finding should explain its trigger, impact, and minimal code evidence. Your main agent verifies it and either fixes it within your authorization or records why it was not adopted.

| Gate | Meaning | Exit code with `--enforce-gate` |
|---|---|---|
| `passed` | No blocking findings | `0` |
| `passed_with_warnings` | P3 findings only | `0` |
| `blocked` | One or more P0–P2 findings | `2` |
| Failed / inconclusive | Timeout, cancellation, policy violation, or no valid terminal result | `1`; cancellation uses dedicated exit codes |

Keep the original reviewer result. Declining a finding does not turn a `blocked` gate into a pass. Reviews do not replace tests.

<a id="documentation"></a>

## Documentation

The landing page and release notes are available in English. The detailed operational references currently use Traditional Chinese.

| Document | What you will find |
|---|---|
| [User guide](references/usage-guide.md) | CLI updates, binary selection, options, cross-repo scopes, custom schemas, and platform limits |
| [Mode and operation details](references/operation-details.md) | Mode selection and execution controls |
| [CLI compatibility reference](references/codex_cli_reference.md) | Official sources, capability checks, profiles, and full diagnostics |
| [Prompt examples](references/example_prompts.md) | Custom review criteria |
| [Skill instructions](SKILL.md) | Agent workflow, invocation boundaries, and gate handling |
| [v261001 release notes — English](references/releases/v261001.en.md) | Changes and the exact validation scope |
| [Published v261001 release](https://github.com/BIGWOO/codex-reviewer/releases/tag/v261001) | Current GitHub release |

<details>
<summary><strong>Frequently asked questions</strong></summary>

**How is this different from a Codex subagent?**<br />
Subagents support delegation inside the main task. This skill runs a separate read-only CLI process with explicit scope, model, and output contracts for a second opinion before handoff. Use both when they cover different risks; avoid reviewing the same scope twice.

**Will it change my code?**<br />
The reviewer does not modify the reviewed repository. Any fixes by the main agent require your authorization.

**Does it need extra Python packages?**<br />
Ordinary reviews and the bundled schema do not. A custom `--schema` needs the optional `jsonschema` dependency. See the [installation instructions](references/usage-guide.md#自訂格式與驗證依賴).

**Will it update my CLI?**<br />
A missing CLI or one older than `0.159.3` is installed/upgraded by default. `--no-update-check`, `--dry-run`, and an explicit binary pin prevent updates, but still require a compatible version. Periodic checks after meeting the minimum need separate opt-in.

**Has this release run every mode against a real model?**<br />
No. Validation for v261001 includes local tests, CLI contracts, command previews, and doctor checks; no real-model review was run for that release. See the [validation history](references/usage-guide.md#相容性與保護措施).

</details>

---

<div align="center">

**Before your next handoff, get a second opinion backed by evidence.**

[Install Codex Reviewer](#install) · [Report an issue](https://github.com/BIGWOO/codex-reviewer/issues) · [繁體中文](README.zh-TW.md)

</div>
