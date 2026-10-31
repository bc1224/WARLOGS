"""Where you went during a round, for the replay map on match details.

During a round the tracker only saves: a small minimap picture every few seconds,
and, whenever the big map is open on a screen it reads anyway, the map pane with
the coordinates its grid labels give. Matching the minimap pictures to those map
pictures happens later in the dashboard, so it costs nothing while playing.

Coordinates are the game's own, in 100 m units (what chat shows as X71.0, Y105.7).
"""
import json
import os
import re
import shutil

# as fractions of the window: x, y, w, h (measured at 1280 x 720)
MINIMAP = (21 / 1280, 497 / 720, 171 / 1280, 153 / 720)  # you are at its center, north up
SNAP_W, SNAP_H = 171, 153
PANE = (421 / 1280, 139 / 720, 437 / 1280, 438 / 720)     # the big map's picture
# the grid labels under and left of the pane, read enlarged once the map is seen open
LABEL_STRIPS = ((400 / 1280, 577 / 720, 479 / 1280, 34 / 720), (380 / 1280, 130 / 720, 41 / 1280, 456 / 720))
LABEL_ZOOM = 3
MAP_HINTS = re.compile(r"toggle\s*(legend|zoom|health)|recenter", re.I)
SCALE = re.compile(r"\d00M|1KM", re.I)
LABEL = re.compile(r"[0-9]{1,3}")
# file and folder names: ASCII digits only, so nothing else can name a path
NAME = re.compile(r"[0-9]{8}-[0-9]{6}")
SNAP_NAME = re.compile(r"[0-9]{9,11}\.(jpg|png)")
MAP_FILE = re.compile(r"[0-9]{8}-[0-9]{6}\.(jpg|png)")


def map_open(lines):
    """The hint bar along the bottom of the big map, or its scale labels down the right."""
    texts = [l.get("text") or "" for l in lines if isinstance(l, dict)]
    return (sum(1 for t in texts if MAP_HINTS.search(t)) >= 2
            or sum(1 for t in texts if SCALE.search(t.replace(" ", ""))) >= 3)


def unzoom(lines, frac, ow, oh, rw, rh):
    """OCR lines of an enlarged strip, moved back to where they are on a rw x rh frame."""
    sx, sy = frac[2] * rw / ow, frac[3] * rh / oh
    return [dict(l, x=frac[0] * rw + l.get("x", 0) * sx, y=frac[1] * rh + l.get("y", 0) * sy,
                 **({"w": l["w"] * sx} if l.get("w") else {}), **({"h": l["h"] * sy} if l.get("h") else {}))
            for l in lines if isinstance(l, dict)]


def _labels(lines, rw, rh):
    """Grid labels: (x center, value) under the pane and (y center, value) left of it,
    and how many 100 m units one label step is."""
    px, py, pw, ph = PANE[0] * rw, PANE[1] * rh, PANE[2] * rw, PANE[3] * rh
    xs, ys, unit = [], [], 1.0
    for l in lines:
        if not isinstance(l, dict):
            continue
        text, x, y = l.get("text") or "", l.get("x", 0), l.get("y", 0)
        h = l.get("h") or 16 * rh / 1080
        w = l.get("w") or len(text) * 11 * rw / 1920
        if x > px + pw and "KM" in text.upper().replace(" ", ""):
            unit = 10.0
        tokens = text.split()
        if py + ph < y < py + ph + 0.06 * rh and px - 0.02 * rw < x < px + pw:
            # one OCR line can hold several labels ("70 71"): spread them over its width
            step = w / max(1, len(text))
            at = x
            for tok in tokens:
                m = LABEL.search(tok)
                if m:
                    xs.append((at + (tok.index(m.group()) + len(m.group()) / 2) * step, int(m.group())))
                at += (len(tok) + 1) * step
        elif px - 0.05 * rw < x < px and py - 0.02 * rh < y < py + ph and len(tokens) == 1:
            m = LABEL.fullmatch(text.strip(".-= "))
            if m:
                ys.append((y + h / 2, int(m.group())))
    return xs, ys, unit


def _fit(points, sign):
    """pos = a + b * value through the most labels; b's sign is +1 (x) or -1 (y, north
    up). Misread labels fall out. Returns (a, b) or None."""
    best = []
    for i, (p1, v1) in enumerate(points):
        for p2, v2 in points[i + 1:]:
            if v1 == v2:
                continue
            b = (p2 - p1) / (v2 - v1)
            if b * sign <= 0:
                continue
            fit = [(p, v) for p, v in points if abs(p1 + b * (v - v1) - p) < 0.25 * abs(b)]
            if len(fit) > len(best):
                best = fit
    if len(best) < 3:
        return None
    n = len(best)
    mv = sum(v for _, v in best) / n
    mp = sum(p for p, _ in best) / n
    b = sum((v - mv) * (p - mp) for p, v in best) / sum((v - mv) ** 2 for _, v in best)
    return mp - b * mv, b


def _place(points, b):
    """pos = a + b * value with b known: the a that at least two labels agree on."""
    best = []
    for p1, v1 in points:
        fit = [(p, v) for p, v in points if abs(p1 + b * (v - v1) - p) < 0.25 * abs(b)]
        if len({v for _, v in fit}) > len({v for _, v in best}):
            best = fit
    if len({v for _, v in best}) < 2:
        return None
    return sum(p - b * v for p, v in best) / len(best), b


def georef(lines, rw, rh):
    """What the pane's edges are in map coordinates, from the grid labels of a
    screen read at rw x rh: {"x0", "x1", "y0", "y1"} (left, right, top, bottom),
    or None when the labels don't give a clear answer."""
    xs, ys, unit = _labels(lines, rw, rh)
    fx, fy = _fit(xs, 1), _fit(ys, -1)
    if fx and not fy:  # zoomed in, only two labels on one axis: the pixels are square
        fy = _place(ys, -fx[1])
    elif fy and not fx:
        fx = _place(xs, -fy[1])
    if not fx or not fy:
        return None
    if abs(abs(fx[1]) / abs(fy[1]) - 1) > 0.15:  # the map's pixels are square
        return None
    px, py, pw, ph = PANE[0] * rw, PANE[1] * rh, PANE[2] * rw, PANE[3] * rh
    at = lambda f, pos: round((pos - f[0]) / f[1] * unit, 3)
    return {"x0": at(fx, px), "x1": at(fx, px + pw), "y0": at(fy, py), "y1": at(fy, py + ph)}


def box(rect, frac):
    """A window-fraction box as a screen rect."""
    x, y, w, h = rect
    return (x + round(frac[0] * w), y + round(frac[1] * h), round(frac[2] * w), round(frac[3] * h))


def crop(bgra, w, h, frac):
    """Cut a window-fraction box out of a grabbed frame: (bgra, cw, ch)."""
    x0, y0 = round(frac[0] * w), round(frac[1] * h)
    cw, ch = min(round(frac[2] * w), w - x0), min(round(frac[3] * h), h - y0)
    out = b"".join(bgra[((y0 + r) * w + x0) * 4:((y0 + r) * w + x0 + cw) * 4] for r in range(ch))
    return out, cw, ch


def prune(d, keep):
    """Keep the newest `keep` entries (named by time) of a folder."""
    try:
        names = sorted(n for n in os.listdir(d) if NAME.match(n))
    except OSError:
        return
    for n in names[:-keep] if keep else names:
        p = os.path.join(d, n)
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass


def listing(ddir, round_dir):
    """A round's minimap pictures [[t, file]] and every big map picture kept, for
    the dashboard: {"snaps", "maps"}."""
    root = os.path.join(ddir, "track")
    snaps = []
    if NAME.fullmatch(round_dir or ""):
        try:
            snaps = sorted([int(n.split(".")[0]), n] for n in os.listdir(os.path.join(root, round_dir))
                           if SNAP_NAME.fullmatch(n))
        except OSError:
            pass
    maps = []
    try:
        names = sorted(n for n in os.listdir(os.path.join(root, "maps")) if n.endswith(".json"))
    except OSError:
        names = []
    for n in names:
        try:
            with open(os.path.join(root, "maps", n), encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and MAP_FILE.fullmatch(str(m.get("file", ""))) \
                and os.path.isfile(os.path.join(root, "maps", m["file"])):
            maps.append(m)
    return {"snaps": snaps, "maps": maps}


def file_path(ddir, round_dir, name):
    """The picture a /track/... URL names, or None for anything else."""
    if round_dir == "maps":
        ok = MAP_FILE.fullmatch(name)
    else:
        ok = NAME.fullmatch(round_dir) and SNAP_NAME.fullmatch(name)
    p = os.path.join(ddir, "track", round_dir, name)
    return p if ok and os.path.isfile(p) else None


def clean_route(body):
    """A replay route posted by the dashboard, checked before it's stored:
    {"path": [[t, x, y, score]], "refs": [map picture names], "v": how it was worked out},
    or None if it's off."""
    if not isinstance(body, dict):
        return None
    path, refs = body.get("path"), body.get("refs") or []
    if not isinstance(path, list) or not isinstance(refs, list) or len(path) > 5000 or len(refs) > 200:
        return None
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e10 and v == v
    if not all(isinstance(p, list) and len(p) == 4 and all(num(v) for v in p) for p in path):
        return None
    if not all(isinstance(r, str) and NAME.fullmatch(r) for r in refs):
        return None
    v = body.get("v", 1)
    if not isinstance(v, int) or isinstance(v, bool) or not 0 < v < 1000:
        return None
    return {"path": [[int(p[0]), round(p[1], 2), round(p[2], 2), round(p[3], 2)] for p in path], "refs": refs, "v": v}
