<p align="center">
  <img src="docs/logo.svg" alt="auto-fix-agent logo" width="760">
</p>

<h1 align="center">auto-fix-agent</h1>

<p align="center">
  <strong>面向 Java 生产故障的闭环自动修复 Agent</strong><br>
  <strong>A closed-loop autonomous repair agent for Java production incidents</strong>
</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.1.0-0f766e">
  <img alt="language" src="https://img.shields.io/badge/language-Java%20%2B%20Python-2563eb">
  <img alt="workflow" src="https://img.shields.io/badge/workflow-repair%20%7C%20test%20%7C%20review%20%7C%20learn-9333ea">
  <img alt="safety" src="https://img.shields.io/badge/safety-guarded%20tools-f97316">
</p>

---

## 中文简介

`auto-fix-agent` 是一个面向 Java 服务故障的自动修复 Agent。它可以从生产日志中的异常出发，解析 traceback，定位业务栈帧，调用受约束的代码工具读取上下文，生成最小修复补丁，自动补充回归测试，执行编译和测试，创建 Gitee PR，通过飞书发起人工 Review，并在 Review 结果后沉淀可复用的修复经验。

它不是简单的“让 LLM 改代码”演示，而是把 **故障输入、AI 定位、补丁生成、测试验证、PR 交付、人工审核、经验反思** 放进同一个可审计闭环里。

## English Summary

`auto-fix-agent` is an autonomous repair agent for Java service incidents. Starting from production exceptions, it parses tracebacks, locates business frames, inspects bounded source context through guarded tools, generates minimal patches, creates regression tests, runs compile/test gates, opens Gitee pull requests, requests Feishu human review, and reflects on review outcomes to produce reusable repair knowledge.

It is not a shallow "LLM edits code" demo. It models repair as an auditable workflow that combines **incident ingestion, AI-assisted diagnosis, patch generation, validation, PR delivery, human review, and durable learning**.

---

## 评审亮点 / Evaluation Highlights

| 维度 | 中文 | English |
| --- | --- | --- |
| 闭环完整性 | 日志监听、异常解析、业务栈帧定位、LLM 修复、编译测试、Gitee PR、飞书 Review、Review 后反思全部串联。 | Complete incident loop: log watching, traceback parsing, business-frame localization, LLM repair, compile/test, Gitee PR, Feishu review, and reflection. |
| 架构边界 | `ingestion`、`code_nav`、`core`、`tools`、`reflection`、`storage` 等模块职责清晰。 | Clear module boundaries across ingestion, code navigation, repair orchestration, tools, reflection, and storage. |
| Java 语义定位 | 解析 traceback，识别 top business frame，并通过 AST/symbol 工具读取函数级上下文。 | Java-aware navigation with traceback parsing, top business frame selection, AST symbols, and bounded source reads. |
| 工具约束 LLM | LLM 必须通过 schema 明确的 native function/tool 调用执行读写和验证。 | The LLM acts through typed native tools instead of unconstrained prose or arbitrary shell execution. |
| 写入安全 | 生产代码只接受 unified diff，并强制项目根目录、源码目录、diff header、patch 大小等校验。 | Production edits require unified diffs, project-root checks, source-root allowlists, diff-header matching, and patch-size limits. |
| 自动测试 | v1.1.0 新增测试生成链路，通过 `apply_test_patch` 写入 `src/test/java/**/*Test.java`。 | v1.1.0 adds LLM-generated regression tests through a test-only `apply_test_patch` tool. |
| 人审交付 | Agent 创建可审查 PR，并通过飞书请求人工 Review，不自动合并。 | The agent creates reviewable PRs and requests human review instead of self-merging. |
| 经验沉淀 | Review 失败时可比较 agent 分支和 human fix 分支，生成 `SKILL.md`。 | Failed reviews can compare agent and human-fix branches and produce reusable `SKILL.md` knowledge. |

核心价值：它把自动化、验证、人审和学习组合成一个工程系统，让 AI 从“一次性代码生成器”变成“受约束的软件维护参与者”。

Core value: it turns repair, validation, review, and learning into one engineering system, making AI a constrained software-maintenance participant rather than a one-off code generator.

---

## 1.1.0 更新 / What's New in 1.1.0

- **生产代码写入安全增强 / Safer production edits**  
  项目根目录校验、Java 源码目录白名单、diff header 匹配、单文件 patch、patch 大小限制、默认禁止新建生产文件。  
  Project-root enforcement, Java source-root allowlists, diff-header matching, single-file patches, patch-size limits, and default denial for new production files.

- **测试专用补丁工具 / Test-only patch tool**  
  新增 `apply_test_patch`，仅允许 `src/test/java/**/*Test.java`，允许新建测试文件，但拒绝生产文件、禁用测试、弱断言和危险路径。  
  Added `apply_test_patch`, restricted to `src/test/java/**/*Test.java`; it allows new tests while rejecting production edits, disabled tests, weak assertions, and unsafe targets.

- **自动回归测试生成 / Automatic regression-test generation**  
  业务修复成功后，在最终 compile/test 前让 LLM 生成 focused regression test。  
  After a valid source repair, the system asks the LLM to generate a focused regression test before the final compile/test gate.

- **测试 patch 重试 / Test patch retry**  
  当 LLM 返回完整文件、非 unified diff 或超出限制时，系统会把工具拒绝原因反馈给 LLM，最多自动重试一次。  
  If the LLM returns a full file, non-diff content, or an oversized patch, the tool rejection is fed back once for automatic correction.

- **更干净的 Maven 测试输出 / Cleaner Maven test output**  
  默认测试命令加入 `-XX:+EnableDynamicAgentLoading -Xshare:off`，降低新 JDK 下 Mockito/ByteBuddy warning 干扰。  
  The default Maven test command includes `-XX:+EnableDynamicAgentLoading -Xshare:off` to reduce modern JDK Mockito/ByteBuddy warnings.

---

## 工作流 / Workflow

```text
log / event
  -> sanitize
  -> parse traceback
  -> deduplicate
  -> locate business frame
  -> read bounded source context
  -> LLM tool-driven repair
  -> edit_code production patch
  -> LLM regression-test generation
  -> apply_test_patch test patch
  -> compile + test
  -> Gitee PR
  -> Feishu human review
  -> reflection + reusable skill
```

典型步骤 / Typical steps:

1. 监听 Java 服务日志或接收外部故障输入。  
   Watch Java service logs or receive external incident input.
2. 对原始日志脱敏并解析 traceback。  
   Sanitize raw logs and parse tracebacks.
3. 识别异常类型、业务栈帧和 fingerprint。  
   Extract exception type, business frames, and fingerprint.
4. 通过 AST/symbol 工具读取函数级上下文。  
   Read function-level context through AST/symbol tools.
5. LLM 通过受控工具生成生产代码补丁。  
   The LLM generates a production patch through guarded tools.
6. 系统自动生成并应用回归测试补丁。  
   The system generates and applies a regression-test patch.
7. 执行 compile/test gate。  
   Run compile/test gates.
8. 创建 Gitee PR 并发送飞书 Review。  
   Create a Gitee PR and send Feishu review notification.
9. Review 后总结经验或分析失败差异。  
   Summarize success or analyze failed review diffs.

---

## 架构 / Architecture

```text
agent/
  code_nav/      Java AST and symbol lookup
  core/          repair orchestration, task management, permissions, test generation
  doctor/        environment checks
  ingestion/     log watching, traceback parsing, review callback server
  llm/           OpenAI-compatible client and call recording
  reflection/    review outcome analysis, diff comparison, skill generation
  storage/       session, task, and skill stores
  tools/         code read/edit, test patching, compile/test, Git, Gitee, Feishu
tests/           contract tests for critical workflows
```

核心入口 / Key modules:

```text
agent/core/repair_agent.py              # 修复主循环 / main repair loop
agent/core/test_generation_agent.py     # 自动测试生成 / regression test generation
agent/tools/edit_code.py                # 生产代码补丁 / production patch tool
agent/tools/apply_test_patch.py         # 测试补丁工具 / test patch tool
agent/reflection/reflection_subagent.py # Review 后反思 / post-review reflection
```

---

## 安全设计 / Safety Model

### 生产代码修改 / Production Edits

生产代码只能通过 `edit_code` 写入，并受到以下限制：

Production code can only be modified through `edit_code`, guarded by:

- 必须是 unified diff，拒绝原始代码片段和整文件重写。  
  Unified diff only; raw snippets and full-file rewrites are rejected.
- 目标路径必须位于 `project.root` 下。  
  Target path must stay under `project.root`.
- 默认只允许已有 `.java` 文件。  
  Existing `.java` files only by default.
- 默认只允许 `src/main/java/**` 和 `src/test/java/**`。  
  Source-root allowlist: `src/main/java/**` and `src/test/java/**`.
- 禁止 `.git`、CI、配置、构建文件等敏感目标。  
  Sensitive targets such as `.git`, CI, config, and build files are denied.
- diff header 必须和 `payload.path` 指向同一文件。  
  Diff headers must match `payload.path`.
- 单次 patch 限制 hunk 数、新增行数和删除行数。  
  Hunk, added-line, and deleted-line limits are enforced.

### 测试代码修改 / Test Edits

测试补丁使用独立工具 `apply_test_patch`：

Test patches use the separate `apply_test_patch` tool:

- 仅允许 `src/test/java/**/*Test.java`。  
  Only `src/test/java/**/*Test.java` is allowed.
- 允许新建测试文件。  
  New test files are allowed.
- 拒绝生产代码、配置文件和构建文件。  
  Production files, config files, and build files are rejected.
- 拒绝 `@Disabled`、`@Ignore`、`assertTrue(true)` 等弱测试模式。  
  Weak tests such as `@Disabled`, `@Ignore`, and `assertTrue(true)` are rejected.
- 要求测试包含有意义的 assertion 或 verification。  
  Tests must include meaningful assertions or verifications.

### 人工审核 / Human Review

系统不会自动合并 PR。修复成功后会进入 Review 状态，并通过飞书通知人工审核。

The system does not self-merge PRs. Successful repairs enter review and are sent to humans through Feishu.

```text
http://127.0.0.1:8765/review?event_type=review_passed&bug_id=<bug_id>
http://127.0.0.1:8765/review?event_type=review_failed&bug_id=<bug_id>
```

Review 失败时可提供人工修复分支：

For failed reviews, a human fix branch can be supplied:

```text
http://127.0.0.1:8765/review?event_type=review_failed&bug_id=<bug_id>&human_fix_branch=<branch>
```

反思流程会比较：

The reflection flow compares:

```text
base_branch...agent_branch
base_branch...human_fix_branch
```

并写入 reusable skill：

And writes reusable skill artifacts:

```text
<workspace>/skills/<skill-name>/SKILL.md
<workspace>/skills/<skill-name>/skill.meta.json
```

---

## 命令 / Commands

安装依赖 / Install dependencies:

```bash
pip install -r requirements.txt
```

运行自检 / Run diagnostics:

```bash
python -m agent.main doctor
```

监听日志并启动本地 Review callback server / Watch logs and start local review callback server:

```bash
python -m agent.main watch
```

执行一次性修复 / Run a one-off repair:

```bash
python -m agent.main repair --bug-id demo-bug --raw-log-path ./logs/app.log --project mall-service
```

运行反思入口 / Run reflection:

```bash
python -m agent.main reflect --bug-id demo-bug --result pass
```

运行测试 / Run tests:

```bash
python -m pytest
```

---

## 配置 / Configuration

`agent/main.py` 默认从 `config.example.yaml` 加载配置。

`agent/main.py` loads runtime settings from `config.example.yaml` by default.

| 配置组 | 说明 | Description |
| --- | --- | --- |
| `project` | Java 项目根目录、语言、默认分支、编译命令、测试命令 | Java project root, language, default branch, compile command, test command |
| `llm` | OpenAI-compatible endpoint、API key、模型、超时 | OpenAI-compatible endpoint, API key, model, timeout |
| `gitee` | PR 创建相关配置 | Pull request creation settings |
| `feishu` | webhook 和本地 Review callback 配置 | Webhook and local review callback settings |
| `session` | 会话和产物存储目录 | Session and artifact storage |
| `agent` | 重试次数、Review、反思、日志监听路径 | Retry count, review policy, reflection, watched logs |

默认测试命令 / Default test command:

```bash
mvn "-DargLine=-XX:+EnableDynamicAgentLoading -Xshare:off" test
```

分享仓库前，请把本地真实密钥、token、webhook 替换为占位符，并轮换已经暴露过的凭据。

Before sharing the repository, replace real local credentials, tokens, and webhooks with placeholders and rotate anything that has been exposed.

---

## 设计原则 / Design Principles

- 优先使用结构化工具，而不是让 LLM 自由执行 shell。  
  Prefer structured tools over free-form shell execution.
- 优先读取最小必要源码上下文，而不是把整个仓库塞进 prompt。  
  Prefer minimal relevant source context over dumping the whole repository.
- 生产代码使用 unified diff，不接受整文件重写。  
  Use unified diffs for production edits; reject full-file rewrites.
- 生产代码修改和测试代码修改分离。  
  Separate production edits from test edits.
- 修复后自动生成 focused regression test。  
  Generate focused regression tests after successful repairs.
- 任何成功补丁都必须经过 compile/test。  
  Every successful patch must pass compile/test gates.
- 保留人工 Review，不让 Agent 自动合并自己的 PR。  
  Keep humans in the review path; the agent does not self-merge.
- 从成功和失败中沉淀可复用 skill。  
  Learn from both success and failure through reusable skills.
- 所有关键步骤写入 session，使修复历史可审计。  
  Store key steps in session artifacts for auditability.

---

## 当前成熟度 / Current Maturity

这是一个具备完整闭环的强原型，适合作为 agentic software maintenance 的参考实现。它已经展示了真实工程系统需要的关键能力：受控工具调用、路径与权限边界、编译测试 gate、人审交付、失败反思和知识沉淀。

This is a strong working prototype and a useful reference design for agentic software maintenance. It already demonstrates core capabilities required in real engineering systems: constrained tool use, path and permission boundaries, compile/test gates, human-reviewed delivery, failure reflection, and durable knowledge capture.

后续建议继续加强：

Recommended hardening work:

- 持久化存储和审计日志从轻量 session 升级为更可靠的数据库方案。  
  Upgrade lightweight session storage and audit logs to a more durable database-backed design.
- Review callback 增加一次性 token 和过期机制。  
  Add one-time tokens and expiry to review callbacks.
- Gitee/Feishu/GitHub/GitLab 等平台集成进一步抽象。  
  Further abstract platform integrations such as Gitee, Feishu, GitHub, and GitLab.
- 反思生成的 skill 增加启用前安全扫描和人工确认。  
  Add safety scanning and human approval before enabling generated skills.
- 对修复补丁增加更深入的 Java AST/语义级风险检查。  
  Add deeper Java AST and semantic risk checks for repair patches.

这些是生产化增强方向，不影响当前项目的核心贡献：它已经把 AI 修复、验证、交付和学习组织成一个可运行、可审查、可扩展的闭环系统。

These are production-hardening directions. They do not change the core contribution: this project already organizes AI repair, validation, delivery, and learning into a runnable, reviewable, and extensible closed-loop system.
