"""
Renders and links the terms of the CEOS EO Glossary.

Workspace content is untrusted, so the server runs this file as a script in the build sandbox
(`render` and `usage`), and imports it only for what reads few files and parses no Markdown.
It needs nothing but the standard library, PyYAML and markdown-it.
"""

import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

TERMS_DIR = "docs/terms"
TERM_CLASSES = ["base", "core", "controversial", "high-impact"]
TERM_STATUSES = ["to be defined", "to be discussed", "to be approved", "approved"]
TERM_GROUPS = ["WGISS", "WGCV", "WGCapD", "LSI-VC", "WGClimate", "B-VC", "AC-VC"]
# The site's export scripts only read this many definitions per term
MAX_DEFINITIONS = 5
# As MAX_TEXT_SIZE in app/utils/limits.py, which this script can't import
MAX_FILE_SIZE = 1024 * 1024

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
DEFINITION_HEADING = re.compile(r"^\d+\s+definition$", re.IGNORECASE)
NOTES_HEADING = re.compile(r"^notes?$", re.IGNORECASE)


class _NoAliasLoader(yaml.SafeLoader):
    """Aliases can expand a few lines into gigabytes once turned into text."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("Aliases aren't allowed in the front matter")
        return super().compose_node(parent, index)


@dataclass
class TermInfo:
    slug: str
    title: str
    path: str


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    data = yaml.load(match.group(1), Loader=_NoAliasLoader) or {}
    if not isinstance(data, dict):
        raise ValueError("The front matter is not a key-value map")
    return data, text[match.end() :]


def slugify(title: str) -> str:
    return re.sub(r"\s+", "_", title.strip().lower())


def read_file(root: Path, path: Path) -> str:
    """
    Only regular files of the workspace, as a symlink could point anywhere, e.g. to the server's settings,
    and only up to MAX_FILE_SIZE, as the server also reads them into its own memory.
    """
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise OSError(f"{path} is not a file of the workspace")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise OSError(f"{path} is larger than {MAX_FILE_SIZE / (1024 * 1024):g} MB")
    return path.read_text(encoding="utf-8", errors="replace")


def build_term_map(root: Path) -> dict[str, TermInfo]:
    """Port of buildTermMap in the glossary's plugins/remark-glossary-links.mjs."""
    term_map: dict[str, TermInfo] = {}
    terms_dir = root / TERMS_DIR
    if not terms_dir.is_dir():
        return term_map

    for file in sorted(terms_dir.iterdir()):
        if file.suffix != ".md":
            continue
        try:
            content = read_file(root, file)
        except OSError:
            continue
        slug = file.stem
        path = f"/{TERMS_DIR}/{file.name}"

        front_matter = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
        if front_matter:
            title_match = re.search(r"^title:\s*(.+)$", front_matter.group(1), re.MULTILINE)
            if title_match:
                title = re.sub(r"['\"]", "", title_match.group(1).strip())
                if len(title) > 2:
                    term_map[title.lower()] = TermInfo(slug, title, path)

        for match in re.finditer(r"^# (.+)$", content, re.MULTILINE):
            term = re.sub(r"[*_`\[\]]", "", match.group(1).strip())
            if len(term) > 2 and term.lower() not in term_map:
                term_map[term.lower()] = TermInfo(slug, term, path)

    return term_map


class TermLinker:
    """Longest match first, case-insensitive, optional plural, never a self link."""

    def __init__(self, term_map: dict[str, TermInfo], self_slugs: set[str]):
        self.term_map = term_map
        keys = sorted((key for key, info in term_map.items() if info.slug not in self_slugs), key=len, reverse=True)
        self.pattern = None
        if keys:
            alternatives = "|".join(re.escape(key) for key in keys)
            # ASCII \w like the JavaScript original
            self.pattern = re.compile(rf"(?<![\w/])(?:{alternatives})(?:es|s)?(?![\w/])", re.IGNORECASE | re.ASCII)

    def lookup(self, matched: str) -> TermInfo | None:
        lower = matched.lower()
        for candidate in (lower, lower[:-2] if lower.endswith("es") else None, lower[:-1] if lower.endswith("s") else None):
            if candidate and candidate in self.term_map:
                return self.term_map[candidate]
        return None

    def find(self, text: str) -> list[tuple[int, int, TermInfo]]:
        if not self.pattern:
            return []
        matches = []
        for match in self.pattern.finditer(text):
            info = self.lookup(match.group(0))
            if info:
                matches.append((match.start(), match.end(), info))
        return matches


def self_slugs_for(file: Path, body: str, term_map: dict[str, TermInfo]) -> set[str]:
    slugs = {file.stem}
    for match in re.finditer(r"^# (.+)$", body, re.MULTILINE):
        info = term_map.get(match.group(1).strip().lower())
        if info:
            slugs.add(info.slug)
    return slugs


def linkable_text(body: str) -> str:
    """The text of the sections the site links terms in: "## N Definition" and "### Notes"."""
    lines = []
    linkable = False
    for line in body.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading:
            depth, text = len(heading.group(1)), heading.group(2).strip()
            linkable = (depth == 2 and bool(DEFINITION_HEADING.match(text))) or (depth == 3 and bool(NOTES_HEADING.match(text)))
            continue
        if linkable:
            lines.append(line)
    return "\n".join(lines)


def parse_term(text: str) -> dict[str, Any]:
    """The term as the editor's form shows it; unknown parts of the file are ignored."""
    front_matter, body = split_front_matter(text)
    tags = [str(tag) for tag in front_matter.get("tags") or []]
    synonyms_match = re.search(r"^\*\*Synonyms\*\*:\s*(.*)$", body, re.MULTILINE)
    definitions = []
    for block in re.split(r"^## \d+ Definition\s*$", body, flags=re.MULTILINE)[1:]:
        block = re.split(r"^_{3,}\s*$", block, flags=re.MULTILINE)[0]
        sections = re.split(r"^### (Notes|Examples|Sources)\s*$", block, flags=re.MULTILINE)
        definition = {"definition": sections[0].strip()}
        for index in range(1, len(sections) - 1, 2):
            definition[sections[index].lower()] = sections[index + 1].strip()
        definitions.append(definition)
    return {
        "title": str(front_matter.get("title") or ""),
        "description": str(front_matter.get("description") or ""),
        "classes": [tag for tag in tags if tag in TERM_CLASSES],
        "statuses": [tag for tag in tags if tag in TERM_STATUSES],
        "groups": [tag for tag in tags if tag in TERM_GROUPS],
        "other_tags": [tag for tag in tags if tag not in TERM_CLASSES + TERM_STATUSES + TERM_GROUPS],
        "synonyms": [s.strip() for s in synonyms_match.group(1).split(",") if s.strip()] if synonyms_match else [],
        "definitions": definitions,
        "has_h1": bool(re.search(r"^# .+$", body, re.MULTILINE)),
    }


def check_term(file: Path, text: str) -> list[dict[str, str]]:
    """Structural checks proposed in ceos-org/eo-glossary#14."""
    issues: list[dict[str, str]] = []

    def issue(level: str, message: str):
        issues.append({"level": level, "message": message})

    try:
        term = parse_term(text)
    except Exception as e:
        return [{"level": "error", "message": f"The front matter is not valid YAML: {e}"}]

    title = term["title"]
    if not title:
        issue("error", "The term has no title.")
    elif slugify(title) != file.stem:
        issue("info", f"The file name doesn't match the title, expected {slugify(title)}.md.")
    if not term["description"]:
        issue("warning", "The summary (description) is missing.")
    if not term["classes"]:
        issue("error", f"A class is required ({', '.join(TERM_CLASSES)}).")
    elif len(term["classes"]) > 1:
        issue("warning", f"The term has more than one class: {', '.join(term['classes'])}.")
    if len(term["statuses"]) != 1:
        issue("error", f"Exactly one status is required ({', '.join(TERM_STATUSES)}).")
    if not term["groups"]:
        issue("info", "No responsible CEOS group is assigned.")

    definitions = term["definitions"]
    if not definitions:
        issue("error", 'The heading "## 1 Definition" is missing.')
    elif len(definitions) > MAX_DEFINITIONS:
        issue("warning", f"Only the first {MAX_DEFINITIONS} definitions are included in the glossary exports.")

    status = term["statuses"][0] if len(term["statuses"]) == 1 else None
    for number, definition in enumerate(definitions, start=1):
        text_ = definition.get("definition", "")
        if not text_:
            if status != "to be defined":
                issue("warning", f'Definition {number} is empty, so the status should be "to be defined".')
            continue
        if title and re.match(rf"^{re.escape(title)}\s+(is|are)\b", text_, re.IGNORECASE):
            issue("warning", f'Definition {number} starts with "{title} is", but it must be substitutable for the term.')
        if not definition.get("sources", "").strip():
            issue("warning", f"Definition {number} has no source.")

    return issues


def _linkify_inline(token: Token, linker: TermLinker) -> list[TermInfo]:
    linked: list[TermInfo] = []
    children = []
    depth = 0
    for child in token.children or []:
        if child.type != "text" or depth > 0:
            depth += child.nesting
            children.append(child)
            continue
        cursor = 0
        text = child.content
        last_end = -1
        for start, end, info in linker.find(text):
            if start < last_end:
                continue
            if start > cursor:
                children.append(Token("text", "", 0, content=text[cursor:start]))
            children.append(Token("link_open", "a", 1, attrs={"href": info.path, "title": info.title, "class": "term", "data-open-file": info.path}))
            children.append(Token("text", "", 0, content=text[start:end]))
            children.append(Token("link_close", "a", -1))
            linked.append(info)
            cursor = last_end = end
        if cursor == 0:
            children.append(child)
        elif cursor < len(text):
            children.append(Token("text", "", 0, content=text[cursor:]))
    token.children = children
    return linked


def render_markdown(body: str, linker: TermLinker | None) -> tuple[str, list[TermInfo]]:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = md.parse(body)
    linked: list[TermInfo] = []
    if linker:
        linkable = False
        in_table = False
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                heading = tokens[index + 1].content.strip()
                depth = int(token.tag[1])
                linkable = (depth == 2 and bool(DEFINITION_HEADING.match(heading))) or (depth == 3 and bool(NOTES_HEADING.match(heading)))
            elif token.type in ("table_open", "table_close"):
                in_table = token.type == "table_open"
            elif token.type == "inline" and linkable and not in_table and tokens[index - 1].type != "heading_open":
                linked.extend(_linkify_inline(token, linker))
    return md.renderer.render(tokens, md.options, {}), linked


PREVIEW_STYLE = """
body { font-family: system-ui, sans-serif; line-height: 1.5; margin: 1.5rem; color: #1f2933; }
h1 { margin: 0 0 .25rem; font-size: 1.8rem; }
h2 { border-bottom: 1px solid #dde3ea; padding-bottom: .2rem; margin-top: 1.6rem; }
h3 { margin-bottom: .2rem; font-size: 1.05rem; color: #3e5265; }
hr { border: 0; border-top: 1px dashed #c3ccd6; margin: 1.8rem 0; }
a.term { color: #0b6bcb; text-decoration: none; border-bottom: 1px dotted #0b6bcb; cursor: pointer; }
.summary { color: #52606d; margin: .25rem 0 .75rem; }
.tags { display: flex; flex-wrap: wrap; gap: .35rem; margin: .5rem 0 1rem; }
.tag { border-radius: 1rem; padding: .1rem .6rem; font-size: .8rem; background: #e4e9ef; }
.tag.status { background: #fff3c4; } .tag.status.approved { background: #c6f7d0; } .tag.status.to-be-defined { background: #ffd6d6; }
.tag.class { background: #dbe7ff; } .tag.group { background: #ece2ff; }
.checks { border-radius: .4rem; padding: .5rem 1rem; margin: 0 0 1rem; background: #fff8e6; border: 1px solid #f0d58c; }
.checks.ok { background: #effaf2; border-color: #a6dfb4; }
.checks li.error { color: #b42318; } .checks li.info { color: #52606d; }
.relations { margin-top: 2rem; font-size: .92rem; color: #52606d; }
.relations ul { columns: 2; }
@media (prefers-color-scheme: dark) {
    body { background: #1e1e1e; color: #e4e7eb; } h3 { color: #9fb3c8; } .summary, .relations { color: #9aa5b1; }
    .tag { background: #3a4048; color: #e4e7eb; } a.term { color: #6cb2ff; border-color: #6cb2ff; }
    .checks { background: #3b3220; border-color: #7a6326; } .checks.ok { background: #1f3326; border-color: #2f6b40; }
}
"""


def _page(title: str, content: str) -> str:
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{PREVIEW_STYLE}</style></head><body>{content}</body></html>"
    )


def _term_list(terms: list[TermInfo]) -> str:
    items = "".join(f'<li><a class="term" href="{t.path}" data-open-file="{t.path}">{html.escape(t.title)}</a></li>' for t in terms)
    return f"<ul>{items}</ul>"


def is_term(file_path: str) -> bool:
    return re.match(rf"^/?{TERMS_DIR}/[^/]+\.md$", file_path) is not None


def tags(root: Path, file_path: str) -> set[str]:
    try:
        front_matter, _ = split_front_matter(read_file(root, root / file_path.lstrip("/")))
        return {str(tag) for tag in front_matter.get("tags") or []}
    except Exception:
        return set()


def backlinks(root: Path, file_path: str, term_map: dict[str, TermInfo] | None = None) -> list[TermInfo]:
    terms_dir = root / TERMS_DIR
    target = Path(file_path).stem
    term_map = term_map if term_map is not None else build_term_map(root)
    keys = [key for key, info in term_map.items() if info.slug == target]
    if not keys:
        return []
    # The first entry per slug is the title, the later ones are alternative H1s
    by_slug: dict[str, TermInfo] = {}
    for info in term_map.values():
        by_slug.setdefault(info.slug, info)
    quick = re.compile(
        r"(?<![\w/])(?:" + "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True)) + r")(?:es|s)?(?![\w/])",
        re.IGNORECASE | re.ASCII,
    )
    result = []
    for file in sorted(terms_dir.glob("*.md")):
        if file.stem == target:
            continue
        try:
            _, body = split_front_matter(read_file(root, file))
        except Exception:
            continue
        text = linkable_text(body)
        if not quick.search(text):
            continue
        # Only counts if no longer term takes the phrase, like on the site
        linker = TermLinker(term_map, self_slugs_for(file, body, term_map))
        matches = linker.find(text)
        cursor = -1
        for start, end, info in sorted(matches, key=lambda m: (m[0], -m[1])):
            if start < cursor:
                continue
            cursor = end
            if info.slug == target:
                result.append(by_slug.get(file.stem) or TermInfo(file.stem, file.stem, f"/{TERMS_DIR}/{file.name}"))
                break
    return result


def render(root: Path, file_path: str) -> str | None:
    file = root / file_path.lstrip("/")
    if file.suffix not in (".md", ".mdx"):
        return None
    try:
        text = read_file(root, file)
    except OSError:
        return None

    if not is_term(file_path):
        try:
            front_matter, body = split_front_matter(text)
        except Exception:
            front_matter, body = {}, text
        content, _ = render_markdown(body, None)
        return _page(str(front_matter.get("title") or file.stem), content)

    issues = check_term(file, text)
    try:
        front_matter, body = split_front_matter(text)
    except Exception:
        front_matter, body = {}, FRONT_MATTER.sub("", text, count=1)
    term_map = build_term_map(root)
    linker = TermLinker(term_map, self_slugs_for(file, body, term_map))
    content, linked = render_markdown(body, linker)

    title = str(front_matter.get("title") or file.stem)
    tags = [str(tag) for tag in front_matter.get("tags") or []]

    def tag_class(tag: str) -> str:
        if tag in TERM_STATUSES:
            return f"status {tag.replace(' ', '-')}"
        if tag in TERM_CLASSES:
            return "class"
        if tag in TERM_GROUPS:
            return "group"
        return ""

    tags_html = "".join(f'<span class="tag {tag_class(tag)}">{html.escape(tag)}</span>' for tag in tags)
    if issues:
        items = "".join(f'<li class="{i["level"]}">{html.escape(i["message"])}</li>' for i in issues)
        checks = f'<div class="checks"><strong>Checks</strong><ul>{items}</ul></div>'
    else:
        checks = '<div class="checks ok">All checks passed.</div>'

    header = f'<div class="tags">{tags_html}</div>'
    if front_matter.get("description"):
        header = f'<p class="summary">{html.escape(str(front_matter["description"]))}</p>' + header

    unique_links = list({info.slug: info for info in linked}.values())
    linked_from = backlinks(root, file_path, term_map)
    relations = ""
    if unique_links:
        relations += f"<h3>Links to {len(unique_links)} terms</h3>{_term_list(sorted(unique_links, key=lambda t: t.title.lower()))}"
    if linked_from:
        relations += f"<h3>Linked from {len(linked_from)} terms</h3>{_term_list(linked_from)}"

    # The H1 is part of the body, so the summary and tags go right after it
    h1_end = content.find("</h1>")
    if h1_end >= 0:
        content = content[: h1_end + 5] + header + checks + content[h1_end + 5 :]
    else:
        content = f"<h1>{html.escape(title)}</h1>{header}{checks}{content}"
    if relations:
        content += f'<div class="relations">{relations}</div>'
    return _page(title, content)


# Exit code for files without a preview
NO_PREVIEW = 3


def main(command: str, root: str, file_path: str) -> int:
    if command == "render":
        page = render(Path(root), file_path)
        if page is None:
            return NO_PREVIEW
        sys.stdout.write(page)
    elif command == "usage":
        json.dump([asdict(info) for info in backlinks(Path(root), file_path)], sys.stdout)
    else:
        raise ValueError(f"Unknown command: {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
