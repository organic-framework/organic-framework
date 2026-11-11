"""Artifact reads across positions (RFC 0001 v3, phase 2, observe gap 5).

Reading another position's brief or code is coordination without a message, which socio-technical
congruence needs. From a transcript record (live ingest, or `of derive` over history): each
Read/Grep/Glob tool call whose path belongs to a system kept by ANOTHER position becomes
`of.artifact_read` {reader, path, system, keeper, tool, tool_use_id}, with a deterministic id
(session + tool_use_id), so re-reading a transcript never duplicates it.
"""

import os
import uuid

import of_router

NS = uuid.UUID("3b1e5c2a-7d4f-4e9b-8a21-5c6d7e8f9a0b")
READ_TOOLS = ("Read", "Grep", "Glob")


def detect(obj, reader, reader_system, index):
    """[(payload, tool_use_id)] for cross-position reads in one transcript record."""
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    if obj.get("type") != "assistant" or not reader:
        return []
    out = []
    for c in msg.get("content") or []:
        if not (isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in READ_TOOLS):
            continue
        path = of_router.target_path(c["name"], c.get("input"), obj.get("cwd"))
        if not path:
            continue
        org, system, keeper = of_router.resolve(path, index)
        if keeper and keeper != reader and system != reader_system:
            home = os.path.expanduser("~")
            out.append(({"reader": reader, "path": path.replace(home, "~", 1), "system": system, "system_org": org,
                         "keeper": keeper, "tool": c["name"], "tool_use_id": c.get("id")}, c.get("id")))
    return out


def event_id(session, tool_use_id):
    return str(uuid.uuid5(NS, f"{session}:{tool_use_id}:artifact_read"))
