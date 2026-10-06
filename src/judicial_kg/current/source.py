"""Immutable source units and reversible horizontal-space matching."""

import re

from judicial_kg.extraction.anchored import source_units
from judicial_kg.sources.records import evidence_span


def compact(text):
    positions = [i for i, c in enumerate(text) if c not in " \u3000\t"]
    return "".join(text[i] for i in positions), positions


def locate(doc, units, selector, allowed):
    uid = selector["unit"]
    if type(uid) is not int or uid not in allowed:
        raise ValueError("source_unit_outside_scope")
    unit = units[uid]
    quote = selector["text"]
    if not isinstance(quote, str) or not quote:
        raise ValueError("empty_source_quote")
    matches = [
        (m.start(), m.start() + len(quote))
        for m in re.finditer("(?=" + re.escape(quote) + ")", unit["text"])
    ]
    method = "exact"
    if not matches:
        needle, _ = compact(quote)
        haystack, mapping = compact(unit["text"])
        if not needle:
            raise ValueError("empty_source_quote")
        matches = [
            (mapping[m.start()], mapping[m.start() + len(needle) - 1] + 1)
            for m in re.finditer("(?=" + re.escape(needle) + ")", haystack)
        ]
        if len({unit["text"][a:b] for a, b in matches}) > 1:
            raise ValueError("ambiguous_source_variants")
        method = "reversible_horizontal_space_mapping"
    occurrence = selector["occurrence"]
    if occurrence is None:
        if len(matches) != 1:
            raise ValueError("source_position_not_unique")
        occurrence = 0
    if type(occurrence) is not int or not 0 <= occurrence < len(matches):
        raise ValueError("source_position_missing")
    a, b = matches[occurrence]
    return evidence_span(doc, unit["field"], unit["start"] + a, unit["start"] + b), method


def batches(doc, size=16):
    """Contiguous clause windows are processing boundaries, not legal contexts."""
    units = source_units(doc)
    rows, active = [], []
    for i, unit in enumerate(units):
        if active and (len(active) >= size or units[active[-1]]["field"] != unit["field"]):
            rows.append(active)
            active = []
        active.append(i)
    if active:
        rows.append(active)
    return units, rows
