"""The Kindle script and the renderer share a few numbers; make sure they agree."""
import re
import struct

from kindle_harness import ROOT

from kindlesky import config

SCRIPT = (ROOT / "kindle" / "kindle-sky" / "skyd.sh").read_text()


def shell_var(name):
    m = re.search(rf"^{name}=\"?([^\"\n]*)\"?$", SCRIPT, re.M)
    assert m, name
    return m.group(1)


def test_shared_numbers():
    assert int(shell_var("W")) == config.W
    assert int(shell_var("H")) == config.H
    assert int(shell_var("STEP")) == config.STEP_MIN * 60
    assert int(shell_var("FOOTER_Y")) == config.FOOTER_Y
    assert int(shell_var("EXIT_X")) == config.EXIT_X
    assert int(shell_var("MAX_BYTES")) == config.MAX_PNG_BYTES


def test_png_header_constant():
    want = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">IIBBBBB", config.W, config.H, 8, 0, 0, 0, 0))
    assert shell_var("PNG_HEADER") == " ".join(f"{b:03o}" for b in want)


def test_default_url_matches_config_file():
    cfg = (ROOT / "kindle" / "kindle-sky" / "config.sh").read_text()
    assert shell_var("BASE_URL") in cfg
    assert shell_var("BASE_URL").startswith("https://")
