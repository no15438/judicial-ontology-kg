"""Compile exact source spans for constrained generation; no attribution inference."""

import re

from jsonschema import Draft202012Validator

from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.mention_repair import occurrences, selector
from judicial_kg.sources.records import canonical_hash, evidence_span

VERSION = "direct-span-catalog-v1"
KINDS = ["name", "pronoun", "description"]


def build_catalog(doc, direct_units, seed_texts, proposals=(), *, space_variants=False):
    units = source_units(doc)
    by_id = {u["id"]: u for u in units}
    if len(set(direct_units)) != len(direct_units) or any(i not in by_id for i in direct_units):
        raise ValueError("Invalid direct source scope")
    if any(not isinstance(t, str) or not t for t in seed_texts):
        raise ValueError("Empty or non-string seed")
    proposals = list(proposals)
    if any(
        set(p) != {"id", "text"}
        or not isinstance(p["text"], str)
        or not p["text"]
        or not isinstance(p["id"], str)
        or not p["id"]
        for p in proposals
    ):
        raise ValueError("Invalid source proposal")
    if len({p["id"] for p in proposals}) != len(proposals):
        raise ValueError("Duplicate proposal ID")
    scope = [u for u in units if u["id"] in direct_units]
    texts = sorted(set(seed_texts) | {p["text"] for p in proposals})
    entries = {}
    matches = {}
    for text in texts:
        matches[text] = []
        positions = occurrences(scope, text)
        if space_variants and text.strip(" \u3000"):
            pattern = "".join(
                "[ \u3000]+" if part and part[0] in " \u3000" else re.escape(part)
                for part in re.split(r"([ \u3000]+)", text)
            )
            variants = {
                m.group(1)
                for unit in scope
                for m in re.finditer("(?=(" + pattern + "))", unit["text"])
            }
            positions = [
                position for variant in sorted(variants) for position in occurrences(scope, variant)
            ]
        for position in positions:
            evidence = evidence_span(doc, position["field"], position["start"], position["end"])
            identifier = "s_" + canonical_hash(evidence)
            entries[identifier] = {
                "span_id": identifier,
                "evidence": evidence,
                "selector": selector(position),
            }
            matches[text].append(identifier)
    result = {
        "version": "direct-span-catalog-space-v1" if space_variants else VERSION,
        "document_id": doc["document_id"],
        "source_sha256": canonical_hash(doc),
        "direct_units": [u["id"] for u in scope],
        "seed_texts": sorted(set(seed_texts)),
        "proposals": proposals,
        "entries": sorted(
            entries.values(),
            key=lambda e: (e["evidence"]["field"], e["evidence"]["start"], e["evidence"]["end"]),
        ),
        "proposal_ledger": [
            {
                "proposal_id": p["id"],
                "span_ids": matches[p["text"]],
                "status": "requires_selection" if matches[p["text"]] else "no_source_match",
            }
            for p in proposals
        ],
        "unmatched_seeds": [t for t in sorted(set(seed_texts)) if not matches[t]],
        "complete_mention_coverage": False,
    }
    if space_variants:
        result["matching_policy"] = "ascii-fullwidth-space-runs-only-no-auto-selection"
    return result


def verify_catalog(doc, direct_units, catalog):
    rebuilt = build_catalog(
        doc,
        direct_units,
        catalog["seed_texts"],
        catalog["proposals"],
        space_variants=catalog["version"] == "direct-span-catalog-space-v1",
    )
    if rebuilt != catalog:
        raise ValueError("Catalog source, scope or entries changed")


def selection_schema(catalog, actor_ids):
    if (
        not actor_ids
        or len(set(actor_ids)) != len(actor_ids)
        or any(not isinstance(a, str) or not a for a in actor_ids)
    ):
        raise ValueError("Expected unique existing actor IDs")

    def enum(values):
        return {"type": "string", "enum": values} if values else {"not": {}}

    def obj(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    spans = [e["span_id"] for e in catalog["entries"]]
    return obj(
        {
            "catalog_sha256": {"const": canonical_hash(catalog)},
            "mentions": {
                "type": "array",
                "uniqueItems": True,
                "items": obj(
                    {
                        "span_id": enum(spans),
                        "actor_id": enum(actor_ids),
                        "kind": enum(KINDS),
                    }
                ),
            },
            "proposal_decisions": {
                "type": "array",
                "items": obj(
                    {
                        "proposal_id": enum([p["id"] for p in catalog["proposals"]]),
                        "action": enum(["select", "uncertain"]),
                        "span_ids": {"type": "array", "uniqueItems": True, "items": enum(spans)},
                    }
                ),
            },
        }
    )


def compile_selection(doc, direct_units, catalog, actor_ids, result):
    """Returns a staged body delta, never an accepted document or a semantic verdict."""
    verify_catalog(doc, direct_units, catalog)
    if list(Draft202012Validator(selection_schema(catalog, actor_ids)).iter_errors(result)):
        raise ValueError("Selection outside frozen schema")
    entries = {e["span_id"]: e for e in catalog["entries"]}
    decisions = result["proposal_decisions"]
    ids = [d["proposal_id"] for d in decisions]
    if len(set(ids)) != len(ids) or set(ids) != {p["id"] for p in catalog["proposals"]}:
        raise ValueError("Proposal decisions must cover the complete ledger")
    selected = {m["span_id"] for m in result["mentions"]}
    ledger = {p["proposal_id"]: p for p in catalog["proposal_ledger"]}
    unresolved = []
    for decision in decisions:
        if decision["action"] == "uncertain":
            if decision["span_ids"]:
                raise ValueError("Uncertain proposal cannot silently select a position")
            unresolved.append(decision["proposal_id"])
        elif (
            not decision["span_ids"]
            or not set(decision["span_ids"]) <= set(ledger[decision["proposal_id"]]["span_ids"])
            or not set(decision["span_ids"]) <= selected
        ):
            raise ValueError("Proposal selection must resolve to explicit Mention positions")
    mentions = []
    for mention in result["mentions"]:
        entry = entries[mention["span_id"]]
        mentions.append(
            {
                "id": "m_" + canonical_hash(mention)[:40],
                "actor_id": mention["actor_id"],
                "kind": mention["kind"],
                "evidence": entry["selector"].copy(),
            }
        )
    return {
        "status": "unresolved" if unresolved else "source_grounded_stage",
        "mentions": mentions,
        "unresolved_proposals": unresolved,
        "receipt": {
            "catalog_sha256": canonical_hash(catalog),
            "selection_sha256": canonical_hash(result),
            "bindings": [
                {"mention_id": m["id"], "span_id": s["span_id"]}
                for m, s in zip(mentions, result["mentions"], strict=True)
            ],
        },
        "document_accepted": False,
        "semantic_attribution_verified": False,
    }
