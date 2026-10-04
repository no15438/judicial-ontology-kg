"""Explicitly regenerate the schema after a reviewed ontology/contract change."""

import json
from pathlib import Path

from judicial_kg.contracts import CONTRACT_FILES, build_schema

root = Path(__file__).resolve().parents[1]
for ontology_file, schema_file in CONTRACT_FILES.values():
    ontology = json.loads((root / ontology_file).read_text())
    path = root / schema_file
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(build_schema(ontology), ensure_ascii=False, indent=2) + "\n")
    print(schema_file)
