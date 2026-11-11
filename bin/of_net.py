"""Is the model API reachable from this machine now? (founder, 2026-11-11: the Mac that runs every session travels on a
mobile hotspot, with lags and drops.)

Retries that count against a limit (the question judge's three tries) are spent only while the API answers a TCP
connect; a run during a drop is skipped and the next pulse tries again, so nothing is given up because of the link.
Everything else OF runs on this machine (the server, the relay, presence, the store) is local and needs no network.
"""

import socket
import time

HOST = "api.anthropic.com"
PORT = 443
TIMEOUT_S = 3
CACHE_S = 60
_last = {}


def online(host=HOST, port=PORT, timeout=TIMEOUT_S, cache_s=CACHE_S, connect=socket.create_connection, now=None):
    """True when a TCP connection to the API opens within `timeout` seconds; cached for `cache_s` per process."""
    now = now or time.time()
    hit = _last.get((host, port))
    if hit and now - hit[0] < cache_s:
        return hit[1]
    try:
        connect((host, port), timeout=timeout).close()
        ok = True
    except OSError:
        ok = False
    _last[(host, port)] = (now, ok)
    return ok
