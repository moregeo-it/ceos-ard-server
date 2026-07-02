from yaml import SafeLoader


def build_default_pfs_document(
    *,
    title: str = "",
    version: str = "",
    type: str = "",
    applies_to: str = "",
    author_username: str = None,
) -> dict:
    data = {
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
        "authors": [],
        "changes": [],
    }
    if author_username is not None:
        data["authors"].append(author_username)

    return data


class PlainStringSafeLoader(SafeLoader):
    pass


PlainStringSafeLoader.yaml_implicit_resolvers = {
    key: [resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in SafeLoader.yaml_implicit_resolvers.items()
}
