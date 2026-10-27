"""Small ctypes wrappers around Windows APIs.

Nothing here opens, reads or hooks the game process. We only read the
process list (like Task Manager), ask which window is in front, and copy
pixels from the desktop (like OBS Display Capture or a Print Screen).
"""
import ctypes
import ctypes.wintypes as wt
import zlib
import os
import struct

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- signatures (64-bit safe) -------------------------------------------------
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.StretchBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetBrushOrgEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]

SRCCOPY = 0x00CC0020
HALFTONE = 4
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000


def set_dpi_aware():
    """Use physical pixels so 4K captures are not scaled by Windows."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


# --- processes ----------------------------------------------------------------
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


def find_pids(exe_name):
    """PIDs whose executable name matches (case-insensitive)."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return set()
    pids = set()
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                pids.add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def foreground_rect(pids):
    """Screen rect (x, y, w, h) of the foreground window if it belongs to pids."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in pids:
        return None
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 200 or h < 200:
        return None
    return pt.x, pt.y, w, h


# --- capture ------------------------------------------------------------------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(rect, out_w, out_h):
    """Copy a screen rect, scaled to out_w x out_h. Returns BGRA bytes."""
    x, y, w, h = rect
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(bmi)
    bmi.biWidth, bmi.biHeight = out_w, -out_h  # negative = top-down rows
    bmi.biPlanes, bmi.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if (out_w, out_h) == (w, h):
            gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY)
        else:
            gdi32.SetStretchBltMode(mem, HALFTONE)
            gdi32.SetBrushOrgEx(mem, 0, 0, None)
            gdi32.StretchBlt(mem, 0, 0, out_w, out_h, screen, x, y, w, h, SRCCOPY)
        return ctypes.string_at(bits, out_w * out_h * 4)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def signature(bgra, w, h):
    """Grayscale bytes used to recognise a screen layout."""
    return bytes((bgra[i] * 29 + bgra[i + 1] * 150 + bgra[i + 2] * 77) >> 8
                 for i in range(0, w * h * 4, 4))


def write_bmp(path, bgra, w, h):
    """Uncompressed and nearly free to write; used for frames that only go to OCR."""
    header = struct.pack("<2sIHHI", b"BM", 54 + len(bgra), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, len(bgra), 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(header + info)
        f.write(bgra)


def write_png(path, bgra, w, h):
    px = bytearray(bgra)
    px[0::4], px[2::4] = px[2::4], px[0::4]  # BGRA -> RGBA
    px[3::4] = b"\xff" * (w * h)
    stride = w * 4
    raw = b"".join(b"\x00" + px[r * stride:(r + 1) * stride] for r in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 3)))
        f.write(chunk(b"IEND", b""))


def write_jpg(path, bgra, w, h, quality=70):
    """Small JPEG via Pillow; falls back to PNG (same name, .png) without it.
    Returns the path written."""
    try:
        from PIL import Image
    except ImportError:
        path = os.path.splitext(path)[0] + ".png"
        write_png(path, bgra, w, h)
        return path
    Image.frombuffer("RGBA", (w, h), bytes(bgra), "raw", "BGRA", 0, 1).convert("RGB").save(
        path, "JPEG", quality=quality, optimize=True)
    return path


# --- hotkeys ------------------------------------------------------------------
VK = {**{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
      **{f"F{n}": 0x6F + n for n in range(1, 13)}}


def parse_hotkey(text):
    """'ctrl+alt+e' -> (modifiers, virtual key)."""
    mods, vk = MOD_NOREPEAT, None
    for part in text.lower().split("+"):
        part = part.strip()
        if part == "ctrl":
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        else:
            vk = VK.get(part.upper())
    if vk is None:
        raise ValueError(f"bad hotkey: {text}")
    return mods, vk


def hotkey_loop(bindings, on_hotkey):
    """Blocking. bindings: {id: 'ctrl+alt+e'}. Calls on_hotkey(id)."""
    for hid, text in bindings.items():
        mods, vk = parse_hotkey(text)
        if not user32.RegisterHotKey(None, hid, mods, vk):
            on_hotkey(-hid)  # negative id = registration failed
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            on_hotkey(int(msg.wParam))
