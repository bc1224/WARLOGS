"""Read-only data source: the wardogstracker.gg public API. It never touches the running game."""
import difflib
import json
import os
import urllib.request

BASE = "https://wardogstracker.gg/api/v1"
API = BASE + "/players/{}"
UA = "wardogs-local-tracker/1.0 (personal use; data from WARDOGS Tracker)"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_stats(steam_id, timeout=15):
    """Current synced stats for a player, or None if not synced/private."""
    player = _get(API.format(steam_id), timeout).get("player") or {}
    stats = player.get("stats")
    if stats:
        stats = dict(stats, name=player.get("name"), rank=player.get("leaderboardRank"),
                     players=player.get("leaderboardPlayers"))
    return stats


def parse_unlocks(body):
    """Flatten the /unlocks response. Items the site can't match keep their game id."""
    items = []
    for u in (body.get("unlocks") or {}).get("items") or []:
        info = u.get("item") or {}
        items.append({"game_id": u["gameId"], "level": u.get("level"),
                      "name": info.get("name") or u["gameId"].rsplit(".", 1)[-1],
                      "grp": info.get("group") or "other",
                      "url": info.get("url"), "image": info.get("imageUrl")})
    return {"synced_at": body.get("syncedAt"), "items": items,
            "value": (body.get("unlockValue") or {}).get("cash")}


def fetch_unlocks(steam_id, timeout=15):
    return parse_unlocks(_get(API.format(steam_id) + "/unlocks", timeout))


# The full maps, as 512 px tiles: zoom z has 2^z x 2^z of them across the 16.384 km
# map, row 0 at the top (north). Game coordinates (100 m units) start at the bottom left.
TILES = "https://wardogstracker.gg/map-tiles/{}/{}/{}_{}.webp"
MAPS = ("bakurani", "ozeti", "zestafona")
TILE_ZOOM = {"bakurani": 4, "ozeti": 4, "zestafona": 5}  # the sharpest zoom each map has
TILE_MAX = 2 * 1024 * 1024


def map_key(name):
    """The tile name of a map read off a results screen ("Akurani" is Bakurani), or None."""
    got = difflib.get_close_matches(str(name or "").strip().lower(), MAPS, n=1, cutoff=0.7)
    return got[0] if got else None


def map_tile(ddir, key, z, x, y, fetch, timeout=15):
    """One map tile's webp bytes: from the copy kept in data/maptiles, else downloaded
    (when fetch is on) and kept. None if it isn't there or can't be had."""
    if key not in MAPS or not (0 <= z <= TILE_ZOOM[key] and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return None
    p = os.path.join(ddir, "maptiles", key, str(z), f"{x}_{y}.webp")
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        pass
    if not fetch:
        return None
    try:
        req = urllib.request.Request(TILES.format(key, z, x, y), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(TILE_MAX + 1)
    except Exception:
        return None
    if len(body) > TILE_MAX or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
        return None
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".part", "wb") as f:
            f.write(body)
        os.replace(p + ".part", p)
    except OSError:
        pass
    return body


def pull_api(store, steam_id, at):
    """Store a new stats sync and its unlocks. Unlocks are only fetched when the
    sync changed (or none are stored yet), so a poll is usually one request.
    Returns (new_sync, names of new or upgraded unlocks)."""
    stats = fetch_stats(steam_id)
    if not stats:
        return False, []
    known = store.has_snapshot(stats.get("syncedAt"))
    if known and store.unlock_count():
        return False, []
    unl = fetch_unlocks(steam_id)
    if unl["value"] is not None:
        stats["unlockValue"] = unl["value"]
    new = store.add_snapshot(stats, at)
    if stats.get("rank"):
        try:  # the players just above and below you on the level leaderboard
            off = max(0, stats["rank"] - 4)
            board = _get(f"{BASE}/leaderboard?sort=level&limit=7&offset={off}")
            store.set_meta("leaderboard", {"total": board.get("total"), "players": board.get("players") or []})
        except Exception:
            pass
    return new, store.sync_unlocks(unl, unl["synced_at"], at)


def pull_status(store):
    """Game and login server status (WARDOGS Tracker's /status)."""
    s = _get(BASE + "/status")
    store.set_meta("server_status", {k: s.get(k) for k in ("overall", "servers", "login", "playersHourly")})
