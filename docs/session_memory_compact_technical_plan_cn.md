# czz-aid Session Memory 与上下文压缩技术方案

## 1. 设计结论

本方案用于解决 czz-aid 在长链路自动修复过程中上下文持续膨胀的问题。

当前 RepairAgent 的主循环会不断向 `llm_messages` 追加：

```text
system prompt
assistant tool_call
tool result
assistant tool_call
tool result
...
```

当修复过程变长时，以下内容会持续占用上下文：

- 多次 `read_code` 的源码片段。
- 多次 `search_code` / `search_skill` / `search_project_doc` 的检索结果。
- `run_compile` / `run_test` 的命令输出。
- `edit_code` 的 patch 结果。
- 编译、测试、rollback、PR、飞书通知等状态。

因此需要引入两个机制：

```text
Session Memory Agent
Context Compactor
```

其中：

- `SessionMemoryAgent` 是异步子 Agent。
- 子 Agent 与主 RepairAgent 隔离。
- 子 Agent 不能调用修复工具。
- 子 Agent 唯一允许的写入目标是当前 bug 会话下的 `summary.md`。
- `summary.md` 是本次修复会话的可读记忆，不是主流程的权威状态源。
- 主 RepairAgent 的权威状态仍然来自结构化 session、ToolResult、edit_state、compile/test result。

上下文窗口按 1M tokens 设计，1M 是模型窗口上限，用于压缩阈值参考，不代表可以无限堆上下文。

核心原则：

```text
完整审计数据进入 session / artifact。
可读修复记忆进入 summary.md。
LLM 主上下文只保留当前工作所需的压缩上下文。
```

## 2. 适用背景

czz-aid 当前已有较完整的自动修复链路：

```text
LogWatcher
-> TracebackParser
-> Dedup
-> AST frame_contexts
-> RepairAgent
-> native function calling
-> read/search/edit/compile/test
-> Gitee PR
-> 飞书 Review
-> Reflection
-> Skill / RAG
```

随着以下能力加入，主上下文会越来越大：

- 本地 Skill RAG。
- 本地业务文档 RAG。
- 飞书知识库同步到本地 RAG。
- `search_skill` / `search_project_doc` 工具。
- 多轮自动修复。
- 测试生成。
- Reflection / Skill 沉淀。

如果只靠简单截断历史，会破坏 OpenAI tool calling 协议：

```json
{
  "role": "assistant",
  "tool_calls": [
    {
      "id": "call_x",
      "type": "function",
      "function": {
        "name": "read_code",
        "arguments": "{}"
      }
    }
  ]
}
```

必须紧跟：

```json
{
  "role": "tool",
  "tool_call_id": "call_x",
  "content": "..."
}
```

因此上下文压缩不能简单删除消息，必须保证 tool call / tool result 成对保留。

## 3. 目标

### 3.1 功能目标

1. 为每个 bug repair session 维护一份 `summary.md`。
2. 每次完整 LLM tool_call 周期后，将事件异步提交给 `SessionMemoryAgent`。
3. `SessionMemoryAgent` 只更新 `summary.md`，不影响主 Agent 决策。
4. 当主上下文接近 1M token 窗口阈值时，触发 Context Compact。
5. Compact 后从 `summary.md`、结构化 session、最近消息重建主 LLM 输入。
6. 保留 OpenAI `assistant.tool_calls` / `tool` 消息协议完整性。
7. 压缩失败不应默认中断 repair，除非已经达到 hard limit。

### 3.2 非目标

第一版不做：

- Claude Code prompt cache sharing。
- 复杂 tokenizer 精确计数。
- 子 Agent 执行修复工具。
- 子 Agent 修改 session、源码、PR、飞书通知。
- 用 `summary.md` 替代结构化 session。
- 把所有历史工具结果长期塞进 prompt。

## 4. 总体架构

```text
RepairAgent
  |
  |-- 主修复循环
  |-- 持久化完整 session / tool_calls / artifacts
  |-- 每次完整 tool_call 周期后 enqueue MemoryUpdateEvent
  |-- LLM 调用前 ContextCompactor.compact_if_needed()
  |
  |-- MemoryUpdateQueue
  |     |-- 接收主 Agent 产生的事件
  |     |-- 单 bug 串行消费
  |     |-- 合并短时间内的多个事件
  |
  |-- SessionMemoryAgent
  |     |-- 异步运行
  |     |-- 与主 Agent 隔离
  |     |-- 不允许调用工具
  |     |-- 只能读事件和当前 summary.md
  |     |-- 只能写 summary.md
  |
  |-- ContextCompactor
        |-- 估算 token
        |-- 判断是否需要 compact
        |-- 读取 summary.md
        |-- 保留最近 tool call/result pair
        |-- 重建 llm_messages
```

## 5. 状态分层

本方案把状态分成三层。

### 5.1 完整审计层

存储位置：

```text
SessionStore
data/sessions/
artifact files
```

保存：

- 完整 `BugEvent`。
- 完整工具调用历史。
- 完整 `ToolResult`。
- 完整 compile/test 输出 artifact。
- patch artifact。
- PR / 飞书结果。

用途：

- Debug。
- Reflection。
- Skill 生成。
- 人工排查。

### 5.2 会话记忆层

存储位置：

```text
{config.session.root_dir}/{bug_id}/session-memory/summary.md
```

保存：

- 本次修复当前状态。
- 已确认事实。
- 已读文件和方法。
- 已做尝试。
- 最新失败原因。
- 当前下一步。
- 不要重复的错误路径。

用途：

- Compact 后恢复。
- 人工查看。
- 飞书求助卡片附加信息。
- 后续 resume。

### 5.3 主 LLM 工作上下文层

存储位置：

```text
RepairAgent.llm_messages
```

保存：

- system prompt。
- 当前 bug 摘要。
- 当前 frame_contexts。
- 当前 top-k RAG 摘要。
- 最近若干轮 tool call/result pair。
- compact 后的 session memory 摘要。

用途：

- 直接发送给主 LLM。

## 6. Session Memory 文件设计

路径：

```text
{config.session.root_dir}/{bug_id}/session-memory/summary.md
```

模板：

```md
# Current State
_当前修复正在做什么，是否已有补丁，下一步要做什么_

# Bug
_bug_id、异常类型、请求路径、关键错误信息、top business frame_

# Evidence
_已确认的代码事实、读过的文件/函数、业务约束、RAG 文档结论_

# Attempts
_做过哪些修复、补丁改了什么、哪些失败/回滚、失败原因_

# Tool Results
_关键工具结果：search/read/edit/compile/test/rollback/PR 的摘要_

# Files and Symbols
_重要文件、类、方法、行号、为什么相关_

# Constraints
_权限限制、白名单拒绝、业务规则、用户/评审反馈_

# Next Actions
_下一步应该做什么，哪些路径不要重复_

# Final Outcome
_PR、测试、编译、飞书审核、最终状态_
```

规则：

- 必须保留所有标题。
- 必须保留每个标题下的斜体说明行。
- 只更新说明行下面的内容。
- 不记录 secret、token、webhook、cookie、password。
- 不把未验证假设写成已确认事实。
- 编译和测试结果必须标明来源和时间。
- `summary.md` 是辅助记忆，不是主流程权威状态。

## 7. 核心数据结构

### 7.1 SessionMemoryState

```python
class SessionMemoryState(BaseModel):
    bug_id: str
    memory_path: str = ""
    initialized: bool = False

    last_summarized_history_index: int = 0
    last_summarized_message_index: int = 0
    last_summarized_tool_call_index: int = 0

    session_revision: int = 0
    summary_revision: int = 0

    update_count: int = 0
    in_progress: bool = False
    last_update_at: datetime | None = None
    last_error: str = ""
```

说明：

- `session_revision` 用于判断异步摘要是否过期。
- `summary_revision` 用于避免旧摘要覆盖新摘要。
- `last_summarized_tool_call_index` 用于增量更新。

### 7.2 MemoryUpdateEvent

```python
class MemoryUpdateEvent(BaseModel):
    bug_id: str
    event_id: str
    event_type: str

    tool_call_index: int = 0
    message_index: int = 0
    history_index: int = 0
    session_revision: int = 0

    tool: str = ""
    reason: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)

    session_status: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

事件必须在工具执行完成后产生，而不是在 tool_call 刚生成时产生。

原因：

```text
tool_call 只代表模型想做什么。
ToolResult 才代表实际发生了什么。
```

### 7.3 CompactionState

```python
class CompactionState(BaseModel):
    bug_id: str
    compacted: bool = False
    compact_count: int = 0

    last_compacted_message_index: int = 0
    last_compacted_history_index: int = 0

    tokens_before: int = 0
    tokens_after: int = 0
    usage_ratio_before: float = 0.0
    usage_ratio_after: float = 0.0

    last_reason: str = ""
    last_compacted_at: datetime | None = None
    last_error: str = ""
```

### 7.4 EditState

虽然本方案重点是 Session Memory，但主 Agent 仍然必须维护结构化 `edit_state`。

```python
class EditState(BaseModel):
    bug_id: str
    editing_file: str = ""
    changed_files: list[str] = Field(default_factory=list)
    last_patch_summary: str = ""
    last_patch_artifact: str = ""
    patch_validated: bool = False
    compile_status: str = "not_run"
    test_status: str = "not_run"
    latest_compile_error: dict[str, Any] = Field(default_factory=dict)
    latest_test_error: dict[str, Any] = Field(default_factory=dict)
```

`summary.md` 不能替代 `edit_state`。

## 8. Token 阈值设计

模型窗口：

```yaml
context_window_tokens: 1000000
```

建议配置：

```yaml
context:
  enabled: true
  context_window_tokens: 1000000
  reserved_output_tokens: 64000

  warning_ratio: 0.70
  auto_compact_ratio: 0.85
  hard_limit_ratio: 0.93
  reactive_retry_limit: 2

  keep_recent_messages: 20
  keep_recent_tool_pairs: 10
  session_memory_max_chars: 30000
```

有效窗口：

```text
effective_window = context_window_tokens - reserved_output_tokens
```

示例：

```text
context_window_tokens = 1,000,000
reserved_output_tokens = 64,000
effective_window = 936,000
```

阈值：

| 阶段 | 触发点 |
|---|---:|
| warning | `936000 * 0.70 = 655200` |
| auto compact | `936000 * 0.85 = 795600` |
| hard limit | `936000 * 0.93 = 870480` |

注意：

1M 是模型窗口，不是实际可用输入上限。必须预留输出空间。

## 9. Memory 更新时机

### 9.1 正确触发点

推荐触发点：

```text
每次完整 LLM tool_call 周期后
```

完整周期定义：

```text
1. 主 LLM 返回 assistant tool_call
2. RepairAgent 执行工具
3. 工具返回 ToolResult
4. RepairAgent 把 ToolResult 写入 history/session/llm_messages
5. RepairAgent 生成 MemoryUpdateEvent
6. 事件进入异步 MemoryUpdateQueue
```

不推荐在 tool_call 刚生成时摘要，因为那时工具还没执行。

### 9.2 高价值事件

以下事件应优先刷新 memory：

```text
edit_code success
edit_code failed
run_compile failed
run_test failed
rollback completed
finish_patch rejected
create_pr success
create_pr failed
repair exhausted
repair passed
review requested
```

### 9.3 事件合并

虽然每次完整 tool_call 周期都会产生事件，但不建议每个事件都立刻单独调用 LLM。

建议：

```text
每个事件都 enqueue。
后台 worker 串行消费。
短时间内多个事件可以 batch。
高价值事件可以立即 flush。
```

这样既满足“每次 tool_call 后摘要”的语义，又避免频繁 LLM 调用。

## 10. 异步隔离设计

### 10.1 子 Agent 权限

`SessionMemoryAgent` 只允许：

```text
read summary.md
read MemoryUpdateEvent
write summary.md
```

禁止：

```text
edit_code
read_code
search_code
run_command
run_compile
run_test
create_pr
feishu_tool
修改 session
修改源码
修改 git
```

### 10.2 单 bug 串行写

必须避免并发写覆盖：

```text
bug_id = log-xxx
同一个 bug 同一时间只允许一个 memory update worker 写 summary.md
```

推荐实现：

```text
MemoryUpdateQueue 按 bug_id 分区
同一 bug_id 串行消费
不同 bug_id 可并行
```

### 10.3 过期写保护

写入前检查：

```text
如果 event.session_revision < current_summary_revision
则说明本次摘要过期，不能直接覆盖 summary.md
```

处理策略：

```text
1. 丢弃旧摘要
2. 或基于最新 summary.md 重新合并
```

第一版建议直接丢弃旧摘要并记录 warning。

## 11. Context Compact 设计

Session Memory 更新和 Context Compact 是两个独立机制。

```text
Session Memory:
  持续异步维护 summary.md

Context Compact:
  只有当主上下文接近 token 阈值时才重建 llm_messages
```

### 11.1 正常上下文

未触发 compact 时：

```text
system prompt
bug_event
frame_contexts
retrieved_skills
retrieved_project_docs
slim session
recent llm_messages
```

### 11.2 compact 后上下文

触发 compact 后：

```python
[
    {"role": "system", "content": compacted_system_prompt},
    {
        "role": "user",
        "content": "This repair session was compacted. Use the session memory as the authoritative summary of earlier work."
    },
    {
        "role": "user",
        "content": "Session memory:\n\n" + summary_md
    },
    *recent_valid_tool_pairs
]
```

### 11.3 summary.md 的作用

`summary.md` 可以用于：

- compact 后恢复上下文。
- 人工 debug。
- 飞书求助。
- resume。

`summary.md` 不用于：

- 判断是否允许 `finish_patch`。
- 判断是否创建 PR。
- 判断 compile/test 是否通过。
- 判断哪些文件要提交。

这些必须继续使用结构化 session / edit_state / ToolResult。

## 12. Tool Pair Preservation

compact 时必须保持 OpenAI tool calling 协议。

合法结构：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "read_code",
        "arguments": "{}"
      }
    }
  ]
}
```

后面必须有：

```json
{
  "role": "tool",
  "tool_call_id": "call_1",
  "content": "..."
}
```

compact 选择 recent messages 时：

```text
1. 先取最后 N 条消息。
2. 如果第一条是 tool message，向前补对应 assistant tool_call。
3. 如果保留 assistant tool_calls，必须保留所有对应 tool result。
4. 如果一个 assistant 有多个 tool_calls，必须全部配对。
5. 无法配对时，整组丢弃。
6. 不允许留下孤立 tool message。
```

## 13. Prompt Session Slimming

当前 `build_prompt_template()` 不应注入完整 session。

推荐 slim session：

```python
{
    "status": session.get("status"),
    "last_error": summarize_tool_result(session.get("last_error")),
    "last_tool_result": summarize_tool_result(session.get("last_tool_result")),
    "compile_result": summarize_tool_result(session.get("compile_result")),
    "test_result": summarize_tool_result(session.get("test_result")),
    "rollback_result": summarize_tool_result(session.get("rollback_result")),
    "pr_url": session.get("pr_url"),
    "agent_branch": session.get("agent_branch"),
    "base_branch": session.get("base_branch"),
    "denied_commands": last_n(session.get("denied_commands"), 3),
    "edit_state": session.get("edit_state"),
    "session_memory_state": session.get("session_memory_state"),
    "compaction_state": session.get("compaction_state"),
}
```

禁止注入：

```text
完整 tool_calls
完整 feishu payload
完整 traceback
完整 stdout/stderr
完整 RAG chunk
完整历史事件
```

## 14. ToolResult 压缩

即使有 1M 上下文，也必须做工具结果压缩。

原因：

```text
1M 是模型窗口，不是无限上下文。
mvn test / run_command / git diff 仍然可能很大。
大工具输出会降低模型关注度。
```

压缩策略：

| 工具 | LLM 保留 | 完整内容 |
|---|---|---|
| read_code | path、range、contentHash、关键片段 | artifact |
| search_code | top matches、命中数量、文件列表 | artifact |
| search_skill | title、score、source、snippet | 本地 RAG 可重查 |
| search_project_doc | title、doc_type、score、业务约束 snippet | 本地 RAG 可重查 |
| edit_code | path、patch summary、changed_files | patch artifact |
| run_compile | file、line、error message | compile log artifact |
| run_test | failed test、expected/actual、surefire path | test log artifact |
| run_command | exit_code、关键错误、最后 N 行 | stdout/stderr artifact |
| git_diff | changed files、hunk summary | full diff artifact |

## 15. SessionMemoryAgent Prompt

子 Agent prompt：

```text
你是 czz-aid 的 SessionMemoryAgent。

你的任务是维护一个 Java 自动修复会话的 summary.md。

你不是修复 Agent。
你不能调用工具。
你不能修改代码。
你不能修改 session。
你只能输出完整的 summary.md 内容。

规则：
- 必须返回完整 Markdown 文件。
- 必须保留所有 section header。
- 必须保留每个 section 下的斜体说明行。
- 不要编造事实。
- 不要把未验证假设写成已确认事实。
- 不要写入 secret、token、webhook、cookie、password。
- 优先记录精确工程事实：文件路径、类名、方法名、行号、命令、错误信息、patch 摘要、rollback 原因、PR 状态。
- 每次都更新 Current State 和 Next Actions。

当前 summary.md：
<memory>
{{current_memory}}
</memory>

BugEvent：
<bug>
{{bug_json}}
</bug>

新增事件：
<events>
{{events_json}}
</events>
```

输出校验：

```text
1. 包含所有标题。
2. 保留斜体说明行。
3. 非空。
4. 已脱敏。
5. 没有明显 tool call JSON 或源码大段粘贴。
```

## 16. 失败处理

### 16.1 Memory update 失败

处理：

```text
记录 SessionMemoryState.last_error
不阻塞主 RepairAgent
不修改已有 summary.md
```

### 16.2 summary.md 缺失

处理：

```text
ContextCompactor 触发时发现 summary.md 缺失
-> 尝试同步生成 emergency summary
-> 如果失败，用结构化 session 构造规则摘要
```

### 16.3 compact 后仍超阈值

处理：

```text
如果 tokens_after >= hard_limit
-> 不继续调用主 LLM
-> 飞书求助或返回明确失败
```

### 16.4 prompt too long

处理：

```text
只在明确上下文过长错误时触发 reactive compact：
- prompt too long
- context length exceeded
- maximum context length
- tokens exceed
```

不要把网络错误、鉴权错误、模型临时错误误判为 reactive compact。

## 17. RepairAgent 集成点

### 17.1 初始化

```python
self.session_memory_store = SessionMemoryStore(config)
self.memory_update_queue = MemoryUpdateQueue(config)
self.session_memory_agent = SessionMemoryAgent(
    config=config,
    llm_client=memory_llm_client,
    memory_store=self.session_memory_store,
)
self.context_compactor = ContextCompactor(
    config=config,
    memory_store=self.session_memory_store,
)
```

### 17.2 工具执行完成后

在工具执行并保存结果后：

```python
result = tool.run(arguments)

history.append({"tool": tool.spec.name, "result": result.model_dump()})
session["last_tool_result"] = result.model_dump()
self._append_session_tool_call(session, action, result)
self._append_tool_result_message(llm_messages, action, result)

event = MemoryUpdateEvent.from_tool_result(
    bug_event=bug_event,
    action=action,
    result=result,
    session=session,
    history_index=len(history),
    message_index=len(llm_messages),
)
self.memory_update_queue.enqueue(event)
```

### 17.3 LLM 调用前

```python
compaction = self.context_compactor.compact_if_needed(
    bug_event=bug_event,
    session=session,
    messages=llm_messages,
    tools=self._openai_tools(),
)

if compaction.compacted:
    llm_messages[:] = compaction.messages
    session["compaction_state"] = compaction.state
```

## 18. 配置建议

```yaml
agent:
  session_memory_enabled: true
  session_memory_async: true
  session_memory_flush_after_tool_result: true
  session_memory_batch_window_seconds: 2
  session_memory_max_events_per_update: 8
  session_memory_max_chars: 30000

  context_compact_enabled: true
  context_window_tokens: 1000000
  context_reserved_output_tokens: 64000
  context_warning_ratio: 0.70
  context_auto_compact_ratio: 0.85
  context_hard_limit_ratio: 0.93
  context_keep_recent_messages: 20
  context_keep_recent_tool_pairs: 10
  context_reactive_retry_limit: 2
```

## 19. 测试计划

### 19.1 Session Memory

```text
test_memory_file_created_from_template
test_template_only_memory_is_empty
test_tool_result_enqueues_memory_event
test_memory_agent_only_writes_summary_md
test_memory_agent_preserves_headers
test_memory_agent_sanitizes_secret
test_stale_memory_update_does_not_overwrite_newer_summary
test_same_bug_memory_updates_are_serialized
```

### 19.2 Context Compact

```text
test_token_threshold_triggers_compact
test_compact_uses_summary_md
test_compact_keeps_recent_tool_pairs
test_compact_drops_orphan_tool_message
test_compact_preserves_multi_tool_call_group
test_missing_summary_uses_emergency_summary
test_compact_result_updates_session_state
```

### 19.3 RepairAgent 集成

```text
test_repair_agent_enqueues_memory_after_tool_result
test_repair_agent_does_not_wait_for_async_memory_update
test_repair_agent_uses_structure_state_not_summary_for_finish_patch
test_build_prompt_template_uses_slim_session
test_summary_md_not_injected_before_compact
```

### 19.4 失败处理

```text
test_memory_update_failure_non_fatal
test_compact_failure_below_threshold_continues
test_compact_failure_above_hard_limit_blocks_llm
test_prompt_too_long_triggers_reactive_compact
test_non_context_error_does_not_trigger_reactive_compact
```

## 20. 实施阶段

### Phase 1：Session Memory 基础

交付：

```text
SessionMemoryStore
MemoryUpdateEvent
SessionMemoryState
summary.md 模板
MemoryUpdateQueue
异步 worker 雏形
```

目标：

```text
每次工具结果后产生 memory event。
子 Agent 异步更新 summary.md。
主 RepairAgent 不等待、不依赖 summary.md。
```

### Phase 2：上下文压缩

交付：

```text
TokenBudget
ContextCompactor
tool pair preservation
compaction_state
compact 后 messages 重建
```

目标：

```text
接近 1M 窗口阈值时，使用 summary.md 和最近消息重建上下文。
```

### Phase 3：规则压缩与 Artifact

交付：

```text
ToolResultCompressor
ArtifactStore
run_command / compile / test / diff 全量 artifact
LLM 接收 compact ToolResult
```

目标：

```text
减少工具结果对主上下文的污染。
保留完整证据供 Reflection 和人工排查。
```

### Phase 4：Reactive Compact

交付：

```text
PromptTooLong 检测
reactive compact retry
retry limit
失败后飞书求助
```

目标：

```text
上下文溢出时自动兜底恢复。
```

## 21. 风险与规避

| 风险 | 影响 | 规避 |
|---|---|---|
| tool_call 后、tool_result 前摘要 | summary 记录未发生事实 | 只在 ToolResult 后产生事件 |
| 异步旧摘要覆盖新摘要 | summary 状态倒退 | revision / 串行队列 / stale check |
| summary.md 被主流程误用 | 错误 finish_patch / PR | 主流程只信结构化 session/edit_state |
| 每个事件单独 LLM 摘要 | 成本和延迟增加 | queue + batch + 高价值事件优先 |
| OpenAI tool pair 被拆 | API 报错 | compact 时成组保留 |
| 1M 窗口被用满 | 输出空间不足 | reserved_output_tokens |
| secret 写入 summary | 安全风险 | Sanitizer + 校验 |
| compact 后丢失当前 patch | 修复连续性断裂 | edit_state P0 恢复 |

## 22. 最终判断

在以下前提成立时，本方案可行：

```text
1. SessionMemoryAgent 异步执行。
2. SessionMemoryAgent 与主 RepairAgent 隔离。
3. SessionMemoryAgent 只能编辑 summary.md。
4. 事件在 ToolResult 产生后入队。
5. 同一 bug 的 summary.md 更新串行化。
6. summary.md 不替代结构化 session。
7. 1M token 只作为阈值参考，仍保留输出空间。
```

推荐的架构定位是：

```text
summary.md 是会话记忆。
session/edit_state 是权威状态。
artifacts 是完整证据。
llm_messages 是当前工作上下文。
```

一句话总结：

```text
Session Memory Agent 负责异步维护“人和模型都能读懂的 repair 记忆”，Context Compactor 只在接近 1M token 阈值时用这份记忆重建主 LLM 上下文；主修复流程始终以结构化 session 和验证结果为准。
```
