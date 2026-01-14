import mimetypes


def get_file_media_type(file_path):
    return mimetypes.guess_type(file_path)[0] or "text/plain"


def get_excerpt(text, pattern, start, context=30):
    """Extracts an excerpt from a line around the first occurrence of the pattern."""
    if start >= 0:
        end = start + len(pattern)
        a = max(0, start - context)
        b = min(len(text), end + context)
        excerpt = text[a:b].replace("\n", "").replace("\r", "").replace("\t", " ").strip()
        if a > 0:
            excerpt = "…" + excerpt
        if b < len(text):
            excerpt = excerpt + "…"

        return excerpt
    return ""
