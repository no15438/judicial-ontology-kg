# 新仓库交接

核对日期：2026-10-04，Australia/Sydney。

当前仓库是 `no15438/judicial-ontology-kg`，与旧 `judicial-graphrag` 独立。不要把旧历史和旧系统整体导入，也不要重复创建仓库。

最新用户决定：开发改在本机进行，避免云端访问 Spark 的收费与流量限制。不要继续等待或部署收费远程访问。见 [本地开发](docs/local-development.md)。当前云端聊天不能自行切换执行主机；本机启动与 Spark 连接尚未验证。模型 API 只是用户提出的替代选项，尚未选定或接入供应商。

已接续旧仓库 `d4614083e72e8d4f4d6543b818e1f8935634fec7` 及用户提供的 Drive 目录。当前云端支持 Python 3.12 的离线开发、测试和元数据核验；初始化入口为 `bash scripts/setup.sh`。

先读：

1. [当前进度](docs/progress.md)。
2. [现有标注审计](docs/gold-data-audit.md)。用户指出人工 gold 难获得，要求先检查已有资源。
3. [架构](docs/architecture.md) 与 [后续任务](docs/roadmap.md)。
4. [本地开发](docs/local-development.md)：本机接续后，只读检查 Spark 的数据与标注；云端连接限制仅作历史背景。

下一项主要工作仍是数据来源与可评估范围：优先 AppealCase 严格借贷案对的原文准入核验，结合 MUSER 部分标签；可以先开展来源适配工程和局部验证，但不提前冻结主语料或图谱 gold。CJRC 的 16 条训练记录保留为跨度流程候选。现有 QA 复核不等于图谱 gold。

本次没有访问 Spark、调用模型、复算完整候选语料、冻结划分或产生新的图谱质量分数。旧 GitHub #14–26 仅作历史任务映射，未修改旧 Issue/Projects；新任务状态在 docs/roadmap.md 中。旧仓库文件内的既往授权不替代本会话授权。

Drive 中的《01 开发进度与复现入口｜Codex》经原生文档读取确认为空白，不可据标题推断完成进度。Drive 原资料没有被修改。
