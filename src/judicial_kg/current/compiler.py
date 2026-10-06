"""Compile independent assertions transactionally; quarantine invalid dependencies."""

import copy

from jsonschema import Draft202012Validator

from judicial_kg.contracts import candidate_record, collections
from judicial_kg.current.source import locate
from judicial_kg.sources.records import canonical_hash, evidence_span
from judicial_kg.validation import validate_record


class Compiler:
    def __init__(self, doc, units, contract, provenance):
        self.doc, self.units, self.contract = doc, units, contract
        self.record = candidate_record(doc, provenance, [f["name"] for f in doc["fields"]], "0.5.0")
        self.issues, self.mappings = [], []

    def identifier(self, *parts):
        return "r_" + canonical_hash([self.doc["document_id"], *parts])[:40]

    def spans(self, ids, allowed):
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("empty_or_duplicate_evidence")
        result = []
        for i in ids:
            if type(i) is not int or i not in allowed:
                raise ValueError("evidence_outside_scope")
            u = self.units[i]
            result.append(evidence_span(self.doc, u["field"], u["start"], u["end"]))
        return result

    def mention(self, selector, allowed, path):
        span, method = locate(self.doc, self.units, selector, allowed)
        self.mappings.append({"path": path, "selector": selector, "span": span, "method": method})
        return span

    def attempt(self, path, operation):
        before = copy.deepcopy(self.record)
        try:
            result = operation()
            issues = validate_record(self.record, self.doc, self.contract)
            if issues:
                self.issues.append({"path": path, "status": "quarantined", "issues": issues})
                self.record = before
                return None
            return result
        except (ValueError, KeyError, TypeError, IndexError) as error:
            self.record = before
            # Store schema paths/codes, not arbitrary exception contents.
            self.issues.append(
                {
                    "path": path,
                    "status": "quarantined",
                    "code": (
                        str(error)
                        if isinstance(error, ValueError)
                        and str(error)
                        in {
                            "wire_schema_invalid",
                            "dependency_quarantined",
                            "local_reference_out_of_range",
                            "verifier_rejected",
                            "source_unit_outside_scope",
                            "empty_source_quote",
                            "ambiguous_source_variants",
                            "source_position_not_unique",
                            "source_position_missing",
                            "empty_or_duplicate_evidence",
                            "evidence_outside_scope",
                        }
                        else type(error).__name__
                    ),
                }
            )
            return None

    @staticmethod
    def check(value, schema):
        if isinstance(value, dict) and value.get("quarantined_by_verifier") is True:
            raise ValueError("verifier_rejected")
        if not Draft202012Validator(schema).is_valid(value):
            raise ValueError("wire_schema_invalid")

    def context(self, wire, schema, targets, prefix):
        cid = self.identifier(prefix, "context")
        props = schema["properties"]
        actor_map, collective_map, object_map, event_map = {}, {}, {}, {}
        defs = {
            k: props[k]["items"]
            for k in (
                "actors",
                "collectives",
                "objects",
                "roles",
                "events",
                "statements",
                "unresolved",
            )
        }

        def ref(mapping, index, collection):
            if index in mapping:
                return mapping[index]
            if type(index) is int and 0 <= index < len(wire[collection]):
                raise ValueError("dependency_quarantined")
            raise ValueError("local_reference_out_of_range")

        # Invalid optional identities do not erase unrelated actors or assertions.
        for i, actor in enumerate(wire.get("actors", [])):
            path = f"{prefix}/actors/{i}"

            def add_actor(actor=actor, i=i, path=path):
                self.check(actor, defs["actors"])
                span = self.mention(actor["mention"], set(range(len(self.units))), path)
                aid, mid = (
                    self.identifier(prefix, "actor", i),
                    self.identifier(prefix, "mention", i),
                )
                self.record["actors"].append(
                    {"id": aid, "actor_type": actor["actor_type"], "mention_ids": [mid]}
                )
                self.record["mentions"].append(
                    {"id": mid, "actor_id": aid, "kind": "description", "evidence": span}
                )
                return aid

            aid = self.attempt(path, add_actor)
            if aid:
                actor_map[i] = aid

        def add_context():
            core = {**wire, **{k: [] for k in defs}}
            self.check(core, schema)
            spans = self.spans(wire["units"], targets)
            self.record["contexts"].append(
                {
                    "id": cid,
                    "kind": wire["kind"],
                    "attributed_actor_ids": [ref(actor_map, i, "actors") for i in wire["speakers"]],
                    "parent_context_id": None,
                    "evidence": spans,
                    "assertion_evidence": spans,
                }
            )
            return cid

        if not self.attempt(prefix, add_context):
            return
        scope = set(wire["units"])

        for i, group in enumerate(wire["collectives"]):
            path = f"{prefix}/collectives/{i}"

            def add_group(group=group, i=i, path=path):
                self.check(group, defs["collectives"])
                span = self.mention(group["mention"], scope, path)
                gid, mid = (
                    self.identifier(prefix, "group", i),
                    self.identifier(prefix, "group_mention", i),
                )
                self.record["collectives"].append(
                    {
                        "id": gid,
                        "context_id": cid,
                        "collective_type": group["collective_type"],
                        "membership_status": group["membership_status"],
                        "mention_ids": [mid],
                    }
                )
                self.record["collective_mentions"].append(
                    {"id": mid, "collective_id": gid, "context_id": cid, "evidence": span}
                )
                for j, member in enumerate(group["members"]):
                    self.record["collective_memberships"].append(
                        {
                            "id": self.identifier(prefix, "member", i, j),
                            "collective_id": gid,
                            "actor_id": ref(actor_map, member["actor"], "actors"),
                            "context_id": cid,
                            "polarity": member["polarity"],
                            "evidence": self.spans(member["units"], scope),
                        }
                    )
                return gid

            gid = self.attempt(path, add_group)
            if gid:
                collective_map[i] = gid
        for i, obj in enumerate(wire["objects"]):
            path = f"{prefix}/objects/{i}"

            def add_object(obj=obj, i=i, path=path):
                self.check(obj, defs["objects"])
                span = self.mention(obj["mention"], scope, path)
                oid, mid = (
                    self.identifier(prefix, "object", i),
                    self.identifier(prefix, "object_mention", i),
                )
                self.record["objects"].append(
                    {"id": oid, "object_type": obj["object_type"], "mention_ids": [mid]}
                )
                self.record["object_mentions"].append(
                    {"id": mid, "object_id": oid, "context_id": cid, "evidence": span}
                )
                return mid

            mid = self.attempt(path, add_object)
            if mid:
                object_map[i] = mid
        for i, role in enumerate(wire["roles"]):

            def add_role(role=role, i=i):
                self.check(role, defs["roles"])
                self.record["roles"].append(
                    {
                        "id": self.identifier(prefix, "role", i),
                        "actor_id": ref(actor_map, role["actor"], "actors"),
                        "role": role["role"],
                        "context_id": cid,
                        "polarity": role["polarity"],
                        "evidence": self.spans(role["units"], scope),
                    }
                )
                return True

            self.attempt(f"{prefix}/roles/{i}", add_role)
        for i, event in enumerate(wire["events"]):

            def add_event(event=event, i=i):
                self.check(event, defs["events"])
                eid = self.identifier(prefix, "event", i)
                spans = self.spans(event["units"], scope)
                participants = []
                for j, p in enumerate(event["participants"]):
                    t = p["target"]
                    if t["kind"] == "actor":
                        participants.append(
                            {"actor_id": ref(actor_map, t["index"], "actors"), "role": p["role"]}
                        )
                    else:
                        self.record["collective_participations"].append(
                            {
                                "id": self.identifier(prefix, "participant", i, j),
                                "collective_id": ref(collective_map, t["index"], "collectives"),
                                "event_id": eid,
                                "context_id": cid,
                                "role": p["role"],
                                "polarity": event["polarity"],
                                "evidence": spans,
                            }
                        )
                self.record["events"].append(
                    {
                        "id": eid,
                        "event_type": event["event_type"],
                        "context_id": cid,
                        "participants": participants,
                        "polarity": event["polarity"],
                        "evidence": spans,
                    }
                )
                for j, index in enumerate(event["objects"]):
                    mid = ref(object_map, index, "objects")
                    mention = next(m for m in self.record["object_mentions"] if m["id"] == mid)
                    self.record["object_links"].append(
                        {
                            "id": self.identifier(prefix, "object_link", i, j),
                            "object_mention_id": mid,
                            "event_id": eid,
                            "context_id": cid,
                            "relation": "involves_object",
                            "polarity": event["polarity"],
                            "evidence": [mention["evidence"]],
                        }
                    )
                return eid

            eid = self.attempt(f"{prefix}/events/{i}", add_event)
            if eid:
                event_map[i] = eid
        for i, statement in enumerate(wire["statements"]):

            def add_statement(statement=statement, i=i):
                self.check(statement, defs["statements"])
                value = statement["value"]
                span = self.mention(value["source"], scope, f"{prefix}/statements/{i}/value")
                target = actor_map if statement["subject_kind"] == "actor" else event_map
                self.record["statements"].append(
                    {
                        "id": self.identifier(prefix, "statement", i),
                        "subject_id": ref(
                            target,
                            statement["subject"],
                            "actors" if statement["subject_kind"] == "actor" else "events",
                        ),
                        "predicate": statement["predicate"],
                        "context_id": cid,
                        "polarity": statement["polarity"],
                        "evidence": self.spans(statement["units"], scope),
                        "value": {
                            "kind": value["kind"],
                            "raw": span["quote"],
                            "normalized": None,
                            "unit": None,
                        },
                    }
                )
                return True

            self.attempt(f"{prefix}/statements/{i}", add_statement)
        for i, item in enumerate(wire["unresolved"]):
            self.issues.append(
                {"path": f"{prefix}/unresolved/{i}", "status": "model_unresolved", "proposal": item}
            )

    def counts(self):
        return {key: len(self.record[key]) for key in collections(self.record)}
