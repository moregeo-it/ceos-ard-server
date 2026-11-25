from conftest import FORK, commit_upstream, file_url, git

from app.services.events_service import event_broker
from app.utils.branch_name import make_branch_name


def test_create_workspace_forks_ceos_ard(client, github, workspace):
    assert ("fork", "ceos-org", "ceos-ard") in github.calls
    assert workspace["fork_repo_owner"] == "jdoe"
    assert workspace["fork_repo_name"] == "ceos-ard"
    assert workspace["selection"] == ["NRB"]
    branch = workspace["branch_name"]
    assert branch == f"ws-nrb-review-{workspace['id'][-8:]}"
    assert git("branch", "--list", branch, cwd=FORK).strip(" *") == branch


ID = "0190a1b2-c3d4-7e5f-8a6b-00001a2b3c4d"


def test_branch_name_cuts_long_titles_at_the_limit():
    title = "Synthetic Aperture Radar Normalised Radar Backscatter Product"
    name = make_branch_name(title, None, ID)
    assert name == "ws-synthetic-aperture-radar-normalise-1a2b3c4d"
    assert len(name) == 46


def test_branch_name_drops_a_dash_left_by_the_cut():
    # The 34th character of the slug is a dash
    assert make_branch_name("Synthetic Aperture Radar Normalis X", None, ID) == "ws-synthetic-aperture-radar-normalis-1a2b3c4d"


def test_branch_name_keeps_the_title_before_a_long_word():
    assert make_branch_name("Ab " + "y" * 40, None, ID) == f"ws-ab-{'y' * 31}-1a2b3c4d"


def test_branch_name_keeps_base_letters():
    assert make_branch_name("Größe: SR v2.0 (Draft)!", None, ID) == "ws-grosse-sr-v2-0-draft-1a2b3c4d"
    assert make_branch_name("Ærø Łódź Þór Café", None, ID) == "ws-aero-lodz-thor-cafe-1a2b3c4d"


def test_branch_name_falls_back_to_the_selection():
    assert make_branch_name("合成孔径雷达", ["NRB", "SAR"], ID) == "ws-nrb-sar-1a2b3c4d"
    assert make_branch_name("A1", ["NRB"], ID) == "ws-nrb-1a2b3c4d"
    assert make_branch_name("合成孔径雷达", None, ID) == "ws-1a2b3c4d"
    assert make_branch_name("合成孔径雷达", [], ID) == "ws-1a2b3c4d"


def test_create_workspace_requires_pfs(client):
    response = client.post("/workspaces", json={"title": "No PFS", "selection": []})
    assert response.status_code == 422
    response = client.post("/workspaces", json={"title": "No PFS"})
    assert response.status_code == 400
    assert response.json()["detail"] == "At least one PFS is required"
    response = client.post("/workspaces", json={"title": "Bad PFS", "selection": ["../x"]})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid PFS id"


def test_list_pfs_of_ceos_ard(client, github):
    response = client.get("/types/ceos-ard/selection-options")
    assert response.status_code == 200
    assert response.json() == {"options": [{"id": "NRB", "title": "NRB", "path": None}, {"id": "SR", "title": "SR", "path": None}]}
    assert ("get_folder_names", "ceos-org", "ceos-ard", "pfs", "main") in github.calls


def test_select_pfs(client, workspace):
    url = f"/workspaces/{workspace['id']}"
    response = client.patch(url, json={"selection": ["NRB", "SR"]})
    assert response.status_code == 200, response.text
    assert response.json()["selection"] == ["NRB", "SR"]
    invalid = [(["XYZ"], "The workspace has no PFS XYZ"), (["NRB", "NRB"], "A PFS is selected more than once"), (["-x"], "Invalid PFS id")]
    for selection, detail in invalid:
        response = client.patch(url, json={"selection": selection})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == detail


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
