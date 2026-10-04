"""Export verified local graph snapshots for Neo4j 2025.11+; no network calls."""

import csv
import json
from pathlib import Path

from judicial_kg.graph.assembly import restore_records
from judicial_kg.sources.development import safe_output, write_json
from judicial_kg.sources.records import canonical_hash, sha256
from judicial_kg.validation_run import strict_json

CONTEXT_NAMES = {
    "party_claim": "当事人主张",
    "party_defense": "当事人抗辩",
    "court_finding": "法院认定（候选）",
    "court_reasoning": "法院说理（候选）",
    "court_disposition": "裁判结果",
    "court_report": "法院转述",
    "quoted_material": "引用材料",
    "procedural_record": "程序记载",
    "unknown": "语境未知",
}
EVENT_NAMES = {
    "loan_agreement": "借贷合意",
    "funds_transfer": "款项转移",
    "repayment": "还款",
    "guarantee": "担保",
    "demand": "催告",
    "litigation": "诉讼",
    "other": "其他事件",
}
POLARITY_NAMES = {
    "affirmed": "肯定陈述",
    "negated": "否定陈述",
    "uncertain": "不确定",
    "conditional": "条件陈述",
}


def export_neo4j(root: Path, source_run: Path, output: Path) -> dict:
    output = safe_output(root, output)
    if output.exists():
        raise ValueError("Neo4j export requires a new output directory")
    report_raw = (source_run / "report.json").read_bytes()
    report = strict_json(report_raw)
    raw = (source_run / "graph.json").read_bytes()
    if sha256(raw) != report["outputs"]["graph.json"]:
        raise ValueError("Graph differs from recorded assembly output")
    graph = strict_json(raw)
    records = restore_records(graph)
    snapshot = canonical_hash(graph)
    if snapshot != report["graph_canonical_sha256"]:
        raise ValueError("Graph canonical hash mismatch")
    folder = "judicial-ontology-kg-" + snapshot[:12]
    nodes = {n["id"]: n for n in graph["nodes"]}
    local = {
        (n["document_id"], n["properties"].get("id")): n
        for n in nodes.values()
        if n["kind"] not in ("Document", "EvidenceSpan")
    }
    names = {}
    for record in records:
        for mention in record["mentions"]:
            actor = local[record["document_id"], mention["actor_id"]]
            names.setdefault(actor["id"], mention["evidence"]["quote"])
    rows = []
    for node in graph["nodes"]:
        p = node["properties"]
        kind = node["kind"]
        name = p.get("id", kind)
        if kind == "Actor":
            name = names[node["id"]]
        elif kind == "Document":
            name = node["document_id"].split(":")[0] + " · 开发文书"
        elif kind == "Context":
            name = CONTEXT_NAMES[p["kind"]] + " · " + p["id"]
        elif kind == "Event":
            name = EVENT_NAMES[p["event_type"]] + " · " + POLARITY_NAMES[p["polarity"]]
        elif kind == "Role":
            name = p["role"] + " · " + POLARITY_NAMES[p["polarity"]]
        elif kind == "Statement":
            name = p["predicate"] + " · " + p["value"]["raw"][:32]
        elif kind == "EvidenceSpan":
            name = "原文 · " + p["quote"][:32]
        value = p.get("value", {})
        rows.append(
            {
                "id": node["id"],
                "snapshot": snapshot,
                "kind": kind,
                "document_id": node["document_id"],
                "name": name,
                "polarity": p.get("polarity", ""),
                "context_kind": p.get("kind", "") if kind == "Context" else "",
                "event_type": p.get("event_type", ""),
                "actor_type": p.get("actor_type", ""),
                "predicate": p.get("predicate", ""),
                "raw": value.get("raw", ""),
                "normalized": value.get("normalized") or "",
                "quote": p.get("quote", ""),
                "payload_json": json.dumps(node, ensure_ascii=False, sort_keys=True),
            }
        )
    edges = [
        {
            "id": e["id"],
            "snapshot": snapshot,
            "source": e["source"],
            "target": e["target"],
            "relation": e["relation"].upper(),
            "position": e["index"],
            "role": e["properties"].get("role", ""),
            "payload_json": json.dumps(e, ensure_ascii=False, sort_keys=True),
        }
        for e in graph["edges"]
    ]
    if not rows or not edges:
        raise ValueError("Neo4j visualization requires a nonempty graph with relationships")
    output.mkdir(parents=True, exist_ok=False)
    for name, items in [("nodes.csv", rows), ("edges.csv", edges)]:
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(items[0]))
            writer.writeheader()
            writer.writerows(items)
    # A single statement/transaction. All values come from CSV, never source-text
    # interpolation. MERGE keys isolate graph snapshots and permit repeat import.
    query = f"""CALL () {{
  LOAD CSV WITH HEADERS FROM 'file:///{folder}/nodes.csv' AS row
  MERGE (n:KGNode {{snapshot: row.snapshot, id: row.id}})
  SET n:$(row.kind)
  SET n += row, n.reference_status = 'not_graph_gold'
  RETURN count(n) AS imported_nodes
}}
CALL () {{
  LOAD CSV WITH HEADERS FROM 'file:///{folder}/edges.csv' AS row
  MATCH (a:KGNode {{snapshot: row.snapshot, id: row.source}})
  MATCH (b:KGNode {{snapshot: row.snapshot, id: row.target}})
  MERGE (a)-[r:$(row.relation) {{snapshot: row.snapshot, id: row.id}}]->(b)
  SET r.payload_json = row.payload_json, r.position = toInteger(row.position), r.role = row.role
  RETURN count(r) AS imported_edges
}}
RETURN imported_nodes, imported_edges;
"""
    (output / "import.cypher").write_text(query)
    scope = f"WHERE e.snapshot = '{snapshot}' "
    overview = (
        "MATCH p=(c:Context)<-[:IN_CONTEXT]-(e:Event)-[:PARTICIPANT]->(a:Actor) "
        + scope
        + "RETURN p LIMIT 80;\n"
    )
    views = (
        "// 在 judicial-ontology-kg 数据库运行。肯定陈述仅表示原语境陈述，非事实裁决。\n" + overview
    )
    views += (
        "\n// 选择否定陈述及其证据\n"
        "MATCH p=(e:Event {polarity:'negated'})-[:SUPPORTED_BY]->(s:EvidenceSpan) "
        + scope
        + "RETURN p;\n"
    )
    views += (
        "\n// 显示完整候选图，包括证据节点\nMATCH p=(n:KGNode)-[r]->(m:KGNode) "
        f"WHERE r.snapshot = '{snapshot}' RETURN p LIMIT {len(edges)};\n"
    )
    (output / "views.cypher").write_text(views)
    write_json(
        output / "manifest.json",
        {
            "adapter_version": "neo4j-csv-v1",
            "source_report_sha256": sha256(report_raw),
            "graph_sha256": sha256(raw),
            "snapshot": snapshot,
            "import_subdirectory": folder,
            "nodes": len(rows),
            "edges": len(edges),
            "database": "judicial-ontology-kg",
            "source_graph_header": {k: v for k, v in graph.items() if k not in ("nodes", "edges")},
            "payloads_preserve_exact_graph": True,
            "original_candidate_records": len(records),
            "gold_available": False,
            "outputs": {p.name: sha256(p.read_bytes()) for p in sorted(output.iterdir())},
        },
    )
    return {
        "output": str(output),
        "import_subdirectory": folder,
        "nodes": len(rows),
        "edges": len(edges),
    }
