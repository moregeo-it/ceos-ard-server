"""Edit buttons in Metanorma's HTML: where the content of each file the document includes starts."""

import json
import re
import shutil
from pathlib import Path

from app.utils import limits

SCRIPT = Path(__file__).with_name("ogc_source_map.rb")
MAP_NAME = "source-map.json"
MAX_MAP_SIZE = 4 * limits.MB
ELEMENT_ID = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*?\sid=\"([^\"]+)\"[^>]*>")
# Metanorma replaces the ids of reference sections, but keeps those of their entries, e.g. * [[[apache_parquet,Apache Parquet]]]
REFERENCE = re.compile(r"\[\[\[([^\],\s]+)")
# Buttons go into the element that starts a file's content, before those that can't hold them
BEFORE = {"table", "thead", "tbody", "tr", "ul", "ol", "dl"}
# What the preview's <!-- edit: --> comments accept
EDIT_PATH = re.compile(r"[\w\-.~/]+")


def prepare(sources: Path) -> dict[str, str]:
    """Load the source map into Metanorma, see ogc_source_map.rb."""
    script = sources / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    return {"RUBYOPT": f"-r{script}", "SOURCE_MAP": str(sources / MAP_NAME)}


def _references(path: Path) -> list[str]:
    try:
        limits.check_read_size(path.stat().st_size, path.name)
        return REFERENCE.findall(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, limits.FileTooLarge):
        return []


def annotate(html: str, source_map: dict[str, str], workspace: Path) -> str:
    """Add an edit comment where the content of each file of `source_map` (id: absolute path) starts."""
    elements = {}
    for match in ELEMENT_ID.finditer(html):
        elements.setdefault(match.group(2), match)
    workspace = workspace.resolve()
    insertions = {}
    for anchor, file in source_map.items():
        try:
            path = Path(file).resolve()
            relative = path.relative_to(workspace).as_posix()
        except (ValueError, OSError):
            continue
        if not EDIT_PATH.fullmatch(relative):
            continue
        match = elements.get(anchor) or next((elements[ref] for ref in _references(path) if ref in elements), None)
        if not match:
            continue
        position = match.start() if match.group(1).lower() in BEFORE else match.end()
        insertions.setdefault(position, f"<!-- edit: {relative} -->")
    for position in sorted(insertions, reverse=True):
        html = html[:position] + insertions[position] + html[position:]
    return html


def finish(sources: Path, output_prefix: str) -> None:
    """Add the edit comments to the HTML of a successful build."""
    map_path = sources / MAP_NAME
    html_path = Path(output_prefix + ".html")
    # Both written by the build, which may have left links instead
    if map_path.is_symlink() or not map_path.is_file() or html_path.is_symlink() or not html_path.is_file():
        return
    limits.check_read_size(map_path.stat().st_size, map_path.name, MAX_MAP_SIZE)
    limits.check_read_size(html_path.stat().st_size, html_path.name, limits.MAX_PREVIEW_SIZE)
    source_map = json.loads(map_path.read_text(encoding="utf-8"))
    if not isinstance(source_map, dict):
        return
    source_map = {anchor: file for anchor, file in source_map.items() if isinstance(file, str)}
    html = annotate(html_path.read_text(encoding="utf-8"), source_map, sources / "workspace")
    html_path.unlink()
    with html_path.open("x", encoding="utf-8") as handle:
        handle.write(html)
