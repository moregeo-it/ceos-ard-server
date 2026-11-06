import pytest
from pydantic import ValidationError

from app.workspace_types.base import Action, EditorRule, FileMenuRule, match_glob


@pytest.mark.parametrize(
    ("path", "glob", "matches"),
    [
        ("/pfs", "pfs", True),
        ("/pfs/NRB", "pfs/*", True),
        ("/pfs/NRB/document.yaml", "pfs/*", False),
        ("/pfs/NRB/document.yaml", "pfs/**", True),
        ("/glossary/accuracy.yaml", "glossary/*.yaml", True),
        ("/foo.yaml", "**/foo.yaml", True),
        ("/a/b/foo.yaml", "**/foo.yaml", True),
        ("/notfoo.yaml", "**/foo.yaml", False),
        ("/a/notfoo.yaml", "**/foo.yaml", False),
        ("/a/b/c", "**", True),
    ],
)
def test_match_glob(path, glob, matches):
    assert match_glob(path, glob) is matches


def test_list_types(client):
    response = client.get("/types")
    assert response.status_code == 200, response.text
    types = response.json()
    assert [item["id"] for item in types] == ["ceos-ard", "eo-glossary"]
    ceos_ard = types[0]
    assert ceos_ard["title"] == "CEOS-ARD"
    assert ceos_ard["available"] is True
    assert ceos_ard["pfs"] is True
    assert ceos_ard["upstream"] == {"owner": "ceos-org", "repo": "ceos-ard", "branch": "main", "url": "https://github.com/ceos-org/ceos-ard"}
    assert [action["id"] for action in ceos_ard["actions"]] == ["create-pfs"]
    assert [editor["match"] for editor in ceos_ard["editors"]] == ["glossary/*.yaml"]
    assert "schema" in ceos_ard["editors"][0]


@pytest.mark.parametrize(
    "rule",
    [
        {"match": "pfs", "kind": "fodler", "ops": []},
        {"match": "pfs", "ops": ["remove"]},
        {"match": "/pfs", "ops": []},
        {"match": "pfs", "op": ["delete"]},
    ],
)
def test_file_menu_rules_are_checked(rule):
    with pytest.raises(ValidationError):
        FileMenuRule.model_validate(rule)


def test_actions_only_use_dialogs_of_the_editor():
    with pytest.raises(ValidationError):
        Action.model_validate({"id": "a", "title": "A", "folder": "/pfs", "dialog": "CreateFileDialog"})


def test_editor_rules_are_checked():
    with pytest.raises(ValidationError):
        EditorRule.model_validate({"match": "glossary/*.yaml", "shema": {}})


def test_workspaces_store_their_type_and_upstream(workspace):
    assert workspace["type"] == "ceos-ard"
    assert (workspace["upstream_owner"], workspace["upstream_repo"], workspace["upstream_branch"]) == ("ceos-org", "ceos-ard", "main")


def test_unknown_types_are_refused(client):
    response = client.post("/workspaces", json={"title": "Unknown", "type": "unknown", "pfs": ["NRB"]})
    assert response.status_code == 400, response.text
