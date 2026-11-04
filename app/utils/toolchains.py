"""The programs builds run with: one pixi environment per toolchain, e.g. per CEOS-ARD CLI version, installed on demand."""

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.utils.process import command_env, run

logger = logging.getLogger(__name__)

# Also a folder name
NAME_PATTERN = r"[a-z0-9][a-z0-9.\-]*"
# Written once a toolchain is completely installed
_READY = ".ready"
# Packages with syntax errors, e.g. tests for other Python versions, don't fail the install
_COMPILE_PACKAGES = "import compileall, site\nfor path in site.getsitepackages():\n    compileall.compile_dir(path, quiet=2, workers=0)"


class ToolchainError(Exception):
    pass


@dataclass(frozen=True)
class ToolchainSpec:
    conda: dict[str, str]
    pypi: dict[str, str] = field(default_factory=dict)
    # Installs Playwright's Chromium into PLAYWRIGHT_BROWSERS_PATH, e.g. for PDFs
    chromium: bool = False


@dataclass(frozen=True)
class Toolchain:
    name: str
    # Only called to install it
    spec: Callable[[], Awaitable[ToolchainSpec]]


def pixi_platform() -> str:
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "win-arm64" if arm else "win-64"
    if sys.platform == "darwin":
        return "osx-arm64" if arm else "osx-64"
    return "linux-aarch64" if arm else "linux-64"


def _manifest(name: str, spec: ToolchainSpec) -> str:
    def table(entries: dict[str, str]) -> str:
        return "".join(f"{json.dumps(key)} = {json.dumps(value)}\n" for key, value in entries.items())

    return (
        f'[workspace]\nname = {json.dumps(name)}\nchannels = ["conda-forge"]\nplatforms = [{json.dumps(pixi_platform())}]\n\n'
        f"[dependencies]\n{table(spec.conda)}\n[pypi-dependencies]\n{table(spec.pypi)}"
    )


class Toolchains:
    def __init__(self, root: Path):
        self.root = root
        self._installs: dict[str, asyncio.Task] = {}

    def path(self, name: str) -> Path:
        if not re.fullmatch(NAME_PATTERN, name):
            raise ValueError(f"Invalid toolchain name: {name}")
        return self.root / name

    def env(self, name: str) -> Path:
        return self.path(name) / ".pixi" / "envs" / "default"

    def bin_path(self, name: str) -> list[Path]:
        env = self.env(name)
        if sys.platform == "win32":
            library = env / "Library"
            return [env, library / "mingw-w64" / "bin", library / "usr" / "bin", library / "bin", env / "Scripts", env / "bin"]
        return [env / "bin"]

    def is_installed(self, name: str) -> bool:
        return (self.path(name) / _READY).exists()

    def installed(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [path.name for path in self.root.iterdir() if (path / _READY).exists()]

    async def ensure(self, toolchain: Toolchain) -> None:
        """Install the toolchain unless it is; waits for an install that is already running."""
        if not self.is_installed(toolchain.name):
            # Shielded: a cancelled request mustn't leave a half-installed toolchain
            await asyncio.shield(self._start(toolchain))

    def prepare(self, toolchain: Toolchain) -> None:
        """Start installing the toolchain in the background, so the first build doesn't wait for it."""
        if not self.is_installed(toolchain.name):
            self._start(toolchain)

    def _start(self, toolchain: Toolchain) -> asyncio.Task:
        task = self._installs.get(toolchain.name)
        if task is None:
            task = asyncio.create_task(self._install(toolchain))
            self._installs[toolchain.name] = task
            task.add_done_callback(lambda task: self._finished(toolchain.name, task))
        return task

    def _finished(self, name: str, task: asyncio.Task) -> None:
        self._installs.pop(name, None)
        if not task.cancelled() and (error := task.exception()):
            logger.warning(f"Installing the toolchain {name} failed: {error}")

    async def _install(self, toolchain: Toolchain) -> None:
        path = self.path(toolchain.name)
        spec = await toolchain.spec()
        logger.info(f"Installing the toolchain {toolchain.name}")
        start = time.monotonic()
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        (path / ".pixi").mkdir(parents=True)
        # In the toolchain's folder, which builds see, even if pixi is configured to keep environments elsewhere
        (path / ".pixi" / "config.toml").write_text("detached-environments = false\n")
        (path / "pixi.toml").write_text(_manifest(toolchain.name, spec))

        env = command_env()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(settings.PLAYWRIGHT_BROWSERS_PATH)
        await self._run(toolchain.name, env, "pixi", "install", "--manifest-path", str(path / "pixi.toml"))
        bin_path = os.pathsep.join(map(str, self.bin_path(toolchain.name)))
        if python := shutil.which("python", path=bin_path):
            # Builds can't write bytecode into the read-only toolchain, and compiling it on every build takes 0.3 s
            await self._run(toolchain.name, env, python, "-c", _COMPILE_PACKAGES)
        if spec.chromium:
            playwright = shutil.which("playwright", path=bin_path)
            if playwright is None:
                raise ToolchainError(f"The toolchain {toolchain.name} has no Playwright for PDFs")
            await self._run(toolchain.name, env, playwright, "install", "--only-shell", "chromium")
        (path / _READY).touch()
        logger.info(f"Installed the toolchain {toolchain.name} in {time.monotonic() - start:.1f} s")

    @staticmethod
    async def _run(name: str, env: dict[str, str], *args: str) -> None:
        try:
            result = await run(*args, timeout=settings.TOOLCHAIN_INSTALL_TIMEOUT, env=env)
        except TimeoutError as e:
            raise ToolchainError(f"Installing the toolchain {name} took too long") from e
        if result.returncode != 0:
            logger.error(f"Installing the toolchain {name} failed: {result.stderr.decode(errors='replace')[-2000:]}")
            raise ToolchainError(f"Installing the toolchain {name} failed")


toolchains = Toolchains(settings.TOOLCHAINS_ROOT)
