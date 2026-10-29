"""The in-match Scoreboard tab: your place by cash earned, kills, assists, deaths
and revives. Windows OCR drops lone digits ("0", "1"), so the numbers are read
with digit shapes learned from the cash column of the same screen, whose
"$13,381"-style amounts OCR reads well."""
import colorsys
import difflib
import re

INK = 95  # max(r, g, b) of text; the table background and row highlight sit well below
SIZE = (6, 10)  # glyphs are compared at this size
HEAD = {"cash": re.compile(r"EARNED", re.I), "kills": re.compile(r"^KILLS$", re.I),
        "assists": re.compile(r"^ASSISTS$", re.I), "deaths": re.compile(r"^DEATHS$", re.I),
        "revives": re.compile(r"^REVIVES$", re.I)}
CASH_TEXT = re.compile(r"^\$\d{1,3}(?:,\d{3})*$")
LEAD_NUM = re.compile(r"^(\d{1,3})\b\s*(.*)$")
STATS = ("kills", "assists", "deaths", "revives")


class Gray:
    """Brightness lookups on a frame: BGRA bytes from winapi.grab, or a plain
    one-byte-per-pixel crop placed at (ox, oy) in frame coordinates."""

    def __init__(self, data, w, h, bpp=4, ox=0, oy=0):
        self.data, self.w, self.h, self.bpp, self.ox, self.oy = data, w, h, bpp, ox, oy

    def get(self, x, y):
        x, y = x - self.ox, y - self.oy
        if not (0 <= x < self.w and 0 <= y < self.h):
            return 0
        i = (y * self.w + x) * self.bpp
        d = self.data
        return d[i] if self.bpp == 1 else max(d[i], d[i + 1], d[i + 2])

    def rgb(self, x, y):
        """(r, g, b), or None off the frame or on a brightness-only crop."""
        x, y = x - self.ox, y - self.oy
        if self.bpp < 3 or not (0 <= x < self.w and 0 <= y < self.h):
            return None
        i = (y * self.w + x) * self.bpp
        return self.data[i + 2], self.data[i + 1], self.data[i]


# hue ranges (degrees) of the faction icons beside each player's level: red wings,
# blue gear, green globe; your row's gold outline (about 45) falls between them
FACTION_HUES = {"red": ((0, 20), (335, 360)), "green": ((85, 165),), "blue": ((180, 235),)}


def faction(px, x0, x1, y0, y1):
    """The team colour of the icon inside a box: "red", "green", "blue" or None."""
    votes = dict.fromkeys(FACTION_HUES, 0)
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = px.rgb(x, y)
            if c is None:
                return None
            h, s, v = colorsys.rgb_to_hsv(*(n / 255 for n in c))
            if s < 0.45 or v < 0.35:
                continue
            for team, spans in FACTION_HUES.items():
                if any(a <= h * 360 < b for a, b in spans):
                    votes[team] += 1
    best = max(votes, key=votes.get)
    rest = max(n for t, n in votes.items() if t != best)
    return best if votes[best] >= 12 and votes[best] >= 3 * rest else None


def is_scoreboard(lines):
    t = {(l.get("text") or "").strip().upper() for l in lines}
    return "SCOREBOARD" in t and sum(any(rx.search(x) for x in t) for rx in HEAD.values()) >= 3


def _ink_cols(px, x0, x1, y0, y1):
    return [any(px.get(x, y) >= INK for y in range(y0, y1)) for x in range(x0, x1)]


def _runs(cols, x0, gap):
    """Runs of inked columns, joined across gaps shorter than gap: (start, end) pairs."""
    out = []
    for i, on in enumerate(cols):
        if not on:
            continue
        x = x0 + i
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x + 1
        else:
            out.append([x, x + 1])
    return out


def _glyph(px, x0, x1, y0, y1):
    rows = [y for y in range(y0, y1) if any(px.get(x, y) >= INK for x in range(x0, x1))]
    if not rows:
        return None
    t, b = rows[0], rows[-1] + 1
    gw, gh = SIZE
    vec = []
    for j in range(gh):
        for i in range(gw):
            xa, xb = x0 + (x1 - x0) * i // gw, x0 + max((x1 - x0) * (i + 1) // gw, (x1 - x0) * i // gw + 1)
            ya, yb = t + (b - t) * j // gh, t + max((b - t) * (j + 1) // gh, (b - t) * j // gh + 1)
            cells = [px.get(x, y) >= INK for x in range(xa, xb) for y in range(ya, yb)]
            vec.append(sum(cells) / len(cells))
    return {"vec": vec, "aspect": (x1 - x0) / (b - t), "h": b - t, "top": t}


def _split(px, a, b, y0, y1):
    """Glyph spans inside an inked run: split where the column ink dips, since
    neighbouring glyphs can touch after scaling."""
    prof = [sum(max(0, px.get(x, y) - 90) for y in range(y0, y1)) for x in range(a, b)]
    top = max(prof) or 1
    cuts, start = [], a
    for i in range(2, len(prof) - 2):
        x = a + i
        if prof[i] <= 0.18 * top and prof[i] <= prof[i - 1] and prof[i] <= prof[i + 1] and x - start >= max(4, (y1 - y0) // 5) and b - x >= max(4, (y1 - y0) // 5):
            cuts.append((start, x))
            start = x + 1
    cuts.append((start, b))
    return [(s, e) for s, e in cuts if e - s >= 2]


def _glyphs(px, x0, x1, y0, y1):
    return [g for ra, rb in _runs(_ink_cols(px, x0, x1, y0, y1), x0, 1)
            for a, b in _split(px, ra, rb, y0, y1) if (g := _glyph(px, a, b, y0, y1))]


def learn(templates, glyphs, text):
    """Add labeled glyphs to templates {char: {"vec", "aspect", "n"}} (running means)."""
    if len(glyphs) != len(text):
        return False
    for g, ch in zip(glyphs, text):
        t = templates.setdefault(ch, {"vec": [0.0] * len(g["vec"]), "aspect": 0.0, "n": 0})
        n = t["n"] = min(t["n"] + 1, 50)
        t["vec"] = [a + (b - a) / n for a, b in zip(t["vec"], g["vec"])]
        t["aspect"] += (g["aspect"] - t["aspect"]) / n
    return True


def classify(templates, g):
    best = None
    for ch, t in templates.items():
        if not ch.isdigit():
            continue
        d = sum((a - b) ** 2 for a, b in zip(g["vec"], t["vec"])) + 20 * (g["aspect"] - t["aspect"]) ** 2
        if best is None or d < best[0]:
            best = (d, ch)
    return best[1] if best else None


def _number(templates, glyphs, height, cash=False):
    # "," is short; "$" pokes above and below the digits, so it is the tallest glyph
    if cash and glyphs and glyphs[0]["h"] >= max(g["h"] for g in glyphs[1:] or glyphs):
        glyphs = glyphs[1:]
    chars = [classify(templates, g) if g["h"] >= height * 0.6 else "," for g in glyphs]
    digits = "".join(c for c in chars if c and c.isdigit())
    if not digits or any(c is None for c in chars):
        return None
    return int(digits)


def read_board(lines, px, name="", templates=None):
    """Parse the scoreboard. Returns {"view", "players", "rows": [...], "me", "above"}
    or None; templates (a dict) is updated in place with digit shapes."""
    templates = {} if templates is None else templates
    txt = lambda l: (l.get("text") or "").strip()
    head = {}
    for l in lines:
        for k, rx in HEAD.items():
            if rx.search(txt(l)) and k not in head:
                head[k] = l
    if not {"cash", "kills", "deaths", "revives"} <= set(head):
        return None
    hy = head["kills"]["y"]
    cashes = [l for l in lines if l["y"] > hy + 15 and abs(l["x"] - head["cash"]["x"]) < 120
              and re.search(r"[\d$S][\d$S.,' ]{2,}", txt(l))]
    if len(cashes) < 2:
        return None
    cash_x = sorted(l["x"] for l in cashes)[len(cashes) // 2]
    ys = sorted({l["y"] for l in cashes})
    diffs = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 10)
    pitch = diffs[0] if diffs else 38
    pitch = sorted(d / round(d / pitch) for d in diffs)[len(diffs) // 2] if diffs else pitch
    rows_y = [ys[0]]
    for y in ys[1:]:
        k = round((y - rows_y[-1]) / pitch)
        rows_y += [round(rows_y[-1] + pitch * (i + 1)) for i in range(k - 1)] + ([y] if k >= 1 else [])
    right = round(head["revives"]["x"] + 0.3 * (head["revives"]["x"] - head["deaths"]["x"]))
    band = lambda y: (round(y - 0.2 * pitch), round(y + 0.62 * pitch))

    # learn digit shapes from cash amounts OCR read cleanly
    for l in cashes:
        t = txt(l).replace(" ", "")
        if CASH_TEXT.match(t):
            y0, y1 = band(l["y"])
            grp = _runs(_ink_cols(px, cash_x - 12, right, y0, y1), cash_x - 12, round(pitch * 0.6))
            if grp:
                learn(templates, _glyphs(px, grp[0][0], grp[0][1], y0, y1), t)
    for l in lines:
        t = txt(l)
        if re.fullmatch(r"\d{1,2}", t) and l["y"] > hy + 15 and head["kills"]["x"] - 80 < l["x"] < right:
            y0, y1 = band(l["y"])
            for a, b in _runs(_ink_cols(px, l["x"] - 6, right, y0, y1), l["x"] - 6, round(pitch * 0.6))[:1]:
                learn(templates, _glyphs(px, a, b, y0, y1), t)
    if sum(ch.isdigit() for ch in templates) < 6:
        return None

    # rank column: leading numbers at the far left of the rows
    nums = [(l["x"], l["y"], int(m.group(1)), m.group(2)) for l in lines
            if l["y"] > hy + 15 and l["x"] < cash_x - 200 and (m := LEAD_NUM.match(txt(l)))]
    rank_x = min((x for x, *_ in nums), default=None)
    # the columns sit in the same place on every row; your own row's gold outline
    # inks every column, so it borrows the layout the plain rows agree on
    grps = {y: _runs(_ink_cols(px, cash_x - 12, right, *band(y)), cash_x - 12, round(pitch * 0.6)) for y in rows_y}
    plain = [g for g in grps.values() if len(g) == 5]
    layout = [(min(g[i][0] for g in plain), max(g[i][1] for g in plain)) for i in range(5)] if plain else None
    rows = []
    for y in rows_y:
        y0, y1 = band(y)
        near = lambda l: y0 - 4 <= l["y"] <= y1 - 6
        row = {"y": y}
        for x, ly, n, rest in nums:
            if not (y0 - 4 <= ly <= y1 - 6):
                continue
            if rank_x is not None and x <= rank_x + 12:
                row["rank"] = n
                if rest and not rest.isdigit():
                    row["name"] = rest
            elif "level" not in row:
                row["level"] = n
                if rest:
                    row["name"] = rest
        for l in lines:
            if near(l) and (rank_x or 0) + 40 < l["x"] < cash_x - 200 and "name" not in row and not LEAD_NUM.match(txt(l)):
                row["name"] = txt(l)
        grp = grps[y]
        if len(grp) != 5 and layout:
            grp = layout
            y0, y1 = y0 + round(0.12 * pitch), y1 - round(0.12 * pitch)  # inside the outline
        if len(grp) == 5:
            gl = [_glyphs(px, a, b, y0, y1) for a, b in grp]
            height = max((g["h"] for gs in gl for g in gs), default=0)
            # OCR is right whenever it read a cell at all; the glyph shapes fill the gaps
            said = lambda a, b, rx: next((txt(l).replace(" ", "") for l in lines if near(l) and a - 10 <= l["x"] <= b
                                          and rx.fullmatch(txt(l).replace(" ", ""))), None)
            c = said(*grp[0], CASH_TEXT)
            row["cash"] = int(re.sub(r"\D", "", c)) if c else _number(templates, gl[0], height, cash=True)
            for k, (a, b), gs in zip(STATS, grp[1:], gl[1:]):
                n = said(a, b, re.compile(r"\d{1,3}"))
                row[k] = int(n) if n else _number(templates, gs, height)
        rows.append(row)
    # ranks run in order down the list, so one read rank places every row
    known = [(i, r["rank"]) for i, r in enumerate(rows) if "rank" in r]
    if known:
        votes = {}
        for i, rk in known:
            votes[rk - i] = votes.get(rk - i, 0) + 1
        base = max(votes, key=votes.get)
        for i, r in enumerate(rows):
            r["rank"] = base + i

    def mine(r):
        n = re.sub(r"^[\[({].*?[\])}]\s*", "", r.get("name", "")).lower()
        me = name.lower()
        return bool(me) and (me in r.get("name", "").lower() or difflib.SequenceMatcher(None, n, me).ratio() >= 0.75)

    me = next((i for i, r in enumerate(rows) if mine(r)), None)
    players = None
    for l in lines:
        if re.search(r"PLAYERS|PLNERS", txt(l), re.I) and l["y"] < hy - 20:
            for m in lines:
                if re.fullmatch(r"\d{1,3}", txt(m)) and abs(m["x"] - l["x"]) < 60 and 0 < m["y"] - l["y"] < 50:
                    players = int(txt(m))
    if players is None:  # the column header sits on the header row; the count box above it
        for m in lines:
            if re.fullmatch(r"\d{1,3}", txt(m)) and m["x"] > head["revives"]["x"] - 40 and hy - 110 < m["y"] < hy - 30:
                players = int(txt(m))
    title = next((txt(l) for l in lines if hy - 140 < l["y"] < hy - 50 and l["x"] < cash_x
                  and re.fullmatch(r"[A-Z][A-Z ]{3,}", re.sub(r"^\W*\w?\s+", "", txt(l)) or "")), "")
    title = re.sub(r"^\W*\w?\s+(?=[A-Z]{3})", "", title)
    team = None
    if me is not None and rank_x is not None:
        # your team's icon sits between your rank and your level
        y = rows[me]["y"]
        team = faction(px, round(rank_x + 0.3 * pitch), round(rank_x + 1.8 * pitch),
                       round(y - 0.35 * pitch), round(y + 0.35 * pitch))
    for r in rows:
        r.pop("y", None)
    return {"view": title.title() or None, "players": players, "rows": rows, "team": team,
            "me": rows[me] if me is not None else None,
            "above": rows[me - 1] if me else None, "below": rows[me + 1] if me is not None and me + 1 < len(rows) else None}
