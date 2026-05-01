<a id="top"></a>

<p align="center">
  <img src="docs/logo.svg" alt="czz-aid logo" width="760">
</p>

<h1 align="center">czz-aid</h1>

<p align="center">
  <strong>面向 Java 生产故障的闭环自动修复 Agent</strong><br>
  <strong>A closed-loop autonomous repair agent for Java production incidents</strong>
</p>

<p align="center">
  <a href="#zh">中文</a>
  |
  <a href="#english">English</a>
</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.1.0-0f766e">
  <img alt="language" src="https://img.shields.io/badge/language-Java%20%2B%20Python-2563eb">
  <img alt="workflow" src="https://img.shields.io/badge/workflow-repair%20%7C%20test%20%7C%20review%20%7C%20learn-9333ea">
  <img alt="safety" src="https://img.shields.io/badge/safety-guarded%20tools-f97316">
</p>

---

<a id="zh"></a>

## 中文

### 项目简介

`czz-aid` 是一个面向 Java 服务故障的自动修复 Agent。它从生产日志中的异常出发，解析 traceback，定位业务栈帧，调用受约束的代码工具读取上下文，生成最小修复补丁，自动补充回归测试，执行编译和测试，创建 Gitee PR，通过飞书发起人工 Review，并在 Review 结果后沉淀可复用的修复经验。

它不是简单的“让 LLM 改代码”演示，而是把 **故障输入、AI 定位、补丁生成、测试验证、PR 交付、人工审核、经验反思** 放进同一个可审计闭环里。

### 评审亮点

| 维度 | 亮点 |
| --- | --- |
| 完整闭环 | 日志监听、异常解析、业务栈帧定位、LLM 修复、编译测试、Gitee PR、飞书 Review、Review 后反思全部串联。 |
| 架构清晰 | `ingestion`、`code_nav`、`core`、`tools`、`reflection`、`storage` 等模块职责明确。 |
| Java 语义定位 | 解析 traceback，识别 top business frame，并通过 AST/symbol 工具读取函数级上下文。 |
| 工具约束 LLM | LLM 必须通过 schema 明确的 native function/tool 调用执行读写和验证。 |
| 写入安全 | 生产代码只接受 unified diff，并强制项目根目录、源码目录、diff header、patch 大小等校验。 |
| 自动测试 | v1.1.0 新增测试生成链路，通过 `apply_test_patch` 写入 `src/test/java/**/*Test.java`。 |
| 人审交付 | Agent 创建可审查 PR，并通过飞书请求人工 Review，不自动合并。 |
| 经验沉淀 | Review 失败时可比较 agent 分支和 human fix 分支，生成 `SKILL.md`。 |

核心价值：它把自动化、验证、人审和学习组合成一个工程系统，让 AI 从“一次性代码生成器”变成“受约束的软件维护参与者”。

### 1.1.0 更新

- 增强生产代码写入安全：项目根目录校验、Java 源码目录白名单、diff header 匹配、单文件 patch、patch 大小限制、默认禁止新建生产文件。
- 新增 `apply_test_patch`：测试专用补丁工具，仅允许 `src/test/java/**/*Test.java`，允许新建测试文件，但拒绝生产文件、禁用测试、弱断言和危险路径。
- 新增自动回归测试生成：业务修复成功后，在最终 compile/test 前让 LLM 生成 focused regression test。
- 新增测试 patch 重试：当 LLM 返回完整文件、非 unified diff 或超出限制时，会把工具拒绝原因反馈给 LLM，最多自动重试一次。
- 更新默认 Maven 测试命令：加入 `-XX:+EnableDynamicAgentLoading -Xshare:off`，降低新 JDK 下 Mockito/ByteBuddy warning 干扰。

### 工作流

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

### 架构

```text
agent/
  code_nav/      Java AST 和符号定位
  core/          修复编排、任务管理、权限、测试生成
  doctor/        环境自检
  ingestion/     日志监听、traceback 解析、Review callback
  llm/           OpenAI-compatible 客户端和调用记录
  reflection/    Review 结果分析、diff 对比、skill 生成
  storage/       session、task、skill 存储
  tools/         代码读写、测试补丁、编译测试、Git、Gitee、飞书等工具
tests/           核心行为契约测试
```

关键模块：

```text
agent/core/repair_agent.py              # 修复主循环
agent/core/test_generation_agent.py     # 自动测试生成
agent/tools/edit_code.py                # 生产代码补丁工具
agent/tools/apply_test_patch.py         # 测试补丁工具
agent/reflection/reflection_subagent.py # Review 后反思
```

### 安全设计

生产代码只能通过 `edit_code` 写入：

- 必须是 unified diff，拒绝原始代码片段和整文件重写。
- 目标路径必须位于 `project.root` 下。
- 默认只允许已有 `.java` 文件。
- 默认只允许 `src/main/java/**` 和 `src/test/java/**`。
- 禁止 `.git`、CI、配置、构建文件等敏感目标。
- diff header 必须和 `payload.path` 指向同一文件。
- 单次 patch 限制 hunk 数、新增行数和删除行数。

测试补丁使用独立工具 `apply_test_patch`：

- 仅允许 `src/test/java/**/*Test.java`。
- 允许新建测试文件。
- 拒绝生产代码、配置文件和构建文件。
- 拒绝 `@Disabled`、`@Ignore`、`assertTrue(true)` 等弱测试模式。
- 要求测试包含有意义的 assertion 或 verification。

系统不会自动合并 PR。修复成功后会进入 Review 状态，并通过飞书通知人工审核。

### 命令

```bash
pip install -r requirements.txt
python -m agent.main doctor
python -m agent.main watch
python -m agent.main repair --bug-id demo-bug --raw-log-path ./logs/app.log --project mall-service
python -m agent.main reflect --bug-id demo-bug --result pass
python -m pytest
```

默认测试命令：

```bash
mvn "-DargLine=-XX:+EnableDynamicAgentLoading -Xshare:off" test
```

### 配置

`agent/main.py` 默认从 `config.example.yaml` 加载配置。

| 配置组 | 说明 |
| --- | --- |
| `project` | Java 项目根目录、语言、默认分支、编译命令、测试命令 |
| `llm` | OpenAI-compatible endpoint、API key、模型、超时 |
| `gitee` | PR 创建相关配置 |
| `feishu` | webhook 和本地 Review callback 配置 |
| `session` | 会话和产物存储目录 |
| `agent` | 重试次数、Review、反思、日志监听路径 |

分享仓库前，请把本地真实密钥、token、webhook 替换为占位符，并轮换已经暴露过的凭据。

### 当前成熟度

这是一个具备完整闭环的强原型，适合作为 agentic software maintenance 的参考实现。它已经展示了真实工程系统需要的关键能力：受控工具调用、路径与权限边界、编译测试 gate、人审交付、失败反思和知识沉淀。

后续建议继续加强持久化存储、Review callback 鉴权、平台集成抽象、skill 启用前安全扫描，以及更深入的 Java AST/语义级风险检查。

<p align="right"><a href="#top">回到顶部</a> | <a href="#english">English</a></p>

---

<a id="english"></a>

## English

### Overview

`czz-aid` is an autonomous repair agent for Java service incidents. Starting from production exceptions, it parses tracebacks, locates business frames, inspects bounded source context through guarded tools, generates minimal patches, creates regression tests, runs compile/test gates, opens Gitee pull requests, requests Feishu human review, and reflects on review outcomes to produce reusable repair knowledge.

It is not a shallow "LLM edits code" demo. It models repair as an auditable workflow that combines **incident ingestion, AI-assisted diagnosis, patch generation, validation, PR delivery, human review, and durable learning**.

### Evaluation Highlights

| Dimension | Highlight |
| --- | --- |
| Complete Loop | Log watching, traceback parsing, business-frame localization, LLM repair, compile/test, Gitee PR, Feishu review, and reflection are connected in one flow. |
| Clear Architecture | Modules such as `ingestion`, `code_nav`, `core`, `tools`, `reflection`, and `storage` have clear responsibilities. |
| Java-Aware Context | Tracebacks are parsed, top business frames are identified, and AST/symbol tools provide function-level context. |
| Tool-Constrained LLM | The LLM acts through typed native function tools instead of unconstrained prose or arbitrary shell execution. |
| Safe Edits | Production edits require unified diffs, project-root checks, source-root allowlists, diff-header matching, and patch-size limits. |
| Test Generation | v1.1.0 adds LLM-generated regression tests through the test-only `apply_test_patch` tool. |
| Human Review | The agent creates reviewable PRs and requests Feishu review instead of self-merging. |
| Durable Learning | Failed reviews can compare agent and human-fix branches and produce reusable `SKILL.md` knowledge. |

Core value: `czz-aid` turns repair, validation, review, and learning into one engineering system, making AI a constrained software-maintenance participant rather than a one-off code generator.

### What's New in 1.1.0

- Safer production edits: project-root enforcement, Java source-root allowlists, diff-header matching, single-file patches, patch-size limits, and default denial for new production files.
- Added `apply_test_patch`, restricted to `src/test/java/**/*Test.java`; it allows new tests while rejecting production edits, disabled tests, weak assertions, and unsafe targets.
- Added automatic regression-test generation after a valid source repair and before the final compile/test gate.
- Added one automatic retry when the LLM returns a full file, non-diff content, or an oversized test patch.
- Updated the default Maven test command with `-XX:+EnableDynamicAgentLoading -Xshare:off` to reduce modern JDK Mockito/ByteBuddy warnings.

### Workflow

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

### Architecture

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

Key modules:

```text
agent/core/repair_agent.py              # main repair loop
agent/core/test_generation_agent.py     # regression test generation
agent/tools/edit_code.py                # production patch tool
agent/tools/apply_test_patch.py         # test patch tool
agent/reflection/reflection_subagent.py # post-review reflection
```

### Safety Model

Production code can only be modified through `edit_code`:

- Unified diff only; raw snippets and full-file rewrites are rejected.
- Target path must stay under `project.root`.
- Existing `.java` files only by default.
- Source-root allowlist: `src/main/java/**` and `src/test/java/**`.
- Sensitive targets such as `.git`, CI, config, and build files are denied.
- Diff headers must match `payload.path`.
- Hunk, added-line, and deleted-line limits are enforced.

Test patches use the separate `apply_test_patch` tool:

- Only `src/test/java/**/*Test.java` is allowed.
- New test files are allowed.
- Production files, config files, and build files are rejected.
- Weak tests such as `@Disabled`, `@Ignore`, and `assertTrue(true)` are rejected.
- Tests must include meaningful assertions or verifications.

The system does not self-merge PRs. Successful repairs enter review and are sent to humans through Feishu.

### Commands

```bash
pip install -r requirements.txt
python -m agent.main doctor
python -m agent.main watch
python -m agent.main repair --bug-id demo-bug --raw-log-path ./logs/app.log --project mall-service
python -m agent.main reflect --bug-id demo-bug --result pass
python -m pytest
```

Default test command:

```bash
mvn "-DargLine=-XX:+EnableDynamicAgentLoading -Xshare:off" test
```

### Configuration

`agent/main.py` loads runtime settings from `config.example.yaml` by default.

| Group | Description |
| --- | --- |
| `project` | Java project root, language, default branch, compile command, test command |
| `llm` | OpenAI-compatible endpoint, API key, model, timeout |
| `gitee` | Pull request creation settings |
| `feishu` | Webhook and local review callback settings |
| `session` | Session and artifact storage |
| `agent` | Retry count, review policy, reflection, watched logs |

Before sharing the repository, replace real local credentials, tokens, and webhooks with placeholders and rotate anything that has been exposed.

### Current Maturity

This is a strong working prototype and a useful reference design for agentic software maintenance. It already demonstrates core capabilities required in real engineering systems: constrained tool use, path and permission boundaries, compile/test gates, human-reviewed delivery, failure reflection, and durable knowledge capture.

Recommended hardening work includes durable storage, review callback authentication, platform abstraction, safety scanning before enabling generated skills, and deeper Java AST or semantic risk checks for repair patches.

<p align="right"><a href="#top">Back to top</a> | <a href="#zh">中文</a></p>
