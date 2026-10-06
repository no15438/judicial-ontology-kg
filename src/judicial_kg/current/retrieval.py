"""Source-first retrieval baselines. TF-IDF is lexical, not semantic embeddings."""

import math
import re
from collections import Counter

from judicial_kg.current.source import batches
from judicial_kg.sources.records import evidence_span


def tokens(text):
    terms = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.lower())
    terms += [
        a + b
        for a, b in zip(terms, terms[1:], strict=False)
        if len(a) == len(b) == 1 and "\u3400" <= a <= "\u9fff" and "\u3400" <= b <= "\u9fff"
    ]
    return terms


def index_documents(docs):
    rows = []
    for doc in docs:
        units, _ = batches(doc)
        for i, u in enumerate(units):
            rows.append(
                {
                    "id": f"{doc['document_id']}#u{i}",
                    "document_id": doc["document_id"],
                    "unit": i,
                    "text": u["text"],
                    "evidence": evidence_span(doc, u["field"], u["start"], u["end"]),
                }
            )
    return rows


def search(rows, query, method="bm25", limit=10):
    if method not in {"bm25", "tfidf", "hybrid"}:
        raise ValueError("Unsupported retrieval method")
    if method == "hybrid":
        combined = {}
        for name in ("bm25", "tfidf"):
            for rank, row in enumerate(search(rows, query, name, len(rows)), 1):
                combined.setdefault(row["id"], {**row, "score": 0})["score"] += 1 / (60 + rank)
        return sorted(combined.values(), key=lambda r: (-r["score"], r["id"]))[:limit]
    documents = [Counter(tokens(row["text"])) for row in rows]
    df = Counter(t for c in documents for t in c)
    n = len(rows)
    avg = sum(sum(c.values()) for c in documents) / max(1, n)
    q = Counter(tokens(query))
    idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
    qnorm = math.sqrt(sum((v * idf.get(t, 0)) ** 2 for t, v in q.items()))
    scored = []
    for row, counts in zip(rows, documents, strict=True):
        if method == "bm25":
            length = sum(counts.values())
            score = sum(
                idf.get(t, 0)
                * counts[t]
                * 2.2
                / (counts[t] + 1.2 * (0.25 + 0.75 * length / max(avg, 1)))
                for t in q
            )
        else:
            norm = math.sqrt(sum((v * idf[t]) ** 2 for t, v in counts.items()))
            score = sum(v * counts[t] * idf.get(t, 0) ** 2 for t, v in q.items()) / max(
                qnorm * norm, 1e-30
            )
        if score > 0:
            scored.append({**row, "score": score})
    return sorted(scored, key=lambda r: (-r["score"], r["id"]))[:limit]


def graph_expand(rows, hits, records):
    """Return only evidence attached to the same candidate event/context, with provenance."""
    expanded = {h["id"]: {**h, "retrieval_origin": "text"} for h in hits}
    for record in records:
        matched = [h["evidence"] for h in hits if h["document_id"] == record["document_id"]]
        for event in record["events"]:
            if not any(
                a["field"] == b["field"] and a["start"] < b["end"] and b["start"] < a["end"]
                for a in matched
                for b in event["evidence"]
            ):
                continue
            spans = event["evidence"] + [
                s
                for statement in record["statements"]
                if statement["subject_id"] == event["id"]
                and statement["context_id"] == event["context_id"]
                for s in statement["evidence"]
            ]
            for row in rows:
                if (
                    row["document_id"] == record["document_id"]
                    and row["evidence"] in spans
                    and row["id"] not in expanded
                ):
                    expanded[row["id"]] = {
                        **row,
                        "retrieval_origin": "candidate_event_evidence",
                        "event_id": event["id"],
                        "context_id": event["context_id"],
                        "polarity": event["polarity"],
                    }
    return list(expanded.values())


def search_context(rows, query, limit=3, radius=2, character_budget=600):
    """Rank neighboring clauses together; return original spans, never synthetic citations."""
    if radius < 0 or limit < 1 or character_budget < 1:
        raise ValueError("Invalid retrieval bounds")
    groups = {}
    for row in rows:
        groups.setdefault((row["document_id"], row["evidence"]["field"]), []).append(row)
    ranked, neighborhoods = [], {}
    for group in groups.values():
        group.sort(key=lambda row: row["evidence"]["start"])
        for i, row in enumerate(group):
            neighbors = group[max(0, i - radius) : i + radius + 1]
            neighborhoods[row["id"]] = neighbors
            # Concatenation is ranking text only; cited evidence remains the original units.
            ranked.append({**row, "text": "".join(n["text"] for n in neighbors)})
    seeds = search(ranked, query, "hybrid", limit)
    hits, seen, characters = [], set(), 0
    for seed in seeds:
        for row in neighborhoods[seed["id"]]:
            if row["id"] not in seen and characters + len(row["text"]) <= character_budget:
                hits.append({**row, "retrieval_origin": "text_context"})
                seen.add(row["id"])
                characters += len(row["text"])
    return hits
