"""mtime-keyed YAML parse cache for the hot read paths.

PFS documents are parsed on the event loop on every /pfs and /context request but change
only on save. Keyed on (mtime_ns, size) — size breaks the tie for rewrites within the
filesystem's mtime resolution.
"""

import logging
from collections import OrderedDict
from pathlib import Path

from yaml import load as yaml_load

logger = logging.getLogger(__name__)

# Bounded so long-gone workspaces cannot grow it forever
_MAX_ENTRIES = 1024

_cache: OrderedDict[str, tuple[int, int, object]] = OrderedDict()


def load_yaml_cached(path: Path, loader) -> object:
    """
    Parse a YAML file, reusing the previous parse while the file is unchanged.

    The returned object is SHARED and must be treated as read-only: callers that mutate the
    parse (create_workspace_pfs does) must load directly instead, or they poison the cache.
    """
    stat = path.stat()
    key = str(path)
    cached = _cache.get(key)
    if cached is not None:
        mtime_ns, size, parsed = cached
        if mtime_ns == stat.st_mtime_ns and size == stat.st_size:
            _cache.move_to_end(key)
            return parsed

    parsed = yaml_load(path.read_text(encoding="utf-8"), Loader=loader)
    _cache[key] = (stat.st_mtime_ns, stat.st_size, parsed)
    _cache.move_to_end(key)
    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)
    return parsed
