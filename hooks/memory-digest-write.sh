#!/usr/bin/env bash
# Stop hook — refresh the read-on-write digest snapshot in the canonical store.
# Bootstrap rewrites __STORE__/__FTS_DB__/__PY__/__LIB__ to your paths.
# Fail-open: never blocks session end.
export MEMORYWEAVER_DATA_DIR=__STORE__
export MEMORYWEAVER_FTS_DB_PATH=__FTS_DB__
cd __LIB__ 2>/dev/null || exit 0
__PY__ -m memoryweaver digest --n 8 >/dev/null 2>&1 || true
exit 0
