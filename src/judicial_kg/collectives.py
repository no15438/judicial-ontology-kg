"""Checks for source-scoped collective candidates; never infer anonymous members."""


def validate_collectives(record):
    issues = []
    groups = {g["id"]: g for g in record["collectives"]}
    mentions = {m["id"]: m for m in record["collective_mentions"]}
    actors = {a["id"] for a in record["actors"]}
    contexts = {c["id"]: c for c in record["contexts"]}
    events = {e["id"]: e for e in record["events"]}

    def issue(code, path):
        issues.append({"code": code, "path": path})

    def within(span, scope):
        return any(
            s["field"] == span["field"] and s["start"] <= span["start"] and span["end"] <= s["end"]
            for s in scope
        )

    for i, group in enumerate(record["collectives"]):
        path = f"/collectives/{i}"
        if group["context_id"] not in contexts:
            issue("unresolved_reference", path)
        for mid in group["mention_ids"]:
            m = mentions.get(mid)
            if m is None or m["collective_id"] != group["id"]:
                issue("collective_mention_disagreement", path)
        members = [m for m in record["collective_memberships"] if m["collective_id"] == group["id"]]
        confirmed = [m for m in members if m["polarity"] == "affirmed"]
        status = group["membership_status"]
        if status == "unknown" and members:
            issue("unknown_collective_has_members", path)
        if status in {"partial", "explicit"} and not confirmed:
            issue("collective_member_evidence_required", path)
        if status == "explicit" and len({m["actor_id"] for m in confirmed}) < 2:
            issue("explicit_collective_requires_multiple_members", path)

    seen_positions = set()
    for key in ("collective_mentions", "collective_memberships", "collective_participations"):
        seen_links = set()
        for i, node in enumerate(record[key]):
            path = f"/{key}/{i}"
            group = groups.get(node["collective_id"])
            context = contexts.get(node["context_id"])
            if group is None or context is None:
                issue("unresolved_reference", path)
                continue
            if group["context_id"] != node["context_id"]:
                issue("collective_context_mismatch", path)
            spans = [node["evidence"]] if key == "collective_mentions" else node["evidence"]
            if not all(within(s, context["assertion_evidence"]) for s in spans):
                issue("collective_evidence_outside_assertion_scope", path)
            if key == "collective_mentions":
                if node["id"] not in group["mention_ids"]:
                    issue("collective_mention_disagreement", path)
                span = node["evidence"]
                position = (span["field"], span["start"], span["end"])
                if position in seen_positions:
                    issue("duplicate_collective_mention_evidence", path)
                seen_positions.add(position)
                if any(
                    m["evidence"] == span for m in record["mentions"] + record["object_mentions"]
                ):
                    issue("collective_position_type_conflict", path)
            elif key == "collective_memberships":
                if node["actor_id"] not in actors:
                    issue("unresolved_reference", path)
                link = (node["collective_id"], node["actor_id"], node["polarity"])
                if link in seen_links:
                    issue("duplicate_collective_membership", path)
                seen_links.add(link)
            else:
                event = events.get(node["event_id"])
                if event is None:
                    issue("unresolved_reference", path)
                    continue
                if event["context_id"] != node["context_id"]:
                    issue("collective_event_context_mismatch", path)
                if event["polarity"] != node["polarity"]:
                    issue("collective_event_polarity_mismatch", path)
                if not all(within(s, event["evidence"]) for s in spans):
                    issue("collective_link_outside_event_evidence", path)
                link = (node["collective_id"], node["event_id"], node["role"])
                if link in seen_links:
                    issue("duplicate_collective_participation", path)
                seen_links.add(link)
    for i, event in enumerate(record["events"]):
        if not event["participants"] and not any(
            p["event_id"] == event["id"] for p in record["collective_participations"]
        ):
            issue("event_participant_required", f"/events/{i}")
    return issues
