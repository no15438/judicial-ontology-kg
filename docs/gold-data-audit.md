# 已有标注能否用于新项目

核验日期：2026-10-04（Australia/Sydney）。结论：旧仓库存在局部标签和填写过的人工复核表，值得复用其来源线索和补标格式；目前没有足够证据认定存在可直接评测新图谱的完整 gold。Spark 当前仅有局域网连接，本次按用户选择先检查旧仓库，没有访问 Spark 内容。

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
| Spark QA 目录 | 仅在历史环境档案发现路径 | 待连接后核验 | 未访问、数量/来源/人工审核未知，不能据文件夹名推断性质 |

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

旧样本已经用于开发、问答实验且本次被审阅，不适合作新的盲测集。未来接入 Spark 后先盘点来源与 review/annotation 文件，再判断能否从已有人工作业中恢复覆盖，避免重复补标。

## 复现与来源

```bash
python scripts/audit_legacy_annotations.py \
  --repo /path/to/judicial-graphrag \
  --output artifacts/legacy-annotation-inventory.json
```

本次结果保存于 [legacy-annotation-inventory.json](../research/legacy-annotation-inventory.json)，只含计数、字段名和源码哈希，不含问答正文或案件文本。脚本读取固定提交；它不证明人工身份、文本真实性或标签正确性。

一手代码：[重建输入](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/preprocess/build_raw_from_ljp.py)、[QA v2 生成](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/evaluation/build_qa_v2.py)、[事件预标](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/src/evaluation/build_event_annotation.py)、[QA 复核说明](https://github.com/no15438/judicial-graphrag/blob/d4614083e72e8d4f4d6543b818e1f8935634fec7/data/processed/eval/qa_v2/qa_v2_human_review_README.md)。
