import logging
from pathlib import Path
from typing import Any

from app.utils.cli_utils import run

logger = logging.getLogger(__name__)


class BuildService:
    async def build(self, workspace_path: Path, workspace_id: str, pfs: list[str] | None, include_format: str | None = None) -> dict[str, Any]:
        if not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided")

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        output_file = workspace_path / "build" / ("-".join(pfs) if pfs else "")

        logger.info(f"Building workspace {workspace_id} {'with PFS ' + ' '.join(pfs) if pfs else '(all files)'}")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", str(output_file), "-i", str(workspace_path), "--pdf", "--docx"]

        if include_format == "pdf":
            cmd_args.remove("--pdf")
        elif include_format == "docx":
            cmd_args.remove("--docx")

        logger.debug(f"Executing build command: {' '.join(cmd_args)}")

        process = await run(*cmd_args)
        if process.returncode == 0:
            return {"status": "success", "message": "Build completed successfully.", "output_file": str(output_file)}
        else:
            logger.error(f"Build failed for workspace {workspace_id} with code {process.returncode}:")
            return {
                "status": "error",
                "message": "Generating document failed, likely one of the changes caused an issue (e.g., invalid YAML).",
                "output_file": str(output_file),
            }


build_service = BuildService()
