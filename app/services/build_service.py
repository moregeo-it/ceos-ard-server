import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class BuildService:
    def __init__(self):
        self.prereqs_ok = self.check_prerequisites()  # Check prerequisites on initialization

    async def check_prerequisites(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                "ceos-ard",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await process.communicate()

            return process.returncode == 0

        except FileNotFoundError:
            logger.error("ceos-ard CLI is not installed or not available in the PATH")
            return False

        except Exception as e:
            logger.error(f"Error checking prequisites: {e}")
            return False

    async def start_build(self, workspace_path: str, workspace_id: str, pfs: list[str] | None) -> dict[str, Any]:
        if not self.prereqs_ok or not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided and prerequisites must be met")

        if not Path(workspace_path).exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        return await self._execute_build(workspace_path, workspace_id, pfs)

    async def _execute_build(self, workspace_path: str, workspace_id: str, pfs: list[str] | None) -> dict[str, Any]:
        output_dir = Path(workspace_path) / "build" / ("-".join(pfs) if pfs else "")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", output_dir, "-i", workspace_path, "--pdf", "--docx"]

        logger.info(f"Building workspace {workspace_id} {'with PFS ' + ' '.join(pfs) if pfs else '(all files)'}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return {"status": "success", "message": f"Build completed successfully for workspace {workspace_id}", "output_dir": str(output_dir)}
            else:
                return {
                    "status": "error",
                    "message": f"Build failed for workspace {workspace_id}: Process exited with code {process.returncode}",
                    "output_dir": str(output_dir),
                }

        except Exception as e:
            error_msg = f"Build process error for workspace {workspace_id}: {e}"
            logger.error(error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg,
            ) from e


build_service = BuildService()
