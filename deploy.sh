#!/bin/sh
# Выкладка nebo на малинку: копирует код в ~/nebo, обновляет сервис и перезапускает его.
# Конфиг /etc/nebo/config.toml не трогает (создаёт из примера, только если его нет).
set -eu

HOST=${NEBO_HOST:-nebo.local}
cd "$(dirname "$0")"

rsync -a --delete --exclude __pycache__ --exclude '*.pyc' \
    nebo tools deploy config.example.toml "$HOST:nebo/"

ssh "$HOST" '
set -e
# юниты и правило polkit написаны для пользователя xtreme — подставляем того, кто выкладывает
render() { sed -e "s#/home/xtreme#$HOME#g" -e "s/\bxtreme\b/$(id -un)/g" "$1"; }
tmp=$(mktemp -d)
render ~/nebo/deploy/nebo.service > "$tmp/nebo.service"
render ~/nebo/deploy/50-nebo-receiver.rules > "$tmp/50-nebo-receiver.rules"
sudo install -d -m 755 /etc/nebo
[ -e /etc/nebo/config.toml ] || sudo install -m 644 ~/nebo/config.example.toml /etc/nebo/config.toml
if ! cmp -s "$tmp/nebo.service" /etc/systemd/system/nebo.service; then
    sudo install -m 644 "$tmp/nebo.service" /etc/systemd/system/nebo.service
    sudo systemctl daemon-reload
fi
# приёмник: юнит обновляем, параметры /etc/default/readsb — только если их нет (дальше руками);
# сам readsb собирается из исходников один раз, см. CLAUDE.md
if [ -x /usr/local/bin/readsb ]; then
    [ -e /etc/default/readsb ] || sudo install -m 644 ~/nebo/deploy/readsb.default /etc/default/readsb
    if ! cmp -s ~/nebo/deploy/readsb.service /etc/systemd/system/readsb.service; then
        sudo install -m 644 ~/nebo/deploy/readsb.service /etc/systemd/system/readsb.service
        sudo systemctl daemon-reload
        sudo systemctl restart readsb
    fi
fi
# суда: юнит ставим, но не включаем — донгл по умолчанию слушает самолёты (переключение руками)
if [ -x /usr/local/bin/AIS-catcher ]; then
    id aiscatcher >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin aiscatcher
    [ -e /etc/default/ais-catcher ] || sudo install -m 644 ~/nebo/deploy/ais-catcher.default /etc/default/ais-catcher
    if ! cmp -s ~/nebo/deploy/ais-catcher.service /etc/systemd/system/ais-catcher.service; then
        sudo install -m 644 ~/nebo/deploy/ais-catcher.service /etc/systemd/system/ais-catcher.service
        sudo systemctl daemon-reload
    fi
fi
# право nebo запускать readsb/ais-catcher без sudo (переключение донгла со страницы)
if ! sudo cmp -s "$tmp/50-nebo-receiver.rules" /etc/polkit-1/rules.d/50-nebo-receiver.rules; then
    sudo install -m 644 "$tmp/50-nebo-receiver.rules" /etc/polkit-1/rules.d/50-nebo-receiver.rules
fi
rm -rf "$tmp"
sudo systemctl enable --quiet nebo
sudo systemctl restart nebo
sleep 2
systemctl --no-pager --lines=8 status nebo
'
