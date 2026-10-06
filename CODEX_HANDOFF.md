# 当前接手入口

唯一开发主入口：`judicial-kg current`；实现位于 `src/judicial_kg/current/`。候选版本 `current-rc1`，不是已证明语义质量或GraphRAG收益的生产最终版。

最新开发分支为 `codex/model-alignment`，对齐计划见 [模型对齐](docs/model-alignment-plan.md)。新鲜三篇结果以最新进度为准。

先读 [当前流程与运行命令](docs/current-workflow.md)、[最新进度](docs/progress.md) 和 AGENTS.md。

- 原文先入文本索引；提议、同模型证据复核、程序编译、局部隔离和图谱回放独立留档。
- 契约0.5已接入当前抽取：主体、集合、对象分开；未知成员不虚构；失败只隔离依赖项；原文仍可检索。
- 中文模型接口，程序生成ID与精确跨度。完整称谓允许可逆水平空白映射，不拆单字拼姓名。
- partial不能计作历史整篇成功；有模型判断的no_relevant_content不代表已证明无遗漏。不得声称图谱语义P/R/F1。
- 用户不提供法律专家gold。已审计168条QA仅14种依赖本案的问题，不当全库检索gold。
- 使用原授权百炼Flash及200元累计预算；最新账本见最新进度。Jev暂停，不改Spark服务，不自动扩样。
- 原文、响应、图谱和.env仅在忽略目录；Google Drive上传遵循本聊天授权，Git只保存代码与脱敏报告。
- 历史v1–v44保留重放。旧交接与旧下一步见 [历史交接](docs/history/handoff-through-v44.md)，不是当前执行指令。
