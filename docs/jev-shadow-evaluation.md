# Qwen / OpenJev 分阶段协作与影子评测

用户授权在独立分支 `codex/jev-shadow-evaluation` 修改设计并开展本地评测。它从 `600fe87` 建立，保留 `codex/qwen-extraction` 的既有实现和结果。OpenJev 是本机部署的独立开放权重决策模型，不等同于 TypeSafe 托管 Jev；本轮不使用收费 API、不改变模型服务。

## 分工与实施边界

1. 程序编译原文位置目录，Qwen 生成主体锚点及语境候选。
2. 在语境冻结前，OpenJev 对有限、明确、来源受限的问题给出分类建议；它不生成原文坐标，不扩大范围，不直接改候选。
3. 两模型意见不一致、选项顺序不稳定、mixed/uncertain 或与原候选不同，都进入复核队列。后续 Qwen 局部修复保存原/新决定；程序做完整验收。模型意见一致也不能证明法律正确。
4. 冻结通过的语境后，Qwen 抽取事件、角色及属性；程序校验来源和跨引用，按既有 M3 计划有界恢复，整篇通过后才导出图谱。

当前交付仅实现步骤 2 的可开关影子评测及步骤 3 的疑点分流报告，不将建议写回生产抽取流程。M1 位置接口、自动局部修复和完整 A/B/C 端到端消融尚未完成，不将本轮分类诊断冒称已完成整个新设计。

## 首轮预登记

- 使用既有 v13 Spark4396 头部的全部 9 个直接语境，涵盖程序、主张、答辩、认定、引用、说理、裁判和落款；这是 1 篇已曝光开发文书，完整来源分母 36 篇保留，35 篇未选择。
- 原头部类别只作 A 组历史参照，不是 gold。B 为 Qwen 独立局部判断，C 为 OpenJev 独立局部判断；这不是三个完整抽取管线的效果对照。
- 两模型接收同一目标原文及明确标记的相邻非目标原文。匿名 case ID、旧类别、旧主体归属和 QA 不进入请求；不得参考对方答案。边界来自旧模型，因此仍有选区偏差，不称来源独立盲测。
- 选项为 7 个既有语境类型加 mixed、uncertain；mixed 表示目标确实混合多个层次。分别正序与反序，各模型各 9 次，总上限 36 次，不自动重试。按语境交替模型先后顺序；这控制部分顺序影响，未构成完整延迟基准。
- Qwen 温度 0.2、seed42、输出上限128 tokens；OpenJev 保持现有 helper 的读出参数，不用 Qwen 温度替代 Jev 概率校准。记录实际 helper 版本、参数和响应模型身份。概率只作模型分布，不能解读为本任务正确率。
- 每个状态最多8192 UTF-8字节，不截断原文；超限显式记账。串行调用，共享仓库运行锁，每次查两模型负载、helper版本及可用内存，低于4GiB或繁忙立即停止新请求。模型服务不重启、不调参。
- 每次 RPC 保存精确请求、原始响应、哈希和耗时；所有语境、失败、未尝试、空容器均保留。零调用回放重建来源范围、选项顺序及请求，并重新解析有效响应。

## 指标和后续决策

本轮报告：响应有效率、同模型选项反序稳定性、Qwen/OpenJev 分歧、与历史类别差异、mixed/uncertain、复核队列、请求数/用量/耗时和墙钟时间。所有费用为既有本地算力，没有云端 API。无独立参考，不报告语义准确率、概率校准或图谱 P/R/F1；模型一致性不是 gold。

先根据本轮判断是否适合进入 M2 的可选分支；若存在明显选项顺序不稳定或不合理确信，保持影子模式。M1/M2/M3 实现后，再固定同一版本和来源比较完整 A（Qwen+程序）、B（Qwen+Qwen复核+程序）、C（Qwen+Jev分流+必要Qwen修复+程序），将额外调用计入预算。只有完整回归显示收益才启用，不能根据单篇的一致率决定替代 Qwen。

## 本地执行

```bash
PYTHONPATH=src .venv/bin/python -m judicial_kg.extraction.jev_shadow \
  --documents artifacts/scoped-v12/spark-documents.json artifacts/scoped-v12/appealcase-documents.json \
  --header artifacts/qwen-extraction/fresh-scoped-v13-spark439/stage-000-header-draft.json \
  --document-id spark_qa:439645deb3d015aa2dd048ed1a4968404905361b10f5b228d9ee78ea779cc998 \
  --output artifacts/jev-shadow/pilot-v1
PYTHONPATH=src .venv/bin/python -m judicial_kg.extraction.jev_shadow \
  --replay artifacts/jev-shadow/pilot-v1
```

已存在目录拒绝重跑；不覆盖旧试验。原文与原始响应只在忽略目录及获授权的 Google 项目文件夹；公开结果仅元数据。用户无需法律专业标注。
