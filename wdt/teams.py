"""Team scores from the bar under the minimap: each team's points (3 digits) and
player count, for blue, red and green. The digits are small, so each cell is
grabbed enlarged and the six are laid side by side with gaps for one OCR read.
Reads are noisy, so a round's final score is the end of the longest run of
reads that never goes down."""
from bisect import bisect_right

TEAMS = ("blue", "red", "green")
# cells as fractions of the window (x0, x1, y0, y1): three scores, then three player counts
CELLS = tuple((a / 1280, b / 1280, 659 / 720, 681 / 720) for a, b in ((24, 57), (83, 114), (139, 170))) + \
    tuple((a / 1280, b / 1280, 665 / 720, 679 / 720) for a, b in ((63, 75), (119, 132), (175, 189)))
ZOOM = 5
GAP = 60
LOOKALIKES = str.maketrans("oOQDcCIl|SsBZzb", "000000111558226")


def strip(crops):
    """[(bgra, w, h)] -> (bgra, w, h, starts): the crops side by side on a dark
    background, and the x where each one starts."""
    hh = max(h for _, _, h in crops) + 20
    ww = sum(w for _, w, _ in crops) + GAP * (len(crops) + 1)
    out = bytearray(b"\x14\x14\x14\xff" * (ww * hh))
    starts, x = [], GAP
    for data, w, h in crops:
        starts.append(x)
        for row in range(h):
            o = ((10 + row) * ww + x) * 4
            out[o:o + w * 4] = data[row * w * 4:(row + 1) * w * 4]
        x += w + GAP
    return bytes(out), ww, hh, starts


def _cell(starts, x):
    return max(0, bisect_right(starts, x + GAP // 2) - 1)


def read(lines, starts):
    """OCR lines of a strip -> {"s": [blue, red, green], "n": [blue, red, green]},
    None where a cell didn't read."""
    got = [None] * 6
    for line in sorted(lines, key=lambda l: l.get("x", 0)):
        i = _cell(starts, line.get("x", 0))
        for tok in (line.get("text") or "").split():
            if i >= 6:
                break
            t = tok.translate(LOOKALIKES)
            if t.isdigit() and got[i] is None:
                v = int(t)
                if (i < 3 and len(t) == 3 and v <= 150) or (i >= 3 and len(t) <= 2 and v <= 64):
                    got[i] = v
            i += 1
    return {"s": got[:3], "n": got[3:]}


def _rising(vals):
    """Longest run (not necessarily adjacent) that never goes down."""
    tails, idx, prev = [], [], [None] * len(vals)
    for i, v in enumerate(vals):
        k = bisect_right(tails, v)
        prev[i] = idx[k - 1] if k else None
        if k == len(tails):
            tails.append(v)
            idx.append(i)
        else:
            tails[k], idx[k] = v, i
    out, i = [], idx[-1] if idx else None
    while i is not None:
        out.append(vals[i])
        i = prev[i]
    return out[::-1]


def summary(reads):
    """A round's reads -> {"scores", "players", "mode"}, or None with nothing read."""
    scores, players = {}, {}
    for k, team in enumerate(TEAMS):
        run = _rising([r["s"][k] for r in reads if r["s"][k] is not None])
        scores[team] = run[-1] if len(run) >= 2 else None  # one lone read could be a misread
        ns = sorted(r["n"][k] for r in reads if r["n"][k] is not None)
        players[team] = ns[len(ns) // 2] if ns else None
    if all(v is None for v in scores.values()):
        return None
    blue = (scores["blue"] or 0) > 0 or (players["blue"] or 0) >= 5
    return {"scores": scores, "players": players, "mode": "3 teams" if blue else "2 teams"}
