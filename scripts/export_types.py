#!/usr/bin/env python
"""Print the workspace types as GET /types returns them, e.g. for the mocked API of the editor's tests.

pixi run python scripts/export_types.py ceos-ard > ../ceos-ard-editor/e2e/support/types/ceos-ard.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.schemas.workspace_type import WorkspaceTypeResponse  # noqa: E402
from app.workspace_types import all_types, available_types  # noqa: E402


def main() -> int:
    wanted = sys.argv[1:]
    available = available_types()
    types = [
        WorkspaceTypeResponse.model_validate(workspace_type.to_public(workspace_type in available)).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        for workspace_type in all_types()
        if not wanted or workspace_type.id in wanted
    ]
    print(json.dumps(types[0] if len(wanted) == 1 else types, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
