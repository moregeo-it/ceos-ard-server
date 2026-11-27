import yaml
from conftest import file_url
from fastapi import HTTPException

from app.services import file_service, workspace_service
from app.workspace_types import config, create_type


def create_pfs(client, workspace, **data):
    return client.post(f"/workspaces/{workspace['id']}/actions/create-pfs", json={"type": "Optical", **data})


def test_list_pfs_of_the_workspace(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/selection-options")
    assert response.status_code == 200, response.text
    pfs = response.json()["options"]
    assert pfs == [
        {"id": "NRB", "title": "Normalised Radar Backscatter", "path": "/pfs/NRB"},
        {"id": "SR", "title": "Surface Reflectance", "path": "/pfs/SR"},
    ]


def test_the_form_offers_the_pfs_of_the_workspace_as_base(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/actions/create-pfs")
    assert response.status_code == 200, response.text
    form = response.json()
    assert form["schema"]["required"] == ["id", "title", "type"]
    assert form["schema"]["properties"]["type"]["enum"] == ["SAR", "Optical"]
    assert form["schema"]["properties"]["base"]["oneOf"] == [
        {"const": "NRB", "title": "NRB: Normalised Radar Backscatter"},
        {"const": "SR", "title": "SR: Surface Reflectance"},
    ]
    assert [element["scope"] for element in form["ui_schema"]["elements"]][-1] == "#/properties/base"

    assert client.get(f"/workspaces/{workspace['id']}/actions/unknown").status_code == 404


def test_create_pfs(client, workspace):
    response = create_pfs(client, workspace, id="ST", title="Surface Temperature")
    assert response.status_code == 201, response.text
    assert response.json() == {"name": "ST", "is_directory": True, "status": None, "path": "/pfs/ST", "open": "/pfs/ST/document.yaml"}

    files = client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs/ST"}).json()
    assert [(item["name"], item["status"]) for item in files] == [("document.yaml", "added")]

    document = yaml.safe_load(client.get(file_url(workspace, "files", "/pfs/ST/document.yaml")).text)
    assert document["title"] == "Surface Temperature"
    assert document["type"] == "Optical"
    assert document["version"] == "1.0.0"
    assert document["authors"] == ["Jane Doe"]

    ids = [item["id"] for item in client.get(f"/workspaces/{workspace['id']}/selection-options").json()["options"]]
    assert "ST" in ids


def test_create_pfs_from_a_base(client, workspace):
    response = create_pfs(client, workspace, id="NRB2", title="NRB, again", base="NRB", type="SAR")
    assert response.status_code == 201, response.text
    document = yaml.safe_load(client.get(file_url(workspace, "files", "/pfs/NRB2/document.yaml")).text)
    assert document["title"] == "NRB, again"
    assert document["type"] == "SAR"
    assert document["version"] == "5.6.0"
    assert document["background"] == "Background."
    # The base has none, which the CLI refuses
    assert document["authors"] == ["Jane Doe"]


def test_create_pfs_refuses_invalid_input(client, workspace):
    response = create_pfs(client, workspace, id="NRB", title="Again")
    assert response.status_code == 409, response.text
    for data, detail in [
        ({"id": "bad-id", "title": "Bad"}, "Invalid id:"),
        ({"id": "ST", "title": "Surface Temperature", "type": "Thermal"}, "The type must be one of SAR, Optical"),
        ({"id": "ST", "title": "Surface Temperature", "base": "XYZ"}, "Base PFS not found"),
    ]:
        response = create_pfs(client, workspace, **data)
        assert response.status_code == 400, response.text
        assert response.json()["detail"].startswith(detail)
    # Nothing left of the failed attempts
    assert [item["name"] for item in client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs"}).json()] == ["NRB", "SR"]


def test_renaming_a_pfs_requires_a_valid_id(client, workspace):
    response = create_pfs(client, workspace, id="ST", title="Surface Temperature")
    assert response.status_code == 201, response.text

    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "bad-id"})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "A PFS id consists of 2 to 10 capital letters or digits, e.g. NRB"
    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "LST"})
    assert response.status_code == 200, response.text


def test_new_pfs_can_be_renamed_and_deleted(client, workspace):
    response = create_pfs(client, workspace, id="ST", title="Surface Temperature")
    assert response.status_code == 201, response.text

    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "LST"})
    assert response.status_code == 200, response.text
    response = client.delete(file_url(workspace, "files", "/pfs/LST"))
    assert response.status_code in (200, 204), response.text


def test_uploads_cant_create_invalid_pfs_folders(client, workspace):
    response = client.put(file_url(workspace, "files", "/pfs/bad-id/nested/file.yaml"), content="a: 1\n")
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "A PFS id consists of 2 to 10 capital letters or digits, e.g. NRB"
    folders = [item["name"] for item in client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs"}).json()]
    assert "bad-id" not in folders


def test_pfs_settings():
    workspace_type = create_type(
        config.TypeConfig.model_validate({"id": "ceos-ard", "module": "ceos_ard", "settings": {"pfs_types": ["SAR"], "default_version": "2.0.0"}})
    )
    assert workspace_type.actions[0].json_schema["properties"]["type"]["enum"] == ["SAR"]
    assert workspace_type.pfs_settings.default_version == "2.0.0"


def test_usage_of_pfs_does_not_wait_for_builds(client, workspace, monkeypatch):
    def locked(user_id):
        raise AssertionError("CEOS-ARD reads its usage without the build lock")

    monkeypatch.setattr(file_service, "user_build_locks", locked)
    response = client.get(file_url(workspace, "usage", "/requirements/general/metadata-machine-readable.yaml"))
    assert response.status_code == 200, response.text
    assert [item["label"] for item in response.json()["usage"]] == ["NRB", "SR"]


def test_actions_leave_nothing_behind_without_a_repository(client, workspace, monkeypatch):
    def broken(path):
        raise HTTPException(status_code=400, detail="Broken repository")

    monkeypatch.setattr(workspace_service, "get_repo", broken)
    response = create_pfs(client, workspace, id="ST", title="Surface Temperature")
    assert response.status_code == 400, response.text
    monkeypatch.undo()
    assert [item["name"] for item in client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs"}).json()] == ["NRB", "SR"]
