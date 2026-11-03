import yaml


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

    document = yaml.safe_load(client.get(f"/workspaces/{workspace['id']}/files/pfs/ST/document.yaml").text)
    assert document["title"] == "Surface Temperature"
    assert document["type"] == "Optical"
    assert document["authors"] == ["Jane Doe"]

    ids = [item["id"] for item in client.get(f"/workspaces/{workspace['id']}/pfs").json()["pfs_types"]]
    assert "ST" in ids


def test_create_pfs_refuses_existing_ids(client, workspace):
    response = client.post(f"/workspaces/{workspace['id']}/pfs", json={"id": "NRB", "title": "Again"})
    assert response.status_code == 400, response.text
