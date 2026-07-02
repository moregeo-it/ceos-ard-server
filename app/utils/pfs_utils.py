def build_default_pfs_document(
    *,
    title: str,
    version: str,
    type: str | None,
    applies_to: str,
    author_username: str,
) -> dict:
    return {
        "title": title,
        "version": version,
        "type": type,
        "applies_to": applies_to,
        "background": "",
        "glossary": [],
        "references": [],
        "introduction": [],
        "requirements": [],
        "annexes": [],
        "authors": [author_username],
        "changes": [],
    }
