# 当前主流程：current-rc1

这是收敛候选版，不是已经证明语义准确或检索增益的生产最终版。唯一开发入口为 `judicial-kg current`（也可 `PYTHONPATH=src .venv/bin/python -m judicial_kg.current.cli`）。旧 v1–v44 命令保留用于复现实验，不再作为新增能力的默认入口。

## 收敛内容

| 保留能力 | 当前模块 |
| --- | --- |
| 原文哈希、真实来源及精确跨度 | source / 既有来源契约 |
| 完整称谓的可逆空白定位，不拆单字拼姓名 | source.locate |
| 主体、集合、对象、主张/认定、极性与事件分离 | protocol / compiler / 契约0.5 |
| 提议与证据复核分开，固定原文单元选择 | runner.verify |
| 原文有效性与图谱往返 | 既有validation、graph assembly |
| 局部失败隔离、依赖拒收和未决账本 | Compiler.attempt / coverage / issues |
| 冻结输入、零调用回放、共享累计预算 | current.cli / SharedBudget |
| 原文检索、BM25、词法TF-IDF、混合检索与事件证据扩展 | retrieval |
| 有来源参考的离线检索对照 | evaluate；无参考项明确不计分 |

## 流程与评价边界

1. 所有输入原文先入文本索引，即使未选做图谱或模型失败，仍能检索。
2. 原文按固定连续单元窗口处理。窗口不是法律语境；给模型完整原文作背景，仅目标窗口允许作为本轮事实证据。
3. 使用中文接口提议语境、主体、集合、对象、角色、事件和原文值；正式ID、证据哈希、偏移全部由程序生成。
4. 第二次同模型调用逐项复核，选择当前窗口的直接证据。不确定项保留在原响应及隔离账本中；这不是独立专家审查。
5. 各实体/断言独立事务校验。不存在的依赖只拒收相关项；未知集合成员不需要虚构，集合事件不传播到成员。未知说话者仍不能冒充明确当事人主张。
6. 每个原文单元保留处理记录：processed / no_relevant_content / uncertain / failed；前三者是模型或流程记录，不能称为召回率。
7. 生成局部候选图并无损回放。partial不是完整通过，历史0/3不改写。engineering_complete也只表示本协议未触发拒收，不证明语义完整。

角色与集合成员身份、观点归属及反事实/否定仍可能判断错误；严格来源只能排除不存在的引文。跨窗口关系与跨语境身份合并目前不自动解决，明确保持局部身份，不能当全局实体图。当前仅为角色/事件/属性的有限本体；无法表达的内容必须保留unresolved及原文。

## 使用

```bash
# 在仓库根目录运行。5元子预算，仍在原200元总授权内。
PYTHONPATH=src .venv/bin/python -m judicial_kg.current.cli run \
  --manifest research/current-rc1-three-preregistration.json \
  --output artifacts/current/three-01 \
  --credential-root ../judicial-ontology-kg-current --execute

# 原文索引包含全部36篇；选择图谱只跑固定三篇。
PYTHONPATH=src .venv/bin/python -m judicial_kg.current.cli search \
  --index artifacts/current/three-01/text-index.json --method hybrid --query '借款 还款'

# 独立回放：需原冻结实现，禁止重新调用模型。
PYTHONPATH=src .venv/bin/python -m judicial_kg.current.cli replay \
  --source-run artifacts/current/three-01/document-01 --output artifacts/current/replay-extra
```

检索对照输入是带来源的 JSON 数组：每项 `id`、`query`、`relevant_document_ids`。这只评估上游参考文书命中，不是专家类案相关性或答案准确率。通过 `evaluate --index ... --questions ... --candidate ...` 比较三种文本基线及图扩展。图扩展使用更多证据，必须同时看返回字符量，不能把上下文增加当算法净收益。TF-IDF不是语义嵌入；没有配置语义embedding模型时不冒充有该基线。

## 继续投入的门槛

先固定三篇验证来源映射、集合与局部隔离，再对真实、有来源的查询做文本/图消融。保留所有问题及成本；不能按图生成的问题反向挑选有利测试。若图没有显示关系/归属任务增益，文本检索保留，图层可关闭。未有此证据前不扩大批次，不声称GraphRAG优于文本RAG。
