"""Переключение единственного донгла между самолётами (readsb) и судами (AIS-catcher).

Веб-страница судов, пока она открыта и видна, просит «суда»; страница самолётов — «самолёты».
Если страницу судов закрыли, через idle_return секунд донгл сам возвращается к самолётам —
иначе табло осталось бы без данных.

Сервисы запускает systemd: `systemctl start` без sudo, право на два конкретных юнита даёт
правило polkit deploy/50-nebo-receiver.rules (nebo работает с NoNewPrivileges, sudo ему нельзя).
Юниты конфликтуют друг с другом, поэтому запуск одного сам останавливает другой.
"""
import logging
import subprocess
import threading
import time

log = logging.getLogger(__name__)

UNITS = {"planes": "readsb.service", "ships": "ais-catcher.service"}
MIN_SWITCH_INTERVAL = 20  # секунд между переключениями: две открытые страницы не дёргают донгл


def _active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True)
    return r.returncode == 0


class ReceiverSwitch:
    def __init__(self, idle_return: float):
        self.idle_return = idle_return
        self.lock = threading.Lock()
        self.last_switch = 0.0
        self.switched_to: str | None = None
        self.ships_seen = 0.0  # когда страница судов последний раз спрашивала данные
        self._mode_cache = (0.0, "planes")
        threading.Thread(target=self._watchdog, name="receiver", daemon=True).start()

    def mode(self) -> str:
        """"planes", "ships" или "off" (ни один приёмник не работает)."""
        now = time.monotonic()
        with self.lock:
            ts, mode = self._mode_cache
            if now - ts < 2:
                return mode
        mode = next((m for m, unit in UNITS.items() if _active(unit)), "off")
        with self.lock:
            self._mode_cache = (now, mode)
        return mode

    def since_switch(self) -> float | None:
        """Сколько секунд назад переключали (для подсказки «суда появляются в течение пары минут»)."""
        return time.monotonic() - self.last_switch if self.last_switch else None

    def want(self, mode: str, reason: str):
        if self.mode() == mode:
            return
        with self.lock:
            now = time.monotonic()
            if now - self.last_switch < MIN_SWITCH_INTERVAL:
                return
            self.last_switch, self.switched_to = now, mode
            self._mode_cache = (0.0, mode)
        unit = UNITS[mode]
        log.info("приёмник → %s (%s)", unit, reason)
        r = subprocess.run(["systemctl", "start", "--no-block", unit], capture_output=True, text=True)
        if r.returncode != 0:
            log.warning("не удалось запустить %s: %s", unit, r.stderr.strip())

    def ships_page(self):
        self.ships_seen = time.monotonic()
        self.want("ships", "открыта страница судов")

    def planes_page(self):
        self.want("planes", "открыта страница самолётов")

    def _watchdog(self):
        while True:
            time.sleep(15)
            try:
                if self.mode() == "ships" and time.monotonic() - self.ships_seen > self.idle_return:
                    self.want("planes", f"страницу судов не смотрят {self.idle_return:.0f} с")
            except Exception as e:  # сторож не должен умирать
                log.warning("сторож приёмника: %s", e)
