# Painel no Linux/Raspberry. Rede do host: a busca de TVs, os avisos UPnP e o
# Wake-on-LAN precisam estar na mesma rede da TV (docker-compose.yml).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl iproute2 iputils-ping ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY tvtuner ./tvtuner
COPY dns ./dns
RUN pip install --no-cache-dir .
ENV TVTUNER_CONFIG=/data/config.json
VOLUME /data
EXPOSE 8780 8743
CMD ["sh", "-c", "exec tv panel ${PIN:+--pin $PIN}"]
