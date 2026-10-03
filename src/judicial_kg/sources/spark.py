"""Normalize selected Spark QA rows; parent text and upstream labels remain distinct."""

from collections import Counter

from judicial_kg.sources.records import canonical_hash, document, sha256


def adapt_scan(capture: dict) -> tuple[list, dict]:
    parents = {}
    for selected in capture["selected_rows"]:
        row = selected["row"]
        parent = row["source_detail"]["source_id"]
        text = row["text"]
        if sha256(text.encode()) != row["content"]["text_sha256"]:
            raise ValueError("Source text hash mismatch")
        locator = {k: v for k, v in selected.items() if k != "row"}
        if parent not in parents:
            source = {
                "manifest_sha256": capture["manifest_sha256"],
                "parent_id_sha256": sha256(parent.encode()),
                **locator,
            }
            parents[parent] = document(
                "spark_qa",
                source,
                {"text": (locator["record_pointer"] + "/text", text)},
                admission="transformed_source_engineering_only",
                external_court_text_verified=False,
                allocation="development_exposed",
                case_type=row["case_type"],
                family_id="spark-parent:" + sha256(parent.encode()),
                family_scope="source_parent_only; related proceedings not verified",
                annotation_origin="upstream_claimed_model_prelabel_and_human_review_unverified",
                source_transform={
                    "raw_hash_declared": row["content"]["raw_text_sha256"],
                    "method_declared": row["privacy_info"]["desensitization_method"],
                    "original_offset_map_available": False,
                },
                reference_labels=[],
            )
        record = parents[parent]
        if record["fields"][0]["text"] != text:
            raise ValueError("Conflicting text for one parent")
        for qa in row["qa_pairs"]:
            record["reference_labels"].append(
                {"source": locator, "qa": qa, "status": "upstream_label_unverified"}
            )
    documents = list(parents.values())
    if len(documents) != capture["selected_parents"]:
        raise ValueError("Captured parent count does not match rows")
    report = {k: v for k, v in capture.items() if k != "selected_rows"}
    report.update(
        {
            "normalized_documents": len(documents),
            "qa_rows_per_parent": sorted(
                Counter(
                    r["row"]["source_detail"]["source_id"] for r in capture["selected_rows"]
                ).values()
            ),
            "exposed_parent_keys": [d["source"]["parent_id_sha256"] for d in documents],
            "source_text_hashes": [d["fields"][0]["sha256"] for d in documents],
            "normalized_content_fingerprint": canonical_hash(documents),
            "semantic_precision_recall_f1": None,
        }
    )
    return documents, report
