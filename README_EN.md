# Skill Router

![Version](https://img.shields.io/badge/version-v0.5-blue) [**中文文档**](README.md)

Route the current task to the minimal necessary set of skills, then read the full `SKILL.md` of the matched skill.

Use this skill when there are too many skills installed in your user-level directories, you don't know which skill to use, skills are duplicated across sources, you need to refresh the skill index, check conflicts or missed matches, track skill usage, or decide whether a skill belongs at project level, global level, or in the router repository.

## Features

- **Multi-source scanning**: Indexes only the Codex user directory (`~/.codex/skills`) by default; can be explicitly extended to `~/.agents/skills`, `~/.agent/skills`, and `~/.claude/skills`. Deduplicates across sources while preserving discoverability for each agent.
- **Explainable routing**: `query` returns a structured decision of `matched` / `ambiguous` / `no_match`, with `confidence`, `score_margin`, and `reasons` — raw scores are never treated as probabilities.
- **Separated public catalog and internal registry**: `catalog.json` contains only three public fields (name, description, recommended scenarios) for scoring; `registry.json` holds internal identity, paths, and hashes, and is never used as a route book.
- **Local minimal usage statistics**: First-run `init` requires informed user consent. When enabled, local files store only skill/project identifiers HMAC'd with a locally generated random key plus per-day event counts — no prompts, task text, skill names, usernames, absolute paths, or individual timestamps, and no network transmission.
- **Read-only placement suggestions**: A default 30-day "check on next use" cycle produces suggestions such as `project` / `global` / `router-store` / `keep`. These are suggestions only — no files are moved, no scheduled tasks or daemons are created, and popularity never affects routing scores.

## Installation

Install this skill in one command:

```bash
npx skills add Sxuan-Coder/skill-router
```

## Quick Start

```powershell
# Check user-level source availability
python scripts/skill_router.py sources --json

# First-time initialization (asks whether to enable local usage statistics)
python scripts/skill_router.py init

# Refresh the index
python scripts/skill_router.py scan

# Route the current task
python scripts/skill_router.py query "export this document as a PDF" --limit 5 --json

# Audit duplicates and conflicts
python scripts/skill_router.py audit
```

For the full routing workflow, usage feedback commands (`feedback selected/opened/corrected`), placement planning, and override configuration, see [SKILL.md](SKILL.md). For step-by-step details, see the **[Usage Manual (USAGE.md)](USAGE.md)** (in Chinese).

## For Beginners

If you're new to skills and don't know how to install or get started, the easiest way is to send the following prompt directly to your AI agent (Codex / Claude Code, or any agent that supports the skills mechanism) and let it walk you through everything:

```text
I want to install and start using the skill-router skill. Please help me with these steps:

1. Run "npx skills add Sxuan-Coder/skill-router" in the terminal to install the skill
   into my user-level skills directory;
2. After installation, go to the skill's directory and run
   "python scripts/skill_router.py sources --json" to see which skill sources exist on my machine;
3. Run "python scripts/skill_router.py init" for first-time initialization. Note: it will ask
   whether to enable local usage statistics. First explain to me that this data is stored only
   locally as anonymous aggregates and never sent over the network, then let ME answer yes or no —
   do not decide for me;
4. After initialization, run "python scripts/skill_router.py scan" to scan my installed skills;
5. Finally, teach me how to use it: whenever I describe a task, run
   "python scripts/skill_router.py query "<task description>" --limit 5 --json",
   tell me which skill will be used and why based on the result, then follow that skill's SKILL.md.

Execute the steps one by one, and after each step tell me in one sentence what happened.
```

Once installed, you only need to remember one habit: **whenever you don't know which skill to use for a task, have the agent run a query first** — it will return a recommendation with reasons.

## Tests

```powershell
python -m pytest tests -q
```

## Privacy & Security Boundaries

- Third-party `SKILL.md` files are treated as untrusted input; only text metadata is extracted, and no scripts are ever executed.
- Scanned skills are never modified, moved, copied, disabled, or deleted.
- Code blocks, credentials, environment variable values, and absolute local paths are never copied into generated route books.
- Global `AGENTS.md`, `~/.codex/config.toml`, or skill enablement states are never modified without explicit user consent.
- `references/generated/`, `.skill-router/`, local usage keys and statistics, and `.spec/` planning documents are never committed to Git (see `.gitignore`); the bundled `check_git_privacy.py` blocks forbidden paths and credential patterns at the pre-commit stage.

## Repository Layout

```
scripts/            Routing, scanning, usage, placement, and privacy-check scripts
config/             Generic routing overrides (personal override files are Git-ignored)
evals/public/       Synthetic evaluation cases (local eval outputs are Git-ignored)
references/         Fixtures and generated route books (generated/ is Git-ignored)
tests/              pytest tests
```
