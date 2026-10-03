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

用户最新明确：自己不是法律专业人员，无法提供人工标注 gold。不得把用户专业补标作为开发前置条件；采用来源回放、工程指标及覆盖明确的上游局部标签继续开发。完整语义图谱 P/R/F1 在缺少合适独立标注时保持缺失，不用模型自评替代。该约束已写入 AGENTS.md。

来源适配本轮已实现：AppealCase 两份固定原文件重新验哈希，复算 10,000 对中双侧借贷 1,258 对；保留既有 24 份开发文书，144 字段回放成功。Spark 有界扫描 2,325 条记录，按原生案由选出 12 个借贷父记录和 168 条 QA，独立字节回放全部对应。输出只在忽略目录。见 [开发数据层](docs/source-development.md) 与 [固定运行记录](research/source-development-report.json)。

开发版最小本体与抽取记录契约 `0.1.0` 已实现，包含 Actor/Mention/Context/Role/Event/Statement、来源证据回放、语境包含、引用一致性、缺失/重复/拒收账本；见 [本体契约](docs/ontology-contract.md)。两篇真实开发文书的部分候选探针回放 22 个跨度，非人工 gold、非 Qwen 输出。其余 34 篇尚未抽取，继续记录缺失。

下一项主要实施工作：连接本地 Qwen 抽取适配器，固定提示、模型和配置，保留原始响应、截断/超时/解析失败，再经契约验证进入保留语境的图谱装配。数据准入仍按来源分别记录缺口，近重复、跨语料关联与正式划分未冻结；原站逐字身份与完整图谱 gold 未获证明。现有 QA 复核不等于图谱 gold。

本次访问了 Spark 的指定 QA 目录：实测 53 个分片的路径/大小，核验 11 份小型证据文件和 100 条样本。样本只有 8 个父记录、无民间借贷；规范把“行业领域专家”“公共授权”和生成指示设为固定值。不能将数据内“人工已核验”声明直接认作图谱 gold。结果见 [标注审计](docs/gold-data-audit.md) 与 [机器记录](research/spark-qa-inventory.json)。这些父记录已登记开发暴露，不能用于盲测。

本轮复算了 AppealCase 固定发布物结构，未调用模型、冻结正式划分或产生新的图谱语义质量分数。旧 GitHub #14–26 仅作历史任务映射，未修改旧 Issue/Projects；新任务状态在 docs/roadmap.md 中。旧仓库文件内的既往授权不替代本会话授权。

本机工作树：`/Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg-current`，当前分支 `codex/ontology-contracts`，继承 `codex/source-adapters` 已推送的 `73a418b`。原同名目录 `judicial-ontology-kg` 是无共同祖先的旧历史 `2eb9811`，保留恢复用途；继续工作使用 `-current` 目录。原始发布物位于 `data/raw/appealcase/`，选中正文位于 `artifacts/source-development/`，契约探针位于 `artifacts/contract-probe/`，均被 Git 忽略。

最新本地 `bash scripts/check.sh` 通过：84 项测试、Ruff、格式、9 份来源参考哈希与 Git 空白检查。来源适配运行固定于干净代码提交 `422f219`。入口 `bash scripts/run.sh prepare-development`；新命令 `validate-extractions` 见本体契约文档。此结果不包含语义图谱质量或 Qwen 实验。8K/16K 测速消息已被用户撤回，本项目未做测速。

2026-10-04 10:20（悉尼）定时接续时，确认“测试 Spark 并发上限”任务已完成，且 Spark 8003/8002 健康、运行及排队数均为零，才恢复开发；等待用自动化 `automation-2` 已暂停，避免重复启动。本轮没有推理请求，也未变更 Spark 服务。后续访问仍需以实际服务状态为准。

Drive 中的《01 开发进度与复现入口｜Codex》经原生文档读取确认为空白，不可据标题推断完成进度。Drive 原资料没有被修改。
