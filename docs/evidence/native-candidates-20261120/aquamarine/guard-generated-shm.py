#!/usr/bin/env python3
from pathlib import Path
import sys
path=Path(sys.argv[1])
source=path.read_text()
old='void CCWlShm::sendRelease() {\n    if (!pResource)\n        return;\n    destroyed = true;\n\n    auto proxy = wl_proxy_marshal_flags(pResource, 1, nullptr, wl_proxy_get_version(pResource), 1);'
new='void CCWlShm::sendRelease() {\n    if (!pResource)\n        return;\n    destroyed = true;\n\n    // wl_shm.release is a v2 request; v1 clients only destroy their local proxy.\n    if (wl_proxy_get_version(pResource) < 2) {\n        wl_proxy_destroy(pResource);\n        return;\n    }\n\n    auto proxy = wl_proxy_marshal_flags(pResource, 1, nullptr, wl_proxy_get_version(pResource), 1);'
if new in source:
    raise SystemExit(0)
if source.count(old)!=1:
    raise SystemExit("unexpected generated wl_shm.release implementation")
path.write_text(source.replace(old,new))
