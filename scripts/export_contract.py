"""Explicitly regenerate the schema after a reviewed ontology/contract change."""

import json
from pathlib import Path

from judicial_kg.contracts import ONTOLOGY_FILE, SCHEMA_FILE, build_schema

root = Path(__file__).resolve().parents[1]
ontology = json.loads((root / ONTOLOGY_FILE).read_text())
path = root / SCHEMA_FILE
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(build_schema(ontology), ensure_ascii=False, indent=2) + "\n")
print(SCHEMA_FILE)
