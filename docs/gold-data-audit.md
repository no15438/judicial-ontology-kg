# 已有标注能否用于新项目

核验日期：2026-10-04（Australia/Sydney）。结论：旧仓库存在局部标签和填写过的人工复核表，值得复用其来源线索和补标格式；目前没有足够证据认定存在可直接评测新图谱的完整 gold。最初云端核验未访问 Spark；本次 Mac 接续已通过 `spark-lan` 完成指定 QA 目录的小文件与样本核验，见下方补充。

## 实际看到的文件

固定旧提交：`d4614083e72e8d4f4d6543b818e1f8935634fec7`。本次只读 Git 文件并静态检查生成代码，没有运行旧管线、模型或重写标注。

| 资料 | 本次实查 | 可以支持什么 | 当前缺口 |
|---|---|---|---|
| `data/annotations/ljp_ground_truth.json` | 200 个记录；字段 claims、claims_labels、fact_labels | 在标签语义核定后做诉求支持/事实分类的局部诊断 | 数据集原标签，不是新实体/关系/事件跨度；MSJudge 的 -1 与重复列已知问题未解决 |
| `qa_v0.json`、`qa_v1.json` | 20/45 题；v1 说明保留 v0 的 20 题 | 已曝光开发样本上的问答与证据定位复查 | 数据说明承认来自重建文本；不相加为 65 道独立题；不是穷尽图谱标注 |
| `qa_v1_human_review.csv` | 48 行，30 个不同题 ID，涉及四种方法；三个人工字段均 48 行非空 | 旧问答输出评分的复核记录 | 行数是方法评分，不是独立案件；非所有 45 题全覆盖；复核者身份未独立验证 |
| `qa_v2.json` | 28 道跨案题；答案程序化派生，含 derivation/fact_basis | 旧问答任务的可追溯规则参考 | 使用原标签、法条规则及相似度启发式，不是全新人工图谱标注 |
| `qa_v2_human_review.csv` | 112 行 = 28 题 × 4 方法；人工评分/幻觉/备注字段全部非空 | 问答答案质量人工复核的既存证据 | 填写内容不等于原始事实与所有图谱关系已独立核验 |
| `event_annotation_sample.json` | 40 个案件 ID | 事件补标的抽样线索 | 当前提交仅有清单及 warning_rule_eval.json，未跟踪 event_annotation.csv |
| Spark QA 目录 | 实查 69 个文件条目、11 份小型证据文件及随附 100 条样本 | 来源和 QA 结构的开发核验候选 | 样本仅 8 个父记录且无民间借贷；人工身份、原站原文和授权待证实，不能升级为图谱 gold |

`qa_v2_human_review_README.md` 声称 112 行已逐条独立复核，并报告 111/112 与模型评分一致。这属于旧文档的记录；本次确认 CSV 字段已填写，但没有独立认证复核者身份、过程或一致率所代表的正确性。即使记录可靠，人工复核对象仍然是问答输出评分。

## 为什么不能直接称为图谱 gold

旧 `src/preprocess/build_raw_from_ljp.py::build_document` 把庭审、事实与诉求字段拼成近似裁判文书，加入虚拟编号、固定当事人段落，并由 claims_labels 生成“本院支持/部分支持/不予支持”的裁判结果句。它不是从法院下载的原始裁判全文；不能把由标签生成的句子再作为预测同一标签的独立真实性证据。

这不意味着所有上游字段都没有价值。后续可直接回到固定版 MSJudge 的真实发布字段，记录去分词/拼接变换和来源位置，选择语义明确的局部标签；应去掉模板补写部分作为原文证据的资格。当前 `-1` 及重复标签问题需要先明确，不能为了获得 gold 擅自解释或修复。

旧 `build_qa_v2.py` 明确使用原数据集标签及法条正则生成答案，类似案件参考使用事实标签 Jaccard。人工对回答打分不等于独立核验全部原标签，更不补全实体边界、关系端点、事件论元、陈述者、否定和穷尽覆盖。

旧 `build_event_annotation.py` 设计为 LLM 预填五维审计与漏抽候选，human 列留空；部分提示只传入前 2,500 字符的事实。即使以后在 Spark 找到该 CSV，也须检查人工列是否完成、完整原文覆盖和模型建议来源，不能把 LLM 预填当 gold，不能将未检查的后半文书当作无事件。

## 缺少人工资源时的可执行路径

1. 先完成来源适配与字段定位。可用 CJRC 训练侧的答案跨度验证回放；这是已覆盖答案的验证，不能计算全图谱召回率。
2. 将现有标签按类型、文本范围、来源、是否穷尽与人工核验状态登记。可靠局部标签支持局部任务；没有标注的输出列为未评估，不当负例。
3. 后续模型可以预标实体和事件，帮助集中人工精力，但保持 candidate 状态。若有少量人工时间，优先核验独立开发小样及争议条目；不要虚构已经有标注者。
4. 在人工 gold 暂缺时，报告证据可定位率、结构错误、处理失败、调用数与效率等工程指标，并明确这些不能代替语义 P/R/F1。正式评估继续记录人工标注与覆盖的缺口。

旧样本已经用于开发、问答实验且本次被审阅，不适合作新的盲测集。Spark 首轮盘点已完成；若后续取得原始采集、逐条复核与变换记录，再判断可恢复的覆盖，避免重复补标。

## 本地补充核验 Spark QA 交付物

2026-10-04 通过既有 `spark-lan` 连接 `spark-f74d`，只读检查 `/work/dataset_shenbao/legal_qa_expanded_v1_1_b02/`。机器结果见 [spark-qa-inventory.json](../research/spark-qa-inventory.json)。本次没有运行旧生成脚本、查询原始数据库、调用模型或更改服务。小文件与样本副本位于忽略目录 `artifacts/spark-qa-audit/source/`，不提交案件正文。

| 核验范围 | 本次结果 | 证据边界 |
|---|---|---|
| 文件清单 | 共 69 个文件条目；正式 `data/` 有 53 个 JSON，合计 252,819,887,125 字节 | 实测文件状态，不是重新解析全量记录 |
| 清单与历史验收 | 53 个路径对应；两份分片哈希声明一致 | 未重新计算 253 GB 分片的哈希；1,050 万 QA、75 万父文书仍是旧验收报告的数量声明 |
| 小文件 | 11 份说明、报告、清单和样本记录新 SHA-256；本机副本与远端相符 | 哈希只固定看到的版本，不证明报告中的质量与授权声明 |
| `data.7z` | 只列目录，53 个文件成员均为数据分片，路径和大小与现存分片一致 | 未解压、未做 CRC/内容校验；成员中未见独立复核资料 |
| 随附 sample100 | 100 个不同 QA ID，8 个不同父 source_id，8 个不同文本；7 组各 14 条，1 组 2 条 | 非随机样本；民间借贷为 0 条，不能推断全量没有借贷案件 |
| 样本工程检查 | 文本 SHA-256、长度、来源标准化哈希、单 QA、annotation 对应、全文对应均 100/100 通过 | 仅结构与字段一致性，不是答案语义正确率 |
| 文本变换 | 样本 100/100 的声明原始哈希与标准化文本哈希不同；说明记录姓名、单位等脱敏 | 未取得原始字节或原文到脱敏文本的偏移映射，不能称原站逐字一致 |

规范文档明确规定 `annotation.annotator` 与 `label_info.annotator` 固定为“行业领域专家”、`license` 固定为“公共授权”、`generated_data_indicator` 固定为 0。这些字段不是独立的人员、许可或非生成证明。样本另外声明 `human_review_status=人工已核验`、`label_method=multi_model_prelabel_human_review`，但交付目录没有找到可独立验证逐条审核者、审核时间与裁决过程的材料；不能由这几个字段推断人工审核真实完成，也不能据此断言从未做过人工审核。

样本 QA 的 `source_basis` 为“标注数据构造”51 条、“原始数据规则构造”49 条；全部 `qa_constructed_flag=true`。这些 QA 含 14 种字段任务，不能当作实体、关系、事件与陈述语境的穷尽标注。来源字段声称来自中国裁判文书网，所见 `source_id` 是 24 字符内部标识；本轮未核实其与法院原文的映射、采集依据或具体使用条件。

当前决定：保留为待准入的 QA/来源工程候选，不纳入正式图谱 gold，不替代 AppealCase 与 MUSER 的已有候选顺序。已查看的 8 个父记录按 `SHA256(source_id UTF-8)`、文本 SHA-256 与声明原始哈希登记于机器结果，之后排除盲测。后续若核验此数据，先寻找原始采集/脱敏映射、逐条审核轨迹与具体使用条件，再选取真实民间借贷开发样本；无需先下载所有展开行。

重复执行同一范围的核验（命令仅在本机写结果文件）：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes spark-lan \
  'python3 - --root /work/dataset_shenbao/legal_qa_expanded_v1_1_b02' \
  < src/judicial_kg/sources/qa_inventory.py > artifacts/spark-qa-inventory.json
```

该工具只读取固定路径的小文件，每个不超过 3 MB；对大分片只读取文件状态。字段缺失、无效样本或重复清单会报错，不把无数据视为通过。归档目录清单为另一次 `7z l -slt` 观察，未包含在上述工具的复跑范围。

## 复现与来源

```bash
python scripts/audit_legacy_annotations.py \
  --repo /path/to/judicial-graphrag \
  --output artifacts/legacy-annotation-inventory.json
```

本次结果保存于 [legacy-annotation-inventory.json](../research/legacy-annotation-inventory.json)，只含计数、字段名和源码哈希，不含问答正文或案件文本。脚本读取固定提交；它不证明人工身份、文本真实性或标签正确性。

一手代码：[重建输入](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/preprocess/build_raw_from_ljp.py)、[QA v2 生成](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/evaluation/build_qa_v2.py)、[事件预标](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/evaluation/build_event_annotation.py)、[QA 复核说明](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/data/processed/eval/qa_v2/qa_v2_human_review_README.md)。
