# of-server, profile 2 (docs/server.md). Build in CI or on a remote builder, never on the founder's Mac:
#   docker build -f deploy/Dockerfile -t of-server:<version> .
# Python 3.14+ for compression.zstd (clients send zstd when they have it); ssh-keygen verifies
# machine signatures and Ed25519 grant signatures.
FROM python:3.14-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "PyYAML==6.0.2" \
 && useradd --uid 10001 --create-home --home-dir /data of

WORKDIR /app
# Only the server's modules and the modules they import; nothing else of the client.
COPY bin/of_server.py bin/of_server_relay.py bin/of_server_coord.py bin/of_server_finance.py bin/of_server_otlp.py \
     bin/of_mask.py bin/of_route.py bin/of_coord.py bin/of_plugins.py bin/of_grants.py \
     bin/of_events.py bin/of_ship.py bin/of_store.py ./
# The secrets broker (docs/agent-secrets.md §5.3) and the catalogue it reads.
COPY bin/of_server_secrets.py bin/of_credentials.py ./
# Studio (of_studio's modules), served under /studio/ when present.
COPY bin/of_studio.py bin/of_studio.css bin/of_model.py bin/of_measures.py bin/of_asks.py bin/of_diagnose.py \
     bin/of_diagnose_rules.py ./
COPY docs/diagnose-v0.yaml ./docs/diagnose-v0.yaml
COPY bin/rules/ ./rules/

USER 10001
# OF_AUTOMATION=1: background processes never reach an SSH agent (the founder's ~/.ssh/config rule).
ENV HOME=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OF_SERVER_CONFIG=/etc/of/server.yaml OF_AUTOMATION=1 \
    OF_DIAGNOSE=/app/docs/diagnose-v0.yaml
EXPOSE 18200
ENTRYPOINT ["python3", "-s", "/app/of_server.py"]
