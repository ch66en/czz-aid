# CZZ-AID

## 基于 LLM 的 web线上bug 服务自动急救agent

---

从日志异常到代码修复，全自动闭环。

**核心能力：** 日志监听 → 异常解析 → 智能修复 → 编译验证 → PR 创建 → 飞书审核 → 沉淀skill 

**技术栈：** Python / OpenAI-compatible API / Gitee / 飞书 

---

# 系统架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CZZ-AID                                     │
│                                                                          │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────────────┐     │
│  │ 日志采集  │──▶│ 异常解析  │──▶│ 去重判断  │──▶│    修复代理        │     │
│  │LogWatcher│   │Traceback │   │  Dedup   │   │  RepairAgent      │     │
│  └──────────┘   └──────────┘   └──────────┘   └────────┬──────────┘     │
│                                              ▲         │                │
│                                              │         │                │
│           ┌──────────────────────────────────┐│         │                │
│           │          LLM 服务层               │◀────────┘                │
│           │  主 LLM ◀──故障转移──▶ 备用 LLM   │                          │
│           │  ModelRouter / OpenAICompatible   │                          │
│           └──────────────┬───────────────────┘                          │
│                          │                                              │
│  ┌───────────────────────┴──────────────────────────────────┐           │
│  │                      代码感知层                           │           │
│  │  ┌────────────────┐  ┌────────────────┐  ┌───────────┐  │           │
│  │  │   AST 解析      │  │   代码搜索      │  │  代码读取  │  │           │
│  │  │ ast_symbols    │  │ search_code    │  │ read_code │  │           │
│  │  │ read_symbol_at │  │  (关键字匹配)   │  │ (文件读取) │  │           │
│  │  └────────────────┘  └────────────────┘  └───────────┘  │           │
│  └──────────────────────────────┬───────────────────────────┘           │
│                                 │                                       │
│  ┌──────────┐  ┌───────────────┴─┐  ┌──────────┐  ┌───────────┐       │
│  │ 代码修改  │  │    编译测试      │  │ 回归测试  │  │  创建 PR  │       │
│  │EditCode  │  │ Compile / Test  │  │ TestGen  │  │ Gitee API │       │
│  │ (diff)   │  │                 │  │          │  │           │       │
│  └──────────┘  └─────────────────┘  └──────────┘  └─────┬─────┘       │
│                                                          │             │
│           ┌──────────────────────────────────────────────┴──┐          │
│           │            飞书通知 & 人工审核                    │          │
│           │         ReviewCallbackServer                    │          │
│           │    通过 ──────────────────▶ 拒绝                │          │
│           └───────────────┬──────────────────┬──────────────┘          │
│                           │                  │                         │
│           ┌───────────────┴──────────────────┴──────────────┐          │
│           │              反思引擎 & 技能学习                  │          │
│           │                                                 │          │
│           │  ┌──────────────────┐  ┌────────────────────┐   │          │
│           │  │ ReflectionAgent  │  │    SkillStore      │   │          │
│           │  │  LLM 分析成功/失败 │──▶│  沉淀修复经验      │   │          │
│           │  │  提取改进方向     │   │  指导后续修复      │   │          │
│           │  └──────────────────┘  └─────────┬──────────┘   │          │
│           └──────────────────────────────────│──────────────┘          │
│                                              │                         │
│                                              │  Skill 反馈注入          │
│                                              │  修复代理上下文          │
│                                              ▼                         │
│                                   ┌───────────────────┐                │
│                                   │    修复代理        │                │
│                                   │  RepairAgent      │                │
│                                   │ (下次修复时携带    │                │
│                                   │  历史 Skill)      │                │
│                                   └───────────────────┘                │
└──────────────────────────────────────────────────────────────────────────┘
```

**七大核心模块：**

| 模块 | 职责 |
|------|------|
| 日志采集 | LogWatcher 轮询日志文件，正则匹配 Java 异常栈起始行 + 帧，空闲防抖后提交流水线 |
| 异常解析 | TracebackParser 将原始堆栈解析为结构化数据：exception_type / frames / top_business_frame |
| 去重判断 | DedupEngine 按 SHA256(project + exception_type + message + top_business_frame + request_path) 指纹去重，1 小时窗口 |
| 代码感知 | Pipeline 阶段：AST 解析器对每个 .java 帧提取符号级上下文（symbolId / 行号范围 / 源码 / contentHash），注入 session 供修复代理使用 |
| 修复代理 | RepairAgent 驱动 LLM 工具调用循环（≤3 轮），读代码 → 生成 diff 补丁 → 编译 → 测试 → 失败自动回滚 |
| 飞书审核 | PR 创建后发送飞书卡片通知，人工点击通过/拒绝，回调本地 ReviewCallbackServer |
| 反思学习 | 审核结果触发 ReflectionSubAgent：LLM 分析成败 → 生成 Skill → 存入 SkillStore → 注入后续修复上下文 |

---

# 全流程

## 从异常发现到代码合入，一条链路走通

```
 ┌─────────────── IngestionPipeline ────────────────────────┐
 │                                                          │
 │  LogWatcher        Sanitizer        TracebackParser      │
 │  轮询日志文件  ──▶  脱敏处理    ──▶   解析异常栈           │
 │  正则匹配堆栈       敏感信息mask      exception_type       │
 │  空闲防抖提交                         frames              │
 │                                     top_business_frame   │
 │                                          │               │
 │                              ┌───────────┴───────────┐   │
 │                              │  JavaAstSymbolExtractor │   │
 │                              │  对每个 .java 帧提取（排除非业务帧）    │   │
 │                              │  符号级上下文            │   │
 │                              │  symbolId / 行号 / 源码  │   │
 │                              └───────────┬───────────┘   │
 │                                          │               │
 │                              DedupEngine.is_duplicate    │
 │                              指纹(fingerprint) 去重 (1h窗口)    │
 │                                          │               │
 │                     重复 ──▶ 跳过    首次 ──▶ 触发修复     │
 └──────────────────────────────────────────│───────────────┘
                                            │
 ┌────────────────── RepairAgent ───────────┴───────────────┐
 │                                                          │
 │  加载 BugEvent + frame_contexts + Skills                 │
 │  构建 prompt 模板（含工具列表 + 流程约束）                  │
 │                                                          │
 │  ┌─────────── LLM 工具调用循环（≤3轮）────────────────┐   │
 │  │                                                    │   │
 │  │  LLM 输出 action                                   │   │
 │  │       │                                            │   │
 │  │       ├── read_symbol_at / ast_symbols              │   │
 │  │       │   → 定位故障函数源码                          │   │
 │  │       │                                            │   │
 │  │       ├── search_code                              │   │
 │  │       │   → 项目内关键字搜索                         │   │
 │  │       │                                            │   │
 │  │       ├── edit_code                                │   │
 │  │       │   → 生成 unified diff 补丁                  │   │
 │  │       │   → 校验格式 / 路径 / 大小                   │   │
 │  │       │                                            │   │
 │  │       └── finish_patch                             │   │
 │  │            → 进入编译测试阶段                        │   │
 │  └────────────────────────────────────────────────────┘   │
 │                         │                                 │
 │         ┌───────────────┴───────────────┐                 │
 │         ▼                               ▼                 │
 │    mvn compile                      mvn compile           │
 │    ✅ 通过 → mvn test               ❌ 失败 → 回滚        │
 │    ✅ 通过 → 创建PR + 回归测试       ❌ 失败 → 回滚        │
 │                                                └──▶ 下一轮重试│
 └──────────────────────────────────────────────────────────┘
                           │
                           ▼
                    Gitee API 创建 PR
                           │
                           ▼
                  飞书卡片通知人工审核
                           │
                ┌──────────┴──────────┐
                ▼                     ▼
           人工点击「通过」        人工点击「拒绝」
                │                  填写 human_fix_branch
                │                     │
                ▼                     ▼
        ReflectionSubAgent      拉取两份 diff 对比：
        提取成功修复经验         git diff(base...agent_branch)  → agent_diff
                │               git diff(base...human_fix_branch) → human_diff
                │                     │
                │                     ▼
                │               DiffAnalyzer 分析：
                │               ├── 共同修改了哪些文件
                │               ├── Agent 独改的文件（可能偏离根因）
                │               ├── 人工独改的文件（Agent 漏掉的上下文）
                │               └── 增删行数对比
                │                     │
                │                     ▼
                │               LLM 对比 agent_diff vs human_diff
                │               提取：Agent 修错在哪 / 漏了什么 / 该怎么改
                │                     │
                ▼                     ▼
           生成 Skill              生成 Skill
           (success 类型)          (failure 类型)
           沉淀：做对了什么         沉淀：哪里做错了 + 人工修对了什么
                │                     │
                └──────────┬──────────┘
                           ▼
                      SkillStore 持久化
                           │
                           ▼
                  注入 RepairAgent 上下文
                  (下次修复时携带历史 Skill)
```

---

# 亮点

## 1. 日志智能采集

不只是 tail -f，而是一套完整的异常生命周期管理。

```
日志文件
  │
  ▼
LogWatcher 轮询监听
  ├── 正则匹配 Java 异常栈起始行（XxxException / XxxError）
  ├── 逐行收集帧（at com.xxx.Method(File.java:42)）
  ├── 空闲防抖 1.5s，确保完整堆栈不被截断
  └── 日志文件重写检测（文件被 logrotate 切割时自动重置偏移量）
  │
  ▼
Sanitizer 脱敏
  ├── Token / Cookie / Session ID / Access Key
  ├── password / secret / api_key
  ├── 手机号 / 邮箱
  └── JDBC URL 中的用户名密码
  │
  ▼
TracebackParser 解析
  ├── exception_type（异常类名）
  ├── frames（堆栈帧列表：文件路径 + 行号）
  ├── top_business_frame（首个业务帧，过滤 JDK/Spring 框架帧）
  └── normalized_trace（归一化堆栈文本）
  │
  ▼
DedupEngine 去重
  └── fingerprint = SHA256(project + exception_type + message + top_business_frame + request_path)
      1 小时窗口内相同指纹跳过，避免重复修复
```

**关键价值：** 从原始日志到结构化 BugEvent，全自动完成，零人工介入。

---

## 2. AST 节约 Token

传统方案：读整个文件 → 喂给 LLM → 浪费大量 token，且大文件可能超出上下文窗口。

CZZ-AID 方案：AST 解析只提取符号级信息，精准高效。

```
                  传统方案                          CZZ-AID
              ┌──────────────┐              ┌──────────────────┐
              │  read_code   │              │  ast_symbols     │
              │  读取整个文件  │              │  只提取符号列表    │
              │  可能 2000 行  │              │  类名/方法/行号    │
              │  消耗大量 token│              │  通常 < 100 行    │
              └──────────────┘              └──────────────────┘
```

**ast_symbols(path)** — 提取文件中所有类、方法、字段的符号信息：

```json
{
  "symbolId": "com.example.UserService#findById",
  "kind": "method",
  "startLine": 42,
  "endLine": 58,
  "signature": "public User findById(Long id)"
}
```

- LLM 先看符号列表，决定需要读哪个函数，再精准调用 `read_symbol_at`
- 上下文窗口留给真正重要的：BugEvent + 函数源码 + 修复历史

---

## 3. AST 提前定位报错栈代码，加速修复

传统 LLM Agent 收到异常栈后，需要自己分析 `file:line`，再决定读哪个文件、找哪个函数 — 多一轮 LLM 调用。

CZZ-AID 在 Pipeline 阶段就用 AST **预提取**好部分帧的函数上下文，Agent 开修时已经知道该改哪里。

```
异常堆栈: NullPointerException at com.example.UserService.findById(UserService.java:47)
                                                                      │
                                                                      ▼
                              ┌──────────────────────────────────────────────┐
                              │  Pipeline 阶段（修复前，零 LLM 调用）         │
                              │                                              │
                              │  JavaAstSymbolExtractor.find_symbol_at(      │
                              │      path="UserService.java",                │
                              │      line=47                                 │
                              │  )                                           │
                              │                                              │
                              │  返回：                                       │
                              │  ├── symbolId: UserService#findById          │
                              │  ├── startLine: 42, endLine: 58             │
                              │  ├── code: "public User findById(Long id)…" │
                              │  └── contentHash: "a1b2c3..."               │
                              └──────────────────────────────────────────────┘
                                              │
                                              ▼
                                    注入 session 的 frame_contexts
                                              │
                                              ▼
                              ┌──────────────────────────────────────────────┐
                              │  RepairAgent 启动时已持有：                     │
                              │                                              │
                              │  BugEvent + frame_contexts（含函数源码）       │
                              │                                              │
                              │  LLM prompt 中直接看到故障函数代码，            │
                              │  无需再调 read_symbol_at，第一轮就能输出 patch │
                              └──────────────────────────────────────────────┘
```

**关键价值：**

| 对比项 | 无 AST 预提取 | 有 AST 预提取 |
|--------|-------------|-------------|
| 定位故障函数 | LLM 需 1-2 轮工具调用 | Pipeline 阶段已准备好 |
| LLM 调用次数 | 至少 3 轮（定位→读码→改码） | 2 轮即可（理解→改码） |
| 修复耗时 | 更长 | 更短 |
| token 消耗 | 更多（额外的工具调用往返） | 更少 |

---

## 4. 安全防护

AI Agent 能改代码、执行命令 — 不加约束就是定时炸弹。CZZ-AID 从四个层面锁死。

### 4.1 权限分级

```
PermissionType          允许的操作
─────────────────────────────────────
READ_ONLY               读代码、搜索
WORKSPACE_WRITE         编辑 src/main/java
TEST_EXECUTION          执行白名单命令
VCS_WRITE               Git 操作
EXTERNAL_NOTIFY         飞书通知
```

每个工具声明所需权限，PermissionGuard 在执行前校验。越权直接拒绝。

### 4.2 命令执行权限 — 黑名单 + 白名单 + 人机协同进化

**黑名单（全局禁止，即使命令在白名单中也拦截）：**

| 类别 | 拦截项 |
|------|--------|
| 破坏性命令 | `rm -rf` / `sudo` / `chmod` / `del` / `rmdir` |
| 网络外连 | `curl` / `wget` / `nc` / `ncat` |
| 任意代码执行 | `python -c` / `python3 -c` |
| 管道注入 | `\| bash` / `\| sh` / `\| powershell` |
| 危险 Git | `git clean -fdx` |

**白名单（可执行命令）：** `mvn` / `git` / `python` / `pytest` / `java` / `javac`

**白名单拒绝 → 人机协同进化：**

```
Agent 请求执行 "gradle build"
      │
      ▼
PermissionGuard 检查白名单
      │
      ├── 在白名单中 → 放行
      │
      └── 不在白名单中 → 拒绝
            │
            ├── 记录到 session["denied_commands"]
            │   保存：命令内容 / 拒绝原因 / 工具名 / Bug ID
            │
            └── 飞书通知人工
                "Agent 想执行 gradle build，但不在白名单中"
                        │
                        ▼
                人工判断：
                ├── 合理 → 加入 config.yaml 的 allowed_commands
                │          下次 Agent 就能执行了
                └── 危险 → 忽略，Agent 继续被拦截
```

**价值：** 白名单不是一成不变的。Agent 被拒绝的命令会通过飞书通知人工，人工评估后决定是否放行。安全策略随实际需求自然生长，既不放开也不卡死。

### 4.3 文件写入保护

- **可写范围：** 仅 `src/main/java/**` 和 `src/test/java/**`
- **禁止目录：** `.git` / `.github` / `.gitee`
- **禁止文件：** `.env` / `Dockerfile` / `Jenkinsfile` / `pom.xml` / `build.gradle`
- **禁止后缀：** `.yaml` / `.yml` / `.properties`
- **禁止操作：** 新建文件 / 删除文件
- **补丁限制：** ≤ 3 hunk / +50 行 / -30 行，必须是 unified diff 格式

### 4.4 流程硬约束

- **禁止自动合并 PR** — 只能创建，不能合并
- **人工审核门禁** — `review_required=true` 时 PR 必须经飞书审核
- **编译测试不可跳过** — 任何一步失败都回滚，不提交代码
- **拒绝日志审计** — 被权限拒绝的命令记录到 `denied_commands` 并通知飞书

---

## 5. AI 自动生成回归测试

修复 Bug 只是第一步，防止它再次出现才是关键。CZZ-AID 在每次修复成功后自动触发 TestGenerationAgent，生成覆盖该缺陷的回归测试。

### 触发时机

```
代码修复完成
      ↓
TestGenerationAgent.generate_for_repair()
      ↓
生成测试补丁 → apply_test_patch → 写入 src/test/java/
      ↓
mvn compile → mvn test（测试随修复一起验证）
```

### 生成流程

```
┌─────────────────────────────────────────────────────────────┐
│                TestGenerationAgent                          │
│                                                             │
│  输入：                                                     │
│  ├── BugEvent（异常类型、堆栈、业务帧）                       │
│  ├── 被修改的源码路径 + 源码内容                              │
│  ├── 已有的同名测试文件（UserService.java → 查找             │
│  │   UserServiceTest.java 作为参考）                         │
│  └── 最近的 edit_code 补丁内容                               │
│                                                             │
│  LLM 生成：                                                 │
│  ├── 输出 JSON：{ "path": "...Test.java", "content": "..." }│
│  ├── content 必须是 unified diff 格式                        │
│  ├── 可以新建测试文件，也可以在已有测试中追加方法               │
│  └── 必须包含有意义的断言（不是 assertTrue(true)）            │
│                                                             │
                                  │
└─────────────────────────────────────────────────────────────┘
```

### 安全约束（ApplyTestPatchTool）

| 约束项 | 规则 |
|--------|------|
| 文件位置 | 只能写入 `src/test/java/**` |
| 文件命名 | 必须以 `Test.java` 结尾 |
| 补丁格式 | 必须是 unified diff，可以新建文件（`--- /dev/null`） |
| 弱测试拦截 | 拒绝 `@Disabled` / `@Ignore` / `assertTrue(true)` / `// assert` |
| 断言要求 | 新增的测试方法必须包含 `assert` 或 `verify` |


### 示例

Agent 修复了 `UserService.java` 中的 NPE 后，自动生成：

```java
// src/test/java/com/example/UserServiceTest.java
@Test
void findById_shouldNotReturnNull_whenUserExists() {
    User result = userService.findById(1L);
    assertNotNull(result);
    assertEquals(1L, result.getId());
}

@Test
void findById_shouldThrow_whenIdIsNull() {
    assertThrows(IllegalArgumentException.class, () -> userService.findById(null));
}
```

### 价值

- **修了就有测试** — 不依赖人工补测试，回归保护立即生效
- **测试质量有保障** — 拒绝弱断言、空测试，必须验证实际行为
- **与修复同步验证** — 测试随编译一起跑，确保修复和测试都正确

---

## 6. 人机协同审核闭环

AI 修复 → 飞书审核 → 反思学习 → 越修越准。

```
修复完成 → 创建 PR → 飞书审核卡片
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
            「通过」             「拒绝」+ 人工修复分支
                │                   │
                ▼                   ▼
        提取成功经验          DiffAnalyzer 对比：
        生成 Skill            agent_diff vs human_diff
        (success)             提取失败教训
                              生成 Skill (failure)
                │                   │
                └─────────┬─────────┘
                          ▼
                    SkillStore 持久化
                    + 上传云端共享
                          │
                          ▼
                  注入下次修复上下文
                  (团队成员可直接复用)
```

**关键价值：** 每一次人工审核都在训练 Agent — 通过的沉淀成功模式，拒绝的对比学习正确修法。生成的 Skill 上传至云端共享，团队成员无需重复踩坑，直接复用已有经验。

---

# 总结

这是一个具备完整闭环的强原型，可作为 **Agentic Software Maintenance** 的参考实现。它已具备真实工程系统所需的关键能力：受控工具调用、路径与权限边界、编译测试 gate、人审交付、失败反思与知识沉淀。

后续将持续加强：持久化存储、Review callback 鉴权、平台集成抽象、Skill 启用前安全扫描，以及更深入的 Java AST / 语义级风险检查。目标是从「能用的原型」走向「可落地的生产系统」，让 AI 真正成为团队中可信赖的自动修复工程师。
