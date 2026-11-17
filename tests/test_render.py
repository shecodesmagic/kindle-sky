"""Tests for the renderer: projections, catalogue, astronomy, drawing, site output."""
import itertools
import json
import random
import struct
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from PIL import Image

from kindlesky import astro, catalog, config, draw, projection, render, validate

T0 = datetime.fromisoformat("2026-11-20T21:00-05:00")   # Saturn and the Moon in the south


@pytest.fixture(scope="module")
def sky0():
    return astro.compute(T0)


@pytest.fixture(scope="module")
def charts(sky0):
    out = {}
    for view in draw.VIEWS:
        c = draw.Chart(view, sky0, T0)
        out[view] = (c, c.render())
    return out


# ------------------------------------------------------------ projection

def test_south_view_geometry():
    v = projection.SouthView(500.0, 1000.0, 400.0)
    x, y = v.project(0.0, 180.0)
    assert (x, y) == pytest.approx((500.0, 1000.0))                 # south point on the horizon
    _, y = v.project(np.zeros(5), np.array([120.0, 150.0, 180.0, 210.0, 240.0]))
    assert y == pytest.approx(np.full(5, 1000.0))                   # horizon is a straight line
    x, y = v.project(90.0, 0.0)
    assert (x, y) == pytest.approx((500.0, 200.0))                  # zenith 2*scale above
    assert v.project(10.0, 240.0)[0] > 500 > v.project(10.0, 120.0)[0]   # west right, east left
    x, _ = v.project(0.0, 180 + 53.13)
    assert x == pytest.approx(500 + 2 * 400 * np.tan(np.radians(53.13 / 2)))


def test_overhead_view_geometry():
    v = projection.OverheadView(536.0, 600.0, 450.0)
    assert v.project(90.0, 123.0) == pytest.approx((536.0, 600.0))
    assert v.project(0.0, 0.0) == pytest.approx((536.0, 150.0))     # north up, on the circle
    assert v.project(0.0, 90.0) == pytest.approx((86.0, 600.0))     # east LEFT
    assert v.project(0.0, 270.0) == pytest.approx((986.0, 600.0))   # west right
    assert v.project(0.0, 180.0) == pytest.approx((536.0, 1050.0))


@pytest.mark.parametrize("view", [projection.SouthView(536.0, 1196.0, 524.0),
                                  projection.OverheadView(536.0, 632.0, 450.0)])
def test_projection_round_trip(view):
    rng = np.random.default_rng(1)
    alt = rng.uniform(0, 89.5, 500)
    az = rng.uniform(0, 360, 500)
    if isinstance(view, projection.SouthView):
        az = rng.uniform(100, 260, 500)
    alt2, az2 = view.unproject(*view.project(alt, az))
    assert alt2 == pytest.approx(alt, abs=1e-6)
    assert ((az2 - az + 180) % 360 - 180) == pytest.approx(np.zeros(500), abs=1e-6)


def test_enu_round_trip():
    alt, az = projection.enu_to_altaz(*projection.altaz_to_enu(33.0, 211.0))
    assert (float(alt), float(az)) == pytest.approx((33.0, 211.0))


# --------------------------------------------------------------- catalogue

def test_catalogue_contents():
    s = catalog.stars()
    assert len(s.hip) == len(set(s.hip.tolist())) > 2500
    assert s.mag.max() <= 6.0 and (s.mag <= 5.5).sum() > 2700
    assert s.ra_deg.min() >= 0 and s.ra_deg.max() < 360
    idx = s.index()
    polaris = idx[11767]
    assert s.ra_deg[polaris] == pytest.approx(37.95, abs=0.1) and s.dec_deg[polaris] == pytest.approx(89.26, abs=0.05)
    assert catalog.star_names()[11767] == "Polaris"
    assert len(catalog.constellations()) >= 88
    assert {"Ori", "UMa", "Cas", "Sco"} <= set(catalog.lines())


def test_every_line_vertex_is_a_catalogue_star():
    idx = catalog.stars().index()
    for abbr, polys in catalog.lines().items():
        for poly in polys:
            assert len(poly) >= 2, abbr
            for h in poly:
                assert h in idx, f"{abbr}: HIP {h} missing"


# ---------------------------------------------------------------- astronomy

def test_compute_requires_timezone():
    with pytest.raises(ValueError):
        astro.compute(datetime(2026, 11, 20, 21))


# Reference values from astropy 7 (independent ERFA implementation, same
# atmosphere settings) for T0 at 45.4 N, 75.7 W. See docs/VERIFY.md.
REFERENCE = {"Polaris": (45.94, 0.48), "Fomalhaut": (10.51, 205.93), "Vega": (23.56, 302.14)}
REF_BODIES = {"Saturn": (45.20, 188.16), "Moon": (51.52, 188.84)}


def sep(a1, z1, a2, z2):
    a1, z1, a2, z2 = map(np.radians, (a1, z1, a2, z2))
    return np.degrees(np.arccos(np.clip(np.sin(a1) * np.sin(a2) + np.cos(a1) * np.cos(a2) * np.cos(z1 - z2), -1, 1)))


@pytest.mark.parametrize("name", list(REFERENCE))
def test_star_positions_match_reference(sky0, name):
    hip = {"Polaris": 11767, "Fomalhaut": 113368, "Vega": 91262}[name]
    i = catalog.stars().index()[hip]
    assert sep(sky0.star_alt[i], sky0.star_az[i], *REFERENCE[name]) < 0.05


@pytest.mark.parametrize("name", list(REF_BODIES))
def test_body_positions_match_reference(sky0, name):
    b = sky0.moon if name == "Moon" else next(p for p in sky0.planets if p.name == name)
    assert sep(b.alt, b.az, *REF_BODIES[name]) < 0.05


def test_only_naked_eye_planets(sky0):
    assert [p.name for p in sky0.planets] == ["Mercury", "Venus", "Mars", "Jupiter", "Saturn"]
    assert 0.8 < sky0.moon.phase < 0.9
    assert sky0.sun.alt < -18 and not sky0.errors


def test_polaris_altitude_is_latitude(sky0):
    i = catalog.stars().index()[11767]
    assert abs(sky0.star_alt[i] - config.LAT) < 0.8          # Polaris is 0.7 deg from the pole


def test_failing_body_is_skipped_not_fatal(monkeypatch):
    real = astro._altaz
    calls = {"n": 0}

    def flaky(apparent):
        calls["n"] += 1
        alt, az = real(apparent)
        if calls["n"] == 6:             # 1 stars, 2 labels, 3 Mercury, 4 Venus, 5 Mars, 6 Jupiter
            return np.nan, az
        return alt, az
    monkeypatch.setattr(astro, "_altaz", flaky)
    sky = astro.compute(T0)
    assert [p.name for p in sky.planets] == ["Mercury", "Venus", "Mars", "Saturn"]
    assert any(e.startswith("Jupiter") for e in sky.errors)


def test_nan_star_positions_are_hidden(monkeypatch):
    real = astro._altaz

    def nan_first(apparent):
        alt, az = real(apparent)
        if np.ndim(alt) and len(alt) > 100:
            alt = alt.copy()
            alt[:3] = np.nan
        return alt, az
    monkeypatch.setattr(astro, "_altaz", nan_first)
    sky = astro.compute(T0)
    assert np.isfinite(sky.star_alt).all() and (sky.star_alt[:3] == -90).all()
    assert "3 star positions were not finite" in sky.errors


# ----------------------------------------------------------------- drawing

@pytest.mark.parametrize("view", draw.VIEWS)
def test_image_format(charts, view):
    _, img = charts[view]
    assert img.size == (1072, 1448) and img.mode == "L"
    assert set(img.tobytes()) <= {17 * k for k in range(16)}       # the 16 e-ink grey levels


@pytest.mark.parametrize("view", draw.VIEWS)
def test_no_drawing_errors(charts, view):
    assert charts[view][0].errors == []


def bright_near(img, x, y, r=4):
    box = img.crop((int(x) - r, int(y) - r, int(x) + r + 1, int(y) + r + 1))
    return max(box.tobytes())


def dark_fraction(img, box):
    data = img.crop(box).tobytes()
    return sum(v < 60 for v in data) / len(data)


@pytest.mark.parametrize("view", draw.VIEWS)
def test_planet_drawn_at_its_position_and_labelled(charts, sky0, view):
    chart, img = charts[view]
    saturn = next(p for p in sky0.planets if p.name == "Saturn")
    x, y = chart.view.project(saturn.alt, saturn.az)
    assert bright_near(img, x / draw.S, y / draw.S) == 255
    assert set(chart.planet_labels) == {"Saturn"}               # the only planet above the horizon
    # And the reverse: the pixel maps back to Saturn's alt/az within 0.2 deg.
    alt, az = chart.view.unproject(x, y)
    assert sep(alt, az, saturn.alt, saturn.az) < 0.2


@pytest.mark.parametrize("view", draw.VIEWS)
def test_line_stars_above_horizon_are_drawn(charts, sky0, view):
    """Every constellation line that is drawn ends on a drawn star."""
    chart, _ = charts[view]
    s = catalog.stars()
    ok, _, _ = chart.visible(sky0.star_alt, sky0.star_az)
    visible_line_stars = {int(h) for h, k in zip(s.hip, ok) if k} & catalog.line_star_hips()
    assert visible_line_stars and visible_line_stars <= chart.drawn_hips


def test_faint_line_star_drawn_even_beyond_mag_limit():
    s = catalog.stars()
    faint = [h for h in catalog.line_star_hips() if s.mag[s.index()[h]] > config.MAG_LIMIT["overhead"]]
    assert faint                                              # the catalogue has some
    found = False
    for hours in range(0, 24, 2):
        sky = astro.compute(T0 + timedelta(hours=hours))
        chart = draw.Chart("overhead", sky, T0)
        chart.render()
        ok, _, _ = chart.visible(sky.star_alt, sky.star_az)
        vis = {int(h) for h, k in zip(s.hip, ok) if k} & set(faint)
        assert vis <= chart.drawn_hips
        found |= bool(vis)
    assert found


def test_header_and_footer(charts):
    for view, (_chart, img) in charts.items():
        hdr = img.crop((0, 0, 1072, config.HEADER_H - 4))
        assert hdr.getextrema() == (0, 255)                       # white text on black
        # EXIT box outline at its left edge, inside the footer
        assert bright_near(img, config.EXIT_X + 2, (config.FOOTER_Y + 1448) // 2, 2) == 255
        assert img.getpixel((config.EXIT_X - 20, config.FOOTER_Y - 4 if view == "south" else 1440)) < 128


def test_south_view_has_ground_and_horizon(charts):
    chart, img = charts["south"]
    y = chart.horizon_y
    assert img.getpixel((5, y + 30)) == draw.GROUND // 17 * 17
    assert bright_near(img, 300, y, 1) == 255


def test_overhead_outside_circle_is_blank(charts):
    chart, img = charts["overhead"]
    cx, cy, r = chart.view.cx / draw.S, chart.view.cy / draw.S, chart.view.radius / draw.S
    # a ring just outside the horizon, away from the N/E/S/W letters, holds nothing
    for ang in range(20, 360, 45):
        x = cx + (r + 12) * np.sin(np.radians(ang))
        y = cy - (r + 12) * np.cos(np.radians(ang))
        assert bright_near(img, x, y, 2) == 0


def test_one_failing_layer_still_produces_image(monkeypatch, sky0):
    def boom(self):
        raise RuntimeError("kaput")
    monkeypatch.setattr(draw.Chart, "constellation_lines", boom)
    chart = draw.Chart("south", sky0, T0)
    img = chart.render()
    assert img.size == (1072, 1448)
    assert chart.errors == ["south/constellation_lines: RuntimeError: kaput"]
    assert chart.planet_labels and chart.drawn_hips               # the rest is still drawn


def test_one_failing_planet_keeps_the_others(monkeypatch):
    sky = astro.compute(datetime.fromisoformat("2027-01-15T02:30-05:00"))   # Mars + Jupiter up
    real = draw.Chart._planet

    def planet(self, p):
        if p.name == "Mars":
            raise ValueError("bad")
        real(self, p)
    monkeypatch.setattr(draw.Chart, "_planet", planet)
    chart = draw.Chart("overhead", sky, T0)
    chart.render()
    assert "overhead/Mars: ValueError: bad" in chart.errors
    assert "Jupiter" in chart.planet_labels


def test_unknown_view_rejected(sky0):
    with pytest.raises(ValueError):
        draw.Chart("north", sky0, T0)


def test_daytime_note_and_sun(sky0):
    sky = astro.compute(datetime.fromisoformat("2027-03-05T14:00-05:00"))
    chart = draw.Chart("south", sky, T0)
    img = chart.render()
    assert sky.sun.alt > 20 and "Sun" in chart.planet_labels
    assert not chart.errors
    assert img.size == (1072, 1448)


def test_star_radius_monotonic():
    r = draw.star_radius(np.array([-1.5, 0, 2, 4, 6]))
    assert list(r) == sorted(r, reverse=True) and r.min() >= 1.6


def test_time_formatting():
    t = datetime(2026, 7, 4, 3, 5, tzinfo=UTC)       # 11:05 PM EDT the day before
    assert draw.local_time(t) == "Fri Jul 3, 11:05 PM EDT"
    assert draw.short_time(datetime(2026, 1, 5, 17, 0, tzinfo=UTC)) == "12:00 PM"
    assert [draw.compass(a) for a in (0, 22, 23, 180, 338, 359)] == ["N", "N", "NE", "S", "N", "N"]


def test_placer_avoids_overlap():
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("L", (400, 400)))
    p = draw.Placer(bounds=(0, 0, 400, 400))
    f = draw.font(20)
    a = p.place(d, 200, 200, "ALPHA", f, 10)
    b = p.place(d, 200, 200, "ALPHA", f, 10)
    assert a and b and a != b
    for _ in range(8):
        p.place(d, 200, 200, "ALPHA", f, 10)
    assert p.place(d, 200, 200, "ALPHA", f, 10) is None
    assert p.place(d, 200, 200, "ALPHA", f, 10, force=True) is not None


# ------------------------------------------------------------- site output

def test_slots():
    now = 1_800_000_000 + 123
    s = render.slots(now, 5, 1)
    assert s[0] == 1_800_000_000 - 300 and s[-1] <= now + 3600 < s[-1] + 300
    assert all(b - a == 300 for a, b in itertools.pairwise(s))


def test_render_site(tmp_path):
    status = render.render_site(tmp_path, T0, hours=0.2)
    assert validate.check_site(tmp_path) == []
    assert status["slots"] == 4 and status["errors"] == []
    assert json.loads((tmp_path / "status.json").read_text())["rendered_at"] == "2026-11-21T02:00:00+00:00"
    cur = status["current_slot"]
    assert cur == int(T0.timestamp())
    assert (tmp_path / "south.png").read_bytes() == (tmp_path / "frames" / f"south-{cur}.png").read_bytes()
    for p in (tmp_path / "frames").iterdir():
        assert p.stat().st_size < config.MAX_PNG_BYTES / 4
    assert "Rendered <b>Fri Nov 20, 9:00 PM EST</b>" in (tmp_path / "index.html").read_text()


def test_render_site_replaces_old_frames(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "south-1.png").write_bytes(b"old")
    render.render_site(tmp_path, T0, hours=0)
    assert not (tmp_path / "frames" / "south-1.png").exists()


def test_frame_failure_is_isolated(tmp_path, monkeypatch):
    real = astro.compute
    bad = int(T0.timestamp()) + 300

    def compute(when):
        if int(when.timestamp()) == bad:
            raise RuntimeError("frame broke")
        return real(when)
    monkeypatch.setattr(astro, "compute", compute)
    status = render.render_site(tmp_path, T0, hours=0.1)
    assert any("frame failed" in e for e in status["errors"])
    assert (tmp_path / "south.png").exists()
    assert not (tmp_path / "frames" / f"south-{bad}.png").exists()


def test_cli(tmp_path, capsys):
    assert render.main(["--out", str(tmp_path), "--hours", "0", "--at", "2026-11-20T21:00-05:00"]) == 0
    assert "rendered 2 slots" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        render.main(["--out", str(tmp_path), "--at", "2026-11-20T21:00"])
    assert validate.main([str(tmp_path)]) == 0


# --------------------------------------------------------------- validator

def ihdr(w=1072, h=1448, depth=8, color=0, interlace=0):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, depth, color, 0, 0,
                                                                                interlace) + b"\0" * 4


def test_kindle_header_rule():
    assert validate.kindle_header_ok(ihdr())
    for bad in (ihdr(w=1071), ihdr(h=1449), ihdr(depth=16), ihdr(color=2), ihdr(interlace=1), ihdr()[:20],
                b"GIF89a" + ihdr()[6:]):
        assert not validate.kindle_header_ok(bad)


def test_check_png_rejects(tmp_path):
    def save(img, name, **kw):
        p = tmp_path / name
        img.save(p, **kw)
        return p
    good = save(Image.effect_noise((1072, 1448), 40).point(lambda v: 0 if v < 200 else 255), "good.png")
    assert validate.check_png(good) == []
    assert validate.check_png(save(Image.new("RGB", (1072, 1448)), "rgb.png"))
    assert validate.check_png(save(Image.new("L", (1000, 1448)), "small.png"))
    assert any("not dark" in p for p in validate.check_png(save(Image.new("L", (1072, 1448), 255), "white.png")))
    assert any("nothing drawn" in p for p in validate.check_png(save(Image.new("L", (1072, 1448), 0), "black.png")))
    assert validate.check_site(tmp_path) and validate.main([str(tmp_path / "nope")]) == 1


# ---------------------------------------------------------------- stability

def _random_times(n, seed):
    rng = random.Random(seed)
    start = datetime(2000, 1, 1, tzinfo=UTC)
    span = (datetime(2052, 12, 31, tzinfo=UTC) - start).total_seconds()
    return [start + timedelta(seconds=rng.uniform(0, span)) for _ in range(n)]


def _render_ok(when):
    sky = astro.compute(when)
    for view in draw.VIEWS:
        chart = draw.Chart(view, sky, when)
        img = chart.render()
        assert not chart.errors, (when, chart.errors)
        assert img.size == (1072, 1448) and img.mode == "L"
        visible = {p.name for p in sky.planets if bool(chart.visible(p.alt, p.az)[0])}
        assert set(chart.planet_labels) - {"Sun"} == visible, when    # every visible planet labelled


def test_stability_quick():
    for when in _random_times(12, seed=7):
        _render_ok(when)


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(10))
def test_stability_many(seed):
    for when in _random_times(50, seed=seed):
        _render_ok(when)
