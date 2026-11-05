"""Запуск: python3 -m nebo [--config /etc/nebo/config.toml] [--png out]"""
import argparse
import logging
import os
import queue
import signal
import sys
import time
from pathlib import Path

from . import __version__, config
from .app import App
from .media import PENDING, DiskImages, Photos
from .routes import RouteResolver
from .sources import make_source

log = logging.getLogger("nebo")

DEFAULT_CONFIG = Path("/etc/nebo/config.toml")


def build(cfg, events: queue.Queue, state_dir: Path):
    def notify():
        events.put(("data",))

    routes = media = None
    if cfg.routes.enabled:
        routes = RouteResolver(cfg.routes.url, cfg.routes.timeout, state_dir / "routes.json", on_update=notify)
    m = cfg.media
    logos = DiskImages("logo", m.logo_url, state_dir / "logos", m.timeout, notify) if m.logos else None
    flags = DiskImages("flag", m.flag_url, state_dir / "flags", m.timeout, notify) if m.flags else None
    photos = None
    if m.photos and m.photo_contact:
        photos = Photos(m.photo_contact, m.timeout, notify)
    elif m.photos:
        log.warning("фото отключены: planespotters требует контакт — задайте [media] photo_contact")
    return routes, logos, flags, photos


def render_previews(app: App, prefix: str):
    """Один раз получить данные и сохранить все экраны в PNG."""
    app.fetch_once()
    deadline = time.time() + 20  # даём маршрутам и картинкам подгрузиться
    while time.time() < deadline:
        planes = app._planes()
        if not planes:
            break
        p = planes[0]
        app.view = "card"
        app._enrich(p)
        if p.route_state != PENDING and p.photo_state != PENDING and (not app.logos or
                                                                       app.logos.get(app.info.operator_code(
                                                                           p.ac.callsign))[0] != PENDING):
            break
        time.sleep(0.5)
    files = []
    for view, overlay in (("card", None), ("card", "qr"), ("radar", None), ("list", None)):
        app.view, app.overlay = view, overlay
        app.screen.path = f"{prefix}-{overlay or view}.png"
        app.screen.show(app.render())
        files.append(app.screen.path)
    print(f"самолётов {len(app.aircraft)}: " + " ".join(files))


def main():
    parser = argparse.ArgumentParser(prog="nebo", description="Табло самолётов поблизости")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    parser.add_argument("--png", metavar="PREFIX",
                        help="не трогать экран: один раз получить данные и сохранить все экраны в PREFIX-*.png")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load(args.config)
    log.info("nebo %s, конфиг %s, точка %.4f %.4f, радиус %.0f км, источник %s",
             __version__, args.config or "по умолчанию", cfg.location.lat, cfg.location.lon,
             cfg.location.radius_km, cfg.source.type)

    events: queue.Queue = queue.Queue()
    state_dir = Path(os.environ.get("STATE_DIRECTORY") or Path.home() / ".cache" / "nebo")
    state_dir.mkdir(parents=True, exist_ok=True)
    routes, logos, flags, photos = build(cfg, events, state_dir)
    source = make_source(cfg)

    if args.png:
        from .screen import PngScreen
        app = App(cfg, PngScreen(f"{args.png}.png"), source, routes, events, logos, flags, photos)
        render_previews(app, args.png)
        if routes:
            routes.close()
        return

    from .tracks import Tracker

    screen = None
    if cfg.display.screen:
        try:
            from .screen import Framebuffer  # нужен numpy — без экрана не обязателен
            screen = Framebuffer(cfg.display.fb_name)
        except (ImportError, RuntimeError, OSError) as e:
            log.warning("экран не найден (%s) — работаю без него, только веб-страница; "
                        "чтобы не искать экран: [display] screen = false", e)
    if screen:
        from .inputs import InputReader
        InputReader(cfg.touch, screen.size, events).start()
    elif not cfg.web.enabled:
        log.error("ни экрана, ни веб-страницы ([web] enabled = false) — показывать нечего")
        sys.exit(1)
    tracks = Tracker(state_dir)
    receiver = None
    if cfg.web.enabled and cfg.web.receiver_switch and cfg.source.type == "readsb":
        from .receiver import ReceiverSwitch
        receiver = ReceiverSwitch(cfg.web.ships_idle_return)
    app = App(cfg, screen, source, routes, events, logos, flags, photos, tracks, receiver)
    if cfg.web.enabled and cfg.media.ship_photos:
        from .media import ShipPhotos
        app.ship_photos = ShipPhotos(cfg.media.commons_contact, cfg.media.timeout, state_dir / "ship_photos.json")
    if cfg.web.enabled and cfg.media.reg_photos:
        from .media import RegPhotos
        app.reg_photos = RegPhotos(cfg.media.commons_contact, cfg.media.timeout, state_dir / "reg_photos.json")
    if cfg.web.enabled and cfg.media.type_photos:
        from .media import TypePhotos
        app.type_photos = TypePhotos(cfg.media.commons_contact, cfg.media.timeout, state_dir / "type_photos.json")
    if cfg.web.enabled:
        from . import web
        try:
            web.start(app, cfg.web.bind, cfg.web.port)
        except OSError as e:
            if not screen:  # без экрана показывать данные больше негде
                log.error("веб-страница не запущена: %s — без экрана показывать нечего", e)
                sys.exit(1)
            log.error("веб-страница не запущена: %s", e)  # порт занят — табло важнее страницы

    def on_term(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_term)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("остановка")
        if routes:
            routes.close()
        tracks.close()
        if screen:
            screen.show(app.renderer.splash("остановлено"))
            screen.close()


if __name__ == "__main__":
    sys.exit(main())
