"""Lossless shared JSON catalogues for repeated choice contexts; no source summarization."""

import json
from collections import Counter


def pack(value, minimum=180):
    counts = Counter()

    def key(node):
        return json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def count(node):
        if isinstance(node, dict) and set(node) == {"_shared_ref"}:
            raise ValueError("reserved_shared_reference")
        serialized = key(node)
        if len(serialized) >= minimum:
            counts[serialized] += 1
        children = (
            node.values() if isinstance(node, dict) else node if isinstance(node, list) else []
        )
        for child in children:
            count(child)

    count(value)
    catalogue, names = {}, {}

    def encode(node, share=True):
        serialized = key(node)
        if share and counts[serialized] > 1:
            if serialized not in names:
                name = f"s{len(names)}"
                names[serialized] = name
                catalogue[name] = encode(node, False)
            return {"_shared_ref": names[serialized]}
        if isinstance(node, dict):
            return {k: encode(v) for k, v in node.items()}
        if isinstance(node, list):
            return [encode(v) for v in node]
        return node

    request = encode(value)
    packed = {"shared": catalogue, "request": request}
    if unpack(packed) != value:
        raise ValueError("shared_catalogue_roundtrip")
    return packed


def unpack(packed):
    def decode(node, visited=frozenset()):
        if isinstance(node, dict):
            if set(node) == {"_shared_ref"}:
                name = node["_shared_ref"]
                if name in visited or name not in packed["shared"]:
                    raise ValueError("invalid_shared_reference")
                return decode(packed["shared"][name], visited | {name})
            return {k: decode(v, visited) for k, v in node.items()}
        if isinstance(node, list):
            return [decode(v, visited) for v in node]
        return node

    return decode(packed["request"])
