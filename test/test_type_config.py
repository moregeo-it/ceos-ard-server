from pathlib import Path

import pytest

from app.workspace_types import config, create_type, load_types


def deployment(*types):
    return config.DeploymentConfig.model_validate({"types": list(types)})


def test_defaults_of_the_module():
    workspace_type = create_type(config.TypeConfig(id="ceos-ard", module="ceos_ard"))
    assert workspace_type.id == "ceos-ard"
    assert workspace_type.title == "CEOS-ARD"
    assert (workspace_type.upstream.owner, workspace_type.upstream.repo, workspace_type.upstream.branch) == ("ceos-org", "ceos-ard", "main")


def test_configuration_replaces_defaults():
    workspace_type = create_type(
        config.TypeConfig(id="ceos-ard-test", module="ceos_ard", title="CEOS-ARD (test)", color="#123456", upstream={"owner": "m-mohr"})
    )
    assert workspace_type.id == "ceos-ard-test"
    assert workspace_type.title == "CEOS-ARD (test)"
    assert workspace_type.color == "#123456"
    # Repository and branch keep the module's default
    assert (workspace_type.upstream.owner, workspace_type.upstream.repo, workspace_type.upstream.branch) == ("m-mohr", "ceos-ard", "main")


def test_offered_types():
    types, offered = load_types(
        deployment({"id": "old", "module": "ceos_ard", "offered": False}, {"id": "new", "module": "ceos_ard", "title": "New"})
    )
    assert set(types) == {"old", "new"}
    assert offered == ["new"]


@pytest.mark.parametrize(
    ("types", "message"),
    [
        ([], "at least 1 item"),
        ([{"id": "a", "module": "ceos_ard"}, {"id": "a", "module": "ceos_ard"}], "configured more than once: a"),
        ([{"id": "a", "module": "ceos_ard", "offered": False}], "At least one workspace type must be offered"),
        ([{"id": "a", "module": "ceos_ard", "colour": "#000000"}], "Extra inputs are not permitted"),
        ([{"id": "A b", "module": "ceos_ard"}], "should match pattern"),
        ([{"id": "a", "module": "../ceos_ard"}], "should match pattern"),
    ],
)
def test_invalid_configuration(types, message):
    with pytest.raises(ValueError, match=message):
        deployment(*types)


@pytest.mark.parametrize(
    ("type_config", "message"),
    [
        ({"id": "a", "module": "unknown"}, "no module unknown"),
        ({"id": "a", "module": "config"}, "has no create()"),
        ({"id": "a", "module": "ceos_ard", "settings": {"x": 1}}, "invalid settings"),
    ],
)
def test_invalid_type(type_config, message):
    with pytest.raises(ValueError, match=message):
        create_type(config.TypeConfig.model_validate(type_config))


def test_load(tmp_path):
    path = tmp_path / "workspace-types.yaml"
    assert config.load(path, required=False) == config.DEFAULT
    with pytest.raises(ValueError, match="doesn't exist"):
        config.load(path, required=True)

    path.write_text("types:\n  - id: ceos-ard\n    module: ceos_ard\n    offered: yes\n", encoding="utf-8")
    assert config.load(path, required=True).types[0].offered is True

    path.write_text("types: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid workspace types configuration"):
        config.load(path, required=True)


def test_example_configuration():
    example = config.load(Path(__file__).parent.parent / "workspace-types.example.yaml", required=True)
    types, offered = load_types(example)
    assert offered == ["ceos-ard"]
    assert types["ceos-ard"].pfs_settings.pfs_types == ["SAR", "Optical"]


def test_build_limits():
    workspace_type = create_type(config.TypeConfig(id="ogc", module="ogc", build_timeout=60, build_memory_max="2G"))
    assert (workspace_type.build_timeout, workspace_type.build_memory_max) == (60, "2G")
    assert (create_type(config.TypeConfig(id="ogc", module="ogc")).build_timeout) == 180
    with pytest.raises(ValueError, match="should match pattern"):
        config.TypeConfig(id="ogc", module="ogc", build_memory_max="lots")
