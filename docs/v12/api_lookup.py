#!/usr/bin/env python3
"""Query docs/v12/openapi-v12.json without reading 1.3 MB of JSON.

    python3 docs/v12/api_lookup.py list collections          # every path containing 'collections'
    python3 docs/v12/api_lookup.py show /api/v1/collections/installments get
    python3 docs/v12/api_lookup.py schema InstallmentRow      # one component schema, refs expanded 1 level
"""
import json
import sys
from pathlib import Path

SPEC = json.loads((Path(__file__).parent / "openapi-v12.json").read_text())
SCHEMAS = SPEC.get("components", {}).get("schemas", {})


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _compact(node, depth=0):
    if isinstance(node, dict):
        if "$ref" in node:
            name = _ref_name(node["$ref"])
            if depth >= 1:
                return f"<{name}>"
            return {"$schema": name, **_compact(SCHEMAS.get(name, {}), depth + 1)}
        return {k: _compact(v, depth) for k, v in node.items() if k not in ("title",)}
    if isinstance(node, list):
        return [_compact(x, depth) for x in node]
    return node


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "list":
        needle = argv[2] if len(argv) > 2 else ""
        for path, ops in sorted(SPEC["paths"].items()):
            if needle in path:
                print(f"{','.join(m.upper() for m in ops):<22} {path}")
    elif cmd == "show":
        op = SPEC["paths"][argv[2]][argv[3].lower()]
        out = {
            "summary": op.get("summary"),
            "params": [
                {"name": p["name"], "in": p["in"], "required": p.get("required", False), "schema": p.get("schema")}
                for p in op.get("parameters", [])
            ],
            "body": _compact(op.get("requestBody", {}).get("content", {})),
            "responses": {k: _compact(v.get("content", {})) for k, v in op.get("responses", {}).items()},
        }
        print(json.dumps(out, indent=1, ensure_ascii=False))
    elif cmd == "schema":
        print(json.dumps(_compact({"$ref": "#/components/schemas/" + argv[2]}), indent=1, ensure_ascii=False))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
