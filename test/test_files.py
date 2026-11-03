import pytest


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
        ("/requirements/general/metadata-machine-readable.yaml", ["NRB", "SR"]),
        ("/requirements/general/unused.yaml", []),
        ("/glossary/accuracy.yaml", None),
        ("/README.md", None),
    ],
)
def test_context_lists_the_pfs_using_a_requirement(client, workspace, path, usage):
    response = client.get(f"/workspaces/{workspace['id']}/context{path}")
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["path"] == path
    assert (sorted(context["usage"]) if context["usage"] is not None else None) == usage


def test_create_rename_and_delete_files(client, workspace):
    base = f"/workspaces/{workspace['id']}/files"
    response = client.post(base, json={"path": "/requirements/general", "name": "new.yaml", "type": "file"})
    assert response.status_code == 201, response.text
    assert response.json()["path"] == "/requirements/general/new.yaml"

    response = client.patch(f"{base}/requirements/general/new.yaml", json={"operation": "rename", "target": "renamed.yaml"})
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "/requirements/general/renamed.yaml"

    response = client.delete(f"{base}/requirements/general/renamed.yaml")
    assert response.status_code in (200, 204), response.text
    assert "renamed.yaml" not in names(client.get(base, params={"path": "/requirements/general"}))


def test_files_in_pfs_folders_can_be_renamed_and_deleted(client, workspace):
    # The editor hides these operations for PFS, the server doesn't refuse them
    base = f"/workspaces/{workspace['id']}/files"
    response = client.patch(f"{base}/pfs/SR", json={"operation": "rename", "target": "SR2"})
    assert response.status_code == 200, response.text
    response = client.delete(f"{base}/pfs/SR2")
    assert response.status_code in (200, 204), response.text
