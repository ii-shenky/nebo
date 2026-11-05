"""Картинки из интернета: фото бортов (planespotters.net), логотипы авиакомпаний, флаги.

Всё загружается в фоне: get() сразу возвращает то, что есть, и ставит загрузку в очередь.

Условия planespotters (https://www.planespotters.net/photo/api), которые здесь соблюдаются:
- User-Agent с названием программы и контактом;
- JSON-ответы кешируются не дольше 24 часов;
- сами фото на диск не пишутся и держатся в памяти, только пока на экране (retain());
- рядом с фото на экране — имя фотографа и QR-код со ссылкой на страницу снимка (это в ui.py).
"""
import html
import io
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import __version__
from .sources import USER_AGENT

log = logging.getLogger(__name__)

PENDING, FOUND, MISSING = "pending", "found", "missing"


def http_get(url: str, timeout: float, user_agent: str = USER_AGENT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class AsyncLookup:
    """Кеш «ключ → значение» в памяти с фоновой загрузкой в одном потоке."""

    def __init__(self, name: str, fetch, ttl_found: float, ttl_missing: float,
                 retry_after: float = 120, on_update=None, pause: float = 0.3):
        self.name = name
        self.fetch = fetch  # key -> value | None (None = точно нет); исключение = временная ошибка
        self.ttl_found, self.ttl_missing, self.retry_after = ttl_found, ttl_missing, retry_after
        self.on_update = on_update
        self.pause = pause
        self.lock = threading.Lock()
        self.cache: dict = {}
        self.retry_at: dict = {}
        self.queued: set = set()
        self.queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, name=name, daemon=True).start()

    def get(self, key):
        if key is None:
            return MISSING, None
        now = time.time()
        with self.lock:
            entry = self.cache.get(key)
            if entry and now - entry[0] < (self.ttl_found if entry[1] is not None else self.ttl_missing):
                return (FOUND, entry[1]) if entry[1] is not None else (MISSING, None)
            if key not in self.queued and now >= self.retry_at.get(key, 0):
                self.queued.add(key)
                self.queue.put(key)
            if entry and entry[1] is not None:
                return FOUND, entry[1]
            return PENDING, None

    def drop(self, keep: set):
        """Забыть всё, кроме keep (для фото: держать в памяти только то, что на экране)."""
        with self.lock:
            for key in [k for k in self.cache if k not in keep]:
                del self.cache[key]

    def _worker(self):
        while True:
            key = self.queue.get()
            try:
                value = self.fetch(key)
                with self.lock:
                    self.cache[key] = (time.time(), value)
            except Exception as e:
                log.warning("%s %s: %s", self.name, key, e)
                with self.lock:
                    self.retry_at[key] = time.time() + self.retry_after
            finally:
                with self.lock:
                    self.queued.discard(key)
            if self.on_update:
                self.on_update()
            time.sleep(self.pause)


class DiskImages:
    """Картинки с постоянным адресом, которые можно хранить на диске (логотипы, флаги)."""

    def __init__(self, name: str, url: str, cache_dir: Path, timeout: float, on_update=None):
        self.url = url
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.lookup = AsyncLookup(name, self._fetch, ttl_found=30 * 86400, ttl_missing=86400,
                                  on_update=on_update)

    def _fetch(self, key: str):
        path = self.dir / f"{key}.png"
        missing = self.dir / f"{key}.none"
        if missing.exists() and time.time() - missing.stat().st_mtime < 7 * 86400:
            return None
        if not path.exists():
            try:
                data = http_get(self.url.format(key=key), self.timeout)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    missing.touch()
                    return None
                raise
            path.write_bytes(data)
        return Image.open(path).convert("RGBA")

    def get(self, key: str | None):
        return self.lookup.get(key)


@dataclass
class Photo:
    image: Image.Image
    photographer: str
    link: str


class Photos:
    def __init__(self, contact: str, timeout: float, on_update=None):
        self.user_agent = f"nebo/{__version__} (Raspberry Pi flight board; +mailto:{contact})"
        self.timeout = timeout
        # описание снимка (JSON) — можно держать до 24 часов
        self.meta = AsyncLookup("photo-meta", self._fetch_meta, ttl_found=20 * 3600,
                                ttl_missing=20 * 3600, on_update=on_update, pause=1.0)
        # сами снимки — только в памяти и только пока на экране
        self.images = AsyncLookup("photo-image", self._fetch_image, ttl_found=3600,
                                  ttl_missing=600, on_update=on_update, pause=0.5)

    def _fetch_meta(self, hex_id: str):
        data = json.loads(http_get(f"https://api.planespotters.net/pub/photos/hex/{hex_id}",
                                   self.timeout, self.user_agent))
        photos = data.get("photos") or []
        if not photos:
            return None
        p = photos[0]
        thumb = p.get("thumbnail_large") or p.get("thumbnail")
        return {"src": thumb["src"], "photographer": p.get("photographer") or "?", "link": p["link"]}

    def _fetch_image(self, hex_id: str):
        state, meta = self.meta.get(hex_id)
        if state != FOUND:
            raise RuntimeError("нет описания снимка")
        img = Image.open(io.BytesIO(http_get(meta["src"], self.timeout, self.user_agent))).convert("RGB")
        return Photo(img, meta["photographer"], meta["link"])

    def get(self, hex_id: str | None):
        """(состояние, Photo). Запрашивать только для самолёта, который сейчас на экране."""
        state, meta = self.meta.get(hex_id)
        if state != FOUND:
            return state, None
        return self.images.get(hex_id)

    def retain(self, hexes: set):
        self.images.drop(hexes)


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


class _Commons:
    """Общее для фото с Wikimedia: User-Agent с адресом проекта (правила Wikimedia), описание
    снимков на диске, чтобы не спрашивать заново после перезапуска.

    Снимки под свободными лицензиями (CC BY, CC BY-SA…): показывать можно с автором и лицензией.
    Браузер грузит превью прямо с upload.wikimedia.org, сервер хранит только описание.
    """

    def __init__(self, contact: str, timeout: float, cache_path: Path | None):
        self.user_agent = f"nebo/{__version__} (+{contact})"
        self.timeout = timeout
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.saved: dict = {}
        if cache_path and cache_path.exists():
            try:
                self.saved = json.loads(cache_path.read_text())
            except ValueError:
                self.saved = {}

    def _api(self, base: str = COMMONS_API, **params) -> dict:
        params.update(action="query", format="json", formatversion="2")
        url = base + "?" + urllib.parse.urlencode(params)
        return json.loads(http_get(url, self.timeout, self.user_agent))

    def _cached(self, key: str, days_found: float, days_missing: float):
        """(есть в кеше, значение)."""
        with self.lock:
            entry = self.saved.get(key)
        if entry and time.time() - entry["t"] < (days_found if entry["v"] else days_missing) * 86400:
            return True, entry["v"]
        return False, None

    def _save(self, key: str, value):
        with self.lock:
            self.saved[key] = {"t": time.time(), "v": value}
            data = json.dumps(self.saved, ensure_ascii=False)
        if self.cache_path:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(data)
            os.replace(tmp, self.cache_path)

    def _describe(self, files: list):
        """Первый из файлов, который картинка: превью, ссылка, автор, лицензия."""
        q = self._api(titles="|".join(files), prop="imageinfo", iiprop="url|extmetadata|mime",
                      iiurlwidth="640", iiextmetadatafilter="Artist|LicenseShortName|LicenseUrl")
        pages = q.get("query", {}).get("pages", [])
        norm = {n["from"]: n["to"] for n in q.get("query", {}).get("normalized", [])}
        pages = {p["title"]: p for p in pages}
        for title in files:
            ii = (pages.get(norm.get(title, title), {}).get("imageinfo") or [None])[0]
            if not ii or ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            meta = ii.get("extmetadata", {})
            return {
                "src": ii.get("thumburl") or ii["url"],
                "link": ii["descriptionurl"],
                "author": re.sub(r"\s+", " ", _strip_html(meta.get("Artist", {}).get("value"))) or "неизвестен",
                "license": _strip_html(meta.get("LicenseShortName", {}).get("value")),
                "license_url": meta.get("LicenseUrl", {}).get("value"),
            }
        return None


class ShipPhotos(_Commons):
    """Фото судов с Wikimedia Commons: категория «IMO N» (или «MMSI N») → категория судна → снимки."""

    def __init__(self, contact: str, timeout: float, cache_path: Path | None, on_update=None):
        super().__init__(contact, timeout, cache_path)
        self.lookup = AsyncLookup("ship-photo", self._fetch, ttl_found=7 * 86400, ttl_missing=3 * 86400,
                                  on_update=on_update, pause=1.0)

    def _members(self, category: str) -> tuple[list, list]:
        """(файлы, подкатегории) категории."""
        q = self._api(list="categorymembers", cmtitle=category, cmtype="file|subcat", cmlimit="50")
        members = q.get("query", {}).get("categorymembers", [])
        return [m["title"] for m in members if m["ns"] == 6], [m["title"] for m in members if m["ns"] == 14]

    def _fetch(self, key: str):
        hit, value = self._cached(key, 7, 3)
        if hit:
            return value
        kind, number = key.split(":")
        files, subcats = self._members(f"Category:{kind.upper()} {number}")
        ship = None
        if not files and subcats:  # обычно снимки лежат в категории самого судна
            ship = subcats[0]
            files, _ = self._members(ship)
        value = None
        if files:
            # в категорию судна попадают и общие снимки порта — сначала файлы с именем судна
            name = re.sub(r"\s*\(.*\)$", "", (ship or "").removeprefix("Category:")).lower()
            value = self._describe(sorted(files, key=lambda f: (name and name not in f.lower(), f))[:5])
            if value:
                value["ship"] = ship.removeprefix("Category:") if ship else None
        self._save(key, value)
        return value

    def get(self, imo: int | None, mmsi: int | None):
        """(состояние, описание снимка). Сначала по IMO, без него — по MMSI."""
        if imo:
            state, value = self.lookup.get(f"imo:{imo}")
            if state != MISSING or not mmsi:
                return state, value
        return self.lookup.get(f"mmsi:{mmsi}") if mmsi else (MISSING, None)


class RegPhotos(_Commons):
    """Фото самого борта с Wikimedia Commons, когда у planespotters снимка нет: категория
    «4X-EKA (aircraft)» → снимки. В категорию попадают и общие виды с бортом вдали —
    сначала берутся файлы с регистрацией в названии.
    """

    def __init__(self, contact: str, timeout: float, cache_path: Path | None, on_update=None):
        super().__init__(contact, timeout, cache_path)
        self.lookup = AsyncLookup("reg-photo", self._fetch, ttl_found=7 * 86400, ttl_missing=3 * 86400,
                                  on_update=on_update, pause=1.0)

    def _fetch(self, reg: str):
        hit, value = self._cached(reg, 7, 3)
        if hit:
            return value
        # недавно добавленные первыми — на них чаще нынешняя ливрея
        q = self._api(list="categorymembers", cmtitle=f"Category:{reg} (aircraft)", cmtype="file", cmlimit="50",
                      cmsort="timestamp", cmdir="desc")
        files = [m["title"] for m in q.get("query", {}).get("categorymembers", [])]
        compact = reg.replace("-", "").lower()
        files = sorted(files, key=lambda f: compact not in f.replace("-", "").lower())[:10]
        value = self._describe(files) if files else None
        self._save(reg, value)
        return value

    def get(self, reg: str | None):
        """(состояние, описание снимка) по регистрации («4X-EKA»)."""
        return self.lookup.get(reg.upper()) if reg else (MISSING, None)


# Семейства, которые по названию типа из tar1090-db в Википедии не находятся: код ICAO → статья.
TYPE_ARTICLES = {
    **dict.fromkeys(("DH8A", "DH8B", "DH8C", "DH8D"), "De Havilland Canada Dash 8"),
    **dict.fromkeys(("E170", "E75L", "E75S", "E190", "E195"), "Embraer E-Jet family"),
    **dict.fromkeys(("E290", "E295"), "Embraer E-Jet E2 family"),
    **dict.fromkeys(("CRJ1", "CRJ2"), "Bombardier CRJ100/200"),
    **dict.fromkeys(("CRJ7", "CRJ9", "CRJX"), "Bombardier CRJ700 series"),
}


def _title_variants(name: str) -> list[str]:
    """«TECNAM P-2008» → ["Tecnam P-2008", "Tecnam P2008"]; длинные названия — ещё и без хвоста
    («CESSNA 172 Skyhawk» → … "Cessna 172"). Изготовитель в tar1090-db заглавными, в Википедии — нет."""
    words = name.split()
    maker = 0
    while maker < len(words) and words[maker].isalpha():
        words[maker] = words[maker].capitalize()
        maker += 1
    base = " ".join(words)
    variants = []
    for full in (base, re.sub(r"\b([A-Z]{1,2})-(?=\d)", r"\1", base)):
        w = full.split()
        for n in range(len(w), maker, -1):
            if " ".join(w[:n]) not in variants:
                variants.append(" ".join(w[:n]))
    return variants


class TypePhotos(_Commons):
    """Фото такого же типа самолёта — когда у planespotters нет снимка самого борта.

    Статья английской Википедии о типе → её главная картинка (только свободная — PageImages
    по умолчанию отдаёт лишь их) → описание файла на Commons. На странице подписано, что это
    не тот самый борт.
    """

    def __init__(self, contact: str, timeout: float, cache_path: Path | None, on_update=None):
        super().__init__(contact, timeout, cache_path)
        self.lookup = AsyncLookup("type-photo", self._fetch, ttl_found=30 * 86400, ttl_missing=7 * 86400,
                                  on_update=on_update, pause=1.0)

    def _article(self, code: str, name: str):
        """(статья, файл картинки) или (None, None)."""
        titles = [TYPE_ARTICLES[code]] if code in TYPE_ARTICLES else _title_variants(name)
        q = self._api(WIKIPEDIA_API, titles="|".join(titles[:50]), prop="pageimages", piprop="name", redirects="1")
        q = q.get("query", {})
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        pages = {p["title"]: p for p in q.get("pages", []) if not p.get("missing")}
        for t in titles:
            t = norm.get(t, t)
            page = pages.get(redir.get(t, t))
            if page:
                return page["title"], page.get("pageimage")
        # не нашлось по названию — поиск, но статья должна содержать номер модели (иначе это другой тип)
        number = re.search(r"\d{2,}", name)
        if not number:
            return None, None
        q = self._api(WIKIPEDIA_API, generator="search", gsrsearch=titles[-1], gsrlimit="5", gsrnamespace="0",
                      prop="pageimages", piprop="name")
        for page in sorted(q.get("query", {}).get("pages", []), key=lambda p: p["index"]):
            if number.group() in page["title"].replace("-", ""):
                return page["title"], page.get("pageimage")
        return None, None

    def _fetch(self, key: str):
        hit, value = self._cached(key, 30, 7)
        if hit:
            return value
        code, name = key.split("|", 1)
        article, image = self._article(code, name)
        value = self._describe([f"File:{image}"]) if image else None
        if value:
            value["article"] = article
            value["article_url"] = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(article.replace(" ", "_"))
        self._save(key, value)
        return value

    def get(self, code: str | None, name: str | None):
        """(состояние, описание снимка) по коду типа ICAO и названию из tar1090-db."""
        if not code or not name:
            return MISSING, None
        return self.lookup.get(f"{code}|{name}")
