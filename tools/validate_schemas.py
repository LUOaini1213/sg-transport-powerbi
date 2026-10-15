"""Validate every PBIP/PBIR JSON file against the JSON schema it declares in "$schema".

The schemas are Microsoft's published ones (https://github.com/microsoft/json-schemas), read from a local copy
so that their relative "$ref"s resolve offline; tools/fetch_schemas.sh makes that copy at a pinned commit.

    python tools/validate_schemas.py --schemas .schemas/json-schemas

Exit code 1 if any file fails, declares no schema, or declares a schema that is not in the copy.
"""
import argparse
import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "https://developer.microsoft.com/json-schemas/"
PROJECT_JSON = ["SGTransport.pbip", "SGTransport.Report/definition.pbir", "SGTransport.Report/.platform",
                "SGTransport.SemanticModel/definition.pbism", "SGTransport.SemanticModel/.platform"]


def project_files(root):
    files = [root / p for p in PROJECT_JSON]
    files += sorted((root / "SGTransport.Report" / "definition").rglob("*.json"))
    return files


def make_registry(schema_root):
    def retrieve(uri):
        if not uri.startswith(PREFIX):
            raise NoSuchResource(ref=uri)
        path = schema_root / uri[len(PREFIX):].split("#")[0]
        if not path.exists():
            raise NoSuchResource(ref=uri)
        return Resource.from_contents(json.loads(path.read_text(encoding="utf-8-sig")))
    return Registry(retrieve=retrieve)


def validate(root, schema_root):
    registry = make_registry(schema_root)
    failures, checked = [], []
    for f in project_files(root):
        rel = f.relative_to(root).as_posix()
        doc = json.loads(f.read_text(encoding="utf-8"))
        url = doc.get("$schema")
        if not url or not url.startswith(PREFIX):
            failures.append(f"{rel}: no Microsoft $schema declared")
            continue
        path = schema_root / url[len(PREFIX):]
        if not path.exists():
            failures.append(f"{rel}: schema {url} not found in {schema_root}")
            continue
        schema = json.loads(path.read_text(encoding="utf-8-sig"))
        cls = validator_for(schema)
        cls.check_schema(schema)
        errors = sorted(cls(schema, registry=registry).iter_errors(doc), key=lambda e: list(e.absolute_path))
        for e in errors:
            loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
            failures.append(f"{rel}: {loc}: {e.message[:300]}")
        checked.append((rel, url[len(PREFIX):]))
    return checked, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemas", type=Path, default=ROOT / ".schemas" / "json-schemas")
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args()
    checked, failures = validate(a.root.resolve(), a.schemas.resolve())
    for rel, schema in checked:
        print(f"ok   {rel}  [{schema}]")
    for f in failures:
        print(f"FAIL {f}")
    print(f"{len(checked)} files checked, {len(failures)} problems")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
