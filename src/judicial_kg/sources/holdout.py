"""Freeze fresh source families before outcomes; literal similarity is a leakage screen only."""

import copy
import unicodedata

from judicial_kg.sources.records import canonical_hash, sha256


def signature(doc):
    # Exclude headers/footers for fuzzy overlap, keeping them unchanged in the source document.
    text = "\n".join(f["text"] for f in doc["fields"] if f["name"] not in {"header", "footer"})
    text = "".join(unicodedata.normalize("NFKC", text).split())
    return sha256(text.encode()), {text[i : i + 5] for i in range(max(0, len(text) - 4))}


def freeze(documents, exposed, count, seed, *, near_jaccard=0.80, near_containment=0.90):
    """One document per unexposed family, with exact/fuzzy screening against all exposed sources."""
    if not 0 < count <= 20 or not seed:
        raise ValueError("Invalid frozen cohort size or seed")
    if not 0 < near_jaccard <= 1 or not 0 < near_containment <= 1:
        raise ValueError("Invalid predeclared near-duplicate thresholds")
    if any(not d.get("family_id") for d in [*documents, *exposed]):
        raise ValueError("Family grouping is required before held-out allocation")
    old_ids = {d["document_id"] for d in exposed}
    old_families = {d["family_id"] for d in exposed}
    old_signatures = [signature(d) for d in exposed]
    selected, rejected = [], []
    selected_families = set()
    # No output quality, inferred assertion, model score or manual source reading enters this order.
    ordered = sorted(documents, key=lambda d: canonical_hash([seed, d["document_id"]]))
    for doc in ordered:
        reason = None
        if doc.get("admission") == "quarantine":
            reason = "quarantined_source"
        elif doc["document_id"] in old_ids or doc.get("allocation") == "development_exposed":
            reason = "development_exposed_document"
        elif doc["family_id"] in old_families:
            reason = "development_exposed_family"
        elif doc["family_id"] in selected_families:
            reason = "same_family_as_selected"
        if reason is None:
            digest, shingles = signature(doc)
            for previous, other in old_signatures:
                shared = len(shingles & other)
                if digest == previous:
                    reason = "exact_core_text_duplicate"
                elif (
                    len(shingles) >= 200
                    and len(other) >= 200
                    and (
                        shared / len(shingles | other) >= near_jaccard
                        or shared / min(len(shingles), len(other)) >= near_containment
                    )
                ):
                    reason = "near_core_text_duplicate"
                if reason:
                    break
        if reason:
            rejected.append({"document_id": doc["document_id"], "reason": reason})
            continue
        adapted = copy.deepcopy(doc)
        adapted["allocation"] = "frozen_unexposed_evaluation_once"
        selected.append(adapted)
        selected_families.add(doc["family_id"])
        old_signatures.append((digest, shingles))
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError("Insufficient unexposed independent source families")
    return selected, {
        "seed": seed,
        "selected": count,
        "eligible_documents": len(documents),
        "screened_exposed_documents": len(exposed),
        "excluded_document_ids_sha256": canonical_hash(sorted(old_ids)),
        "excluded_family_ids_sha256": canonical_hash(sorted(old_families)),
        "order": [d["document_id"] for d in selected],
        "families": [d["family_id"] for d in selected],
        "selected_source_sha256": [canonical_hash(d) for d in selected],
        "rejected_before_selection_complete": rejected,
        "near_duplicate_rule": {
            "shingle_characters": 5,
            "minimum_shingles": 200,
            "jaccard_threshold": near_jaccard,
            "containment_threshold": near_containment,
        },
        "boundary": (
            "Project-recorded exposure screened; no claim of no pretraining exposure or complete "
            "cross-corpus linkage. After viewing outcomes this batch becomes development data."
        ),
        "graph_gold_available": False,
        "semantic_precision_recall_f1": None,
    }
