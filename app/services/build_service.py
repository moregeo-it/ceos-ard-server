import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from app.schemas.build import BuildInfo, BuildLog, BuildStatus, LogType

logger = logging.getLogger(__name__)


class BuildService:
    def __init__(self):
        self.build_processes: dict[str, BuildInfo] = {}
        self.cleanup_interval = 30 * 60  # 30 minutes

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

    async def start_build(self, workspace_path: str, workspace_id: str, pfs: list[str] | None) -> BuildInfo:
        build_info = BuildInfo(status=BuildStatus.STARTING, build_type="specific" if pfs else "all", pfs=pfs, automatic=True)

        try:
            if workspace_id in self.build_processes:
                existing_build = self.build_processes[workspace_id]

                if (
                    existing_build.process
                    and existing_build.process.returncode is None
                    and existing_build.status in [BuildStatus.STARTING, BuildStatus.IN_PROGRESS]
                ):
                    logger.info(f"Build already in progress for workspace {workspace_id}")
                    return existing_build

                logger.info(f"Previous build for workspace {workspace_id} completed, starting new one")

            self.build_processes[workspace_id] = build_info

            prereqs_ok = await self.check_prerequisites()
            if not prereqs_ok:
                build_info.status = BuildStatus.FAILED
                build_info.error = "ceos-ard CLI tool is not installed or not available"
                build_info.end_time = time.time()
                build_info.logs.append(BuildLog(type=LogType.ERROR, text=build_info.error))
                return build_info

            if not Path(workspace_path).exists():
                build_info.status = BuildStatus.FAILED
                build_info.error = f"Workspace path does not exist: {workspace_path}"
                build_info.end_time = time.time()
                build_info.logs.append(BuildLog(type=LogType.ERROR, text=build_info.error))
                return build_info

            await self._execute_build(build_info, workspace_path, workspace_id, pfs)

            return build_info

        except Exception as e:
            build_info.status = BuildStatus.FAILED
            build_info.error = str(e)
            build_info.end_time = time.time()
            build_info.logs.append(BuildLog(type=LogType.ERROR, text=f"Fatal error starting build: {e}"))
            logger.error(f"Fatal error starting build for workspace {workspace_id}: {e}")
            return build_info

    async def _execute_build(self, build_info: BuildInfo, workspace_path: str, workspace_id: str, pfs: list[str] | None):
        build_info.status = BuildStatus.IN_PROGRESS
        output_dir = Path(workspace_path) / "build" / ("-".join(pfs) if pfs else "")

        cmd_args = ["ceos-ard", "generate", *pfs, "-o", output_dir, "-i", workspace_path, "--pdf", "--docx"]

        build_type_desc = f" with PFS {pfs}" if pfs else " (all files)"
        logm_messsge = f"Building workspace {workspace_id}{build_type_desc}"
        build_info.logs.append(BuildLog(type=LogType.INFO, text=logm_messsge))
        logger.info(logm_messsge)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            build_info.process = process

            stdout_task = asyncio.create_task(self._read_stream(process.stdout, build_info, LogType.STDOUT))
            stderr_task = asyncio.create_task(self._read_stream(process.stderr, build_info, LogType.STDERR))

            returncode = await process.wait()

            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

            build_info.end_time = time.time()

            if returncode == 0:
                build_info.status = BuildStatus.COMPLETED
                success_msg = f"Build completed successfully for workspace {workspace_id}"
                build_info.logs.append(BuildLog(type=LogType.INFO, text=success_msg))
                logger.info(success_msg)
            else:
                build_info.status = BuildStatus.FAILED
                build_info.error = f"Process exited with code {returncode}"
                error_msg = f"Build failed for workspace {workspace_id}: Process exited with code {returncode}"
                build_info.logs.append(BuildLog(type=LogType.ERROR, text=error_msg))
                logger.error(error_msg)

        except Exception as e:
            build_info.status = BuildStatus.FAILED
            build_info.error = str(e)
            build_info.end_time = time.time()
            error_msg = f"Build process error for workspace {workspace_id}: {e}"
            build_info.logs.append(BuildLog(type=LogType.ERROR, text=error_msg))
            logger.error(error_msg)

        finally:
            # Schedule cleanup after 30 minutes
            asyncio.create_task(self._schedule_cleanup(workspace_id, build_info))

    async def _read_stream(self, stream: asyncio.StreamReader, build_info: BuildInfo, log_type: LogType):
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break

                log_text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                if log_text:  # Only log non-empty lines
                    build_info.logs.append(BuildLog(type=log_type, text=log_text))

        except Exception as e:
            logger.error(f"Error reading stream: {e}")

    async def _schedule_cleanup(self, workspace_id: str, build_info: BuildInfo):
        await asyncio.sleep(self.cleanup_interval)

        # Only remove if it's still the same build info object
        if workspace_id in self.build_processes and self.build_processes[workspace_id] is build_info:
            del self.build_processes[workspace_id]
            logger.info(f"Cleaned up build info for workspace {workspace_id}")

    def get_build_status(self, workspace_id: str) -> dict[str, Any] | None:
        if workspace_id not in self.build_processes:
            return None

        build_info = self.build_processes[workspace_id]

        # Return a dictionary without the process reference for safety
        return {
            "status": build_info.status.value,
            "logs": [{"type": log.type.value, "text": log.text, "timestamp": log.timestamp.isoformat()} for log in build_info.logs],
            "start_time": build_info.start_time,
            "end_time": build_info.end_time,
            "error": build_info.error,
            "build_type": build_info.build_type,
            "pfs": build_info.pfs,
            "automatic": build_info.automatic,
            "duration": ((build_info.end_time or time.time()) - build_info.start_time),
        }

    async def cancel_build(self, workspace_id: str) -> bool:
        if workspace_id not in self.build_processes:
            return False

        build_info = self.build_processes[workspace_id]

        if build_info.process and build_info.process.returncode is None:
            try:
                build_info.process.terminate()

                # Wait a bit for graceful termination
                try:
                    await asyncio.wait_for(build_info.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # Force kill if it doesn't terminate gracefully
                    build_info.process.kill()
                    await build_info.process.wait()

                build_info.status = BuildStatus.FAILED
                build_info.error = "Build cancelled by user"
                build_info.end_time = time.time()
                build_info.logs.append(BuildLog(type=LogType.INFO, text="Build cancelled"))

                logger.info(f"Build cancelled for workspace {workspace_id}")
                return True

            except Exception as e:
                logger.error(f"Error cancelling build for workspace {workspace_id}: {e}")
                return False

        return False

    def get_all_build_statuses(self) -> dict[str, dict[str, Any]]:
        return {workspace_id: self.get_build_status(workspace_id) for workspace_id in self.build_processes}


# Global build service instance
build_service = BuildService()
