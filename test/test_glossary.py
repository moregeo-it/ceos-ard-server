import sys
import time
from pathlib import Path

import pytest
from conftest import GLOSSARY_FORK, file_url, git
from pydantic import ValidationError

from app.db.database import SessionLocal
from app.models.workspace import GitWorkspace
from app.workspace_types import eo_glossary_render
from app.workspace_types.base import Action

TERMS = "/docs/terms"


def test_glossary_type(client):
    glossary = next(item for item in client.get("/types").json() if item["id"] == "eo-glossary")
    assert glossary["pfs"] is False
    assert glossary["preview"] == "file"
    assert glossary["upstream"]["repo"] == "eo-glossary"
    assert [action["id"] for action in glossary["actions"]] == ["new-term"]
    assert [item["id"] for item in glossary["search_filters"]] == ["status", "class", "group"]


def test_create_glossary_workspace(client, github, glossary_workspace):
    assert ("fork", "ceos-org", "eo-glossary") in github.calls
    assert glossary_workspace["type"] == "eo-glossary"
    assert glossary_workspace["pfs"] is None
    assert glossary_workspace["upstream_repo"] == "eo-glossary"
    branch = f"workspace/{glossary_workspace['id']}"
    assert git("branch", "--list", branch, cwd=GLOSSARY_FORK).strip(" *") == branch


def test_only_docs_are_shown(client, glossary_workspace):
    response = client.get(f"/workspaces/{glossary_workspace['id']}/files", params={"path": "/"})
    assert [item["name"] for item in response.json()] == ["docs"]
    response = client.get(f"/workspaces/{glossary_workspace['id']}/files/README.md")
    assert response.status_code == 404


def test_terms_list_the_terms_linking_to_them(client, glossary_workspace):
    response = client.get(file_url(glossary_workspace, "context", f"{TERMS}/accuracy.md"))
    assert response.status_code == 200, response.text
    assert response.json()["usage"] == [{"label": "Precision", "path": f"{TERMS}/precision.md"}]


def test_render_a_term(client, glossary_workspace):
    response = client.get(file_url(glossary_workspace, "render", f"{TERMS}/precision.md"))
    assert response.status_code == 200, response.text
    assert "<h1>Precision</h1>" in response.text
    assert f'data-open-file="{TERMS}/accuracy.md"' in response.text

    response = client.get(file_url(glossary_workspace, "render", "/docs/intro.md"))
    assert response.status_code == 200, response.text
    assert "The glossary." in response.text


# Each level repeats the one before nine times: 9^8 strings once turned into text
ALIAS_BOMB = (
    "---\na0: &a0 x\n" + "".join(f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]\n" for i in range(1, 9)) + "title: *a8\ntags: *a8\n---\n\n# Bomb\n"
)


def workspace_path(workspace) -> Path:
    with SessionLocal() as db:
        return db.get(GitWorkspace, workspace["id"]).abs_path


def test_yaml_aliases_are_refused(client, glossary_workspace):
    (workspace_path(glossary_workspace) / "docs/terms/bomb.md").write_text(ALIAS_BOMB, encoding="utf-8")
    start = time.monotonic()
    response = client.get(file_url(glossary_workspace, "render", f"{TERMS}/bomb.md"))
    assert response.status_code == 200, response.text
    assert "Aliases aren&#x27;t allowed" in response.text
    response = client.get(f"/workspaces/{glossary_workspace['id']}/search", params={"filter": ["status:approved"]})
    assert [item["path"] for item in response.json()] == [f"{TERMS}/accuracy.md"]
    assert time.monotonic() - start < 20


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks need extra rights on Windows")
def test_symlinks_out_of_the_workspace_are_not_read(client, glossary_workspace, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("---\ntitle: Leaked\ntags:\n  - approved\n---\n\n# Leaked\n\n## 1 Definition\n\nLeaked secret Term Name.\n")
    root = workspace_path(glossary_workspace)
    (root / "docs/terms/leaked.md").symlink_to(secret)
    (root / "docs/_template.md").symlink_to(secret)

    response = client.get(file_url(glossary_workspace, "render", f"{TERMS}/precision.md"))
    assert response.status_code == 200, response.text
    assert "Leaked" not in response.text
    response = client.get(file_url(glossary_workspace, "context", f"{TERMS}/precision.md"))
    assert response.status_code == 200, response.text
    assert "Leaked" not in response.text
    response = client.get(f"/workspaces/{glossary_workspace['id']}/search", params={"filter": ["status:approved"]})
    assert [item["path"] for item in response.json()] == [f"{TERMS}/accuracy.md"]

    response = client.post(f"/workspaces/{glossary_workspace['id']}/actions/new-term", json={"title": "Data Cube"})
    assert response.status_code == 201, response.text
    assert "Leaked" not in (root / "docs/terms/data_cube.md").read_text()


def test_glossary_has_no_pfs_preview(client, glossary_workspace):
    response = client.post(f"/workspaces/{glossary_workspace['id']}/previews")
    assert response.status_code == 400, response.text


def test_search_filters(client, glossary_workspace):
    search = f"/workspaces/{glossary_workspace['id']}/search"
    response = client.get(search, params={"filter": ["status:approved"]})
    assert response.status_code == 200, response.text
    assert [item["path"] for item in response.json()] == [f"{TERMS}/accuracy.md"]

    response = client.get(search, params={"filter": ["group:WGCV"]})
    assert [item["path"] for item in response.json()] == [f"{TERMS}/accuracy.md", f"{TERMS}/precision.md"]

    response = client.get(search, params={"query": "repeated", "filter": ["group:WGCV"]})
    assert [item["path"] for item in response.json()] == [f"{TERMS}/precision.md"]


def test_new_term(client, glossary_workspace):
    action = f"/workspaces/{glossary_workspace['id']}/actions/new-term"
    response = client.post(action, json={"title": "Data Cube"})
    assert response.status_code == 201, response.text
    assert response.json()["path"] == f"{TERMS}/data_cube.md"
    assert response.json()["status"] == "added"

    response = client.post(action, json={"title": "Accuracy"})
    assert response.status_code == 409, response.text


def test_terms_can_be_deleted_but_not_renamed(client, glossary_workspace):
    base = f"/workspaces/{glossary_workspace['id']}/files"
    response = client.patch(file_url(glossary_workspace, "files", f"{TERMS}/precision.md"), json={"operation": "rename", "target": "other.md"})
    assert response.status_code == 403, response.text
    response = client.post(base, json={"path": TERMS, "name": "other.md", "type": "file"})
    assert response.status_code == 403, response.text
    response = client.delete(file_url(glossary_workspace, "files", f"{TERMS}/precision.md"))
    assert response.status_code in (200, 204), response.text


@pytest.mark.parametrize(
    "action",
    [
        {"id": "a", "title": "A", "folder": "/docs"},
        {"id": "a", "title": "A", "folder": "/docs", "schema": {}, "dialog": "CreatePfsDialog"},
    ],
)
def test_actions_have_a_schema_or_a_dialog(action):
    with pytest.raises(ValidationError):
        Action.model_validate(action)


def test_dialog_actions_are_not_run_generically(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/actions/create-pfs", json={})
    assert response.status_code == 404, response.text


def test_saving_does_not_create_terms(client, glossary_workspace):
    # Terms are created with "New term", from the glossary's template
    response = client.put(file_url(glossary_workspace, "files", f"{TERMS}/new.md"), content="# New\n")
    assert response.status_code == 403, response.text


def test_terms_too_large_to_hold_in_memory_are_not_read(client, glossary_workspace, monkeypatch):
    monkeypatch.setattr(eo_glossary_render, "MAX_FILE_SIZE", 100)
    response = client.get(f"/workspaces/{glossary_workspace['id']}/search", params={"filter": ["status:approved"]})
    assert response.status_code == 200, response.text
    assert response.json() == []
