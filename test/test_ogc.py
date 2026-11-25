import asyncio
import json
import subprocess
from pathlib import Path

import pygit2
import pytest

from app import dependencies
from app.db.database import SessionLocal
from app.models.workspace import GitWorkspace
from app.services import build_service
from app.utils.toolchains import ToolchainError
from app.workspace_types import get_type, ogc, ogc_sources
from app.workspace_types.base import SelectionError

CORE = "core/standard/20-004.adoc"
FACETS = "extensions/facets/document.adoc"


@pytest.fixture(autouse=True)
def latest_metanorma(monkeypatch):
    """No requests to RubyGems."""

    async def refresh():
        pass

    monkeypatch.setattr(ogc, "refresh_latest", refresh)
    monkeypatch.setattr(ogc._Latest, "version", "1.18.7")


@pytest.fixture
def builds(monkeypatch):
    """Replaces Metanorma with a build that writes a small document."""
    calls = []

    async def build(workspace_path, workspace_id, workspace_type, selection, include_format=None, build_dir=None):
        calls.append(selection)
        prefix = Path(dependencies.build_service.output_prefix(workspace_type, workspace_path, selection, build_dir))
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".html").write_text("<h1>Records</h1>", encoding="utf-8")
        return {"status": "success", "message": "Build completed successfully.", "output_file": str(prefix)}

    monkeypatch.setattr(dependencies.build_service, "build", build)
    return calls


def test_ogc_type(client):
    item = next(item for item in client.get("/types").json() if item["id"] == "ogc")
    assert item["preview"] == "build"
    assert item["selection"] == {"label": "document", "multiple": False, "on_create": False}
    assert item["downloads"] == []
    assert item["choose_repository"] is True
    assert item["upstream"] == {"owner": "opengeospatial", "url": "https://github.com/opengeospatial"}


def test_list_repositories(client):
    response = client.get("/types/ogc/repositories")
    assert response.status_code == 200, response.text
    assert response.json() == [{"name": "ogcapi-records", "description": "OGC API - Records", "default_branch": "main"}]

    response = client.get("/types/ceos-ard/repositories")
    assert response.status_code == 400


def test_create_ogc_workspace(client, github, ogc_workspace):
    assert ("fork", "opengeospatial", "ogcapi-records") in github.calls
    assert ("branch_exists", "opengeospatial", "ogcapi-records", "main") in github.calls
    assert ogc_workspace["upstream_owner"] == "opengeospatial"
    assert ogc_workspace["upstream_repo"] == "ogcapi-records"
    assert ogc_workspace["upstream_branch"] == "main"
    assert ogc_workspace["selection"] is None


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        ({}, "A repository is required"),
        ({"repository": "unknown"}, "opengeospatial has no public repository unknown"),
        ({"repository": "WFS_FES"}, "opengeospatial/WFS_FES is archived and can't be changed"),
        ({"repository": "ogcapi-records", "branch": "gone"}, "opengeospatial/ogcapi-records has no branch gone"),
    ],
)
def test_create_ogc_workspace_needs_a_repository(client, data, detail):
    response = client.post("/workspaces", json={"title": "Records", "type": "ogc", **data})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == detail


def test_fixed_repositories_cant_be_chosen(client):
    response = client.post("/workspaces", json={"title": "NRB", "selection": ["NRB"], "repository": "ogcapi-records"})
    assert response.status_code == 400, response.text


def test_list_documents(client, ogc_workspace):
    response = client.get(f"/workspaces/{ogc_workspace['id']}/selection-options")
    assert response.status_code == 200, response.text
    assert response.json()["options"] == [
        {"id": CORE, "title": "OGC API - Records - Part 1: Core", "path": f"/{CORE}"},
        {"id": FACETS, "title": "OGC API - Records - Facets", "path": f"/{FACETS}"},
    ]


def test_select_a_document(client, ogc_workspace):
    url = f"/workspaces/{ogc_workspace['id']}"
    response = client.patch(url, json={"selection": [CORE]})
    assert response.status_code == 200, response.text
    assert response.json()["selection"] == [CORE]

    for document in ("README.md", "core/standard/README.adoc", "../ogc-fork.git/HEAD"):
        response = client.patch(url, json={"selection": [document]})
        assert response.status_code == 400, response.text

    response = client.patch(url, json={"selection": [CORE, FACETS]})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Select one document"


def test_preview_builds_the_selected_document(client, ogc_workspace, builds):
    url = f"/workspaces/{ogc_workspace['id']}"
    response = client.post(f"{url}/previews")
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Select a document for the preview"

    client.patch(url, json={"selection": [CORE]})
    response = client.post(f"{url}/previews")
    assert response.status_code == 200, response.text
    assert builds == [[CORE]]
    assert "<h1>Records</h1>" in response.text

    response = client.get(f"{url}/previews/current")
    assert response.status_code == 200, response.text
    assert "<h1>Records</h1>" in response.text

    with SessionLocal() as db:
        path = db.get(GitWorkspace, ogc_workspace["id"]).abs_path
    assert (path / "build" / "core-standard-20-004" / "20-004.html").is_file()
    # Previews are no change of the workspace
    assert not [file for file in pygit2.Repository(str(path)).status() if file.startswith("build")]

    response = client.get(f"{url}/download", params={"format": "pdf", "selection": [CORE]}, headers={"X-Client-Id": "test-client"})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "This workspace type has no downloads"


@pytest.mark.parametrize("document", ["../x.adoc", "/abs.adoc", "-x.adoc", "a/-x.adoc", ".hidden/x.adoc", "a/../x.adoc", "x.md", "a b.adoc"])
def test_invalid_documents(document):
    with pytest.raises(SelectionError):
        get_type("ogc").output_name([document])


def test_build_command(tmp_path):
    for path, content in {CORE: "include::clause.adoc[]\n", "core/standard/clause.adoc": "include::../examples/a.json[]\n"}.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content, encoding="utf-8")
    ogc_type = get_type("ogc")
    output = str(tmp_path / "build" / "core-standard-20-004" / "20-004")
    assert ogc_type.output_name([CORE]) == "core-standard-20-004/20-004"
    assert ogc_type.build_folder([CORE]) == "core/standard"
    command = ogc_type.build_command(tmp_path, [CORE], output, None)
    assert command[:3] == ["metanorma", "compile", "20-004.adoc"]
    # Includes of the folder above need the include jail at the workspace
    assert command[-2:] == ["--base-dir", str(tmp_path)]

    # Absolute paths are relative to the jail, and files of subfolders may include files of the document's folder
    (tmp_path / "core/standard/clause.adoc").write_text("include::/examples/a.json[]\n", encoding="utf-8")
    (tmp_path / "core/standard/sections").mkdir()
    (tmp_path / "core/standard/sections/annex.adoc").write_text("include::../abstract_tests/a.adoc[]\n", encoding="utf-8")
    assert "--base-dir" not in ogc_type.build_command(tmp_path, [CORE], output, None)

    with pytest.raises(ToolchainError):
        ogc_type.build_command(tmp_path, [CORE], output, "pdf")


def test_toolchain_follows_the_latest_version(tmp_path, monkeypatch):
    toolchain = get_type("ogc").toolchain(tmp_path)
    assert toolchain.name == "metanorma-cli-1.18.7"

    monkeypatch.setattr(ogc._Latest, "version", None)
    monkeypatch.setattr(ogc.toolchains, "installed", lambda: ["metanorma-cli-1.9.0", "metanorma-cli-1.10.1", "ceos-ard-cli-0.8.0"])
    assert get_type("ogc").toolchain(tmp_path).name == "metanorma-cli-1.10.1"

    monkeypatch.setattr(ogc.toolchains, "installed", lambda: [])
    with pytest.raises(ToolchainError):
        get_type("ogc").toolchain(tmp_path)


def _sources(tmp_path):
    workspace = tmp_path / "sources" / "workspace"
    for path, content in {
        CORE: "include::clause_1_scope.adoc[]\n",
        "core/standard/clause_1_scope.adoc": "== Scope\n",
        "core/standard/requirements/REQ_a.adoc": "[[req_a]]\n[requirement]\n",
        "core/standard/annex_bibliography.adoc": "[bibliography]\n== Bibliography\n\n* [[[missing,X]]] X\n* [[[SDWBP,SDWBP]]] Best Practices\n",
        "core/standard/annex_history.adoc": "[appendix]\n== History\n",
    }.items():
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / path).write_text(content, encoding="utf-8")
    return workspace


HTML = (
    '<html><body><div id="mn-source-1"><h1>1. Scope</h1></div>'
    '<table id="req_a" class="modspec"><tr><td>Requirement</td></tr></table>'
    '<div><h1 id="_5fe463c6">Bibliography</h1><p id="SDWBP" class="Biblio">Best Practices</p></div></body></html>'
)


def test_annotate(tmp_path):
    workspace = _sources(tmp_path)
    standard = workspace / "core" / "standard"
    source_map = {
        "mn-source-1": str(standard / "clause_1_scope.adoc"),
        "req_a": str(standard / "requirements" / "REQ_a.adoc"),
        # Reference sections lose their id, their entries keep theirs
        "Bibliography": str(standard / "annex_bibliography.adoc"),
        # Not in the HTML
        "mn-source-2": str(standard / "annex_history.adoc"),
        # Not in the workspace
        "outside": str(tmp_path / "other.adoc"),
    }
    assert ogc_sources.annotate(HTML, source_map, workspace) == (
        '<html><body><div id="mn-source-1"><!-- edit: core/standard/clause_1_scope.adoc --><h1>1. Scope</h1></div>'
        '<!-- edit: core/standard/requirements/REQ_a.adoc --><table id="req_a" class="modspec"><tr><td>Requirement</td></tr></table>'
        '<div><h1 id="_5fe463c6">Bibliography</h1><p id="SDWBP" class="Biblio"><!-- edit: core/standard/annex_bibliography.adoc -->'
        "Best Practices</p></div></body></html>"
    )


def test_build_adds_edit_buttons(tmp_path, monkeypatch):
    workspace = _sources(tmp_path)
    builds = []

    async def run_build(command, *, env, cwd, writable, **kwargs):
        # Metanorma with the source map loaded, see ogc_source_map.rb
        sources = Path(env["SOURCE_MAP"]).parent
        assert env["RUBYOPT"] == f"-r{sources / 'ogc_source_map.rb'}"
        assert (sources / "ogc_source_map.rb").is_file()
        assert sources in writable
        builds.append(sources)
        source_map = {"mn-source-1": str(cwd / "clause_1_scope.adoc"), "req_a": str(cwd / "requirements" / "REQ_a.adoc")}
        Path(env["SOURCE_MAP"]).write_text(json.dumps(source_map), encoding="utf-8")
        output = Path(command[command.index("-o") + 1])
        (output / "20-004.html").write_text(HTML, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    async def ensure(toolchain):
        pass

    monkeypatch.setattr(build_service, "run_build", run_build)
    monkeypatch.setattr(build_service.toolchains, "ensure", ensure)
    monkeypatch.setattr(build_service.toolchains, "bin_path", lambda name: [])
    result = asyncio.run(build_service.BuildService().build(workspace, "ws", get_type("ogc"), [CORE]))
    assert result["status"] == "success", result

    html = (workspace / "build" / "core-standard-20-004" / "20-004.html").read_text(encoding="utf-8")
    assert "<!-- edit: core/standard/clause_1_scope.adoc -->" in html
    assert "<!-- edit: core/standard/requirements/REQ_a.adoc --><table" in html
    # The copy of the workspace is gone
    assert not builds[0].exists()


def test_finish_ignores_links(tmp_path):
    sources = tmp_path / "sources"
    _sources(tmp_path)
    target = tmp_path / "server.html"
    target.write_text(HTML, encoding="utf-8")
    (sources / ogc_sources.MAP_NAME).write_text('{"mn-source-1": "x"}', encoding="utf-8")
    output = tmp_path / "build" / "20-004"
    output.parent.mkdir()
    try:
        Path(str(output) + ".html").symlink_to(target)
    except OSError:
        pytest.skip("Symlinks need privileges on Windows")
    ogc_sources.finish(sources, str(output))
    assert target.read_text(encoding="utf-8") == HTML


def test_build_errors():
    output = "asciidoctor: FAILED: document.adoc: Failed to load AsciiDoc document - undefined method 'empty?' for nil (NoMethodError)\n"
    assert get_type("ogc").build_error(output) == (
        "Metanorma couldn't read the document: document.adoc: Failed to load AsciiDoc document - undefined method 'empty?' for nil (NoMethodError)"
    )
    assert get_type("ogc").build_error("") == "Metanorma couldn't generate the document, e.g. because of an error in the document."
    assert get_type("ceos-ard").build_error(output) is None


def test_search_by_block_type(client, ogc_workspace):
    with SessionLocal() as db:
        path = db.get(GitWorkspace, ogc_workspace["id"]).abs_path
    (path / "core/standard/requirements").mkdir()
    (path / "core/standard/requirements/REQ_a.adoc").write_text("[[req_a]]\n[requirement]\n====\n[%metadata]\nidentifier:: /req/a\n====\n")
    (path / "core/standard/requirements/ATS_a.adoc").write_text("[abstract_test]\n====\n[%metadata]\nidentifier:: /conf/a\n====\n")
    search = f"/workspaces/{ogc_workspace['id']}/search"

    response = client.get(search, params={"filter": ["block:requirement"]})
    assert response.status_code == 200, response.text
    assert [item["path"] for item in response.json()] == ["/core/standard/requirements/REQ_a.adoc"]

    response = client.get(search, params={"filter": ["block:requirement", "block:abstract test"]})
    assert [item["path"] for item in response.json()] == ["/core/standard/requirements/ATS_a.adoc", "/core/standard/requirements/REQ_a.adoc"]


def test_modspec_form():
    ogc_type = get_type("ogc")
    assert [(rule.match, rule.codec) for rule in ogc_type.editors] == [("**/*.adoc", "modspec")]
    assert ogc_type.search_filters[0].values == [
        "requirement",
        "recommendation",
        "permission",
        "requirements class",
        "conformance class",
        "abstract test",
        "conformance test",
    ]
    # Every field of the form is shown for some kind of block
    elements = {element["scope"].rsplit("/", 1)[1]: element for element in ogc_type.editors[0].ui_schema["elements"]}
    assert set(elements) == set(ogc_type.editors[0].json_schema["properties"])
