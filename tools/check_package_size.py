"""Check the prospective GitHub source ZIP and extracted size, without packaging caches.

Uses current tracked files plus nonignored new files, so it also works before a
commit. This is a working-tree estimate, not the currently published market ZIP.
"""

import io
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 16 * 1024 * 1024


def main():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).split(b"\0")
    paths = sorted({name.decode("utf-8") for name in names if name})
    attributes = subprocess.check_output(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"],
        input=b"\0".join(name.encode("utf-8") for name in paths) + b"\0",
        cwd=ROOT,
    ).split(b"\0")
    ignored = {
        attributes[index].decode("utf-8")
        for index in range(0, len(attributes) - 1, 3)
        if attributes[index + 2] == b"set"
    }
    raw_size = count = 0
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in paths:
            path = ROOT / name
            if name in ignored or not path.is_file():
                continue
            data = path.read_bytes()
            raw_size += len(data)
            count += 1
            archive.writestr(f"cs2-plugin-source/{name}", data)
    zip_size = output.tell()
    print(f"Working-tree package estimate: {count} files")
    for label, size in (("ZIP", zip_size), ("Extracted", raw_size)):
        print(f"{label}: {size:,} bytes ({size / 1024**2:.2f} MiB)")
    if max(raw_size, zip_size) >= LIMIT:
        raise SystemExit("Package must stay below 16 MiB in both measurements")
    print(f"Both below 16 MiB; minimum headroom: {(LIMIT - max(raw_size, zip_size)) / 1024**2:.2f} MiB")


if __name__ == "__main__":
    main()
