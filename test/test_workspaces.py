from conftest import FORK, commit_upstream, file_url, git


def test_create_workspace_forks_ceos_ard(client, github, workspace):
    assert ("fork", "ceos-org", "ceos-ard") in github.calls
    assert workspace["fork_repo_owner"] == "jdoe"
    assert workspace["fork_repo_name"] == "ceos-ard"
    assert workspace["pfs"] == ["NRB"]
    branch = f"workspace/{workspace['id']}"
    assert git("branch", "--list", branch, cwd=FORK).strip(" *") == branch


def test_create_workspace_requires_pfs(client):
    response = client.post("/workspaces/", json={"title": "No PFS", "pfs": []})
    assert response.status_code == 422


def test_list_pfs_of_ceos_ard(client, github):
    response = client.get("/pfs")
    assert response.status_code == 200
    assert response.json() == {"pfs_types": [{"id": "NRB", "title": None}, {"id": "SR", "title": None}]}
    assert ("get_pfs_types", "ceos-org", "ceos-ard", "main") in github.calls


def test_sync_reports_upstream_behind(client, workspace):
    commit_upstream({"README.md": "# CEOS-ARD\n\nUpdated.\n"}, "Update README")

    response = client.post(f"/workspaces/{workspace['id']}/sync")
    assert response.status_code == 200, response.text
    assert response.json()["upstream"] == {"behind_commits": 1, "conflicting_files": []}


def test_sync_reports_upstream_conflicts(client, workspace):
    path = "sections/introduction/what-are-ceos-ard-products.md"
    response = client.put(file_url(workspace, "files", f"/{path}"), content="## Mine\n")
    assert response.status_code == 200, response.text
    response = client.put(f"/workspaces/{workspace['id']}/diffs", json={"message": "Change introduction"})
    assert response.status_code == 200, response.text
    commit_upstream({path: "## Theirs\n"}, "Change introduction upstream")

    response = client.post(f"/workspaces/{workspace['id']}/sync")
    assert response.status_code == 200, response.text
    assert response.json()["upstream"] == {"behind_commits": 1, "conflicting_files": [path]}


def test_commit_pushes_to_the_fork(client, workspace):
    response = client.put(file_url(workspace, "files", "/README.md"), content="# Changed\n")
    assert response.status_code == 200, response.text

    response = client.put(f"/workspaces/{workspace['id']}/diffs", json={"message": "Change README"})
    assert response.status_code == 200, response.text
    assert git("log", "-1", "--format=%s", f"workspace/{workspace['id']}", cwd=FORK) == "Change README"
