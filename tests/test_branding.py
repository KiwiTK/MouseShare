"""Guard the macOS menu-bar (status item) Kiwi wordmark icon.

The menu-bar icon is the user's Kiwi brand wordmark, baked as an inline
black+alpha master mask. The whole point of the redesign was: *slim the letters
so gaps survive at 18 pt and the wordmark stops reading as one black block*.
These are behavioural tests: they render real pixels through icon_image() and
measure alpha, so a no-op source edit cannot pass (the 0.8.31/0.8.32 lesson).

The colour app/Dock icon and the Windows/Linux tray icon are still vectors and
must stay untouched by the template-artwork swap.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pytest  # noqa: E402

from mouseshare import _kiwi_tray_art  # noqa: E402
from mouseshare import branding  # noqa: E402

SOLID = 128
CLEAR = 16


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alpha(image, x, y):
    return (image.pixel(x, y) >> 24) & 0xFF


def _ink_bounds(image):
    minx, miny, maxx, maxy = image.width(), image.height(), 0, 0
    for y in range(image.height()):
        for x in range(image.width()):
            if _alpha(image, x, y) > SOLID:
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    return minx, miny, maxx, maxy


def _edges_have_clear_pixel(image):
    """Every outer edge keeps a transparent pixel => the mark is not cropped
    into a hard square."""
    w, h = image.width(), image.height()
    return (
        any(_alpha(image, x, 0) < CLEAR for x in range(w))
        and any(_alpha(image, x, h - 1) < CLEAR for x in range(w))
        and any(_alpha(image, 0, y) < CLEAR for y in range(h))
        and any(_alpha(image, w - 1, y) < CLEAR for y in range(h))
    )


def _clear_column_runs(image, y0, y1, threshold=0.85):
    """Widths of consecutive columns that are clear across the letter band.

    This is the "not one black block" metric: several separate runs means the
    letters are individually readable; a single run (or none) means the gaps
    collapsed and the wordmark is a blob again.
    """
    runs, run = [], 0
    height = max(1, y1 - y0)
    for x in range(image.width()):
        clear = sum(1 for y in range(y0, y1) if _alpha(image, x, y) <= 8) / height
        if clear > threshold:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


# ------------------------------------------------------------------ master art

def test_master_art_decodes_as_monochrome_mask(app):
    """Both baked variants must decode to a pure black + alpha 512 px PNG."""
    for variant in ("slim14", "slim22"):
        image = QImage()
        assert image.loadFromData(_kiwi_tray_art.master_png(variant), "PNG")
        assert image.width() == image.height() == 512
        coloured = 0
        for y in range(0, 512, 5):
            for x in range(0, 512, 5):
                pixel = image.pixel(x, y)
                if (pixel >> 24) & 0xFF > SOLID:
                    if pixel & 0x00FFFFFF:
                        coloured += 1
        assert coloured == 0, "template mask must be pure black (macOS tints from alpha)"


def test_both_variants_are_distinct(app):
    assert _kiwi_tray_art.master_png("slim14") != _kiwi_tray_art.master_png("slim22")


def test_unknown_variant_rejected(app):
    with pytest.raises(KeyError):
        _kiwi_tray_art.master_png("nope")


def test_default_variant_is_available(app):
    assert _kiwi_tray_art.DEFAULT_VARIANT in ("slim14", "slim22")


# ------------------------------------------------------------- rendered template

@pytest.mark.parametrize("size", [22, 32, 36, 44, 64])
def test_template_is_hard_alpha_mask(app, size):
    """Alpha hardening: only fully opaque or fully transparent, no grey fringes
    that would bridge the letter gaps after Qt's smooth downscale."""
    image = branding.icon_image(size, template=True)
    values = {_alpha(image, x, y) for y in range(size) for x in range(size)}
    assert values <= {0, 255}


@pytest.mark.parametrize("size", [22, 32, 36, 44, 64])
def test_template_letters_have_gaps(app, size):
    """The user's requirement: slimmed letters leave several readable gaps in
    the letter band instead of one solid black block. Rendered at the true
    ~18 pt (@2x = 36 px) scale too."""
    image = branding.icon_image(size, template=True)
    # The lettering occupies the lower part of the wordmark; the bird head sits
    # above it, so measure only that band (fractions of a 36 px reference).
    y0, y1 = int(size * 14 / 36), int(size * 30 / 36)
    runs = _clear_column_runs(image, y0, y1)
    assert len(runs) >= 3, f"letter gaps collapsed at {size}px: {runs}"
    assert max(runs) >= 1


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_corners_not_cropped(app, size):
    image = branding.icon_image(size, template=True)
    assert _edges_have_clear_pixel(image)


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_fills_menu_bar_width(app, size):
    """Wordmark spans nearly the full canvas width like neighbouring icons."""
    image = branding.icon_image(size, template=True)
    minx, _, maxx, _ = _ink_bounds(image)
    assert (maxx - minx + 1) / size >= 0.88


def test_template_alpha_cutoff_configured(app):
    assert 0 < branding.TEMPLATE_ALPHA_CUTOFF < 255


# ----------------------------------------------------------- colour art untouched

def test_colour_icon_is_not_the_wordmark(app):
    """The app/Dock/Windows/Linux icon must stay the original colour vector,
    with real colour (not a black mask) and its original inset."""
    coloured = branding.icon_image(256, template=False)
    minx, _, maxx, _ = _ink_bounds(coloured)
    assert (maxx - minx + 1) / coloured.width() < 0.88
    has_colour = any(
        (coloured.pixel(x, y) & 0x00FFFFFF) and _alpha(coloured, x, y) > SOLID
        for y in range(0, 256, 4) for x in range(0, 256, 4)
    )
    assert has_colour


# ------------------------------------------------------------------- tray icon

def test_tray_icon_mask_and_sizes(app):
    """setIsMask follows the template flag; the mac template never offers the
    16 px canvas that collapses the letter gaps."""
    mac = branding.tray_icon(template=True)
    other = branding.tray_icon(template=False)
    assert mac.isMask() is True
    assert other.isMask() is False
    mac_sizes = {s.width() for s in mac.availableSizes()}
    assert mac_sizes and min(mac_sizes) >= 22
    assert 16 not in mac_sizes
    assert 16 in {s.width() for s in other.availableSizes()}
