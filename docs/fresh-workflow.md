# 固定流程从原文自动运行

用户验收标准：固定代码/提示/契约/配置后，从原文启动，自动完成抽取、校验、预设的有界纠正、零调用回放和图谱装配；中途不改代码，不读取历史候选或复用旧响应，不手工编辑图谱。

`judicial_kg.extraction.fresh_workflow` 将这些步骤串为一次调用。历史运行目录仅供累计费用核算。运行前保存来源和实现文件哈希，抽取后、装配前后重复核验；发生变动不能报告本次自动流程通过。失败原响应和分母保留，程序不会整篇自动重试或让失败候选进入图谱。

```bash
PYTHONPATH=src .venv/bin/python -m judicial_kg.extraction.fresh_workflow \
  --root "$PWD" \
  --documents "$PWD/artifacts/scoped-v12/spark-documents.json" \
  --documents "$PWD/artifacts/scoped-v12/appealcase-documents.json" \
  --version 31 --budget-cny 200 --execute \
  --output "$PWD/artifacts/bailian-extraction/fresh-v31-01" \
  --previous-run /absolute/path/to/prior/extraction
```

实际执行必须为每份既有计费报告重复提供 `--previous-run`，包括其全部祖先；示例最后一行不是完整账本。输出目录必须是全新的忽略目录。当前有界选样仍为输入顺序第一篇Spark4396，完整36篇保留35篇未选中，不表示全量运行。

报告分别定义：

- `automatic_pipeline_accepted`：此次单次固定流程从原文完成；允许已经写入程序的有界纠正，禁止跨运行恢复和人工修补。
- `uncorrected_first_pass_accepted`：自动流程通过，且没有追加模型纠正。机械性的逐字查询去重保留回执，不合并原文中的不同出现。
- `semantic_precision_recall_f1`：没有独立完整gold，继续为空；工程通过不是法律语义准确率。

运行期间不改变冻结文件。若失败，应在该轮结束并回放之后修复通用实现，记录新提交，再从原文开启新轮。旧失败不能被覆盖，续跑成功也不能冒称新首轮。
