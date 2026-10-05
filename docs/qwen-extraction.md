# 本地 Qwen 抽取

实现位置为 `src/judicial_kg/extraction/`，v5 使用开发版 `0.1.0`，v6/v7/v8 使用 `0.2.0` 本体与最终抽取契约。此阶段是本地模型候选抽取与工程验证，不是专家 gold 或图谱语义准确率评测。

## 连接与运行限制

通过已有 `spark-lan` SSH 连接，在 Spark 内用标准库访问 `127.0.0.1:8003` 的 llama.cpp 服务。请求体通过 SSH stdin 传递，不放到命令参数，不在远端创建脚本或数据文件，不配置公网隧道或收费云 API。当前模型别名为 `qwen3.8-flash-next-iq4xs`；每次运行重新读取健康、模型目录、服务配置和负载，未配置/不可达时明确失败。

用户最新决定是后续使用本地模型；百炼试跑已结束，不自动发起新付费调用。`configs/qwen-extraction.json` 当前为 v5：单路串行，每个来源前 1 篇、最多 2 篇，最多输出 8,192 tokens，温度 0.2、seed 42、关闭 thinking。此上限用于避免抽取输出截断，不是已取消的 8K/16K 性能测试。完整预期文书清单不随抽样缩小；未选中的 34 篇仍记录 `not_selected`，在统一验证器中继续属于缺失候选。

每篇先调用服务实际模板与 tokenizer，要求输入 tokens + 预留输出 + 128 tokens 余量不超过配置的 32K。超预算就拒绝该篇，不静默截断原文。服务忙时也不插入推理。请求超时/SSH 状态不明时停止后续提交，避免未知在途任务叠加；没有自动重试、服务重启或配置改动。

## 模型与程序各自负责什么

当前提示文件为 `prompts/qwen-extraction-v5.txt`，配置版本 `qwen-extraction-v5`。只发送带编号的原文片段，不发送上游 QA 答案、标签或历史候选。程序无损划分原字段，不改变任何字符；片段只是位置索引，不是法律语境分类。模型仍需读取全部片段，判断说话者和主张、抗辩、法院认定、转述、裁判之间的区别。

模型仅生成 `Mention.actor_id` 正向关联，程序从实际提及生成 `Actor.mention_ids` 反向索引。此索引是确定性数据结构，不是新的语义标注；主体类型、共指、语境和事件仍由模型提议。相同主体的相同证据位置重复生成 Mention 时拒收，不静默去重或补造占位提及。

Mention 用 `unit_id / text / occurrence` 选择片段内的一次精确提及；重复名称跨片段时由片段 ID 消歧。片段内部重复仍需明确 occurrence，不能默认选第一次。Context 选择 `evidence_units`，Role/Event 嵌套在其所属 Context 内，事件属性嵌套在 Event 内；程序生成 context_id 与事件属性 subject_id，拒绝越出所选语境的证据。Context.statements 只允许主体属性。

属性值只选择原文 `source`。固定 predicate 的类型由本体映射确定，仅 `other` 仍由模型明确选择 kind。模型不能另填 normalized/unit。程序只将明确完整的数字年月日转成 ISO 日期，不补全月份、不推算期限；金额仅解析明确的数字、千位分隔和万/亿单位，利率仅解析明确百分数，使用 Decimal 保持精度。不能明确解析时保留 raw 与 null；金额原文未明确人民币单位时不凭数字推定币种。

如果模型额外重复填写固定 predicate 的 kind，编译器只接受与本体映射完全一致的类型，冲突类型仍拒收。归一化字段仍禁止由模型填写。这样不会因正确的冗余类型字段而拒收整篇，也不会静默改正矛盾类型。

程序不模糊匹配、不修改原文、不自动扩充语境、不删除难处理节点。属性 raw 必须来自选中片段，所有引用和原值逐字回放后才生成证据跨度。默认 v5 的最终本体/契约保持 0.1.0，v6/v7/v8 使用 0.2.0；严格校验规则没有放宽。定位失败会报告全部已发现错误；未完成的后续语义/契约检查不能假定通过。

服务端使用 `json_object` 约束 JSON 语法，模型草稿 Schema 放在提示中并在本地严格验证。旧 v1/v2 的提及编号循环、v3 的扁平引用协议和 v4 的片段/嵌套协议保留用于复现，不覆盖旧失败。`configs/qwen-grounded.json` 固定 v4，默认命令使用 v5。

所有主体引用、语境范围、陈述归属、日期和类型边界仍由本地校验器检查。`finish_reason` 非 stop、截断、空内容、拒绝、错误模型别名、JSON 错误、证据不匹配或契约错误都单独记账，不丢弃后重新计算通过率。模型未覆盖到的事实无法用结构检查发现。

## 执行

```bash
bash scripts/run.sh extract-qwen \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json
```

默认输出为 `artifacts/qwen-extraction/<UTC时间>-<随机ID>/`。也可显式提供 `--config` 和 `--output`；输出目录必须是新的 Git 忽略目录，不覆盖任何既往试跑，包括失败试跑。不要复用旧的 pilot 目录。

该命令确实提交本地推理请求。`validate-extractions` 仍是纯离线校验。输入只允许已曝光的开发文书；不能将正式测试集用于改提示或本体。

编译器修复后可离线重放已完成的 v5 运行，不再次推理：

```bash
bash scripts/run.sh replay-qwen \
  --source-run artifacts/qwen-extraction/typed-v1 \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --output artifacts/qwen-extraction/typed-v1-replay
```

重放要求完整输入清单及其原哈希一致，检查保存的请求/响应哈希和逐篇原文绑定。输出必须是新目录，报告记录原运行 ID、原报告/响应哈希、编译代码与规则版本、原拒收状态和新结果；模型请求数恒为零。不会修改原响应、原报告或缩小文书分母，重放通过也不记作新的独立模型试验。

## 输出与分母

| 文件 | 内容 |
|---|---|
| `rpc-*.request.json` / `rpc-*.response.bin` | 每次服务调用的请求及原始响应字节，包括失败；含原文，只留忽略目录 |
| `*.diagnostics.bin` | 如有，保存 SSH 诊断；不直接输出到公共报告 |
| `selected-documents.json` / `prompt.txt` / `config.json` | 本次选中原文、提示、运行配置 |
| `candidate-*.json` / `candidates.jsonl` | 引用已定位后的候选；契约拒收项仍保存 |
| `accepted.json` | 整篇契约通过的候选，不是事实真值 |
| `outcomes.json` | 每篇预期文书的选择、处理状态、输入 token、调用耗时、usage 和失败位置 |
| `validation-ledger.json` | 统一契约校验器的完整文书与尝试账本 |
| `report.json` | 代码、依赖、本体、提示、配置、输入输出哈希，以及计数与请求状态；无案件正文 |

每次请求前后保存检查点。被中断的运行保留 `completed=false` 与 `in_flight`；不能据此重试而假定远端已经停止。`completed=true` 仅表示本轮流程终结，仍需查看逐篇状态。CLI 返回 0 表示**所选**文书全部完成契约检查；不意味着完整预期清单已抽取，空候选也单独计数。

运行记录保存模型目录/配置哈希、模型别名、build_info、上下文上限及归一化规则版本；尚未重算所有 GGUF 权重哈希，因此明确保留 `model_weight_hash_verified=false`。源片段映射与归一化是程序派生，主体身份、共指、语境、事件与 predicate 仍是模型候选。温度与种子固定不构成跨硬件、跨服务版本完全确定性的承诺。

耗时与 token usage 用于诊断本次抽取成本，没有执行用户已撤回的 8K/16K 性能基准。没有完整独立标注时，precision、recall 和 F1 保持 null；不向用户索要专业法律标注。

当前 Mention 和属性原值选择器要求文字落在单个片段内；跨片段值尚未实现。联合陈述的多个归属主体、证据转述与法院采信的细分也不能靠位置校验解决。不得将这些未覆盖内容算作已验证正确，或因候选通过就把开发样本升级为 gold。

## 已定位的问题

离线重放四份百炼响应发现 10 处可检测错误：重复引用未消歧 2 处、引用选错字段 1 处、证据越出语境 5 处、属性原值不在证据中 1 处、事件属性语境错接 1 处。定位失败的两份响应尚未执行后续契约检查，因此 10 不是其全部语义错误数量。原响应与原失败报告不变，新增诊断见 [extraction-grounding-diagnosis.json](../research/extraction-grounding-diagnosis.json)。温度 0.7 与 0.2 的结果不支持将原因简单归于温度。

v4 在干净提交 `334bb6f` 用本地 Qwen 重跑同两篇：均完整返回；一篇通过，另一篇仅剩两处 predicate/value 类型冲突和一处不完整日期被补成具体日期的问题。首篇保留 17 个主体、9 个语境、12 个事件、17 条属性和 276 个证据跨度，并非空候选。它仍存在主体简称归属等语义疑点，结构通过不证明语义正确。v5 进一步把类型和保守归一化交给程序，避免重复询问模型可确定的字段；没有删除旧错误记录或把失败改写成成功。

v5 推理固定在 `793ed34`，两篇均完整返回；一篇通过，另一篇因四个正确但冗余的 kind 字段拒收。`cc72332` 修复兼容性后，原响应离线重放均通过，合计 29 个主体、22 个事件、56 条属性、462 个证据跨度；34 篇未选中仍缺失。5 条日期表达保留原文且 normalized=null。重放没有再次调用模型，不能算作独立样本。原失败与修复后结果分开保存在 [qwen-grounded-report.json](../research/qwen-grounded-report.json)，所有正文仍在忽略目录。165 项工程测试通过；语义准确率与完整性没有获得独立验证。

后续候选图装配已实现：同两篇修复重放输出在 `be6f338` 无损装配为 524 节点和 994 边，并从写盘图还原原记录后重验全部 462 次证据引用。无新增推理；34 篇缺失仍在分母。新的重复字面出现及联合/转述归属诊断仅作为本地抽取改进线索，见 [候选图装配](graph-assembly.md)。

## v6 归属与覆盖开发回归

`configs/qwen-contextual.json` 显式启用 v6 / 契约 0.2.0；旧默认 v5 保留。新提示要求列出共同陈述者、不同位置的可确定提及，以及法院叙述中的独立引述语境。旧提示曾允许省略重复提及，因此旧 85 处字面出现线索不能归因于模型单独失误。

程序仍不自动补 Mention、猜主体或重分语境。新轮温度保持 0.2，输出预留从 8192 调至 12288 以容纳更多提及；不是单因素对照或测速。运行前检查现有服务健康、模型与空闲状态，不修改服务。

```bash
bash scripts/run.sh extract-qwen --config configs/qwen-contextual.json \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --output artifacts/qwen-extraction/contextual-v1
```

新轮所有原始响应、失败和候选保留在新目录；完整 36 篇分母不变。联合归属、提及或嵌套数量增加本身不构成质量提升；仍需逐项依据原文检查并保留未解决疑点。

## v6 两篇回归结果

固定 `47cfa9e` 的两次调用均完整返回：1 篇通过、1 篇因 6 处提及定位问题拒收。36 篇分母保留 34 未选中。通过文书的 16 处旧重复名称线索都有新版对应，但另有 3 处旧提及没有新版重叠提及；不宣称整体覆盖提高。失败草稿中的双主体和嵌套引述不是已接受成果。详见 [报告](../research/qwen-contextual-report.json)。默认 v5 保持不变。

只读比较命令会先验证候选和原运行哈希、逐篇来源，再同时保留抽取失败状态与下游缺失数量。输出只写新的本地忽略路径：

```sh
PYTHONPATH=src .venv/bin/python scripts/inspect_contextual_run.py \
  --before artifacts/qwen-extraction/typed-v1-replay/candidates.jsonl \
  --after artifacts/qwen-extraction/contextual-v1/candidates.jsonl \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --output artifacts/qwen-extraction/contextual-comparison-new.json
```

新 0.2.0 候选离线装配须显式传 `assemble-graph --contract-version 0.2.0`；本轮 1 篇生成 204 节点/431 边，168 次证据还原。未修改旧 Neo4j 图。下一步处理重复引用序号、片段定位漂移及旧提及未保留，再决定扩批。

## 提及的有界修复

新增 `repair-qwen-mentions`，只处理已完整保存的 v6 草稿和已验证来源的旧候选覆盖线索。先核对完整 36 篇输入、原请求/响应及旧候选哈希，再列出真实来源位置供本地 Qwen 选择。旧候选不作为 gold。当前修复协议为 `qwen-mention-repair-v5`，与抽取 v5/v6 是不同版本序列；早期修复配置留作历史，使用相应 Git 提交复现。

允许的变更仅包括：选择明确原文位置；把误展开的名称改回原片段内唯一匹配的代词/描述；明确标记同主体同文字的重复提及；依据旧位置复核补提及和新主体。已有 Actor 身份/类型、Context、Role、Event、Statement 和极性均不能被修复响应改写。缺失/重复决策、目录外位置、不唯一引用和未知主体均拒收；不确定项仍保留失败，不能选第一个或最近位置制造通过。

```sh
bash scripts/run.sh repair-qwen-mentions \
  --source-run artifacts/qwen-extraction/contextual-v1 \
  --candidates artifacts/qwen-extraction/typed-v1-replay/candidates.jsonl \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --output artifacts/qwen-extraction/mention-repair-new
```

`repair-plans.json`、`original-drafts.json`、原始修复响应和 `patched-*.json` 均保存在新忽略目录；报告另记原运行状态、修复决策计数和哈希。局部修复不是新的独立文书样本，也不是独立法律复核。

真实五轮局部修复共 10 次本地调用：前两轮暴露位置冲突和不确定归属；第三轮工程通过但提及边界有误；第四轮因提示歧义拒判；第五轮修正提示并用唯一原文文字定位，两篇通过。最终两处代词/描述边界已对照原文检查，Spark 的 3 处旧提及补回；29 个旧提及位置均有新版重叠，85 处旧重复名称线索中 74 处有精确对应，剩余 11 处仍须分层检查。它们不是语义 recall。

最终图 778 节点/1,581 边、623 次证据引用，写盘还原、原文重验和 CSV payload 比对通过；未覆盖旧 Neo4j 展示。215 项工程测试通过。完整历史见 [提及修复报告](../research/qwen-mention-repair-report.json)。


当前版本修复决策可离线重放，核对原文清单、原 v6 草稿、旧候选、修复计划及逐篇请求/响应绑定；不调用模型。重放同时比对候选内容和逐篇状态，不能只以空输出一致冒充成功。

```sh
bash scripts/run.sh replay-qwen-mentions \
  --source-run artifacts/qwen-extraction/contextual-v1 \
  --repair-run artifacts/qwen-extraction/mention-repair-v5 \
  --candidates artifacts/qwen-extraction/typed-v1-replay/candidates.jsonl \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json \
  --output artifacts/qwen-extraction/mention-repair-replay-new
```


## v7 主体内嵌提及草稿（两篇失败回归已完成）

新增 8 篇 v6 开发扩展的首篇在 12,288 输出 tokens 时截断。原始响应一直停留在根级 actors 数组，已枚举 1,030 个不同主体 ID，尚未进入 mentions 或 contexts。该响应不生成候选；不能将此失败当作普通正文过长并继续增加输出预算。

`configs/qwen-actor-grounded.json` 显式启用 v7：每个 Actor 必须内嵌至少一个有原文选择器的 Mention，草稿不再分开输出根级 mentions。程序据所属 Actor 填写 mention.actor_id，随后走相同的 v6 严格编译及 0.2.0 最终契约。没有设置任意主体数量上限、自动合并或删除主体，也没有放宽证据和语境要求。保存响应可通过 `replay-qwen` 离线重放；现有局部提及修复命令仍只支持原 v6 草稿。

226 项工程测试通过，包括嵌套草稿等价编译、空壳主体拒收、冲突身份拒收和保存运行离线回放。`c7be724` 上针对两个来源各一篇 Actor 枚举截断样本做串行实跑，两篇均完整返回；分别仍有 16/3 处定位问题，继续拒收。该结果只说明这两个开发失败样本不再发生原先的编号枚举截断，不能推广为所有文书已解决，更不能称为语义质量通过。两篇原响应及零调用离线重放保存在 `artifacts/qwen-extraction/actor-grounded-truncated-pair-v1/` 及对应 `-replay/`。


## v6 冗余主体类型兼容

扩展样本出现 Mention 额外填写 `actor_type` 的响应。编译器仅当其引用恰好一个 Actor、类型逐字相同时移除冗余字段，再执行全部原有校验；不新建或合并主体，不覆盖冲突类型，其他未知字段仍拒收。提示 Schema 保持主体类型只在 Actor 上声明，原响应不改写。新增 5 项元数据回归测试，全套 226 项通过。修复不保证该篇其他定位/语境问题也已通过，应以保存响应的离线重放结果为准。


## 十篇开发范围的当前结论

新增 8 篇 v6 首轮为 1 通过、5 拒收、2 截断；两篇截断经 v7 完整返回后仍有定位问题。加上已修复的原两篇，当前 10 篇里 3 篇工程通过、7 篇拒收，余下 26 篇保持未选中。全部失败保留，默认 v5 不自动升级。合并的 3 篇候选图为 997 节点、2,082 边、829 次证据引用，来源和 CSV 保真验证通过，不是同协议的语义性能基线。下一步扩展有界引用修复到没有旧候选的新文书及事件属性，先验证固定失败清单，再考虑扩量。见 [扩展元数据](../research/qwen-development-expansion-report.json)。

## 有界语境与不支持提及复核（最新）

代码33e347f实现独立归属复核协议；df92b17补齐属性定位错误不进入主体复核的边界。全套259项测试、Ruff/格式、9项参考哈希与diff检查通过，新增16项测试。原文、Actor、金额、事件、极性、归属主体和语境范围不可改；只可有限调整语境类别，或明确拒绝目录中无原文支持的姓名提及。原提及及模型理由保留独立rejected-assertions账本；uncertain仍拒收，模型拒绝不等于gold。

同两篇真实本地回归在干净33e347f完成：2次调用、温度0.2/seed42，累计34.714秒；AppealCase两处语境重新分类，Spark三处不支持提及明确拒绝并留档。两篇完整契约均通过、506次证据引用回放。离线重放0调用，候选、状态与拒绝账本完全一致；原始草稿/响应不改写。累计10篇尝试中5通过/5拒收/26未选中。

5篇候选合并装配为1602节点/3211边、1335次证据引用；写盘还原、来源重验和CSV逐条payload保真通过。artifacts/graph/development10-v2和artifacts/neo4j/development10-v2仅本地导出，未加载Neo4j。装配缺失31篇=5篇当前拒收+26篇未选中，抽取历史失败不从分母消失。公共结果research/qwen-attribution-repair-report.json。

接着修复剩余5篇已曝光失败文书：原v6/v7响应固定，明确选择且完整36篇分母，有限位置修复先行；若仍失败，按新错误分层处理，不自动换主体、扩大语境或新增案件。该批是否完成以新运行报告为准，不重复启动在途目录。无需用户法律gold，语义P/R/F1保持缺失。

复现（每次使用新的output目录）：
```bash
bash scripts/run.sh repair-qwen-attribution \
 --documents artifacts/source-development/appealcase-documents.json \
 --documents artifacts/source-development/spark-documents.json \
 --repair-spec artifacts/next-round-audit-v1/pilot-spec.json \
 --source-run artifacts/qwen-extraction/selector-repair-pilot-v1 \
 --config configs/qwen-attribution-repair-v1.json \
 --output artifacts/qwen-extraction/attribution-repair-pilot-v1
```
离线使用replay-qwen-attribution、相同来源/spec，并指定--repair-run和新的--output；不会调用模型。

## 剩余5篇有限位置回归已完成（最新状态）

5次本地串行调用已结束，累计255.657秒：1新增通过、3定位拒判、1完整契约拒收；31篇本轮未选中。零调用重放候选与全部outcomes一致，原失败/原响应不可变。运行时工作树有上轮结果元数据未提交，但全部源码字节哈希与记录的df92b17提交一致，不能误记为干净运行。公共记录research/qwen-remaining-five-repair-report.json。

累计完整36篇：10篇曾尝试，其中6通过、4拒收，另26未选中。6篇候选装配1942节点/3950边、1642次证据引用，写盘还原、来源重验、CSV逐条payload一致。artifacts/graph/development10-v3与artifacts/neo4j/development10-v3为最终新快照，尚未载入Neo4j。装配缺失30篇=4拒收+26未选中。无推理在途，不要重复启动selector-repair-remaining-five-v1目录。

下轮固定4篇，不新增案件：
1. AppealCase 026b…：7提及和1事件属性拒判；当前归属/不支持姓名目录均无可修项目，需要局部证据重新分析，属性仍限原语境。
2. AppealCase 442c…：5提及拒判；另有3个候选语境复核项目、2个不支持姓名目录项目，不能把目录当正确决策。
3. Spark 4396…：2提及拒判，其中1个符合不支持姓名复核目录；另1个仍需来源核查。
4. Spark 6b9b…：a9 organization无提及、无语境/角色引用；下轮实现有界主体保留/拒绝复核前，不能自动删除、补姓名或放宽minItems。

优先补齐已确定位置与未决项的分阶段账本：只保留明确模型位置选择，未决项保持失败，不重新猜已解决位置；测试不可变语义、来源和回放后，才局部回归上述4篇。原始断言、拒绝原因及完整分母持续保留，语义P/R/F1仍为空。

## 分阶段局部复核与法院主体类型修复（最新状态）

代码9893e35/bd275e0实现零调用分阶段选择器账本及局部复核；cc849ba补齐法院主体类型有限更正和局部明确决策留存。全套288项测试、Ruff/格式、9项参考哈希及diff检查通过。本轮新增29项测试，另扩充v6/v7集成的分阶段回放检查。固定原响应、完整36篇分母，已确定18项位置选择保留，15项拒判单独记账；阶段产物不是接受候选。

固定4篇本地Qwen复核4次调用、154.241秒：2新增通过、2拒收。省略主语不得编成姓名Mention；真实代词/描述与全角空格按原文字面选取；无提及且无引用的空壳Actor仅在明确模型拒绝后排除，原断言和理由留账，不是gold。日期/语境拒判不删除。

另外1次法院类型复核12.677秒：Actor a10的既有原文名称是法院机构，但类型错为person；明确模型判断后仅更正actor_type为court，ID、Mention、语境/归属及事件不变。此前明确局部选择与2条拒绝断言继续保留，三个法院语境均通过完整契约。两个新协议均零调用重放候选/状态完全一致，拒绝账本字节相同。原失败记录不改写。

累计10篇尝试：9工程通过、1拒收，26未选中。9篇候选装配3200节点/6464边、2708次证据引用；写盘还原、来源重验、CSV payload一致。新快照artifacts/graph/development10-v4及artifacts/neo4j/development10-v4尚未载入Neo4j。装配缺失27篇=1拒收+26未选中。图装配时仅结果元数据未提交，所有源码哈希与cc849ba一致，报告不冒记干净工作树。无推理在途，不重复旧目录。

剩余单篇AppealCase 026b…：事件属性s2的日期在其原语境没有字面来源；匹配原文在其他语境。模型仍拒判“是否同一事件”，不能自动搬来源、扩大语境或删除未决日期换取通过。7条Mention决策（4明确拒绝、3局部修正）作为未接受阶段保留，不计新图谱成果。对应来源对照/未决账本在忽略目录artifacts/next-round-audit-v1/remaining-date-lineage.json。

下一步优先核查日期原语境的事件身份和陈述者归属；如果缺乏可证明的关联则保持拒收，并将相应属性留在未支持断言层。之后把“不得编造省略主语Mention、保留名称全角空格、法院机构类型前向检查”的约束纳入新版本首轮提示，先在固定10篇回归，暂不新增案件或改变默认v5。独立法律gold缺失，语义P/R/F1仍为空；用户无需专业补标。

公共结果research/qwen-local-review-report.json、research/qwen-actor-type-review-report.json；原文/QA/响应/派生草稿/图谱仍不进Git，已授权可上传原Google项目文件夹。

## 日期陈述来源错接修复（最新状态）

最后单篇的日期并非不存在：原文日期属于法院认定的转账事件，另有法院语境的独立日期属性；草稿却把它放进当事人的借贷主张。此轮未扩大语境、改写日期或把法院认定冒充当事人陈述。d2bb5ad实现仅属性语境复核，8bcd087将单项输出预算限为1024。初次预检因28828输入+4096输出预算超上下文拒绝，0推理请求，原失败目录保留。

干净8bcd087上实际1次本地Qwen调用42.283秒，模型明确reject_outside_context，将当前主张中无证据属性排入独立拒绝账本，保留原值/原模型响应/理由。这不表示该日期虚假，也不是人工gold。此前4条省略主语姓名断言拒绝、3条局部提及修正继续保留；法院Context、日期、金额和事件逐字段完全不变。完整契约通过；零调用重放候选/状态一致，拒绝账本字节相同。全套293项测试及Ruff/格式、9参考哈希、diff检查通过。

累计36篇完整分母：固定10篇曾尝试，现10工程通过、0当前拒收、26未选中；历史拒收/截断不改写。10篇装配3843节点/7631边、3183次证据引用，写盘还原、原文重验、CSV payload一致。新快照artifacts/graph/development10-v5及artifacts/neo4j/development10-v5未载入Neo4j。最新元数据research/qwen-property-scope-review-report.json；原始文书/QA/响应/派生图仍不进Git。推理全部结束，不重复property-scope-review旧目录。

这是工程失败回归通过，不能报告整体语义准确率/P/R/F1或完整覆盖。下一阶段先把省略主语提及、全角空格、法院类型、陈述者局部属性约束纳入新版本首轮提示；在固定10篇回归后才考虑扩量，默认v5暂不改。同步开展主体—事件参与角色与转账/借贷事件一致性诊断，避免把结构通过当角色正确；模型复核和疑点保持独立标记，用户无需法律gold。OpenJev与新增案件不提前启动。

## v8从原文重新抽取回归（进行中）

用户要求检验首轮能否工作，旧10篇通过是修复后候选，不作为本轮结果。已提交df23553：v8首轮加入省略主语/逐字空格、法院类型、陈述者属性边界及角色方向约束，沿用v7嵌套Actor/Mention输出和严格0.2.0契约，无自动删除/放宽校验。首次v8单元/来源重放检查295项通过；新增首轮报告分母检查后全套304项通过。默认v5未切换。

固定既有10篇开发文书（AppealCase5+Spark5），全36篇仅重排清单，无新增案件/原文改写，选样在请求前登记artifacts/fresh-first-pass-v8/selection.json。请求只含原文source_units，QA、旧候选、修复决定不发送。温度0.2、seed42、输出12288、串行、本地Spark Qwen；代码运行固定于干净df23553。

artifacts/qwen-extraction/fresh-first-pass-v8当前在途，不重复启动该目录。先完整完成10篇新抽取与严格校验，再零调用回放，分别报告首轮候选通过/空输出/失败、未选中26篇；后续修复另列，不能用旧修复通过充数。新图若导出亦与旧修复10篇快照隔离，不自动载入Neo4j。语义P/R/F1仍为空，已曝光开发回归不是盲测。

## v8首轮完整回归与v9词表修复准备

v8固定10篇从原文重新生成已结束，执行为干净df23553：10次本地调用，3首轮候选通过、6定位/Schema拒收、1截断、0空输出；完整36篇分母保留，26未选中。累计模型请求3135.807秒。严格首轮通过率30%仅为工程通过率，不是法律语义准确率；旧修复10篇不计入本轮。

已零调用回放，接受候选和逐篇状态/问题一致；3篇新候选独立装配819节点/1503边、706次证据引用，保存还原、原文重验及CSV payload比对通过。新路径artifacts/graph/fresh-first-pass-v8及artifacts/neo4j/fresh-first-pass-v8未载入Neo4j，旧图哈希未变。公共元数据research/qwen-fresh-first-pass-v8-report.json。

主要首轮问题为重复文字未指定序号、姓名/属性选错片段、事件证据越出语境、输出截断，以及Role.role误用事件参与者词表claimant/respondent。v9代码68c365e已加入明确词表区分和服务端结构Schema约束，并将片段ID枚举绑定当前原文；唯一性、predicate条件、引用和逐字证据仍由完整本地校验，服务端失败不回退到无约束生成。本体0.2.0/编译器严格规则未放宽。全套322项测试通过，默认仍v5。

当前准备串行重抽固定的一篇词表错误短文书：configs/qwen-constrained-pilot-v9.json，选样在artifacts/fresh-first-pass-v8/v9-pilot-registration.json登记；只发送原文，不发送旧候选或修复答案。该单篇是已曝光错误回归，不能外推10篇稳定性，结果不得改写v8首轮3/10。定位与语境错误仍需分别有界修复，不新增案件或OpenJev，整体语义P/R/F1为null。

## 本轮修复已完成：新首轮3/10，后续新增1篇接受

v8固定10篇从原文重抽的首轮结果保持3通过、6校验拒收、1截断。独立v9单篇重抽消除了Role词表错误，但发现2处程序角色证据越出语境，仍拒收，不切默认。该已曝光单篇不能证明v9整体稳定。

首篇新失败经过有界选择器v2解决3处定位，另外2处提及保留不确定；局部复核v2明确拒绝2处无当前主体姓名支持的提及，原断言/原响应/决定和理由完整保存在拒绝账本，随后整篇完整契约通过。模型复核不是独立gold，不能将拒绝数量当真实漏抽或语义错误数量。旧v1修复中超长理由/漏项及动作与理由矛盾均保留失败记录。新v2加入目录ID、数量、短理由生成约束，完整本地校验不放宽，不确定仍拒收；局部来源结论在action前生成以减少先选择后反悔，语义一致性仍需复核。

本轮共15次本地模型请求、3547.759秒模型请求时间；零调用回放首轮、v9、选择器及局部复核，结果一致。后续修复新增1篇，当前固定10篇中4候选工程接受、6仍未解决，完整36篇保留26未选中；不使用旧修复10篇替代本轮失败。4篇新合并图1373节点/2553边、1133次证据引用，保存还原、原文重验和CSV payload比对通过。输出artifacts/graph/fresh-first-pass-post-repair-v1及artifacts/neo4j/fresh-first-pass-post-repair-v1，未载入Neo4j；旧图哈希不变。完整元数据research/qwen-fresh-followup-repair-report.json，首轮元数据独立保留。全套338项测试通过，本轮推理全部结束。

下一轮只处理固定失败，不扩量：先为程序角色/事件建立不可跨陈述者的有界语境范围复核；重复引用和局部主体仍按冻结目录处理，无法判定保留拒收；截断样本按明确输出预算从原文独立重抽，结果列为重抽不改写首轮分母。更长期考虑先冻结语境、再在各自原文范围内抽取角色/事件，避免单次长输出同时猜类型、位置和归属。未实现的语境分阶段抽取不当作完成；OpenJev及规模化仍待稳定性证据。整体语义P/R/F1仍为null，无需用户法律gold。

### 有界语境范围审核 v1

`context_scope_review` 处理完整、已拒收草稿中角色/事件的 `evidence_outside_context`。父抽取先做零调用回放，并核验响应、输入及全量分母。修复目录保存原草稿、冻结选项、模型决定、修复草稿和逐项前后账本。

模型只能选择证据包含完整子记录的既有语境，且语境类型、归属主体和父语境须与原语境完全相同。人员角色若来自 root `procedural_record`、姓名已定位且全部原角色证据都在 `footer`，可明确审核后迁入独立落款程序语境；两个角色共享完全相同证据时复用该语境。原语境不扩大、不重新分类，角色、事件、属性、参与者、极性和引用均不可改写或删除。事件属性随事件一起迁移并必须落在目标证据范围内。

决定使用绑定条目数、ID、选项及 80 字理由上限的生成 Schema，并在本地重新验证逐项选项、覆盖和全部契约。不确定或没有合法目标时保留拒收。该流程属于模型工程修复，不是人工 gold，也不保证同类语境的语义一致性；仍需逐项原文核验和后续独立评测。默认首轮配置不切换。

```bash
bash scripts/run.sh repair-qwen-context-scope --documents <全量登记1> --documents <全量登记2> --repair-spec <固定父运行及目标ID> --config configs/qwen-context-scope-review-v1.json --output artifacts/<新修复目录>
bash scripts/run.sh replay-qwen-context-scope --documents <同一全量登记1> --documents <同一全量登记2> --repair-spec <同一登记> --repair-run artifacts/<修复目录> --output artifacts/<新回放目录>
```
