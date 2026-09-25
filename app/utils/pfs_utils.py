try:
    # libyaml-backed loader: roughly an order of magnitude faster than the pure-Python one,
    # which matters because PFS documents are parsed on the event loop in request paths.
    from yaml import CSafeLoader as _BaseSafeLoader
except ImportError:  # pragma: no cover — conda-forge PyYAML ships libyaml, but don't require it
    from yaml import SafeLoader as _BaseSafeLoader


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


class PlainStringSafeLoader(_BaseSafeLoader):
    pass


PlainStringSafeLoader.yaml_implicit_resolvers = {
    key: [resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in _BaseSafeLoader.yaml_implicit_resolvers.items()
}
