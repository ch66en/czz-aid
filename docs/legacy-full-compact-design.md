# Legacy Full Compact 中文设计方案

## 1. 背景

`auto-fix-agent` 在一次修复任务中持续维护 `llm_messages`：

```text
system prompt
  -> assistant(tool_calls)
  -> tool(result)
  -> assistant(tool_calls)
  -> tool(result)
  -> ...
```

当前主循环会不断向 `llm_messages` 追加工具调用和工具结果。外层修复重试次数有限，但单次修复中的工具调用循环没有硬性上限。复杂缺陷可能经历多次代码读取、搜索、编辑、编译、测试和回滚，最终导致模型输入超过上下文窗口。

本方案借鉴 Claude Code Legacy Full Compact 的核心思路：

> 当上下文接近模型窗口上限时，调用一次无工具 LLM 生成结构化摘要，再用摘要、关键状态和必要文件内容替换旧的活动上下文。

本方案只修改 `auto-fix-agent`，不修改仓库根目录下的 `src`。

## 2. 目标

- 在主模型请求前自动检查上下文大小。
- 接近模型上下文窗口时执行 Legacy Full Compact。
- 使用 LLM 总结完整历史，保留继续修复所需的信息。
- 压缩后使用“旧历史摘要 + 最近完整 API 轮次”重建活动消息。
- 最近消息按完整工具调用轮次保留，避免残留孤立的 `tool` 消息。
- 保留完整审计记录，不影响 PR 创建、回滚和人工排查。
- 摘要请求自身超长时，按完整工具轮次截断旧消息并重试。
- 连续压缩失败时触发熔断，避免重复浪费 API 调用。
- 将压缩摘要和指标持久化，便于调试和后续优化。

## 3. 非目标

第一版不实现以下能力：

- Session Memory 增量记忆 Agent。
- Prompt Cache 共享。
- Reactive Compact。
- Partial Compact。
- Microcompact。
- 图片和文档附件剥离。
- Claude Code Hooks 体系。

这些能力可以在 Legacy Full Compact 稳定运行后再按需增加。

## 4. 当前项目中的接入位置

核心接入文件：

```text
agent/core/repair_agent.py
```

当前活动上下文初始化位置：

```python
history: list[dict[str, Any]] = []
llm_messages: list[dict[str, Any]] = [
    {"role": "system", "content": prompt_template}
]
```

当前主模型调用位置：

```python
action = self._ask_llm(llm_messages, history, bug_event, session)
```

当前工具结果追加位置：

```python
self._append_tool_result_message(llm_messages, action, result)
```

Legacy Full Compact 应在每次 `_ask_llm()` 之前运行：

```text
准备调用主模型
  -> 检查是否需要 compact
  -> 必要时生成摘要并替换 llm_messages
  -> 调用主模型
```

## 5. 数据边界

Legacy Full Compact 只压缩模型活动上下文，不删除审计数据。

| 数据 | 处理方式 |
| --- | --- |
| `llm_messages` | 达到阈值后重建：旧历史替换为摘要，最近完整 API 轮次原样保留 |
| `history` | 完整保留 |
| `session["tool_calls"]` | 完整保留 |
| `data/sessions/llm_calls/*.json` | 继续保留 |
| 压缩摘要 | 单独写入 Markdown 文件 |
| 完整 transcript | 按任务追加写入 JSONL 文件，PTL 截断后可按路径恢复 |
| 最近读取的源码 | 压缩后从磁盘重新读取并注入 |

保留 `history` 很重要，因为以下逻辑依赖它：

- 识别已经修改的文件。
- 回滚当前修复轮次中的代码变更。
- 创建 PR 时收集需要提交的文件。
- 生成回归测试时读取最近编辑结果。

## 6. 总体架构

```text
RepairAgent
  |
  |-- 主修复循环
  |     |
  |     |-- LegacyFullCompactor.compact_if_needed()
  |     |     |
  |     |     |-- TokenEstimator
  |     |     |-- SummaryPromptBuilder
  |     |     |-- 无工具 LLM 摘要调用
  |     |     |-- PTL 截断重试
  |     |     |-- 最近源码恢复
  |     |     |-- 摘要和指标持久化
  |     |
  |     |-- OpenAICompatibleClient.chat()
  |     |-- 执行工具
  |     |-- 追加完整审计记录
  |
  |-- SessionStore
        |-- 保留完整 tool_calls
        |-- 保存 legacy_compaction_state
```

## 7. 新增配置

在 `agent/config.py` 中新增：

```python
class CompactConfig(BaseModel):
    enabled: bool = True
    context_window_tokens: int = 1_000_000
    summary_max_output_tokens: int = 20_000
    normal_output_reserve_tokens: int = 40_000
    buffer_tokens: int = 100_000
    max_ptl_retries: int = 3
    max_consecutive_failures: int = 3
    keep_recent_rounds: int = 8
    restore_max_files: int = 8
    restore_max_chars_per_file: int = 16_000
    restore_total_chars: int = 80_000
```

在 `AppConfig` 中增加：

```python
compact: CompactConfig = Field(default_factory=CompactConfig)
```

在 `config.example.copy.yaml` 中增加：

```yaml
compact:
  enabled: true
  context_window_tokens: 1000000
  summary_max_output_tokens: 20000
  normal_output_reserve_tokens: 40000
  buffer_tokens: 100000
  max_ptl_retries: 3
  max_consecutive_failures: 3
  keep_recent_rounds: 8
  restore_max_files: 8
  restore_max_chars_per_file: 16000
  restore_total_chars: 80000
```

上下文窗口必须显式配置。项目支持不同的 OpenAI-compatible 模型和供应商，不应仅根据模型名称猜测窗口大小。

## 8. 阈值计算

默认计算公式：

```text
有效上下文窗口
  = context_window_tokens - summary_max_output_tokens

自动压缩阈值
  = 有效上下文窗口 - buffer_tokens

主调用阻断阈值
  = context_window_tokens - normal_output_reserve_tokens
```

使用默认配置时：

```text
有效上下文窗口 = 1000000 - 20000 = 980000
自动压缩阈值   = 980000 - 100000 = 880000
主调用阻断阈值 = 1000000 - 40000 = 960000
```

含义：

- 估算 token 小于 `880000`：正常调用主模型。
- 估算 token 大于等于 `880000`：尝试压缩。
- 压缩失败且原上下文仍小于 `960000`：允许继续调用主模型。
- 压缩失败且原上下文大于等于 `960000`：阻断主模型调用并请求人工介入。

## 9. Token 估算

第一版使用轻量估算即可，不需要引入额外 tokenizer 依赖。

新增：

```text
agent/core/token_estimator.py
```

建议实现：

```python
class TokenEstimator:
    def estimate_text(self, text: str) -> int:
        return max(1, len(text.encode("utf-8")) // 4)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        ...

    def estimate_tools(self, tools: list[dict[str, Any]]) -> int:
        ...
```

估算时需要计入：

- 全部 `llm_messages`。
- 工具 schema。
- JSON 结构开销。
- 每条消息的固定角色开销。

后续可以根据 LLM API 返回的 `prompt_tokens` 校准估算比例。

## 10. 新增数据结构

在 `agent/models.py` 中新增：

```python
class LegacyCompactionState(BaseModel):
    compact_count: int = 0
    consecutive_failures: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    last_summary_path: str = ""
    last_transcript_path: str = ""
    last_error: str = ""
    last_compacted_at: datetime | None = None


class LegacyCompactionResult(BaseModel):
    compacted: bool = False
    blocked: bool = False
    reason: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    dropped_message_count: int = 0
    restored_file_count: int = 0
    summary_path: str = ""
    transcript_path: str = ""
```

在 `session` 中新增：

```python
session["legacy_compaction_state"]
session["last_legacy_compaction"]
```

## 11. 新增核心模块

新增：

```text
agent/core/legacy_full_compactor.py
```

建议类结构：

```python
class LegacyFullCompactor:
    def __init__(
        self,
        config: AppConfig,
        llm_client: OpenAICompatibleClient | None,
        estimator: TokenEstimator | None = None,
    ) -> None:
        ...

    def compact_if_needed(
        self,
        *,
        bug_id: str,
        messages: list[dict[str, Any]],
        session: dict[str, Any],
        rebuilt_system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> LegacyCompactionResult:
        ...

    def summarize_conversation(
        self,
        *,
        messages: list[dict[str, Any]],
        bug_id: str,
    ) -> str:
        ...

    def group_messages_by_api_round(
        self,
        messages: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        ...

    def truncate_head_for_ptl_retry(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        ...

    def select_recent_rounds(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...

    def restore_recent_files(
        self,
        session: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ...
```

## 12. 完整执行流程

```text
Step 1: 计算当前上下文 token
  |
  |-- 未达到自动压缩阈值
  |     -> 返回 below_threshold
  |
  |-- 达到自动压缩阈值
        |
        v
Step 2: 检查熔断器
  |
  |-- 连续失败次数 >= 3
  |     -> 根据阻断阈值决定继续或停止
  |
  |-- 允许尝试压缩
        |
        v
Step 3: 调用无工具 LLM 生成摘要
  |
  |-- 成功
  |     -> 进入 Step 4
  |
  |-- Prompt Too Long
  |     -> 持久化 transcript，丢弃最旧完整轮次并携带恢复路径重试，最多 3 次
  |
  |-- 其他错误
        -> 记录失败并根据阻断阈值决定继续或停止
        |
        v
Step 4: 恢复必要上下文
  |
  |-- 生成精简 session 状态
  |-- 原样保留最近完整 API 轮次
  |-- 重新读取最近使用的源码文件
        |
        v
Step 5: 重建 llm_messages
  |
  |-- system prompt
  |-- compact boundary 用户消息
  |-- 最近完整 API 轮次
        |
        v
Step 6: 持久化
  |
  |-- 写入 compact-N.md
  |-- 更新 session["legacy_compaction_state"]
  |-- 更新 session["last_legacy_compaction"]
        |
        v
Step 7: 使用新上下文继续主模型调用
```

## 13. 摘要提示词

摘要调用必须禁用工具：

```python
response = self.llm_client.chat(
    summary_messages,
    tools=None,
    max_tokens=self.config.compact.summary_max_output_tokens,
)
```

建议摘要提示词：

```text
你正在压缩一个 Java 自动修复 Agent 的历史上下文。

目标：生成一份可以让主修复 Agent 直接继续工作的结构化摘要。

要求：
1. 不要调用工具。
2. 不要输出思考过程。
3. 只输出 Markdown 摘要。
4. 保留准确的文件路径、类名、方法名和行号。
5. 保留已经执行过的工具调用及其关键结果。
6. 保留失败补丁、编译错误、测试错误和回滚原因。
7. 保留权限拒绝、业务约束和人工反馈。
8. 明确当前修复状态和下一步操作。
9. 不要泄露密钥、Token、Webhook 或其他敏感信息。

输出结构：

# 缺陷与修复目标
# 已确认的代码事实
# 已读取的文件与符号
# 已尝试的修改
# 编译、测试与回滚
# 权限拒绝与约束
# 当前状态
# 下一步
```

摘要请求直接使用原始 `llm_messages`，末尾追加上述用户消息。这样模型可以看到真实的工具调用链。

摘要覆盖完整历史，包括稍后仍会原样保留的最近轮次。这会产生少量信息重复，但可以保证：如果重建后的上下文仍然过大，需要进一步减少最旧的保留轮次，被减少的内容仍然存在于摘要中。

## 14. 压缩后的消息格式

成功压缩后，完整历史使用摘要承载，同时最近 `keep_recent_rounds` 个 API 轮次额外原样保留：

```python
[
    {
        "role": "system",
        "content": rebuilt_system_prompt,
    },
    {
        "role": "user",
        "content": json.dumps(
            {
                "type": "legacy_full_compact_boundary",
                "summary": summary,
                "current_state": slim_session_state,
                "restored_files": restored_files,
                "transcript_path": transcript_path,
                "instruction": (
                    "直接继续修复任务。需要补充证据时调用工具。"
                    "不要复述摘要，不要向用户提问。"
                ),
            },
            ensure_ascii=False,
        ),
    },
    *recent_round_messages,
]
```

这里的 `recent_round_messages` 不能简单使用 `messages[-N:]`。必须按完整 API 轮次选择，例如：

```text
assistant(tool_calls=[call_x])
tool(tool_call_id=call_x)
```

如果最近一轮是普通文本消息，也应作为一个完整轮次保留。

这样设计的好处：

- 模型可以直接看到最近几次调查、修改和工具结果，不必完全依赖摘要。
- 不会留下孤立的 `tool` 消息。
- 不需要在压缩后的活动上下文中修复工具配对。
- 能显著降低 token 使用量。
- 审计历史仍然保留在 `history` 和 `session["tool_calls"]` 中。

## 15. 最近轮次保留策略

保留单位必须是 API 轮次，而不是单条消息。

建议算法：

```text
1. 永远保留 system prompt。
2. 将 system prompt 之后的消息按 API 轮次分组。
3. 默认选取最后 keep_recent_rounds 个完整轮次。
4. assistant(tool_calls) 与对应 tool(result) 必须一起保留。
5. 普通 user 或 assistant 文本消息并入相邻轮次，避免丢失反馈。
6. 如果压缩后的上下文仍超过主调用阻断阈值，从最旧的保留轮次开始减少。
7. 至少保留最后一个完整轮次；仍然超限时阻断主模型调用。
```

默认建议：

```yaml
keep_recent_rounds: 8
```

这是面向 1M 上下文窗口的保守起点。项目当前要求每轮只调用一个工具，八轮通常足以保留最近的搜索、读取、编辑和验证过程。

## 16. Prompt Session 瘦身

当前 `build_prompt_template()` 会将接近完整的 `session` 注入系统提示词。任务恢复后，`session["tool_calls"]` 可能再次进入 prompt，造成重复膨胀。

应增加：

```python
def _build_slim_session_view(self, session: dict[str, Any]) -> dict[str, Any]:
    ...
```

只保留：

```python
{
    "status": session.get("status"),
    "last_error": summarize_tool_result(session.get("last_error")),
    "last_tool_result": summarize_tool_result(session.get("last_tool_result")),
    "compile_result": summarize_tool_result(session.get("compile_result")),
    "test_result": summarize_tool_result(session.get("test_result")),
    "rollback_result": summarize_tool_result(session.get("rollback_result")),
    "pr_url": session.get("pr_url"),
    "denied_commands": tail(session.get("denied_commands"), 3),
    "legacy_compaction_state": session.get("legacy_compaction_state"),
}
```

不要注入：

- 完整 `session["tool_calls"]`。
- 飞书通知 payload。
- 大段标准输出和错误输出。
- 完整 traceback。
- 历史 LLM 调用记录。
- 完整历史事件列表。

## 17. 最近源码恢复

摘要可能丢失精确代码，因此压缩后应重新注入最近读取的源码。

数据来源：

```python
session["tool_calls"]
```

恢复算法：

```text
1. 倒序遍历 session["tool_calls"]。
2. 只选择 name == "read_code" 且执行成功的记录。
3. 从 arguments.path 或 result.data.path 取文件路径。
4. 去重。
5. 最多恢复 restore_max_files 个文件。
6. 每个文件最多读取 restore_max_chars_per_file 个字符。
7. 所有文件合计不超过 restore_total_chars。
8. 从磁盘重新读取当前内容。
```

必须重新读取磁盘，而不是复用历史 `read_code` 结果。原因是文件可能已经被 `edit_code` 修改。

最近轮次保留和源码恢复并不冲突：

- 最近完整轮次保留模型刚刚做过什么。
- 源码恢复提供磁盘上的最新内容。
- 后续可以优化：如果最近轮次中已经包含某个文件的最新 `read_code` 结果，并且之后没有对该文件执行 `edit_code`，可以跳过重复注入。

建议附件格式：

```python
{
    "path": ".../OrderService.java",
    "content": "...",
    "truncated": False,
}
```

## 18. Prompt Too Long 重试

摘要请求本身也可能超出上下文窗口。此时按完整 API 轮次丢弃最旧消息。

删除最旧轮次前，必须保证当前修复任务的完整 transcript 已经追加写入：

```text
{config.session.root_dir}/{bug_id}/transcript.jsonl
```

transcript 使用 JSON Lines 格式，每行记录一个模型可见消息或 compact 事件。它是只追加文件，不会随着 `llm_messages` 重建而覆盖。后续模型可以通过 `read_code(path, start_line, end_line)` 分段读取旧记录。

当前项目要求主模型每次只调用一个工具，因此一个常见轮次是：

```text
assistant(tool_calls=[call_x])
tool(tool_call_id=call_x)
```

截断规则：

```text
1. 永远保留 system prompt。
2. 将后续消息按 API 轮次分组。
3. assistant(tool_calls) 与对应 tool(result) 必须处于同一组。
4. 每次丢弃最旧约 20% 的轮次，至少丢弃一组。
5. 至少保留一个可总结轮次。
6. 最多重试 max_ptl_retries 次。
```

截断后加入标记：

```text
[earlier conversation truncated for compaction retry]
If you need specific details from before compaction
(like exact code snippets, error messages, or content you generated),
read the full transcript at: <绝对路径>
```

压缩后的 compact boundary 也要保留 `transcript_path`，使主修复模型能够按需恢复早期细节。

不要直接按消息数量切片，否则可能产生：

- 孤立的 `tool` 消息。
- 没有结果的 `assistant(tool_calls)`。
- OpenAI-compatible API 请求校验失败。

## 19. 熔断器

连续压缩失败时，不应在每次循环中重复调用摘要 API。

规则：

```text
连续压缩失败次数 >= max_consecutive_failures
  -> 停止自动压缩尝试
```

后续处理：

| 场景 | 行为 |
| --- | --- |
| 原上下文低于主调用阻断阈值 | 保留原消息，继续主模型调用 |
| 原上下文达到主调用阻断阈值 | 阻断主模型调用，记录错误并走飞书求助 |
| 压缩成功 | 将连续失败计数清零 |

## 20. LLM 客户端修改

修改：

```text
agent/llm/openai_compatible_client.py
```

`chat()` 增加可选参数：

```python
def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> ToolResult:
    ...
```

构建请求时：

```python
if max_tokens is not None:
    kwargs["max_tokens"] = max_tokens
```

成功结果中增加 token 使用量：

```python
data["token_usage"] = token_usage
```

这样压缩器可以记录真实的摘要输入和输出 token。

## 21. RepairAgent 修改

### 21.1 初始化

在 `RepairAgent.__init__()` 中增加：

```python
self.legacy_compactor = LegacyFullCompactor(
    config=config,
    llm_client=llm_client,
)
```

### 21.2 调用主模型前压缩

在 `while True` 中、调用 `_ask_llm()` 之前增加：

```python
compaction = self.legacy_compactor.compact_if_needed(
    bug_id=bug_id,
    messages=llm_messages,
    session=session,
    rebuilt_system_prompt=prompt_template,
    tools=self._openai_tools(),
)

if compaction.compacted:
    llm_messages[:] = compaction.messages
    self._save_session(bug_id, session)

if compaction.blocked:
    last_result = ToolResult(
        tool="legacy_full_compact",
        success=False,
        exit_code=1,
        stderr_summary=compaction.reason,
    )
    self._send_feishu_help(bug_event, session, last_result)
    return RepairRunResult(
        False,
        "failed",
        "context compact failed at blocking threshold",
        task=task,
        last_result=last_result,
        prompt_template=prompt_template,
        history=history,
    )
```

### 21.3 系统提示词瘦身

在 `build_prompt_template()` 中：

```python
prompt_session = self._build_slim_session_view(session)
```

不要再直接复制完整 `session`。

## 22. 持久化格式

每次成功摘要写入：

```text
{config.session.root_dir}/{bug_id}/compactions/compact-{n}.md
```

例如：

```text
data/sessions/BUG-2026-001/compactions/compact-1.md
```

完整 transcript 独立追加写入：

```text
data/sessions/BUG-2026-001/transcript.jsonl
```

`session["legacy_compaction_state"]` 示例：

```json
{
  "compact_count": 1,
  "consecutive_failures": 0,
  "tokens_before": 112340,
  "tokens_after": 14820,
  "last_summary_path": "data/sessions/BUG-2026-001/compactions/compact-1.md",
  "last_transcript_path": "data/sessions/BUG-2026-001/transcript.jsonl",
  "last_error": "",
  "last_compacted_at": "2026-05-30T12:00:00Z"
}
```

## 23. 监控指标

每次压缩至少记录：

```text
bug_id
compact_count
tokens_before
tokens_after
tokens_freed
summary_input_tokens
summary_output_tokens
restored_file_count
dropped_round_count
dropped_message_count
ptl_retry_count
consecutive_failures
summary_path
transcript_path
```

第一版可以写入 `session["last_legacy_compaction"]`，后续再接入统一日志或指标平台。

## 24. 测试计划

新增：

```text
tests/test_legacy_full_compactor.py
```

必须覆盖：

1. 低于阈值时不压缩。
2. 达到阈值时调用摘要 LLM。
3. 摘要调用不携带工具。
4. 摘要成功后使用摘要和最近完整轮次重建活动上下文。
5. 最近完整轮次原样保留。
6. 不会从 `assistant(tool_calls)` 和 `tool(result)` 中间截断。
7. 压缩后不存在孤立 `tool` 消息。
8. `history` 保持完整。
9. `session["tool_calls"]` 保持完整。
10. 最近读取文件按数量限制恢复。
11. 最近读取文件按单文件字符预算截断。
12. 最近读取文件按总字符预算截断。
13. 恢复文件时从磁盘读取最新内容。
14. PTL 重试按完整工具轮次删除。
15. PTL 重试最多执行三次。
16. 连续失败三次后熔断。
17. 熔断后低于阻断阈值时允许继续。
18. 熔断后达到阻断阈值时阻断主模型调用。
19. 压缩状态写入 session。
20. 摘要写入 Markdown 文件。
21. 系统提示词不再包含完整 `session["tool_calls"]`。
22. `OpenAICompatibleClient.chat()` 正确转发 `max_tokens`。

同时更新：

```text
tests/test_repair_agent.py
tests/test_llm_client.py
tests/test_config.py
```

## 25. 实施顺序

### 第一阶段：控制重复膨胀

- 增加 `CompactConfig`。
- 增加 prompt session 瘦身。
- 为 LLM 客户端增加 `max_tokens`。
- 将 token 使用量放入 `ToolResult.data`。

### 第二阶段：实现压缩主路径

- 增加 `TokenEstimator`。
- 增加 `LegacyFullCompactor`。
- 实现摘要提示词。
- 实现压缩后消息重建。
- 实现最近完整 API 轮次保留。
- 接入 `RepairAgent` 主循环。

### 第三阶段：增强稳定性

- 实现最近源码恢复。
- 实现 PTL 截断重试。
- 实现熔断器。
- 增加飞书求助分支。
- 增加摘要持久化和指标。

### 第四阶段：补齐测试

- 增加压缩器单元测试。
- 更新 RepairAgent 测试。
- 更新 LLM 客户端测试。
- 运行完整测试集。

## 26. 后续演进

Legacy Full Compact 稳定后，可以继续评估：

1. Session Memory 增量记忆：提前维护更稳定的长期状态，减少临时摘要成本。
2. Microcompact：优先清理过大的旧工具结果。
3. Reactive Compact：捕获供应商返回的 Prompt Too Long 后自动恢复。
4. 更精确的 tokenizer：根据具体模型计算 token。
5. 摘要质量检查：验证摘要是否包含当前补丁、错误和下一步。

## 27. 最终结论

第一版应优先实现一个边界清晰、行为可验证的 Legacy Full Compact：

```text
主模型调用前检查 token
  -> 超阈值时调用无工具 LLM 生成摘要
  -> 恢复关键状态和最近源码
  -> 原样保留最近完整 API 轮次
  -> 使用“摘要 + 最近轮次”重建 llm_messages
  -> 完整审计记录继续保留
```

这套方案与当前项目结构匹配，改动范围可控，也为后续增加 Session Memory、Microcompact 和 Reactive Compact 留出了空间。
