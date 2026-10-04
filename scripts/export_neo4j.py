"""Export a saved candidate graph to local CSV/Cypher without contacting Neo4j."""

import argparse
import json
from pathlib import Path

from judicial_kg.graph.neo4j import export_neo4j

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source-run", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
print(json.dumps(export_neo4j(Path.cwd(), args.source_run, args.output), indent=2))
