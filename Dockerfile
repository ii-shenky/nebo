# nebo без экрана и без антенны: только веб-страница, самолёты с adsb.lol.
#   docker build -t nebo .
#   docker run -d -p 127.0.0.1:8080:8080 -v nebo-state:/var/lib/nebo nebo   → http://localhost:8080
# Страница без авторизации и со своим конфигом показывает координаты дома — наружу только за авторизацией.
# Свой конфиг (координаты дома, контакт для фото): -v $PWD/config.toml:/etc/nebo/config.toml:ro
# Экран 3,2" и свой приёмник — это малинка, см. README.
FROM python:3.13-slim
RUN pip install --no-cache-dir pillow qrcode \
    && useradd --system --create-home nebo \
    && install -d -o nebo /var/lib/nebo /etc/nebo
COPY nebo /opt/nebo/nebo
COPY config.example.toml /etc/nebo/config.toml
RUN sed -i 's/^screen = true /screen = false/' /etc/nebo/config.toml
USER nebo
WORKDIR /opt/nebo
ENV STATE_DIRECTORY=/var/lib/nebo PYTHONUNBUFFERED=1
VOLUME /var/lib/nebo
EXPOSE 8080
CMD ["python", "-m", "nebo", "--config", "/etc/nebo/config.toml"]
