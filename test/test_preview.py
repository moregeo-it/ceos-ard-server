import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import dependencies
from app.db.database import SessionLocal
from app.models.workspace import GitWorkspace
from app.services.preview_service import PreviewService


@pytest.fixture
def builds(monkeypatch):
    """Replaces the ceos-ard CLI with a build that writes a small document."""
    calls = []

    async def build(workspace_path, workspace_id, workspace_type, pfs, include_format=None, build_dir=None):
        calls.append({"pfs": pfs, "include_format": include_format})
        prefix = Path(dependencies.build_service.output_prefix(workspace_path, pfs, build_dir))
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".html").write_text(
            "<h1>Preview</h1><!-- edit: pfs/NRB/document.yaml --><p>Text</p>",
            encoding="utf-8",
        )
        for suffix in (".pdf", ".docx"):
            prefix.with_suffix(suffix).write_bytes(b"document")
        return {"status": "success", "message": "Build completed successfully.", "output_file": str(prefix)}

    monkeypatch.setattr(dependencies.build_service, "build", build)
    return calls


def test_preview_builds_the_selected_pfs(client, workspace, builds):
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 200, response.text
    assert builds == [{"pfs": ["NRB"], "include_format": None}]
    assert '<button class="edit" value="/pfs/NRB/document.yaml">Edit</button>' in response.text

    response = client.get(f"/workspaces/{workspace['id']}/previews/current")
    assert response.status_code == 200, response.text
    assert "<h1>Preview</h1>" in response.text


def test_preview_requires_a_pfs(client, workspace, builds):
    with SessionLocal() as db:
        db.get(GitWorkspace, workspace["id"]).pfs = []
        db.commit()

    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Select at least one PFS for the preview"


@pytest.mark.parametrize(("format", "media_type"), [("pdf", "application/pdf"), ("docx", "application/vnd.openxmlformats")])
def test_download_document(client, workspace, builds, format, media_type):
    response = client.get(f"/workspaces/{workspace['id']}/download", params={"format": format, "pfs": ["NRB"]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media_type)
    assert builds[0]["pfs"] == ["NRB"]


def test_each_user_runs_one_build_at_a_time():
    running = []
    peak = 0

    async def build(**kwargs):
        nonlocal peak
        running.append(kwargs["workspace_id"])
        peak = max(peak, len(running))
        await asyncio.sleep(0.05)
        running.pop()
        return {"status": "success", "output_file": "prefix"}

    service = PreviewService(build_service=SimpleNamespace(build=build))
    workspace = SimpleNamespace(abs_path=Path("."), id="workspace", workspace_type=None)

    async def builds(users):
        await asyncio.gather(*(service._build(workspace, user, ["NRB"]) for user in users))

    asyncio.run(builds(["jane", "jane", "jane"]))
    assert peak == 1
    peak = 0
    asyncio.run(builds(["jane", "john"]))
    assert peak == 2
