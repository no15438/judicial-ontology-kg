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

下一项主要实施工作：基于已隔离的真实开发文书建立最小本体和实体/提及/事件/带归属陈述契约，再接本地抽取与校验。数据准入仍按来源分别记录缺口，近重复、跨语料关联与正式划分未冻结；原站逐字身份与完整图谱 gold 未获证明。现有 QA 复核不等于图谱 gold。

本次访问了 Spark 的指定 QA 目录：实测 53 个分片的路径/大小，核验 11 份小型证据文件和 100 条样本。样本只有 8 个父记录、无民间借贷；规范把“行业领域专家”“公共授权”和生成指示设为固定值。不能将数据内“人工已核验”声明直接认作图谱 gold。结果见 [标注审计](docs/gold-data-audit.md) 与 [机器记录](research/spark-qa-inventory.json)。这些父记录已登记开发暴露，不能用于盲测。

本轮复算了 AppealCase 固定发布物结构，未调用模型、冻结正式划分或产生新的图谱语义质量分数。旧 GitHub #14–26 仅作历史任务映射，未修改旧 Issue/Projects；新任务状态在 docs/roadmap.md 中。旧仓库文件内的既往授权不替代本会话授权。

本机工作树：`/Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg-current`，分支 `codex/source-adapters`，继承已推送的 `32ea863` 核验工作。原同名目录 `judicial-ontology-kg` 是无共同祖先的旧历史 `2eb9811`，保留恢复用途；继续工作使用 `-current` 目录。原始发布物位于 `data/raw/appealcase/`，选中正文位于 `artifacts/source-development/`，均被 Git 忽略。

最新本地 `bash scripts/check.sh` 通过：41 项测试、Ruff、格式、9 份来源参考哈希与 Git 空白检查。真实来源运行固定于干净代码提交 `422f219`。入口 `bash scripts/run.sh prepare-development`；可选 `--spark-capture`，详见开发数据层文档。此结果不包含语义图谱质量或模型实验。8K/16K 测速消息已被用户撤回，未做测速；撤回前仅只读探查服务健康、模型元数据与模板/分词接口，没有推理请求。

Drive 中的《01 开发进度与复现入口｜Codex》经原生文档读取确认为空白，不可据标题推断完成进度。Drive 原资料没有被修改。
