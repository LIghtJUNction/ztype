#!/usr/bin/env python3
"""Verify physical-key composition in Chromium on a verified private Wayland output."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import tempfile
import threading
import time

from test_wayland import build_keyboard, command, focus_window, hypr_json, Keyboard

PAGE = r"""<!doctype html><meta charset="utf-8"><title>Ztype Chromium acceptance</title>
<style>body{max-width:840px;margin:64px auto;padding:0 32px;background:#f8f9fa;color:#202124;font:18px system-ui,sans-serif}h1{font-size:25px;font-weight:500}p{color:#5f6368}textarea,input{box-sizing:border-box;width:100%;padding:20px;font:24px system-ui,sans-serif;border:1px solid #dadce0;border-radius:6px;background:white;color:#202124}textarea{height:210px;resize:none}label{display:block;font-size:14px;margin:24px 0 8px}</style>
<h1>Chromium · Native text input</h1><p>Keyboard → Ztype → Wayland → application</p>
<label for="normal">Text</label><textarea id="normal" autofocus spellcheck="false"></textarea>
<label for="password">Password input</label><input id="password" type="password" autocomplete="off">
<script>
let sequence=0, composing=false, preedit='', starts=0;
function report(){const e=document.activeElement;fetch('/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sequence:++sequence,field:e?.id||'',focused:document.hasFocus(),value:e?.value||'',composing,preedit,starts})}).catch(()=>{});}
for(const e of document.querySelectorAll('textarea,input')){
 e.addEventListener('compositionstart',()=>{composing=true;starts++;report();});
 e.addEventListener('compositionupdate',v=>{preedit=v.data;report();});
 e.addEventListener('compositionend',()=>{composing=false;preedit='';report();});
 for(const name of ['input','focus','blur'])e.addEventListener(name,report);
}
window.addEventListener('focus',report);window.addEventListener('load',()=>{normal.focus();report();});
</script>"""


def private_output():
    if os.environ.get("ZTYPE_ACCEPTANCE_PRIVATE") != "1":
        raise RuntimeError("Chromium acceptance requires the private headless runner")
    runtime = Path(os.environ["ZTYPE_ACCEPTANCE_RUNTIME"])
    info = runtime.stat()
    if (str(runtime) != os.environ.get("XDG_RUNTIME_DIR")
            or runtime.parent != Path("/var/tmp") or not runtime.name.startswith("zt-")
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("unverified private runtime directory")
    if os.environ["WAYLAND_DISPLAY"] == os.environ["ZTYPE_ACCEPTANCE_PARENT_SOCKET"]:
        raise RuntimeError("the acceptance child is connected to its parent compositor")
    pid = int(os.environ["ZTYPE_ACCEPTANCE_COMPOSITOR_PID"])
    instances = hypr_json("instances")
    if not any(item.get("pid") == pid and item.get("instance") == os.environ["HYPRLAND_INSTANCE_SIGNATURE"] for item in instances):
        raise RuntimeError("the compositor does not match the private runner")
    monitors = hypr_json("monitors")
    output = os.environ["ZTYPE_ACCEPTANCE_OUTPUT"]
    if len(monitors) != 1 or monitors[0].get("name") != output:
        raise RuntimeError("the private compositor output changed")
    return output


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path,
                        help="Explicit read-only TSV shared by the native IME and expected CLI candidates")
    parser.add_argument("--enable-wayland-ime", action="store_true", help="Compare explicit Chromium IME flags after a default configuration failure")
    args = parser.parse_args()
    output_name = private_output()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve(strict=True)
    dictionary_path = args.dictionary.resolve(strict=True) if args.dictionary else None
    dictionary_args = ["--no-config", "--dict", str(dictionary_path)] if dictionary_path else []
    browser = shutil.which("google-chrome-stable") or shutil.which("google-chrome") or shutil.which("chromium")
    if not browser:
        raise RuntimeError("Chromium is not installed")
    report = {"passed": False, "application": "Chromium", "transport": "native Wayland", "checks": [], "host_windows_created": False}
    report["browser_version"] = command([browser, "--version"]).stdout.strip()
    report["dictionary"] = str(dictionary_path) if dictionary_path else None
    candidate_lines = command([binary, "candidates", "--text", "ta", *dictionary_args]).stdout.splitlines()
    candidates = [line.split("\t", 1)[1] for line in candidate_lines]
    if len(candidates) < 2:
        raise RuntimeError("Chromium candidate check requires two ta candidates")
    report["expected_second_candidate"] = candidates[1]
    latest = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/state" or not 0 < length <= 8192:
                self.send_error(400)
                return
            state = json.loads(self.rfile.read(length))
            with lock:
                if state.get("sequence", 0) > latest.get("sequence", 0):
                    latest.clear()
                    latest.update(state)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    ime = chrome = keyboard = None
    window_pid = None

    def wait(predicate, message, timeout=12):
        deadline = time.monotonic() + timeout
        state = {}
        while time.monotonic() < deadline:
            if chrome is not None and chrome.poll() is not None:
                raise RuntimeError("Chromium exited before input acceptance")
            if ime is not None and ime.poll() is not None:
                raise RuntimeError("Ztype exited before input acceptance")
            with lock:
                state = dict(latest)
            if predicate(state):
                return state
            time.sleep(0.02)
        raise AssertionError(f"{message}; observed {state!r}")

    def focused():
        if hypr_json("activewindow").get("pid") != window_pid:
            raise RuntimeError("the owned Chromium window lost focus; keyboard injection stopped")

    def key(action, *arguments):
        focused()
        keyboard.send(action, *arguments)
        focused()

    def check(name, predicate, message):
        state = wait(predicate, message)
        report["checks"].append({"name": name, "passed": True, "field": state.get("field"), "composing": state.get("composing")})
        return state

    def clear():
        key("key", "Escape")
        key("shortcut", "ctrl", "a")
        key("key", "BackSpace")
        wait(lambda state: state.get("value") == "" and not state.get("composing"), "Chromium did not clear its own text field")

    try:
        with tempfile.TemporaryDirectory(prefix="chromium-", dir=output) as directory, \
                (output / "ime.log").open("w") as ime_log, \
                (output / "chromium.log").open("w") as chrome_log, ExitStack() as cleanup:
            scratch = Path(directory)
            environment = dict(os.environ)
            environment.pop("WAYLAND_DEBUG", None)
            environment.pop("ZTYPE_RECEIVER_TRACE", None)
            environment["ZTYPE_POPUP_STATE_PATH"] = str(output / "popup-state.json")
            ime = subprocess.Popen([str(binary), "wayland", *dictionary_args], env=environment, stdout=ime_log, stderr=subprocess.STDOUT, start_new_session=True)
            cleanup.callback(stop, ime)
            wait(lambda _: "native Wayland input method ready" in (output / "ime.log").read_text(), "native IME did not become ready")
            keyboard = Keyboard(build_keyboard(scratch), output / "keyboard.xkb", environment)
            cleanup.callback(keyboard.stop)
            browser_environment = dict(environment)
            for name in ("GTK_IM_MODULE", "QT_IM_MODULE", "QT_IM_MODULES", "XMODIFIERS", "ZTYPE_POPUP_STATE_PATH"):
                browser_environment.pop(name, None)
            flags = ["--ozone-platform=wayland", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--password-store=basic", f"--user-data-dir={scratch / 'profile'}", f"--app=http://127.0.0.1:{server.server_port}/"]
            if args.enable_wayland_ime:
                flags += ["--enable-wayland-ime", "--wayland-text-input-version=3"]
            report["explicit_ime_flags"] = args.enable_wayland_ime
            chrome = subprocess.Popen([browser, *flags], env=browser_environment, stdout=chrome_log, stderr=subprocess.STDOUT, start_new_session=True)
            cleanup.callback(stop, chrome)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                windows = [item for item in hypr_json("clients") if item.get("pid") == chrome.pid and item.get("mapped")]
                if windows:
                    window = windows[0]
                    if window.get("xwayland"):
                        raise RuntimeError("Chromium is not using native Wayland")
                    window_pid = chrome.pid
                    focus_window(window["address"], True)
                    break
                if chrome.poll() is not None:
                    raise RuntimeError("Chromium could not open a private Wayland window")
                time.sleep(0.05)
            else:
                raise RuntimeError("Chromium did not map its own window")
            wait(lambda state: state.get("focused") and state.get("field") == "normal", "Chromium did not focus its textarea")
            key("text", "nihao")
            check("native composition", lambda s: s.get("composing") and s.get("preedit") == "你好", "ASCII keys did not enter a native Chromium composition")
            command(["grim", "-o", output_name, output / "chromium-preedit.png"])
            key("key", "space")
            check("Chinese commit", lambda s: not s.get("composing") and s.get("value") == "你好", "Chromium did not commit Chinese exactly once")
            key("key", "Return")
            check("application newline", lambda s: s.get("value") == "你好\n", "Chromium did not receive a normal newline after the commit")
            command(["grim", "-o", output_name, output / "chromium-committed.png"])
            clear()
            key("text", "ta")
            key("key", "Tab")
            key("key", "space")
            check("nonfirst candidate", lambda s: not s.get("composing") and s.get("value") == candidates[1], "Chromium did not receive the selected second candidate")
            clear()
            key("shortcut", "ctrl", "space")
            key("text", "hello")
            key("key", "space")
            check("English mode", lambda s: not s.get("composing") and s.get("value") == "hello ", "English mode did not preserve normal browser typing")
            clear()
            key("shortcut", "ctrl", "space")
            key("text", "nihao")
            wait(lambda s: s.get("composing"), "composition did not resume")
            key("key", "Escape")
            check("cancel", lambda s: not s.get("composing") and s.get("value") == "", "cancelled composition changed browser text")
            key("key", "Tab")
            state = wait(lambda s: s.get("field") == "password", "password field did not receive focus")
            previous = state["starts"]
            key("text", "nihao")
            check("password passthrough", lambda s: s.get("field") == "password" and s.get("value") == "nihao" and not s.get("composing") and s.get("starts") == previous, "password input unexpectedly entered composition")
            report["passed"] = True
    except KeyboardInterrupt:
        report["error"] = "acceptance interrupted"
    except Exception as error:
        report["error"] = str(error)
    finally:
        if keyboard is not None:
            keyboard.stop()
        stop(chrome)
        stop(ime)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        report["owned_processes_stopped"] = all(process is None or process.poll() is not None for process in (chrome, ime))
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(main())
