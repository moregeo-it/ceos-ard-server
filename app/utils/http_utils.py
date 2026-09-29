import hashlib
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

from fastapi import HTTPException, status

from app.schemas.error import create_error_detail


@contextmanager
def internal_errors(operation: str, logger: logging.Logger) -> Generator[None]:
    """Turn an unexpected exception in a route into a logged 500; HTTPExceptions pass through."""
    try:
        yield
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to {operation}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail(operation, e)) from e


def compute_file_etag(stat_result: os.stat_result) -> str:
    """Build an ETag validator from a file's size and modification time."""
    base = f"{stat_result.st_mtime_ns}-{stat_result.st_size}"
    return f'"{hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()}"'


def if_none_match_matches(if_none_match: str, etag: str) -> bool:
    """Return True if an If-None-Match header value covers the given ETag."""
    if if_none_match.strip() == "*":
        return True

    def _normalize(tag: str) -> str:
        tag = tag.strip()
        # Ignore the weak-validator prefix when comparing.
        return tag[2:] if tag.startswith("W/") else tag

    normalized_etag = _normalize(etag)
    return any(_normalize(candidate) == normalized_etag for candidate in if_none_match.split(","))
