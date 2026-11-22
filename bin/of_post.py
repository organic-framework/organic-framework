"""`of post comment|review`: a pull request comment or review through the posting unit, never with a token in the
session (of_server's posting unit, hand-over 7d270323, its interface v1, message 01a20553-b1bd).

The unit runs as its own user and holds the token. A session writes one request into its door and touches the door's
signal; the unit posts it or refuses it and writes the result where the door's user reads it. Two doors: the
reviewers' (their own ~/.local/state/of/gh-post) and every other OF session's (/srv/of-gh-post/of). A review goes
only through the reviewers' door, always as a comment-type review on the pull request's head commit; a comment never
carries a line starting "Verdict:"; the unit checks all of it again, and the body passes the pinned gate there."""

import json
import os
import pwd
import time
import uuid

V = 1
MAX_BYTES = 128 * 1024
REPO = "organic-framework/organic-framework"
REVIEWERS_DOOR = os.path.join("~", ".local", "state", "of", "gh-post")
SESSIONS_DOOR = "/srv/of-gh-post/of"
RESULTS = "/var/lib/of-gh-post"


class PostError(Exception):
    pass


REVIEWERS_GROUP = "of-gh-review"


def door(home=None, group=REVIEWERS_GROUP):
    """(the door folder, its kind): the reviewers' door when this user's own one is there as the sitting makes it
    (its requests folder of the reviewers' group), else the sessions' door. A folder any session could make by name
    alone is not taken: nobody reads it (of_review on PR 576)."""
    import grp
    mine = os.path.join(home or os.path.expanduser("~"), ".local", "state", "of", "gh-post")
    req = os.path.join(mine, "requests")
    try:
        st = os.lstat(req)
        gid = grp.getgrnam(group).gr_gid
    except (OSError, KeyError):
        return SESSIONS_DOOR, "sessions"
    import stat as _st
    if _st.S_ISDIR(st.st_mode) and st.st_gid == gid:
        return mine, "reviewers"
    return SESSIONS_DOOR, "sessions"


def request(kind, pr, body, position, session, commit=None, repo=REPO):
    """The request as the unit reads it, or PostError for what the unit would refuse anyway."""
    if kind not in ("comment", "review"):
        raise PostError("kind is comment or review")
    if not isinstance(pr, int) or pr <= 0:
        raise PostError("--pr is the pull request's number")
    if kind == "review" and not commit:
        raise PostError("a review names the pull request's head commit (--commit)")
    if commit and not (len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)):
        raise PostError("--commit is the full head commit, 40 hex digits")
    if not body.strip():
        raise PostError("the body is empty")
    req = {"v": V, "kind": kind, "repo": repo, "pr": pr, "commit": commit, "body": body, "position": position,
           "session": session}
    if len(json.dumps(req).encode("utf-8")) > MAX_BYTES:
        raise PostError(f"the request is over {MAX_BYTES // 1024} KiB")
    return req


def submit(req, folder, kind_of_door, wait_s=120, poll_s=1.0, results=RESULTS, user=None, sleep=time.sleep):
    """Write REQ into the door FOLDER as a new file, touch the signal, and wait for the unit's result: (state, the
    result's data). state is posted, refused, or waiting (no result within WAIT_S)."""
    if req["kind"] == "review" and kind_of_door != "reviewers":
        raise PostError("a review goes only through the reviewers' door; this session has the sessions' door")
    sig = os.path.join(folder, "signal")
    # the signal first: a request written to a door with no unit behind it would stay there for a unit started later
    # to post, and a retry would post twice (of_review on PR 576)
    if not os.path.lexists(sig) or os.path.islink(sig) or not os.path.isfile(sig):
        raise PostError(f"the door has no signal file ({sig}); the unit is not set up here, and nothing was written")
    rid = str(uuid.uuid4())
    path = os.path.join(folder, "requests", f"{rid}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    try:
        os.fchmod(fd, 0o640)
        os.write(fd, json.dumps(req, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    os.utime(sig, None, follow_symlinks=False)
    user = user or pwd.getpwuid(os.getuid()).pw_name
    out = os.path.join(results, user, f"{rid}.json")
    end = time.monotonic() + wait_s
    while True:
        if os.path.isfile(out) and not os.path.islink(out):
            try:
                with open(out, encoding="utf-8") as fh:
                    res = json.load(fh)
                return ("posted" if res.get("url") else "refused"), dict(res, id=rid)
            except ValueError:
                pass   # being written: read again
        if time.monotonic() >= end:
            return "waiting", {"id": rid, "result": out}
        sleep(poll_s)
