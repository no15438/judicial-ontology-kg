# 新仓库交接

核对日期：2026-10-04，Australia/Sydney。

当前仓库是 `no15438/judicial-ontology-kg`，与旧 `judicial-graphrag` 独立。不要把旧历史和旧系统整体导入，也不要重复创建仓库。

最新用户决定：开发改在本机进行，避免云端访问 Spark 的收费与流量限制。不要继续等待或部署收费远程访问。见 [本地开发](docs/local-development.md)。本次本地接续已完成 Mac 初始化并验证 `spark-lan`，指定 QA 目录已做首轮只读盘点。模型 API 尚未选定或接入供应商。

已接续旧仓库 `d4614083e72e8d4f4d6543b818e1f8935634fec7` 及用户提供的 Drive 目录。当前云端支持 Python 3.12 的离线开发、测试和元数据核验；初始化入口为 `bash scripts/setup.sh`。

先读：

1. [当前进度](docs/progress.md)。
2. [现有标注审计](docs/gold-data-audit.md)。用户指出人工 gold 难获得，要求先检查已有资源。
3. [架构](docs/architecture.md) 与 [后续任务](docs/roadmap.md)。
4. [本地开发](docs/local-development.md)：本机接续后，只读检查 Spark 的数据与标注；云端连接限制仅作历史背景。

下一项主要工作仍是数据来源与可评估范围：优先 AppealCase 严格借贷案对的原文准入核验，结合 MUSER 部分标签；可以先开展来源适配工程和局部验证，但不提前冻结主语料或图谱 gold。CJRC 的 16 条训练记录保留为跨度流程候选。现有 QA 复核不等于图谱 gold。

本次访问了 Spark 的指定 QA 目录：实测 53 个分片的路径/大小，核验 11 份小型证据文件和 100 条样本。样本只有 8 个父记录、无民间借贷；规范把“行业领域专家”“公共授权”和生成指示设为固定值。不能将数据内“人工已核验”声明直接认作图谱 gold。结果见 [标注审计](docs/gold-data-audit.md) 与 [机器记录](research/spark-qa-inventory.json)。这些父记录已登记开发暴露，不能用于盲测。

本次未调用模型、复算完整候选语料、冻结划分或产生新的图谱质量分数。旧 GitHub #14–26 仅作历史任务映射，未修改旧 Issue/Projects；新任务状态在 docs/roadmap.md 中。旧仓库文件内的既往授权不替代本会话授权。

本机工作树：`/Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg-current`，分支 `codex/spark-data-audit`，基于远端 `bec2b6a`。原同名目录 `judicial-ontology-kg` 是无共同祖先的旧历史 `2eb9811`，保留恢复用途；继续工作使用 `-current` 目录。案件样本与交付物副本位于 Git 忽略的 `artifacts/spark-qa-audit/`。

最新本地 `bash scripts/check.sh` 通过：13 项测试、Ruff、格式、9 份来源参考哈希与 Git 空白检查。此结果不包含远端 Actions、全量语料、人工 gold 或模型实验。

Drive 中的《01 开发进度与复现入口｜Codex》经原生文档读取确认为空白，不可据标题推断完成进度。Drive 原资料没有被修改。
