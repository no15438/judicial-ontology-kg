# 当前接手入口

唯一开发主入口：`judicial-kg current`；实现位于 `src/judicial_kg/current/`。候选版本 `current-rc1`，不是已证明语义质量或GraphRAG收益的生产最终版。

最新开发分支为 `codex/source-semantic-guards`。项目定位见 [定位与工程目标](docs/project-positioning.md)，当前M1计划见 [来源语义修复](docs/source-semantic-repair-plan.md)。实测期间独立工作树修复分别在codex/source-scope-stage与codex/proof-scope-regression，两次源码修复已合回主开发分支。最后源码改动15ea8b3、889项测试；所有本轮调用已结束，累计保守134.8362131元/授权200元。不同冻结版本的完整原文与局部窗口结果独立记录，见最新进度及对应报告。

历史固定20篇已完成：第一轮73b66d4已完成16/20工程通过、20/20离线回放；第二轮aa01d59已完成20/20工程通过、20/20零网络回放、851项测试通过，两轮结果未拼接。最终报告为 research/current-twenty-02-report.json，截至该20篇轮次累计保守费用126.0062429元/授权200元；该轮请求已结束。新来源语义实测费用与状态另见最新进度。第二轮冻结分支为 `codex/model-alignment-local-repair`，修复源码已合回开发分支；失败反馈如何进入设计见 [工程经验](docs/workflow-repair-lessons.md)。

先读 [当前流程与运行命令](docs/current-workflow.md)、[最新进度](docs/progress.md) 和 AGENTS.md。

- 原文先入文本索引；提议、同模型证据复核、程序编译、局部隔离和图谱回放独立留档。
- 契约0.5已接入当前抽取：主体、集合、对象分开；未知成员不虚构；失败只隔离依赖项；原文仍可检索。
- 中文模型接口，程序生成ID与精确跨度。完整称谓允许可逆水平空白映射，不拆单字拼姓名。
- partial不能计作历史整篇成功；有模型判断的no_relevant_content不代表已证明无遗漏。不得声称图谱语义P/R/F1。
- 用户不提供法律专家gold。已审计168条QA仅14种依赖本案的问题，不当全库检索gold。
- 使用原授权百炼Flash及200元累计预算；最新账本见最新进度。Jev暂停，不改Spark服务，用户已授权持续修复，固定20篇工程目标已达成，目前转向来源可靠性与任务用途；见 docs/twenty-acceptance.md。
- 原文、响应、图谱和.env仅在忽略目录；Google Drive上传遵循本聊天授权，Git只保存代码与脱敏报告。
- 历史v1–v44保留重放。旧交接与旧下一步见 [历史交接](docs/history/handoff-through-v44.md)，不是当前执行指令。
