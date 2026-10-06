"""V41: source-preserving horizontal-space mapping before object routing."""

import re

from judicial_kg.extraction import position_consistency, source_discovery
from judicial_kg.extraction.draft import DraftError
from judicial_kg.extraction.occurrence_routing import OccurrenceRouter
from judicial_kg.sources.records import evidence_span

CONTRACT_VERSION = source_discovery.CONTRACT_VERSION
NORMALIZER_VERSION = source_discovery.NORMALIZER_VERSION
PROMPT_FILE = source_discovery.PROMPT_FILE
GENERATION_CONSTRAINT_VERSION = "source-horizontal-space-map-v1"
GENERATION_CONSTRAINT_SCOPE = "reversible-source-mapping-not-identity-or-semantic-repair"
draft_schema = source_discovery.draft_schema
response_format = source_discovery.response_format


def horizontal_view(text):
    """Only ASCII/ideographic space runs fold; no deleted spaces or crossed newlines."""
    chars, offsets = [], []
    for match in re.finditer(r"[ \u3000]+|[^ \u3000]", text):
        chars.append(" " if match[0][0] in " \u3000" else match[0])
        offsets.append((match.start(), match.end()))
    return "".join(chars), offsets


def resolve_forms(doc, units, forms):
    mapped, ledger = [], []
    for index, proposed in enumerate(forms["forms"]):
        if not isinstance(proposed, str) or not proposed.strip(" \u3000"):
            raise DraftError("source_form_empty")
        needle, _ = horizontal_view(proposed)
        candidates = []
        for unit in units:
            view, offsets = horizontal_view(unit["text"])
            for match in re.finditer("(?=" + re.escape(needle) + ")", view):
                start = offsets[match.start()][0]
                end = offsets[match.start() + len(needle) - 1][1]
                candidates.append(
                    evidence_span(doc, unit["field"], unit["start"] + start, unit["start"] + end)
                )
        variants = {span["quote"] for span in candidates}
        row = {
            "proposal_index": index,
            "proposed": proposed,
            "candidates": candidates,
            "method": GENERATION_CONSTRAINT_VERSION,
        }
        ledger.append(row)
        if not candidates:
            row["status"] = "missing"
        elif len(variants) != 1:
            row["status"] = "ambiguous_source_variants"
        else:
            literal = next(iter(variants))
            row.update(
                status="exact" if literal == proposed else "space_mapped", source_form=literal
            )
            if literal not in mapped:
                mapped.append(literal)
            row["output_index"] = mapped.index(literal)
    return {"forms": mapped}, ledger


class SourcePositionRouter(OccurrenceRouter):
    def route(self, forms, unit_ids, stage):
        units = [unit for unit in self.units if unit["id"] in unit_ids]
        mapped, ledger = resolve_forms(self.doc, units, forms)
        self.save(
            stage + "-source-position-map",
            {"input": forms, "output": mapped, "ledger": ledger, "semantic_verified": False},
        )
        if any(row["status"] == "missing" for row in ledger):
            raise DraftError("object_routing_missing_source")
        if any(row["status"] == "ambiguous_source_variants" for row in ledger):
            raise DraftError("source_form_mapping_ambiguous")
        return super().route(mapped, unit_ids, stage)


def extract(doc, prompt, contract, provenance, infer, save):
    def dispatch(messages, fmt, stage):
        return infer(source_discovery.discovery_messages(messages, stage), fmt, stage)

    return position_consistency.extract(
        doc, prompt, contract, provenance, dispatch, save, router_class=SourcePositionRouter
    )
