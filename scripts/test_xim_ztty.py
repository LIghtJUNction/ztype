#!/usr/bin/env python3
"""Check the actual Ztty PTY with native Ztype XIM on a private Xvfb display."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

RECORDER = """import json,os,sys
from pathlib import Path
out=Path(sys.argv[1])
out.with_suffix('.ready').write_text(str(os.getpid()))
print('ZTYPE PRIVATE XIM ACCEPTANCE',flush=True)
with out.open('a', buffering=1) as stream:
 while True:
  line=sys.stdin.buffer.readline()
  if not line: break
  stream.write(json.dumps({'utf8':line.decode('utf-8'),'hex':line.hex()},ensure_ascii=False)+'\\n')
  print('received: '+repr(line.decode('utf-8')),flush=True)
"""

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ztype", required=True)
    parser.add_argument("--ztty", default=shutil.which("ztty"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dictionary", help="Read-only TSV vocabulary; user settings and learning stay isolated")
    parser.add_argument("--scenario", choices=["typing", "reconnect"], default="typing")
    args = parser.parse_args()
    if os.environ.get("ZTYPE_XIM_TEST_PRIVATE") != "1" or not os.environ.get("DISPLAY") or os.environ["DISPLAY"] in {":0", ":1"}:
        raise SystemExit("requires private xvfb-run and ZTYPE_XIM_TEST_PRIVATE=1")
    binary = Path(args.ztype).resolve()
    ztty_binary = Path(args.ztty).resolve()
    dictionary = Path(args.dictionary).resolve() if args.dictionary else None
    dictionary_info = None
    if dictionary is not None:
        data = dictionary.read_bytes()
        dictionary_info = {"path":str(dictionary),"bytes":len(data),
            "sha256":hashlib.sha256(data).hexdigest(),
            "entries":sum(bool(line) and not line.startswith(b"#") for line in data.splitlines())}
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    recorder = out/"recorder.py"
    recorder.write_text(RECORDER)
    lines_path = out/"pty-lines.jsonl"
    if lines_path.exists():
        raise SystemExit("use a fresh output directory to preserve earlier evidence")
    runtime = out/"runtime"
    runtime.mkdir(mode=0o700)
    # Validate the private-display marker above before dropping inherited
    # product settings. A daily build must never read or learn into user data.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("ZTYPE_")}
    for name in ["WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "ZTTY_SESSION"]:
        env.pop(name, None)
    isolated_xdg = tempfile.TemporaryDirectory(prefix="ztype-xim-xdg-")
    for kind in ["config", "data", "state", "cache"]:
        directory = Path(isolated_xdg.name)/kind
        directory.mkdir(mode=0o700)
        env["XDG_"+kind.upper()+"_HOME"] = str(directory)
    env["XDG_CONFIG_DIRS"] = env["XDG_CONFIG_HOME"]
    env["XDG_DATA_DIRS"] = "/usr/local/share:/usr/share"
    name = "ztype-private-xim-" + str(os.getpid())
    xim_name = "fcitx" if args.scenario == "reconnect" else "ztype"
    env.update({"LC_ALL":"C.UTF-8", "XMODIFIERS":"@im="+xim_name,
                "ZTYPE_XIM_NAME":xim_name, "ZTTY_SOCKET":"@"+name,
                "XDG_RUNTIME_DIR":str(runtime), "LIBGL_ALWAYS_SOFTWARE":"1"})
    server = terminal = None
    checks = []
    window = None
    def run(*command, check=True, timeout=10):
        return subprocess.run(command, env=env, capture_output=True, text=True,
                              check=check, timeout=timeout)
    def wait(predicate, label, timeout=10):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if server is not None and server.poll() is not None:
                raise AssertionError("XIM server exited: "+(out/"ztype.log").read_text())
            value = predicate()
            if value:
                return value
            time.sleep(0.03)
        raise AssertionError(label)
    def passed(label):
        checks.append(label)
        print("PASS "+label, flush=True)
    def records():
        if not lines_path.exists():
            return []
        return [json.loads(line) for line in lines_path.read_text().splitlines()]
    def focus_check():
        actual = run("xdotool","getwindowfocus").stdout.strip()
        assert actual == window, (actual, window)
    def text(value):
        assert value.isascii()
        focus_check()
        run("xdotool","type","--clearmodifiers","--delay","55","--",value)
    def key(*values):
        focus_check()
        run("xdotool","key","--clearmodifiers",*values)
    def expect_line(index, expected, label):
        wait(lambda: len(records()) > index, "PTY did not receive "+label)
        row = records()[index]
        assert row == {"utf8":expected,"hex":expected.encode().hex()}, row
        passed(label)
    def visible_popup():
        result = run("xdotool","search","--onlyvisible","--name",
                     "^zinput-x11 candidate popup$",check=False)
        ids = result.stdout.split()
        assert len(ids) <= 1, ids
        return ids[0] if ids else None
    def close_owned_broker():
        marker = ("ZTTY_SOCKET="+env["ZTTY_SOCKET"]).encode()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if marker not in (entry/"environ").read_bytes().split(b"\0"):
                    continue
                if (entry/"exe").resolve() != ztty_binary:
                    continue
                os.kill(int(entry.name), signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
    ztype_log = (out/"ztype.log").open("w")
    terminal_log = (out/"ztty.log").open("w")
    def start_server():
        nonlocal server
        ready = "XMODIFIERS=@im="+xim_name+";"
        previous=(out/"ztype.log").read_text().count(ready)
        command = [str(binary),"x11"]
        if dictionary is not None:
            command.extend(["--dict",str(dictionary)])
        server = subprocess.Popen(command, env=env,
            stdout=ztype_log,stderr=subprocess.STDOUT)
        wait(lambda: (out/"ztype.log").read_text().count(ready) > previous,
             "XIM server did not become ready with the requested name")
    def stop_server():
        nonlocal server
        server.terminate()
        server.wait(timeout=5)
        server=None
    def identity():
        return {"window":window,
                "broker_pid":int(run("xdotool","getwindowpid",window).stdout.strip()),
                "pty_pid":int(lines_path.with_suffix(".ready").read_text())}
    try:
        if args.scenario == "typing":
            start_server()
        terminal = subprocess.Popen([str(ztty_binary),"--backend","x11","--name",name,
            "--geometry","80x24","--cwd",str(out),"-e",sys.executable,"-I","-u",str(recorder),str(lines_path)],
            env=env,stdout=terminal_log,stderr=subprocess.STDOUT)
        wait(lambda: lines_path.with_suffix(".ready").exists(), "Ztty recorder did not start")
        def terminal_window():
            result = run("xdotool","search","--onlyvisible","--class","ztty",check=False)
            ids=result.stdout.split()
            assert len(ids) <= 1, ids
            return ids[0] if ids else None
        window=wait(terminal_window, "Ztty window did not appear")
        run("xdotool","windowfocus","--sync",window)
        original_identity=identity()
        identities=[{"phase":"opened",**original_identity}]
        def unchanged(phase):
            current=identity()
            assert current == original_identity, (original_identity,current)
            assert Path("/proc/"+str(current["broker_pid"])).exists()
            assert Path("/proc/"+str(current["pty_pid"])).exists()
            identities.append({"phase":phase,**current})
        passed("installed Ztty opens its own X11 window and live PTY")
        geometry=None
        if args.scenario == "reconnect":
            text("before");key("Return")
            expect_line(0,"before\n","terminal receives ordinary text before the input service exists")
            start_server()
            text("nihao")
            wait(visible_popup,"existing terminal did not discover the new same-name XIM service")
            focus_check()
            key("space","Return")
            expect_line(1,"你好\n","existing terminal discovers Ztype through the explicit fcitx XIM name")
            unchanged("first-XIM-connection")
            stop_server()
            text("offline");key("Return")
            expect_line(2,"offline\n","terminal remains usable while its input service is stopped")
            unchanged("XIM-stopped")
            start_server()
            text("nihao")
            wait(visible_popup,"existing terminal did not reconnect to its restarted XIM service")
            focus_check()
            key("space","Return")
            expect_line(3,"你好\n","existing terminal reconnects after the XIM service restarts")
            unchanged("XIM-reconnected")
            passed("broker PID, PTY process and terminal window stay unchanged throughout recovery")
        else:
            text("nihao")
            popup=wait(visible_popup, "native candidate popup did not appear")
            focus_check()
            geometry=run("xdotool","getwindowgeometry","--shell",popup).stdout
            if shutil.which("magick"):
                run("magick","import","-display",env["DISPLAY"],"-window","root",
                    str(out/"ztty-candidates.png"))
            passed("native candidate popup appears without moving Ztty focus")
            key("space","Return")
            expect_line(0,"你好\n","pinyin Space commits Chinese to the real PTY")
            wait(lambda: not visible_popup(), "candidate popup remained after commit")
            text("nihao");key("Tab","space","Return")
            expect_line(1,"拟好\n","Tab selects the second candidate in Ztty")
            text("nihao");key("Escape","Return")
            expect_line(2,"\n","Escape cancels composition before the PTY receives text")
            key("ctrl+space");text("hello world");key("Return")
            expect_line(3,"hello world\n","Ctrl+Space switches to ordinary English typing")
            key("ctrl+space");text("nihao");key("space","Return")
            expect_line(4,"你好\n","Ctrl+Space restores Chinese composition")
            text("nihaox");key("BackSpace","space","Return")
            expect_line(5,"你好\n","Backspace edits composition in the real terminal")
            unchanged("typing-complete")
        assert server.poll() is None
        focus_check()
        if dictionary is not None:
            assert hashlib.sha256(dictionary.read_bytes()).hexdigest() == dictionary_info["sha256"], "dictionary changed during acceptance"
        report={
            "scope":"installed Ztty X11 backend, native XIM, real PTY on private Xvfb",
            "scenario":args.scenario,"xim_name":xim_name,"dictionary":dictionary_info,
            "environment":{"ztype_variables":sorted(key for key in env if key.startswith("ZTYPE_")),
                "temporary_xdg":{key:env[key] for key in ["XDG_CONFIG_HOME","XDG_DATA_HOME",
                    "XDG_STATE_HOME","XDG_CACHE_HOME","XDG_CONFIG_DIRS"]},
                "system_data_dirs":env["XDG_DATA_DIRS"],
                "home_preserved":env.get("HOME") == os.environ.get("HOME")},
            "passed":len(checks),"checks":checks,
            "ztype":{"path":str(binary),"sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
                     "bytes":binary.stat().st_size,"features":json.loads(run(str(binary),"features").stdout)},
            "ztty":{"path":str(ztty_binary),"sha256":hashlib.sha256(ztty_binary.read_bytes()).hexdigest(),
                    "version":run(str(ztty_binary),"--version").stdout.strip()},
            "display":env["DISPLAY"],"socket":env["ZTTY_SOCKET"],"window":window,
            "popup_geometry":geometry,"received":records(),"identities":identities
        }
        (out/"acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        if terminal is not None:
            run(str(ztty_binary),"close",name,check=False)
        close_owned_broker()
        for process in [terminal, server]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        ztype_log.close();terminal_log.close()
        isolated_xdg.cleanup()

if __name__ == "__main__":
    main()
