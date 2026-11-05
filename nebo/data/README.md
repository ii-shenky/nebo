# Данные nebo и откуда они

| Файл | Что | Источник | Лицензия | Обновить |
|---|---|---|---|---|
| `types.json` | типы самолётов ICAO: название, описание (L2J), турбулентность | [wiedehopf/tar1090-db](https://github.com/wiedehopf/tar1090-db) (база [Mictronics](https://www.mictronics.de)) | явной лицензии нет; данные открыто раздаются для ADS-B-приёмников, используются как есть, без правок | `python3 tools/update_tar1090db.py` |
| `operators.json` | авиакомпании ICAO: название, страна | то же | то же | то же |
| `map.json` | береговая линия и аэропорты для радара на табло (район Кипра) | [Natural Earth](https://www.naturalearthdata.com) 10m; аэропорты LCA, ECN, AKT добавлены вручную | общественное достояние | — |
| `nav.json` | радиомаяки и аэродромы с полосами в 600 км от точки | [OurAirports](https://ourairports.com/data/) | общественное достояние | `tools/build_navdata.py` |
| `aip_lccc.json` | путевые точки, трассы, граница района Никосии | AIP Cyprus (Department of Civil Aviation) | **© DCA Cyprus, распространять нельзя** — в репозитории нет | `tools/build_aip.py` из своей копии AIP |

Свои дополнения к справочникам — в коде, а не в этих файлах: страны по адресам ИКАО
(`nebo/countries.py`), статьи Википедии для семейств самолётов (`TYPE_ARTICLES` в `nebo/media.py`),
силуэты (`nebo/shapes.py`).

Без `aip_lccc.json` всё работает, на карте просто нет путевых точек и трасс.

## Что грузится из интернета во время работы (в репозитории не хранится)

- маршруты рейсов — [adsbdb.com](https://www.adsbdb.com);
- самолёты, если нет своего приёмника — [adsb.lol](https://adsb.lol) (ODbL);
- фото бортов — [planespotters.net](https://www.planespotters.net/photo/api): условия — автор рядом с фото,
  ссылка на страницу снимка, JSON не дольше суток, контакт в User-Agent (`[media] photo_contact`);
- фото бортов, судов и типов — [Wikimedia Commons](https://commons.wikimedia.org) и английская Википедия:
  только свободные лицензии, автор и лицензия показываются рядом;
- логотипы авиакомпаний — [Jxck-S/airline-logos](https://github.com/Jxck-S/airline-logos) (лицензии нет,
  логотипы — товарные знаки авиакомпаний; кешируются только на своём устройстве);
- флаги — [flagcdn.com](https://flagcdn.com);
- карта на странице — тайлы [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL) и
  [Leaflet](https://leafletjs.com) (BSD-2) с cdnjs.
