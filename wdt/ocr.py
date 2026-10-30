"""Windows OCR on screenshots, and pulling XP / cash numbers out of the text."""
import difflib
import json
import os
import re
import subprocess
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  r"System32\WindowsPowerShell\v1.0\powershell.exe")
CREATE_NO_WINDOW = 0x08000000

NUM = r"\d{1,3}(?:[,.]\s?\d{3})+|\d+"
XP_RES = [re.compile(rf"([+-]?)\s*({NUM})\s*XP\b", re.I),
          re.compile(rf"\bXP\b\s*:?\s*([+-]?)\s*({NUM})", re.I)]
CASH_RE = re.compile(rf"([+-]?)\s*\$\s*([+-]?)\s*({NUM})")
SPEND_WORDS = re.compile(r"cost|spent|spend|loss|lost|repair|fee|penalt", re.I)
# words that show up on a post-match results screen but not on the HUD or menus
END_WORDS = re.compile(r"\b(victory|defeat|winner|winners|won|draw|match (over|complete|summary|results?)"
                       r"|round (over|complete)|results?|placement|placed|1st|2nd|3rd|mvp"
                       r"|rewards?|payout|earned|next round)\b", re.I)
# a role card on the between-rounds Progression tab: "XP 19,597 22,500" (before, after)
PROGRESS_RE = re.compile(rf"\bXP\s*({NUM})\s*/?\s*({NUM})\s*$", re.I)


class OcrWorker:
    """One long-lived PowerShell process, so each read costs ~0.1 s, not ~0.8 s."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        self.proc = subprocess.Popen(
            [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             os.path.join(HERE, "ocr.ps1"), "-Serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)

    def read(self, image_path):
        """Returns [{'text', 'x', 'y'}] lines, or raises on failure."""
        # WinRT file APIs need a clean absolute path with backslashes
        image_path = os.path.abspath(os.path.normpath(image_path))
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.proc.stdin.write(image_path + "\n")
            self.proc.stdin.flush()
            reply = self.proc.stdout.readline()
        if not reply:
            self.stop()
            raise RuntimeError("OCR process exited")
        lines = json.loads(reply, strict=False)  # OCR text can contain raw control characters
        if isinstance(lines, dict):
            raise RuntimeError(lines.get("error", "OCR failed"))
        for line in lines:
            line["text"] = latin(line.get("text") or "")
        return lines

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.close()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None


# Cyrillic letters drawn like Latin ones, which a non-English recognizer puts in
# English words: "ВОNUS" -> "BONUS"
LOOKALIKE = str.maketrans("АВЕКМНОРСТХУаеорсухі", "ABEKMHOPCTXYaeopcyxi")
CYRILLIC = r"[Ѐ-ӿ]"
CYRILLIC_WORD = re.compile(rf"\w*{CYRILLIC}\w*")


def latin(text):
    """Swap look-alike Cyrillic letters for Latin ones in words that are mixed with
    Latin letters or written only in look-alikes; real Cyrillic chat stays as it is."""
    def fix(m):
        w = m.group(0)
        swapped = w.translate(LOOKALIKE)
        return swapped if re.search(r"[A-Za-z]", w) or not re.search(CYRILLIC, swapped) else w
    return CYRILLIC_WORD.sub(fix, text)


def _text(line):
    return line["text"] if isinstance(line, dict) else str(line)


def _num(text):
    return int(re.sub(r"[,.\s]", "", text))


def parse(lines):
    """Best guess at {xp, cash_earned, cash_spent} from OCR lines.

    Every candidate is kept in 'candidates' so the dashboard can show what
    was read and the user can correct a wrong pick.
    """
    xp, earned, spent = [], [], []
    for line in lines:
        text = _text(line)
        for rx in () if PROGRESS_RE.search(text) else XP_RES:  # role totals, not round XP
            for sign, n in rx.findall(text):
                if sign != "-":
                    xp.append(_num(n))
        for s1, s2, n in CASH_RE.findall(text):
            value = _num(n)
            if "-" in (s1, s2) or SPEND_WORDS.search(text):
                spent.append(value)
            else:
                earned.append(value)
    return {
        "xp": max(xp) if xp else None,
        "cash_earned": max(earned) if earned else None,
        "cash_spent": sum(spent) if spent else None,
        "candidates": {"xp": xp, "cash_earned": earned, "cash_spent": spent},
    }


def role_progress(lines):
    """{role: [XP now, XP for the next level]} from the Progression tab. Each card's
    XP line is paired with the role name above it, left to right."""
    names = [l for l in lines if _text(l).strip().title() in ROLES]
    bars = [l for l in lines if PROGRESS_RE.search(_text(l))]
    if not bars or len(names) != len(bars):
        return {}
    names.sort(key=lambda l: _pos(l)[0])
    bars.sort(key=lambda l: _pos(l)[0])
    out = {}
    for n, b in zip(names, bars):
        m = PROGRESS_RE.search(_text(b))
        out[_text(n).strip().title()] = [_num(m.group(1)), _num(m.group(2))]
    return out


CASH_SUMMARY = (re.compile(r"\b(spent|spend|costs?|expenses?)\b", re.I),
                re.compile(r"\b(gained|earned|made|income|payout)\b", re.I),
                re.compile(r"\bbonus(es)?\b", re.I))
# the big banner when a match ends: "WINS" under the team name, or "VALKYRA WINS"
# (a recognizer for a Cyrillic language reads I as 1: "LONESTAR W1NS")
BANNER_RE = re.compile(r"^\W*(?:[A-Za-z][\w-]{1,20}\s+)?(w[i1]ns|v[i1]ctory|defeat|draw)\W*$", re.I)
# the inventory and looting screens mid-round: item tooltips say "REWARDS + MEDIC XP"
# and the reward feed's "25XP" stays up, so they'd pass for results screens
BAG_RE = re.compile(r"^(?:looting\W*|deceased player|equipment|quick transfer|split stack|toggle weight"
                    r"|drop one|swap / move|.*// quick slot)$", re.I)


def looks_like_end_screen(lines):
    """True for a winner banner, the Progression tab, or an XP figure plus
    results-screen wording."""
    texts = [_text(l) for l in lines if not CHAT_RE.search(_text(l))]
    if any(BANNER_RE.search(t) for t in texts):
        return True
    if sum(bool(BAG_RE.match(t.strip())) for t in texts) >= 2:
        return False
    # the money summary shown first after a round: spent / gained / bonus with $ amounts
    words = " ".join(texts)
    if "$" in words and sum(bool(rx.search(words)) for rx in CASH_SUMMARY) >= 2:
        return True
    if parse(lines)["xp"] is None and not role_progress(lines):
        return False
    return any(END_WORDS.search(_text(l)) for l in lines)


# --- match details: result, winner, score, length, personal stats -----------
RESULT_RE = re.compile(r"\b(victory|defeat|draw)\b|\byou (won|lost|win|lose)\b", re.I)
WINNER_RE = [re.compile(r"^(.{2,40}?)\s+(?:w[i1]ns|won|is victorious)\b", re.I),
             re.compile(r"\bwinn(?:er|ers|ing team)\b\s*:?\s*(.{2,40})$", re.I)]
WINNER_LABEL = re.compile(r"^\s*winn(?:er|ers|ing team)\s*:?\s*$", re.I)
SCORE_RE = re.compile(r"\b(\d{1,3})\s*(?:-|–|:|vs\.?)\s*(\d{1,3})\b", re.I)
CLOCK = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})"
CLOCK_RE = re.compile(rf"(?<![\d:]){CLOCK}(?![\d:])")
HM_RE = re.compile(r"(?:(\d{1,2})\s*h(?:ours?|rs?)?\s*)?(\d{1,3})\s*m(?:in(?:utes?|s)?)?\b", re.I)
TIME_LABEL = re.compile(r"\b(time|duration|length|elapsed)\b", re.I)
STATS = {"kills": r"kills?", "deaths": r"deaths?", "assists": r"assists?",
         "revives": r"revives?", "headshots": r"headshots?", "captures": r"captures?",
         "heals": r"heals?|healing", "repairs": r"repairs?", "damage": r"damage"}
STAT_RES = {k: (re.compile(rf"\b(?:{p})\b\s*:?\s*({NUM})\b", re.I),
                re.compile(rf"\b({NUM})\s+(?:{p})\b", re.I),
                re.compile(rf"^\s*(?:{p})\s*:?\s*$", re.I)) for k, p in STATS.items()}
ROLES = ("Assault", "Medic", "Recon", "Support", "Driver", "Pilot")


def _pos(line):
    return (line.get("x", 0), line.get("y", 0)) if isinstance(line, dict) else (0, 0)


def _beside(lines, label, want):
    """The nearest line that satisfies want(text), to the right on the same row
    or just below the label: how results screens lay out label/value columns."""
    lx, ly = _pos(label)
    best = None
    for l in lines:
        if l is label or not want(_text(l)):
            continue
        x, y = _pos(l)
        if abs(y - ly) <= 12 and x > lx:
            d = x - lx
        elif 0 < y - ly <= 60 and abs(x - lx) <= 80:
            d = 1000 + (y - ly)
        else:
            continue
        if best is None or d < best[0]:
            best = (d, l)
    return _text(best[1]) if best else None


def _duration(text):
    m = CLOCK_RE.search(text)
    if m:
        h, mi, s = (int(g or 0) for g in m.groups())
        return h * 3600 + mi * 60 + s
    m = HM_RE.search(text)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60
    return None


def details(lines):
    """Who won, the score, how long it took and your own stats, as far as the
    results screen shows them. Anything not found stays None/empty."""
    out = {"result": None, "winner": None, "score": None, "duration_s": None, "stats": {}, "map": None}
    texts = [_text(l) for l in lines]
    for line, text in zip(lines, texts):
        m = RESULT_RE.search(text)
        if m and not out["result"]:
            word = (m.group(1) or m.group(2)).lower()
            out["result"] = {"won": "victory", "win": "victory", "lost": "defeat",
                             "lose": "defeat"}.get(word, word)
        if not out["winner"]:
            if WINNER_LABEL.match(text):
                out["winner"] = _beside(lines, line, lambda t: bool(re.search(r"[A-Za-z]", t)))
            else:
                for rx in WINNER_RE:
                    w = rx.search(text)
                    if w and w.group(1).strip().lower() not in ("you", "team"):
                        out["winner"] = w.group(1).strip()
                        break
        if not out["score"]:
            for a, b in SCORE_RE.findall(text):
                a, b = int(a), int(b)
                if max(a, b) == 100 and min(a, b) < 100:
                    out["score"] = f"{a}-{b}"
                    break
        if out["duration_s"] is None and TIME_LABEL.search(text):
            d = _duration(TIME_LABEL.split(text, 1)[-1])
            if d is None:
                near = _beside(lines, line, lambda t: _duration(t) is not None)
                d = _duration(near) if near else None
            out["duration_s"] = d
        for key, (after, before, alone) in STAT_RES.items():
            if key in out["stats"] or "$" in text:  # "-$2,000 repairs" is a cost, not a stat
                continue
            m = after.search(text) or before.search(text)
            if m:
                out["stats"][key] = _num(m.group(1))
            elif alone.match(text):
                near = _beside(lines, line, lambda t: re.fullmatch(rf"\s*({NUM})\s*", t) is not None)
                if near:
                    out["stats"][key] = _num(near.strip())
    if out["duration_s"] is None:  # a lone h:mm:ss is almost certainly the match clock
        for t in texts:
            m = re.search(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])", t)
            if m:
                out["duration_s"] = _duration(m.group(0))
                break
    for t in texts:  # "BAKURANI 45'15.0"N 122'25.08"W" on the results banner
        m = MAP_RE.match(t)
        if m:
            out["map"] = m.group(1).strip().title()
            break
    return out


def mentions(lines, item_names):
    """Roles and owned items named anywhere on screen (loadout, deploy and
    results screens), so a match can show what you were playing with."""
    text = "\n".join(_text(l) for l in lines)
    roles = [r for r in ROLES if re.search(rf"\b{r}\b", text, re.I)]
    items = sorted(n for n in set(item_names) if len(n) >= 3 and
                   re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", text, re.I))
    return {"roles": roles, "items": items}


# --- where the XP and cash came from ------------------------------------------
TOTAL_RE = re.compile(r"\b(total|earned|gained|summary|rewards?|payout|level|rank|xp)\b\s*$|^\s*(total|xp|cash)\b", re.I)
AMOUNT_RE = re.compile(rf"[+-]?\s*\$?\s*[+-]?\s*(?:{NUM})\s*(?:XP\b)?|\$|\bXP\b|[+:•·|]", re.I)


def _amounts(text):
    xp = cash = None
    for rx in XP_RES:
        for sign, n in rx.findall(text):
            xp = (-1 if sign == "-" else 1) * _num(n)
    for s1, s2, n in CASH_RE.findall(text):
        v = _num(n)
        cash = -v if "-" in (s1, s2) or SPEND_WORDS.search(text) else v
    return xp, cash


def _label(text):
    t = re.sub(r"\s+", " ", AMOUNT_RE.sub(" ", text)).strip(" -")
    return t if len(re.findall(r"[A-Za-z]", t)) >= 3 else None


def xp_sources(lines):
    """[{label, xp, cash}] rows like 'Zone Capture +1,200 XP' or a label with its
    value in a column to the right. Totals are left out; same labels merge."""
    rows = {}
    for line in lines:
        text = _text(line)
        xp, cash = _amounts(text)
        if xp is None and cash is None:
            continue
        label = _label(text)
        if label is None:  # value on its own: take the label to its left on the same row
            lx, ly = _pos(line)
            best = None
            for other in lines:
                ox, oy = _pos(other)
                ot = _text(other)
                if other is line or abs(oy - ly) > 12 or ox >= lx or _amounts(ot) != (None, None):
                    continue
                if _label(ot) and (best is None or ox > best[0]):
                    best = (ox, _label(ot))
            label = best and best[1]
        if not label or TOTAL_RE.search(label):
            continue
        row = rows.setdefault(label.lower(), {"label": label, "xp": None, "cash": None})
        if xp is not None:
            row["xp"] = (row["xp"] or 0) + xp
        if cash is not None:
            row["cash"] = (row["cash"] or 0) + cash
    return list(rows.values())


# --- in-match HUD: where you are and which team you're on --------------------
HOT_RE = re.compile(r"\b(hot\s*zone|control\s*zone|capturing|contested|securing)\b", re.I)
FOB_RE = re.compile(r"\b(fob|forward\s+operating\s+base)\b", re.I)
DIST_RE = re.compile(r"\b\d{1,4}\s?m\b", re.I)
TEAM_RES = (re.compile(r"\bteam\s*[:#]?\s*([A-Za-z][\w-]{1,15})\b", re.I),
            re.compile(r"\b([A-Za-z][\w-]{2,15})\s+team\b", re.I))
CHAT_RE = re.compile(r"\[(team|all|local|squad|party|global)\]", re.I)
TEAMMATE_RE = re.compile(r"\bteam\s*mates?\b", re.I)  # "Revived Team Mate" names no team
# screens the HUD reads between rounds: loading into the next round, the server list
ROUND_BREAK_RE = re.compile(r"^\W*(WAITING\s*FOR\s*(THE\s*)?(WORLD\s*TO\s*LOAD|PLAYERS)|JOINING\s*SERVER"
                            r"|SERVER\s*BROWSER|IN\s*SERVER\s*QUEUE)\W*$", re.I)
NOT_TEAM = {"your", "enemy", "the", "our", "their", "friendly", "own", "my", "switch",
            "join", "change", "select", "auto", "other", "opposing", "winning", "losing",
            "mate", "mates", "member", "members"}


def round_break(lines):
    """True on a screen that only shows up between rounds."""
    return any(ROUND_BREAK_RE.search(_text(l)) for l in lines)


def hud_state(lines):
    """Where the in-match HUD says you are: {'hot', 'fob', 'team'}. A label with a
    distance next to it ('Hot Zone 320m') is a waypoint, not where you are."""
    out = {"hot": False, "fob": False, "team": None}
    for line in lines:
        text = _text(line)
        if CHAT_RE.search(text):  # other players' chat, not your HUD
            continue
        away = DIST_RE.search(text)
        out["hot"] |= bool(HOT_RE.search(text)) and not away
        out["fob"] |= bool(FOB_RE.search(text)) and not away
        if out["team"] is None:
            for rx in TEAM_RES:
                m = rx.search(TEAMMATE_RE.sub(" ", text))
                if m and m.group(1).lower() not in NOT_TEAM:
                    out["team"] = m.group(1)
                    break
    return out


# --- cash in the top-right corner: this round's profit, then your balance ------
# OCR reads the minus as other dashes too ("_ $4,980" for "-$4,980")
MONEY_RE = re.compile(rf"([-_~–—−=]?)\s*\$\s*([-–—−]?)\s*({NUM})")
DIGIT_LOOKALIKES = str.maketrans("OoQDIl|", "0000111")
CUT_OFF = re.compile(r"\s?[,.]\s?(\S|$)")  # "-$8,52" for "-$8,520": the number didn't read whole


def top_bar(lines, w, h):
    """{'net': profit this round so far, 'balance': money you have} from the top
    bar. With two amounts the left one is the round's profit; it is hidden until
    something changes, so a lone amount is the balance unless it is negative."""
    vals = []
    for line in lines:
        x, y = _pos(line)
        if y > h * 0.06 or x < w * 0.7:
            continue
        text = _text(line)
        text = re.sub(r"\$.*", lambda d: d.group(0).translate(DIGIT_LOOKALIKES), text)
        m = MONEY_RE.search(text)
        if m and not CUT_OFF.match(text, m.end()):
            v = _num(m.group(3))
            vals.append((x, -v if m.group(1) + m.group(2) else v))
    vals.sort()
    # menus (lobby, server browser) show balance, gold and your Wardog level up there,
    # with the level as a bare number at the far right; a match never shows that
    menu = any(re.fullmatch(r"\d{1,3}", _text(l).strip()) for l in lines
               if _pos(l)[1] <= h * 0.06 and _pos(l)[0] > w * 0.93)
    if len(vals) >= 2:  # the results screen shows the round's profit beside the balance
        return {"net": vals[-2][1], "balance": vals[-1][1], **({"menu": True} if menu else {})}
    if menu:
        return {"balance": vals[0][1], "menu": True} if vals else {"menu": True}
    if vals:  # profit sits left of the balance (x ~1700 vs ~1815 of 1920)
        x, v = vals[0]
        return {"net": v} if v < 0 or x < w * 0.92 else {"balance": v}
    return {}


# --- the gun in your hands: the nameplate "KH-2002 // 5.56MM // SN 2TMYJP5" ----
PLATE_RE = re.compile(r"^\W*([A-Za-z0-9][\w\-. ]{1,24}?)\s*//\s*[^/]{2,16}//\s*S\W?[NM]\b", re.I)
GUNS = ("AK74", "Galil", "M4", "FAL", "SKS", "Mosin Nagant", "AMR 50", "SV98", "KH-2002", "GGX 18",
        "MP5", "M1911", "Judge", "RPG-7", "Glock 18", "PKM", "RPK", "Saiga", "Benelli M4", "UMP45",
        "M500", "A-91", "T-21")
# held items with a nameplate like a gun's; kept out of the gun stats
GEAR = ("Range Finder", "Infrared Range Finder", "Monocular", "Binoculars", "Defibrillator",
        "Field Resuscitator", "Medical Bag", "Individual First Aid Kit", "Adrenaline Pen",
        "Large Hammer", "Repair Tool", "C4 Charge", "Remote Detonator")
# letters and digits the nameplate font makes OCR swap: 5/S/E, 0/O, 1/I/L, 8/B, 2/Z, 6/G
CONFUSED = str.maketrans("5e01l8z6", "ssoiibzg")


def _squash(s):
    return re.sub(r"[\s\-_.]", "", s).lower().translate(CONFUSED)


def gun_name(raw, known=(), gear=()):
    """A nameplate's name as a known gun, None for equipment, or as read if it's neither."""
    guns = list(dict.fromkeys(list(GUNS) + list(known)))
    keyed = {_squash(n): n for n in guns}
    key = _squash(raw)
    hit = difflib.get_close_matches(key, list(keyed), n=1, cutoff=0.75)
    if not hit and 3 <= len(key) <= 5:
        # short names: one wrong character ('MPE') is still that gun if no other is as close
        near = [k for k in keyed if len(k) == len(key) and sum(a != b for a, b in zip(k, key)) == 1]
        hit = near if len(near) == 1 else None
    if hit:
        return keyed[hit[0]]
    if difflib.get_close_matches(key, [_squash(n) for n in list(GEAR) + list(gear)], n=1, cutoff=0.75):
        return None
    return raw.upper() if len(raw) <= 8 else raw.title()


def weapon(lines, known=(), gear=()):
    """Name of the gun on the HUD nameplate, matched to a known name when OCR is
    a letter or two off ('MOSIUNAGANT' -> 'Mosin Nagant'). None for equipment."""
    for line in lines:
        m = PLATE_RE.search(_text(line))
        if m:
            return gun_name(m.group(1).strip(), known, gear)
    return None


# --- the money screen right after a round: spent, gained, bonus ---------------
SUMMARY_LABELS = {"spent": re.compile(r"\b(spent|spend|costs?|expenses?)\b", re.I),
                  "gained": re.compile(r"\b(gained|earned|made|income|payout)\b", re.I),
                  "bonus": re.compile(r"\bbonus(es)?\b", re.I)}
AMOUNT_ONLY = re.compile(rf"[+-]?\s*\$\s*[+-]?\s*({NUM})")


def cash_summary(lines):
    """{'spent', 'gained', 'bonus'} amounts from the post-round money screen, each
    on the label's line or in the column beside/below it."""
    out = {}
    for key, rx in SUMMARY_LABELS.items():
        for line in lines:
            text = _text(line)
            if CHAT_RE.search(text) or not rx.search(text):
                continue
            m = AMOUNT_ONLY.search(text)
            if not m:
                other = _beside(lines, line, lambda t: bool(AMOUNT_ONLY.search(t)))
                m = other and AMOUNT_ONLY.search(_text(other))
            if m:
                out[key] = _num(m.group(1))
                break
    return out


# --- the map name on the results banner: "BAKURANI 45'15.0"N 122'25.08"W" -----
MAP_RE = re.compile(r"^\W*([A-Za-z][A-Za-z\-' ]{2,24}?)\s+\d{1,3}\s*['’°]\s*\d")


# --- reward feed under the top-right cash: "CONTROL ZONE PRESENCE +$200" ------
FEED_RE = re.compile(rf"^\W*([A-Za-z][A-Za-z \-'/.,]*?[A-Za-z])\s*\W*?\s*([+*\-−]?)\s*(\$?)\s*({NUM})\s*(XP)?\W*$", re.I)
FEED_LABELS = ("Control Zone Presence", "Hot Zone Presence", "Control Zone Entered", "Hot Zone Entered",
               "Player Assist", "Deployed Player Assist", "Revived Player Assist", "Healed Teammate",
               "Supplies Deposited", "Waited To Be Revived", "Be Revived", "Vehicle Refuelling", "Bribe",
               "Kill", "Headshot", "Revive", "Resupply", "Capture", "Target Spotted", "Target Spotted Refreshed",
               "Spotted Target Destroyed", "Vehicle Destroyed Assist", "Artillery Kill", "Kill Assist",
               "Supplies Delivered", "Passenger Survived", "Tactical Deployment", "Defender", "Vehicle Repairing",
               "Fob Door Closed", "Revived Teammate", "Applied Stim", "Spotted Vehicle Destroyed", "Target Destroyed")
# XP entries read as "TARGET SPOTTED 25XP O": the number runs into XP, an icon follows it, and
# its digits can read as letters ("soxp" for 50XP); a lone "150XP" lost its label off the edge
XP_TAIL = re.compile(r"(?<![A-Za-z])([0-9OoIlSsBZ]{1,4})\s?XP\s*[O0o+*]?\s*$", re.I)
XP_LOOKALIKES = str.maketrans("OoIlSsBZ", "00115582")
LONE_XP = re.compile(r"^\W*(\d{1,4})\s?XP\W*$", re.I)
ZONE_REWARD = re.compile(r"zone|presence", re.I)  # paid every few seconds while you're in the zone


def _feed_label(raw, cash=None):
    squash = lambda s: re.sub(r"[^A-Z]", "", s.upper())
    sq = squash(raw)
    known = {squash(k): k for k in FEED_LABELS}
    if sq in known:
        return known[sq]
    hit = difflib.get_close_matches(sq, list(known), n=1, cutoff=0.8)
    if hit:
        return known[hit[0]]
    for k in sorted(known, key=len, reverse=True):  # "CJERYIYEPPLAYER ASSIST" -> Player Assist
        if len(k) >= 5 and sq.endswith(k):
            return known[k]
    if (sq.endswith("PRESENCE") or sq.endswith("SENCE") or "PRESEN" in sq  # "L ZONE PRESENCE", "NEVESENCE", "GLZPNEPRESENC"
            or "ZONE" in sq and difflib.SequenceMatcher(None, sq[-8:], "PRESENCE").ratio() >= .6):  # "IMOTZONE PQESENGE"
        return "Hot Zone Presence" if "HOT" in sq or "OTZONE" in sq else "Control Zone Presence"
    ends = [k for k in known if len(sq) >= 8 and k.endswith(sq)]  # the start was cut off
    if ends:  # "DEPOSITED" is Supplies Deposited; "TEAMMATE" could be healed or revived
        return known[ends[0]] if len(ends) == 1 else raw.strip().title()
    if sq.startswith("TIP"):  # "TIP - <player>": a tip from a teammate
        return "Tip"
    for k in known:  # "DEPLOY-I ASSIST": the middle got lost
        if len(k) > 10 and len(sq) >= 10 and sq[:5] == k[:5] and sq[-5:] == k[-5:]:
            return known[k]
    if cash is not None and 0 < cash <= 50:  # a few dollars under a teammate's name: a tip that lost its "TIP -"
        return "Tip"
    # anything else is kept only if it reads as words; a smeared line
    # ("Lennayyyy", "P A Imargencv", "E Ef Elling") is just Other
    words = re.findall(r"[A-Za-z]+", raw)
    if len(words) >= 2 and all(len(w) >= 3 for w in words) and all(re.search(r"[aeiouy]", w, re.I) for w in words):
        return " ".join(w.capitalize() for w in words)
    return "Other"


reward_label = _feed_label


def reward_feed(lines):
    """Entries of the reward feed: [{label, cash, xp}]. OCR reads '+' as '*'."""
    out = []
    for line in lines:
        text = _text(line)
        text = XP_TAIL.sub(lambda x: x.group(1).translate(XP_LOOKALIKES) + "XP", text)
        if (lone := LONE_XP.match(text)) and not CHAT_RE.search(text):
            out.append({"label": "Other", "cash": None, "xp": int(lone.group(1))})
            continue
        m = FEED_RE.match(text)
        if not m or CHAT_RE.search(text) or not (m.group(3) or m.group(5)):
            continue
        n = _num(m.group(4))
        if m.group(3):
            cash = -n if m.group(2) in "-−" and m.group(2) else n
            out.append({"label": _feed_label(m.group(1), cash), "cash": cash, "xp": None})
        else:
            out.append({"label": _feed_label(m.group(1)), "cash": None, "xp": n})
    return out


# --- kill feed on the left: "<killer> <gun icon> <victim>", clan tags in brackets -
TAG_RE = re.compile(r"[\[(][A-Za-z0-9]{1,6}[\])]")


def _clean_name(text):
    words = [w.strip(".,'\"`•*|") for w in TAG_RE.sub(" ", text).split()]
    return " ".join(w for w in words if len(w) > 1 or w.isdigit()).strip()


DISTANCE_RE = re.compile(r"^\W*\d{1,4}\s*m(?![a-z]{2})\W*", re.I)  # "1220m) Hydra", "(82mY"
DISTANCE_TAIL = re.compile(r"[\s(\[]+\d{1,4}\s*m\W*$", re.I)  # "White Night 1598m]"


def player_name(text):
    """A name from the kill feed without the kill distance stuck to it; '' if nothing is left."""
    text = DISTANCE_TAIL.sub("", DISTANCE_RE.sub("", text or "")).strip()
    return text if len(re.sub(r"\W", "", text)) >= 2 and re.search(r"[A-Za-z]", text) else ""


def kill_feed(lines, name):
    """[('kill', victim) | ('death', killer)] for feed lines that name you."""
    if not name:
        return []
    rx = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", re.I)
    out = []
    for line in lines:
        text = _text(line)
        m = rx.search(text)
        if not m or CHAT_RE.search(text) or text[m.end():].lstrip().startswith(":"):
            continue
        before, after = _clean_name(text[:m.start()]), _clean_name(text[m.end():])
        if not before and after:
            out.append(("kill", player_name(after)))
        elif before:
            out.append(("death", player_name(before)))
    return out


# --- other screens: downed, the vendor, promotions in chat ------------------
def downed(lines):
    """'down' while bleeding out, 'reviving' while a teammate picks you up."""
    texts = " | ".join(_text(l) for l in lines if not CHAT_RE.search(_text(l)))
    if re.search(r"\bbeing revived\b", texts, re.I):
        return "reviving"
    if re.search(r"\b(give up|bleeding out)\b", texts, re.I):
        return "down"
    return None


def vendor_item(lines):
    """The item selected in an open vendor: the title just above its 'TYPE // SLOT' line."""
    texts = [_text(l) for l in lines]
    if not any(re.search(r"\bvendor\b", t, re.I) for t in texts):
        return None
    for line in lines:
        t = _text(line).strip()
        if "//" in t or not re.fullmatch(r"[A-Z][A-Z0-9 \-'.]{3,40}", t):
            continue
        x, y = _pos(line)
        for other in lines:
            ot = _text(other)
            ox, oy = _pos(other)
            if "//" in ot and "vendor" not in ot.lower() and 0 < oy - y <= 40 and abs(ox - x) < 60:
                return t.title()
    return None


PROMO_RE = re.compile(r"promoted\s+t\w\s+([A-Za-z]+)\s+lv[l1i|]?\s*(\d+)?", re.I)


def promotions(lines):
    """[(role, level or None)] from 'PROMOTED TO ASSAULT LVL 9' chat lines."""
    out = []
    for line in lines:
        m = PROMO_RE.search(_text(line))
        if m and m.group(1).title() in ROLES:
            out.append((m.group(1).title(), int(m.group(2)) if m.group(2) else None))
    return out
