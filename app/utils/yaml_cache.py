"""mtime-keyed YAML parse cache for the hot read paths.

PFS documents are parsed on the event loop on every /pfs and /context request but change
only on save. Keyed on (mtime_ns, size) — size breaks the tie for rewrites within the
filesystem's mtime resolution.
"""

import logging
import threading
from collections import OrderedDict
from pathlib import Path

from yaml import load as yaml_load

from app.utils.limits import MB, check_read_size

logger = logging.getLogger(__name__)

# By the size of the files it holds, as a parse takes about eight times that in memory
_MAX_BYTES = 16 * MB

_cache: OrderedDict[str, tuple[int, int, object]] = OrderedDict()
_cached_bytes = 0
# Also used from worker threads, e.g. the usage lookup of /context
_lock = threading.Lock()


def load_yaml_cached(path: Path, loader) -> object:
    """
    Parse a YAML file, reusing the previous parse while the file is unchanged.

    The returned object is SHARED and must be treated as read-only: callers that mutate the
    parse (create_workspace_pfs does) must load directly instead, or they poison the cache.
    """
    stat = path.stat()
    check_read_size(stat.st_size, path.name)
    key = str(path)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            mtime_ns, size, parsed = cached
            if mtime_ns == stat.st_mtime_ns and size == stat.st_size:
                _cache.move_to_end(key)
                return parsed

    parsed = yaml_load(path.read_text(encoding="utf-8"), Loader=loader)
    with _lock:
        global _cached_bytes
        if (previous := _cache.pop(key, None)) is not None:
            _cached_bytes -= previous[1]
        _cache[key] = (stat.st_mtime_ns, stat.st_size, parsed)
        _cached_bytes += stat.st_size
        while _cached_bytes > _MAX_BYTES and len(_cache) > 1:
            _, (_, size, _) = _cache.popitem(last=False)
            _cached_bytes -= size
    return parsed
