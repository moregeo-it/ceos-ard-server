import asyncio
import logging
from pathlib import Path

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

    async def start_build(self, workspace_path: str, workspace_id: str, pfs: list[str] | None) -> bool:
        if not workspace_path:
            raise ValueError("Workspace path must be provided")

        if not workspace_id:
            raise ValueError("Workspace ID must be provided")

        try:
            if not self.prereqs_ok:
                logger.error("Prerequisites not met, cannot start build")
                raise RuntimeError("Prerequisites not met, cannot start build")

            if not Path(workspace_path).exists():
                logger.error(f"Workspace path {workspace_path} does not exist")
                raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

            await self._execute_build(workspace_path, workspace_id, pfs)
        except Exception as e:
            logger.error(f"Fatal error starting build for workspace {workspace_id}: {e}")
            raise RuntimeError(f"Fatal error starting build for workspace {workspace_id}: {e}") from e

    async def _execute_build(self, workspace_path: str, workspace_id: str, pfs: list[str] | None) -> bool:
        output_dir = Path(workspace_path) / "build" / ("-".join(pfs) if pfs else "")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", output_dir, "-i", workspace_path, "--pdf", "--docx"]

        build_type_desc = f" with PFS {pfs}" if pfs else " (all files)"
        logm_messsge = f"Building workspace {workspace_id}{build_type_desc}"
        logger.info(logm_messsge)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await process.wait()

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                success_msg = f"Build completed successfully for workspace {workspace_id}"
                logger.info(success_msg + f" - Output: {stdout.decode().strip()}")
                return True
            else:
                error_msg = f"Build failed for workspace {workspace_id}: Process exited with code {process.returncode}"
                logger.error(error_msg + f" - Error: {stderr.decode().strip()}")
                return False

        except Exception as e:
            error_msg = f"Build process error for workspace {workspace_id}: {e}"
            logger.error(error_msg)
            return False


build_service = BuildService()
