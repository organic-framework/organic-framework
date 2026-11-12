# of-server, profile 2 (docs/server.md). Build in CI or on a remote builder, never on the founder's Mac:
#   docker build -f deploy/Dockerfile -t of-server:<version> .
# Python 3.14+ for compression.zstd (clients send zstd when they have it).
#
# ssh-keygen verifies machine signatures and Ed25519 grant signatures (`ssh-keygen -Y verify`). The server never signs
# and never runs ssh or sshd, so the image holds Debian's ssh-keygen binary alone, taken from openssh-client in a build
# stage with the libraries it needs that the runtime image lacks; the OpenSSH package, its client, agent, scp and sftp
# stay out (2026-11-12: a user organization's scanner flagged CVE-2026-123158, on CISA's KEV list, on openssh-client;
# the flaw is sshd's password check on musl libc without PAM, and Debian 13 has no fixed package). The runtime stage
# stops the build unless ssh-keygen loads, signs and verifies.
FROM python:3.14-slim AS keygen

# A library is copied when its soname was not in the image before the install, into its directory's real path (on
# Debian 13 /lib is a link to /usr/lib, and a COPY must never replace that link with a directory).
RUN ldconfig -p | awk '/=>/ {print $1}' | LC_ALL=C sort -u > /tmp/libs.before \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client \
 && mkdir -p /out/usr/bin \
 && cp /usr/bin/ssh-keygen /out/usr/bin/ssh-keygen \
 && ldd /usr/bin/ssh-keygen | awk '$2 == "=>" && $3 ~ /^\// {print $1, $3}' > /tmp/libs.needed \
 && while read -r name path; do \
      if ! grep -qxF "$name" /tmp/libs.before; then \
        dir="/out$(readlink -f "$(dirname "$path")")" && mkdir -p "$dir" && cp -L "$path" "$dir/$name"; \
      fi; \
    done < /tmp/libs.needed

FROM python:3.14-slim

COPY --from=keygen /out/ /
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && ldconfig \
 && if ldd /usr/bin/ssh-keygen | grep -q 'not found'; then ldd /usr/bin/ssh-keygen; exit 1; fi \
 && ssh-keygen -q -t ed25519 -N '' -C probe -f /tmp/probe \
 && printf 'probe' > /tmp/probe.msg \
 && ssh-keygen -q -Y sign -f /tmp/probe -n of-probe /tmp/probe.msg \
 && printf 'probe %s\n' "$(cut -d' ' -f1,2 /tmp/probe.pub)" > /tmp/probe.allowed \
 && ssh-keygen -q -Y verify -f /tmp/probe.allowed -I probe -n of-probe -s /tmp/probe.msg.sig < /tmp/probe.msg \
 && rm -f /tmp/probe* \
 && pip install --no-cache-dir "PyYAML==6.0.2" \
 && useradd --uid 10001 --create-home --home-dir /data of

WORKDIR /app
# Only the server's modules and the modules they import; nothing else of the client.
COPY bin/of_server.py bin/of_server_relay.py bin/of_server_coord.py bin/of_server_finance.py bin/of_server_otlp.py \
     bin/of_mask.py bin/of_route.py bin/of_coord.py bin/of_plugins.py bin/of_family.py bin/of_grants.py \
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
