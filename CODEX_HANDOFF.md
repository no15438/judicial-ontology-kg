# 新仓库交接

核对日期：2026-10-04，Australia/Sydney。

当前仓库是 `no15438/judicial-ontology-kg`，与旧 `judicial-graphrag` 独立。不要把旧历史和旧系统整体导入，也不要重复创建仓库。

最新用户决定（2026-10-04）：找出并修复抽取问题，后续使用本地模型服务。开发仍在 Mac，调用已有 spark-lan 上的 Qwen；不要修改模型服务或配置收费远程访问。此前已授权的北京百炼两篇试跑已经结束，后续没有新的付费授权。专用密钥仍保留在被忽略的 .env.local（权限 600），不打印、不提交。最新本地修复与运行记录见 [本地抽取](docs/qwen-extraction.md)，云端历史见 [百炼接入](docs/bailian-extraction.md)。

已接续旧仓库 `d4614083e72e8d4f4d6543b818e1f8935634fec7` 及用户提供的 Drive 目录。当前云端支持 Python 3.12 的离线开发、测试和元数据核验；初始化入口为 `bash scripts/setup.sh`。

先读：

1. [当前进度](docs/progress.md)。
2. [现有标注审计](docs/gold-data-audit.md)。用户指出人工 gold 难获得，要求先检查已有资源。
3. [架构](docs/architecture.md) 与 [后续任务](docs/roadmap.md)。
4. [本地开发](docs/local-development.md)：本机接续后，只读检查 Spark 的数据与标注；云端连接限制仅作历史背景。

用户最新明确：自己不是法律专业人员，无法提供人工标注 gold。不得把用户专业补标作为开发前置条件；采用来源回放、工程指标及覆盖明确的上游局部标签继续开发。完整语义图谱 P/R/F1 在缺少合适独立标注时保持缺失，不用模型自评替代。该约束已写入 AGENTS.md。

来源适配本轮已实现：AppealCase 两份固定原文件重新验哈希，复算 10,000 对中双侧借贷 1,258 对；保留既有 24 份开发文书，144 字段回放成功。Spark 有界扫描 2,325 条记录，按原生案由选出 12 个借贷父记录和 168 条 QA，独立字节回放全部对应。输出只在忽略目录。见 [开发数据层](docs/source-development.md) 与 [固定运行记录](research/source-development-report.json)。

开发版最小本体与抽取记录契约 `0.1.0` 已实现，包含 Actor/Mention/Context/Role/Event/Statement、来源证据回放、语境包含、引用一致性、缺失/重复/拒收账本；见 [本体契约](docs/ontology-contract.md)。两篇真实开发文书的部分候选探针回放 22 个跨度，非人工 gold、非 Qwen 输出。其余 34 篇尚未抽取，继续记录缺失。

契约验证的三次 CLI 运行固定于干净代码提交 `806664d`，计数、版本、输入输出哈希及退出码见 [机器记录](research/ontology-contract-report.json)。所有原文、候选和逐篇账本仍在忽略目录。

抽取适配器已实现本地 Qwen 与百炼两条路径，当前按最新决定使用本地 Qwen。下一项主要实施工作：依据原始响应及逐篇失败账本验证证据锚定、类型与归一化修复，再进入保留语境的图谱装配。云端扩批或新增付费轮次需明确新范围及预算；不要把已有两篇授权当成全量授权。数据准入仍按来源分别记录缺口，近重复、跨语料关联与正式划分未冻结；原站逐字身份与完整图谱 gold 未获证明。现有 QA 复核不等于图谱 gold。

本次访问了 Spark 的指定 QA 目录：实测 53 个分片的路径/大小，核验 11 份小型证据文件和 100 条样本。样本只有 8 个父记录、无民间借贷；规范把“行业领域专家”“公共授权”和生成指示设为固定值。不能将数据内“人工已核验”声明直接认作图谱 gold。结果见 [标注审计](docs/gold-data-audit.md) 与 [机器记录](research/spark-qa-inventory.json)。这些父记录已登记开发暴露，不能用于盲测。

本轮复算了 AppealCase 固定发布物结构，未调用模型、冻结正式划分或产生新的图谱语义质量分数。旧 GitHub #14–26 仅作历史任务映射，未修改旧 Issue/Projects；新任务状态在 docs/roadmap.md 中。旧仓库文件内的既往授权不替代本会话授权。

本机工作树：`/Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-ontology-kg-current`，当前分支 `codex/qwen-extraction`，继承已推送的本体契约分支与来源分支。原同名目录 `judicial-ontology-kg` 是无共同祖先的旧历史 `2eb9811`，保留恢复用途；继续工作使用 `-current` 目录。原始发布物位于 `data/raw/appealcase/`，选中正文位于 `artifacts/source-development/`，契约探针位于 `artifacts/contract-probe/`，均被 Git 忽略。

最新本地 `bash scripts/check.sh` 通过：123 项测试、Ruff、格式、9 份来源参考哈希与 Git 空白检查。来源适配运行固定于干净代码提交 `422f219`。入口 `bash scripts/run.sh prepare-development`；新命令 `validate-extractions` 见本体契约文档。单元测试不是语义图谱质量；真实模型试跑另行记录。8K/16K 测速消息已被用户撤回，本项目未做测速。

2026-10-04 10:20（悉尼）定时接续时，确认“测试 Spark 并发上限”任务已完成，且 Spark 8003/8002 健康、运行及排队数均为零，才恢复开发；等待用自动化 `automation-2` 已暂停，避免重复启动。本轮没有推理请求，也未变更 Spark 服务。后续访问仍需以实际服务状态为准。

Drive 中的《01 开发进度与复现入口｜Codex》经原生文档读取确认为空白，不可据标题推断完成进度。Drive 原资料没有被修改。

本地 Qwen v1/v2 共四次调用均输出截断，没有接受候选；更换 JSON 解码后仍出现编号循环。v3 代码在 77e67c8 将反向提及索引交给程序，但用户改用云端后没有本地实跑，不能宣称已解决循环，更不能归因于温度。固定失败记录见 research/qwen-extraction-report.json。云端温度 0.7 是默认配置，不是对照实验证据。

百炼适配冻结于 4ddae2a；首次执行发现 Mac Python 缺少默认 CA，停止后续提交。修复于 1d5c827，加入现有系统 CA，未禁用 TLS 校验；恢复配置保守保留首轮失败调用的预算预留，合并预留 3.995904 元。真实输出、失败与费用记录在 artifacts/bailian-extraction/，不提交正文。

用户进一步指出 0.7 对抽取可能偏高，低温配置已准备为 configs/bailian-low-temperature.json（0.2，提示和两篇原文不变）。此前两篇完整返回，但一篇引用歧义、一篇证据超出语境，未接受候选；费用估算 0.357804 元。低温轮仍在原 5 元总预算内，见百炼文档的累计预留计算；原失败不得改写成通过。

本轮百炼试跑已结束，自动化保持暂停。四次完整响应（0.7/0.2 各两篇）均未通过最终证据/语境校验，接受候选为 0；34 篇未选中保持缺失。API 接入成功不等于抽取质量通过。默认温度已改为 0.2，不能把这两篇结果宣称为温度优化成功。费用按返回 usage 原价估算 0.733860 元，累计保守预留 4.991232 元，详见 research/bailian-extraction-report.json。下一步先离线改进证据锚定和语境归属；新付费轮次/扩批另定，不能反复运行原批次当作仍有自动授权。当前代码 123 项测试通过。
