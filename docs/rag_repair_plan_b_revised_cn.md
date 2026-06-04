# czz-aid RAG 改造方案 B（修订版）：开发任务拆解

## 1. 文档信息

```text
适用分支：feature-compact-se-260531
对应设计：czz-aid RAG 改造方案 A
修订日期：2026-06-04
目标版本：第一阶段修复前知识补充型 RAG
```

本修订版基于当前项目实际代码编写。项目已经具备 Skill、本地文档、飞书文档加载、Markdown 切块、SQLite 向量检索、修复前预取、动态搜索工具和批量索引命令。

因此，本次改造不是从零建设 RAG，而是将当前链路：

```text
纯向量 chunk 召回
  -> 直接注入 retrieved_skills / retrieved_project_docs
```

升级为：

```text
可信知识元数据
  -> 结构化 RepairRagQuery
  -> BM25 + 向量召回
  -> 加权 RRF
  -> Skill 父级聚合
  -> Context Synthesizer
  -> RagRepairContext
  -> RepairAgent
```

## 2. 修订结论

### 2.1 保留的核心设计

第一阶段继续采用以下设计：

```text
1. RAG 在 RepairAgent 正式修复前执行一次。
2. Skill 提供历史修复经验，属于 soft hint。
3. 经确认的业务文档提供业务约束。
4. Skill 使用父子索引，项目文档按 Markdown 标题切分。
5. 召回使用 BM25 + 向量检索 + 加权 RRF。
6. Context Synthesizer 生成结构化 RagRepairContext。
7. RAG 失败不能阻断 RepairAgent。
8. Session 只保存 RagRepairContext 和 RagStatus，不保存粗召回候选。
```

### 2.2 本修订版新增的关键要求

原开发拆解必须补充以下任务：

```text
1. 先补齐 Skill 可信元数据，再开发父子索引。
2. 增加 Query 字段规范化，不能直接把 Java 包名当业务 module。
3. 增加 SQLite schema migration、索引版本和全量重建机制。
4. BM25 使用当前环境已支持的 SQLite FTS5，并与向量索引同事务写入。
5. RRF 分数不能继续使用向量 min_score=0.25 过滤。
6. Context Synthesizer 不允许通过现有 LLM 客户端保存完整候选输入。
7. 只有明确标记为 approved 的业务文档才能成为 hard constraint。
8. 明确现有 search_skill / search_project_doc 动态工具的兼容策略。
9. 明确与 Legacy Full Compact 的集成和回归测试。
10. 增加 Python 版本、配置入口和测试基线前置任务。
```

## 3. 当前代码基线

### 3.1 可直接复用的模块

| 当前模块 | 可复用能力 | 改造方式 |
|---|---|---|
| `agent/rag/models.py` | KnowledgeDocument、KnowledgeChunk、RetrievalResult | 扩展模型 |
| `agent/rag/skill_loader.py` | 扫描 `workspace/skills` | 增加 metadata 校验和旧 Skill 降级 |
| `agent/rag/local_doc_loader.py` | front matter、本地 Markdown | 增加可信等级校验 |
| `agent/rag/feishu_loader.py` | 飞书同步和脱敏 | 保留为后续增强，默认非 hard constraint |
| `agent/rag/chunker.py` | Markdown 标题切分 | 项目文档继续复用 |
| `agent/rag/vector_store.py` | SQLite 文档、chunk、向量检索 | 扩展 schema、FTS5 和父级读取 |
| `agent/rag/knowledge_service.py` | 索引、修复前召回入口 | 改造成 `pre_retrieve_for_bug` |
| `agent/core/repair_agent.py` | 修复前预取和 prompt 注入位置 | 改为只消费 RagRepairContext |
| `agent/llm/openai_compatible_client.py` | JSON response format、max tokens | 增加禁止完整调用日志的选项 |
| `agent/core/legacy_full_compactor.py` | 重建 system prompt | 增加 RAG 上下文保留回归测试 |
| `agent/reflection/*` | review passed/failed Skill 生成 | 扩展 Skill 分类与 metadata |
| `agent/main.py` | RAG 索引和搜索 CLI | 复用命令，移除默认启动全量重建 |

### 3.2 当前已知限制

```text
1. 当前检索只有向量检索，没有 BM25 和融合。
2. fallback embedding 是 64 维 hashing trick，不能作为唯一召回来源。
3. rag_documents 不保存完整 parent 内容。
4. rag_chunks 没有 child_type / section_name。
5. SkillMeta 缺少 skill_type / use_types / exception_type 等字段。
6. Reflection 尚未支持 repair_failed + 人工修复后的总结。
7. 当前 module_name 实际是 Java 类全限定名，不是业务模块名。
8. 当前 top_business_frame 不包含 method_name。
9. 当前 LLM 客户端会持久化完整 messages。
10. 当前程序启动时会自动重建 Skill 和本地文档索引。
11. 当前 `_load_skills` fallback 可能注入与 bug 无关的任意 Skill。
12. 当前配置入口硬编码读取被忽略的 config.example.yaml。
```

## 4. 第一阶段范围

### 4.1 纳入范围

```text
1. Skill 可信 metadata 生成和校验。
2. review_passed / review_failed 二类 Skill。
3. Skill 父子索引。
4. 本地 Markdown 业务文档索引。
5. SQLite FTS5 BM25。
6. 向量召回。
7. 加权 RRF。
8. Query 规范化和 Query Builder。
9. Context Synthesizer。
10. RagRepairContext / RagStatus。
11. RepairAgent 修复前集成。
12. 全链路降级。
13. SQLite schema migration 和索引重建。
14. 与 Legacy Full Compact 的兼容。
```

### 4.2 暂不纳入范围

```text
1. 外部向量数据库。
2. 修复失败后的自动动态纠偏流程。
3. 项目文档父子索引。
4. 代码自动生成业务文档。
5. 飞书文档作为强依赖。
6. 完整离线评估平台。
7. Cross-encoder reranker。
8. 自动解决源码与业务文档冲突。
```

虽然第一阶段不建设完整评估平台，但必须增加一组小型固定检索用例，防止 BM25、RRF 和 metadata 调整后出现明显回归。

## 5. 前置任务

### 5.1 固定运行环境

项目使用了 `datetime | None` 等 Python 3.10 语法，第一阶段必须明确：

```text
Python >= 3.10
Pydantic >= 2
```

任务：

```text
1. 增加 Python 版本声明。
2. 为开发和 CI 安装 requirements.txt。
3. 先运行当前 RAG、RepairAgent、Reflection 和 Compact 基线测试。
4. 基线测试未通过前，不开始行为改造。
```

### 5.2 修正配置入口

当前 `agent/main.py` 固定读取 `config.example.yaml`，而该文件被 `.gitignore` 忽略。

建议调整为：

```text
1. 提交无密钥的 config.example.yaml。
2. 运行时默认读取 config.yaml。
3. config.yaml 加入 .gitignore。
4. CLI 增加 --config 参数。
5. 测试中显式传入临时配置。
```

## 6. 知识可信模型

### 6.1 SkillMeta 扩展

在 `agent/models.py` 中扩展 `SkillMeta`：

```python
class SkillMeta(BaseModel):
    name: str
    version: str = "1.0.0"
    schema_version: int = 2
    description: str = ""
    source_bug_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: str = ""
    module: str = ""
    exception_type: str = ""
    top_business_frame: str = ""
    class_name: str = ""
    method_name: str = ""
    root_cause_type: str = ""
    fix_pattern: str = ""

    skill_type: str = "legacy_unclassified"
    use_types: list[str] = Field(default_factory=list)
    has_human_diff: bool = False
    has_agent_diff: bool = False
```

枚举语义：

```text
skill_type:
  review_passed
  review_failed
  legacy_unclassified

use_types:
  recommended_fix
  human_fix_hint
  avoid_pattern
  debug_hint
  validation_hint
```

默认映射：

| Skill 类型 | use_types |
|---|---|
| `review_passed` | `recommended_fix`, `validation_hint` |
| `review_failed` | `human_fix_hint`, `avoid_pattern`, `validation_hint` |
| `legacy_unclassified` | `debug_hint` |

未分类的旧 Skill 不能自动成为 `recommended_fix`。

### 6.2 Reflection 改造

修改：

```text
agent/reflection/reflection_subagent.py
agent/reflection/skill_generator.py
agent/models.py
```

任务：

```text
1. review_passed 生成 skill_type=review_passed。
2. review_failed 生成 skill_type=review_failed，并记录 agent/human diff 状态。
3. Reflection prompt 输出 root_cause_type 和 fix_pattern。
4. SkillGenerator 将结构化 metadata 写入 skill.meta.json。
5. 缺少必要 metadata 时生成 legacy_unclassified，而不是猜测为成功经验。
```

### 6.3 业务文档可信等级

业务文档 front matter 增加：

```yaml
---
project: mall-service
module: order
doc_type: api_doc
authority: approved
version: v1
effective_at: 2026-06-03
updated_at: 2026-06-03
owner: order-team
---
```

可信等级：

```text
approved:
  可以进入 hard_constraints。

draft:
  只能进入 soft_hints 或 missing_info。

inferred:
  自动推断或飞书目录推断得到，只能作为 soft_hints。

unclassified:
  旧文档或 metadata 不完整，只能作为 soft_hints。
```

规则：

```text
1. 只有 authority=approved 的业务文档可以成为 hard constraint。
2. 本地文档缺少 authority 时默认为 unclassified。
3. 飞书文档默认 authority=inferred。
4. 文档冲突时保留来源、版本和更新时间，不自动决定真实业务规则。
```

## 7. 数据模型

修改 `agent/rag/models.py`。

### 7.1 KnowledgeDocument

继续保留当前字段。完整正文必须持久化到数据库，作为 Skill parent 内容。

### 7.2 KnowledgeChunk

使用现有 `doc_id` 作为 Skill 的 parent id，不新增重复的数据库 `parent_id` 列。

新增：

```python
class KnowledgeChunk(BaseModel):
    chunk_id: str
    doc_id: str
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    heading_path: list[str] = Field(default_factory=list)
    child_type: str = ""
    section_name: str = ""
    content: str
    token_count: int = 0
    content_hash: str
    embedding: list[float] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str
```

`child_type`：

```text
signal_chunk
fix_chunk
validation_chunk
document_chunk
```

### 7.3 RetrievalResult

```python
class RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    parent_id: str = ""
    source: str
    doc_type: str
    project: str
    module: str = ""
    title: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_sources: list[str] = Field(default_factory=list)
    ranks: dict[str, int] = Field(default_factory=dict)
```

规则：

```text
Skill result: parent_id = doc_id
Project document result: parent_id = ""
```

### 7.4 RepairRagQuery

```python
class RepairRagQuery(BaseModel):
    project: str
    module_candidates: list[str] = Field(default_factory=list)
    exception_type: str = ""
    message: str = ""
    class_name: str = ""
    method_name: str = ""
    package_name: str = ""
    top_business_frame: str = ""
    request_path: str = ""
    repair_stage: str = "before_edit"
    root_cause_hint: str = ""

    skill_bm25_query: str = ""
    skill_vector_query: str = ""
    project_doc_bm25_query: str = ""
    project_doc_vector_query: str = ""
```

### 7.5 RagRepairContext

避免使用完全无约束的 `dict`，增加来源引用模型：

```python
class KnowledgeReference(BaseModel):
    source: str
    source_type: str
    doc_id: str
    chunk_id: str = ""
    parent_id: str = ""
    title: str = ""
    uri: str = ""


class RagContextItem(BaseModel):
    text: str
    source: KnowledgeReference
    confidence: str = "medium"
    use_type: str = ""


class RagRepairContext(BaseModel):
    query_summary: str = ""
    hard_constraints: list[RagContextItem] = Field(default_factory=list)
    soft_hints: list[RagContextItem] = Field(default_factory=list)
    avoid_patterns: list[RagContextItem] = Field(default_factory=list)
    validation_hints: list[RagContextItem] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    selected_sources: list[KnowledgeReference] = Field(default_factory=list)
    confidence: str = "low"
    missing_info: list[str] = Field(default_factory=list)
```

### 7.6 RagStatus

一次预取可能同时发生多个降级，状态模型使用列表：

```python
class RagStatus(BaseModel):
    status: str = "success"
    degraded_stages: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    fallback_strategies: list[str] = Field(default_factory=list)
```

状态：

```text
success
degraded
failed
disabled
```

## 8. SQLite Schema 和迁移

当前 `_ensure_schema()` 只执行 `CREATE TABLE IF NOT EXISTS`，不能为已有表增加字段。必须新增显式迁移。

### 8.1 Schema 变更

`rag_documents` 新增：

```sql
content TEXT NOT NULL DEFAULT ''
```

`rag_chunks` 新增：

```sql
child_type TEXT NOT NULL DEFAULT ''
section_name TEXT NOT NULL DEFAULT ''
```

新增索引元数据表：

```sql
CREATE TABLE IF NOT EXISTS rag_index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

至少记录：

```text
schema_version
embedding_provider
embedding_model
embedding_dimension
last_full_rebuild_at
```

### 8.2 FTS5 表

BM25 使用 SQLite FTS5：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    project UNINDEXED,
    doc_type UNINDEXED,
    module UNINDEXED,
    title,
    heading_path,
    content,
    tokenize = 'unicode61 tokenchars ''._:-/$'''
);
```

### 8.3 迁移任务

新增：

```text
agent/rag/migrations.py
```

任务：

```text
1. 使用 PRAGMA table_info 检查现有列。
2. 使用 ALTER TABLE 补充普通列。
3. 创建或重建 FTS5 表。
4. 检测 embedding provider/model/dimension 变化。
5. embedding 维度变化时要求全量重建，禁止按最短长度计算 cosine。
6. 增加 rag-rebuild-index 命令。
7. 为旧索引补充 schema_version。
```

当前向量余弦实现使用较短向量长度进行计算。改造后，维度不一致必须跳过结果并记录降级，不能静默计算。

### 8.4 SQLite 可靠性

初始化连接时建议增加：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

文档、向量 chunk 和 FTS chunk 必须在同一个事务中写入，避免双索引不一致。

## 9. SkillChunker

新增：

```text
agent/rag/skill_chunker.py
```

接口：

```python
class SkillChunker:
    def chunk(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        ...
```

### 9.1 解析方式

当前 SkillGenerator 已生成稳定标题：

```text
适用场景
典型信号
根因判断
本次有效步骤
本次多余步骤
遗漏点与错误
人工修复关键点
推荐排查步骤
避免事项
验证方式
```

SkillChunker 应按标题解析，不使用脆弱的全文正则猜测。

### 9.2 Child 映射

```text
signal_chunk:
  Skill metadata
  适用场景
  典型信号

fix_chunk:
  根因判断
  本次有效步骤
  人工修复关键点
  推荐排查步骤

validation_chunk:
  本次多余步骤
  遗漏点与错误
  避免事项
  验证方式
```

每个 child 必须带完整 Skill metadata。

### 9.3 旧 Skill 降级

旧 Skill 标题不完整或 metadata 不完整时：

```text
1. 仍可生成 child chunk。
2. skill_type=legacy_unclassified。
3. use_types 只能包含 debug_hint。
4. 不得进入 recommended_fix。
```

## 10. 项目文档切分

继续复用 `MarkdownChunker`，第一阶段不开发项目文档父子索引。

增强任务：

```text
1. chunk 内容前增加 title 和 heading_path，提升 BM25 和 embedding 命中率。
2. 每个 chunk 复制 authority/version/effective_at/owner metadata。
3. 对无 front matter 文档标记 authority=unclassified。
4. 根据 doc_type 保留现有标题切分语义。
5. 增加单文档最大 chunk 数量告警。
```

## 11. 入库流程

复用当前 CLI：

```text
python -m agent.main rag-index-skills
python -m agent.main rag-index-docs
python -m agent.main rag-sync-feishu
```

新增：

```text
python -m agent.main rag-rebuild-index
python -m agent.main rag-index-status
```

### 11.1 Skill 入库

```text
1. 扫描 workspace/skills。
2. 读取 SKILL.md 和 skill.meta.json。
3. 校验并补充 metadata。
4. 旧 Skill 标记为 legacy_unclassified。
5. 根据 content_hash 跳过未变化内容。
6. SkillChunker 生成 child chunks。
7. 生成 embedding。
8. 同事务写入 rag_documents / rag_chunks / rag_chunks_fts。
9. 删除源文件已经不存在的旧索引。
```

### 11.2 项目文档入库

```text
1. 扫描 workspace/docs。
2. 读取 front matter。
3. 校验 authority 和 metadata。
4. MarkdownChunker 切分。
5. 根据 content_hash 跳过未变化内容。
6. 生成 embedding。
7. 同事务写入向量和 FTS5 索引。
8. 删除源文件已经不存在的旧索引。
```

### 11.3 启动策略

当前程序在每次启动时自动索引 Skill 和本地文档。第一阶段改为：

```yaml
rag:
  auto_index_on_startup: false
```

默认通过显式 CLI 或定时任务批量入库。启动索引只作为开发环境可选能力，失败不能阻断 RepairAgent。

## 12. Query 规范化和 Query Builder

新增：

```text
agent/rag/repair_context_resolver.py
agent/rag/repair_query_builder.py
```

### 12.1 RepairContextResolver

职责：

```text
1. 从 BugEvent.frames 选择首个业务 frame。
2. 提取 class_name / method_name / package_name。
3. 从 filePath、package 和配置映射生成 module_candidates。
4. 读取 request_path。
5. 从 frame_contexts.symbol 提取有限的符号名。
6. 保留原始 top_business_frame，避免丢失证据。
```

注意：

```text
当前 StackFrame.module_name 是 Java 类全限定名，不是业务 module。
当前 top_business_frame 不包含方法名。
当前 frame_contexts 不包含 module 字段。
```

因此第一阶段 module 只作为排序 boost，不能默认作为硬过滤。

允许增加配置映射：

```yaml
rag:
  module_aliases:
    "com.example.order": order
    "com.example.payment": payment
```

### 12.2 RepairQueryBuilder

规则：

```text
1. BM25 query 保留异常类名、Java symbol、API path 和错误码。
2. Vector query 使用自然语言描述和有限结构化上下文。
3. root_cause_hint 第一阶段仅允许规则生成或为空，不调用额外 LLM 猜测。
4. Skill 和项目文档使用不同 query。
5. 不把完整 frame_contexts.code 放入 query。
```

## 13. BM25 与 HybridRetriever

新增：

```text
agent/rag/hybrid_retriever.py
```

BM25 能力直接扩展到当前 SQLite store，避免独立 BM25Store 和 VectorStore 写入不一致。

### 13.1 召回流程

```text
Skill:
  signal/fix/validation child BM25 recall
  + child vector recall
  -> RRF
  -> 按 doc_id 聚合 parent
  -> parent top-k

Project docs:
  document chunk BM25 recall
  + document chunk vector recall
  -> RRF
  -> per-doc cap
  -> docs top-k
```

### 13.2 加权 RRF

```python
score = bm25_weight / (rrf_k + bm25_rank) \
      + vector_weight / (rrf_k + vector_rank)
```

规则：

```text
1. rank 从 1 开始。
2. 缺少某一路召回时，该路不加分。
3. RRF 只按排名融合，不直接融合原始 BM25/cosine 分数。
4. vector_min_score 只作用于向量召回。
5. RRF 结果不能使用 min_score=0.25 过滤。
```

### 13.3 Skill Parent 聚合

同一个 Skill 可能命中多个 child。为避免长 Skill 因 child 数量多而天然占优：

```text
1. parent 主分数使用最高 child RRF 分数。
2. 可为第二个不同 child_type 提供小幅固定 boost。
3. boost 必须设置上限。
4. 记录 matched_child_types 和 matched_chunk_ids。
5. Synthesizer 输入包含完整 parent 和命中的关键 child 引用。
```

### 13.4 检索降级

```text
向量失败 -> BM25 only
BM25/FTS5 失败 -> vector only
两者失败 -> 空 candidates
```

如果运行环境不支持 FTS5，允许使用简单 term-overlap scorer 作为 BM25 降级，但必须记录 RagStatus。

## 14. Context Synthesizer

新增：

```text
agent/rag/context_synthesizer.py
```

### 14.1 职责

```text
1. 对 TopN candidates 去噪。
2. 识别 approved 业务文档中的 hard constraints。
3. 根据 Skill use_types 分类 soft_hints / avoid_patterns / validation_hints。
4. 识别候选之间的显式冲突。
5. 输出带来源引用的 RagRepairContext。
6. 不生成 patch。
```

### 14.2 LLM 客户端改造

当前 `OpenAICompatibleClient` 会保存完整 messages。必须增加：

```python
def chat(
    ...,
    persist_call_record: bool = True,
) -> ToolResult:
    ...
```

Context Synthesizer 调用必须使用：

```python
persist_call_record=False
```

允许记录：

```text
模型名
耗时
token usage
成功/失败
候选数量
最终 selected_sources 数量
```

禁止记录：

```text
完整 candidates
完整 Synthesizer prompt
完整原始 chunk 内容
```

### 14.3 配置继承

Synthesizer 可以独立配置，也可以显式继承 RepairAgent 主模型：

```yaml
context_synthesizer:
  enabled: true
  inherit_main_llm: true
  provider: ""
  base_url: ""
  api_key: ""
  model: ""
  timeout_seconds: 60
  max_output_tokens: 4000
```

不得通过修改全局 `config.llm` 临时切换模型。

### 14.4 输出解析

```text
1. 优先使用 response_format=json_object。
2. 使用 Pydantic 校验 RagRepairContext。
3. 允许一次 JSON 修复重试。
4. 解析失败后直接使用 deterministic fallback。
5. selected_sources 必须来自输入候选，禁止编造来源。
6. 非 approved 文档不能进入 hard_constraints。
7. review_failed / repair_failed / legacy_unclassified 不能进入 recommended_fix。
```

### 14.5 Deterministic Fallback

Synthesizer 失败时，不把原始 TopN 直接塞入 RepairAgent。

使用规则生成简化上下文：

```text
approved project docs -> hard_constraints
draft/inferred/unclassified docs -> soft_hints
review_passed Skill -> soft_hints + validation_hints
review_failed Skill -> avoid_patterns + validation_hints
repair_failed Skill -> avoid_patterns + soft debug hints
legacy_unclassified Skill -> low-confidence debug hints
```

## 15. KnowledgeService 改造

修改：

```text
agent/rag/knowledge_service.py
```

新增：

```python
def pre_retrieve_for_bug(
    self,
    bug_event: BugEvent,
    session: dict[str, Any],
) -> tuple[RagRepairContext, RagStatus]:
    ...
```

流程：

```text
1. RepairContextResolver 生成规范化上下文。
2. RepairQueryBuilder 生成 RepairRagQuery。
3. HybridRetriever 检索 Skill。
4. HybridRetriever 检索项目文档。
5. 合并并限制候选内容预算。
6. ContextSynthesizer 生成 RagRepairContext。
7. 返回 RagRepairContext + RagStatus。
```

要求：

```text
1. 每个阶段独立 try/except。
2. 任意阶段失败均不抛出到 RepairAgent。
3. 不使用当前 `_load_skills` 注入任意 Skill 的 fallback。
4. 所有错误信息必须脱敏并限制长度。
5. 粗召回 candidates 不写入 session。
```

## 16. RepairAgent 集成

修改：

```text
agent/core/repair_agent.py
```

### 16.1 调用位置

在 `repair()` 中：

```text
load bug_event
load session / frame_contexts
  -> knowledge_service.pre_retrieve_for_bug()
  -> session["rag_context"]
  -> session["rag_status"]
  -> build_prompt_template()
```

### 16.2 Prompt

移除：

```text
retrieved_skills
retrieved_project_docs
```

替换为：

```json
{
  "rag_context": {
    "hard_constraints": [],
    "soft_hints": [],
    "avoid_patterns": [],
    "validation_hints": [],
    "conflicts": [],
    "confidence": "low",
    "missing_info": []
  }
}
```

新增规则：

```text
1. 当前源码事实优先级最高。
2. hard_constraints 仅作为 approved 业务约束。
3. soft_hints 是经验，不是当前源码事实。
4. edit_code 前必须 read_code 验证。
5. avoid_patterns 不得转换为推荐 patch。
6. confidence=low 时仅作为弱提示。
7. conflicts 非空时在修复说明中保留冲突，并等待人工审核。
```

### 16.3 与 Legacy Full Compact 集成

当前 Compact 会使用 `prompt_template` 重建 system prompt，因此 RagRepairContext 应保留在重建后的 system prompt 中。

必须增加回归测试：

```text
1. compact 前 system prompt 包含 rag_context。
2. compact 后重建的 system prompt 仍包含相同 rag_context。
3. 粗召回 candidates 不进入 transcript。
4. session 中只保存 rag_context / rag_status。
```

## 17. 动态搜索工具策略

当前 RepairAgent 已注册：

```text
search_skill
search_project_doc
```

为了与“第一阶段不做多轮 Agentic RAG”保持一致：

```yaml
rag:
  dynamic_tools_enabled: false
```

策略：

```text
1. 默认关闭动态 RAG 工具，不删除现有实现。
2. 关闭时不注册工具，也不在 prompt 中鼓励调用。
3. 开启时工具内部复用 HybridRetriever，但不调用 Context Synthesizer。
4. 动态工具不属于第一阶段核心验收标准。
```

## 18. Session 和日志策略

Session 只保存：

```text
session["rag_context"]
session["rag_status"]
```

不保存：

```text
RepairRagQuery 全文
完整粗召回结果
完整 candidates
完整 Synthesizer 输入
全部原始 chunk
```

允许保存的运行统计：

```text
candidate_count
selected_source_count
retrieval_latency_ms
synthesizer_latency_ms
degraded_stages
```

这些统计应放入 `rag_status`，不得包含原始知识正文。

## 19. 配置设计

修改 `agent/config.py`：

```python
class RagRetrievalConfig(BaseModel):
    bm25_weight: float = 1.2
    vector_weight: float = 1.0
    rrf_k: int = 60
    vector_min_score: float = 0.25
    skill_child_top_k: int = 20
    parent_skill_top_k: int = 3
    project_doc_recall_top_k: int = 20
    project_doc_final_top_k: int = 5
    candidate_top_n: int = 15
    per_doc_chunk_cap: int = 2


class RagContextSynthesizerConfig(BaseModel):
    enabled: bool = True
    inherit_main_llm: bool = True
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 60
    max_output_tokens: int = 4000


class RagConfig(BaseModel):
    enabled: bool = True
    backend: str = "sqlite"
    db_path: str = "./data/sessions/agent.db"
    auto_index_on_startup: bool = False
    dynamic_tools_enabled: bool = False
    debug_candidate_logging: bool = False

    embedding_provider: str = "fallback"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""

    module_aliases: dict[str, str] = Field(default_factory=dict)
    retrieval: RagRetrievalConfig = Field(default_factory=RagRetrievalConfig)
    context_synthesizer: RagContextSynthesizerConfig = Field(
        default_factory=RagContextSynthesizerConfig
    )
```

示例：

```yaml
rag:
  enabled: true
  backend: sqlite
  db_path: ./data/sessions/agent.db
  auto_index_on_startup: false
  dynamic_tools_enabled: false
  debug_candidate_logging: false

  embedding_provider: fallback
  embedding_base_url: ""
  embedding_api_key: ""
  embedding_model: ""

  module_aliases:
    "com.example.order": order

  retrieval:
    bm25_weight: 1.2
    vector_weight: 1.0
    rrf_k: 60
    vector_min_score: 0.25
    skill_child_top_k: 20
    parent_skill_top_k: 3
    project_doc_recall_top_k: 20
    project_doc_final_top_k: 5
    candidate_top_n: 15
    per_doc_chunk_cap: 2

  context_synthesizer:
    enabled: true
    inherit_main_llm: true
    provider: ""
    base_url: ""
    api_key: ""
    model: ""
    timeout_seconds: 60
    max_output_tokens: 4000
```

## 20. 降级矩阵

| 失败阶段 | 降级策略 | RagStatus |
|---|---|---|
| Skill metadata 缺失 | 标记 legacy_unclassified | degraded |
| 文档 authority 缺失 | 降为 soft hint | degraded |
| embedding 失败 | BM25 only | degraded |
| BM25/FTS5 失败 | vector only | degraded |
| 两路召回失败 | 空 candidates | failed |
| parent 读取失败 | 使用命中的 child | degraded |
| Synthesizer 未配置 | deterministic fallback | degraded |
| Synthesizer 超时/输出非法 | deterministic fallback | degraded |
| RAG 全部失败 | 空 RagRepairContext | failed |
| RAG disabled | 空 RagRepairContext | disabled |

无论何种 RAG 状态，RepairAgent 主流程都必须继续。

## 21. 测试计划

### 21.1 Phase 0 基线

```text
1. Python 3.10+ 环境可运行。
2. 当前 RAG、RepairAgent、Reflection、Compact 测试通过。
3. 当前 SQLite 数据库可以完成迁移备份和重建。
```

### 21.2 Knowledge 和 Skill

```text
test_reflection_writes_review_passed_metadata
test_reflection_writes_review_failed_metadata
test_reflection_writes_repair_failed_metadata
test_legacy_skill_is_not_recommended_fix
test_skill_chunker_builds_signal_fix_validation_chunks
test_skill_chunker_preserves_source_metadata
test_unapproved_doc_is_not_hard_constraint
```

### 21.3 SQLite 和入库

```text
test_rag_schema_migrates_existing_database
test_rag_rebuild_recreates_fts_and_vectors
test_upsert_updates_vector_and_fts_in_one_transaction
test_deleted_source_removes_stale_index
test_unchanged_document_skips_embedding
test_embedding_dimension_mismatch_requires_rebuild
test_fts5_fallback_is_recorded
```

### 21.4 Query 和检索

```text
test_context_resolver_extracts_class_and_method
test_context_resolver_does_not_treat_fqcn_as_business_module
test_query_builder_keeps_java_symbols_for_bm25
test_bm25_matches_exception_and_method_name
test_hybrid_retriever_merges_bm25_and_vector
test_weighted_rrf_uses_rank_not_raw_score
test_rrf_result_is_not_filtered_by_vector_min_score
test_skill_parent_aggregation_does_not_reward_chunk_count
test_project_doc_per_doc_cap
```

### 21.5 Synthesizer 和降级

```text
test_synthesizer_does_not_generate_patch
test_synthesizer_rejects_unknown_sources
test_synthesizer_only_uses_approved_docs_as_hard_constraints
test_synthesizer_does_not_persist_candidate_messages
test_synthesizer_invalid_json_uses_deterministic_fallback
test_vector_failure_uses_bm25_only
test_bm25_failure_uses_vector_only
test_total_rag_failure_returns_empty_context
```

### 21.6 RepairAgent 和 Compact

```text
test_repair_agent_saves_only_rag_context_and_status
test_repair_agent_prompt_contains_rag_context
test_rag_failure_does_not_block_repair
test_dynamic_rag_tools_disabled_by_default
test_legacy_compact_preserves_rag_context
test_candidates_do_not_enter_compact_transcript
```

### 21.7 小型固定验收集

不建设完整指标平台，但增加 8 到 15 个固定 case：

```text
1. 精确异常类型命中。
2. Java 类名/方法名命中。
3. API path 命中。
4. 错误码命中。
5. review_passed Skill 分类。
6. review_failed Skill 分类。
7. approved 文档成为 hard constraint。
8. draft 文档不成为 hard constraint。
9. Skill 与业务文档冲突。
10. 无结果时返回低置信空上下文。
```

## 22. 修订后的开发顺序

### Phase 0：环境和基线

```text
1. 固定 Python >= 3.10。
2. 修正配置入口和 --config。
3. 安装依赖并运行基线测试。
4. 备份现有 SQLite 数据库。
```

验收：

```text
当前测试可稳定运行，工作区和配置行为明确。
```

### Phase 1：知识可信模型和 Reflection

```text
1. 扩展 SkillMeta。
2. 增加文档 authority。
3. 改造 review_passed / review_failed Skill 生成。
4. 增加旧 Skill 降级规则。
```

验收：

```text
每个新 Skill 都可以可靠判断其用途和可信类别。
```

### Phase 2：Schema migration 和入库

```text
1. 增加 rag_documents.content。
2. 增加 rag_chunks.child_type / section_name。
3. 增加 rag_index_meta。
4. 增加 FTS5 表。
5. 实现 SkillChunker。
6. 实现增量索引、删除清理和全量重建。
7. 默认关闭启动自动索引。
```

验收：

```text
旧数据库可迁移，Skill 父子索引和项目文档双索引可稳定构建。
```

### Phase 3：Query 和混合召回

```text
1. 实现 RepairContextResolver。
2. 实现 RepairQueryBuilder。
3. 实现 BM25 查询。
4. 实现 HybridRetriever。
5. 实现加权 RRF。
6. 实现 Skill parent 聚合。
7. 实现召回阶段降级。
```

验收：

```text
精确代码符号和语义相似内容都可召回，单路失败时仍可返回结果。
```

### Phase 4：Context Synthesizer

```text
1. 扩展 LLM 客户端的 persist_call_record 参数。
2. 实现独立或继承式 Synthesizer 配置。
3. 实现 JSON 输出校验。
4. 实现来源白名单校验。
5. 实现 deterministic fallback。
```

验收：

```text
能够生成可追踪的 RagRepairContext，且完整 candidates 不落盘。
```

### Phase 5：RepairAgent 和 Compact 集成

```text
1. 实现 KnowledgeService.pre_retrieve_for_bug。
2. RepairAgent 保存 rag_context / rag_status。
3. Prompt 改为只消费 RagRepairContext。
4. 默认关闭动态 RAG 工具。
5. 验证 Legacy Full Compact 保留 RagRepairContext。
```

验收：

```text
RepairAgent 修复前获得结构化上下文，RAG 或 Compact 失败不影响主流程。
```

### Phase 6：降级、安全和验收

```text
1. 覆盖完整降级矩阵。
2. 增加脱敏和日志体积测试。
3. 增加固定验收 case。
4. 更新 README 和配置模板。
```

验收：

```text
满足第一阶段完整验收标准。
```

## 23. 第一阶段验收标准

```text
1. 可以从 workspace/skills 构建带可信 metadata 的 Skill 父子索引。
2. 可以从 workspace/docs 构建带 authority 的项目文档索引。
3. 已有 SQLite 数据库可以迁移或全量重建。
4. BM25、向量召回和加权 RRF 可以独立测试。
5. QueryBuilder 可以稳定提取 class/method/module candidates。
6. review_failed / repair_failed Skill 不会成为 recommended_fix。
7. 只有 approved 文档会进入 hard_constraints。
8. Context Synthesizer 可以生成带来源引用的 RagRepairContext。
9. Synthesizer 完整候选输入不会被写入 session 或 LLM 调用日志。
10. RepairAgent prompt 只消费 RagRepairContext，不直接消费原始 candidates。
11. Legacy Full Compact 后仍保留 RagRepairContext。
12. 向量、BM25、Synthesizer 或全部 RAG 失败时，RepairAgent 均可继续。
13. Session 只保存 rag_context、rag_status 和无正文运行统计。
14. 动态 RAG 工具默认关闭。
15. 固定验收 case 全部通过。
```

## 24. 主要风险和控制措施

| 风险 | 控制措施 |
|---|---|
| 旧 Skill 缺少分类，被错误作为推荐修复 | 默认 `legacy_unclassified + debug_hint` |
| 业务文档不准确却成为 hard constraint | 只有 `authority=approved` 才可进入 hard constraint |
| module 字段含义不一致导致漏召 | Query 规范化，module 先 boost 后放宽 |
| RRF 分数被向量阈值全部过滤 | 分离 `vector_min_score` 与 RRF 截断 |
| 向量模型变化后旧 embedding 被静默使用 | 记录模型和维度，变化时强制重建 |
| BM25 和向量索引不一致 | 同一 SQLite 事务写入 |
| Synthesizer 编造来源 | Pydantic 校验 + selected_sources 白名单 |
| Synthesizer 泄露完整候选日志 | `persist_call_record=False` |
| 启动自动索引导致修复延迟或失败 | 默认 `auto_index_on_startup=false` |
| RAG 内容在 Compact 后丢失 | 重建 system prompt 回归测试 |
| 动态工具使第一阶段变成多轮 Agentic RAG | 默认 `dynamic_tools_enabled=false` |
| SQLite 并发锁 | WAL + busy_timeout + 短事务 |

## 25. 工作量估算

按一名熟悉当前项目的开发者估算：

| 阶段 | 估算 |
|---|---:|
| Phase 0：环境和基线 | 0.5 - 1 天 |
| Phase 1：可信模型和 Reflection | 2 - 3 天 |
| Phase 2：迁移、FTS5、入库、SkillChunker | 3 - 4 天 |
| Phase 3：Query、Hybrid、RRF、父级聚合 | 2 - 3 天 |
| Phase 4：Context Synthesizer | 2 - 3 天 |
| Phase 5：RepairAgent 和 Compact 集成 | 1 - 2 天 |
| Phase 6：降级、安全、验收 | 2 - 3 天 |

总计：

```text
约 10 - 15 个开发日
```

## 26. 最终开发原则

```text
先保证知识可以被可靠分类，再优化召回。
先保证索引可以迁移和降级，再接入 RepairAgent。
业务文档必须有可信等级，Skill 必须有用途分类。
BM25 和向量检索负责召回，LLM 只负责整理候选。
RAG 是增强链路，任何失败都不能阻断自动修复。
```
