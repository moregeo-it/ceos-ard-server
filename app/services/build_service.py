import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.utils.cli_checker import CEOS_ARD_AVAILABLE

logger = logging.getLogger(__name__)


class BuildService:
    def __init__(self):
        self.prereqs_ok = CEOS_ARD_AVAILABLE  # Use the global check result

    async def start_build(
        self, workspace_path: Path, workspace_id: str, pfs: list[str] | None, include_document_type: str | None = None
    ) -> dict[str, Any]:
        if not self.prereqs_ok or not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided and prerequisites must be met")

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        return await self._execute_build(workspace_path, workspace_id, pfs, include_document_type)

    async def _execute_build(
        self, workspace_path: Path, workspace_id: str, pfs: list[str] | None, include_document_type: str | None = None
    ) -> dict[str, Any]:
        output_file = workspace_path / "build" / ("-".join(pfs) if pfs else "")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", str(output_file), "-i", str(workspace_path), "--pdf", "--docx"]

        if include_document_type == "pdf":
            cmd_args.remove("--pdf")
        elif include_document_type == "docx":
            cmd_args.remove("--docx")

        logger.info(f"Building workspace {workspace_id} {'with PFS ' + ' '.join(pfs) if pfs else '(all files)'}")

        try:
            process = await asyncio.create_subprocess_exec(*cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

            await process.communicate()

            if process.returncode == 0:
                return {"status": "success", "message": f"Build completed successfully for workspace {workspace_id}", "output_file": str(output_file)}
            else:
                return {
                    "status": "error",
                    "message": f"Build failed for workspace {workspace_id}: Process exited with code {process.returncode}",
                    "output_file": str(output_file),
                }

        except Exception as e:
            error_msg = f"Build process error for workspace {workspace_id}: {e}"
            logger.error(error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg,
            ) from e


build_service = BuildService()
