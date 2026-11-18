from conftest import FORK, commit_upstream, file_url, git

from app.models.workspace import make_branch_name
from app.services.events_service import event_broker


def test_create_workspace_forks_ceos_ard(client, github, workspace):
    assert ("fork", "ceos-org", "ceos-ard") in github.calls
    assert workspace["fork_repo_owner"] == "jdoe"
    assert workspace["fork_repo_name"] == "ceos-ard"
    assert workspace["pfs"] == ["NRB"]
    branch = workspace["branch_name"]
    assert branch == f"ws-nrb-review-{workspace['id'][:8]}"
    assert git("branch", "--list", branch, cwd=FORK).strip(" *") == branch


def test_branch_name_cuts_long_titles_at_a_dash():
    title = "Synthetic Aperture Radar Normalised Radar Backscatter Product"
    assert make_branch_name(title, "1a2b3c4d-0000") == "ws-synthetic-aperture-radar-1a2b3c4d"


def test_branch_name_cuts_one_long_word():
    name = make_branch_name("x" * 60, "1a2b3c4d-0000")
    assert name == f"ws-{'x' * 34}-1a2b3c4d"
    assert len(name) == 46


def test_branch_name_keeps_letters_digits_and_dashes():
    assert make_branch_name("Größe: SR v2.0 (Draft)!", "1a2b3c4d") == "ws-groe-sr-v2-0-draft-1a2b3c4d"
    assert make_branch_name("Ärger über Café", "1a2b3c4d") == "ws-arger-uber-cafe-1a2b3c4d"


def test_branch_name_without_a_slug():
    assert make_branch_name("日本語 ---", "1a2b3c4d") == "ws-1a2b3c4d"


def test_create_workspace_requires_pfs(client):
    response = client.post("/workspaces", json={"title": "No PFS", "pfs": []})
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
    assert git("log", "-1", "--format=%s", workspace["branch_name"], cwd=FORK) == "Change README"


def test_propose_tells_the_owners_other_tabs(client, github, user, workspace, monkeypatch):
    emitted = {}
    monkeypatch.setattr(event_broker, "emit", lambda workspace_id, event_type, **fields: emitted.setdefault(event_type.value, fields))

    response = client.put(f"/workspaces/{workspace['id']}/proposal", json={"title": "Update NRB", "description": "Details"})
    assert response.status_code == 200, response.text
    assert ("create_pull_request", "ceos-org", "ceos-ard", f"jdoe:{workspace['branch_name']}") in github.calls

    event = emitted["proposal.updated"]
    # The proposal is owner-only, so no collaborator receives it
    assert event["target_user_id"] == user.id
    assert event["proposal"] == response.json()
