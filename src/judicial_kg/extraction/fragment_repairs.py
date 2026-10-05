"""V28 repairs discovery only by choosing maximal source-supported literal fragments."""

from judicial_kg.extraction import checked_forms, context_ids, scoped, scoped_incremental
from judicial_kg.extraction.anchored import source_units
from judicial_kg.extraction.draft import DraftError

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v28.txt"
GENERATION_CONSTRAINT_VERSION = "bounded-source-fragment-discovery-correction-v1"
GENERATION_CONSTRAINT_SCOPE = "explicit_fragment_choice_never_automatic_identity_resolution"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format


def source_fragments(doc, units, original, missing):
    source = [u["text"] for u in source_units(doc) if u["id"] in units]
    result = {}
    for key in missing:
        query = original["forms"][int(key[1:])]
        if not 0 < len(query) <= 128:
            raise DraftError("form_fragment_query_too_long")
        candidates = {
            query[start:end]
            for start in range(len(query))
            for end in range(start + 1, len(query) + 1)
            if any(c.isalnum() for c in query[start:end])
            and any(query[start:end] in text for text in source)
        }
        maximal = sorted(
            t for t in candidates if not any(t != other and t in other for other in candidates)
        )
        if not maximal:
            raise DraftError("form_fragment_candidates_missing")
        result[key] = maximal
    return result


def extract(doc, prompt, contract, provenance, infer, save):
    budget = {"used": 0}

    def body(*args):
        return checked_forms.generate_body(*args, budget=budget, repair_options=source_fragments)

    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        generate_header=context_ids.generate_header,
        generate_body=body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
