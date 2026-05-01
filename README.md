# auto-fix-agent

Version: **1.1.0**

An autonomous Java repair agent that turns production failures into reviewed patches, PRs, and reusable repair knowledge.

This project is intentionally more ambitious than a simple "LLM edits code" demo. It implements a guarded, end-to-end repair loop: ingest a Java exception, locate the business frame, inspect source code with structured tools, apply a minimal patch, compile and test, create a Gitee pull request, request human review through Feishu, and finally reflect on the outcome to generate reusable skills for future incidents.

## Evaluation Highlights

This repository is best evaluated as a closed-loop agentic software maintenance system, not as a shallow prompt wrapper. The implementation has several concrete signals that are relevant to technical review:

- End-to-end incident flow: log ingestion, traceback parsing, business-frame localization, LLM-guided repair, compile/test validation, Gitee PR creation, Feishu human review, and post-review reflection.
- Agentic architecture with boundaries: ingestion, code navigation, repair orchestration, bounded tools, permission checks, storage, review callbacks, reflection, and skill generation are separated into clear modules.
- Java-aware context gathering: traceback frames are normalized, top business frames are identified, and AST/symbol tools expose bounded source context instead of dumping entire files into the model.
- Tool-constrained LLM behavior: repairs are driven through native function-style tools, explicit schemas, project-scoped reads, unified diff edits, and runtime validation rather than unconstrained prose.
- Safety gates for code modification: production edits must be unified diffs, target existing Java files under source roots, match diff headers, stay within patch-size limits, and pass compile/test before PR delivery.
- Automated regression-test generation in v1.1.0: after a valid source repair, the system asks the LLM for a focused Java test patch and applies it through a separate `apply_test_patch` tool restricted to `src/test/java/**/*Test.java`.
- Human-in-the-loop delivery: the agent creates a reviewable PR and sends a Feishu review request, but does not automatically merge its own changes.
- Learning loop: accepted or rejected reviews are summarized, failed agent fixes can be compared with human fix branches, and reusable `SKILL.md` repair knowledge is written for future incidents.
- Evidence of engineering discipline: tests cover parsing, deduplication, log watching, code editing, tool permissions, Gitee/Feishu integrations, review callbacks, reflection, rollback, and test generation behavior.

The core contribution is that repair, verification, review, and learning are modeled as one auditable workflow. That makes the project stronger than a one-off code-generation demo and more aligned with how autonomous coding systems need to operate in real engineering teams.

## Version 1.1.0 Highlights

- Added guarded production-code edit rules: project-root enforcement, Java source-root allowlists, diff-header matching, single-file patches, patch-size limits, and default denial for new production files.
- Added `apply_test_patch`, a separate test-only patch tool that allows new Java test files while rejecting production edits, disabled tests, weak assertions, and unsafe targets.
- Added automatic LLM regression-test generation after a successful source repair and before final compile/test validation.
- Added one retry for malformed generated test patches, with the tool rejection reason fed back to the LLM.
- Updated default Maven test command to quiet modern JDK Mockito/ByteBuddy warnings with `-XX:+EnableDynamicAgentLoading -Xshare:off`.

## What It Does

`auto-fix-agent` is designed for Java service incidents, especially exceptions found in logs.

Typical flow:

1. Watch application logs for Java exceptions.
2. Parse the traceback and identify useful business frames.
3. Build a repair task and session context.
4. Ask an OpenAI-compatible model to use native repair tools.
5. Read symbols or source code in bounded, project-scoped ways.
6. Apply a unified diff patch through the edit tool.
7. Generate a focused regression test patch through the test-only patch tool.
8. Run compile and test commands.
9. Create and push a Gitee repair branch.
10. Open a pull request.
11. Send a Feishu review card.
12. On human approval, summarize what worked.
13. On human rejection, compare the agent branch with the human fix branch and generate a reusable skill.

## Architecture

```text
agent/
  code_nav/      Java AST and symbol lookup
  core/          repair orchestration, task management, permissions
  doctor/        environment checks
  ingestion/     log watching, traceback parsing, review callback server
  llm/           OpenAI-compatible client and call recording
  reflection/    review outcome analysis and skill generation
  storage/       session, task, and skill stores
  tools/         bounded tools for code search/read/edit, test patching, git, tests, Feishu
tests/           contract tests for the critical workflows
```

The central repair loop lives in `agent/core/repair_agent.py`. The review-and-learning loop lives in `agent/reflection/reflection_subagent.py`.

## Human Review And Reflection

When review is required, the agent does not merge its own code. It sends a Feishu card with local review URLs:

```text
http://127.0.0.1:8765/review?event_type=review_passed&bug_id=<bug_id>
http://127.0.0.1:8765/review?event_type=review_failed&bug_id=<bug_id>
```

For a failed review, a human supplies the final repair branch:

```text
http://127.0.0.1:8765/review?event_type=review_failed&bug_id=<bug_id>&human_fix_branch=<branch>
```

The reflection agent then compares:

```text
base_branch...agent_branch
base_branch...human_fix_branch
```

It stores the agent diff, human diff, file overlap, missing files, line counts, review event, LLM summary, and generated skill path in the session. The generated skill is written under:

```text
<workspace>/skills/<skill-name>/SKILL.md
<workspace>/skills/<skill-name>/skill.meta.json
```

## Commands

Install dependencies:

```bash
pip install -r requirements.txt
```

Run diagnostics:

```bash
python -m agent.main doctor
```

Watch logs and start the local review callback server:

```bash
python -m agent.main watch
```

Run a one-off repair:

```bash
python -m agent.main repair --bug-id demo-bug --raw-log-path ./logs/app.log --project mall-service
```

Run legacy reflection entry:

```bash
python -m agent.main reflect --bug-id demo-bug --result pass
```

Run tests:

```bash
python -m pytest
```

## Configuration

Runtime settings are loaded from `config.example.yaml` by `agent/main.py`.

Important groups:

- `project`: Java project root, language, default branch, compile command, test command.
- `llm`: OpenAI-compatible endpoint, API key, model, timeout.
- `gitee`: PR creation settings.
- `feishu`: webhook and local review callback settings.
- `session`: session retention location.
- `agent`: retry count, review requirement, reflection toggle, watched logs.

Before sharing this repository, replace any local credentials in configuration files with placeholders.

## Design Principles

- Prefer structured tools over free-form shell access.
- Prefer smallest relevant code reads over dumping whole files into the model.
- Prefer unified diffs over full-file rewrites.
- Separate production-code edits from test-only patches.
- Generate focused regression tests for successful repairs when LLM support is available.
- Compile and test after successful edits.
- Keep humans in the approval path.
- Learn from both success and failure.
- Make repair history inspectable through session artifacts.

## Current Maturity

This is a strong working prototype with a coherent architecture and meaningful tests. It is especially valuable as a reference design for agentic software maintenance.

Known areas for continued hardening:

- Session storage is currently lightweight and should be made durable for long-running deployments.
- Remote human fix branches must be available locally before reflection can diff them.
- Diff analysis is structural; deeper semantic comparison is delegated to the LLM.
- Production deployment should externalize secrets, logging, persistence, and network callback handling.

These are sensible next steps, not architectural weaknesses. The core contribution is already clear: the project models repair as a verified, reviewed, and self-improving engineering loop.
