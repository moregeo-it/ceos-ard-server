import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.schemas.workspace import PFS_ID_PATTERN
from app.utils.cli_utils import run

logger = logging.getLogger(__name__)


class BuildService:
    @staticmethod
    def output_prefix(workspace_path: Path, pfs: list[str] | None) -> str:
        """Where a selection's outputs go (`<prefix>.html`, `.pdf`, `.docx`).

        Every build and read goes through here, so the ids are checked here too, stored ones included:
        `../` in an id would reach another workspace, a leading `-` would be a CLI option.
        """
        if not pfs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one PFS for the preview")
        if not all(re.fullmatch(PFS_ID_PATTERN, pfs_id) for pfs_id in pfs):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PFS id")
        return str(workspace_path / "build" / "-".join(pfs))

    async def build(self, workspace_path: Path, workspace_id: str, pfs: list[str], include_format: str | None = None) -> dict[str, Any]:
        if not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided")

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        output_file = self.output_prefix(workspace_path, pfs)

        logger.info(f"Building workspace {workspace_id} with PFS {' '.join(pfs)}")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", output_file, "-i", str(workspace_path), "--pdf", "--docx"]

        if include_format == "pdf":
            cmd_args.remove("--pdf")
        elif include_format == "docx":
            cmd_args.remove("--docx")

        logger.debug(f"Executing build command: {' '.join(cmd_args)}")

        process = await run(*cmd_args)
        if process.returncode == 0:
            return {"status": "success", "message": "Build completed successfully.", "output_file": output_file}
        else:
            logger.error(f"Build failed for workspace {workspace_id} with code {process.returncode}:")
            return {
                "status": "error",
                "message": "Generating document failed, likely one of the changes caused an issue (e.g., invalid YAML).",
                "output_file": output_file,
            }
