"""
Runs builds isolated: in bubblewrap, and each in a cgroup of its own under the server's delegated cgroup.

    <server's cgroup>    Delegate=yes in the systemd unit
    ├── main/            the server, preferred for CPU
    └── builds/          BUILD_CPU_CORES and BUILD_MEMORY_MAX for all builds, no swap
        └── <build>/     BUILD_MEMORY_MAX_PER_BUILD, killed as a whole on timeout or out of memory

Linux only; see `setup()` for the conditions.
"""

import asyncio
import contextlib
import errno
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.utils.process import command_env, run

logger = logging.getLogger(__name__)

# Bounds concurrent builds: a burst of preview requests would otherwise run one build per request
_semaphore = asyncio.Semaphore(settings.BUILD_CONCURRENCY)

CGROUP_ROOT = Path("/sys/fs/cgroup")
CONTROLLERS = ("cpu", "memory", "pids")
# Moves the shell into the build's cgroup and makes the build the OOM killer's first choice, then runs it
_ENTER_CGROUP = 'echo 0 > "$1/cgroup.procs" && echo 500 > /proc/self/oom_score_adj && shift && exec "$@"'
# What a build sees of the system besides /usr: links or folders, depending on the distribution
_SYSTEM_DIRS = ("/bin", "/lib", "/lib32", "/lib64", "/libx32", "/sbin")
_SYSTEM_FILES = ("/etc/fonts", "/etc/ld.so.cache", "/etc/localtime")


class BuildOutOfMemory(Exception):
    pass


class BuildOutputTooLarge(Exception):
    pass


# Output folders are checked this often, and may hold at most this many files
_OUTPUT_CHECK_INTERVAL = 0.2
_OUTPUT_MAX_FILES = 10_000


def _output_exceeds(paths: list[Path], max_size: int) -> bool:
    """Whether the folders hold more than `max_size` bytes or _OUTPUT_MAX_FILES files; stops counting at either."""
    size = files = 0
    pending = list(paths)
    while pending:
        try:
            entries = list(os.scandir(pending.pop()))
        except OSError:
            continue
        for entry in entries:
            files += 1
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                else:
                    # Not entry.stat(): Windows keeps the size of a file still being written in its directory entry stale
                    size += os.stat(entry.path, follow_symlinks=False).st_size
            except OSError:
                continue
            if size > max_size or files > _OUTPUT_MAX_FILES:
                return True
    return False


def parse_size(value: str) -> int:
    """Bytes from e.g. `700M` or `1.5G`."""
    value = value.strip().upper().removesuffix("B")
    units = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
    if value[-1:] in units:
        return int(float(value[:-1]) * units[value[-1]])
    return int(value)


def _write(path: Path, value: str) -> None:
    path.write_text(value)


class Cgroups:
    def __init__(self, base: Path):
        self.base = base
        self.builds = base / "builds"

    @classmethod
    def delegate(cls) -> "Cgroups":
        """Split the server's delegated cgroup into `main/` and `builds/`."""
        if not (CGROUP_ROOT / "cgroup.controllers").exists():
            raise Exception(f"No cgroup v2 at {CGROUP_ROOT}")
        own = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
        base = CGROUP_ROOT / own.strip("/")
        if base.name == "main" and (base.parent / "builds").is_dir():
            base = base.parent
        if not os.access(base / "cgroup.subtree_control", os.W_OK) or not os.access(base / "cgroup.procs", os.W_OK):
            raise Exception(f"The cgroup {base} isn't delegated to the server, see Delegate=yes in the README")
        missing = set(CONTROLLERS) - set((base / "cgroup.controllers").read_text().split())
        if missing:
            raise Exception(f"The cgroup {base} lacks the controllers {', '.join(sorted(missing))}")

        main = base / "main"
        main.mkdir(exist_ok=True)
        # Only a cgroup without processes can pass controllers on to its children
        for _ in range(10):
            pids = (base / "cgroup.procs").read_text().split()
            if not pids:
                break
            for pid in pids:
                try:
                    _write(main / "cgroup.procs", pid)
                except ProcessLookupError:
                    pass
        _write(base / "cgroup.subtree_control", " ".join(f"+{controller}" for controller in CONTROLLERS))
        _write(main / "cpu.weight", "1000")

        cgroups = cls(base)
        cgroups.builds.mkdir(exist_ok=True)
        # Builds left over from before a restart
        for leftover in cgroups.builds.iterdir():
            if leftover.is_dir():
                cgroups.remove(leftover)
        _write(cgroups.builds / "cpu.max", f"{round(settings.BUILD_CPU_CORES * 100000)} 100000")
        _write(cgroups.builds / "memory.max", str(parse_size(settings.BUILD_MEMORY_MAX)))
        _write_if_exists(cgroups.builds / "memory.swap.max", "0")
        _write(cgroups.builds / "cgroup.subtree_control", " ".join(f"+{controller}" for controller in CONTROLLERS))
        return cgroups

    def create(self) -> Path:
        cgroup = self.builds / uuid.uuid4().hex
        cgroup.mkdir()
        try:
            _write(cgroup / "memory.max", str(parse_size(settings.BUILD_MEMORY_MAX_PER_BUILD)))
            _write_if_exists(cgroup / "memory.swap.max", "0")
            _write(cgroup / "memory.oom.group", "1")
            _write(cgroup / "pids.max", "1024")
        except BaseException:
            cgroup.rmdir()
            raise
        return cgroup

    @staticmethod
    def out_of_memory(cgroup: Path) -> bool:
        events = dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines())
        return int(events.get("oom_kill", 0)) > 0

    @staticmethod
    def remove(cgroup: Path) -> None:
        """Kill whatever still runs in the cgroup, then remove it. Blocking."""
        deadline = time.monotonic() + 5
        while True:
            if (cgroup / "cgroup.kill").exists():
                _write(cgroup / "cgroup.kill", "1")
            else:
                for pid in (cgroup / "cgroup.procs").read_text().split():
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                cgroup.rmdir()
                return
            except OSError as e:
                if e.errno != errno.EBUSY or time.monotonic() > deadline:
                    raise
            time.sleep(0.01)


def _write_if_exists(path: Path, value: str) -> None:
    # memory.swap.max is missing without swap accounting
    if path.exists():
        _write(path, value)


@dataclass
class Isolation:
    sandbox: bool = False
    cgroups: Cgroups | None = None


isolation = Isolation()


async def setup() -> None:
    """Enable what isolation the system allows; raises with the reason if builds can't be fully isolated."""
    isolation.sandbox, isolation.cgroups = False, None
    if sys.platform != "linux":
        raise Exception("Builds can only be isolated on Linux")
    if not settings.BUILD_SANDBOX:
        raise Exception("BUILD_SANDBOX is false")
    try:
        result = await run("bwrap", "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "true", timeout=10)
    except FileNotFoundError as e:
        raise Exception("bubblewrap (bwrap) isn't installed") from e
    if result.returncode != 0:
        raise Exception(f"bubblewrap can't create a sandbox, check that user namespaces are allowed: {result.stderr.decode().strip()}")
    isolation.sandbox = True
    isolation.cgroups = await asyncio.to_thread(Cgroups.delegate)


def _bwrap_args(read_only: list[Path], writable: list[Path], hidden: list[Path], cwd: Path) -> list[str]:
    # Order matters: later mounts go on top of earlier ones
    args = ["--unshare-all", "--die-with-parent", "--new-session", "--tmpfs", "/tmp"]
    for path in _SYSTEM_DIRS:
        if os.path.islink(path):
            args += ["--symlink", os.readlink(path), path]
        elif os.path.isdir(path):
            args += ["--ro-bind", path, path]
    args += ["--ro-bind", "/usr", "/usr", "--proc", "/proc", "--dev", "/dev"]
    for path in _SYSTEM_FILES:
        args += ["--ro-bind-try", path, path]
    for path in read_only:
        args += ["--ro-bind-try", str(path), str(path)]
    for path in hidden:
        args += ["--tmpfs", str(path)]
    for path in writable:
        args += ["--bind", str(path), str(path)]
    return [*args, "--chdir", str(cwd)]


async def run_build(
    command: list[str], *, path: list[Path], read_only: list[Path], writable: list[Path], hidden: list[Path], cwd: Path, timeout: float
) -> subprocess.CompletedProcess:
    """
    Run a build with the programs in `path`, isolated as far as `setup()` allowed.

    The build sees the system's libraries, `read_only`, `writable` and an empty `/tmp`, which is also
    its HOME, but no network, other processes or environment variables beyond its PATH and locale.
    `hidden` paths are replaced by empty folders. Raises TimeoutError or BuildOutOfMemory.
    """
    executable = shutil.which(command[0], path=os.pathsep.join(map(str, path)))
    if executable is None:
        raise FileNotFoundError(f"{command[0]} isn't installed in the toolchain")
    command = [executable, *command[1:]]
    browsers = settings.PLAYWRIGHT_BROWSERS_PATH

    if isolation.sandbox:
        env = {
            "PATH": os.pathsep.join([*map(str, path), "/usr/bin", "/bin"]),
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "PLAYWRIGHT_BROWSERS_PATH": str(browsers),
        }
        command = ["bwrap", *_bwrap_args([*read_only, browsers], writable, hidden, cwd), "--", *command]
    else:
        env = command_env()
        env["PATH"] = os.pathsep.join([*map(str, path), env.get("PATH", "")])
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)

    async with _semaphore:
        cgroup = isolation.cgroups.create() if isolation.cgroups else None
        try:
            if cgroup:
                command = ["/bin/sh", "-c", _ENTER_CGROUP, "sh", str(cgroup), *command]
            build = asyncio.ensure_future(run(*command, timeout=timeout, env=env, cwd=cwd))
            try:
                # The output folders are on disk, unlike /tmp, so their size is limited by watching them
                max_size = parse_size(settings.BUILD_OUTPUT_MAX)
                while not build.done():
                    await asyncio.wait([build], timeout=_OUTPUT_CHECK_INTERVAL)
                    if not build.done() and await asyncio.to_thread(_output_exceeds, writable, max_size):
                        raise BuildOutputTooLarge()
            finally:
                if not build.done():
                    build.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await build
            result = build.result()
            if cgroup and Cgroups.out_of_memory(cgroup):
                raise BuildOutOfMemory()
            return result
        finally:
            if cgroup:
                await asyncio.to_thread(Cgroups.remove, cgroup)
