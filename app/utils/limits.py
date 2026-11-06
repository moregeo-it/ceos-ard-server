from pathlib import Path

KB = 1024
MB = 1024 * KB
GB = 1024 * MB

# Workspace files the server reads itself, far above real files (the largest YAML file has 13 KB, the largest file
# is a 1.5 MB PDF): a fork can sync in anything, while saves and uploads are limited by MAX_FILE_SIZE_MB.
# Text is parsed or searched, and a YAML file takes about eight times its size in memory.
MAX_TEXT_SIZE = 1 * MB
MAX_BINARY_SIZE = 5 * MB
TEXT_EXTENSIONS = frozenset(
    {".yaml", ".yml", ".md", ".markdown", ".txt", ".bib", ".json", ".xml", ".html", ".htm", ".svg", ".csv", ".css", ".js", ".lua", ".tex"}
)
# The preview HTML a build writes, which the server reads to add the Edit buttons
MAX_PREVIEW_SIZE = 16 * MB


class FileTooLarge(ValueError):
    pass


def max_read_size(name: str) -> int:
    return MAX_TEXT_SIZE if Path(name).suffix.lower() in TEXT_EXTENSIONS else MAX_BINARY_SIZE


def check_read_size(size: int, name: str, limit: int | None = None) -> None:
    """Raise FileTooLarge above `limit`, by default the limit of the file's kind (text or binary)."""
    limit = max_read_size(name) if limit is None else limit
    if size > limit:
        raise FileTooLarge(f"{name} is larger than {limit / MB:g} MB")
