#!/usr/bin/env python3
"""Pack vscript/addon/ into a single-file VPK (version 1) add-on for L4D2.

Usage: python vscript/build_vpk.py [output.vpk]
Default output: vscript/dist/l4d2_voicecmd.vpk
"""

import struct
import sys
import zlib
from pathlib import Path

VPK_SIGNATURE = 0x55AA1234
EMBEDDED_ARCHIVE = 0x7FFF  # data stored in the same file, right after the tree

HERE = Path(__file__).resolve().parent
ADDON_DIR = HERE / "addon"
DEFAULT_OUTPUT = HERE / "dist" / "l4d2_voicecmd.vpk"


def collect(root: Path) -> dict[tuple[str, str, str], bytes]:
    """Map (extension, directory, name) -> file contents."""
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        directory = rel.parent.as_posix()
        files[(path.suffix[1:] or " ", " " if directory == "." else directory.lower(), rel.stem.lower())] = (
            path.read_bytes()
        )
    return files


def build(root: Path) -> bytes:
    files = collect(root)

    tree = bytearray()
    data = bytearray()
    for ext in sorted({k[0] for k in files}):
        tree += ext.encode() + b"\0"
        for directory in sorted({k[1] for k in files if k[0] == ext}):
            tree += directory.encode() + b"\0"
            for name in sorted(k[2] for k in files if k[0] == ext and k[1] == directory):
                content = files[(ext, directory, name)]
                tree += name.encode() + b"\0"
                tree += struct.pack(
                    "<IHHIIH", zlib.crc32(content), 0, EMBEDDED_ARCHIVE, len(data), len(content), 0xFFFF
                )
                data += content
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"

    return struct.pack("<III", VPK_SIGNATURE, 1, len(tree)) + bytes(tree) + bytes(data)


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build(ADDON_DIR))
    print(f"wrote {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
