import yaml
from conftest import file_url


def test_list_pfs_of_the_workspace(client, workspace):
    response = client.get(f"/workspaces/{workspace['id']}/pfs")
    assert response.status_code == 200, response.text
    pfs = sorted(response.json()["pfs_types"], key=lambda item: item["id"])
    assert pfs == [{"id": "NRB", "title": "Normalised Radar Backscatter"}, {"id": "SR", "title": "Surface Reflectance"}]


def test_create_pfs(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/pfs", json={"id": "ST", "title": "Surface Temperature", "type": "Optical"})
    assert response.status_code == 201, response.text

    files = client.get(f"/workspaces/{workspace['id']}/files", params={"path": "/pfs/ST"}).json()
    assert [(item["name"], item["status"]) for item in files] == [("document.yaml", "added")]

    document = yaml.safe_load(client.get(file_url(workspace, "files", "/pfs/ST/document.yaml")).text)
    assert document["title"] == "Surface Temperature"
    assert document["type"] == "Optical"
    assert document["version"] == "1.0.0"
    assert document["authors"] == ["Jane Doe"]

    ids = [item["id"] for item in client.get(f"/workspaces/{workspace['id']}/pfs").json()["pfs_types"]]
    assert "ST" in ids


def test_create_pfs_refuses_existing_ids(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/pfs", json={"id": "NRB", "title": "Again"})
    assert response.status_code == 400, response.text


def test_renaming_a_pfs_requires_a_valid_id(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/pfs", json={"id": "ST", "title": "Surface Temperature"})
    assert response.status_code == 201, response.text

    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "bad-id"})
    assert response.status_code == 400, response.text
    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "LST"})
    assert response.status_code == 200, response.text


def test_new_pfs_can_be_renamed_and_deleted(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/pfs", json={"id": "ST", "title": "Surface Temperature"})
    assert response.status_code == 201, response.text

    response = client.patch(file_url(workspace, "files", "/pfs/ST"), json={"operation": "rename", "target": "LST"})
    assert response.status_code == 200, response.text
    response = client.delete(file_url(workspace, "files", "/pfs/LST"))
    assert response.status_code in (200, 204), response.text
