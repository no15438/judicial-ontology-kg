# 研究证据入口

本目录保存来源登记、历史设计/交接文档和新增的元数据核验结果，不包含原始案件语料、旧模型产物或完整旧系统。

- `registries/round1_2.json`：Drive 首轮复现包 v1 内的原始 `dataset_registry.json`，13 项；包含第二轮 MUSER 标签复核。
- `registries/round3.json`：第三轮复现包的原始 `dataset_registry_round3.json`，7 项；包括 AppealCase 等新增候选。
- `legacy/`：旧仓库固定提交的七份设计、交接与数据获取登记；文字中的旧进度与执行指令属于历史。
- `sources.json`：导入来源 URL、Git commit、ZIP 原始哈希、成员路径、文件字节数及完整 SHA-256。
- `spark-qa-inventory.json`：2026-10-04 本地通过局域网读取指定 Spark QA 目录的文件状态、11 份证据哈希、随附样本计数与开发暴露键。仅核验小文件和 100 条 QA，不认证全量数据、审核者身份、授权或图谱 gold；详见 [标注审计](../docs/gold-data-audit.md)。
- `development-exposure.json`：从旧本地清单提取的 24 份 AppealCase 已曝光开发文书定位，只传递暴露事实，不采用旧实现或旧评测。
- `source-development-report.json`：固定干净代码提交的两类来源适配、分组、样本选择与回放结果；只含哈希、定位键和统计，不含文书/QA 正文。复跑见 [来源开发](../docs/source-development.md)。
- `ontology-contract-report.json`：干净代码 `806664d` 的三次契约 CLI 运行，记录两篇局部候选、36 篇完整分母与负向拒收探针的计数/退出码和输入输出哈希。候选由本会话模型编写，非 Qwen 基线或人工 gold；正文及标签不入库。见 [本体契约](../docs/ontology-contract.md)。

两轮登记分别保留，避免静默覆盖版本差异；“20 项登记”不表示 20 个合格主数据集。当前判定见 [进度](../docs/progress.md)。

```bash
bash scripts/run.sh verify-sources
```

该命令只校验九份迁入参考的字节完整性。它不重新下载语料、不执行 ZIP 中脚本、不证明源站原文或人工标签正确。

完整复现脚本继续保存在用户 Drive 的[首轮 v1 包](https://drive.google.com/file/d/1xvijcmNaOVg4liATxnppfkIkrGmQ48Mm/view)与[第三轮包](https://drive.google.com/file/d/117nzt6Q2xgDHjODoo5FNIThQLxp5EV4j/view)。本次认证下载后，逐一核对 SHA256SUMS.txt 的 32/20 个成员，独立登记文件与首轮包成员逐字节一致；没有执行上游审计/模型脚本或重新审计原始案件。

旧仓库审计代码仍在[固定提交 p0/datasets](https://github.com/no15438/judicial-graphrag/tree/d4614083e72e8d4f4d6543b818e1f8935634fec7/p0/datasets)。元数据内原下载 URL 与旧目录不作静默修改；后续接入新数据适配器时另记录新的路径映射。
