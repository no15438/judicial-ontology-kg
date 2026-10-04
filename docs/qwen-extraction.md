# 本地 Qwen 抽取

实现位置为 `src/judicial_kg/extraction/`，继续使用开发版 `0.1.0` 本体与最终抽取契约。此阶段是本地模型候选抽取与工程验证，不是专家 gold 或图谱语义准确率评测。

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

程序不模糊匹配、不修改原文、不自动扩充语境、不删除难处理节点。属性 raw 必须来自选中片段，所有引用和原值逐字回放后才生成证据跨度。最终本体/契约保持 0.1.0，严格校验规则没有放宽。定位失败会报告全部已发现错误；未完成的后续语义/契约检查不能假定通过。

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
