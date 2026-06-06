"""Local-dev helper: re-tag the lru-dict 1.4.1 cp313 wheel as 1.3.0.

Home Assistant pins ``lru-dict==1.3.0`` but that version ships no cp313 Windows
wheel, while 1.4.1 does and is API-compatible. This lets the test suite run on
Windows + Python 3.13 without MSVC build tools. Not used at runtime or in CI
(Linux has a 1.3.0 manylinux wheel).
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import zipfile

SRC_VER = "1.4.1"
DST_VER = "1.3.0"


def _hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def retag(src_whl: str, out_dir: str) -> str:
    """Rewrite ``src_whl`` (version 1.4.1) as version 1.3.0 in ``out_dir``."""
    with zipfile.ZipFile(src_whl) as zin:
        names = zin.namelist()
        contents = {n: zin.read(n) for n in names}

    new_contents: dict[str, bytes] = {}
    for name, data in contents.items():
        new_name = name.replace(
            f"lru_dict-{SRC_VER}.dist-info", f"lru_dict-{DST_VER}.dist-info"
        )
        if new_name.endswith(".dist-info/METADATA"):
            data = data.replace(
                f"Version: {SRC_VER}".encode(), f"Version: {DST_VER}".encode()
            )
        if new_name.endswith(".dist-info/RECORD"):
            continue  # rebuilt below
        new_contents[new_name] = data

    record_name = f"lru_dict-{DST_VER}.dist-info/RECORD"
    lines = [f"{name},{_hash(data)},{len(data)}" for name, data in new_contents.items()]
    lines.append(f"{record_name},,")
    new_contents[record_name] = ("\n".join(lines) + "\n").encode()

    out_path = os.path.join(out_dir, f"lru_dict-{DST_VER}-cp313-cp313-win_amd64.whl")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in new_contents.items():
            zout.writestr(name, data)
    return out_path


if __name__ == "__main__":
    print(retag(sys.argv[1], sys.argv[2]))
