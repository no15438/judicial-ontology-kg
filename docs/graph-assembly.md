# 保留语境的候选图装配

2026-10-04 新增离线 `assemble-graph`。输入是本地 Qwen 等来源的候选记录和完整开发文书清单。重新执行原文、Schema、引用与语境校验后，只装配通过记录；不调用模型、不依赖数据库、不修改抽取结果。图谱仍是未经独立语义核验的候选，整体 P/R/F1 保持 `null`。

## 图结构与无损要求

实现位于 `src/judicial_kg/graph/`，图版本 `0.1.0`。每个输出目录是一份独立的候选运行快照；不将不同抽取轮次直接拼成一张图。

- Document 保存原候选的版本、文书与案组 ID、provenance 和 coverage。
- Actor、Mention、Context、Role、Event、Statement 各自成节点；节点键由文书 ID、概念类型和原局部 ID 的规范化元组哈希生成。同案组、同名或同脱敏占位符都不触发跨文书身份合并。
- 同一文书中完全相同的 EvidenceSpan 可以共用一个节点；每次引用仍有独立的 `supported_by` 边与原数组位置。引用文本、字段哈希、来源 pointer 和 Unicode 偏移不改写。
- Role/Event/Statement 的 `polarity` 保留在节点属性中，经 `in_context` 关联原 Context。`affirmed` 仅表示该语境里的肯定陈述，不是系统认定事件发生。
- 语境经 `attributed_to` 关联候选陈述者，经 `within_context` 保留嵌套。父语境不向子语境传播事实成立状态。
- Event 经 `participant` 关联 Actor，边上保留参与角色和数组顺序；Statement 经 `about` 关联原主体或事件。不会压平成不带语境的主体间事实边。
- `has_mention`、`denotes`、`role_of` 和 Document 的 `contains` 保存其余原引用。每个候选节点保留其在原记录中的 JSON pointer。

装配后由图反向还原完整候选记录，比较规范化 JSON 哈希，包括数组顺序、属性值、未知归一化值和来源信息。额外边、悬空边、跨文书边、错误节点类型/位置、重复节点、篡改状态均不接受。某篇装配失败就丢弃该篇全部图元素，并记为 `assembly_failed`，不能把丢失节点静默排除后报成功。

`restore_records` 只检查图序列化与映射完整性；单独读取外部图时仍需固定输入/输出哈希，并对还原记录及原始来源执行契约校验。可无损还原不表示原候选语义正确。

## 运行与退出状态

```bash
bash scripts/run.sh assemble-graph \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --candidates artifacts/qwen-extraction/typed-v1-replay/candidates.jsonl \
  --output artifacts/graph/local-qwen-v1
```

输出目录必须不存在，且只能位于 `artifacts/` 或 `data/derived/`：

| 输出 | 内容 |
|---|---|
| `graph.json` | 可还原候选记录的节点和边，含原文证据，仅留本地 |
| `diagnostics.json` | 逐字段证据覆盖区间、重复字面出现的位置及归属检查提示 |
| `ledger.json` | 所有预期文书、输入尝试、原校验状态、装配结果与还原哈希 |
| `report.json` | 代码/本体/输入输出哈希、环境、计数和诊断汇总，不含原文与候选标签值 |

任何输入尝试被拒收、预期文书缺失或装配失败，CLI 返回 1，仍完整保存账本。全部预期文书通过才返回 0；明确空候选另计 `assembled_empty`。不能删掉未抽取文书来使本轮完整分母看起来全部通过。

## 诊断的含义

`candidate-diagnostics-v1` 计算各字段中 Mention、Context 以及 Role/Event/Statement 证据跨度的区间并集。重叠跨度只计一次；分母包含该字段全部 Unicode 字符，包括标点。`processed_fields` 是模型报告的处理范围，不能据此推断穷尽抽取。

另外寻找已输出的、至少两个字符的 `name` 提及文本在原文中的精确重复出现，记录未被现有 Mention 精确覆盖的坐标。同一坐标只计一次；姓名可能是别名、子串或不同主体，因此该计数既不是确认遗漏，也不是语义 recall 分母，不自动补提及或共指。

启发式提示覆盖：同字面名称关联多个 Actor、当事人语境中出现“共同辩称”等联合表达、`court_finding` 语境中出现证言/转述线索。提示只供进一步诊断，可能误报或漏报，不改写 Context 类型、归属、接受状态或 gold 标记，也不让用户承担专业法律标注。

## 开发验证与边界

首轮使用同两篇本地 Qwen v5 原响应经修复重放后的候选，不是新增推理样本。完整预期清单仍为 36 篇，34 篇未抽取；原始失败与重放记录保持原样。固定运行证据见 `research/graph-assembly-report.json`。

元数据测试覆盖无损还原、同案组同名隔离、主张/认定/嵌套引用分离、否定保留、共享证据、多角色参与、损坏边拒收、装配丢失记账、缺失/重复/空输出、来源变更拒收及本地输出保护。测试文本是元数据夹具，不是司法原文或 gold。

图谱目前没有执行主体身份消歧、联合陈述者修正、事实采信裁决或法律推理；没有数据库、正式测试划分或独立语义参考。后续先修正抽取契约对联合归属和多次提及的表达，再通过本地 Qwen 对已曝光开发样本验证。不能用当前图规模、结构通过率或证据字符覆盖声称语义质量提高。

固定运行 `be6f338` 得到 524 节点、994 边（其中 326 个独立证据节点、462 次证据引用），2 篇写盘还原后重新通过原文契约校验；34 篇缺失导致预期退出码 1。重复字面出现线索 85 处，联合归属提示和转述语境提示各 1 处。19 项新增测试及全套 184 项通过。原始图与逐篇诊断仍在 `artifacts/graph/local-qwen-v1/`；示例命令复跑时需更换新的输出目录。
