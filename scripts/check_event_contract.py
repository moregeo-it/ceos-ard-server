#!/usr/bin/env python
"""Guard against drift between the realtime event contract in code and in the OpenAPI spec.

Checks that:
- the `WorkspaceEventType` enum in openapi.yaml matches `EventType` in app/schemas/events.py,
- the `WorkspaceEvent` discriminator mapping covers exactly those event types,
- every schema referenced by the mapping exists.

The client-side counterpart (ceos-ard-editor scripts/check-event-contract.mjs) compares the same
enum against the editor's `src/services/events.js`. Run via pre-commit.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.schemas.events import EventType  # noqa: E402


def main() -> int:
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    errors = []

    code_types = {event_type.value for event_type in EventType}
    spec_types = set(schemas["WorkspaceEventType"]["enum"])
    if code_types != spec_types:
        missing_in_spec = code_types - spec_types
        missing_in_code = spec_types - code_types
        if missing_in_spec:
            errors.append(f"Event types missing in openapi.yaml WorkspaceEventType: {sorted(missing_in_spec)}")
        if missing_in_code:
            errors.append(f"Event types in openapi.yaml but not in app/schemas/events.py: {sorted(missing_in_code)}")

    mapping = schemas["WorkspaceEvent"].get("discriminator", {}).get("mapping", {})
    if set(mapping) != code_types:
        errors.append(f"WorkspaceEvent discriminator mapping does not cover the event types exactly: {sorted(set(mapping) ^ code_types)}")

    for event_type, ref in mapping.items():
        name = ref.rsplit("/", 1)[-1]
        if name not in schemas:
            errors.append(f"Discriminator mapping for '{event_type}' references missing schema '{name}'")

    if errors:
        print("Realtime event contract drift detected (app/schemas/events.py vs openapi.yaml):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Event contract OK ({len(code_types)} event types).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
