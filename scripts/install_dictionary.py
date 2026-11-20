#!/usr/bin/env python3
"""Install a pinned, separately licensed pinyin vocabulary for Ztype.

The application remains independent of Rime/Fcitx libraries and services. The
downloaded data keeps its original GPL-3.0 license, source files and provenance.
Only public vocabulary is read; this tool never inspects user input history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

REPOSITORY = "https://github.com/iDvel/rime-ice"
REVISION = "927e1cf1d3eeb9bf4be85e3f684dba4dd485b6f6"
SOURCES = {
    "cn_dicts/8105.dict.yaml": (
        115728,
        "1f9a42b91dea6982baee2551981780271aeffd78876662b9c9f324e56b37b120",
    ),
    "cn_dicts/base.dict.yaml": (
        16609062,
        "c1f07daa4680a437a04169211e4a80b19923fd61cff322753d3ee98a7e1bffc8",
    ),
    "LICENSE": (
        35149,
        "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    ),
}


def default_destination() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base and not Path(base).is_absolute():
        raise ValueError("XDG_DATA_HOME must be an absolute path")
    return (Path(base) if base else Path.home() / ".local/share") / "ztype/dictionaries"


def source_bytes(relative: str, source_dir: Path | None) -> bytes:
    size, expected = SOURCES[relative]
    if source_dir is None:
        url = f"https://raw.githubusercontent.com/iDvel/rime-ice/{REVISION}/{relative}"
        request = urllib.request.Request(url, headers={"User-Agent": "ztype-dictionary-installer"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(size + 1)
    else:
        path = source_dir / relative
        if not path.exists():
            path = source_dir / Path(relative).name
        with path.open("rb") as stream:
            data = stream.read(size + 1)
    actual = hashlib.sha256(data).hexdigest()
    if len(data) != size or actual != expected:
        raise ValueError(f"{relative}: source size or SHA-256 differs from pinned revision {REVISION}")
    return data


def parse_rime(data: bytes, name: str) -> tuple[list[tuple[str, str, int]], int]:
    active = False
    entries = []
    source_count = 0
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if line.strip() == "...":
            active = True
            continue
        if not active or not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"{name}:{number}: expected text, spelling and frequency")
        text, syllables, frequency = fields
        code = "".join(syllables.split()).lower().replace("ü", "v")
        frequency = frequency.split("#", 1)[0].strip()
        if (not code or len(code) > 128 or not code.isascii() or not code.isalpha()
                or not text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)
                or len(text.encode("utf-8")) > 4096
                or not frequency.isdecimal() or not 0 <= int(frequency) <= 0xFFFFFFFF):
            raise ValueError(f"{name}:{number}: unsupported or invalid vocabulary entry")
        entries.append((code, text, int(frequency)))
        source_count += 1
        parts = syllables.lower().replace("ü", "v").split()
        separated = parts[0] + "".join(
            ("'" if part[0] in "aeo" else "") + part for part in parts[1:]
        )
        if separated != code:
            if len(separated) > 128:
                raise ValueError(f"{name}:{number}: separated spelling exceeds 128 bytes")
            entries.append((separated, text, int(frequency)))
    if not active or not entries:
        raise ValueError(f"{name}: no Rime dictionary body")
    return entries, source_count


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ztype-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(destination: Path, source_dir: Path | None) -> dict:
    # Fetch and validate every source before replacing an installed dictionary.
    sources = {name: source_bytes(name, source_dir) for name in SOURCES}
    combined: dict[tuple[str, str], int] = {}
    counts = {}
    aliases = {}
    for name, raw in sources.items():
        if name == "LICENSE":
            continue
        entries, source_count = parse_rime(raw, name)
        counts[name] = source_count
        aliases[name] = len(entries) - source_count
        for code, text, priority in entries:
            combined[code, text] = max(priority, combined.get((code, text), 0))
    header = (
        "# Rime Ice pinyin data; GPL-3.0. Separate from the Ztype application license.\n"
        f"# Source: {REPOSITORY}/tree/{REVISION}\n"
        "# Original source files and LICENSE: rime-ice-source/\n"
        "# code<TAB>text<TAB>frequency\n"
    )
    output = (header + "".join(
        f"{code}\t{text}\t{priority}\n"
        for (code, text), priority in sorted(combined.items())
    )).encode("utf-8")
    provenance = {
        "repository": REPOSITORY,
        "revision": REVISION,
        "license": "GPL-3.0",
        "sources": [
            {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
             **({"entries": counts[name], "boundary_aliases": aliases[name]} if name in counts else {})}
            for name, raw in sources.items()
        ],
        "transformation": "Join pinyin syllables and preserve standard apostrophes before vowel-initial syllables as aliases; retain maximum frequency for identical code/text; sort code/text.",
        "output": {"file": "rime-ice.tsv", "entries": len(combined), "bytes": len(output),
                   "sha256": hashlib.sha256(output).hexdigest()},
    }
    for name, raw in sources.items():
        atomic_write(destination / "rime-ice-source" / name, raw)
    atomic_write(destination / "rime-ice-source/provenance.json",
                 (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode())
    atomic_write(destination / "rime-ice.tsv", output)
    return {"dictionary": str(destination / "rime-ice.tsv"), **provenance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--source", choices=("rime-ice", "local"), default="rime-ice")
    parser.add_argument("--source-dir", type=Path, help="Pinned source files for --source local")
    args = parser.parse_args()
    if (args.source == "local") != (args.source_dir is not None):
        parser.error("--source local requires --source-dir; --source-dir is valid only with --source local")
    try:
        result = install(args.destination or default_destination(), args.source_dir)
    except (OSError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, f"ztype dictionary installation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
