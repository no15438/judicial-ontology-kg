# judicial-ontology-kg

司法文书本体与知识图谱构建研究的新仓库。首期研究中国民间借贷：真实原文 → 带证据的抽取记录 → 本体约束图谱 → 分层质量与效率评测。

当前已完成研究资料接续和云端开发环境初始化；本体、抽取、图谱装配、人工 gold 与正式评测尚未实现或冻结。旧 GraphRAG 产品不作为新项目运行依赖。

## 开始工作

使用 Python 3.12；初始化脚本使用 uv 0.12.19 和已提交的依赖锁文件。

```bash
bash scripts/setup.sh
bash scripts/check.sh
.venv/bin/judicial-kg verify-sources
```

初始化不下载案件、不访问模型、不创建数据库。`doctor` 检查开发环境和导入参考文件的完整性，不代表原始数据或模型已通过验证。

## 接手入口

- [当前进度与证据](docs/progress.md)：旧仓库和 Drive 三轮研究的合并结论。
- [现有标注可用性](docs/gold-data-audit.md)：200 条案件标签、QA 复核和事件抽样的区别。
- [新架构与迁移范围](docs/architecture.md)：模块边界与尚未实现的部分。
- [云端环境](docs/cloud-environment.md)：启动方式、网络和 Spark 连接状态。
- [后续任务](docs/roadmap.md)：依赖与下一步。
- [资料来源](research/README.md)：原字节登记、固定旧提交、Drive 复现入口。
- [后续会话交接](CODEX_HANDOFF.md)。

开发代码位于 `src/judicial_kg/`，测试位于 `tests/`。`research/` 保存元数据和历史设计参考。`data/`、`artifacts/`、`local/` 为初始化创建的 Git 忽略目录；原始案件、模型输出、凭据不进入源码版本库。

新仓库：[no15438/judicial-ontology-kg](https://github.com/no15438/judicial-ontology-kg)。旧仓库：[no15438/judicial-graphrag](https://github.com/no15438/judicial-graphrag)，本次检查固定于 `d4614083e72e8d4f4d6543b818e1f8935634fec7`。
