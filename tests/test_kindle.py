"""Tests for the Kindle side (kindle/kindle-sky/skyd.sh) on a fake Kindle.

See kindle_harness.py for what is faked. Every test runs under dash and,
when installed, under busybox ash with busybox's own od/dd/awk.
"""
import itertools
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from kindle_harness import (
    BASE,
    BUSYBOX,
    CLOCK0,
    ROOT,
    SLOT0,
    SPEED,
    FakeKindle,
    alive,
    curl_urls,
    drawn,
    png_bytes,
    wait_dead,
)

SHELLS = ["dash"] + (["busybox"] if BUSYBOX else [])
EXIT_TAP = (900, 1400)      # inside the EXIT box (x >= 760, y >= 1328)
SKY_TAP = (500, 600)


@pytest.fixture(params=SHELLS)
def shell(request):
    return request.param


@pytest.fixture
def kindle(tmp_path, shell):
    made = []

    def make(**kw):
        k = FakeKindle(tmp_path / f"k{len(made)}", shell=shell, **kw)
        made.append(k)
        return k
    (tmp_path).mkdir(exist_ok=True)
    yield make
    for k in made:
        k.teardown()


def started(kindle, **kw):
    k = kindle(**kw)
    k.publish_range()
    res = k.launch()
    assert res.returncode == 0, res.stderr
    k.wait_for(lambda c: f"drew south-{SLOT0}" in c, what="first sky drawn")
    return k


def ui_restored(calls):
    return "start lab126_gui" in calls


# ------------------------------------------------------------------- start

def test_start_stops_ui_downloads_and_draws(kindle):
    k = kindle()
    k.publish_range()
    t0 = time.time()
    res = k.launch()
    assert res.returncode == 0
    assert time.time() - t0 < 3, "the scriptlet must return quickly"
    pid = k.pid()
    assert pid and alive(pid)
    calls = k.wait_for(lambda c: len(curl_urls(c)) >= 2, what="downloads")
    order = [c.split()[0] for c in calls]
    assert calls[0] == "lipc-set-prop com.lab126.powerd preventScreenSaver 1"
    assert calls[1] == "stop lab126_gui"
    assert order.index("stop") < order.index("drew")
    urls = [u for _, u in curl_urls(calls)]
    assert urls[:2] == [f"{BASE}/frames/south-{SLOT0}.png", f"{BASE}/frames/overhead-{SLOT0}.png"]
    assert drawn(calls)[0] == f"south-{SLOT0}"        # the downloaded current view is shown
    assert alive(pid), "must survive the TERM that stopping the UI sends"


def test_curl_is_https_only_with_verification(kindle):
    k = started(kindle)
    for c in k.calls():
        if c.startswith("curl@"):
            args = c.split()
            assert "--proto" in args and args[args.index("--proto") + 1] == "=https"
            assert "--fail" in args and "--max-filesize" in args
            assert not {"-k", "--insecure", "-L", "--location"} & set(args)
            assert args[-1].startswith("https://")


def test_ca_bundle_option(kindle):
    k = started(kindle, config={"CA_BUNDLE": "/mnt/us/kindle-sky/cacert.pem"})
    args = next(c for c in k.calls() if c.startswith("curl@")).split()
    assert args[args.index("--cacert") + 1] == "/mnt/us/kindle-sky/cacert.pem"


def test_cached_image_is_shown_before_downloading(kindle):
    k = kindle()
    k.state.mkdir()
    (k.state / "south.png").write_bytes(png_bytes("cached"))
    k.publish_range()
    k.launch()
    calls = k.wait_for(lambda c: len(drawn(c)) >= 2, what="two draws")
    assert drawn(calls)[0] == "cached"
    first_curl = next(i for i, c in enumerate(calls) if c.startswith("curl@"))
    assert calls.index("drew cached") < first_curl


def test_eips_used_when_no_fbink(kindle):
    k = kindle(fbink=False)
    k.publish_range()
    k.launch()
    calls = k.wait_for(lambda c: any(x.startswith("eips -f -g") for x in c), what="eips draw")
    assert f"eips -f -g {k.state}/south.png" in calls
    assert "drawing with eips" in k.log_text()


def test_launch_while_running_stops_it(kindle):
    k = started(kindle)
    pid = k.pid()
    k.run("launch")
    assert wait_dead(pid)
    k.wait_for(ui_restored, what="UI restarted")


# -------------------------------------------------------------- preflight

@pytest.mark.parametrize("problem", ["no_touch", "http_url"])
def test_preflight_failure_never_stops_the_ui(kindle, problem):
    k = kindle()
    if problem == "no_touch":
        k.touch.unlink()
    else:
        k.cfg["BASE_URL"] = '"http://example.test/sky"'
        k.write_config()
    res = k.run("launch")
    assert res.returncode == 1
    time.sleep(0.5)
    calls = k.calls()
    assert not any(c.startswith(("stop ", "curl")) for c in calls)
    assert any(c.startswith("fbink") and "cannot start" in c for c in calls)
    assert k.pid() is None
    assert ("not readable" if problem == "no_touch" else "must start with https://") in k.log_text()


# ------------------------------------------------------------- touch / exit

def assert_clean_exit(k, pid):
    assert wait_dead(pid), "sky mode should have exited"
    calls = k.wait_for(ui_restored, what="UI restarted")
    i = calls.index("start lab126_gui")
    assert "lipc-set-prop com.lab126.powerd preventScreenSaver 0" in calls[i:]
    assert not any(c.startswith(("drew", "fbink", "eips")) for c in calls[i:]), "nothing may draw over the UI"
    assert not (k.state / "skyd.pid").exists()
    time.sleep(0.3)
    assert k.helpers(pid) == []
    assert k.leftovers() == [], "every process sky mode started must be gone"


def test_exit_box_tap_restores_the_kindle(kindle):
    k = started(kindle)
    pid = k.pid()
    helpers = k.helpers(pid)
    assert len(helpers) >= 2
    k.tap(*EXIT_TAP)
    assert_clean_exit(k, pid)
    assert all(wait_dead(h) for h in helpers)
    assert "EXIT tapped" in k.log_text()


@pytest.mark.parametrize("xy", [SKY_TAP, (100, 1400), (759, 1440), (1000, 1327)])
def test_tap_outside_exit_box_switches_view(kindle, xy):
    k = started(kindle)
    k.tap(*xy)
    k.wait_for(lambda c: drawn(c)[-1:] == [f"overhead-{SLOT0}"], what="overhead drawn")
    assert (k.state / "view").read_text().strip() == "overhead"
    assert alive(k.pid())
    k.tap(*xy, sec=CLOCK0 + 100)
    k.wait_for(lambda c: drawn(c)[-1:] == [f"south-{SLOT0}"], what="back to south")
    assert (k.state / "view").read_text().strip() == "south"


def test_switched_view_is_kept_on_refresh(kindle):
    k = started(kindle)
    k.tap(*SKY_TAP)
    calls = k.wait_for(lambda c: f"drew overhead-{SLOT0 + 300}" in c
                       and any(u.endswith(f"south-{SLOT0 + 300}.png") for _, u in curl_urls(c)),
                       timeout=20, what="next slot in overhead")
    urls = [u for _, u in curl_urls(calls)]
    i = urls.index(f"{BASE}/frames/overhead-{SLOT0 + 300}.png")
    assert urls[i + 1] == f"{BASE}/frames/south-{SLOT0 + 300}.png"   # current view first
    assert f"drew south-{SLOT0 + 300}" not in calls


@pytest.mark.parametrize("tracking,btn", [(True, False), (False, True)])
def test_finger_up_detected_either_way(kindle, tracking, btn):
    k = started(kindle)
    k.tap(*SKY_TAP, tracking=tracking, btn=btn)
    k.wait_for(lambda c: drawn(c)[-1:] == [f"overhead-{SLOT0}"], what="switch")


def test_triple_tap_anywhere_exits(kindle):
    k = started(kindle)
    pid = k.pid()
    s = k.now()
    for d in (0, 1, 2):
        k.tap(*SKY_TAP, sec=s + d)
    assert_clean_exit(k, pid)
    assert "triple tap" in k.log_text()


def test_slow_taps_do_not_exit(kindle):
    k = started(kindle)
    s = k.now()
    for d in (0, 3, 6, 9):
        k.tap(*SKY_TAP, sec=s + d)
    k.wait_for(lambda c: len([x for x in drawn(c) if x.startswith(("south", "overhead"))]) >= 5, what="4 switches")
    assert alive(k.pid())


def test_power_button_exits(kindle):
    k = started(kindle)
    pid = k.pid()
    (k.t / "power").write_text("")
    assert_clean_exit(k, pid)
    assert "power button" in k.log_text()


def test_missing_power_events_do_not_exit(kindle):
    k = kindle()
    (k.t / "no_power_events").write_text("")
    k.publish_range()
    k.launch()
    k.wait_log("power button watch unavailable")
    time.sleep(60 / SPEED + 0.5)        # a few main-loop checks
    assert alive(k.pid())


@pytest.mark.parametrize("cfg,raw,screen", [
    ({}, (500, 600), (500, 600)),
    ({"TOUCH_SWAP_XY": "1"}, (600, 500), (500, 600)),
    ({"TOUCH_MIRROR_X": "1", "TOUCH_MIRROR_Y": "1"}, (500, 600), (571, 847)),
    ({"TOUCH_MAX_X": "4096", "TOUCH_MAX_Y": "4096"}, (2048, 4000), (536, 1414)),
])
def test_touch_calibration(kindle, cfg, raw, screen):
    k = started(kindle, config=cfg)
    k.tap(*raw)
    k.wait_log(f"tap raw=({raw[0]},{raw[1]}) screen=({screen[0]},{screen[1]})")


def test_64bit_input_events(kindle):
    k = started(kindle, config={"EVENT_SIZE": "24"})
    pid = k.pid()
    k.tap(*EXIT_TAP, size=24)
    assert_clean_exit(k, pid)


def test_unreadable_touchscreen_gives_kindle_back(kindle):
    k = kindle()
    k.publish_range()
    k.touch.unlink()
    k.touch.write_text("")          # readable, but never any data
    k.launch()
    pid = k.pid()
    assert_clean_exit(k, pid)
    assert "gives no data" in k.log_text()


# --------------------------------------------------------- downloads

def test_refresh_schedule_follows_slots(kindle):
    k = started(kindle)
    calls = k.wait_for(lambda c: f"drew south-{SLOT0 + 600}" in c, timeout=25, what="two more slots")
    south = [(t, u) for t, u in curl_urls(calls) if "/south-" in u]
    slots = [int(re.search(r"south-(\d+)", u).group(1)) for _, u in south]
    assert slots[:3] == [SLOT0, SLOT0 + 300, SLOT0 + 600]
    for (t, _), slot in zip(south[1:3], slots[1:3]):
        assert slot <= t <= slot + 30, "each frame is fetched just after its slot starts"
    # no extra downloads in between: exactly one per view per slot
    assert len([u for _, u in curl_urls(calls) if f"-{SLOT0 + 300}." in u]) == 2


def test_download_failure_keeps_last_image_and_retries(kindle):
    k = kindle()
    k.state.mkdir()
    (k.state / "south.png").write_bytes(png_bytes("old"))
    k.set_curl("fail:7")
    k.publish_range()
    k.launch()
    calls = k.wait_for(lambda c: len([u for _, u in curl_urls(c) if "south" in u]) >= 3, timeout=20,
                       what="retries")
    times = [t for t, u in curl_urls(calls) if "south" in u]
    # retried within RETRY (60 s, or sooner at a slot boundary): never hammering, never a full slot later
    # (bounds are loose: at 100x fake speed, process start-up latency shows up as tens of "seconds")
    assert all(15 <= b - a <= 150 for a, b in itertools.pairwise(times)), f"retry spacing: {times}"
    assert drawn(calls) == ["old"]
    assert b"kskyid:old;" in (k.state / "south.png").read_bytes()
    assert "download failed: south (curl exit 7)" in k.log_text()
    k.set_curl("ok")
    k.wait_for(lambda c: any(d.startswith("south-") for d in drawn(c)), timeout=20, what="recovered")


def test_missing_frame_404_keeps_last_image(kindle):
    k = kindle()
    k.state.mkdir()
    (k.state / "south.png").write_bytes(png_bytes("old"))
    k.launch()                       # nothing published
    k.wait_log("curl exit 22")
    time.sleep(0.3)
    assert drawn(k.calls()) == ["old"]


BAD = {
    "html": lambda: b"<html>" + b"x" * 5000,
    "rgb": lambda: png_bytes("bad", mode="RGB"),
    "wrong_size": lambda: png_bytes("bad", size=(1448, 1072)),
    "16_bit": lambda: png_bytes("bad", bits16=True),
    "interlaced": lambda: png_bytes("bad", interlace=True),
    "too_big": lambda: png_bytes("bad", pad=2_100_000),
    "truncated": lambda: png_bytes("bad")[:600],
    "empty": lambda: b"",
}


@pytest.mark.parametrize("kind", list(BAD))
def test_bad_downloads_are_rejected(kindle, kind):
    k = kindle(config={"RETRY": "6000"})
    k.state.mkdir()
    (k.state / "south.png").write_bytes(png_bytes("old"))
    if kind == "too_big":
        k.set_curl("ignore_maxsize")      # e.g. chunked reply, so curl can't stop it early
    k.publish("south", SLOT0, BAD[kind]())
    k.publish("overhead", SLOT0)
    k.launch()
    k.wait_for(lambda c: any("overhead" in u for _, u in curl_urls(c)), what="both downloads")
    time.sleep(0.3)
    assert "rejected download: south" in k.log_text()
    assert drawn(k.calls()) == ["old"], "a rejected file must never be drawn"
    assert b"kskyid:old;" in (k.state / "south.png").read_bytes()
    assert not (k.state / "south.part").exists()


def test_curl_max_filesize_failure_is_handled(kindle):
    k = kindle()
    k.publish("south", SLOT0, png_bytes("big", pad=2_100_000))
    k.launch()
    k.wait_log("curl exit 63")


# ------------------------------------------------------- process safety

def test_only_processes_it_started_are_signalled(kindle):
    bystander = subprocess.Popen([shutil.which("sleep"), "300"])
    try:
        k = started(kindle)
        pid = k.pid()
        k.tap(*EXIT_TAP)
        assert wait_dead(pid)
        assert alive(bystander.pid)
    finally:
        bystander.kill()
        bystander.wait()


@pytest.mark.parametrize("content", ["{pid} {start}", "{pid} 1", "{pid}", "garbage", ""])
def test_pidfile_pointing_elsewhere_is_never_killed(kindle, content):
    victim = subprocess.Popen([shutil.which("sleep"), "300"])
    try:
        k = kindle()
        k.state.mkdir()
        start = Path(f"/proc/{victim.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        (k.state / "skyd.pid").write_text(content.format(pid=victim.pid, start=start))
        assert k.run("stop").stdout.strip() == "not running"
        assert k.run("status").stdout.strip() == "not running"
        k.publish_range()
        k.launch()
        assert k.pid() != victim.pid and alive(k.pid())
        assert alive(victim.pid)
    finally:
        victim.kill()
        victim.wait()


def test_stop_and_status_commands(kindle):
    k = started(kindle)
    pid = k.pid()
    assert k.run("status").stdout.strip() == f"running (pid {pid})"
    assert k.run("stop").stdout.strip() == "stopped"
    assert_clean_exit(k, pid)


def test_crashed_helper_gives_kindle_back(kindle):
    k = started(kindle)
    pid = k.pid()
    helpers = k.helpers(pid)
    os.kill(helpers[0], 9)
    assert_clean_exit(k, pid)
    assert "stopped unexpectedly" in k.log_text()


def test_max_runtime(kindle):
    k = started(kindle, config={"MAX_RUNTIME": "100"})
    pid = k.pid()
    assert_clean_exit(k, pid)
    assert "MAX_RUNTIME reached" in k.log_text()


def test_usage(kindle):
    res = kindle().run("bogus")
    assert res.returncode == 2 and "usage" in res.stderr


def test_log_is_trimmed(kindle):
    k = kindle()
    k.log.write_text("".join(f"old line {i}\n" for i in range(30000)))
    k.publish_range()
    k.launch()
    text = k.log_text()
    assert len(text) < 60000
    assert "old line 29999" in text and "old line 0\n" not in text


# ------------------------------------------------------------ scriptlet

def test_scriptlet_header_and_command():
    text = (ROOT / "kindle" / "documents" / "Sky Mode.sh").read_text()
    lines = text.splitlines()
    assert lines[0] == "#!/bin/sh"
    assert "# Name: Sky Mode" in lines[:5]
    assert "# DontUseFBInk" in lines[:5]
    assert lines[-1] == "exec sh /mnt/us/kindle-sky/skyd.sh launch"


# ------------------------------------------------------- exact boundaries

def test_exit_box_corner_is_inside(kindle):
    k = started(kindle)
    pid = k.pid()
    k.tap(760, 1328)                 # top-left pixel of the EXIT box
    assert_clean_exit(k, pid)


def sized_png(ident, size):
    """A valid frame PNG padded to exactly `size` bytes."""
    base = png_bytes(ident)
    data = base + b"\0" * (size - len(base))     # trailing bytes after IEND
    assert len(data) == size
    return data


@pytest.mark.parametrize("size,ok", [(1000, True), (999, False), (2_000_000, True), (2_000_001, False)])
def test_size_limits_are_exact(kindle, size, ok):
    k = kindle(config={"RETRY": "6000"})
    k.set_curl("ignore_maxsize")
    data = sized_png("edge", size) if size > 1100 else png_bytes("edge")
    if size <= 1100:
        # a small valid header followed by filler: only the size rule decides
        data = (data[:33] + b"\0" * size)[:size]
    k.publish("south", SLOT0, data)
    k.publish("overhead", SLOT0)
    k.launch()
    k.wait_for(lambda c: any("overhead" in u for _, u in curl_urls(c)), what="both downloads")
    time.sleep(0.3)
    assert ("rejected download: south" in k.log_text()) is (not ok)
    assert (k.state / "south.png").exists() is ok


def test_decoy_with_matching_command_line_but_wrong_start_time_survives(kindle):
    """Only the start-time check stops this one: its command line looks like ours."""
    decoy = subprocess.Popen(["sh", "-c", "sleep 300", "skyd.sh", "run"])
    try:
        k = kindle()
        k.state.mkdir()
        (k.state / "skyd.pid").write_text(f"{decoy.pid} 1")
        assert k.run("stop").stdout.strip() == "not running"
        assert alive(decoy.pid)
    finally:
        decoy.kill()
        decoy.wait()
