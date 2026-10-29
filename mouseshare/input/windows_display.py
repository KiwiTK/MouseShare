"""Move physical displays with the Windows Display Configuration API.

This is what makes a placement in MouseShare's layout real: without it, moving
Display 2 in the editor only changes how MouseShare routes, while Windows keeps
transporting the cursor between the two screens along its own arrangement.
"""
import ctypes as C
from ctypes import wintypes as W

from ..i18n import tr

U = C.WinDLL("user32", use_last_error=True)

QDC_ONLY_ACTIVE_PATHS = 0x00000002
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1

SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x00000020
SDC_VALIDATE = 0x00000040
SDC_APPLY = 0x00000080
SDC_SAVE_TO_DATABASE = 0x00000200
SDC_ALLOW_CHANGES = 0x00000400
ERROR_SUCCESS = 0

# Refuse to touch the machine if Windows' structures are not exactly the shape
# we compiled against instead of writing into the wrong offsets.
EXPECTED = {"path": 72, "mode": 64, "source_name": 84}


class LastError(Exception):
    pass


class LUID(C.Structure):
    _fields_ = [("low", W.DWORD), ("high", W.LONG)]


class Rect(C.Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]


class MonitorInfo(C.Structure):
    _fields_ = [("size", W.DWORD), ("monitor", Rect), ("work", Rect),
                ("flags", W.DWORD), ("device", W.WCHAR*32)]


class PathSourceInfo(C.Structure):
    _fields_ = [("adapter", LUID), ("id", C.c_uint32), ("mode", C.c_uint32),
                ("status_flags", C.c_uint32)]


class Rational(C.Structure):
    _fields_ = [("numerator", C.c_uint32), ("denominator", C.c_uint32)]


class Region(C.Structure):
    _fields_ = [("cx", C.c_uint32), ("cy", C.c_uint32)]


class PathTargetInfo(C.Structure):
    _fields_ = [("adapter", LUID), ("id", C.c_uint32), ("mode", C.c_uint32),
                ("output_technology", C.c_uint32), ("rotation", C.c_uint32),
                ("scaling", C.c_uint32), ("refresh_rate", Rational),
                ("scan_line_ordering", C.c_uint32), ("available", W.BOOL),
                ("status_flags", C.c_uint32)]


class PathInfo(C.Structure):
    _fields_ = [("source", PathSourceInfo), ("target", PathTargetInfo), ("flags", C.c_uint32)]


class Point(C.Structure):
    _fields_ = [("x", W.LONG), ("y", W.LONG)]


class SignalInfo(C.Structure):
    _fields_ = [("pixel_rate", C.c_uint64), ("h_sync", Rational), ("v_sync", Rational),
                ("active", Region), ("total", Region), ("standard", C.c_uint32),
                ("scan_line_ordering", C.c_uint32)]


class TargetMode(C.Structure):
    _fields_ = [("signal", SignalInfo)]


class SourceMode(C.Structure):
    _fields_ = [("width", C.c_uint32), ("height", C.c_uint32), ("format", C.c_uint32),
                ("position", Point)]


class _ModeUnion(C.Union):
    _fields_ = [("target", TargetMode), ("source", SourceMode)]


class ModeInfo(C.Structure):
    _fields_ = [("kind", C.c_uint32), ("id", C.c_uint32), ("adapter", LUID), ("mode", _ModeUnion)]


class DeviceInfoHeader(C.Structure):
    _fields_ = [("kind", C.c_uint32), ("size", C.c_uint32), ("adapter", LUID), ("id", C.c_uint32)]


class SourceName(C.Structure):
    _fields_ = [("header", DeviceInfoHeader), ("device", W.WCHAR*32)]


U.GetDisplayConfigBufferSizes.argtypes = [C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]
U.GetDisplayConfigBufferSizes.restype = C.c_long
U.QueryDisplayConfig.argtypes = [C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(PathInfo),
                                 C.POINTER(C.c_uint32), C.POINTER(ModeInfo), C.c_void_p]
U.QueryDisplayConfig.restype = C.c_long
U.DisplayConfigGetDeviceInfo.argtypes = [C.POINTER(SourceName)]
U.DisplayConfigGetDeviceInfo.restype = C.c_long
U.SetDisplayConfig.argtypes = [C.c_uint32, C.POINTER(PathInfo), C.c_uint32,
                               C.POINTER(ModeInfo), C.c_uint32]
U.SetDisplayConfig.restype = C.c_long
U.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
U.GetMonitorInfoW.restype = W.BOOL


def _check(code):
    if code != ERROR_SUCCESS:
        raise LastError(C.WinError(C.get_last_error()))
    return code


def gdi_names():
    """Device names (\\.\\DISPLAYn) in EnumDisplayMonitors order."""
    callback = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
    names = []

    def collect(handle, dc, rect, data):
        info = MonitorInfo(size=C.sizeof(MonitorInfo))
        if U.GetMonitorInfoW(handle, C.byref(info)):
            names.append(info.device)
        else:
            names.append("")
        return True

    U.EnumDisplayMonitors.argtypes = [W.HDC, C.POINTER(W.RECT), callback, W.LPARAM]
    U.EnumDisplayMonitors.restype = W.BOOL
    if not U.EnumDisplayMonitors(None, None, callback(collect), 0) or not names:
        raise LastError(C.WinError(C.get_last_error()))
    return names


def _active():
    if (C.sizeof(PathInfo) != EXPECTED["path"] or C.sizeof(ModeInfo) != EXPECTED["mode"]
            or C.sizeof(SourceName) != EXPECTED["source_name"]):
        raise LastError(OSError(tr('Windows 显示接口结构不匹配，已中止')))
    paths, modes = C.c_uint32(), C.c_uint32()
    _check(U.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, C.byref(paths), C.byref(modes)))
    if not paths.value or not modes.value:
        raise LastError(OSError(tr('没有读取到活动的显示器')))
    path_array = (PathInfo*paths.value)()
    mode_array = (ModeInfo*modes.value)()
    _check(U.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, C.byref(paths), path_array,
                                C.byref(modes), mode_array, None))
    return path_array, mode_array, paths.value, modes.value


def _source_positions(path_array, count):
    """Map each path's GDI device name to the mode holding its screen position."""
    modes = {}
    for index in range(count):
        path = path_array[index]
        query = SourceName()
        query.header.kind = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
        query.header.size = C.sizeof(SourceName)
        query.header.adapter = path.source.adapter
        query.header.id = path.source.id
        if U.DisplayConfigGetDeviceInfo(C.byref(query)) != ERROR_SUCCESS:
            continue
        modes[query.device or ""] = (path.source.mode, index)
    return modes


def set_positions(positions):
    """Place every display at (x, y) in the virtual desktop, order of gdi_names()."""
    path_array, mode_array, count, mode_count = _active()
    by_name = _source_positions(path_array, count)
    names = gdi_names()
    if len(names) != len(positions):
        raise LastError(OSError(tr('显示器数量已变化，请重新读取布局')))
    missing = [name for name in names if name not in by_name]
    if missing:
        raise LastError(OSError(tr('找不到屏幕 {p0}，无法排列', p0='、'.join(missing) or '?')))
    for index, name in enumerate(names):
        slot = by_name[name][0]
        mode_array[slot].mode.source.position.x = int(positions[index][0])
        mode_array[slot].mode.source.position.y = int(positions[index][1])
    _check(U.SetDisplayConfig(count, path_array, mode_count, mode_array,
                              SDC_VALIDATE | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES))
    _check(U.SetDisplayConfig(count, path_array, mode_count, mode_array,
                              SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG
                              | SDC_SAVE_TO_DATABASE | SDC_ALLOW_CHANGES))
    return True
