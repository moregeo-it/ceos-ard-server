import asyncio
import sys
import time
from pathlib import Path

import pytest

from app.config import settings
from app.utils.sandbox import BuildOutputTooLarge, run_build

# Writes into the output folder until it is stopped
LARGE = """
import pathlib, sys
with open(pathlib.Path(sys.argv[1], "large.bin"), "wb") as file:
    while True:
        file.write(b"x" * 65536)
"""
# Writes too much at once and is done before the next check
FINITE = """
import pathlib, sys
pathlib.Path(sys.argv[1], "large.bin").write_bytes(b"x" * 8 * 1024 * 1024)
"""
MANY = """
import pathlib, sys
i = 0
while True:
    pathlib.Path(sys.argv[1], f"{i}.txt").write_text("x")
    i += 1
"""


def build(tmp_path: Path, script: str):
    workspace, output = tmp_path / "workspace", tmp_path / "output"
    workspace.mkdir()
    output.mkdir()
    (workspace / "script.py").write_text(script)
    python = Path(sys.executable)
    asyncio.run(
        run_build(
            [python.name, "script.py", str(output)],
            path=[python.parent],
            read_only=[Path(sys.prefix), workspace],
            writable=[output],
            hidden=[],
            cwd=workspace,
            timeout=30,
        )
    )


@pytest.mark.parametrize("script", [LARGE, FINITE, MANY], ids=["large", "large and done", "many files"])
def test_a_build_that_writes_too_much_is_stopped(tmp_path, monkeypatch, script):
    monkeypatch.setattr(settings, "BUILD_OUTPUT_MAX", "5M")
    start = time.monotonic()
    with pytest.raises(BuildOutputTooLarge):
        build(tmp_path, script)
    assert time.monotonic() - start < 20
    # It stopped writing when it was stopped
    written = sum(path.stat().st_size for path in (tmp_path / "output").iterdir())
    time.sleep(0.5)
    assert sum(path.stat().st_size for path in (tmp_path / "output").iterdir()) == written
