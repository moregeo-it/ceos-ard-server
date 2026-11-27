import re
import unicodedata

BRANCH_NAME_MAX_LENGTH = 46
# Letters that NFKD can't reduce to a base letter, which would otherwise be dropped
_FOLD = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "ss",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "ø": "o",
        "Ø": "o",
        "ł": "l",
        "Ł": "l",
        "đ": "d",
        "Đ": "d",
        "ð": "d",
        "Ð": "d",
        "þ": "th",
        "Þ": "th",
        "ı": "i",
    }
)


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def make_branch_name(title: str, selection: list[str] | None, workspace_id: str) -> str:
    """ws-<slug>-<last 8 characters of the id>, at most 46 characters.

    The slug comes from the title, or from the selection when the title leaves fewer than 3 characters.
    """
    suffix = f"-{workspace_id[-8:]}"
    slug = _slugify(title)
    if len(slug.replace("-", "")) < 3:
        slug = _slugify(" ".join(selection or []))
    slug = slug[: BRANCH_NAME_MAX_LENGTH - len("ws-") - len(suffix)].rstrip("-")
    return f"ws-{slug}{suffix}" if slug else f"ws{suffix}"
