"""V25 explicitly permits grounded collective mentions without merging actor identities."""

from functools import partial

from judicial_kg.extraction import (
    context_ids,
    mention_stage,
    owner_first,
    scoped,
    scoped_incremental,
    typed_owner,
)

CONTRACT_VERSION = context_ids.CONTRACT_VERSION
NORMALIZER_VERSION = context_ids.NORMALIZER_VERSION
PROMPT_FILE = "prompts/qwen-extraction-v25.txt"
GENERATION_CONSTRAINT_VERSION = "collective-mentions-with-owner-typed-properties-v1"
GENERATION_CONSTRAINT_SCOPE = "multiple_actor_bindings_same_exact_span_not_actor_merge"
draft_schema = context_ids.draft_schema
response_format = context_ids.response_format

INSTRUCTION = (
    "\n同一span_id允许对应多个不同actor_id，不是只能选一个人。"
    "原文‘双方’‘二人’等集合指代，若本语境明确其组成，可为每个成员分别生成"
    "同一span_id的pronoun Mention，再在proposal_decisions选择该位置一次。"
    "不得创建合并Actor，不得把集合指代只绑定其中一人；无法确定成员时仍保留uncertain，"
    "不得为了通过校验猜测成员。此处允许集合归属不代表所有同名主体可以合并。"
)


def generate_body(
    doc, prompt, combined, context, contract, infer, save, index, *, facts_generator=None
):
    return mention_stage.generate_body(
        doc,
        prompt,
        combined,
        context,
        contract,
        infer,
        save,
        index,
        facts_generator=facts_generator
        or partial(
            owner_first.generate_facts,
            schema_builder=typed_owner.property_schema,
            property_instruction=typed_owner.INSTRUCTION,
        ),
        mention_instruction=INSTRUCTION,
    )


def extract(doc, prompt, contract, provenance, infer, save):
    return scoped.extract(
        doc,
        prompt,
        contract,
        provenance,
        infer,
        save,
        generate_header=context_ids.generate_header,
        generate_body=generate_body,
        merge=scoped_incremental.merge_body,
        on_header=scoped_incremental.header_diagnostics,
    )
