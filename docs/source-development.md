# 来源适配与开发验证

2026-10-04 已实现两类来源的开发数据层。用户不是法律专业人员，无法提供人工 gold；数据适配、证据回放、本体及抽取工程继续推进，不以用户补标为前提。当前交付不声称已完成语义抽取、正式图谱或完整图谱 P/R/F1。

## 本次实际运行

固定代码提交 `422f219f4f30076594fa14e4af7cbd320c2e81f8`，工作区干净时执行来源准备。完整元数据结果见 [source-development-report.json](../research/source-development-report.json)，包括代码哈希、输入与输出哈希、开发暴露记录和失败项。原始数据、选中正文、上游 QA 答案均留在 `data/` 和 `artifacts/` 忽略目录。

| 来源 | 本次执行 | 可用于什么 | 尚未证明 |
|---|---|---|---|
| AppealCase | 对本机原始发布文件重新核验两份 SHA-256；适配 10,000 对、20,000 份文书，复算严格双侧借贷 1,258 对 | 来源定位、案件分组、保留六字段的抽取输入 | 原法院全文逐字等价、近重复和跨语料关联、正式测试隔离 |
| AppealCase 开发集 | 沿用 12 个既有开发组、24 份借贷文书；144 个字段回放成功 | 后续本体与证据跨度契约的开发 | 这批样本已曝光，不能作盲测或人工 gold |
| Spark 有界筛选 | 按清单顺序读取首片前 2,325 行记录、54,831,012 字节；按上游案由筛得 12 个借贷父记录、168 条 QA | 脱敏输入适配、父记录去重、弱标签诊断与规模化读取 | 非随机抽样，不估计全量借贷比例或全量质量 |
| Spark 回放 | 168 条记录的原始字节哈希与解码正文哈希分别 168/168 对应；12 个归一化正文字段可回放 | 确认输出来自指定文件的指定字节和字段 | 不代表原站真实性、审核身份或 QA 答案语义正确 |

Spark 的正式大分片只做有界读取，没有重新计算全部 253 GB 文件的哈希。`declared_file_sha256` 仍是交付清单的声明，`file_hash_verified=false`；本次独立确认的是所选记录的字节片段哈希。筛选时最后一条用于判断父记录边界，因此检查了 2,325 行；其中 168 行入选。每个入选父记录观察到 14 条 QA，不由此证明任何父记录在全量中仅出现一次。

AppealCase 的来源版本与字段划分依据固定发布物。作者说明其文书经过规则分段，标签来自规则、模型与部分专家审核；本项目仍将这些标签与专家穷尽图谱标注分开。[固定版仓库说明](https://github.com/ythuang02/AppealCase/blob/9a62b2c3e8bb18c7c5a264c4df40b5578b18c990/README.md)、[论文构建方法](https://arxiv.org/html/2505.16514v1#S2.SS2)。

## 来源与偏移契约

`src/judicial_kg/sources/records.py` 生成带来源的文书记录。记录保留文件版本/哈希、JSON pointer、原字段文字、字段 SHA-256 和字符数。AppealCase 六字段保持分离，不补写裁判段落或冒称法院原始文书。Spark 使用已发布的脱敏 `text` 字段，记录上游脱敏方法和声明原始哈希，原文到脱敏文本的映射仍缺失。

证据跨度采用 Python Unicode code point 的半开区间 `[start, end)`，坐标空间固定为 `decoded_published_field_unicode_codepoints`。它是解码后的发布字段字符位置，不是 JSON 原文件字节位置、UTF-16 位置或法院原文位置。Spark 另行保存 JSON 记录的字节区间。`verify_evidence` 检查文书、字段、哈希、区间及引用完全对应；越界、空跨度、重复字段、字段变化和跨文书引用均报错。

所有记录的 `reference_status` 固定为 `not_graph_gold`。Spark QA 单独放入 `reference_labels`，状态为 `upstream_label_unverified`。原字段中的“人工已核验”不升级为项目审核结论；没有标签的部分不当负例。

## 案件分组与失败记录

AppealCase 分组使用作者的一二审配对、规范化原生案号、六字段精确内容哈希，以及文中提及的本语料已知案号，合并为 9,903 个保守组。观察到 32 个重复案号键、32 个重复字段内容键和 13,035 次指向其他文书的已知案号提及。案号提及只作为防泄漏的保守合组依据，不是法律上的同案判定，也不用于跨案合并人物。

全量来源检查中 2 份文书的元数据案号未在标题中匹配，保留为 `quarantine`，未进入本轮 24 份开发样本；失败项继续包含在总数和报告中。近重复、跨语料重叠与完整程序关联仍未完成，剩余文书标为 `unallocated_not_test`，不称为测试集。

历史开发暴露仅从旧清单提取文件哈希与记录位置，保存到 [development-exposure.json](../research/development-exposure.json)，不复用旧实现或旧成绩。开发暴露会传递到关联组。Spark 本轮 12 个父记录亦登记为开发暴露；其 family_id 仅表示来源父记录，不证明跨审级关联已完成。

## 复跑

当前本机目录为 `judicial-ontology-kg-current`，分支 `codex/source-adapters`。准备步骤不会调用模型或把数据上传至外部服务。

1. 确保固定版本原文件位于 `data/raw/appealcase/AppealCase.json` 和 `AppealCase.metadata.json`。本次从本机已有副本核验哈希后复制，仅复用来源字节；其他机器可按 `research/registries/round3.json` 的固定 URL 获取同版文件。适配器遇到哈希不符立即停止。
2. 如需 Spark 小样，运行下列命令，stdout 含选中原记录，必须重定向到忽略目录。
3. 执行统一准备命令和检查。

```bash
mkdir -p artifacts/source-development
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes spark-lan \
  'python3 - --root /work/dataset_shenbao/legal_qa_expanded_v1_1_b02 --max-parents 12 --max-rows 5000 --max-bytes 128000000' \
  < src/judicial_kg/sources/spark_scan.py \
  > artifacts/source-development/spark-capture.json

bash scripts/run.sh prepare-development \
  --spark-capture artifacts/source-development/spark-capture.json
bash scripts/check.sh
```

省略 `--spark-capture` 可以只准备 AppealCase。输出路径只允许在 `artifacts/` 或 `data/derived/` 下；相同路径的旧产物会原子替换，真实来源文件不改写。每次 `run.json` 保存本次代码版本和文件哈希；若工作区有未提交改动会明确记录。

Spark 解析器针对本交付物“一行一个对象、数组括号各占一行”的 JSON 格式，不伪装成任意 JSON 流解析器。遇到不支持的格式、超长单记录、冲突父文本、重复分片清单或解析错误会失败；到达扫描预算则明确记录停止原因。字节预算可多读一个探测字节。不会跳过坏记录后声称完整成功。

`src/judicial_kg/sources/spark_replay.py` 提供独立回放函数；从 stdin 接收 `root` 与只含文件路径、字节起止、预期记录哈希及文本哈希的 `references`，在源主机读取原文件，输出匹配计数。它不接收原文，也不输出正文。本次独立回放的结果附在上述机器报告中。

## 无用户人工 gold 的评测边界

继续实现最小本体、抽取契约、证据校验与图谱装配；不向用户索要专业法律标注。工程报告可以检查来源回放、结构约束、拒收、缺失输出和调用效率。已有上游标签只用于覆盖明确的局部对照，并注明标注方法与可信程度；弱标签对照应称一致性，不称人工 gold 准确率。

模型建议、规则推导及多模型一致结果保留为候选。若将来取得适用的独立标注，可增加相应范围的语义评测；目前完整图谱 P/R/F1 保持 `null`，不以模型自评替代。后续主要实施项是基于这些真实开发文书建立最小实体、提及、事件与带归属陈述契约，然后接入本地抽取服务。8K/16K 测速请求已由用户取消，本轮未做模型推理或测速。
