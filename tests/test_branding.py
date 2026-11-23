"""Guard the macOS menu-bar (status item) icon sizing.

Regression: the template artwork used to fill only 82% of its canvas and the
QIcon offered a 16 px size, so the mark rendered visibly smaller than the
~18 pt status-item slot where neighbouring icons draw nearly edge-to-edge.
MouseShare looked a whole size smaller and "insubstantial". These are
*behavioural* tests: they render real pixels and measure the ink, so a no-op
source edit cannot pass (the 0.8.31/0.8.32 lesson).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import pytest  # noqa: E402

from mouseshare import branding  # noqa: E402

# Threshold for "solid ink". Antialiased corner pixels have tiny alpha and
# must not count toward the visible bounds.
SOLID = 128
CLEAR = 16


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alpha(image, x, y):
    return (image.pixel(x, y) >> 24) & 0xFF


def _ink_bounds(image):
    """Bounds of solid (non-antialiased) ink."""
    minx, miny, maxx, maxy = image.width(), image.height(), 0, 0
    for y in range(image.height()):
        for x in range(image.width()):
            if _alpha(image, x, y) > SOLID:
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    return minx, miny, maxx, maxy


def _ink_fill(image):
    minx, miny, maxx, maxy = _ink_bounds(image)
    return (maxx - minx + 1) / image.width()


def _edges_have_clear_pixel(image):
    """Every outer edge keeps a transparent pixel => rounded corners survive
    (the mark is not cropped into a hard square by the enlargement)."""
    w, h = image.width(), image.height()
    return (
        any(_alpha(image, x, 0) < CLEAR for x in range(w))
        and any(_alpha(image, x, h - 1) < CLEAR for x in range(w))
        and any(_alpha(image, 0, y) < CLEAR for y in range(h))
        and any(_alpha(image, w - 1, y) < CLEAR for y in range(h))
    )


def test_template_scale_is_configured(app):
    assert branding.TRAY_TEMPLATE_SCALE > 1.1
    # The colour tray/dock icon keeps the full original size set; only the
    # macOS template drops the tiny 16 px canvas.
    assert 16 in branding.TRAY_SIZES
    assert 16 not in branding.MAC_TEMPLATE_TRAY_SIZES
    assert min(branding.MAC_TEMPLATE_TRAY_SIZES) >= 22


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_artwork_fills_status_item_slot(app, size):
    """Solid ink must fill >=88% of the canvas at every offered template
    size - not the old ~77% inset. Real rendered pixels, not substrings.
    """
    image = branding.icon_image(size, template=True)
    assert _ink_fill(image) >= 0.88


@pytest.mark.parametrize("size", [22, 32, 44, 64])
def test_template_corners_not_cropped(app, size):
    """Filling the slot must not crop the rounded corners."""
    image = branding.icon_image(size, template=True)
    assert _edges_have_clear_pixel(image)


def test_colour_icon_unchanged_by_template_scale(app):
    """The enlargement is template-only: the colour artwork keeps its inset,
    so app/dock icons are untouched.
    """
    coloured = branding.icon_image(256, template=False)
    assert _ink_fill(coloured) < 0.88


def test_tray_icon_mask_and_sizes(app):
    """setIsMask follows the template flag, and the mac template icon exposes
    only the enlarged sizes (>=22 px), never a 16 px canvas.
    """
    mac = branding.tray_icon(template=True)
    other = branding.tray_icon(template=False)
    assert mac.isMask() is True
    assert other.isMask() is False
    mac_sizes = {s.width() for s in mac.availableSizes()}
    assert mac_sizes and min(mac_sizes) >= 22
    assert 16 not in mac_sizes
