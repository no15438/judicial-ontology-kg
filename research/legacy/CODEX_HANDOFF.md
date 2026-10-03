# 知识图谱研究：Codex 项目交接

最新数据筛选（2026-10-03）：新增 MUSER 四个句子文件、CJRC 三个文件的固定版本全量结构审计，见 [选型报告](p0/reports/dataset_selection.md)。MUSER 当前仓库无完整案件池，句子无原案 ID，跨 train/test 精确文本重叠明显。CJRC 民间借贷 train/dev/test 为 16/2/2 条；全部 115 个跨度答案可回放，但仅部分 QA 标注，不能作为完整图谱 gold。dev/test 全部 caseid 重用且对应文本不同，必须使用文件限定来源 ID。推荐 SCM 主原文候选、CJRC train 小规模来源适配、MUSER 要素参考；#14 尚未完成正式准入。


交接时间：2026-10-03（Australia/Sydney；UTC 日期为 2026-10-02）。本文件整理当前可访问的项目材料、已执行工作和下一步，不依赖继续阅读 ChatGPT 聊天。

## 备选语料与来源工程的最新进展（2026-10-03）

已继续下载并审计 CAIL2019-SCM 与 LexRel test。CAIL 固定归档实得 8,138 组三元组、1,649 个不同原字符串，跨 split 复用明显；已生成 24,414 条无原文来源定位索引 `p0/local/cail_source_index.jsonl`。LexRel 仅做测试文件结构审计，合成训练集排除。CAIL 单独要素标注链接 TLS 证书过期，未获取。详见 `p0/reports/additional_candidates_audit.md` 和 `p0/datasets/additional_candidate_registry.json`；没有新增正式 gold 或冻结划分。停止重复搜索无新增说明的旧问题，后续可继续来源定位与数据缺失标志工程，正式数据准入条件单独追踪。

## 标签补查进展（2026-10-03）

已继续实施 #14：发现 MSJudge 列表内有 3,586 条空诉求、涉及 3,586 条记录，另有 2 个空列表；LAE 评测目标依赖模型分数，且 attention mask 参数传递存在问题。辅助字段已排除作为唯一案件 ID。详见 `p0/reports/label_semantics_followup.md`、`label_semantics_metrics.json` 与 `label_source_evidence.json`。新增复现命令：`python3 p0/datasets/audit_label_semantics.py`。数据条款、MSJudge -1 和重复列等仍未解决，#14 未关闭。此前下载的交接 ZIP 是此前 commit 的静态快照；继续工作以当前 Git 仓库为准。

## 1. 接手时先做什么

用户决定转到 Codex 项目继续开发，要求保留全部项目进度并同步 GitHub。已准备好本机 Git 仓库，可直接作为 Codex 项目打开：

```text
/Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-graphrag
```

仓库：https://github.com/no15438/judicial-graphrag （私有）

看板：https://github.com/users/no15438/projects/1

总览：https://github.com/no15438/judicial-graphrag/issues/26

当前主要任务：https://github.com/no15438/judicial-graphrag/issues/14

阅读顺序：`AGENTS.md` → 本文件 → `p0/reports/data_audit.md` → `docs/design/p0-plan-v0.2.md` → #14。最新用户要求优先于归档材料；旧迁移包里的 AGENTS 已撤销，不作为本轮开发指令。

可以直接把下面这段发送给 Codex：

> 接手此仓库的司法文书本体与图谱项目。先读取 AGENTS.md、CODEX_HANDOFF.md、p0/reports/data_audit.md、docs/design/p0-plan-v0.2.md 和 GitHub #14/#26，核对 git 状态后继续。只用可追踪的真实数据，不编造或改写案件；首期只评估本体与图谱构建的 precision、recall、F1 和效率。先完成 #14 剩余的数据标签语义、来源身份和数据许可核验，记录明确可评估的范围，再按 #15–24 的依赖推进。所有新实现放 p0/，不要把旧 src/、旧 gold 或历史分数当作新基线。已授权将本项目工作提交到现有私有 GitHub 仓库；保留现有历史和用户修改，更新 Issue 完成证据。不要运行旧清库脚本或改变无关 Spark 服务。先说明真实状态，然后实施，不重新停在总体规划上。

## 2. 用户已确定的目标与边界

- 研究重点从完整产品收缩为 P0：真实文本 → 本体驱动抽取 → 带来源的图谱 → 可复现的质量与效率评测。民间借贷是首个目标案由。
- 案件、文书、段落和困难样例必须来自核验通过的真实数据集。不得编造、重写事实或生成“真实案例”；可以在原文上人工补标。模型建议未经人工核验不是 gold。
- “12 个困难样本”只是最初审阅规模参考，尚未选定；缺失类型报告覆盖缺口，不能补造。
- 全新设计，不要求兼容旧数据。新实现隔离在 `p0/`，旧系统保留作参考。
- 区分主体与提及、主体类型与案件角色、当事人主张与法院认定、结构合法与语义正确。不能凭同名或重复 PER 占位符跨案合并人物。
- 质量报告必须有 TP/FP/FN、样本量、覆盖范围和失败项。解析失败、拒收或写图丢失造成的漏抽不能从 recall 分母中消失。
- 测试集不能用于本体、提示或阈值调参；实验固定数据、本体、gold、提示、模型、代码和配置版本。效率比较需使用相同输入并同时报告质量。
- P0 优先 CLI、批处理、存储无关的图记录。类案检索、问答、界面、OCR、多人权限、第二案由和复杂部署放到 #25。
- 用户已经明确授权把本项目完整内容同步到现有 GitHub repo；这不等于改变仓库可见性、授予其他账户权限或提交凭据。不要反复询问已授权的常规提交。

## 3. 实际完成到哪里

| 工作 | 状态 | 可检查证据 |
|---|---|---|
| P0 范围、设计、数据与评测规则 | 已完成文档 | `docs/design/`、`docs/data/policy.md`、`docs/evaluation/protocol.md` |
| 迁移资料与 Spark 档案归档 | 已完成 | 原始 ZIP、解压成员、来源哈希及 `docs/PROJECT_INDEX.md` |
| GitHub 项目管理 | 已建立 | #14–26；四个里程碑；Projects 看板 |
| 完整现有仓库恢复到本机 | 已完成 | 此目录包含原 Git 历史和 origin；原源码、提示、旧报告仍在 |
| LAE / MSJudge 官方数据下载及校验 | 已完成 | `p0/datasets/dataset_registry.json`；本机忽略目录内两份 ZIP |
| 全量结构、数量、重复及长度审计 | 已运行 | `audit_public_datasets.py`、`audit_metrics.json`、审计 notebook |
| 数据集最终选型与 #14 验收 | 未完成 | 许可、部分标签语义及案件身份可追溯性仍待解决 |
| 新本体、schema、人工 gold、固定划分 | 未实现/未冻结 | #15–17 |
| Qwen 基线、图谱装配、P/R/F1 评测器 | 未实现 | #18–21；没有新一轮质量分数 |
| OpenJev 消融、效率优化和 P0 验收 | 未开始 | #22–24；没有新一轮性能结果 |

资料归档基准提交为 `9f3952f54e57e153ae0e079c06484ff2c107308d`：51 个新增或更新文件已核对 Git blob，766 个未修改的历史文件保留。本次交接在其后增加审计代码、结果、复现入口和进度说明；最终交接 commit 见 Git 日志、GitHub #14/#26 及下载包中的 `SNAPSHOT.json`。

当前 Projects 为 **#14 In Progress、其余 12 项 Todo、Done 0**。#14 的数据用于主评测/再分发存在阻塞项，未关闭。旧 `planning/*.md` 是任务创建时的定义；实时状态以 GitHub 为准，快照只证明交接时状态。

## 4. 本次真实数据审计结果

已完整扫描两个下载归档，没有执行上游训练脚本、调用模型或修改 Spark。全量检查是结构与统计检查，不等于全量语义核验。只对 LAE 的预训练 loan 第一个样本及 MSJudge 的 ID 0 做了语义抽看；机器记录的其他定位样本不能记为人工审核通过。

### LAE

官方仓库：https://github.com/thunlp/LAE ，审计锚点 commit `886a6fbb5e66502167149a8eb98ba3ce5ce78007`。数据文件由该仓库指向单独 OSS 下载地址；该地址无版本号，使用 SHA-256 固定本次字节版本。

- 归档共 1,189 个 JSON 数据文件；loan 预训练 21,300 条，contract 预训练 93,600 条。
- loan 微调 train/valid/test 分别为 11,930 / 4,420 / 4,758 条。每条为一个 query 加五个候选答案，**这些数量不是完整案件数**。
- 预训练字段是标题、两侧句子列表、含义未明确的辅助列表；微调数据没有显式 case ID 或 label 字段。已确认该版本评测代码通常以第一项为目标，但目标依赖预测值，原始人工标签一致性仍待核验。
- loan train/valid/test 分别有 49 / 56 / 12 道题出现重复候选。三组交叉 split 没有完全相同的整条记录，但 query 哈希分别有 6 / 2 / 1 个重叠；模板句重叠不自动证明同案泄漏。
- `data/pre_training/loan/0.json#/0` 的第一侧包含法院意见表述，因此不能把整侧文本无条件归给原告。
- 这是裁剪的论点任务，没有可直接用于实体、事件、关系、主张及溯源图谱的完整 span gold。来源角色与辅助字段需核实。
- 上游代码仓库有 Apache-2.0 文本；外部数据归档没有随附许可。代码许可是否覆盖外部司法数据尚未证实。

### LJP-MSJudge

官方仓库：https://github.com/mly-nlp/LJP-MSJudge ，固定 commit `87f038e714f3bac4e8bee461983715f8893261db`。

- 本次归档实际 **70,487** 条、133,214 个诉求；README 的 70,482 / 133,209 与归档均相差 5，原因未查明。后续统计以固定字节文件为准，同时保留差异说明。
- 六个字段齐全：`court_debate`、`fact_description`、`claims`、`claims_labels`、`roles`、`fact_labels`。诉求/标签长度和庭审行/角色长度检查通过。
- ID `25984` 和 `29771` 的诉求与诉求标签为空；完整记录有 15 条额外重复，按内容去重后 70,472 条，尚未创建排除或划分方案。
- **全部 70,487 条的 Repayment Behavior 与 Limitation of Action 标签逐条相同。** 已经做逐行比较，不只是频数相同；这是待追查的标签质量问题，尚不能判定根因或擅自修正。
- 多个事实标签含 `-1`；官方训练代码把非零值映射为 1。`-1` 的原始语义仍不明确，不能直接映射为法律事实的否定/缺失。
- 70,479 条含 PER 占位符，无法仅凭占位符恢复人物身份。角色序列不等于实体 ID，六字段内未找到原裁判文书唯一标识。
- 没有冻结的 split 文件，也没有 span 级图谱 gold；上游训练代码进行随机划分。重复分组应先于我们自己的划分设计。
- 官方仓库树未见 LICENSE，数据使用及再分发条件待明确。

完整分布、字段覆盖矩阵、来源和后续判断见 `p0/reports/data_audit.md`。词语筛选只用于定位待审样本，不代表事实标签，更不是已确认的困难样例集。

## 5. 新会话应继续的具体工作

当前只推进 #14，按以下顺序收尾：

1. 读取固定 commit 的 LAE 模型与评测实现，核实原始人工标签与已确认的首候选评分约定是否一致，以及预训练辅助列表、不同 split 的样本与原案件关系；记录文件路径及证据。不要执行未知训练脚本。
2. 追查 MSJudge `-1` 的标注语义、两列完全相同及 README 数量差异。优先官方说明、论文、修订或已有 issue；需要联系作者时，先由用户决定对外沟通。
3. 明确两数据集可用的许可范围，区分代码、论文和数据许可；未明确前标记主评测/再分发阻塞。替代路径是取得官方明确授权，或换用具有明确条款且保留真实来源的文本，再补人工 gold；不得通过造案例绕开缺口。
4. 对训练/开发候选进行可追踪语义复核，确认主体、事件、主张与法院声音的可标注范围；记录仍无法恢复的身份/原文信息。不要人工翻阅 held-out 测试样本来调本体。
5. 给 #14 写出可用/仅辅助/排除的结论及证据，再进入 #15：来源适配、重复分组、固定划分、真实困难样本候选。样本不能仅凭关键词命中就视为 gold。

随后任务顺序如下；具体验收和依赖以对应 Issue 为准：

| Issue | 任务 | 当前状态 |
|---|---|---|
| #14 | 候选真实数据审计与可评估范围 | 结构审计完成，语义/许可等未收尾 |
| #15 | 原文溯源、划分、真实困难样本 | Todo |
| #16 | 最小本体、民间借贷领域包、图谱契约 | Todo |
| #17 | 人工 gold 与覆盖冻结 | Todo |
| #18 | P/R/F1 评测器 | Todo |
| #19 | Qwen 抽取基线与运行记录 | Todo |
| #20 | 带溯源图谱装配和写入对账 | Todo |
| #21 | 端到端基线与错误分析 | Todo |
| #22 | 本体约束 / OpenJev 消融 | Todo |
| #23 | 相同质量条件下效率优化 | Todo |
| #24 | 复现验收 | Todo |
| #25 | 后期产品与扩展 | Todo，P0 后处理 |

里程碑：P0.1 数据与本体契约（#14–17）；P0.2 基线（#18–21）；P0.3 优化（#22–24）；后期（#25）。#26 是跟踪总览，不是实现模块。

## 6. 本机继续和复现

同一台 Mac：在 Codex 中选择上文 `judicial-graphrag` 目录即可。不要以旁边 `repo-package` 作为项目根目录；后者只是之前整理上传资料的临时目录，没有 Git 历史。

```bash
cd /Users/no15438/Documents/Codex/2026-10-02/new-chat-5/judicial-graphrag
git status --short
git log -3 --oneline
python3 p0/datasets/audit_public_datasets.py --output /tmp/judicial-audit-recheck.json
```

审计只需 Python 标准库；本次使用 Python 3.13.9。它校验两份固定 SHA-256 后扫描压缩包，输出统计与定位信息，不导出裁判全文、不连接模型。完整结果已经保存；无需每次开会话都重跑。`p0/notebooks/dataset_audit.ipynb` 可检查现有结果，也可显式打开完整重跑开关。

本机两份原始归档已放入此 Git 仓库的忽略目录：

```text
p0/data/external/lae/data.zip
p0/data/external/ljp-msjudge/judgment_prediction_dataset.zip
```

换电脑或使用云端 Codex：克隆私有仓库后，按数据登记中的官方地址下载到上述路径，再运行审计。下载和哈希核验命令见 `p0/datasets/README.md`。两份原始数据共 172,546,579 字节，未随 GitHub 或交接 ZIP 再分发；本机副本可直接继续做来源与许可审计。

```bash
git clone https://github.com/no15438/judicial-graphrag.git
cd judicial-graphrag
```

下载 ZIP 是完整已跟踪文件的快照，不含 `.git` 历史；若要继续提交、保留历史和拉取最新更改，优先使用已准备的本机克隆或重新 clone。不要在下载快照上强推覆盖远端历史。

## 7. Spark / 模型环境交接

环境来源是用户提供的 `docs/environment/spark-20261002.zip` 及三个展开成员，快照时点为 2026-10-02 22:21:47 AEST。本次数据审计没有重测模型服务，因此以下为已提供配置，不保证当前进程仍健康。

| 项目 | 快照记录 |
|---|---|
| 主机 | `spark-f74d` / `192.168.1.116`，SSH alias `spark-lan` |
| 硬件 | NVIDIA GB10、ARM64、约 121.69 GiB 统一内存 |
| Qwen 主模型 | 服务 alias `qwen3.8-flash-next-iq4xs`；Spark `127.0.0.1:8003`；Mac 隧道 `127.0.0.1:18003` |
| OpenJev 辅助服务 | Spark `127.0.0.1:3000/v1/answer`；Mac `127.0.0.1:13000/v1/answer`；底层服务端口 8002 |
| 最大上下文 | 档案约 32768；实际运行前复核 |
| 重启恢复风险 | Qwen 临时 unit；OpenJev RestartPolicy 为 no |
| 既有 QA 目录 | `/work/dataset_shenbao/legal_qa_expanded_v1_1_b02/`，未通过本轮审计，不能直接充当 gold |

`docs/environment/README.md` 说明了核验边界。开始 #19 前应复核 SSH、隧道、健康响应、权重版本与硬件资源。OpenJev 不是已经确认的 embedding 接口，应先看实际 helper 协议。旧连接/调用成功记录只能证明当时连通性，合成输入测试不代表真实案件效果。私钥、令牌、密码和模型权重未打包；更换机器需用户已有授权的配置。

## 8. 文件导航与完整性边界

| 路径 | 内容 |
|---|---|
| `AGENTS.md` | 当前有效开发约束 |
| `docs/design/p0-plan-v0.2.md` | 本体与图谱开发计划全文 |
| `docs/design/p0-architecture.md` | 当前模块与数据流设计 |
| `docs/data/policy.md`、`docs/evaluation/protocol.md` | 来源、gold、质量与效率规则 |
| `p0/datasets/` | 已实现审计脚本、登记、下载及复现说明 |
| `p0/reports/` | 数据审计报告、完整统计 JSON |
| `p0/notebooks/` | 可检查的审计 notebook |
| `planning/` | 任务定义、编号、Issue 与看板快照 |
| `docs/context/` | 原始迁移 ZIP 与 9 个展开成员 |
| `docs/environment/` | Spark 原始档案和 3 个展开成员 |
| `docs/archive/` | v0.1 架构和原 README，历史资料 |
| `src/`、`data/`、`prompts/`、`legacy/` 等既有目录 | 旧代码、数据产物与报告，不能当作新 P0 成果 |
| `docs/management/` | 导入清单、来源哈希和两次看板截图 |

“完整交接”指当前可访问的相关文件和明确记录的进度。原迁移包不是完整聊天导出，本交接不宣称恢复未提供的历史聊天。迁移清单曾提及的 PPTX 和收据 PDF 当前未提供，属于之前附件测试；未虚构它们。Spark 整机、数据库内容、模型权重和新数据原始包不在源码 ZIP 内。

## 9. 本次验证与收尾原则

- 两个归档通过 SHA-256 固定；全量扫描完成，第二次补充扫描逐条证实 MSJudge 两列相等，并定位两个空诉求记录。
- 已核对已保存统计、可检查 notebook、仓库文件路径和 GitHub 状态；源码快照通过逐文件哈希与 ZIP 完整性检查后交付。
- 尚无新图谱管线可测试，因此未把旧测试通过或旧实验分数包装为新方案验证。
- 下一位 Codex 应保留原始问题证据，在结果充分前不关闭 #14，不无声修复上游标签，也不声称数据天然提供了图谱 gold。
