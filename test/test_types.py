import pytest
from pydantic import ValidationError

from app.workspace_types import get_type
from app.workspace_types.base import Action, EditorRule, FileMenuRule, NameRule, match_glob


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
    assert [item["id"] for item in types] == ["ceos-ard", "ogc"]
    ceos_ard = types[0]
    assert ceos_ard["title"] == "CEOS-ARD"
    assert ceos_ard["available"] is True
    assert ceos_ard["preview"] == "build"
    assert ceos_ard["selection"] == {"label": "PFS", "multiple": True, "on_create": True}
    assert ceos_ard["downloads"] == ["pdf", "docx"]
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


def test_actions_need_a_schema():
    with pytest.raises(ValidationError):
        Action.model_validate({"id": "a", "title": "A", "folder": "/pfs", "dialog": "CreateFileDialog"})


def test_name_rules():
    workspace_type = get_type("ceos-ard")
    assert workspace_type.name_error("pfs/bad-id", "folder") == "A PFS id consists of 2 to 10 capital letters or digits, e.g. NRB"
    assert workspace_type.name_error("pfs/NRB", "folder") is None
    assert workspace_type.name_error("pfs/notes.md", "file") is None
    assert workspace_type.name_error("requirements/bad-id", "folder") is None
    message = "References are BibTeX files with the extension .bib in lowercase, e.g. Bell2023.bib"
    assert workspace_type.name_error("references/Bell2023.BIB", "file") == message
    assert workspace_type.name_error("references/Bell2023.bib", "file") is None
    assert workspace_type.name_error("glossary/Bell2023.BIB", "file") is None
    with pytest.raises(ValidationError):
        NameRule.model_validate({"match": "a/*", "pattern": "(", "message": "x"})


def test_editor_rules_are_checked():
    with pytest.raises(ValidationError):
        EditorRule.model_validate({"match": "glossary/*.yaml", "shema": {}})


def test_workspaces_store_their_type_and_upstream(workspace):
    assert workspace["type"] == "ceos-ard"
    assert (workspace["upstream_owner"], workspace["upstream_repo"], workspace["upstream_branch"]) == ("ceos-org", "ceos-ard", "main")


def test_unknown_types_are_refused(client):
    response = client.post("/workspaces", json={"title": "Unknown", "type": "unknown", "selection": ["NRB"]})
    assert response.status_code == 400, response.text
