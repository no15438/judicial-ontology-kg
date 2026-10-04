# 本地 Qwen 抽取

实现位置为 `src/judicial_kg/extraction/`，继续使用开发版 `0.1.0` 本体与最终抽取契约。此阶段是本地模型候选抽取与工程验证，不是专家 gold 或图谱语义准确率评测。

## 连接与运行限制

通过已有 `spark-lan` SSH 连接，在 Spark 内用标准库访问 `127.0.0.1:8003` 的 llama.cpp 服务。请求体通过 SSH stdin 传递，不放到命令参数，不在远端创建脚本或数据文件，不配置公网隧道或收费云 API。当前模型别名为 `qwen3.8-flash-next-iq4xs`；每次运行重新读取健康、模型目录、服务配置和负载，未配置/不可达时明确失败。

`configs/qwen-extraction.json` 固定首轮参数：单路串行，每个来源前 1 篇、最多 2 篇，最多输出 6,144 tokens，温度 0、seed 42、关闭 thinking。完整预期文书清单不随抽样缩小；未选中的 34 篇仍记录 `not_selected`，在统一验证器中继续属于缺失候选。

每篇先调用服务实际模板与 tokenizer，要求输入 tokens + 预留输出 + 128 tokens 余量不超过配置的 32K。超预算就拒绝该篇，不静默截断原文。服务忙时也不插入推理。请求超时/SSH 状态不明时停止后续提交，避免未知在途任务叠加；没有自动重试、服务重启或配置改动。

## 模型与程序各自负责什么

当前提示文件为 `prompts/qwen-extraction-v3.txt`，配置版本 `qwen-extraction-v3`。只发送发布物的字段名及原文字段，不发送上游 QA 答案、标签或历史候选。模型返回六类节点及 `field / quote / occurrence` 引用；不让模型编造文书 ID、来源路径、哈希和字符偏移。旧 v1/v2 提示和提交保留，用于追溯失败试跑。

模型仅生成 `Mention.actor_id` 正向关联，程序从实际提及生成 `Actor.mention_ids` 反向索引。此索引是确定性数据结构，不是新的语义标注；主体类型、共指、语境和事件仍由模型提议。相同主体的相同证据位置重复生成 Mention 时拒收，不静默去重或补造占位提及。

引用必须逐字匹配同一字段。唯一引用的 occurrence 可为 null；重复引用必须明确指定从 0 开始的出现序号。程序不模糊匹配、不默认选第一次、不拼接原文，也不让模型“修复”原文。定位成功后，程序生成标准证据跨度并补齐来源元数据，再运行既有契约校验。

服务端使用 `json_object` 约束 JSON 语法，模型草稿 Schema 放在提示中并在本地严格验证。v1 将完整草稿 Schema 交给解码器，观察到提及 ID 列表异常重复；v2 简化解码并改成简短 ID 后，在同一长文书仍重现，因而不能把原因归于复杂解码约束。v3 将反向索引从模型任务移交程序，没有放宽最终本体契约或增加输出预算。

所有主体引用、语境范围、陈述归属、日期和类型边界仍由本地校验器检查。`finish_reason` 非 stop、截断、空内容、拒绝、错误模型别名、JSON 错误、证据不匹配或契约错误都单独记账，不丢弃后重新计算通过率。模型未覆盖到的事实无法用结构检查发现。

## 执行

```bash
bash scripts/run.sh extract-qwen \
  --documents artifacts/source-development/appealcase-documents.json \
  --documents artifacts/source-development/spark-documents.json
```

默认输出为 `artifacts/qwen-extraction/<UTC时间>-<随机ID>/`。也可显式提供 `--config` 和 `--output`；输出目录必须是新的 Git 忽略目录，不覆盖任何既往试跑，包括失败试跑。不要复用旧的 pilot 目录。

该命令确实提交本地推理请求。`validate-extractions` 仍是纯离线校验。输入只允许已曝光的开发文书；不能将正式测试集用于改提示或本体。

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

运行记录保存模型目录/配置哈希、模型别名、build_info 与上下文上限；尚未重算所有 GGUF 权重哈希，因此明确保留 `model_weight_hash_verified=false`。温度与种子固定不构成跨硬件、跨服务版本完全确定性的承诺。

耗时与 token usage 用于诊断本次抽取成本，没有执行用户已撤回的 8K/16K 性能基准。没有完整独立标注时，precision、recall 和 F1 保持 null；不向用户索要专业法律标注。
