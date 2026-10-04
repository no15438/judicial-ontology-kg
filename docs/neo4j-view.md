# 本地 Neo4j 查看候选图

已在 Neo4j Desktop 的 Judicialgraphrag 实例中新建独立数据库 `judicial-ontology-kg`，使用 Neo4j 2026.05.0。原有数据库保持不变。服务仅监听本机回环地址。

两篇本地 Qwen 开发候选已导入：524 个节点、994 条关系，其中 462 条 SUPPORTED_BY 证据引用。以 CSV 逐条核对节点原始载荷、节点标签、关系原始载荷、端点与类型，节点差异和关系差异均为 0。这是工程保真验证，不是语义准确率；这些记录不是 gold。

## 查看

打开 Neo4j Desktop，启动 Judicialgraphrag，进入 Query 并选择 `judicial-ontology-kg`。侧边浏览器已经打开本次图形结果；Desktop 前端地址可能随重启变化。

先查看事件、语境与参与者：

```cypher
MATCH p=(c:Context)<-[:IN_CONTEXT]-(e:Event)-[:PARTICIPANT]->(a:Actor)
RETURN p LIMIT 80;
```

当前结果为 44 个节点、69 条关系，是完整库的概览。点击节点查看属性，双击节点可展开相邻节点。`name` 用于显示名称，`payload_json` 保留完整原始节点或关系记录。肯定、否定与法院认定均属于候选记录的语境属性，不代表人工法律裁决。

查看原文证据：

```cypher
MATCH p=(e:Event)-[:SUPPORTED_BY]->(s:EvidenceSpan)
RETURN p LIMIT 80;
```

查看全部关系（较拥挤）：

```cypher
MATCH p=(n:KGNode)-[r]->(m:KGNode)
RETURN p LIMIT 994;
```

## 重新导出

```bash
PYTHONPATH=src .venv/bin/python scripts/export_neo4j.py \
  --source-run artifacts/graph/local-qwen-v1 \
  --output artifacts/neo4j/local-qwen-v3
```

输出目录必须不存在。导出器先验证装配报告哈希和候选记录可还原性，再生成 CSV、导入语句、按快照限定的查看语句与 manifest。将两个 CSV 放入实例 import 目录下 manifest 指定的子目录，在目标数据库执行 `import.cypher`。不需要 APOC。导入按快照哈希及原始 ID 合并，重复执行同一快照不会新增副本；不同快照并存时应使用导出的 `views.cypher`。

当前实际导入来自 `artifacts/neo4j/local-qwen-v1`；最终脚本复导出的 v2 中 nodes.csv、edges.csv、import.cypher 与 v1 字节相同。所有原始文书、图谱与 CSV 均留在本机 Git 忽略目录，不上传仓库。
