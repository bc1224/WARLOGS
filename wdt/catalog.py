"""Item catalog (unlock level, unlock cost, loadout price, role) read from the public
wardogstracker.gg database pages. Fetched about once a week, slowly, in the background."""
import html
import logging
import re
import time
import urllib.request

from .sources import UA

SITE = "https://wardogstracker.gg"
CATEGORIES = ("equipment", "throwables", "vests", "armor", "helmets", "backpacks", "attachments",
              "explosives", "buildables", "supplies", "vehicles", "ammunition")
MAX_AGE = 7 * 86400
ROLE_NAMES = {"infantry": "assault"}
log = logging.getLogger("wdt")

LEVEL_RE = re.compile(r"(?:Level requirement|Unlocks at) \| LVL (\d+) (\w+)")
COST_RE = re.compile(r"Unlock cost \| \$([\d,]+)")
PRICE_RE = re.compile(r"(?:\| Price \| |· )\$([\d,]+)(?: \||\s*·)")
OWNED_RE = re.compile(r"Unlocked by \| ([\d,]+) \|? ?of \|? ?([\d,]+)")
CAT_RE = re.compile(r"Database \| › \| ([^|]+?) \| › \| ([^|]+?) \|")


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def page_text(body):
    """Visible text of a page, cells joined with ' | '."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " | ", t))
    return re.sub(r"(\s*\|\s*)+", " | ", t)


def _num(s):
    return int(s.replace(",", ""))


def parse_item(text, url=""):
    """One item page -> {name, category, level, role, cost, price, owned_pct}, or None.
    The current values sit after the changelog, so take the last level/cost pair."""
    cat = CAT_RE.search(text)
    if not cat:
        return None
    levels = list(LEVEL_RE.finditer(text))
    item = {"name": cat.group(2).strip(), "category": cat.group(1).strip().lower(), "url": url,
            "level": None, "role": None, "cost": None, "price": None, "owned_pct": None}
    if levels:
        lv = levels[-1]
        item["level"] = int(lv.group(1))
        role = lv.group(2).lower()
        item["role"] = ROLE_NAMES.get(role, role)
        cost = COST_RE.search(text, lv.end())
        if cost:
            item["cost"] = _num(cost.group(1))
    price = PRICE_RE.search(text)
    if price:
        item["price"] = _num(price.group(1))
    owned = OWNED_RE.search(text)
    if owned and _num(owned.group(2)):
        item["owned_pct"] = round(100 * _num(owned.group(1)) / _num(owned.group(2)))
    return item


def item_links(body, prefix):
    return sorted(set(re.findall(r'href="(%s/[a-z0-9-]+)"' % re.escape(prefix), body)))


def fetch_catalog(pause=0.5):
    links = [l for l in item_links(_get(SITE + "/weapons"), "/weapons") if not l.endswith(("/compare", "/tier-list"))]
    for cat in CATEGORIES:
        try:
            links += item_links(_get(f"{SITE}/database/{cat}"), f"/database/{cat}")
        except Exception as e:
            log.warning("catalog list %s failed: %s", cat, e)
        time.sleep(pause)
    items = []
    for link in links:
        try:
            it = parse_item(page_text(_get(SITE + link)), SITE + link)
            if it and it["level"] is not None:
                items.append(it)
        except Exception as e:
            log.warning("catalog item %s failed: %s", link, e)
        time.sleep(pause)
    return items


def refresh_catalog(store, now=None, force=False):
    """Refetch when the stored catalog is older than a week. Returns the item count, or None if skipped."""
    now = now or time.time()
    cur = store.get_meta("catalog") or {}
    if not force and cur.get("fetched", 0) > now - MAX_AGE and cur.get("items"):
        return None
    items = fetch_catalog()
    if items:  # keep the old catalog if the site was unreachable
        store.set_meta("catalog", {"fetched": now, "items": items})
        log.info("catalog: %d items", len(items))
    return len(items)
