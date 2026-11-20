#!/usr/bin/env python3
"""Exercise the built application and its real native engine on each CI OS.

This test performs conversion only. It does not open a desktop window or inject
keyboard input. Actual external application acceptance is tested separately by
test_native.py on a private Linux display.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("target/debug"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    binary = directory / ("ztype.exe" if sys.platform == "win32" else "ztype")
    plugin_name = "kana_plugin.dll" if sys.platform == "win32" else (
        "libkana_plugin.dylib" if sys.platform == "darwin" else "libkana_plugin.so")
    plugin = directory / "examples" / plugin_name
    binary.resolve(strict=True)
    plugin.resolve(strict=True)
    checks = []

    def run(options, expected=None, stdin=None, error=None):
        result = subprocess.run([str(binary), *options, "--no-config"], input=stdin, capture_output=True,
                                text=True, encoding="utf-8", timeout=15)
        if error is not None:
            assert result.returncode != 0, (options, "unexpected success")
            for fragment in (error,) if isinstance(error, str) else error:
                assert fragment in result.stderr, (options, result.stderr)
            assert result.stdout == "", (options, "error emitted successful text", result.stdout)
        else:
            assert result.returncode == 0, (options, result.stderr)
            assert result.stdout == expected + "\n", (options, result.stdout, expected)
        checks.append(options[0])

    run(["convert", "--text", "nihao"], "你好")
    run(["convert", "--text", "ta", "--candidate", "2"], "她")
    run(["convert", "--engine", "kana", "--text", "sushi"], "すし")
    run(["convert", "--engine", "unicode", "--text", "U+1F642 4F60"], "🙂你")
    text = "α/مرحبا/नमस्ते/🙂"
    run(["convert", "--engine", "literal", "--text", "-"], text, stdin=text)
    run(["convert", "--plugin", str(plugin), "--text", "sushi"], "すし")
    run(["convert", "--plugin", str(plugin), "--text", "sushi", "--candidate", "2"], "スシ")
    run(["convert", "--engine", "unicode", "--text", "U+D800"], error="unsupported Unicode scalar")
    run(["convert", "--text", "hello", "--candidate", "0"], error="candidate must be")
    run(["convert", "--engine", "missing", "--text", "hello"], error="unknown engine")
    run(["commit", "--text", "nihao"], error="commit requires --window")
    run(["convert", "--engine", "literal", "--text", "-"], stdin="x" * 4097, error="text exceeds")
    with tempfile.TemporaryDirectory(prefix="ztype-cli-") as temporary:
        path = Path(temporary) / "phrases.tsv"
        path.write_text("hello\t你好\t100\nhello\tمرحبا\t80\n", encoding="utf-8")
        run(["convert", "--engine", "table", "--dict", str(path), "--text", "hello", "--candidate", "2"], "مرحبا")
        path.write_bytes(b"hello\t\xff\n")
        run(["convert", "--engine", "table", "--dict", str(path), "--text", "hello"],
            error=(f"dictionary {path}", "must be UTF-8"))
    print(json.dumps({"platform": sys.platform, "checks": len(checks), "native_plugin": plugin.name,
                      "status": "passed", "desktop_input": "not attempted"}))


if __name__ == "__main__":
    main()
