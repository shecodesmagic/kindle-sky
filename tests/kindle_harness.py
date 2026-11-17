"""A fake Kindle for running kindle/kindle-sky/skyd.sh on Linux.

- Kindle-only commands (stop, start, lipc-set-prop, lipc-wait-event, fbink,
  eips) and curl are stubs first in PATH; each call is appended to calls.log.
- The touchscreen is a FIFO; tests write real input_event records into it.
- Time runs SPEED times faster: `date +%s` and `sleep` are stubs on a shared
  scaled clock, so a 300 s refresh slot takes 300/SPEED real seconds.
- shell="busybox" runs the script with busybox ash and puts busybox's own
  od/dd/awk/sed/... first in PATH, which is closer to the Kindle's userland.
- The "web server" is a directory: https://example.test/sky/<path> -> server/<path>.
"""
import io
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get("KSKY_SCRIPT", ROOT / "kindle" / "kindle-sky" / "skyd.sh"))
BASE = "https://example.test/sky"
CLOCK0 = 1_800_000_100            # fake epoch at start; slot 1_800_000_000 (divisible by 300)
SLOT0 = CLOCK0 // 300 * 300
SPEED = 100
REAL = {name: shutil.which(name) for name in ("sleep", "date", "awk", "cat")}
BUSYBOX = shutil.which("busybox")
BUSYBOX_APPLETS = ["sh", "od", "dd", "awk", "sed", "cut", "tr", "grep", "wc", "tail", "head", "cat",
                   "mv", "rm", "mkdir", "rmdir", "setsid"]

LOG_STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$T/calls.log"\n'

STUBS = {
    "lipc-set-prop": LOG_STUB,
    "eips": LOG_STUB,
    "start": LOG_STUB,
    # Upstart sends TERM while stopping the UI; emulate the worst case (it reaches us).
    "stop": LOG_STUB + 'kill -TERM "$PPID" 2>/dev/null\nexit 0\n',
    "fbink": LOG_STUB + """for a in "$@"; do
  case "$a" in file=*)
    id=$(grep -a -o 'kskyid:[a-z0-9-]*;' "${a#file=}" | head -n 1); id=${id%;}
    echo "drew ${id#kskyid:}" >> "$T/calls.log" ;;
  esac
done
""",
    "lipc-wait-event": LOG_STUB + """if [ -f "$T/no_power_events" ]; then
  echo "lipc-wait-event: unknown publisher"; exit 1
fi
while [ ! -f "$T/power" ]; do "$REAL_SLEEP" 0.05; done
echo "goingToScreenSaver"
""",
    "date": """#!/bin/sh
real=$("$REAL_DATE" +%s%N)
now=$(( CLOCK0 + (real - START_NS) * SPEED / 1000000000 ))
case "$1" in +%s) echo "$now" ;; *) echo "D$now" ;; esac
""",
    "sleep": """#!/bin/sh
exec "$REAL_SLEEP" "$("$REAL_AWK" -v s="$1" -v k="$SPEED" 'BEGIN { printf "%.4f", s / k }')"
""",
}

CURL_STUB = f"""#!{sys.executable}
import os, shutil, sys, time
T = os.environ["T"]
args = sys.argv[1:]
now = int(os.environ["CLOCK0"]) + (time.time_ns() - int(os.environ["START_NS"])) * int(os.environ["SPEED"]) // 10**9
with open(os.path.join(T, "calls.log"), "a") as fh:
    fh.write("curl@%d %s\\n" % (now, " ".join(args)))
mode = open(os.path.join(T, "curl_mode")).read().strip()
url = [a for a in args if a.startswith(("http://", "https://"))][-1]
out = args[args.index("-o") + 1]
if "--proto" in args and args[args.index("--proto") + 1] == "=https" and not url.startswith("https://"):
    sys.exit(1)
if mode.startswith("fail:"):
    sys.exit(int(mode[5:]))
base = os.environ["KSKY_BASE"]
path = os.path.join(T, "server", url[len(base):].lstrip("/"))
if not url.startswith(base) or not os.path.isfile(path):
    sys.exit(22)
if "--max-filesize" in args and mode != "ignore_maxsize":
    if os.path.getsize(path) > int(args[args.index("--max-filesize") + 1]):
        sys.exit(63)
shutil.copyfile(path, out)
"""


def png_bytes(ident, size=(1072, 1448), mode="L", interlace=False, bits16=False, pad=0):
    """A real PNG, tagged so the fbink stub can tell which image was drawn."""
    img = Image.new(mode if not bits16 else "I;16", size, 0)
    noise = Image.frombytes("L", (size[0], 40), os.urandom(size[0] * 40))
    img.paste(noise if mode == "L" and not bits16 else noise.convert(img.mode), (0, 0))
    info = PngImagePlugin.PngInfo()
    info.add_text("id", f"kskyid:{ident};")
    if pad:
        info.add_text("pad", "x" * pad)
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info, interlace=1 if interlace else 0)
    data = buf.getvalue()
    if interlace:   # Pillow ignores the interlace flag when saving; set it in IHDR like a real interlaced PNG
        import zlib
        ihdr = bytearray(data[16:29])
        ihdr[12] = 1
        crc = struct.pack(">I", zlib.crc32(b"IHDR" + bytes(ihdr)) & 0xFFFFFFFF)
        data = data[:16] + bytes(ihdr) + crc + data[33:]
    return data


def event(sec, typ, code, value, size=16):
    if size == 16:
        return struct.pack("<IIHHI", sec, 0, typ, code, value & 0xFFFFFFFF)
    return struct.pack("<QQHHI", sec, 0, typ, code, value & 0xFFFFFFFF)


def tap_bytes(x, y, sec=1000, size=16, tracking=True, btn=True):
    ev = []
    if tracking:
        ev.append(event(sec, 3, 57, 7, size))
    ev += [event(sec, 3, 53, x, size), event(sec, 3, 54, y, size)]
    if btn:
        ev.append(event(sec, 1, 330, 1, size))
    ev.append(event(sec, 0, 0, 0, size))
    if tracking:
        ev.append(event(sec, 3, 57, -1, size))
    if btn:
        ev.append(event(sec, 1, 330, 0, size))
    ev.append(event(sec, 0, 0, 0, size))
    return b"".join(ev)


class FakeKindle:
    def __init__(self, tmp: Path, shell="dash", fbink=True, config=None):
        self.t = tmp
        self.shell = shell
        tmp.mkdir(parents=True, exist_ok=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.sky = tmp / "sky"
        self.sky.mkdir()
        self.state = tmp / "state"
        self.server = tmp / "server" / "frames"
        self.server.mkdir(parents=True)
        self.calls_file = tmp / "calls.log"
        self.calls_file.write_text("")
        self.log = self.sky / "kindle-sky.log"
        self.touch = tmp / "touch"
        os.mkfifo(self.touch)
        for name, body in STUBS.items():
            self._stub(name, body)
        self._stub("curl", CURL_STUB)
        self.fbink = tmp / "fbin" / "fbink"
        if fbink:
            self.fbink.parent.mkdir()
            self.fbink.write_text(STUBS["fbink"])
            self.fbink.chmod(0o755)
        path = [str(self.bin)]
        if shell == "busybox":
            bb = tmp / "bb"
            bb.mkdir()
            for applet in BUSYBOX_APPLETS:
                (bb / applet).symlink_to(BUSYBOX)
            path.append(str(bb))
        path.append(os.environ["PATH"])
        self.set_curl("ok")
        self.cfg = {
            "BASE_URL": f'"{BASE}"', "TOUCH_DEV": str(self.touch), "LAUNCH_SETTLE": "0", "UI_SETTLE": "0",
            "FBINK_CANDIDATES": f'"{self.fbink}"', "RETRY": "60", "MAX_RUNTIME": "0",
        }
        self.cfg.update(config or {})
        self.write_config()
        self.script = SCRIPT
        self.env = dict(os.environ, T=str(tmp), PATH=":".join(path), KSKY_DIR=str(self.sky),
                        KSKY_STATE=str(self.state), KSKY_LOG=str(self.log), KSKY_BASE=BASE,
                        CLOCK0=str(CLOCK0), START_NS=str(time.time_ns()), SPEED=str(SPEED),
                        KSKY_SLEEP=str(self.bin / "sleep"), REAL_SLEEP=REAL["sleep"],
                        REAL_DATE=REAL["date"], REAL_AWK=REAL["awk"])
        self.started_pids = []

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def write_config(self):
        (self.sky / "config.sh").write_text("".join(f"{k}={v}\n" for k, v in self.cfg.items()))

    def set_curl(self, mode):
        (self.t / "curl_mode").write_text(mode)

    # ------------------------------------------------------------ fake world
    def now(self):
        return CLOCK0 + (time.time_ns() - int(self.env["START_NS"])) * SPEED // 10**9

    def publish(self, view, slot, data=None):
        (self.server / f"{view}-{slot}.png").write_bytes(data if data is not None else png_bytes(f"{view}-{slot}"))

    def publish_range(self, first=SLOT0, n=6):
        for k in range(n):
            for view in ("south", "overhead"):
                self.publish(view, first + 300 * k)

    def tap(self, x, y, sec=None, size=16, **kw):
        if sec is None:
            sec = self.now()
        data = tap_bytes(x, y, sec, size, **kw)
        end = time.time() + 10
        while True:
            try:
                fd = os.open(self.touch, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:          # no reader yet
                if time.time() > end:
                    raise AssertionError("nobody is reading the touchscreen") from None
                time.sleep(0.01)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        time.sleep(0.15)             # let dd return before the next tap opens the FIFO

    # -------------------------------------------------------------- running
    def sh(self):
        return ["busybox", "sh"] if self.shell == "busybox" else ["dash"]

    def run(self, *args, timeout=20):
        res = subprocess.run([*self.sh(), str(self.script), *args], env=self.env, cwd=self.t,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                             check=False)
        return res

    def launch(self):
        before = self.pid()
        res = self.run("launch")
        pid = self.wait_pid(exclude=before)
        if pid:
            self.started_pids.append(pid)
        return res

    def pid(self):
        try:
            return int((self.state / "skyd.pid").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def wait_pid(self, timeout=5, exclude=None):
        end = time.time() + timeout
        while time.time() < end:
            p = self.pid()
            if p and p != exclude and alive(p):
                return p
            time.sleep(0.02)
        return None

    def calls(self):
        return self.calls_file.read_text().splitlines()

    def log_text(self):
        return self.log.read_text() if self.log.exists() else ""

    def wait_for(self, pred, timeout=15, what="condition"):
        end = time.time() + timeout
        while time.time() < end:
            calls = self.calls()
            if pred(calls):
                return calls
            time.sleep(0.03)
        raise AssertionError(f"timeout waiting for {what}; calls:\n" + "\n".join(self.calls()[-40:])
                             + "\nlog:\n" + self.log_text()[-3000:])

    def wait_log(self, text, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if text in self.log_text():
                return
            time.sleep(0.03)
        raise AssertionError(f"timeout waiting for log {text!r}; log:\n" + self.log_text()[-3000:])

    def helpers(self, pid):
        """PIDs whose parent is `pid` and that run skyd.sh (the helper subshells)."""
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text()
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            if ppid == pid:
                out.append(int(d.name))
        return out

    def leftovers(self):
        """Live processes whose command line mentions this fake Kindle's directory."""
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit() or int(d.name) == os.getpid():
                continue
            try:
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if str(self.t) in cmd and alive(int(d.name)):
                out.append((int(d.name), cmd))
        return out

    def teardown(self):
        for pid in self.started_pids:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def alive(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def wait_dead(pid, timeout=10):
    end = time.time() + timeout
    while alive(pid) and time.time() < end:
        time.sleep(0.03)
    return not alive(pid)


def drawn(calls):
    return [c.split(" ", 1)[1] for c in calls if c.startswith("drew ")]


def curl_urls(calls):
    out = []
    for c in calls:
        m = re.match(r"curl@(\d+) .* (https?://\S+)$", c)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out
