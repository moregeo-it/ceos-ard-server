import sys

import pygit2
import pytest
from conftest import file_url
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.workspace import GitWorkspace
from app.services.events_service import event_broker
from app.utils import limits
from app.workspace_types.ceos_ard import CeosArdType

MB = 1024 * 1024


def names(response):
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()]


def test_root_hides_build_files_and_templates(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/"})
    assert names(response) == ["glossary", "images", "pfs", "requirements", "sections", "README.md"]


def test_search_skips_hidden_folders(client, workspace):
    # .github/workflows/build.yml and templates/template.html
    response = client.get(f"/workspaces/{workspace['id']}/search", params={"query": "build"})
    assert names(response) == []
    response = client.get(f"/workspaces/{workspace['id']}/search", params={"query": "template"})
    assert names(response) == []


@pytest.mark.parametrize("path", ["/templates", "/.github", "/.git"])
def test_hidden_folders_are_not_listed(client, workspace, path):
    response = client.get(f"/workspaces/{workspace['id']}/files", params={"path": path})
    assert response.status_code in (400, 403, 404), response.text


def test_workspaces_cloned_with_symlinks_check_out_none_after_a_restart(client, workspace):
    with SessionLocal() as db:
        path = str(db.get(GitWorkspace, workspace["id"]).abs_path)
    # As before core.symlinks=false was set for new clones
    pygit2.Repository(path).config["core.symlinks"] = True
    with TestClient(app):
        pass
    assert pygit2.Repository(path).config.get_bool("core.symlinks") is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks need extra rights on Windows")
def test_symlinks_are_not_listed_or_searched(client, workspace, tmp_path):
    secret = tmp_path / "secret.yaml"
    secret.write_text("SECRET_KEY: must-not-leak\n")
    with SessionLocal() as db:
        root = db.get(GitWorkspace, workspace["id"]).abs_path
    (root / "requirements" / "leak.yaml").symlink_to(secret)
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)

    assert "leak.yaml" not in names(client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/requirements"}))
    assert "outside" not in names(client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/"}))
    assert names(client.get(f"/workspaces/{workspace['id']}/search", params={"query": "must-not-leak"})) == []
    response = client.get(file_url(workspace, "files", "/requirements/leak.yaml"))
    assert response.status_code in (400, 403, 404), response.text


def test_search_finds_names_and_contents(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/search", params={"query": "machine readable"})
    assert response.status_code == 200, response.text
    results = response.json()
    assert [result["path"] for result in results] == ["/requirements/general/metadata-machine-readable.yaml"]
    assert results[0]["type"] == "content"
    assert results[0]["line"] == 6

    response = client.get(f"/workspaces/{workspace['id']}/search", params={"query": "accuracy"})
    assert [(result["path"], result["type"]) for result in response.json()] == [("/glossary/accuracy.yaml", "filename")]


def test_search_skips_other_file_types(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/search", params={"query": "<svg>"})
    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.parametrize(
    ("path", "usage"),
    [
        (
            "/requirements/general/metadata-machine-readable.yaml",
            [{"label": "NRB", "path": "/pfs/NRB/document.yaml"}, {"label": "SR", "path": "/pfs/SR/document.yaml"}],
        ),
        ("/requirements/general/unused.yaml", []),
        ("/glossary/accuracy.yaml", None),
        ("/README.md", None),
    ],
)
def test_context_lists_the_pfs_using_a_requirement(client, workspace, path, usage):
    response = client.get(file_url(workspace, "context", path))
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["path"] == path
    assert (sorted(context["usage"], key=lambda item: item["label"]) if context["usage"] is not None else None) == usage


def test_files_open_even_if_their_usage_fails(client, workspace, monkeypatch):
    def fail(self, workspace_path, file_path):
        raise ValueError("A PFS document that can't be parsed")

    monkeypatch.setattr(CeosArdType, "_requirement_usage", fail)
    response = client.get(file_url(workspace, "context", "/requirements/general/unused.yaml"))
    assert response.status_code == 200, response.text
    assert response.json()["usage"] is None


def test_create_rename_and_delete_files(client, workspace):
    base = f"/workspaces/{workspace['id']}/files"
    response = client.post(base, json={"path": "/requirements/general", "name": "new.yaml", "type": "file"})
    assert response.status_code == 201, response.text
    assert response.json()["path"] == "/requirements/general/new.yaml"

    response = client.patch(file_url(workspace, "files", "/requirements/general/new.yaml"), json={"operation": "rename", "target": "renamed.yaml"})
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "/requirements/general/renamed.yaml"

    response = client.delete(file_url(workspace, "files", "/requirements/general/renamed.yaml"))
    assert response.status_code in (200, 204), response.text
    assert "renamed.yaml" not in names(client.get(base, params={"path": "/requirements/general"}))


def test_existing_pfs_can_not_be_renamed_or_deleted(client, workspace):
    base = f"/workspaces/{workspace['id']}/files"
    response = client.patch(file_url(workspace, "files", "/pfs/SR"), json={"operation": "rename", "target": "SR2"})
    assert response.status_code == 403, response.text
    response = client.delete(file_url(workspace, "files", "/pfs/SR"))
    assert response.status_code == 403, response.text
    response = client.delete(file_url(workspace, "files", "/pfs/SR/document.yaml"))
    assert response.status_code == 403, response.text
    response = client.post(base, json={"path": "/pfs", "name": "notes.md", "type": "file"})
    assert response.status_code == 403, response.text


@pytest.fixture
def events(monkeypatch):
    emitted = []
    monkeypatch.setattr(event_broker, "emit", lambda workspace_id, event_type, **fields: emitted.append((event_type.value, fields["path"])))
    return emitted


def test_saving_a_new_file_uploads_it(client, workspace, events):
    response = client.put(file_url(workspace, "files", "/images/logo.png"), content=b"x" * MB)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "added"
    assert events == [("file.created", "/images/logo.png")]


def test_saving_an_existing_file(client, workspace, events):
    response = client.put(file_url(workspace, "files", "/README.md"), content="# Changed\n")
    assert response.status_code == 200, response.text
    assert events == [("file.saved", "/README.md")]


@pytest.mark.parametrize("streamed", [False, True], ids=["content-length", "streamed"])
def test_files_over_the_limit_are_refused(client, workspace, events, streamed):
    content = b"x" * (MB + 1)
    response = client.put(file_url(workspace, "files", "/README.md"), content=iter([content[:MB], content[MB:]]) if streamed else content)
    assert response.status_code == 413, response.text
    assert client.get(file_url(workspace, "files", "/README.md")).text == "# CEOS-ARD\n"
    assert events == []


@pytest.mark.parametrize("path", ["/images/logo.svg", "/images/logo.html", "/images/my logo.png", "/images/ logo.png", "/images/.hidden/logo.png"])
def test_uploads_need_an_allowed_name_and_format(client, workspace, events, path):
    listing = names(client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/images"}))
    response = client.put(file_url(workspace, "files", path), content=b"x")
    assert response.status_code == 400, response.text
    assert names(client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/images"})) == listing
    assert events == []


@pytest.mark.parametrize("path", ["/pfs/notes.md", "/pfs/XYZ/document.yaml"])
def test_saving_does_not_create_files_where_the_menu_does_not_offer_it(client, workspace, path):
    response = client.put(file_url(workspace, "files", path), content="title: New\n")
    assert response.status_code == 403, response.text
    assert names(client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs"})) == ["NRB", "SR"]


def test_saving_creates_files_where_the_menu_offers_it(client, workspace):
    response = client.put(file_url(workspace, "files", "/requirements/general/new.yaml"), content="title: New\n")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "added"


def test_saving_restores_deleted_files(client, workspace):
    # E.g. a file that stayed open in the editor while its folder was deleted
    response = client.delete(file_url(workspace, "files", "/requirements/general"))
    assert response.status_code in (200, 204), response.text

    response = client.put(file_url(workspace, "files", "/requirements/general/unused.yaml"), content="title: Unused\n")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "modified"


def test_files_too_large_to_hold_in_memory_are_not_read(client, workspace, monkeypatch):
    monkeypatch.setattr(limits, "MAX_READ_SIZE", 1000)
    with SessionLocal() as db:
        root = db.get(GitWorkspace, workspace["id"]).abs_path
    large = "description: needle\n" + "#" * 2000 + "\n"
    (root / "README.md").write_text(large)
    (root / "pfs/SR/document.yaml").write_text("title: Surface Reflectance\n" + large)

    response = client.get(file_url(workspace, "files", "/README.md"))
    assert response.status_code == 413, response.text
    response = client.get(file_url(workspace, "diffs", "/README.md"))
    assert response.status_code == 413, response.text
    assert names(client.get(f"/workspaces/{workspace['id']}/search", params={"query": "needle"})) == []
    # The PFS whose document can't be read is left out
    response = client.get(file_url(workspace, "context", "/requirements/general/metadata-machine-readable.yaml"))
    assert response.json()["usage"] == [{"label": "NRB", "path": "/pfs/NRB/document.yaml"}]


def test_diff_of_a_changed_file(client, workspace):
    response = client.put(file_url(workspace, "files", "/README.md"), content="# Changed\n")
    assert response.status_code == 200, response.text
    response = client.get(file_url(workspace, "diffs", "/README.md"))
    assert response.status_code == 200, response.text
    assert "-# CEOS-ARD" in response.text
    assert "+# Changed" in response.text
