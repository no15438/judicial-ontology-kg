# judicial-ontology-kg

**当前开发入口已收敛为 current-rc1，见[当前主流程](docs/current-workflow.md)。** 旧v1–v44保留历史回放；新的局部隔离结果不能与历史整篇接受率直接混算。当前为待真实验收候选版，尚未证明检索增益。

司法文书本体与知识图谱构建研究的新仓库。首期研究中国民间借贷：真实原文 → 带证据的抽取记录 → 本体约束图谱 → 分层质量与效率评测。

当前已完成研究资料接续、开发环境、AppealCase/Spark 来源适配、开发隔离、证据回放、开发版最小本体、抽取契约和校验账本，并实现本地 Qwen / 百炼小批抽取适配器；保留语境的候选图装配已实现，正式语义评测尚未实现或冻结。用户无法提供专业人工 gold，开发按工程验证及已有标签覆盖继续推进，不以用户补标为前提。旧 GraphRAG 产品不作为新项目运行依赖。

## 开始工作

支持 macOS/Linux。初始化脚本使用 uv 0.12.19 和已提交的依赖锁文件，自动选择或下载 Python 3.12，不替换系统 Python。若未安装 uv，需已有可创建 venv 的 python3 来引导安装。

```bash
bash scripts/setup.sh
bash scripts/check.sh
bash scripts/run.sh verify-sources
```

初始化不下载案件、不访问模型、不创建数据库。`doctor` 检查开发环境和导入参考文件的完整性，不代表原始数据或模型已通过验证。

## 接手入口

- [当前进度与证据](docs/progress.md)：旧仓库和 Drive 三轮研究的合并结论。
- [现有标注可用性](docs/gold-data-audit.md)：200 条案件标签、QA 复核和事件抽样的区别。
- [来源适配与开发验证](docs/source-development.md)：24 份 AppealCase 和 12 份 Spark 开发文书的可复跑入口，以及无用户人工 gold 的边界。
- [最小本体与抽取契约](docs/ontology-contract.md)：开发版概念、证据/语境约束、逐篇失败账本及真实来源探针。
- [本地 Qwen 抽取](docs/qwen-extraction.md)：局域网串行调用、版本化提示、引用定位、上下文预算与失败留档。
- [百炼小批抽取](docs/bailian-extraction.md)：云端历史试跑；最新用户授权继续使用现有百炼项目，累计上限200元，详见当前进度。
- [固定自动流程](docs/fresh-workflow.md)：从原文全新运行，自动抽取、回放与装配，不复用历史响应或人工改图。
- [候选图装配](docs/graph-assembly.md)：文书内身份、语境与证据保留、无损还原、诊断和完整分母。
- [新架构与迁移范围](docs/architecture.md)：模块边界与尚未实现的部分。
- [云端环境](docs/cloud-environment.md)：启动方式、网络和 Spark 连接状态。
- [本地开发](docs/local-development.md)：当前优先方式，Mac 直接通过局域网连接 Spark。
- [后续任务](docs/roadmap.md)：依赖与下一步。
- [资料来源](research/README.md)：原字节登记、固定旧提交、Drive 复现入口。
- [后续会话交接](CODEX_HANDOFF.md)。

开发代码位于 `src/judicial_kg/`，测试位于 `tests/`。`research/` 保存元数据和历史设计参考。`data/`、`artifacts/`、`local/` 为初始化创建的 Git 忽略目录；原始案件、模型输出、凭据不进入源码版本库。

新仓库：[no15438/judicial-ontology-kg](https://github.com/no15438/judicial-ontology-kg)。旧仓库：[no15438/judicial-graphrag](https://github.com/no15438/judicial-graphrag)，本次检查固定于 `d4614083e72e8d4f4d6543b818e1f8935634fec7`。
