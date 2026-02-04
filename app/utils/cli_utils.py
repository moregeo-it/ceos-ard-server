import asyncio
import logging
import subprocess
import sys
import tomllib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings

logger = logging.getLogger(__name__)


def load_project_info() -> tuple[str, str]:
    # Read project metadata from pixi.toml
    with open("pixi.toml", "rb") as f:
        _pixi_config = tomllib.load(f)
        name = _pixi_config["workspace"]["name"]
        version = _pixi_config["workspace"]["version"]
    return name, version


def _run_subprocess(*args, timeout=60) -> subprocess.CompletedProcess:
    """Run a command in the pixi environment synchronously."""
    return subprocess.run(
        ["pixi", "run", "--", *args],
        capture_output=True,
        timeout=timeout,
    )


async def run(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run a command in the pixi environment asynchronously."""
    return await asyncio.to_thread(_run_subprocess, *args, **kwargs)


@asynccontextmanager
async def fastapi_run_checks(app: FastAPI):
    await run_checks()
    yield


async def run_checks():
    checks = {
        "CEOS-ARD CLI": check_ceos_ard_cli,
        "Playwright": check_playwright,
    }
    failures = []
    for check_name, check_func in checks.items():
        try:
            await check_func()
        except Exception as e:
            failures.append(f"{check_name}: {e}")

    if len(failures) > 0:
        logger.error("\n!!!! CHECKS FAILED !!!!\n - " + "\n - ".join(failures) + "\n")
        if settings.ENVIRONMENT == "production":
            sys.exit(1)
    else:
        logger.info("All prerequisite checks passed.")


async def check_playwright():
    """Check if Playwright browsers are installed."""
    try:
        result = await run("playwright", "install", "--list", timeout=10)
        if result.returncode != 0:
            raise Exception(f"Failed with exit code {result.returncode}, likely needs to be reinstalled")
        if "chromium_headless_shell" not in result.stdout.decode():
            raise Exception("Chromium browser not installed, run 'pixi run install-browser'")
    except FileNotFoundError as e:
        raise Exception("Not installed or not available in the PATH") from e
    except Exception:
        raise


async def check_ceos_ard_cli():
    """Check if ceos-ard CLI is available."""
    try:
        result = await run("ceos-ard", "--version", timeout=10)
        if result.returncode != 0:
            raise Exception(f"Returned exit code {result.returncode}, likely needs to be reinstalled")
    except FileNotFoundError as e:
        raise Exception("Not installed or not available in the PATH") from e
    except Exception:
        raise
