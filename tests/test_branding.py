"""Guard the macOS menu-bar (status item) KIWI icon — the user's own design.

The user drew this icon themselves and rendered ONE finished PNG per tray size
(16/22/32/44/64) with LANCZOS, each with deliberately widened white letter gaps
and soft anti-aliased edges. The whole point is that the status bar shows that
artwork verbatim: crisp at every size, the "KIWI" letters individually readable
instead of one black block.

These are behavioural tests: they render real pixels through icon_image() and
measure alpha, so a no-op source edit cannot pass (the 0.8.31/0.8.32 lesson).
The colour app/Dock icon and Windows/Linux tray icon are still vectors and must
stay untouched by the template-artwork swap.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pytest  # noqa: E402

from mouseshare import _kiwi_tray_art  # noqa: E402
from mouseshare import branding  # noqa: E402

INK = 128          # alpha >= this counts as solid ink (soft edges sit below)
CLEAR = 16
FINISHED_SIZES = (16, 22, 32, 44, 64)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alpha(image, x, y):
    return (image.pixel(x, y) >> 24) & 0xFF


def _ink_bounds(image):
    minx, miny, maxx, maxy = image.width(), image.height(), 0, 0
    for y in range(image.height()):
        for x in range(image.width()):
            if _alpha(image, x, y) >= INK:
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    return minx, miny, maxx, maxy


def _edges_have_clear_pixel(image):
    """Every outer edge keeps a nearly-transparent pixel => not cropped into a
    hard square (the artwork is inset in its canvas)."""
    w, h = image.width(), image.height()
    return (
        any(_alpha(image, x, 0) < CLEAR for x in range(w))
        and any(_alpha(image, x, h - 1) < CLEAR for x in range(w))
        and any(_alpha(image, 0, y) < CLEAR for y in range(h))
        and any(_alpha(image, w - 1, y) < CLEAR for y in range(h))
    )


def _gap_runs(image, y0, y1, threshold=0.85):
    """Widths of consecutive 'clear' columns across the letter band.

    The "not one black block" metric on a soft-alpha artwork: a column counts
    as clear when >85% of its pixels in the band are below INK. Several runs
    means the letters are individually readable; one/none means the gaps
    collapsed and the wordmark is a blob again.
    """
    runs, run = [], 0
    height = max(1, y1 - y0)
    for x in range(image.width()):
        clear = sum(1 for y in range(y0, y1) if _alpha(image, x, y) < INK) / height
        if clear > threshold:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


# ----------------------------------------------------------- baked artwork data

@pytest.mark.parametrize("size", FINISHED_SIZES)
def test_finished_artwork_decodes_pure_black_at_its_size(app, size):
    """Each of the user's per-size renders must decode to a square PNG of that
    exact size, with pure-black ink (macOS tints a template from alpha alone)."""
    data = _kiwi_tray_art.size_png(size)
    assert data, f"no finished artwork for {size}px"
    image = QImage()
    assert image.loadFromData(data, "PNG")
    assert image.width() == image.height() == size
    coloured = 0
    for y in range(size):
        for x in range(size):
            p = image.pixel(x, y)
            if ((p >> 24) & 0xFF) >= INK and (p & 0x00FFFFFF):
                coloured += 1
    assert coloured == 0, "template mask ink must be pure black"


def test_master_art_decodes_as_monochrome_mask(app):
    """The 512 px fallback master is also a pure-black + alpha PNG."""
    image = QImage()
    assert image.loadFromData(_kiwi_tray_art.master_png(), "PNG")
    assert image.width() == image.height() == 512
    coloured = 0
    for y in range(0, 512, 5):
        for x in range(0, 512, 5):
            p = image.pixel(x, y)
            if ((p >> 24) & 0xFF) >= INK and (p & 0x00FFFFFF):
                coloured += 1
    assert coloured == 0


def test_finished_sizes_are_exactly_the_designed_set(app):
    assert tuple(sorted(_kiwi_tray_art.SIZED_SIZES)) == FINISHED_SIZES


def test_unknown_size_has_no_finished_asset(app):
    assert _kiwi_tray_art.size_png(36) is None
    assert _kiwi_tray_art.size_png(999) is None


# ------------------------------------------------------------- rendered template

@pytest.mark.parametrize("size", FINISHED_SIZES)
def test_template_uses_finished_artwork_verbatim(app, size):
    """The status bar shows the user's exact render: icon_image() at a designed
    size must be pixel-identical to the baked PNG (no rescale, no hardening)."""
    baked = QImage()
    baked.loadFromData(_kiwi_tray_art.size_png(size), "PNG")
    baked = baked.convertToFormat(QImage.Format.Format_ARGB32)
    rendered = branding.icon_image(size, template=True)
    assert (rendered.width(), rendered.height()) == (size, size)
    for y in range(size):
        for x in range(size):
            assert rendered.pixel(x, y) == baked.pixel(x, y), f"pixel {x},{y} changed"


@pytest.mark.parametrize("size", FINISHED_SIZES)
def test_template_keeps_soft_antialiased_edges(app, size):
    """The user's LANCZOS anti-aliasing is intentional and must survive: there
    are partially transparent pixels (not a hard 0/255 mask)."""
    image = branding.icon_image(size, template=True)
    soft = sum(1 for y in range(size) for x in range(size)
               if 0 < _alpha(image, x, y) < 255)
    assert soft > 0, "soft anti-aliased edges were flattened"


@pytest.mark.parametrize("size", FINISHED_SIZES)
def test_template_letters_have_gaps(app, size):
    """The user's requirement: widened gaps keep the letters readable at every
    tray size instead of one solid black block. Measure the letter band (lower
    part of the design; the bird sits above it)."""
    image = branding.icon_image(size, template=True)
    y0, y1 = int(size * 0.42), int(size * 0.88)
    runs = _gap_runs(image, y0, y1)
    assert len(runs) >= 3, f"letter gaps collapsed at {size}px: {runs}"


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_corners_not_cropped(app, size):
    assert _edges_have_clear_pixel(branding.icon_image(size, template=True))


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_fills_menu_bar_width(app, size):
    """Wordmark spans nearly the full canvas width like neighbouring icons.
    16 px rounds down to 0.875 by integer rasterisation and is not a mac size."""
    image = branding.icon_image(size, template=True)
    minx, _, maxx, _ = _ink_bounds(image)
    assert (maxx - minx + 1) / size >= 0.88


@pytest.mark.parametrize("size", [20, 28, 36, 48, 128])
def test_fallback_size_still_renders_and_keeps_gaps(app, size):
    """Sizes with no finished asset fall back to a smooth scale of the master;
    they must still render an alpha mask with readable letter gaps."""
    image = branding.icon_image(size, template=True)
    assert (image.width(), image.height()) == (size, size)
    y0, y1 = int(size * 0.42), int(size * 0.88)
    runs = _gap_runs(image, y0, y1)
    assert len(runs) >= 3, f"fallback gaps collapsed at {size}px: {runs}"


# ----------------------------------------------------------- colour art untouched

def test_colour_icon_is_not_the_wordmark(app):
    """The app/Dock/Windows/Linux icon stays the original colour vector, with
    real colour (not a black mask) and its original inset."""
    coloured = branding.icon_image(256, template=False)
    minx, _, maxx, _ = _ink_bounds(coloured)
    assert (maxx - minx + 1) / coloured.width() < 0.88
    has_colour = any(
        (coloured.pixel(x, y) & 0x00FFFFFF) and _alpha(coloured, x, y) >= INK
        for y in range(0, 256, 4) for x in range(0, 256, 4)
    )
    assert has_colour


# ------------------------------------------------------------------- tray icon

def test_tray_icon_mask_and_sizes(app):
    """setIsMask follows the template flag; the mac template never offers the
    16 px canvas Qt would pick and render too small."""
    mac = branding.tray_icon(template=True)
    other = branding.tray_icon(template=False)
    assert mac.isMask() is True
    assert other.isMask() is False
    mac_sizes = {s.width() for s in mac.availableSizes()}
    assert mac_sizes and min(mac_sizes) >= 22
    assert 16 not in mac_sizes
    assert 16 in {s.width() for s in other.availableSizes()}
