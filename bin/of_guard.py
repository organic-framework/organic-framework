"""The guard: critical denies before a tool runs (RFC 0002 §4.1; AGENTS.md). Extracted from bin/of_hook.py on
2026-11-07 for the hand-over to of_server (organic-framework-build, sd-2026-11-07-of-team-roles, ask 909d03a2).

critical_deny(tool, tool_input, cwd) parses a Bash command once, quotes tracked, and denies what will execute or be
read as data: secret values reaching the transcript, secret files leaving their place, environment dumps, cluster and
secret-manager reads, kernel and process memory and cores, the agents' identity directory, a bare `op`, and op on the
founder's personal 1Password account; also inside ssh, sudo, docker/kubectl exec, heredocs and a remote shell's stdin.
prompt_secret_mention(prompt) finds an @-mention of a secret path in a prompt. of_hook's _guard and _mention_guard
turn their verdicts into the hook's output and of.mandate_check events. Tests: tests/test_guard*.py."""

import ast
import os
import re
import shlex
import stat
import warnings

import of_classify

# `$HOME`, `${HOME}`, and `${HOME` with an operator that gives HOME while it is set: `:?`, `?`, `:-`, `-`, `:=`, `=` (any
# word; of_secrets-review on organic-framework PR 268: `"${HOME:?}"` is the form shellcheck asks for before an
# `rm -rf`); not `:+` or `+`, which give the other word. A trim (`%`, `%%`, `#`, `##`) is computed against HOME as bash
# computes it (of_review on PR 268: `${HOME%/*}/<its last name>` is HOME again)
_HOME_WORD = re.compile(r"^(?:\$\{HOME(?:(:?[?=-])[^}]*|(%%?|##?)([^}]*))?\}|\$HOME)(?=/|$)")


def _home_trim(home, op, pat):
    """HOME with bash's ${HOME<op><pat>} applied: `%` the shortest matching suffix removed, `%%` the longest, `#` the
    shortest matching prefix, `##` the longest. None for a pattern the guard cannot read (a quote, a variable, a backslash)."""
    import fnmatch
    if re.search(r"""[$`'"\\]""", pat):
        return None
    n = len(home)
    if op in ("%", "%%"):
        cuts = range(n, -1, -1) if op == "%" else range(0, n + 1)
        return next((home[:i] for i in cuts if fnmatch.fnmatchcase(home[i:], pat)), home)
    cuts = range(0, n + 1) if op == "#" else range(n, -1, -1)
    return next((home[j:] for j in cuts if fnmatch.fnmatchcase(home[:j], pat)), home)


def _home_word(m):
    if not m.group(2):
        return "~"
    home = os.path.expanduser("~")
    got = _home_trim(home, m.group(2), m.group(3))
    return m.group(0) if got is None else got


def _expanduser(p):
    """os.path.expanduser, with a leading `$HOME` or `${HOME}` read as the home folder: the shell expands it from the
    session's HOME, and a path written so reached G19's and G21's folders unseen (2026-11-17, `bash "$HOME/…"`)."""
    return os.path.expanduser(_HOME_WORD.sub(_home_word, p) if isinstance(p, str) else p)

CRITICAL_DENY_BASH = re.compile(
    r"(?:^|[;&|(\s])(?:printenv\b|env\s*(?:$|[|;>&])|export\s+-p\b|set\s*(?:$|[|;>&])|"
    r"echo\s+[^|;&]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\w*|"
    r"security\s+find-(?:generic|internet)-password\b[^|;&]*-w)", re.I)
READERS = re.compile(r"(?:^|[;&|(\s])(?:cat|less|more|head|tail|bat|strings|xxd|od|base64|jq|yq|sed|awk|grep|rg|"
                     r"cp|scp|curl|python3?|node|sqlite3?)\b", re.I)


_READER_WORDS = {"cat", "less", "more", "head", "tail", "bat", "strings", "xxd", "od", "base64", "jq", "yq", "sed",
                 "awk", "grep", "rg", "python", "python3", "node", "tar", "zip", "gzip", "gunzip", "gpg", "openssl",
                 "sqlite3", "sqlite"}  # gcloud's token stores are SQLite (of_review on organic-framework PR 178)
_COPY = {"cp", "scp", "rsync"}
_FILE_READERS = _READER_WORDS | _COPY
_COPY_VALUE_OPTS = {"-i", "-F", "-o", "-c", "-l", "-P", "-S", "-e", "--rsh", "-T", "--exclude", "--include",
                    "--chmod", "-f", "--filter", "--files-from", "--out-format"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "eval"}
_INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "php", "osascript"}
_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "stdbuf", "xargs", "builtin", "doas", "ionice",
             "caffeinate", "taskset", "chrt", "taskpolicy", "arch", "script"}
_WRAPPER_POSITIONAL = {"taskset": 1, "chrt": 1, "script": 1}  # a mask, a priority, a typescript file, then the command
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
_SEPS, _REDIR = set(";&|()\n"), set("<>&")

# A variable reference, and the rule for a secret-shaped NAME: KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL(S)|
# PRIVATE only as a whole _-separated part (of_architect, 2026-11-06). R2_HAWKEYE_ID passes; R2_SECRET_KEY does not.
_VAR_REF = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
_SECRET_PART = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE)$", re.I)


def _secret_name(name):
    return any(_SECRET_PART.fullmatch(p) for p in name.split("_"))


def _expandable_secret_vars(text):
    """The secret-shaped variable references the shell would expand in this raw command: outside single
    quotes (double quotes expand). A single-quoted $NAME is prose (of_architect/of_platform, 2026-11-06)."""
    out, i, n, single = set(), 0, len(text), False
    while i < n:
        c = text[i]
        if single:
            single = c != "'"
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'":
            single = True
            i += 1
            continue
        m = _VAR_REF.match(text, i)
        if m:
            if _secret_name(m.group(1)):
                out.add(m.group(0))
            i = m.end()
            continue
        i += 1
    return out


# Guard-local secret paths beyond of_classify.SECRET_GLOBS:
# - /proc/<pid>/environ holds a process's environment (a leak on 11-04: a DSN with its password read this way);
# - gateway key material: /etc/wireguard/**, /etc/amnezia/**, and any *.conf under a wireguard/amnezia directory.
_EXTRA_SECRET = re.compile(
    r"/proc/(?:\d+|self|thread-self|\$\{?\w+\}?|\*)/(?:task/[^/]+/)?environ$"
    r"|(?:^|/)etc/(?:wireguard|amnezia)/"
    r"|(?:^|/)(?:wireguard|amnezia)/[^/]*\.conf$"
    # the agents' identity (RFC 0003 §1a, ask 29ea1baa): the 1Password service-account token and anything beside it;
    # only an organization's committed scripts read it
    r"|(?:^|/)\.config/[^/]+/agent-identity(?:/|$)", re.I)

# An organization's own config directory (~/.config/<org>/) holds its working credentials under plain names:
# clickhouse-password, bootstrap-token, secrets.yaml, ghcr-secret.<x>.json, r2.env (a user organization's
# sec_keeper, msg 01a1c87f, 2026-11-11; docs/guard.md G1.8). There a file is secret when a whole part of its name
# (split on . - _) is a credential word or it ends in .env, unless a part says it holds none (example, template…)
# or it is code. Outside such a directory these names are too common (a chart's templates/secrets.yaml, design
# tokens.json).
_ORG_CONFIG_FILE = re.compile(r"(?:^|/)\.config/[^/]+/(?:[^/]+/)*([^/]+)$")
_CRED_WORDS = ("password", "passwd", "secret", "secrets", "token", "tokens", "credential", "credentials", "creds",
               "header", "auth", "authkey", "apikey", "api-key", "dsn")
_CRED_WORD = re.compile(r"(?:^|[._-])(?:" + "|".join(re.escape(w) for w in _CRED_WORDS) + r")(?=$|[._-])|.\.env$",
                        re.I)
_NO_CRED = re.compile(r"(?:^|[._-])(?:example|sample|template|placeholder|empty)(?=$|[._-])"
                      r"|\.(?:py|pyc|sh|bash|zsh|go|rs|js|mjs|ts|tsx|md|j2|tpl|tf|rb|pl|lua|html|css|lock|sum|mod)$",
                      re.I)
# name.env anywhere else warns first (G1.9): 225 reads in 21 days were live flows (etcd.env over ssh, pins.env,
# grep -c on a key), and some printed a value (sed -n 's/^DISCOVERY_DB_URL=//p'). It denies once they have moved.
ENV_FILE_MODE = "warn"
_ENV_FILE = re.compile(r"(?:^|/)([^/]*[^/.][^/]*\.env)$")
ENV_FILE_NOTE = ("an env file holds values: read names only through a committed script, never print a value; "
                 "name.env files will be secret paths once these flows have moved (docs/guard.md G1.9)")


# A names list (G1.8): a file whose name also has a part `names` lists credentials by name, and is no secret file when
# the guard reads it and finds no value in it (2026-11-18, a user organization's structure keeper, msg 01a1ee9d-ae5f: a
# keeper's names-only table of its Secrets, `<kind>-secret-names.txt`, was denied by G1 and G8 for the word in its
# name, and it blocked a manifest generator its owner had approved). An env file is never one: it holds values.
_NAMES_PART = re.compile(r"(?:^|[._-])names(?=$|[._-])", re.I)
NAMES_LIST_MAX = 256 * 1024
# A names list is read by what it may hold, not by the values it must not: a list of value patterns let `DB_PASS=…`,
# `aws_secret_access_key = …`, lower-case keys, a seed phrase, a mixed-case password, a UUID and base64 under a key
# through (of_secrets-review and of_review on organic-framework PR 332). It may hold Kubernetes-style names, a type
# (Opaque, kubernetes.io/tls), a count or an age (1, 12d, 3h5m), a `-` list marker and upper-case column headers on its
# first line, at most NAMES_LINE_MAX on a line; no private key's header and nothing of_classify.SECRET finds anywhere;
# no twelve BIP39 words in a row. A name shaped like a value (a UUID, 32 or more hex digits, a run of 16 or more
# lower-case letters and digits with two digits or more) is no name. A short lower-case value shaped like a name
# (`hunter2`) cannot be told from one (docs/guard.md G1.8.1, Known limits).
_KEY_ANYWHERE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-\d+:")
_NAME_TOKEN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_TYPE_TOKEN = re.compile(r"Opaque|[a-z0-9.-]+/[a-z0-9.-]+")
# the column names kubectl and its kin print (of_review on PR 332: an upper-case Base32 key passed as a header)
_HEADERS = frozenset("NAME NAMESPACE TYPE DATA AGE KIND STATUS CREATED LABELS".split())
# a field that names what a column holds as a credential: the file holds values, not names (of_review on PR 332:
# `name,password` over `demo,public-test-passphrase` passed)
_CRED_FIELDS = frozenset("password passwd passphrase pass pwd secret secrets token tokens key keys apikey api_key api-key "
                         "private_key privatekey credential credentials auth pin otp value values".split()) | \
    frozenset(_CRED_WORDS)  # the guard's own credential words too (`creds`: of_review on organic-framework PR 332)
_COUNT_TOKEN = re.compile(r"\d{1,6}|(?:\d{1,5}[smhdy]){1,3}|-")
NAMES_LINE_MAX = 8
# a value in any field: a UUID, 32 or more hex digits, a lower-case run with digits, a Kubernetes bootstrap token
# (`abcdef.0123456789abcdef`, digits or none), 16 or more digits (of_review on PR 332)
_VALUE_SHAPE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}"
                          r"|(?<![A-Za-z0-9])(?=[a-z0-9]*\d[a-z0-9]*\d)[a-z0-9]{16,}(?![A-Za-z0-9])"
                          r"|(?<![a-z0-9])[a-z0-9]{6}\.[a-z0-9]{16}(?![a-z0-9])|\d{6,}")


def _names_list_clean(p):
    """Is the file at P (absolute or ~-rooted) a plain-text list with no value in it? Reads at most NAMES_LIST_MAX
    bytes, keeps nothing and prints nothing; a file it cannot read, a larger one, a dataless one or one that is not
    UTF-8 text is no."""
    path = os.path.expanduser(p.strip().strip("'\""))
    if not os.path.isabs(path):
        return False
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > NAMES_LIST_MAX or getattr(st, "st_flags", 0) & 0x40000000:
            return False
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            data = os.read(fd, NAMES_LIST_MAX + 1)
        finally:
            os.close(fd)
        text = data.decode("utf-8")
    except (OSError, ValueError):
        return False
    if len(data) > NAMES_LIST_MAX or "\x00" in text or _KEY_ANYWHERE.search(text) or of_classify.SECRET.search(text):
        return False
    if _REMOTE_BODY[0] or _LINE_REMOTE[0]:
        return False  # a remote command's file is another machine's: this one's copy says nothing about it
    names, first = [], True
    for line in text.splitlines():
        toks = [t for t in re.split(r"[\s,]+", line.strip()) if t]
        if not toks:
            continue
        # 12 or more digits in one field of a separated line, or in a run of digit groups (`4242 4242 4242 4242`:
        # of_review on PR 332); a column table's line is no one field, its name's date and its counts apart
        if re.search(r"[,\t;|]", line) and any(sum(c.isdigit() for c in f) >= 12 for f in re.split(r"[,\t;|]", line)):
            return False
        run = 0
        for t in toks:
            run = run + len(t) if t.isdigit() else 0
            if run >= 12:
                return False
        if len(toks) > NAMES_LINE_MAX:
            return False  # a table of names has a few columns; a seed phrase or a sentence has more
        if first and all(t in _HEADERS for t in toks):
            first = False
            continue  # the first non-empty line, made of known column names only
        if first and len(toks) > 1 and not any(_COUNT_TOKEN.fullmatch(t) or _TYPE_TOKEN.fullmatch(t) for t in toks) \
                and any(x in _CRED_FIELDS for t in toks for x in re.split(r"[-_.]", t.lower())):
            return False  # a header row naming a credential column inside a joined word (`name,client-secret`:
            # of_review on organic-framework PR 332); a single name on the first line is a name
        first = False
        for t in toks:
            if _VALUE_SHAPE.search(t) or t.lower() in _CRED_FIELDS or sum(c.isdigit() for c in t) >= 12:
                return False  # in any field: a type, a count or a name
            if _COUNT_TOKEN.fullmatch(t) or _TYPE_TOKEN.fullmatch(t):
                continue
            if _NAME_TOKEN.fullmatch(t):
                names.append(t)
                continue
            return False  # anything else (`=`, `key:`, upper case, a symbol, base64) is no name
    return not _bip39_run(names)


_BIP39 = None


def _bip39_run(words, need=12):
    """Do NEED words in a row come from the BIP39 list (a seed phrase, even split across lines)?"""
    global _BIP39
    if _BIP39 is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "bip39-english.txt")) as fh:
                _BIP39 = frozenset(w.strip() for w in fh if w.strip())
        except OSError:
            _BIP39 = frozenset()
    run = 0
    for w in words:
        run = run + 1 if w in _BIP39 else 0
        if run >= need:
            return True
    return False


def _org_config_secret(p):
    m = _ORG_CONFIG_FILE.search(p)
    if not m or not _CRED_WORD.search(m.group(1)) or _NO_CRED.search(m.group(1)):
        return False
    name = m.group(1)
    return not (_NAMES_PART.search(name) and not name.lower().endswith(".env") and _names_list_clean(p))


def _env_file(q):
    m = _ENV_FILE.search(q)
    if not m or _NO_CRED.search(m.group(1)) or re.search(r"[\[\]{}$*]", m.group(1)):
        return False  # an example, a template or a pattern (`$h.env`, `*.env`, a jq path `.containers[0].env`)
    if ENV_FILE_MODE == "deny":
        return True
    _WARNINGS.append((f"a command that reads an env file ({m.group(1)})", q, ENV_FILE_NOTE))
    return False


def _secret_path(p):
    if not p:
        return False
    q = p.strip().strip("'\"")
    n = os.path.normpath(q) if q and q != os.path.normpath(q) else None  # `~/.aws//sso`, `sso/../sso` (PR 178)
    return (of_classify.secret_path(p) or bool(_EXTRA_SECRET.search(q)) or _org_config_secret(q)
            or _env_file(q) or (n is not None and _secret_path(n)))


def _in_folder(here, fn, *a):
    """FN(*A) judged with the session's folder set to HERE, the folder the line moved to; then set back."""
    if not isinstance(here, str) or here == MAYBE_HOME:
        return fn(*a)
    saved = _DENY_CWD[0]
    try:
        _DENY_CWD[0] = here
        return fn(*a)
    finally:
        _DENY_CWD[0] = saved


def _secret_here(word, here):
    """Is the relative path WORD a secret path once read from HERE, the folder the line moved to (`cd ~/.codex && cat
    auth.json`: of_secrets-review on organic-framework PR 387)? A glob that names a folder needs it."""
    w = str(word or "").strip("'\"")
    if not w or not isinstance(here, str) or here == MAYBE_HOME or w.startswith(("/", "~", "$")):
        return False
    return _secret_path(os.path.join(here, w))


def _reads(p):
    return (f"a command that reads a secret file ({os.path.basename(p.rstrip('/')) or p})", p) if p else None


CODE_READS = "code that names a secret file in a string"


def _code_reads(lit):
    """G8's reason: the name it matched, said to be a string in the code, and what to do when it is no file the code
    reads (a user organization's structure keeper, msg 01a1ecc1-40a4: a filter word was denied as a read, and the agent
    reshaped the string instead of reporting it). The name is the string's last path part, as in every other read's
    reason, so a URL's or a path's other parts stay out of it (of_review on organic-framework PR 323: a credential in a
    URL's user part reached the recorded reason); the report goes to the agent's own structure keeper (the same
    review: framework matters come through it, AGENTS.md)."""
    shown = os.path.basename(lit.rstrip("/")) or lit
    shown = shown if len(shown) <= 120 else shown[:117] + "..."
    return (f"{CODE_READS}: {shown!r} (docs/guard.md G8). Secrets stay in their files; ask the owner if you need a "
            "value. In python a string the code only compares or searches for is a filter, not a read, when every "
            "call in the code is on G8's short list (string, list, csv, json, re, sys.stdin) and it opens files only by "
            "plain literal names; if this string is no file the code reads, report the command to your structure "
            "keeper rather than reshape the string"), lit


# G1.13 and G0.9 (2026-11-18; a user organization's security owner through its structure keeper, msg 01a1eb16-9ebf): a
# subagent ran `for f in <kit>/*; do [ -f "$f" ] && head -40 "$f"; done` over its position's runner kit and printed the
# kit's SSH private key into its tool output. No word on the line named the key: the shell expanded the glob, and the
# loop handed each file to head as $f. So a reader's file arguments are expanded as the shell would (a glob, the words
# of a `for` list the reader gets through its variable, a variable set on the line), and a file is a secret file by its
# first bytes as well as by its name, so a copy under another name, in a scratch folder, is one too.
_KEY_HEAD = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*(?:-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
                       rb"|PuTTY-User-Key-File-\d+:)")
_GLOB = re.compile(r"[*?\[]")
EXPAND_LIMIT = 256   # matches of one glob looked at; a glob over more is not followed further (docs/guard.md, Gaps)
EXPAND_BUDGET = 4000  # folder entries one glob may read: the guard runs before every call, so a deep glob stops here
KEY_READ_MODE = {"content": "deny", "expand": "deny"}
KEY_NOTE = ("a private key is used through the program that needs it (ssh -i, ssh -F <config>, the tool's own command), "
            "never read; list a folder's file names with ls (docs/guard.md G1.13, G0.9)")


def _key_file(path):
    """Does the regular file at PATH start with a private key's header (OpenSSH, PEM, PGP, PuTTY)? Reads at most its
    first 96 bytes, kept nowhere and never printed; anything else (a folder, a FIFO, no file) is no."""
    try:
        st = os.stat(path) if path else None
        # a dataless file (iCloud Drive, a File Provider folder) would be downloaded by opening it: never opened
        if not st or not stat.S_ISREG(st.st_mode) or getattr(st, "st_flags", 0) & 0x40000000:
            return False
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            head = os.read(fd, 96)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False
    return bool(_KEY_HEAD.match(head))


def _local(word, here):
    """WORD as a path on this machine (~ and $HOME read, relative to HERE), or None when it rests on a variable or a
    substitution that is not known here, or is relative with no folder known."""
    p = _expanduser(word)
    if not p or "$" in p or "`" in p:
        return None
    if not os.path.isabs(p):
        if not here:
            return None
        p = os.path.join(here, p)
    return p


def _loop_header(stext, loops):
    """`for NAME in WORDS` (after do, then, else, {, !): record WORDS as what NAME holds in the loop's body."""
    try:
        toks = _tokens(_ansi_c(stext))
    except ValueError:
        return
    while toks and toks[0] in ("do", "then", "else", "elif", "if", "while", "until", "!", "{", "(", "time"):
        toks = toks[1:]
    if len(toks) >= 3 and toks[0] in ("for", "select") and re.fullmatch(r"[A-Za-z_]\w*", toks[1]) and toks[2] == "in":
        loops[toks[1]] = [t for t in toks[3:] if t != "do"]


_POSIX_CLASS = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
                "xdigit": "0-9A-Fa-f", "space": " \t\n\r\f\v", "blank": " \t", "word": "a-zA-Z0-9_"}


def _fnpattern(part):
    """A glob component in fnmatch's terms: bash's `[[:alpha:]]` classes and `[^…]` negation (of_secrets-review on
    organic-framework PR 321: a class matched nothing)."""
    part = re.sub(r"\[:(\w+):\]", lambda m: _POSIX_CLASS.get(m.group(1), m.group(0)), part)
    return part.replace("[^", "[!")


def _braces(word, limit=EXPAND_LIMIT):
    """WORD's brace expansion, which bash does before anything else: `{a,b}`, nested, and `{1..3}` or `{a..c}` ranges;
    `${…}` is a parameter, not a brace. At most LIMIT words (of_secrets-review on organic-framework PR 321:
    `cat k/{run.sh,deploy}` printed a key)."""
    i = 0
    while i < len(word):
        if word[i] != "{" or (i and word[i - 1] == "$"):
            i += 1
            continue
        depth, j, commas = 0, i, []
        while j < len(word):
            c = word[j]
            if c == "\\":
                j += 2
                continue
            depth += c == "{"
            depth -= c == "}"
            if c == "}" and depth == 0:
                break
            if c == "," and depth == 1:
                commas.append(j)
            j += 1
        if j >= len(word):
            return [word]
        body = word[i + 1:j]
        if commas:
            bounds = [i] + commas + [j]
            parts = [word[bounds[k] + 1:bounds[k + 1]] for k in range(len(bounds) - 1)]
        else:
            m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])", body)
            if not m:
                i += 1
                continue
            if m.group(1) is not None:
                a, b, step = int(m.group(1)), int(m.group(2)), abs(int(m.group(3) or 1)) or 1
                pad = max(len(m.group(1)), len(m.group(2))) if re.match(r"-?0\d", m.group(1) + " ") or \
                    re.match(r"-?0\d", m.group(2) + " ") else 0
                seq = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
                parts = [str(x).zfill(pad) for x in seq[:limit]]
            else:
                a, b = ord(m.group(4)), ord(m.group(5))
                parts = [chr(x) for x in (range(a, b + 1) if a <= b else range(a, b - 1, -1))]
        out = []
        for part in parts:
            out += _braces(word[:i] + part + word[j + 1:], limit)
            if len(out) >= limit:
                return out[:limit]
        return out
    return [word]


def _glob(pattern, wide=False):
    """The paths an absolute PATTERN matches as bash matches them (case-sensitive, `*` and `?` never match a leading
    dot), reading at most EXPAND_BUDGET folder entries and keeping at most EXPAND_LIMIT matches. WIDE: as with
    `shopt -s dotglob globstar` as well, a leading dot matched and `**` any depth, for the content check alone, where
    matching more costs no false deny (of_secrets-review on organic-framework PR 321)."""
    import fnmatch
    if pattern.endswith("/") and len(pattern) > 1:  # `k/*/`: folders only, as the shell matches it
        return [q for q in _glob(pattern.rstrip("/"), wide) if os.path.isdir(q)]
    parts, paths, read = [x for x in pattern.split("/") if x], ["/"], 0
    for i, part in enumerate(parts):
        last, nxt = i == len(parts) - 1, []
        if not _GLOB.search(part):
            nxt = [q for q in (os.path.join(x, part) for x in paths) if last or os.path.isdir(q)]
        elif part == "**":  # recursive, as zsh (the agents' shell on a Mac) reads it by default and bash with globstar
            stack = list(paths)
            while stack:
                x = stack.pop()
                nxt.append(x)
                try:
                    with os.scandir(x) as it:
                        for e in it:
                            read += 1
                            if read > EXPAND_BUDGET:
                                return [q for q in nxt if os.path.lexists(q)][:EXPAND_LIMIT] if last else []
                            if not wide and e.name.startswith("."):
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(os.path.join(x, e.name))
                            elif last:
                                nxt.append(os.path.join(x, e.name))
                except OSError:
                    continue
        else:
            # for the content check, any bracket expression is one character of any kind: a class fnmatch does not read
            # never hides a key (of_review on organic-framework PR 321)
            pat = re.sub(r"\[(?:\[:\w+:\]|[^\]\[])+\]", "?", part) if wide else _fnpattern(part)
            for x in paths:
                try:
                    with os.scandir(x) as it:
                        for e in it:
                            read += 1
                            if read > EXPAND_BUDGET:
                                return [q for q in nxt if os.path.lexists(q)][:EXPAND_LIMIT] if last else []
                            if not wide and e.name.startswith(".") and not part.startswith("."):
                                continue
                            if fnmatch.fnmatchcase(e.name, pat) and (last or e.is_dir()):
                                nxt.append(os.path.join(x, e.name))
                except OSError:
                    continue
        paths = nxt[:EXPAND_LIMIT * 4]
        if not paths:
            return []
    return [q for q in paths if os.path.lexists(q)][:EXPAND_LIMIT]


def _expanded(word, here, loops, shvars):
    """[(by_shell, path, by_name)]: what a reader's WORD reaches on this machine as the shell expands it: braces, a loop
    variable as each word of its `for` list, the line's variables, then a glob. BY_SHELL: the path came out of braces, a
    glob or a loop's list, which no word on the line names (G0.9). BY_NAME: the name rules may judge it too (bash's
    default glob, a brace word, a loop's word); a path only the wide glob reaches, or a line variable holds, is judged
    by content. Nothing for an unknown part, or inside a remote command (ssh, a container's or a VM's exec), whose files
    are not this machine's."""
    if _REMOTE_BODY[0]:
        return []
    braced = _braces(word)
    brace = braced != [word]
    out = []
    for bw in braced:
        words, loop = [bw], False
        m = re.search(r"\$\{?([A-Za-z_]\w*)\}?", bw)
        if m and m.group(1) in loops:
            words, loop = [bw[:m.start()] + w + bw[m.end():] for w in loops[m.group(1)]], True
        for w in words:
            p = _local(_expand_vars(w, shvars), here)
            if p and _GLOB.search(p):
                plain = set(_glob(p))
                out += [(True, x, x in plain) for x in _glob(p, wide=True)]
            elif p and (not loop or os.path.lexists(p)):  # a loop's word that is no file here is a name, not a path
                out.append((loop or brace, p, loop or brace))
    return out


def _key_hit(path, expanded, by_name=None):
    """A secret file a reader reaches only through a glob or a loop's list (G0.9: by name or content), or a private key
    by content (G1.13): (reason, path) to deny, None (a warning is noted when its mode warns). A path a variable set on
    the line holds is judged by content only: by name it is G5's own reading of the word (Known limits)."""
    by_name = expanded if by_name is None else by_name
    if by_name and _secret_path(path) and not _NO_CRED.search(os.path.basename(path)):  # not kubeconfig.go
        kind = "expand"
    elif _key_file(path):
        kind = "expand" if expanded else "content"
    else:
        return None
    hit = _reads(path)
    if KEY_READ_MODE[kind] == "deny":
        return hit
    _WARNINGS.append((hit[0], hit[1], KEY_NOTE))
    return None


def _reader_key(word, here, loops, shvars):
    for expanded, p, by_name in _expanded(word, here, loops, shvars):
        hit = _key_hit(p, expanded, by_name)
        if hit:
            return hit
    return None


def _redirect_key(target):
    """A `<` redirection's target that reaches a secret file once expanded (a loop's variable, a line variable, a glob)
    or is a private key by content: (reason, path) or None (of_secrets-review on organic-framework PR 321:
    `for f in k/*; do head -5 < "$f"; done`)."""
    here, shvars = _REDIR_CTX[0] or (_DENY_CWD[0], {})
    return _reader_key(target, here, _LOOPS[0], shvars)


def _prints(token):
    """Every deny names what it matched (of_architect, 2026-11-06)."""
    return f"a command that would print secrets ({token})", token


def _loose_secret_path(text):
    """The old token scan, for fallbacks when the command line cannot be parsed."""
    for tok in re.split(r"[\s;&|<>()]+", text):
        if _secret_path(tok):
            return tok.strip("'\"")
    return None


def _strip_quoted(text):
    """Remove single- and double-quoted spans (prose or carried data), keeping only what the shell would run
    as words. Used by the parse-failure fallback so a quoted `of send`/`git commit -m` text is never scanned."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                break
            i = j + 1
            out.append(" ")
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                break
            i = j + 1
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


_LITERAL = re.compile(r"""(['"])([^'"\n]{1,300}?)\1""")
# interpreter code that serializes the WHOLE environment (print(os.environ), dict(os.environ),
# json.dumps(os.environ), console.log(process.env)); reading one named key (os.environ['X'], .get, .keys) is not.
# the whole environment serialized to output: a dumping call wrapping os.environ / process.env / ENV.
# Setting or reading parts (os.environ.update(...), os.environ['X'], os.environ.items() into a dict) is not.
_ENV_DUMP_CODE = re.compile(
    r"(?:print|pprint|pp|repr|json\.dumps?|console\.(?:log|dir|error)|puts|inspect)\s*\("
    r"[^()]*?(?:os\.environ|process\.env|(?<![.\w])ENV)\b(?!\s*[\[.])"  # not os.environ['X'] / process.env.HOME
    # dict(os.environ)/list(os.environ) only when the environment is the SOLE argument: dict(os.environ, X=y)
    # extends it for a subprocess (not a dump) and is allowed
    r"|(?:dict|list|str|vars)\s*\(\s*(?:os\.environ|process\.env)\s*\)")


_BLOCK = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"", re.S)


def _block_words(code):
    """The triple-quoted blocks that hold one word once stripped of whitespace: a path the code may open, not text
    it carries (docs/guard.md G8)."""
    words = ((m.group(1) if m.group(1) is not None else m.group(2)).strip() for m in _BLOCK.finditer(code))
    return [w for w in words if w and len(w) <= 300 and not re.search(r"\s", w)]


_STRING_HEAD = re.compile(r"([A-Za-z]*)('''|\"\"\"|'|\")")
_PYTHON = re.compile(r"python[\d.]*$")


def _py_strings(code):
    """(the literals G8 checks, the code without its strings and comments) from python's own tokenizer, or None
    when the code does not tokenize. A triple-quoted string counts only when it is one word once stripped (carried
    text otherwise); a comment counts never; a stray triple quote inside a comment or a string shifts nothing
    (of_secrets-review on organic-framework PR 97)."""
    import io
    import tokenize
    starts = [0]
    for line in io.StringIO(code).readlines():  # the tokenizer's own lines (str.splitlines splits on more)
        starts.append(starts[-1] + len(line))

    def off(pos):
        return starts[pos[0] - 1] + pos[1] if pos[0] - 1 < len(starts) else len(code)

    lits, cut, fstack = [], [], []
    try:
        for t in tokenize.generate_tokens(io.StringIO(code).readline):
            name = tokenize.tok_name.get(t.type, "")
            if t.type == tokenize.COMMENT:
                cut.append((off(t.start), off(t.end)))
            elif name.endswith("STRING_START"):  # an f-string or t-string (python 3.12+): its parts come as tokens
                fstack.append(off(t.start))
            elif name.endswith("STRING_END") and fstack:
                begin = fstack.pop()
                if not fstack:
                    cut.append((begin, off(t.end)))
                    lits.append(code[begin:off(t.end)])
            elif t.type == tokenize.STRING:
                if not fstack:
                    cut.append((off(t.start), off(t.end)))
                lits.append(t.string)
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    words = []
    for s in lits:
        m = _STRING_HEAD.match(s)
        if not m or not s.endswith(m.group(2)) or len(s) < len(m.group(0)) + len(m.group(2)):
            continue
        bodies = [s[len(m.group(0)):len(s) - len(m.group(2))]]
        # its value too, escapes decoded: "\x7e/.ssh/…" or a backslash-newline inside the string hid a path (of_review
        # on organic-framework PR 97); an f-string's or t-string's text parts the same way, each replacement field
        # read as {} (of_review and of_secrets-review on PR 97: f-strings had kept their escapes)
        try:
            with warnings.catch_warnings():  # an invalid escape ("\$") warns on stderr, which a hook must not
                warnings.simplefilter("ignore")
                node = ast.parse(s.lstrip(), mode="eval").body
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            node = None
        parts = getattr(node, "values", None) if not isinstance(node, ast.Constant) else [node]
        value = "".join(p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else
                        p.value.decode("latin-1") if isinstance(p, ast.Constant) and isinstance(p.value, bytes) else
                        "{}" for p in parts) if isinstance(parts, list) else None
        if value is not None and value != bodies[0]:
            bodies.append(value)
        for body in bodies:
            if len(m.group(2)) == 3:
                body = body.strip()
                if body and len(body) <= 300 and not re.search(r"\s", body):
                    words.append(body)
                continue  # otherwise carried text: a file edit's old or new string, a docstring
            # a quoted span inside a one-line string is a literal too, as the regex read it: a shell command the
            # code builds (`"cat '<path>'"`), an f-string's inner literal before python 3.12 (the replay: 9 denies)
            words += [x.group(2).strip() for x in _LITERAL.finditer(body)]
            if "\n" not in body and len(body) <= 300:
                words.append(body.strip())
    bare, last = [], 0
    for a, b in sorted(cut):
        if a >= last:
            bare.append(code[last:a])
            last = b
    bare.append(code[last:])
    return words, "".join(bare)


def _lang(word):
    """"python" for a python interpreter's name (python, python3, python3.12), else None."""
    return "python" if _PYTHON.match(word or "") else None


# G8's filters (2026-11-18; a user organization's security keeper through its structure keeper, msg 01a1ecc1-40a4): a
# python one-liner searched a names-only secrets register for one credential kind, `if '<kind>' in row`, and was denied
# as reading a secret file by that name. A string the code only compares or searches for is a filter: an operand of
# `in`, `not in`, `==` or `!=` (alone or in a tuple, list or set), an argument of a string's own search (startswith,
# endswith, find, index, count, removeprefix…), the pattern of `re` or `fnmatch`, a `case` value. It is no read when
# nothing in the code can reach a file by a name it computes, since then the filter picks no file. That is told by a
# known list, not by a list of what reaches a file (of_secrets-review on organic-framework PR 323: such a list missed
# netrc, dotenv, configparser, linecache, toml, archive members and methodcaller): every name, attribute and call in
# the code is on it, and a file is opened only as `open('<literal>')` or `Path('<literal>').read_text()`, with no `$`
# or backtick in the literal, which the shell fills in before python runs (the same review). Anything else keeps the
# deny, as it was before.
_PY_STR_SEARCH = {"startswith", "endswith", "find", "rfind", "index", "rindex", "count", "removeprefix",
                  "removesuffix", "__contains__"}
_PY_RE_CALLS = {"search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split", "compile"}
_PY_FN_CALLS = {"fnmatch", "fnmatchcase", "filter"}
# never `type`: type(open('<literal>', 'rb', buffering=0)) is io.FileIO, which opens any name (of_secrets-review on
# organic-framework PR 323)
_PY_BUILTINS = frozenset((
    "print len sum min max sorted reversed enumerate zip range any all list dict set frozenset tuple str int float "
    "bool repr abs round map filter isinstance ord chr hex format divmod input iter next slice bytes hash "
    "id callable Exception ValueError KeyError IndexError TypeError AttributeError StopIteration OSError "
    "FileNotFoundError RuntimeError UnicodeDecodeError").split())
_PY_MODULES = {
    "csv": frozenset("reader DictReader writer DictWriter QUOTE_ALL QUOTE_MINIMAL QUOTE_NONE excel excel_tab".split()),
    "json": frozenset("load loads dump dumps JSONDecodeError".split()),
    "re": frozenset("search match fullmatch findall finditer sub subn split compile escape I IGNORECASE M MULTILINE "
                    "S DOTALL X VERBOSE A ASCII error".split()),
    "fnmatch": frozenset("fnmatch fnmatchcase filter translate".split()),
    "sys": frozenset("stdin stdout stderr argv exit".split()),
    "collections": frozenset("Counter defaultdict OrderedDict namedtuple deque".split()),
    "itertools": frozenset("groupby chain islice count takewhile dropwhile product combinations permutations "
                           "zip_longest accumulate".split()),
}
# methods of what those give: a string, a list, a dict, a set, a Counter, a file opened by a literal or sys.stdin, a
# csv reader or writer, a regular expression and its match
_PY_METHODS = frozenset((
    "lower upper strip lstrip rstrip split rsplit splitlines join replace startswith endswith find rfind index rindex "
    "count partition rpartition removeprefix removesuffix casefold title capitalize isdigit isalpha isalnum isspace "
    "isupper islower zfill ljust rjust center encode decode expandtabs format "
    "append extend insert pop remove sort reverse copy get items keys values setdefault update add discard union "
    "intersection difference clear most_common elements "
    "read readline readlines write writerow writerows writeheader flush close fieldnames "
    "group groups groupdict span start end pattern search match fullmatch findall finditer sub subn").split())
_PY_PATH_READS = frozenset(("read_text", "read_bytes", "open"))


def _py_plain(node):
    """A literal python sees as written: a constant with no `$`, backtick or `$( )` (which the parser shows as
    __OFSUB__), all of which the shell fills in first; a bytes literal the same (of_review on organic-framework PR 323:
    open(b'$F') opened what the shell put there)."""
    import ast as _ast
    if not isinstance(node, _ast.Constant):
        return False
    value = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
    return not (isinstance(value, str) and re.search(r"[$`]|__OFSUB", value))


def _py_reaches(tree, parent):
    """Can python code (its ast; PARENT maps each node to its parent) reach anything the guard does not know? True
    unless every name, attribute and call in it is on G8's known list and it opens files only by plain literals."""
    import ast as _ast
    import builtins as _builtins
    modules, path_names, bound = {}, set(), set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Import):
            for a in n.names:
                if a.name == "pathlib":
                    path_names.add(("module", a.asname or a.name))
                elif a.name in _PY_MODULES:
                    modules[a.asname or a.name] = a.name
                else:
                    return True
        elif isinstance(n, _ast.ImportFrom):
            for a in n.names:
                if n.module == "pathlib" and a.name == "Path" and not n.level:
                    path_names.add(("class", a.asname or a.name))
                elif n.level or n.module not in _PY_MODULES or a.name not in _PY_MODULES[n.module]:
                    return True
                else:
                    bound.add(a.asname or a.name)
        elif isinstance(n, _ast.Name) and not isinstance(n.ctx, _ast.Load):
            bound.add(n.id)
        elif isinstance(n, _ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, _ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (getattr(_ast, "MatchAs", ()), getattr(_ast, "MatchStar", ()))) and n.name:
            bound.add(n.name)
    names = bound | set(modules) | {x for _, x in path_names}
    if any(x.startswith("__") or (x in vars(_builtins) and x not in _PY_BUILTINS) for x in names):
        return True  # `eval = print` after an eval, `import csv as open`, a dunder

    def path_class(f):  # Path or pathlib.Path
        return ((isinstance(f, _ast.Name) and ("class", f.id) in path_names) or
                (isinstance(f, _ast.Attribute) and f.attr == "Path" and isinstance(f.value, _ast.Name) and
                 ("module", f.value.id) in path_names))

    def plain_call(c):
        return isinstance(c, _ast.Call) and all(_py_plain(a) for a in c.args) and all(
            k.arg and _py_plain(k.value) for k in c.keywords)

    for n in _ast.walk(tree):
        p = parent.get(n)
        called = isinstance(p, _ast.Call) and p.func is n
        if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load):
            if n.id == "open" or ("class", n.id) in path_names:
                if not (called and plain_call(p)):
                    return True
            elif ("module", n.id) in path_names:
                if not (isinstance(p, _ast.Attribute) and path_class(p)):
                    return True
            elif n.id not in names and n.id not in _PY_BUILTINS:
                return True
        elif isinstance(n, _ast.Attribute):
            if n.attr.startswith("_"):
                return True
            if path_class(n):
                if not (called and plain_call(p)):
                    return True
            elif isinstance(n.value, _ast.Name) and n.value.id in modules:
                if n.attr not in _PY_MODULES[modules[n.value.id]]:
                    return True
            elif n.attr in _PY_PATH_READS and isinstance(n.value, _ast.Call) and path_class(n.value.func):
                if not (called and plain_call(p) and plain_call(n.value)):
                    return True
            elif n.attr not in _PY_METHODS:
                return True
    return False


def _py_filters(code):
    """The strings python CODE only compares or searches for (each stripped), when the code reaches no file by a name it
    computes (_py_reaches); else, or when the code does not parse, none. A string the code also uses any other way, or
    one that is part of such a string, is no filter. Any slip here gives none: the deny stands, never the guard's
    looser fallback match."""
    try:
        return _py_filters_of(code)
    except Exception:  # noqa: BLE001
        return frozenset()


def _py_filters_of(code):
    import ast as _ast
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = _ast.parse(code)
    parent = {}
    for n in _ast.walk(tree):
        for c in _ast.iter_child_nodes(n):
            parent[c] = n
    if _py_reaches(tree, parent):
        return frozenset()
    filters, others = set(), []
    for n in _ast.walk(tree):
        if not isinstance(n, _ast.Constant) or not isinstance(n.value, (str, bytes)):
            continue
        value = n.value if isinstance(n.value, str) else n.value.decode("latin-1")
        node, p = n, parent.get(n)
        if isinstance(p, (_ast.Tuple, _ast.List, _ast.Set)):
            node, p = p, parent.get(p)
        is_filter = False
        if isinstance(p, _ast.Compare) and all(isinstance(o, (_ast.In, _ast.NotIn, _ast.Eq, _ast.NotEq))
                                               for o in p.ops):
            is_filter = node is p.left or node in p.comparators
        elif isinstance(p, _ast.keyword):
            call = parent.get(p)
            f = getattr(call, "func", None)
            is_filter = (isinstance(f, _ast.Attribute) and isinstance(f.value, _ast.Name) and
                         ((f.value.id in ("re", "regex") and f.attr in _PY_RE_CALLS and p.arg == "pattern") or
                          (f.value.id == "fnmatch" and f.attr in _PY_FN_CALLS and p.arg == "pat")))
        elif isinstance(p, _ast.Call) and node in p.args:
            f, i = p.func, p.args.index(node)
            if isinstance(f, _ast.Attribute) and f.attr in _PY_STR_SEARCH and f.value is not node:
                is_filter = True
            elif isinstance(f, _ast.Attribute) and isinstance(f.value, _ast.Name) and f.value.id in ("re", "regex"):
                is_filter = f.attr in _PY_RE_CALLS and i == 0 and node is n
            elif isinstance(f, _ast.Attribute) and isinstance(f.value, _ast.Name) and f.value.id == "fnmatch":
                is_filter = f.attr in _PY_FN_CALLS and i == 1 and node is n
            elif isinstance(f, _ast.Name) and f.id in ("fnmatch", "fnmatchcase"):
                is_filter = i == 1 and node is n
        elif hasattr(_ast, "MatchValue") and isinstance(p, _ast.MatchValue):
            is_filter = True
        (filters.add(value.strip()) if is_filter else others.append(value))
    return frozenset(v for v in filters if v and not any(v in o for o in others))


def plugin_deny(tool, excluded):
    """G22: (plugin id, basis) when TOOL is an MCP tool of a plugin in EXCLUDED ([{plugin, basis}], the session's
    organization's `tools_excluded`), else None. Claude Code names a plugin's MCP tools
    `mcp__plugin_<plugin>_<server>__<tool>`; the plugin id's part before `@` is the name."""
    tool = str(tool or "").lower()  # case-insensitive: it leans to the deny (of_secrets-review on PR 124)
    if not tool.startswith("mcp__plugin_"):
        return None
    for e in excluded or []:
        pid = str((e or {}).get("plugin") or "")
        name = pid.split("@", 1)[0].lower()
        for n in {name, re.sub(r"[^a-z0-9_-]", "_", name)}:
            if n and tool.startswith(f"mcp__plugin_{n}_"):
                return pid, (e or {}).get("basis") or ""
    return None


def _service_label(url):
    """The service's name in an MCP server URL: the label left of the top-level domain (mcp.slack.com -> slack)."""
    from urllib.parse import urlparse
    host = (urlparse(str(url or "").replace("*", "")).hostname or "").lower()
    labels = [x for x in host.split(".") if x]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def connector_deny(tool, excluded):
    """G22: (url, basis) when TOOL is a tool of a claude.ai connector excluded by the organization, else None. Claude
    Code names a connector's tools `mcp__claude_ai_<Name>__<tool>` (the account's "claude.ai <Name>"). An entry is
    matched by its `connector` name when it gives one, else by the service in its url (https://mcp.slack.com/* ->
    Slack); case is ignored (a user organization's card e2bb5eef, through its structure keeper, msg 01a1d6c3-fe78)."""
    tool = str(tool or "").lower()
    if not tool.startswith("mcp__claude_ai_") or "__" not in tool[len("mcp__claude_ai_"):]:
        return None
    name = tool[len("mcp__claude_ai_"):].split("__", 1)[0]
    for e in excluded or []:
        e = e or {}
        want = re.sub(r"[^a-z0-9]+", "_", (e.get("connector") or "").lower()).strip("_") or _service_label(e.get("url"))
        if want and (name == want or name.startswith(want + "_")):
            return str(e.get("url") or e.get("connector")), e.get("basis") or ""
    return None


# a write from interpreter code: a file opened to write or append, a whole-file write, a move, a removal or a dump
# (of_review and of_secrets-review on organic-framework PR 387: python's open(…, 'a').write, ruby -e, node -e)
# `.write(` is a write but for sys.stdout and sys.stderr, which print (of_review's note on organic-framework PR 410):
# a handle may come from Path.open('w'), os.open, a mode held in a name, a node stream (of_secrets-review on PR 413)
_CODE_WRITE = re.compile(r"""open\s*\(.*?,\s*['"][rbtU]*[wax+][rbtwax+U]*['"]|open\s*\([^,]*,\s*['"]\s*\+?>|\bmode\s*=\s*['"][rbt]*[wax+]|write_text|"""
                         r"""write_bytes|(?<!sys\.stdout)(?<!sys\.stderr)\.write\s*\(|\bdump\s*\(|"""
                         r"""os\.(?:replace|rename|remove|unlink|truncate)|"""
                         r"""shutil\.|\.unlink\s*\(|\.touch\s*\(|\.rename\s*\(|File\.(?:write|delete|rename)|"""
                         r"""File\.open\s*\(.*?,\s*['"][rbt]*[wa+]|"""
                         r"""FileUtils\.|fs\.(?:write|append|rename|unlink|rm|copy)|writeFile|appendFile|"""
                         r"""\bsysopen\b|\bunlink\b|\brename\b""")


def _owner_config_in_code(code, lits):
    """The path G33 protects that interpreter CODE names and may write, else None: a literal that is orgs.yaml or
    views.yaml in OF's config folder (or the bare name, with `.config` named in the code too), beside a write."""
    names = [l.strip() for l in lits if not re.search(r"\s", l.strip())   # one path, as G8 reads one
             and os.path.basename(l.strip().rstrip("/")) in OWNER_CONFIG_FILES]
    if not names or not _CODE_WRITE.search(code):
        return None
    targets = _owner_config_targets()
    for n in names:
        if os.path.realpath(_expanduser(n)) in targets or \
                re.search(r"(?:^|/)\.config/of/(?:orgs|views)\.yaml$", n):   # a home folder joined in code
            return n
    return None


def _code_deny(code, lang=None):
    """Code an interpreter runs (a heredoc, a -c/-e string or a here-string): a string literal that is a
    secret path (open('.env')), a literal that is an env dump (subprocess.run(['printenv'])), or a dump of
    the whole environment (print(os.environ)). A false positive is accepted over a miss here: the agent
    writes such code to a file with the Edit tool. Comments do not count. LANG "python" reads the code with
    python's tokenizer; python code that does not tokenize is read with no carve-out at all."""
    py = _py_strings(code) if lang == "python" else None
    if py is not None:
        lits, bare = py
    else:
        # os.environ / process.env named in a STRING the code edits is text, not a dump: strip string literals first
        bare = re.sub(r"'''.*?'''|\"\"\".*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\"", "", code, flags=re.S)
        # a triple-quoted block is text the code carries (a file edit's old and new strings), never a path it
        # opens: its inner quotes are not literals (docs/guard.md G8; of_capacity, of_platform, of_server,
        # 2026-11-07). A block that is one word once stripped is read as a literal: `open('''~/.ssh/id_ed25519''')`
        # read the key (of_review and of_secrets-review on organic-framework PR 93). Python code that does not
        # tokenize gets no carve-out: every quoted span is a literal (of_secrets-review on organic-framework PR 97).
        carried = code if lang == "python" else re.sub(r"'''.*?'''|\"\"\".*?\"\"\"", " ", code, flags=re.S)
        lits = _block_words(code) + [m.group(2).strip() for m in _LITERAL.finditer(carried)]
    if _ENV_DUMP_CODE.search(bare):
        return _prints("the whole environment")
    if _TOOL_TOKEN_CODE.search(re.sub(r"'''.*?'''|\"\"\".*?\"\"\"", " ", code, flags=re.S)):  # G23 in code;
        # a triple-quoted block is text an edit carries (G8), not a call (of_secrets-review on organic-framework PR 136)
        return TOOL_TOKEN_DENY, "of_tools in code"
    if _PERSON_ACT_CODE.search(re.sub(r"'''.*?'''|\"\"\".*?\"\"\"", " ", code, flags=re.S)):  # G24 in code
        if PERSON_ACT_MODE == "deny":
            return PERSON_ACT_DENY, "a person's act written by code"
        note = ("code that writes what only a person does", "a person's act written by code", PERSON_ACT_NOTE)
        if note not in _WARNINGS:  # the code is read on more than one path; note it once
            _WARNINGS.append(note)
    if _GRANT_SIGN_CODE.search(bare):  # G13: signing a grant from code (of_grants.sign_ed25519, of's cmd_grant)
        return GRANT_DENY, _GRANT_SIGN_CODE.search(bare).group(0)
    box = next((l for l in lits if not re.search(r"\s", l.strip()) and l.strip().lstrip("<>+|& ").startswith(("/", "~", "$"))
                and (re.search(r"/(?:" + "|".join(map(re.escape, ASKS_OWNER_FOLDERS)) + r")(?:/|$)", l.casefold())
                     or _in_outbox(l.strip().lstrip("<>+|& ")))),
               None) \
        if _CODE_WRITE.search(code) else None   # G35 in code: a write into the owner's ask folders (PRs 395, 410)
    fwd = next((l for l in lits if not re.search(r"\s", l.strip()) and l.strip().lstrip("<>+|& ").startswith(("/", "~", "$"))
                and _in_folders(l.strip().lstrip("<>+|& "), {_forward_dir().casefold()})), None) \
        if _CODE_WRITE.search(code) else None   # G34 in code: a write into the forward's folder (of_review on PR 397)
    if fwd:
        return FORWARD_DENY, fwd
    idx = next((l for l in lits if not re.search(r"\s", l.strip())
                and l.strip().lstrip("<>+|& ").startswith(("/", "~", "$"))
                and (_in_session_index(l.strip().lstrip("<>+|& "))
                     or re.search(r"(?:^|/)\.local/state/of/index\.json(?:\.\d+\.\d+\.tmp)?$", l.strip().casefold()))),
               None) if _CODE_WRITE.search(code) else None   # G36 in code: a write to the session index
    if idx:
        if SESSION_INDEX_MODE == "deny":
            return SESSION_INDEX_DENY, idx
        _WARNINGS.append((SESSION_INDEX_DENY, idx, "a warning now, a deny once the replay shows no agent writes it"))
    if box:
        if ASKS_OUTBOX_MODE == "deny":
            return ASKS_OUTBOX_DENY, box
        _WARNINGS.append((ASKS_OUTBOX_DENY, box, "a warning now, a deny once the flows that write it have moved"))
    owner_cfg = _owner_config_in_code(code, lits)  # G33 in code: a write to orgs.yaml or views.yaml
    if owner_cfg:
        if OWNER_CONFIG_MODE == "deny":
            return OWNER_CONFIG_DENY, owner_cfg
        _WARNINGS.append((OWNER_CONFIG_DENY, owner_cfg, "a warning now, a deny once the flows that write it have moved"))
    filters = None
    for lit in lits:
        if _keychain_name(lit):  # G20: a keychain item an organization protects, named in code (Security framework)
            return KEYCHAIN_DENY, _keychain_name(lit)
        if " " not in lit and _secret_path(lit):
            if py is not None:
                filters = _py_filters(code) if filters is None else filters
                if lit.strip() in filters:
                    continue  # a word the code only compares or searches for, and no file it may open by a name
            return _code_reads(lit)
        if " " not in lit and _MEMORY_CODE.match(lit):
            return f"code that reads memory or a core dump ({lit})", lit
        first = lit.split()[0] if lit else ""
        # a bare 'env' or 'set' literal is a dict key or a word far more often than a command: not matched
        if os.path.basename(first) == "printenv" or lit == "export -p" or re.match(r"env\s*\|", lit):
            return _prints(first)
    return None


def _scan(text, i=0, nested=False):
    """One pass over a command line as the shell reads it, with quotes tracked. Returns
    (the line with heredoc bodies cut and each $( ) / backtick replaced by a placeholder,
     [the bodies of $( ) and backticks the shell would run],
     [(delimiter quoted?, heredoc body, the text before << on its line)],
     the index of the closing parenthesis when nested).
    A substitution counts outside single quotes (also inside double quotes); a heredoc outside any quotes."""
    n, out, subs, docs, pending = len(text), [], [], [], []
    single = double = False
    depth = 0
    while i < n:
        c = text[i]
        if c == "\n" and pending and not single and not double:
            out.append(c)
            i += 1
            for strip, quoted, delim, opener in pending:
                body = []
                while i < n:
                    e = text.find("\n", i)
                    e = n if e < 0 else e
                    line, i = text[i:e], e + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    body.append(line)
                docs.append((quoted, "\n".join(body), opener))
            pending = []
            continue
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == "$" and not double and text.startswith('$"', i):
            i += 1  # $"…" is "…" (bash's locale translation; of_secrets-review on organic-framework PR 332)
            continue
        elif c == "$" and not double and text.startswith("$'", i) and _ANSI_WORD.match(text, i):
            # a $'…' word: its escapes (\' included) are bash's, so it is read whole and decoded, and the line
            # every rule reads holds its value single-quoted (of_secrets-review on organic-framework PR 97)
            m = _ANSI_WORD.match(text, i)
            out.append(shlex.quote(_ansi_c_body(m.group(1))))
            i = m.end()
            continue
        elif c == "'" and not double:
            single = True
        elif c == '"':
            double = not double
        elif text.startswith("$(", i) and not text.startswith("$((", i):
            j = _scan(text, i + 2, nested=True)[3]
            subs.append(text[i + 2:j])
            out.append("__OFSUB__")
            i = j + 1
            continue
        elif c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            subs.append(text[i + 1:j])
            out.append("__OFSUB__")
            i = j + 1
            continue
        elif not double and text.startswith("<<<", i):  # a here-string, not a heredoc: left inline for _simple
            out.append("<<<")
            i += 3
            continue
        elif not double and text.startswith("<<", i):
            m = _HEREDOC.match(text, i)
            if m:
                pending.append((m.group(1) == "-", bool(m.group(2)), m.group(3), text[text.rfind("\n", 0, i) + 1:i]))
                out.append("<< __OFDOC__")
                i = m.end()
                continue
        elif nested and not double:
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    return "".join(out), subs, docs, i
                depth -= 1
        out.append(c)
        i += 1
    return "".join(out), subs, docs, n


def _segments(text):
    """Simple commands as token lists: split on ; & | ( ) and newlines outside quotes (quotes removed)."""
    import shlex
    # a backslash-newline joins the words, as bash reads it; a comment starts only at a word's start (G0.7;
    # of_secrets-review on organic-framework PR 178)
    lex = shlex.shlex(_strip_comments(text.replace("\\\n", "")), posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    segs, cur = [], []
    for tok in lex:
        if tok and set(tok) <= _SEPS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    return segs + ([cur] if cur else [])


# The options of a wrapper that take a value as the next word: without them `nice -n 5 cat <key>`, `sudo -u root cat
# <key>` and `exec -a x …` read the value as the command, and no rule saw what ran (G0.6; of_server, 2026-11-14,
# probing G23).
def _opt_takes_next(word, opts, flags=frozenset()):
    """Does wrapper option WORD take the next word as its value? getopt's reading: `-u` or `--user` named in OPTS does;
    so does a cluster of short options whose last letter takes a value (`-nu root`, `-lo file`, `-ca label`); one whose
    value-taking letter is not its last holds its value in the rest of the word (`-uroot`, `-i0`). G0.6
    (of_secrets-review on organic-framework PR 332: `sudo -nu root cat <a key>` passed every rule)."""
    if word in opts:
        return 1
    if word.startswith("--") or len(word) < 3 or not word.startswith("-"):
        return 0
    for k, c in enumerate(word[1:], 1):
        if f"-{c}" in opts:
            return 1 if k == len(word) - 1 else 0
        if f"-{c}" not in flags:
            return 0  # a letter the tables do not know: the next word may be the command (of_secrets-review, PR 332)
    return 0


def _tokens_or_empty(stext):
    try:
        return _tokens(stext)
    except ValueError:
        return []


def _option_known(word, opts, flags):
    """Is every letter of a wrapper's option one the tables know, up to the one that takes a value?"""
    if word in opts or word in flags or word.startswith("--"):
        return True
    for c in word[1:]:
        if f"-{c}" in opts:
            return True
        if f"-{c}" not in flags:
            return False
    return True


# a duration timeout reads with strtod: 2, .5, 1e1, +2, 0x10, inf (of_secrets-review on organic-framework PR 332)
_TIMEOUT_DURATION = re.compile(r"[+-]?(?:inf(?:inity)?|nan|0[xX][0-9a-fA-F.]+(?:[pP][+-]?\d+)?"
                               r"|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)[smhd]?", re.I)
_REDIRECTION = re.compile(r"\d*(?:<<<|[<>]{1,2}|&>>?|>&|<&)")
_NEGATIVE = re.compile(r"-(?:\d|\.\d)\S*")  # `timeout -1 cat`: a negative duration, not an option


# wrappers whose every word is read as a possible command line: `script` exists to run one, and six review passes on
# organic-framework PR 332 found each new branch of its reader dropping a reading the old one had (of_secrets-review)
_DOUBT_ALWAYS = frozenset({"script"})


def _redirection_at(words, i):
    """How many tokens a redirection at WORDS[i] takes (its operator and its target), or 0. The tokenizer splits
    `2>/dev/null` into `2`, `>`, `/dev/null`, so a number before an operator belongs to it."""
    k = i + 1 if words[i].isdigit() and i + 1 < len(words) and _REDIRECTION.fullmatch(words[i + 1]) else i
    if _REDIRECTION.fullmatch(words[k]):  # `>&`, `<<<`: the operator, then its target (`2>&1`: of_secrets-review)
        return k - i + 2
    return k - i + 1 if _REDIRECTION.match(words[k]) else 0


# long options whose value GNU takes only attached, `--flag=value`: bare, each is a flag (of_secrets-review on PR 332:
# `xargs --verbose --replace cat <key>` read `cat` as --replace's value)
_OPTIONAL_LONG = {"xargs": frozenset({"--replace", "--eof", "--max-lines"})}


def _option_exact(word, opts, flags, wrapper=None):
    """Is WORD an option the wrapper's tables read exactly: a name they hold (`--kill-after`, never the abbreviation
    `--kill-a`), `--name=value` for a value option they hold, or a cluster of short letters they all know up to the
    one that takes a value?"""
    if word in opts or word in flags:
        return True
    if word.startswith("--"):
        name = word.split("=", 1)[0]
        return "=" in word and (name in opts or name in _OPTIONAL_LONG.get(wrapper, ()))
    for c in word[1:]:
        if f"-{c}" in opts:
            return True
        if f"-{c}" not in flags:
            return False
    return True


def _wrapper_readings(line):
    """_wrapper_readings_of LINE; and, when a number stands before a redirection, also with that number kept as a word.
    The tokens cannot tell `7>/dev/null` (a file descriptor) from `7 >/dev/null` (an argument, then a redirection), so
    both readings count (`timeout 7 >/dev/null cat <key>`: of_secrets-review on organic-framework PR 332)."""
    out = _wrapper_readings_of(line)
    if any(w.isdigit() and k + 1 < len(line) and _REDIRECTION.fullmatch(line[k + 1]) for k, w in enumerate(line)):
        kept = _wrapper_readings_of(line, fd_numbers=False)
        out += [x for x in kept if x not in out]
    return out


def _wrapper_readings_of(line, fd_numbers=True):
    """The extra readings of a simple command run through a wrapper, as shell lines, each judged like the line itself
    (critical_deny). The plain reading (_command_words) stays; these are added to it, and a deny by any of them denies
    (of_secrets-review on organic-framework PR 332, sixth pass: "keep the previous reading and add the new one, and deny
    when either does").

    A wrapper is read with doubt when it is `script` (_DOUBT_ALWAYS), or when one of its options is not one its tables
    read exactly: a letter they do not know (`sudo -Q root cat <key>`), an abbreviated long option (`timeout --kill-a 1 2
    cat <key>`), a redirection among its options (`script </dev/null -c '…'`). Then, from the wrapper's first argument:
    every tail of its words is a possible command (an xargs tail also with its first word as the replacement string,
    `xargs --max-a 1 -tJ MARK MARK <key>`); every word holding a space or a shell character, and the text after an
    option's `=` or after each letter of an option cluster (`-qc'cat <key>'`, `--comm='…'`), is a `bash -c` body; and
    what the line feeds the command's standard input (`<<< '…'`, `< file`) is what a shell in it may read. The line's
    leading redirections, keywords and assignments are passed over (`2>/dev/null script -q -c '…' /dev/null`)."""
    # the shell takes every redirection out of the command's words before it runs (`nice -n </dev/null 0 chrt …` gives
    # nice `-n 0`: of_secrets-review on PR 332); what they feed the command's standard input is read from them
    words, stdin, k = [], [], 0
    redir_at = set()   # where among the words a redirection stood: between a wrapper and its command, it is read with
    # doubt (`timeout 7 >/dev/null cat <key>`, `nice -n 7 >/dev/null …`: of_secrets-review on PR 332)
    while k < len(line):
        r = _redirection_at(line, k)
        if r and not fd_numbers and line[k].isdigit():
            words.append(line[k])   # read as an argument before a spaced redirection
            k += 1
            continue
        if r:
            redir_at.add(len(words))
            op = line[k + r - 2] if r >= 2 else ""
            if op == "<<<" and k + r - 1 < len(line):
                stdin.append(line[k + r - 1])
            elif op == "<" and k + r - 1 < len(line):
                stdin.append("bash " + shlex.quote(line[k + r - 1]))
            k += r
            continue
        words.append(line[k])
        k += 1
    i, extra = 0, []
    while i < len(words):
        r = _redirection_at(words, i)
        if r:
            i += r
            continue
        if words[i] in ("do", "then", "else", "elif", "if", "while", "until", "!", "{", "coproc") or \
                re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
            continue
        w = _fold_name(os.path.basename(words[i]))
        if not (w in _WRAPPERS or w in ("timeout", "env")):
            return extra
        opts, flags = _WRAPPER_VALUE_OPTS.get(w, frozenset()), _WRAPPER_FLAGS.get(w, frozenset())
        i += 1
        start, doubt = i, w in _DOUBT_ALWAYS
        while not doubt and i < len(words) and words[i] != "--":
            if words[i].startswith("-") and not (w == "timeout" and _NEGATIVE.fullmatch(words[i])):
                doubt = not _option_exact(words[i], opts, flags, w)
                i += 0 if doubt else 1 + _opt_takes_next(words[i], opts, flags)
            elif w == "env" and "=" in words[i]:
                i += 1
            else:
                break
        if w == "xargs":  # the file -a names is what xargs reads (of_secrets-review on PR 332)
            extra += ["cat " + shlex.quote(f) for f in _xargs_arg_files(words[start:i + (1 if doubt else 0)])]
        if doubt:
            return extra + _doubted(words[start:], xargs=w == "xargs", stdin=stdin)
        if i < len(words) and words[i] == "--":
            i += 1
        if w == "timeout" and i < len(words):
            i += 1  # its duration, whatever its form
        if w == "chrt" and i < len(words) and re.search(r"[$`{*?\[]|__OF|^~", words[i]):
            # `p=0; chrt -o "$p" cat <key>`: the priority, or the command, as the shell fills it in
            return extra + _doubted(words[i:], stdin=stdin)
        if w in _WRAPPER_POSITIONAL and i < len(words) and _positional_here(w, words[i]):
            i += _WRAPPER_POSITIONAL[w]
        if w != "exec" and any(start <= r <= i for r in redir_at):   # exec's are the shell's own (`exec 3<>…`)
            return extra + _doubted(words[start:], xargs=w == "xargs", stdin=stdin)
    return extra


def _positional_here(w, word):
    """Is WORD the wrapper W's positional operand? chrt's priority is optional for SCHED_OTHER, BATCH and IDLE, so it
    is one only when it is a number (`chrt -o cat <key>`: of_secrets-review on organic-framework PR 332)."""
    return w != "chrt" or bool(re.fullmatch(r"[+-]?\d+", word))


_SHELL_CHARS = re.compile(r"[\s;&|()<>`$]")
# What the readings of one check may hold in all, in characters, and how deep they may go: past either the command is
# denied, never passed (of_review on organic-framework PR 332 at 16cfcee: an xargs line of 384 words took 41 s, past the
# hook's 30 s, so the hook was killed and the deny lost; and four subshells around a doubted timeout put its readings past
# G0's depth, where a reading returned nothing). The guard read about 45 microseconds a character there, so the bound keeps the
# readings to a few seconds.
READINGS_MAX_CHARS = 60_000
_READINGS_SPENT = [0]
_PAST_BUDGET = "\0past the readings' budget"  # _doubted's one line when its tails would not fit
WRAPPER_BOUND_DENY = ("a command run through a wrapper the guard must read every way (`script`, or an option it does not "
                      "read exactly) is too long or nested too deep to read within the hook's time. Use the wrapper's full "
                      "option names, or write the command to a script with the Edit tool and run that")


def _readings_past_bound(lines, depth):
    """WRAPPER_BOUND_DENY's (reason, subject) when LINES, judged at DEPTH + 1, would pass G0's depth or the readings'
    budget for this check (READINGS_MAX_CHARS); else None. Counts LINES against the budget."""
    if depth + 1 > DEPTH_CAP:
        return WRAPPER_BOUND_DENY, "a wrapper's readings, nested past G0's depth"
    if _PAST_BUDGET in lines:
        return WRAPPER_BOUND_DENY, "a wrapper's readings, past their budget"
    _READINGS_SPENT[0] += sum(len(x) for x in lines)
    if _READINGS_SPENT[0] > READINGS_MAX_CHARS:
        return WRAPPER_BOUND_DENY, "a wrapper's readings, past their budget"
    return None


def _line_doubted(text):
    """Does any simple command of the line TEXT run a doubted wrapper? A heredoc whose opener names no command
    (`<<'END' script -q`) is fed to the command after it (of_secrets-review on organic-framework PR 332)."""
    try:
        pipes = _split(text)
    except ValueError:
        return True
    return any(kind == "cmd" and _wrapper_readings(_tokens_or_empty(st)) for pipe in pipes for kind, st in pipe)


def _doubted(rest, xargs=False, stdin=()):
    """_wrapper_readings' lines for a doubted wrapper's words REST; its standard input is read from the whole LINE, so a
    here-string or a `<` before the wrapper counts (`<<< '…' script -q`: of_secrets-review on organic-framework PR 332).
    Never empty: a doubted wrapper always gives its readings' check a line to judge (`script <<'END'` with no words)."""
    out = ([""] if not rest else []) + list(stdin)
    # the tails' size, from each word's quoted length, before any is built: past what the check has left, one marker
    # stands for them all and the check denies (of_review on organic-framework PR 332 at a1842fc: building every tail
    # first took 7.3 s for 3,000 words and 320 s for 20,000)
    quoted = [shlex.quote(t) for t in rest]
    size, total = 0, 0
    for j in range(len(rest) - 1, -1, -1):
        size += len(quoted[j]) + 1
        if not (rest[j].startswith("-") or _REDIRECTION.match(rest[j])):
            total += size * (2 if xargs else 1)
    if total > READINGS_MAX_CHARS - _READINGS_SPENT[0]:
        return [_PAST_BUDGET]
    for j in range(len(rest)):
        if rest[j].startswith("-") or _REDIRECTION.match(rest[j]):
            continue  # no command is named so (its attached text is read below)
        out.append(" ".join(quoted[j:]))
        if xargs:
            out.append(" ".join(["cat"] + quoted[j + 1:]))
    for k, wd in enumerate(rest):
        if wd == "<<<" and k + 1 < len(rest):
            out.append(rest[k + 1])                        # a here-string, read by a shell in the command
            continue
        if wd == "<" and k + 1 < len(rest):
            out.append("bash " + shlex.quote(rest[k + 1]))  # a file on its standard input, run by a shell in it
            continue
        if _SHELL_CHARS.search(wd):
            out.append(wd)
        if wd.startswith("-") and len(wd) > 2:
            if "=" in wd:
                out.append(wd.split("=", 1)[1])
            for n, c in enumerate(wd[1:], 1):
                if not c.isalpha():
                    break
                if wd[n + 1:]:
                    out.append(wd[n + 1:])
    seen, lines = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            lines.append(x)
    return [x for x in lines if x] or [":"]   # `:` judges nothing, but says a doubted wrapper runs here


def _xargs_arg_files(opts):
    """The files xargs's -a or --arg-file names among its options: -a F, -aF, a cluster ending in a (-ta F),
    --arg-file F, --arg-file=F."""
    out, k = [], 0
    while k < len(opts):
        o = opts[k]
        if o.startswith("--arg-file="):
            out.append(o.split("=", 1)[1])
        elif o == "--arg-file" and k + 1 < len(opts):
            out.append(opts[k + 1])
            k += 1
        elif o.startswith("-") and not o.startswith("--"):
            for j, ch in enumerate(o[1:], 1):
                rest = o[j + 1:]
                if ch == "a":
                    if rest:
                        out.append(rest)
                    elif k + 1 < len(opts):
                        out.append(opts[k + 1])
                        k += 1
                    break
                if f"-{ch}" in _WRAPPER_VALUE_OPTS["xargs"]:
                    k += 0 if rest else 1
                    break
        k += 1
    return [f for f in out if f]


def _xargs_replstr(opts):
    """xargs's replacement string among its options, or None: -I X, -IX, -J X, -JX, --replace=X, -i[X] or --replace
    as {}, read the getopt way inside a cluster (`xargs -tJ MARK MARK <key>`: of_secrets-review on PR 332)."""
    vals = _WRAPPER_VALUE_OPTS["xargs"]
    out, k = None, 0
    while k < len(opts):
        o = opts[k]
        if o.startswith("--replace="):
            out = o.split("=", 1)[1]
        elif o == "--replace":
            out = "{}"
        elif o.startswith("-") and not o.startswith("--"):
            for j, ch in enumerate(o[1:], 1):
                rest = o[j + 1:]
                if ch in "IJ":
                    if rest:
                        out = rest
                    elif k + 1 < len(opts):
                        out, k = opts[k + 1], k + 1
                    break
                if ch == "i":  # GNU: an optional string, attached
                    out = rest or "{}"
                    break
                if f"-{ch}" in vals:
                    k += 0 if rest else 1
                    break
        k += 1
    return out


def _env_split_cluster(word):
    """The string an env cluster holding -S gives (`-iS'cat x'` -> "cat x"; "" when S ends the cluster and the next word
    is the string), when every letter before S is a flag env knows; else None (of_secrets-review on PR 332)."""
    if not word.startswith("-") or word.startswith("--") or len(word) < 3:
        return None
    for k, c in enumerate(word[1:], 1):
        if c == "S":
            return word[k + 1:]
        if f"-{c}" not in _WRAPPER_FLAGS["env"]:
            return None
    return None


_WRAPPER_VALUE_OPTS = {
    "sudo": {"-u", "-g", "-U", "-C", "-D", "-h", "-p", "-r", "-R", "-t", "-T", "--user", "--group", "--other-user",
             "--close-from", "--chdir", "--chroot", "--host", "--prompt", "--role", "--type", "--command-timeout"},
    "doas": {"-u", "-C"}, "nice": {"-n", "--adjustment"}, "exec": {"-a"}, "time": {"-o", "-f", "--output", "--format"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"}, "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-n", "-I", "-J", "-P", "-L", "-s", "-d", "-E", "-a", "-R", "-S", "--max-args", "--max-procs",
              "--max-chars", "--delimiter", "--arg-file"},
    "env": {"-u", "--unset", "-C", "--chdir", "-P"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"},
    "caffeinate": {"-t", "-w"}, "taskset": set(),
    "chrt": {"-T", "-P", "-D", "--sched-runtime", "--sched-period", "--sched-deadline"},
    "taskpolicy": {"-c", "-d", "-g", "-t", "-l", "-p", "-m", "-j", "-P", "-S"}, "arch": {"-arch", "-e", "-d"},
    "script": {"-t", "-T", "-c", "--command"},
}
# the flags (no value) each wrapper knows, so a cluster of them before a value-taking letter is read getopt's way;
# a letter in neither table stops that reading (of_secrets-review on organic-framework PR 332)
_WRAPPER_FLAGS = {
    "sudo": frozenset("-A -B -b -E -e -H -i -K -k -l -N -n -P -S -s -V -v".split()),
    "doas": frozenset("-n -s -L".split()), "exec": frozenset("-c -l".split()),
    "time": frozenset("-p -l -a -h -v -q -V".split()), "timeout": frozenset("-v -f -p --foreground --preserve-status --verbose".split()),
    "xargs": frozenset("-0 -o -p -r -t -x --null --verbose --no-run-if-empty --interactive --exit --open-tty "
                       "--show-limits --replace --eof --max-lines".split()), "env": frozenset("-0 -i -v".split()),
    "ionice": frozenset(["-t"]), "caffeinate": frozenset("-d -i -m -s -u".split()),
    "taskpolicy": frozenset("-b -B -a -s -x -X".split()), "script": frozenset("-a -d -e -F -f -k -p -q -r".split()),
    "command": frozenset("-p -v -V".split()),  # bash's builtin: `command -v awk` (the PR 332 replay)
    # util-linux's man pages: taskset's mask (or list, with -c) and chrt's priority are their positional word; without
    # these, `taskset -c 0,16 nice …` read with doubt took a later `…/deploy/env` path for env (the PR 332 replay)
    "taskset": frozenset("-a -c -p --all-tasks --cpu-list --pid".split()),
    "chrt": frozenset("-a -b -d -e -f -i -m -o -p -r -R -v --all-tasks --batch --deadline --fifo --idle --max --other "
                      "--pid --rr --reset-on-fork --verbose".split()),
}


# a command name made of plain characters, whose case _command_words folds (a variable or a placeholder keeps its own)
_PLAIN_NAME = re.compile(r"[A-Za-z0-9._+-]+")


def _fold_name(name):
    """A command name as this Mac's volumes match it: NFKC, case-folded (`awK` with KELVIN SIGN, `ſed` with LONG S are
    awk and sed; of_secrets-review on organic-framework PR 332)."""
    import unicodedata
    return unicodedata.normalize("NFKC", name).casefold()
# A versioned or alternative name of an interpreter the rules know (2026-11-18, of_server, found while reviewing
# organic-framework PR 329: `python3.15 bin/of_tools.py header` passed G23, and the same name passed G1, G8 and G13,
# since every rule compares the command's name with python3, python, node, ruby, perl or php).
_INTERPRETER_NAME = re.compile(r"(?:(?P<py>python|pythonw|pypy)(?P<pv>\d+(?:\.\d+)*)?[tdm]{0,2}(?:-intel64|-arm64)?"
                               r"|(?P<pl>perl)5(?:\.\d+)*|(?P<rb>ruby)\d+(?:\.\d+)*|(?P<ph>php)\d+(?:\.\d+)*"
                               r"|(?P<nd>node)(?:js|\d+))")


def _interpreter_name(name):
    """The name the rules know for a versioned or alternative interpreter: python3.15, python3.13t, python3.7m,
    python3.12d, python3.15-intel64, pypy3 are python3; python2.7 and pypy are python; perl5.34 is perl, ruby3.3 ruby, php8.2 php, nodejs and node22
    node. Any other name (python3-config, nodemon, perldoc) is returned as it is."""
    m = _INTERPRETER_NAME.fullmatch(name or "")
    if not m:
        return name
    if m.group("py"):
        v = m.group("pv") or ("" if m.group("py") in ("python", "pythonw") else "2")
        return "python3" if v.startswith("3") else "python"
    return next(m.group(k) for k in ("pl", "rb", "ph", "nd") if m.group(k))


def _command_words(words):
    """Drop shell keywords, assignments, wrappers and `env VAR=x` prefixes: the words of the command that
    actually runs (`for h in a b; do ssh h 'cat x'; done` runs ssh). A versioned interpreter's name becomes the one
    the rules know (`/opt/homebrew/bin/python3.15` -> `/opt/homebrew/bin/python3`; _interpreter_name), in lower case:
    this Mac's volumes ignore case, so `CAT`, `HEAD` or `PYTHON3` run the real programs (of_secrets-review on
    organic-framework PR 332). Only the name is folded, never the folder it is in."""
    out = _command_words_of(words)
    if out:
        name = os.path.basename(out[0])
        # a variable or the parser's placeholder for one (`$T`, `__OFSUB__`) keeps its letters: names are case-sensitive
        folded = _fold_name(name)
        known = _interpreter_name(folded) if _PLAIN_NAME.fullmatch(folded) and "__OF" not in name else name
        if known != name:
            out = [out[0][:len(out[0]) - len(name)] + known] + list(out[1:])
    return out


def _command_words_of(words):
    if words and words[0] in ("for", "case", "select", "function", "done", "fi", "esac", "in", "}"):
        return []  # a loop header, a function header or a closer runs nothing itself
    i = 0
    while i < len(words):
        w = _fold_name(os.path.basename(words[i]))  # `SUDO`, `NICE`, `ENV` run the real ones too (G0.10)
        # `time` is a wrapper, with its options (G0.6), not a bare keyword: `time -p cat <key>` (of_secrets-review, PR 136)
        if words[i] in ("do", "then", "else", "elif", "if", "while", "until", "!", "{", "coproc"):
            i += 1
        elif re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        elif w in _WRAPPERS or w == "timeout":
            i += 1
            opts = _WRAPPER_VALUE_OPTS.get(w, ())
            start = i
            while i < len(words) and words[i].startswith("-") and not (w == "timeout" and _NEGATIVE.fullmatch(words[i])):
                if words[i] == "--":
                    i += 1
                    break
                i += 1 + _opt_takes_next(words[i], opts, _WRAPPER_FLAGS.get(w, frozenset()))
            if w == "timeout" and i < len(words):
                i += 1  # its duration, in any form strtod reads (` 2`, `nan(x)`, `-- 1e1`; of_secrets-review on PR 332)
            if w == "xargs" and i < len(words) and words[i] == _xargs_replstr(words[start:i]):
                # `echo cat | xargs -J X X <key>`: the utility is the replacement string, so the command comes from
                # input and no name rule sees it; its arguments are judged as a reader's (of_secrets-review on PR 332)
                words = words[:i] + ["cat"] + words[i + 1:]
            # GNU `script -c '<command>' <file>`: the string is a command line of its own, judged whole by
            # _wrapper_readings as `bash -c` is (a list in it, `true && cat <key>`: of_secrets-review on PR 332);
            # the command here is BSD's reading, the words after the typescript file
            if w in _WRAPPER_POSITIONAL and i < len(words) and _positional_here(w, words[i]):
                i += _WRAPPER_POSITIONAL[w]
        elif w == "env" and any(not x.startswith("-") and "=" not in x for x in words[i + 1:]):
            i += 1  # env [opts] [VAR=x] cmd ...: the command after it runs
            while i < len(words) and (words[i].startswith("-") or "=" in words[i]):
                rest = None if words[i] in ("-S", "--split-string") else _env_split_cluster(words[i])
                if (words[i] in ("-S", "--split-string") or rest == "") and i + 1 < len(words):
                    try:  # env -S 'cmd args', env -iS 'cmd args': the string is the command line (of_review on PR 136)
                        words = words[:i] + shlex.split(words[i + 1]) + words[i + 2:]
                    except ValueError:
                        i += 2
                    continue
                if rest:  # env -iS'cmd args', env -iSX=u: the rest of the cluster is the string
                    try:
                        words = words[:i] + shlex.split(rest) + words[i + 1:]
                    except ValueError:
                        i += 1
                    continue
                i += 1 + _opt_takes_next(words[i], _WRAPPER_VALUE_OPTS["env"], _WRAPPER_FLAGS["env"])
        else:
            break
    return words[i:]


def _head(prefix):
    try:
        segs = _segments(prefix)
    except ValueError:
        return ""
    words = _command_words(segs[-1]) if segs else []
    return os.path.basename(words[0]) if words else ""


_SSH_VALUE_OPTS = {"-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-I", "-m", "-O", "-Q",
                   "-S", "-W", "-w", "-B"}
_RUNNERS = {"docker", "podman", "nerdctl", "kubectl", "crictl", "limactl", "lima"}
_RUNNER_VALUE_OPTS = {"-e", "--env", "-u", "--user", "-w", "--workdir", "--env-file", "-c", "--container", "-n",
                      "--namespace", "--context"}
_DEV_STREAMS = ("/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/null")

# curl/wget: a secret file used as a CREDENTIAL is read by the program and not printed (allowed); a secret
# file UPLOADED or shown by a verbose trace leaks it (denied) (of_architect/of_platform, 2026-11-06).
_CURL_UPLOAD_AT = {"-F", "--form", "-d", "--data", "--data-ascii", "--data-binary", "--data-raw", "--data-urlencode"}
_CURL_UPLOAD_FILE = {"-T", "--upload-file"}
_CURL_CRED = {"--cert", "-E", "--key", "--cacert", "--capath", "-K", "--config", "--netrc-file", "--cert-type",
              "--key-type", "--pinnedpubkey", "--proxy-cert", "--proxy-key"}
_CURL_VERBOSE = {"-v", "--verbose", "--trace", "--trace-ascii", "--trace-config"}
_CURL_VALUE = _CURL_UPLOAD_AT | _CURL_UPLOAD_FILE | _CURL_CRED | {"-H", "--header", "-o", "--output", "-X"}


def _at_path(val):
    """The file an upload argument loads: '@file', 'name=@file', 'name@file' (urlencode); else None."""
    if val.startswith("@"):
        return val[1:].split(";")[0]
    m = re.match(r"[^=@]*=@(.+)", val) or re.match(r"[^@]+@(.+)", val)
    return m.group(1).split(";")[0] if m else None


def _curl_deny(args):
    has_cred = has_verbose = False
    i = 0
    while i < len(args):
        a, val = args[i], None
        if a.startswith("--") and "=" in a:
            flag, val = a.split("=", 1)
        elif a.startswith("-") and not a.startswith("--") and len(a) > 2:
            flag, val = a[:2], a[2:]
        else:
            flag = a
        if val is None and flag in _CURL_VALUE and i + 1 < len(args):
            val, i = args[i + 1], i + 1
        if val and flag in _CURL_UPLOAD_AT:
            p = _at_path(val)
            if p and _secret_path(p):
                return _reads(p)
        if val and flag in _CURL_UPLOAD_FILE and _secret_path(val):
            return _reads(val)
        # `-H @file` sends a header file (a Grafana bearer header): use, like -K, but a trace prints it (G1.7)
        has_cred = (has_cred or flag in _CURL_CRED
                    or (flag in ("-H", "--header") and bool(val) and val.startswith("@") and _secret_path(val[1:])))
        has_verbose = has_verbose or flag in _CURL_VERBOSE
        i += 1
    if has_cred and has_verbose:
        return "a verbose curl trace with a credential (the secret would be printed)", "curl -v"
    return None


def _wget_deny(args):
    for k, a in enumerate(args):
        m = re.match(r"--(?:post-file|body-file)=(.+)", a)
        p = m.group(1) if m else (args[k + 1] if a in ("--post-file", "--body-file") and k + 1 < len(args) else None)
        if p and _secret_path(p):
            return _reads(p)
    return None


def _strip_comments(text):
    """The line without its comments, as bash reads them: `#` starts one only at the start of a word, outside quotes.
    shlex took a `#` inside a word for one, so `cat a#b <secret>` read as `cat a` (G0.7, 2026-11-14)."""
    out, i, n, single, double, start = [], 0, len(text), False, False, True
    while i < n:
        c = text[i]
        if c == "\\" and not single and i + 1 < n:  # an escaped character, a blank too, belongs to the word
            out.append(text[i:i + 2])               # (`a\ #b` is one word; of_review on organic-framework PR 178)
            i += 2
            start = False
            continue
        if c == "#" and start and not single and not double:
            j = text.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        start = not single and not double and c in " \t\r\n;&|()"
        out.append(c)
        i += 1
    return "".join(out)


def _tokens(text):
    import shlex
    lex = shlex.shlex(_strip_comments(text.replace("\\\n", "")), posix=True, punctuation_chars="<>&")
    lex.whitespace = " \t\r\n"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _redirect_before_amp(buf):
    """Does BUF (a command's text so far, outside quotes) end with a `<` or `>` operator, so the `&` after it is part of
    `<&` or `>&`? An operator escaped by an odd run of backslashes is a word's character, not an operator."""
    b = buf.rstrip()
    if not b.endswith((">", "<")) or len(b) != len(buf):  # `a< &`: a space before & ends the operator
        return False
    slashes = len(b[:-1]) - len(b[:-1].rstrip("\\"))
    return slashes % 2 == 0


def _split(text, ops=None):
    """[pipelines]; each pipeline is a list of stages; a stage is ('cmd', raw) or ('sub', inner raw). Splits on
    ; && || & newline and top-level ( ); a single '|' (not '||') keeps the pipeline. Quotes are honored. Given a list
    OPS, appends the separator before each pipeline to it: None for the first, else ';', '\n', '&', '&&' or '||' (a
    newline right after && or || continues that list, as bash reads it; G27)."""
    pipes, pipe, buf, i, n = [], [], [], 0, len(text)
    single = double = False
    pending = [None]

    def flush_cmd():
        s = "".join(buf).strip()
        if s:
            pipe.append(("cmd", s))
        buf.clear()

    def flush_pipe(sep=None):
        flush_cmd()
        if pipe:
            pipes.append(pipe[:])
            pipe.clear()
            if ops is not None:
                ops.append(pending[0])
            pending[0] = sep
        elif sep is not None and pending[0] not in ("&&", "||"):
            pending[0] = sep

    while i < n:
        c = text[i]
        if single:
            buf.append(c)
            single = c != "'"
            i += 1
        elif double:
            if c == "\\":  # an escaped char inside double quotes (\" does not close the quote)
                buf.append(text[i:i + 2])
                i += 2
            else:
                buf.append(c)
                double = c != '"'
                i += 1
        elif c == "\\":
            buf.append(text[i:i + 2])
            i += 2
        elif c == "'":
            single = True
            buf.append(c)
            i += 1
        elif c == '"':
            double = True
            buf.append(c)
            i += 1
        elif c == "(" or (c == "{" and not "".join(buf).strip() and text[i + 1:i + 2] in (" ", "\t", "\n")):
            # a ( ) subshell or a { } group at command position: one pipeline stage, so `( … ) | ssh` and
            # `{ …; } | ssh` pipe the group's output on (it does not reach the agent)
            brace = c == "{"
            j, d, s2, d2, pd = i + 1, 1, False, False, 0
            while j < n and d:
                cc = text[j]
                if s2:
                    s2 = cc != "'"
                elif d2:
                    if cc == "\\":  # an escaped char inside double quotes (\" does not close the quote)
                        j += 2
                        continue
                    d2 = cc != '"'
                elif cc == "\\":
                    j += 2
                    continue
                elif cc == "'":
                    s2 = True
                elif cc == '"':
                    d2 = True
                elif not brace and cc == "(":
                    d += 1
                elif not brace and cc == ")":
                    d -= 1
                elif brace and cc == "(":
                    pd += 1
                elif brace and cc == ")":
                    pd -= 1
                elif brace and pd == 0 and cc == "{" and text[j - 1] in " \t\n;&|" and text[j + 1:j + 2] in (" ", "\t", "\n"):
                    d += 1
                elif brace and pd == 0 and cc == "}" and text[j - 1] in " \t\n;":
                    d -= 1
                j += 1
            flush_cmd()
            pipe.append(("sub", text[i + 1:j - 1]))
            i = j
        elif c == "|":
            if "".join(buf).endswith(">") and _redirect_before_amp("".join(buf)):  # `>|`: noclobber's override
                buf.append(c)
                i += 1
            elif text[i:i + 2] == "||":
                flush_pipe("||")
                i += 2
            else:
                flush_cmd()
                i += 2 if text[i:i + 2] == "|&" else 1
        elif c == "&":
            if text[i:i + 2] == "&&":
                flush_pipe("&&")
                i += 2
            elif _redirect_before_amp("".join(buf)):  # >& or <& redirect, not a separator (`0<&0`; an escaped
                # `\<` is a word, and `echo a\< & cmd` runs cmd: of_review and of_secrets-review on PR 332)
                buf.append(c)
                i += 1
            else:
                flush_pipe("&")
                i += 1
        elif c in ";\n":
            flush_pipe(c)
            i += 1
        else:
            buf.append(c)
            i += 1
    flush_pipe()
    return pipes


_SEARCH_FIRST = {"grep", "rg", "sed", "awk"}           # their first non-option argument is a pattern or program
_PATTERN_VALUE = {"-e", "--regexp", "--expression"}   # the next argument is a pattern or a program, not a file
_PATTERN_FILE = {"-f", "--file", "--from-file"}        # the next argument is a file the command reads


def _data_args(head, args):
    """The file arguments a reader reads: cp/scp/rsync read their sources (not the destination or option
    values); grep, rg, sed and awk read every non-option argument but their pattern or program (the first one,
    or the value of -e; the value of -f is a file they read); every other reader reads any non-option argument.
    2026-11-10: `grep -n "…\\|<name>.key" tests/x.py` was denied as a read of the pattern. jq and yq keep the
    old reading on purpose: a filter like `.token` extracts the value itself, and the deny it gets is, for now,
    the only one that keeps that value out of the transcript (docs/guard.md, Gaps)."""
    if head in _SEARCH_FIRST:
        out, i, given = [], 0, False
        while i < len(args):
            a = args[i]
            if a in _PATTERN_VALUE or a.startswith(("--regexp=", "--expression=")):
                given = True
                i += 2 if a in _PATTERN_VALUE else 1
                continue
            if a in _PATTERN_FILE or a.startswith(("--file=", "--from-file=")):
                given = True
                if a in _PATTERN_FILE:
                    out += args[i + 1:i + 2]
                    i += 2
                else:
                    out.append(a.split("=", 1)[1])
                    i += 1
                continue
            if not (a.startswith("-") and a != "-"):
                out.append(a)
            i += 1
        return out if given else out[1:]  # without -e or -f, the first non-option argument is the pattern
    if head in _COPY:
        out, i = [], 0
        while i < len(args):
            a = args[i]
            if a.startswith("-") and a != "-":
                i += 2 if a in _COPY_VALUE_OPTS else 1
            else:
                out.append(a)
                i += 1
        return out[:-1]  # the destination is written, not read
    return [a for a in args if not (a.startswith("-") and a != "-")]


def _env_value_dump(head, args):
    """True if the command writes environment VALUES to its stdout (not just names)."""
    if head == "printenv":
        names = [a for a in args if not a.startswith("-")]
        return not names or any(_secret_name(a) for a in names)
    if head == "env":
        return all(a.startswith("-") for a in args)
    if head == "export":
        return "-p" in args
    if head == "set":
        return not args
    return False


# G3's one named value through grep -o (hand-over e280da4f, of_server; a user organization's structure keeper, msg
# 01a1f823-65a0): exactly `^?NAME=VALUE`, VALUE one class that cannot cross a space ([^ ], [^[:space:]], \S, or a bracket
# class with no space, \s, [:space:] or dot in it) with one quantifier, or nothing. `HOME=.*` and an alternation
# (`A=[^ ]*\|SECRET=[^ ]*`) printed values that were never the one named.
_G3_CLASS_INNER = r"(?:[A-Za-z0-9_+/:@%,~#!-]|\\[-\]_]|\[:(?:alnum|alpha|digit|upper|lower|xdigit|punct|graph):\])"
_G3_CLASS = (r"(?:\[\^ \]|\[\^\[:space:\]\]|\\S|\[(?!\^)" + _G3_CLASS_INNER + r"(?:\^|" + _G3_CLASS_INNER + r")*\])")
_G3_QUANT = r"(?:\*|\+|\?|\\\+|\\\?|\{\d+(?:,\d*)?\}|\\\{\d+(?:,\d*)?\\\})"
# a name pattern may group and alternate name parts (`^[A-Z_]*\(R2\|CF\)[A-Z_]*`): none of them can match `=`
_NAME_PATTERN = re.compile(r"\^?(?:[A-Za-z0-9_]|\[(?!\^)" + _G3_CLASS_INNER.replace("%,~#!", "") + r"+\]|" + _G3_QUANT
                           + r"|\\\(|\\\)|\\\||[()|])+")
_ONE_NAMED_VALUE = re.compile(r"(?P<anchor>\^|\\b|\\<| )?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?:" + _G3_CLASS + _G3_QUANT
                              + r"?)?\$?")


def _grep_only_matching(args):
    """Does this grep print only what matched: -o alone, in a short-flag bundle (-oE, -Eo, -ohE), or
    --only-matching? Options that take a value end a bundle's flags (-e, -f, -m, -A, -B, -C, -d, -D)."""
    for a in args:
        if a == "--only-matching":
            return True
        if a == "--":
            return False
        if re.fullmatch(r"-[A-Za-z]+", a):
            for c in a[1:]:
                if c == "o":
                    return True
                if c in "efmABCdD":
                    break
    return False


def _grep_flag(args, chars, longs=()):
    """Does this grep take one of the short flags CHARS (alone or in a bundle, up to an option with a value) or LONGS?"""
    for a in args:
        if a == "--":
            return False
        if a in longs:
            return True
        if re.fullmatch(r"-[A-Za-z]+", a):
            for c in a[1:]:
                if c in chars:
                    return True
                if c in "efmABCdD":
                    break
    return False


def _grep_patterns(args):
    """The patterns of a grep call, quotes removed: each -e/--regexp value, else its first operand; None with -f or
    --file (the patterns are not on the line)."""
    pats, explicit, operand, i = [], False, None, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            if not explicit and operand is None and i + 1 < len(args):
                operand = args[i + 1]
            break
        if a in ("-f", "--file") or a.startswith("--file=") or (re.fullmatch(r"-[A-Za-z]*f.*", a)
                                                                  and not a.startswith("--")):
            if re.fullmatch(r"-[A-Za-z]*f.*", a) and "e" in a[1:a.index("f")]:
                pass  # `-ef…`: f is -e's pattern, not the -f option
            else:
                return None
        if a in ("-e", "--regexp"):
            if i + 1 < len(args):
                pats.append(args[i + 1])
            explicit, i = True, i + 2
            continue
        if a.startswith("--regexp="):
            pats.append(a.split("=", 1)[1])
            explicit = True
        elif re.fullmatch(r"-[A-Za-z]*e.+", a) and not a.startswith("--"):
            pats.append(a[a.index("e") + 1:])
            explicit = True
        elif a in ("-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--context"):
            i += 2
            continue
        elif not a.startswith("-") and operand is None:
            operand = a
        i += 1
    return [x.strip("'\"") for x in (pats if explicit else ([operand] if operand is not None else []))]


def _names_only(head, args):
    """A pipeline stage that reduces env lines to names, so no value reaches the agent (of_architect:
    env | cut -d= -f1, env | grep -o '^PREFIX[A-Z_]*', awk -F= '{print $1}', sed 's/=.*//')."""
    joined = " ".join(args)
    if head == "cut":
        delim = field = None
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("-d"):
                delim, i = (a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else "")), i + (1 if len(a) > 2 else 2)
            elif a.startswith("-f"):
                field, i = (a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else "")), i + (1 if len(a) > 2 else 2)
            else:
                i += 1
        return delim == "=" and field == "1"
    if head == "awk":
        return bool(re.search(r"-F\s*'?=", joined) and re.search(r"print\s+\$1\b", joined) and "$2" not in joined)
    if head == "grep":
        if _grep_only_matching(args):  # grep -o prints only what matched
            pats = _grep_patterns(args)
            if pats is None or len(pats) != 1:
                return False  # -f, or several patterns: not one reading (hand-over e280da4f)
            pat = pats[0]
            if "=" not in pat:
                # a name pattern, no value: only when no part of it can match `=`, a dot or a space, so it cannot run
                # on into a value (`[^ ]*`, `.*`, `\S+` printed whole lines; of_platform on hand-over e280da4f)
                return bool(_NAME_PATTERN.fullmatch(pat))
            m = _ONE_NAMED_VALUE.fullmatch(pat)   # `NAME=[^ ]*`: one named value, allowed iff NAME is not secret
            # and only at a variable's start: unanchored, a short name matches a secret one's tail (`KEN=[^ ]*` printed
            # GITHUB_TOKEN's value: of_secrets-review on organic-framework PR 448), so ^, \b, \< or -w/-x
            anchored = bool(m) and (m.group("anchor") or _grep_flag(args, "wx", ("--word-regexp", "--line-regexp")))
            return bool(m) and bool(anchored) and not _secret_name(m.group("name"))   # by name: the anchor is a
            # group too, and group(1) checked `^` (of_secrets-review on organic-framework PR 448 at f36f48e)
        # -c (a count), -q (nothing), -l/-L (filenames): the matched line's value never reaches the output
        return any(re.fullmatch(r"-[A-Za-z]*[cqlL][A-Za-z]*", a) for a in args)
    if head == "wc":
        return True
    if head == "sed":
        return any(re.search(r"s/\^?\.?=\.?\*?//|s/=\.\*//", a.replace(" ", "")) for a in args)
    return False


def _opt_value(args, names):
    """The value of an option given as `-o v`, `-o=v`, `-ov`, `--out v` or `--out=v`; else None."""
    for k, a in enumerate(args):
        for nm in names:
            if a == nm and k + 1 < len(args):
                return args[k + 1]
            if a.startswith(nm + "="):
                return a[len(nm) + 1:]
            if len(nm) == 2 and a.startswith(nm) and len(a) > 2:
                return a[2:]
    return None


_KUBECTL_VALUE = {"-n", "--namespace", "-o", "--output", "-l", "--selector", "--field-selector", "-c", "--container",
                  "--tail", "--since", "--since-time", "--timeout", "--for", "--type", "--sort-by", "--template",
                  "--replicas", "--image", "--from", "--field-manager", "--grace-period", "--limit-bytes",
                  "--max-log-requests", "--patch", "--to-revision", "-k", "--kustomize", "--overrides",
                  "--context", "--cluster", "--user", "-f", "--filename", "--kubeconfig", "--server", "--as",
                  "--as-group", "--token", "--cache-dir", "--request-timeout", "--chunk-size", "--client-key",
                  "--client-certificate", "--certificate-authority", "--raw"}


def _positional(args, value_flags):
    """The non-option arguments, skipping the values of value-taking options (`-n ci`, `-o yaml`)."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            i += 1 if ("=" in a or a not in value_flags) else 2
        else:
            out.append(a)
            i += 1
    return out


# `kubectl get --raw /api/v1/namespaces/<ns>/secrets/<name>`: the API path itself names the namespace and the kind
_RAW_POD_SUBS = ("log", "proxy")  # a pod's subresources that are not the Pod object (status, ephemeralcontainers … are)


def _kube_raw(args, line_vars=None):
    """(namespace or None, kind, subresource or None) of a `--raw` API path, read as the API server reads it (of_review
    and of_secrets-review on organic-framework PR 151): cut at `#`, percent-decoded until it no longer changes, cut at
    `?`, empty and `.` segments dropped and `..` applied, lowercased, and a leading legacy `watch/` (which streams the
    objects) or `proxy/` (its subresource is then proxy) dropped. The kind is `unparsed` for a path the guard cannot
    read (held in a variable or a substitution) and `(none)` for a path that names no object (discovery, /healthz).
    A variable LINE_VARS knows (set earlier on the same line, as G14 follows one) is read as its value (of_review on
    PR 151: `P=/api/v1/…/secrets/k; kubectl get --raw "$P"`). None without --raw."""
    from urllib.parse import unquote
    path = _opt_value(args, ("--raw",))
    if path is None:
        return None
    if "$" in path and line_vars:
        path = re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: line_vars.get(m.group(1) or m.group(2), m.group(0)), path)
    if "$" in path or "__OFSUB__" in path or "`" in path:
        return None, "unparsed", None
    p = path.split("#", 1)[0]
    for _ in range(4):
        q = unquote(p)
        if q == p:
            break
        p = q
    segs = []
    for seg in p.split("?", 1)[0].split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            segs = segs[:-1]
            continue
        segs.append(seg.lower())
    if segs[:1] == ["api"] and len(segs) > 2:
        rest = segs[2:]
    elif segs[:1] == ["apis"] and len(segs) > 3:
        rest = segs[3:]
    else:
        return None, "(none)", None
    legacy_proxy = rest[:1] == ["proxy"]
    if rest[:1] in (["watch"], ["proxy"]):
        rest = rest[1:]
    ns = None
    if rest[:1] == ["namespaces"] and len(rest) >= 3:
        ns, rest = rest[1], rest[2:]
    if not rest:
        return ns, "(none)", None
    sub = "proxy" if legacy_proxy else ("/".join(rest[2:]) or None)
    return ns, rest[0], sub


# A Pod's spec, and a workload's pod template, carry each container's literal env values; a workload's
# last-applied-configuration annotation carries the whole applied object (of_review's probe on organic-framework PR 253:
# `describe pod`, `-o jsonpath|go-template|custom-columns` and `set env --list` printed what `-o yaml` is denied for).
_KUBE_POD_KINDS = {"pod", "pods", "po", "all"}
_KUBE_WORKLOAD_KINDS = {"deployment", "deployments", "deploy", "statefulset", "statefulsets", "sts", "daemonset",
                        "daemonsets", "ds", "replicaset", "replicasets", "rs", "job", "jobs", "cronjob", "cronjobs", "cj",
                        "replicationcontroller", "replicationcontrollers", "rc", "podtemplate", "podtemplates"}
_KUBE_TABLE_OUT = {"", "wide", "name"}  # the outputs that print no spec; any other but a template prints the object
_KUBE_TEMPLATE_OUT = {"jsonpath", "jsonpath-as-json", "go-template", "template", "custom-columns"}
_KUBE_TEMPLATE_FILE_OUT = {"jsonpath-file", "go-template-file", "templatefile", "custom-columns-file"}
# a printed subtree that holds container env values or the applied object
_KUBE_ENV_HOLDERS = {"items", "spec", "template", "jobtemplate", "containers", "initcontainers", "ephemeralcontainers",
                     "metadata", "annotations"}
_KUBE_PATH_TOKEN = re.compile(r"\.\.|\.|\[[^\]]*\]|[^.\[\]]+")
# go-template contexts: a range over one of these lists yields its elements ("[]" marks an element); a range over
# anything else is a map's values, any field ("*"), and its key is a name ("(key)")
_KUBE_LISTS = {"items", "containers", "initcontainers", "ephemeralcontainers", "containerstatuses",
               "initcontainerstatuses", "ephemeralcontainerstatuses", "conditions", "volumes", "volumemounts", "ports",
               "env", "envfrom", "tolerations", "ownerreferences", "imagepullsecrets", "managedfields", "args", "command"}
_KUBE_ELEMENT, _KUBE_ANY, _KUBE_KEY = ("[]", False), ("*", False), ("(key)", False)
# The Pod's own template reads deny (its -o yaml|json already did). `describe pod` and every read of a workload's spec
# warn first: the 21-day replay before 2026-11-17 (35,200 distinct commands naming kubectl) found them in live flows
# (a workload's -o yaml|json 439, a workload template reaching env or its applied object 103, describe pod 57, describe
# of a workload 14), while a Pod's template reaching env was 11, and `set env --list` and `view-last-applied` on these
# kinds had no use.
POD_SPEC_MODE = {"describe pod": "warn", "workload": "warn"}
POD_ENV_DENY = "a command that would print container env values"
POD_ENV_ADVICE = ("read the fields you need outside the containers' env ({.status}, {.spec.containers[*].image}), or "
                  "env names only (env[*].name); a value stays where it is used (docs/guard.md G6)")
POD_SPEC_NOTE = ("a Pod's and a workload's spec carry container env values, and a workload's last-applied annotation "
                 "the whole applied object: read the fields you need outside the containers' env ({.status}, "
                 "{.spec.template.spec.containers[*].image}, env[*].name), and a pod's state with `kubectl get events "
                 "--field-selector involvedObject.name=<pod>`; these reads will be denied once the flows have moved "
                 "(docs/guard.md G6.1)")


def _kube_kinds_named(nonopt, at=1):
    """The resource kinds a kubectl call names at position AT (`pod`, `pod,deploy`, `deployment.apps/web`), plus a
    `kind/name` later on, lowercased and without a group."""
    out = set()
    if len(nonopt) > at:
        out |= {p.split("/")[0].split(".")[0].lower() for p in nonopt[at].split(",") if p}
    out |= {a.split("/")[0].split(".")[0].lower() for a in nonopt[at + 1:] if "/" in a}
    return out


def _kube_field_segs(field, rec=False):
    """A quoted bracket field is a path to kubectl (`['spec.containers']`): its segments, split on unescaped dots
    (of_review on organic-framework PR 264)."""
    parts = [x.replace("\\.", ".").lower() for x in re.split(r"(?<!\\)\.", field) if x]
    return [(x, rec and i == 0) for i, x in enumerate(parts)] or [("", rec)]


def _kube_segs(path):
    """[(field, recursive)] of a JSONPath-like path; indexes and filters dropped, a quoted bracket kept as a field."""
    out, rec = [], False
    for t in _KUBE_PATH_TOKEN.findall(path.strip().strip("{}")):
        if t == "..":
            rec = True
        elif t == ".":
            continue
        elif t.startswith("["):
            inner = t[1:-1].strip()
            names = re.findall(r"'([^']*)'|\"([^\"]*)\"", inner)
            if len(names) > 1 and re.fullmatch(r"\s*(?:(?:'[^']*'|\"[^\"]*\")\s*,\s*)+(?:'[^']*'|\"[^\"]*\")\s*", inner):
                # a union of fields in one bracket: judged once per field (of_review on organic-framework PR 264)
                out.append(("|" + "|".join(a or b for a, b in names), rec))
                rec = False
            elif len(inner) > 1 and inner[0] in "'\"" and inner[-1] == inner[0]:
                out.extend(_kube_field_segs(inner[1:-1], rec))
                rec = False
        else:
            out.append((t.strip().lower(), rec))
            rec = False
    if rec:
        out.append(("*", True))
    return out


def _kube_path_holds_env(segs):
    """Does a printed path reach container env values or the applied object? `env` itself or its `value`, a
    last-applied-configuration, a recursive `..value`/`..env`/`..*`, or a subtree that holds them (`.spec`,
    `.spec.containers[0]`, `.items[*]`, the object's own `.metadata.annotations`, the root). `env[*].name` and
    `env[*].valueFrom` name references only."""
    for i, (n, rec) in enumerate(segs):
        if n.startswith("|"):  # a union: any of its fields
            return any(_kube_path_holds_env(segs[:i] + _kube_field_segs(m, rec) + segs[i + 1:]) for m in n[1:].split("|"))
    segs = [s for s in segs if s != _KUBE_ELEMENT]
    names = [n for n, _ in segs]
    for i, (n, rec) in enumerate(segs):
        if n == "last-applied-configuration" or n.endswith("/last-applied-configuration"):
            return True
        if n == "env" and (i + 1 == len(names) or names[i + 1] not in ("name", "valuefrom")):
            return True
        if rec and (n in ("value", "env", "*") or n in _KUBE_ENV_HOLDERS):
            return True
        if n == "*" and not rec:  # any field of the root or of a subtree that holds env (a map ranged over, `.spec.*`)
            above = [x for x in names[:i] if x != "*"]
            if not above or above[-1] in _KUBE_ENV_HOLDERS:
                return True
    named = [n for n in names if n != "*"]
    if named and named[-1] in ("metadata", "annotations"):  # the object's own: a pod template's holds no applied copy
        return (named[1:] if named[0] == "items" else named) in (["metadata"], ["metadata", "annotations"])
    return not named or named[-1] in _KUBE_ENV_HOLDERS


def _kube_unquote(s):
    return re.sub(r'"(?:\\.|[^"\\])*"|`[^`]*`', '""', s)


def _kube_jsonpath_paths(expr):
    """The paths a kubectl JSONPath template prints, each from the root (`{range}` sets the context)."""
    out, ctx = [], [[]]
    for act in re.findall(r"\{([^{}]*)\}", expr):
        a = _kube_unquote(act).strip()
        if a.startswith("range "):
            ctx.append(ctx[-1] + _kube_segs(a[6:].strip().lstrip("@$")))
        elif a == "end":
            ctx = ctx[:-1] if len(ctx) > 1 else ctx
        elif a in ('""', "''") or (len(a) > 1 and a[0] == a[-1] == "'"):
            continue
        elif a.startswith("$"):
            out.append(_kube_segs(a[1:]))
        else:
            out.append(ctx[-1] + _kube_segs(a.lstrip("@")))
    if "{" not in expr and expr.strip():  # kubectl wraps a bare path in braces
        out.append(_kube_segs(expr))
    return out


def _kube_go_path(tok, ctx, var):
    """The path of `.a.b` (from the context) or `$v.a.b` (from the variable; `$` is the root); None when the context or
    the variable is unknown, or TOK is neither."""
    m = re.match(r"(\$\w*)((?:\.[\w-]+)*)$", tok)
    if m:
        base = [] if m.group(1) == "$" else var.get(m.group(1))
        return None if base is None else list(base) + _kube_segs(m.group(2))
    if tok.startswith(".") and ctx[-1] is not None:
        return ctx[-1] + _kube_segs(tok)
    return None


def _kube_go_base(pipe, ctx, var):
    """The path a go-template pipeline yields: a plain `.a.b` or `$v.a.b`; `index P 0 …` with numeric indexes (an
    element of P); `(…).a.b`. None for anything else (a string key, a function, a pipe): the guard cannot read it."""
    e = pipe.strip()
    m = re.match(r"\((.*)\)((?:\.[\w-]+)*)$", e, re.S)
    if m:
        inner = _kube_go_base(m.group(1), ctx, var)
        return None if inner is None else inner + _kube_segs(m.group(2))
    m = re.match(r"index\s+(\(.*\)|\S+)((?:\s+\d+)+)$", e, re.S)
    if m:
        inner = _kube_go_base(m.group(1), ctx, var)
        return None if inner is None else inner + [_KUBE_ELEMENT] * len(m.group(2).split())
    return _kube_go_path(e, ctx, var) if re.match(r"[.$][\w.$-]*$", e) else None


def _kube_gotemplate_paths(expr):
    """The paths a go-template prints, each from the root: `range` and `with` set the context, `$v :=` binds one;
    an `if` condition and a `range`/`with` pipeline are not printed; `template`/`block` count as the root. A base the
    guard cannot read (a function, a string key: of_secrets-review on organic-framework PR 264) makes an unknown
    context, and a print inside it counts as the root."""
    out, ctx, var, blocks = [], [[]], {}, []  # blocks: (the keyword that opened it, the context outside it)
    for act in re.findall(r"\{\{-?(.*?)-?\}\}", expr, re.S):
        a = _kube_unquote(act).strip()
        if a == "else" or a.startswith(("else ", "else\t", "else\n")):
            # Go runs a with's or a range's else with the outer dot; `else with X` is a with on X from there, and an
            # if's else keeps its dot (of_secrets-review on organic-framework PR 264)
            kw, outer = blocks[-1] if blocks else ("if", ctx[-1])
            rest = a[4:].strip()
            if kw in ("range", "with") or rest.startswith("with "):
                ctx[-1] = outer
            if rest.startswith("with "):
                m = re.match(r"(\$\w+)\s*:?=\s*(.*)", rest[5:], re.S)
                base = _kube_go_base(m.group(2) if m else rest[5:], ctx, var)
                if m:
                    var[m.group(1)] = base
                ctx[-1] = base
            if blocks:
                blocks[-1] = ("with" if rest.startswith("with ") else "if", outer)
            continue
        if a.startswith(("range ", "with ")):
            kw, body = a.split(" ", 1)
            m = re.match(r"(\$\w+)\s*(?:,\s*(\$\w+))?\s*:?=\s*(.*)", body, re.S)
            base = _kube_go_base(m.group(3) if m else body, ctx, var)
            if kw == "range" and base is not None:  # a list's elements, or a map's values (any field)
                base = base + [_KUBE_ELEMENT if base and base[-1][0] in _KUBE_LISTS else _KUBE_ANY]
            if m and m.group(2):
                var[m.group(1)], var[m.group(2)] = ([_KUBE_KEY] if kw == "range" else base), base
            elif m:
                var[m.group(1)] = base
            blocks.append((kw, ctx[-1]))
            ctx.append(base)
        elif a.startswith("if "):
            blocks.append(("if", ctx[-1]))
            ctx.append(ctx[-1])
        elif a == "end":
            ctx = ctx[:-1] if len(ctx) > 1 else ctx
            blocks = blocks[:-1]
        elif a.startswith(("else", "/*")) or a in ("break", "continue"):
            continue
        elif a.startswith(("define ", "block ", "template ")):
            out.append([])
        elif re.match(r"\$\w+\s*:?=", a):
            name, val = re.split(r"\s*:?=\s*", a, 1)
            var[name] = _kube_go_base(val, ctx, var)
        else:
            whole = _kube_go_base(a, ctx, var)
            if whole is not None:
                out.append(whole)
                continue
            if ctx[-1] is None and re.search(r"(?<![\w)])\.", a):
                out.append([])  # a field of an unknown context
            for tok in re.findall(r"\$\w*(?:\.[\w-]+)*|(?<![\w)])\.(?:[\w-]+(?:\.[\w-]+)*)?", a):
                p = _kube_go_path(tok, ctx, var)
                out.append(p if p is not None else [])
    return out


def _kube_template_holds_env(args, line_vars=None):
    """(format, True) when a kubectl template output prints container env values or the applied object, or cannot be
    read (a template file, a substitution, a shell variable the line does not set); (format, False) when it reads
    other fields; None for any other output."""
    o = _opt_value(args, ("-o", "--output")) or ""
    fmt, _, expr = o.partition("=")
    fmt = fmt.lower()  # kubectl reads an output name in any case (of_review on organic-framework PR 264)
    tmpl = _opt_value(args, ("--template",))
    if not fmt and tmpl:
        fmt = "go-template"
    if fmt in _KUBE_TEMPLATE_FILE_OUT:
        return fmt, True
    if fmt not in _KUBE_TEMPLATE_OUT:
        return None
    expr = expr or tmpl or ""
    if line_vars and "$" in expr and fmt not in ("go-template", "template"):
        expr = re.sub(r"\$\{(\w+)\}|\$([A-Za-z_]\w*)", lambda m: line_vars.get(m.group(1) or m.group(2), m.group(0)), expr)
    if not expr.strip() or "__OFSUB__" in expr or "`" in expr or \
            (fmt not in ("go-template", "template") and re.search(r"\$\{?[A-Za-z_]", expr)):
        return fmt, True
    if fmt == "custom-columns":
        paths = [_kube_segs(c.split(":", 1)[1]) for c in expr.split(",") if ":" in c]
    elif fmt in ("go-template", "template"):
        paths = _kube_gotemplate_paths(expr)
    else:
        paths = _kube_jsonpath_paths(expr)
    return fmt, any(_kube_path_holds_env(p) for p in paths)


def _kube_pod_spec_read(sub, nonopt, args, out, line_vars=None):
    """G6 and G6.1: a read that prints a Pod's or a workload's container env values. Returns a deny, or None after
    noting a warning in POD_SPEC_MODE's warn mode."""
    if sub == "set" and nonopt[1:2] == ["env"] and any(a == "--list" or a.startswith("--list=") for a in args) and \
            not any(a in ("--list=false",) for a in args):
        token = "kubectl set env --list" + (" --resolve" if any(a.startswith("--resolve") for a in args) else "")
        return f"{POD_ENV_DENY} ({token}): {POD_ENV_ADVICE}", token
    if sub == "apply" and "view-last-applied" in nonopt and \
            _kube_kinds_named(nonopt, nonopt.index("view-last-applied") + 1) & (_KUBE_POD_KINDS | _KUBE_WORKLOAD_KINDS):
        return f"{POD_ENV_DENY} (kubectl apply view-last-applied): {POD_ENV_ADVICE}", "kubectl apply view-last-applied"
    kinds = _kube_kinds_named(nonopt)
    if sub == "edit" and kinds & _KUBE_POD_KINDS:  # `KUBE_EDITOR=cat kubectl edit pod` prints the spec (PR 264 review)
        return f"{POD_ENV_DENY} (kubectl edit pod): {POD_ENV_ADVICE}", "kubectl edit pod"
    if sub not in ("get", "describe"):
        return None
    pod, work = bool(kinds & _KUBE_POD_KINDS), bool(kinds & _KUBE_WORKLOAD_KINDS)
    if not (pod or work):
        return None
    tmpl = _kube_template_holds_env(args, line_vars) if sub == "get" else None
    whole = sub == "get" and out not in _KUBE_TABLE_OUT and tmpl is None  # yaml, json, kyaml, or one not known yet
    if pod and ((tmpl and tmpl[1]) or whole):
        token = f"kubectl get pod -o {tmpl[0] if tmpl else out}"
        return f"{POD_ENV_DENY} ({token}): {POD_ENV_ADVICE}", token
    if sub == "describe" and not out:
        what, mode = ("kubectl describe pod", "describe pod") if pod else ("kubectl describe <workload>", "workload")
    elif work and (whole or (tmpl and tmpl[1])):
        what, mode = f"kubectl {sub} <workload> -o {tmpl[0] if tmpl else out}", "workload"
    else:
        return None
    if POD_SPEC_MODE.get(mode) == "deny":
        return f"{POD_ENV_DENY} ({what}): {POD_ENV_ADVICE}", what
    note = (f"{POD_ENV_DENY} ({what})", what, POD_SPEC_NOTE)
    if note not in _WARNINGS:
        _WARNINGS.append(note)
    return None


# `kubectl get secret -o name` and `describe secret` print no value, but kubectl fetches the whole Secret, data included,
# into its own memory on this machine to make them; the default table is the API server's own Table, names and counts
# only (a user organization's security finding, through its structure keeper, msg 01a1e8ad-767a: its rule that no Secret
# value enters a process on the Mac; RFC 0002 §4.1, "Where the script runs")
SECRET_FETCH_DENY = "a command that fetches a Secret's values onto this machine"
SECRET_FETCH_ADVICE = ("for its name, type and key count use the table, which the API server makes and which holds no "
                       "value: `kubectl get secret NAME --no-headers` (`--ignore-not-found` to test that it exists); a "
                       "value is read where it lives (docs/guard.md G6)")


def _kube_names(nonopt, kinds):
    """Does a kubectl call name one of KINDS: any word, split on `,` and `/`, lowercased and without its group or
    version (`Secret/arc`, `SECRETS`, `secrets.v1.`; of_secrets-review on organic-framework PR 271: a Secret named so
    printed its values through `-o yaml`)."""
    return any(p.split(".")[0].lower() in kinds for a in nonopt for p in re.split(r"[,/]", a) if p)


# A Secret read that kubectl makes from the whole object, though it prints a table: --sort-by asks the server for the
# objects (includeObject=Object), --server-print=false prints the table from the plain object (of_review and
# of_secrets-review on organic-framework PR 271, with kubectl 1.36.1 against a local fake API server)
def _kube_whole_flag(args):
    """`--sort-by` or `--server-print=false` (any spelling Go's flag package reads as false) when given, else None."""
    for a in args:
        if a == "--sort-by" or a.startswith("--sort-by="):
            return "--sort-by"
        if a.startswith("--server-print=") and a.split("=", 1)[1].lower() in ("false", "0", "f"):
            return "--server-print=false"
    return None


def _kubectl_secret_fetch(args):
    """G6 for a Secret read written to a file: any -o, --template or -w, and describe, still fetch the whole Secret,
    data included, into kubectl on this machine; only the default table does not."""
    nonopt = _positional(args, _KUBECTL_VALUE)
    sub = nonopt[0] if nonopt else None
    if not _kube_names(nonopt, ("secret", "secrets", "all")):
        return None
    out = (_opt_value(args, ("-o", "--output")) or "").split("=")[0].lower()
    whole = _kube_whole_flag(args)
    if sub == "get" and whole:
        token = f"kubectl get secret {whole} > file"
    elif sub == "get" and (out or _opt_value(args, ("--template",)) is not None or any(a in ("-w", "--watch") for a in args)):
        token = f"kubectl get secret -o {out or 'go-template'} > file"
    elif sub == "describe":
        token = "kubectl describe secret > file"
    else:
        return None
    return f"{SECRET_FETCH_DENY} ({token}): {SECRET_FETCH_ADVICE}", token


def _kubectl_secret_deny(args, line_vars=None):
    """Cluster reads that print a stored Secret's values (of_architect, 2026-11-06, after three real leaks,
    the last from a go-template/jsonpath that fell back to printing the whole object). For a Secret kind
    (any alias/namespace, -A, comma lists, `all`): allow only -o name, the default table and `describe`
    (which lists keys with sizes); deny every other -o, plus get -w/--watch, edit and apply view-last-applied.
    For a pod kind, -o yaml|json is denied too (the spec carries container env values), and so is every other read of
    those values: a template output reaching them, `set env --list`, `apply view-last-applied`; `describe pod` and a
    workload's spec warn first (_kube_pod_spec_read, docs/guard.md G6 and G6.1)."""
    nonopt = _positional(args, _KUBECTL_VALUE)
    sub = nonopt[0] if nonopt else None
    has_secret = _kube_names(nonopt, ("secret", "secrets", "all"))
    has_pod = _kube_names(nonopt, ("pod", "pods", "po"))
    out = (_opt_value(args, ("-o", "--output")) or "").split("=")[0].lower()  # kubectl reads `-o YAML` as yaml
    raw = _kube_raw(args, line_vars)  # the API's JSON: a Secret's values; the Pod, its spec with env (not its log);
    # a service account's token subresource, which mints and prints a bearer token (of_secrets-review on PR 151)
    if raw and (raw[1] == "secrets" or (raw[1] == "pods" and (raw[2] or "").split("/")[0] not in _RAW_POD_SUBS)
                or (raw[1] == "serviceaccounts" and (raw[2] or "").split("/")[0] == "token")):
        return _prints(f"kubectl {sub or ''} --raw on {raw[1]}".replace("  ", " "))
    if sub == "edit" and has_secret:
        return _prints("kubectl edit secret")
    if sub == "apply" and "view-last-applied" in args and has_secret:
        return _prints("kubectl apply view-last-applied secret")
    # kubectl prints --template as a go-template when no -o is given (checked with kubectl 1.36.1, 2026-11-17)
    template = _opt_value(args, ("--template",)) is not None
    if sub == "create" and "secret" in nonopt and any(a.startswith("--dry-run") for a in args) and \
            (out not in ("", "name") or template):
        return _prints("kubectl create secret --dry-run -o " + (out or "go-template"))
    if sub == "get" and has_secret:
        if any(a in ("-w", "--watch") for a in args):
            return _prints("kubectl get secret --watch")
        if template and not out:
            return _prints("kubectl get secret --template")
        if out and out != "name":  # jsonpath/go-template fall back to the whole object
            return _prints(f"kubectl get secret -o {out}")
        if out == "name":  # kubectl takes the whole Secret for it; only the default table is the server's own
            return f"{SECRET_FETCH_DENY} (kubectl get secret -o name): {SECRET_FETCH_ADVICE}", "kubectl get secret -o name"
        whole = _kube_whole_flag(args)
        if whole:
            return f"{SECRET_FETCH_DENY} (kubectl get secret {whole}): {SECRET_FETCH_ADVICE}", f"kubectl get secret {whole}"
    if sub == "describe" and has_secret:
        return f"{SECRET_FETCH_DENY} (kubectl describe secret): {SECRET_FETCH_ADVICE}", "kubectl describe secret"
    if sub in ("get", "describe") and has_pod and out in ("yaml", "json"):  # the pod spec carries env values (kyaml and
    # any other output that prints the object: _kube_pod_spec_read)
        return _prints(f"kubectl {sub} pod -o {out}")
    return _kube_pod_spec_read(sub, nonopt, args, out, line_vars)


# helm's global flags that take a value (helm --help, v4): before the subcommand they hold its place otherwise
_HELM_VALUE = {"--kube-context", "--kubeconfig", "--registry-config", "--repository-config", "--repository-cache",
               "--content-cache", "--burst-limit", "--qps", "--kube-apiserver", "--kube-as-user", "--kube-as-group",
               "--kube-ca-file", "--kube-token", "--kube-tls-server-name"}


def _helm_all_namespaces(args):
    """-A in any spelling helm takes: --all-namespaces[=true], -A[=true], or A among a short-flag cluster's letters
    (-qA, -Aa: helm parses them as -q -A; of_secrets-review and of_review on organic-framework PR 434). A namespace
    is lower case, so an upper-case A in a cluster is never one."""
    for a in args:
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name == "--all-namespaces" and not (eq and val.lower() in ("false", "0")):
                return True
        elif re.fullmatch(r"-[A-Za-z]+(=.*)?", a):
            letters, eq, val = a[1:].partition("=")
            if "A" in letters and not (eq and val.lower() in ("false", "0")):
                return True
    return False


def _helm_release_read(args, line_vars=None):
    """Helm keeps each release as a Secret and decodes the whole record to list or show it: `helm list -A` reads
    every namespace's, and a read with no namespace takes the context's, which is not on the line (a user
    organization's structure keeper, msgs 01a1f649-9018 and 01a1f652-2f62: a cluster-wide Secret list alerted four
    times from one subagent)."""
    pos = _positional(args, _KUBECTL_VALUE | _HELM_VALUE)
    if pos[:1] in (["list"], ["ls"]) and _helm_all_namespaces(args):
        return _helm_releases(f"helm {pos[0]} -A")
    if pos[:1] in (["get"], ["status"], ["history"], ["hist"], ["list"], ["ls"]) and \
            not _helm_namespace(args, line_vars):
        return _helm_releases(f"helm {pos[0]} without -n")
    return None


def _helm_releases(token):
    return (f"a command that would bring Helm's release records, which it keeps as Secrets, onto this machine "
            f"({token}): name one namespace with -n <namespace> (docs/guard.md G6)", token)


def _helm_namespace(args, line_vars=None):
    """Whether a helm call names its namespace: -n, --namespace (with its value or after =), or HELM_NAMESPACE set on
    the line."""
    for i, a in enumerate(args):
        if a in ("-n", "--namespace") and i + 1 < len(args) and args[i + 1]:
            return True
        if a.startswith("--namespace=") and len(a) > len("--namespace=") or (a.startswith("-n") and len(a) > 2
                                                                             and not a.startswith("--")):
            return True
    return bool((line_vars or {}).get("HELM_NAMESPACE"))


def _cluster_secret_deny(head, args, to_file, line_vars=None):
    """A command that prints a stored secret value from a cluster or a secret manager. Writing it to a file is
    the owner's to allow, so a `>` redirect is exempt (of_architect, 2026-11-06)."""
    if head in ("wg", "awg"):  # WireGuard/Amnezia: showconf and the private/preshared keys (plain `show` is public)
        if args[:1] == ["showconf"]:
            return _prints(f"{head} showconf")
        if args[:1] == ["show"] and any(a in ("private-key", "preshared-key", "preshared-keys") for a in args):
            return _prints(f"{head} show private-key")
        return None
    if head == "helm":  # before the file exemption: written to a file, helm still fetches the Secrets (PR 434)
        hit = _helm_release_read(args, line_vars)
        if hit:
            return hit
    if to_file:  # kubectl still fetches the whole Secret onto this machine to write it (SECRET_FETCH_DENY)
        return _kubectl_secret_fetch(args) if head == "kubectl" else None
    if head == "kubectl":
        return _kubectl_secret_deny(args, line_vars)
    if head == "helm":
        pos = _positional(args, _KUBECTL_VALUE | {"--kube-context"})
        if pos[:1] == ["get"] and len(pos) > 1 and pos[1] in ("values", "all", "manifest"):
            return _prints("helm get " + pos[1])

    if head == "sops" and any(a in ("-d", "--decrypt", "decrypt") for a in args) and \
            not any(a in ("-i", "--in-place") or a.startswith(("-o", "--output")) for a in args):
        return _prints("sops --decrypt")
    if head == "vault" and (args[:2] == ["kv", "get"] or args[:1] == ["read"]):
        return _prints("vault kv get")
    if head == "aws" and "secretsmanager" in args and "get-secret-value" in args:
        return _prints("aws secretsmanager get-secret-value")
    if head == "gcloud" and args[:3] == ["secrets", "versions", "access"]:
        return _prints("gcloud secrets versions access")
    # the cloud CLIs' cached sign-ins printed as tokens (G1.12 keeps their files; of_secrets-review and of_review on
    # organic-framework PR 178)
    if head == "gcloud" and args[:2] == ["config", "config-helper"]:  # prints the cached access token (PR 178)
        return _prints("gcloud config config-helper")
    if _lang(head) and _py_module(args) == "awscli":  # `python3 -m awscli configure export-credentials`
        head, args = "aws", args[args.index("awscli") + 1:] if "awscli" in args else []
    if head == "aws" and "configure" in args and "export-credentials" in args:
        return _prints("aws configure export-credentials")
    if head == "gcloud" and "auth" in args and any(a in ("print-access-token", "print-identity-token") for a in args):
        return _prints("gcloud auth print-access-token")
    if head == "az" and "account" in args and "get-access-token" in args:
        return _prints("az account get-access-token")
    if head == "op" and (args[:1] == ["read"] or args[:2] == ["item", "get"]):
        return _prints("op read")
    if head == "kubeseal" and "--raw" in args:
        return _prints("kubeseal --raw")
    if head in ("docker", "podman", "nerdctl"):
        return _docker_inspect_deny(head, args)
    return None


# Global options of docker, podman and nerdctl that take a value, and the inspect options that do.
_DOCKER_VALUE = {"-H", "--host", "-c", "--context", "--config", "-l", "--log-level", "--tlscacert", "--tlscert",
                 "--tlskey", "--connection", "--url", "--identity", "--root", "--runroot", "--storage-driver",
                 "--cgroup-manager", "--module", "-n", "--namespace", "-a", "--address", "--snapshotter",
                 "--data-root", "-f", "--format", "--type", "--builder"}
# Objects whose inspect output carries stored values: a container's and an image's Config.Env, a swarm service's
# ContainerSpec.Env, a plugin's Settings.Env, a swarm config's Data, a volume's driver options (a CIFS mount's
# o=…,password=…).
_VALUE_INSPECT = {"container", "image", "service", "plugin", "config", "volume"}


def _docker_inspect_deny(head, args):
    """`docker inspect` (and container, image, service, plugin, config, volume inspect) without a --format, or with
    one naming Env, prints environment values or stored options; `podman secret inspect --showsecret` prints the secret;
    `buildx imagetools inspect` prints a manifest unless its --format names the image config. Only these
    subcommands, never an "inspect" anywhere after docker (2026-11-09, from a user organization:
    `docker buildx imagetools inspect <ref>` was refused as docker inspect)."""
    pos = _positional(args, _DOCKER_VALUE)
    fmt = _opt_value(args, ("-f", "--format"))
    if pos[:1] == ["inspect"] or (len(pos) > 1 and pos[0] in _VALUE_INSPECT and pos[1] == "inspect"):
        if fmt is None or re.search(r"\.?Env\b", fmt):
            return _prints("docker inspect (.Config.Env; use --format '{{.State.Health.Status}}' or docker ps)")
    if pos[:2] == ["secret", "inspect"] and "--showsecret" in args:
        return _prints(f"{head} secret inspect --showsecret")
    if pos[:3] == ["buildx", "imagetools", "inspect"] and fmt and re.search(r"\b(?:Image|Config|Env)\b", fmt):
        return _prints("docker buildx imagetools inspect --format (the image config carries Env)")
    return None


def _ps_env(head, args):
    """`ps e` / `ps eww` (a BSD flag bundle with `e`) prints every process's environment. `-e` with a dash is
    'all processes' and does not; column names after -o never reach here (they have no leading dash to miss)."""
    return head == "ps" and any(re.fullmatch(r"[auxewfl]*e[auxewfl]*", a) for a in args)


def _nested(head, args):
    """Command lines a command runs: sh/bash -c, eval, ssh's remote command, `docker exec C cmd …`,
    `kubectl exec P -- cmd …`, and any `sh -c STRING` among the arguments (sudo, timeout, xargs …)."""
    import shlex
    out = []
    if head in _SHELLS:
        if "-c" in args[:-1]:
            out.append(args[args.index("-c") + 1])
        elif head == "eval":
            out.append(" ".join(args))
    elif head == "ssh" and args:
        # the remote command is every word after the host, joined with spaces as ssh sends it (`ssh gw sudo dd
        # if=x` runs `sudo dd if=x`); options before the host may take a value
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in _SSH_VALUE_OPTS else 1
        rest = args[i + 1:]
        if rest:
            out.append(" ".join(rest))
    elif head in _RUNNERS and any(a in ("exec", "shell", "run") for a in args):
        if "--" in args:
            rest = args[args.index("--") + 1:]
        else:
            i = next(k for k, a in enumerate(args) if a in ("exec", "shell", "run")) + 1
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in _RUNNER_VALUE_OPTS else 1
            rest = args[i + 1:]  # after the container, pod or VM name
        if rest:
            out.append(shlex.join(rest))
    for k, a in enumerate(args[:-1]):
        if os.path.basename(a) in _SHELLS - {"eval"} and args[k + 1] == "-c" and k + 2 < len(args):
            out.append(args[k + 2])
    return out


_ANSI_WORD = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)
_ANSI_ESC = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
             "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c_body(body):
    """The value of a $'…' word's body, as bash decodes it (\\n, \\t, \\nnn, \\xHH, \\uHHHH, \\cX …)."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = body[i + 1]
        m = (re.match(r"[0-7]{1,3}", body[i + 1:]) if e in "01234567" else
             re.match(r"[0-9A-Fa-f]{1,2}", body[i + 2:]) if e == "x" else
             re.match(r"[0-9A-Fa-f]{1,%d}" % (4 if e == "u" else 8), body[i + 2:]) if e in "uU" else None)
        if e in _ANSI_ESC:
            out.append(_ANSI_ESC[e])
            i += 2
        elif m and e in "01234567":
            out.append(chr(int(m.group(0), 8) & 0xFF))
            i += 1 + len(m.group(0))
        elif m:
            out.append(chr(min(int(m.group(0), 16), 0x10FFFF)))
            i += 2 + len(m.group(0))
        elif e == "c" and i + 2 < n:
            out.append(chr(ord(body[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + e)
            i += 2
    return "".join(out)


def _ansi_c(text):
    """TEXT with each $'…' word outside other quotes replaced by its decoded value, single-quoted as shlex reads it
    (shlex keeps `$` and the escapes: `python3 <<< $'# x\\nopen(…)'` read as one comment line; of_secrets-review on
    organic-framework PR 97)."""
    if "$'" not in text:
        return text
    out, i, n, single, double = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if single:
            single = c != "'"
        elif c == "\\" and not single:
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == '"':
            double = not double
        elif c == "'" and not double:
            single = True
        elif c == "$" and not double and text.startswith('$"', i):
            i += 1  # $"…" is "…"
            continue
        elif c == "$" and not double and text.startswith("$'", i):
            m = _ANSI_WORD.match(text, i)
            if m:
                out.append(shlex.quote(_ansi_c_body(m.group(1))))
                i = m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _simple(stext):
    """(head, args, words, to_file, here-string) for one simple command's raw text, honoring redirections
    and a <<< here-string; or None when it runs nothing. A $'…' word is read decoded, as bash runs it."""
    toks, words, to_file, herestr, k = _tokens(_ansi_c(stext)), [], False, None, 0
    while k < len(toks):
        w = toks[k]
        if w == "<<<":
            herestr = toks[k + 1] if k + 1 < len(toks) else ""
            k += 2
        elif w and set(w) <= _REDIR:
            target = toks[k + 1] if k + 1 < len(toks) else ""
            if "<" in w and "<<" not in w and (_secret_path(target) or _memory_path(target)):
                return ("<read", target, None, None, None)  # `done < .env`, `nc h 9 < /proc/kcore`: deny now
            hit = _redirect_key(target) if "<" in w and "<<" not in w else None  # G0.9, G1.13
            if hit:
                return ("<read", hit[1], None, None, None)
            if ">" in w and not target.isdigit() and target not in _DEV_STREAMS and not w.endswith("&"):
                to_file = True
            k += 2
        elif w.isdigit() and k + 1 < len(toks) and toks[k + 1] and set(toks[k + 1]) <= _REDIR:
            k += 1  # a file descriptor number (`2>&1`) belongs to its redirection, it is not an argument
        else:
            words.append(w)
            k += 1
    words = _command_words(words)
    if not words:
        return None
    return os.path.basename(words[0]), words[1:], words, to_file, herestr


# Kernel and process memory (a user organization's rule; RFC 0002 §4.1, Memory, b1e29a3): memory holds every secret a
# process or the kernel has seen, so reading, copying or uploading it, or dumping a process, is a critical deny,
# also inside ssh, sudo, docker exec and kubectl exec. Running a committed script by its file name stays allowed,
# and naming these paths in prose (a commit message, an of send text) is not a read.
_MEMORY_PATH = re.compile(
    r"^(?:/proc/kcore|/dev/k?mem|/proc/vmcore"
    r"|/proc/(?:\d+|self|thread-self|\*|\$\{?\w+\}?|\$\(\w+\))/(?:task/(?:\d+|\*|\$\{?\w+\}?)/)?mem"
    r"|/var/crash(?:/.*)?|/cores/.*|(?:.*/)?vmcore(?:\.(?:flat|kdump|elf|\d[\w.-]*))?|(?:.*/)?core\.\d+)$")
# a file named just `core` only where cores land; a bare `core` word is a grep pattern or a crate far more often
_BARE_CORE = re.compile(r"^(?:\./core|/(?:tmp|var/tmp|var/lib/systemd/coredump|root|home/[^/]+)/core)$")
# in an interpreter's code, only the kernel and process memory files (crash and core names there are prose)
_MEMORY_CODE = re.compile(r"^(?:/proc/kcore|/dev/k?mem|/proc/vmcore|/proc/(?:\d+|self|thread-self)/mem)$")
# commands that read, copy or upload a file argument (beyond _FILE_READERS)
_MEMORY_READERS = _FILE_READERS | {"dd", "hexdump", "hd", "crash", "volatility", "vol", "vol.py", "objdump", "readelf",
                                    "gdb", "lldb", "aws", "gsutil", "rclone", "mc", "az", "s3cmd", "curl", "wget", "nc",
                                    "ncat", "socat", "docker", "podman", "kubectl", "sz", "split", "bzip2", "xz",
                                    "zstd", "7z", "lz4", "md5sum", "sha256sum", "shasum", "cmp", "diff"}
_RECURSIVE = {"-r", "-R", "-a", "--recursive", "--archive"}
_PATTERN_FIRST = {"grep", "rg", "sed", "awk", "jq", "yq"}


def _memory_path(p, recursive=False):
    """A kernel or process memory file, a crash dump or a core file (`core` alone only where cores land, and
    never as the source of a recursive copy: a source folder is often called core)."""
    p = (p or "").strip().strip("'\"")
    if not p:
        return False
    if _MEMORY_PATH.match(p):
        return True
    return bool(_BARE_CORE.match(p)) and not recursive


def _memory_candidates(a):
    """The paths an argument may name: itself, after '@' (curl -F f=@x), after 'key=' (dd if=x), and after a
    'host:' or 'container:' prefix (scp gw:/proc/kcore ., docker cp c:/proc/kcore .)."""
    out = [a]
    if "=" in a:
        out.append(a.split("=", 1)[1])
    out += [x[1:] for x in list(out) if x.startswith("@")]
    out += [x.split(":", 1)[1] for x in list(out) if ":" in x and not x.startswith("/")]
    return out


def _memory_deny(head, args):
    """(reason, token) for a read, copy or upload of memory or a core, or a process memory dump; else None."""
    if head == "gcore":
        return "a process memory dump (gcore)", "gcore " + " ".join(args)[:80]
    if head in ("gdb", "lldb"):
        for k, a in enumerate(args):
            if a in ("-p", "--pid", "--attach-pid", "-n", "--attach-name", "-w", "--wait-for") \
                    or a.startswith(("--pid=", "--attach-pid=", "-p=")) or re.search(r"\battach\b", a) \
                    or (head == "gdb" and k > 0 and a.isdigit() and not args[k - 1].startswith("-")):
                return f"a process memory dump ({head} attached to a process)", f"{head} " + " ".join(args)[:80]
    if head not in _MEMORY_READERS:
        return None
    recursive = bool(_RECURSIVE & set(args)) or any(re.match(r"^-[a-zA-Z]*[rRa][a-zA-Z]*$", a) for a in args
                                                     if head in _COPY)
    if head in _PATTERN_FIRST and not ({"-e", "-f", "--regexp", "--file"} & set(args)):
        pos = [k for k, a in enumerate(args) if not a.startswith("-")]
        if pos:  # the first positional is a pattern or a program (grep x f, sed s/a/b/ f, jq .k f), not a file
            args = args[:pos[0]] + args[pos[0] + 1:]
    for a in _mem_sources(head, args):
        for c in _memory_candidates(a):
            if _memory_path(c, recursive):
                return f"a command that reads memory or a core dump ({c})", c
    return None


_REMOTE_HEADS = {"ssh"} | _RUNNERS
_DENY_CWD = [None]  # the session's working folder while critical_deny runs (relative local script paths)


def _remote_shell(head, args):
    """'shell' when ssh, docker exec or kubectl exec runs a remote shell that reads its script on stdin (ssh with no
    remote command, `bash -s`, `sh`, `sudo bash`), 'code' for an interpreter reading stdin (`python3 -`), else None.
    Its stdin (a heredoc, a `<` file, a `cat file |`) is then a script the guard checks (reported 2026-11-07)."""
    if head not in _REMOTE_HEADS:
        return None
    if head == "ssh":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in _SSH_VALUE_OPTS else 1
        if not args[i + 1:]:
            return "shell"  # no remote command: the login shell runs what arrives on stdin
    nested = _nested(head, args)
    if not nested:
        return None
    s = _simple(nested[0])
    if not s or s[0] == "<read":
        return None
    h, a = s[0], s[1]
    if h in _SHELLS - {"eval"} and "-c" not in a:
        return "shell"
    if h in _INTERPRETERS and "-c" not in a and "-e" not in a and (not a or a[0] == "-"):
        return "code"
    return None


_SYSTEM_GIT = "/usr/bin/git"  # the system's own git (read-only on macOS, root-owned on Linux), never one found on PATH


_DEV_TOOLS = []


def _developer_tools():
    """Are Apple's command line developer tools installed (so /usr/bin/git is git, not the stub that offers them)?
    Read from the folder `xcode-select -p` would print, without running anything; once per process."""
    if not _DEV_TOOLS:
        _DEV_TOOLS.append(any(os.path.isfile(os.path.join(p, "usr", "bin", "git")) for p in
                              ("/Library/Developer/CommandLineTools", "/Applications/Xcode.app/Contents/Developer")))
    return _DEV_TOOLS[0]


def _committed_git_env():
    """The environment of the guard's own git (_committed_clean): no system configuration, no lock, no prompt, no
    lazy fetch, and no transport at all. A git older than 2.44 does not read GIT_NO_LAZY_FETCH, and a repository's
    own protocol.<name>.allow wins over protocol.allow=never, so a promisor's program would run; an empty
    GIT_ALLOW_PROTOCOL wins over both (of_review on organic-framework PR 510; of_agent-server, msg 01a20305-0958)."""
    return {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}


def _committed_clean(path):
    """Is this local file committed at HEAD and unchanged, byte for byte? (a committed script: the sanctioned pattern)
    The guard runs no program a repository names (of_review on organic-framework PR 283: `git ls-files` and `git diff`
    in the file's own repository ran its core.fsmonitor script inside the guard): the system's git compares the blob
    HEAD holds (`rev-parse HEAD:<file>`, which reads no index and no work tree) with the file's bytes hashed with no
    filter (`hash-object --no-filters`), with fsmonitor and hooks off, and no fetch: a partial clone missing HEAD's tree
    would fetch it through the repository's own core.sshCommand (of_secrets-review on organic-framework PR 284), so
    lazy fetching is off and no transport is allowed. On a Mac without the developer tools, /usr/bin/git is a stub
    that offers to install them: no developer tools counts as no git. No system git, or any doubt: not committed."""
    import subprocess
    import sys
    if not os.path.isfile(_SYSTEM_GIT):
        return False
    if sys.platform == "darwin" and not _developer_tools():
        return False
    d = os.path.dirname(os.path.abspath(path)) or "."
    env = _committed_git_env()
    git = [_SYSTEM_GIT, "-C", d, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
           "-c", "protocol.allow=never"]
    try:
        head = subprocess.run(git + ["rev-parse", "--verify", "--quiet", f"HEAD:./{os.path.basename(path)}"],
                              capture_output=True, text=True, timeout=5, env=env)
        if head.returncode != 0 or not head.stdout.strip():
            return False
        here = subprocess.run(git + ["hash-object", "--no-filters", "--", os.path.basename(path)],
                              capture_output=True, text=True, timeout=5, env=env)
        return here.returncode == 0 and here.stdout.strip() == head.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _stdin_script_deny(path, kind, depth):
    """A local file sent as the script of a remote shell or interpreter: allowed when committed and unchanged,
    otherwise its text is checked like a heredoc body."""
    p = _expanduser(path)
    if not os.path.isabs(p) and _DENY_CWD[0]:
        p = os.path.join(_DENY_CWD[0], p)
    if not os.path.isfile(p):
        return None  # nothing to send: the command fails by itself
    if _committed_clean(p):
        return None
    try:
        with open(p, errors="replace") as fh:
            body = fh.read(512 * 1024)
    except OSError:
        return f"a local script sent to a remote shell that the guard cannot read ({path})", path
    return _bash_deny(body, depth + 1) if kind == "shell" else _code_deny(body)


def _stdin_file(stext):
    """The file a simple command reads on stdin (`< file`), or None."""
    toks = _tokens(stext)
    for k, w in enumerate(toks[:-1]):
        if w == "<":
            return toks[k + 1]
    return None


def _mem_sources(head, args):
    """The arguments a command reads (copy and upload sources, dd if=), not the ones it writes."""
    if head in _COPY:
        return _data_args(head, args)
    if head == "dd":
        return [a[3:] for a in args if a.startswith("if=")]
    pos = [a for a in args if not a.startswith("-")]
    if head in ("docker", "podman") and pos[:1] == ["cp"]:
        return pos[1:2]
    if head in ("aws", "gsutil", "s3cmd", "mc") and any(x in pos for x in ("cp", "mv", "sync", "put")):
        k = next(i for i, x in enumerate(pos) if x in ("cp", "mv", "sync", "put"))
        return pos[k + 1:-1] or pos[k + 1:]
    if head == "rclone" and pos[:1] and pos[0] in ("copy", "copyto", "move", "moveto", "sync"):
        return pos[1:-1] or pos[1:]
    return args


# A bare op: denied. It warned while a user organization's op flows (Talos, FRR, ECR) moved to its committed
# script with the service account; its structure keeper reported them moved on 2026-11-07.
OP_BARE_MODE = "deny"
_WARNINGS = []


# The founder's personal 1Password account: never reached by an agent, whatever OP_BARE_MODE says, and no service
# account ever covers it (of_architect, 2026-11-07: an operator position listed items, vault users and groups there).
FOUNDER_OP_ACCOUNT = re.compile(r"(?i)\bmy\.1password\.com\b")


def _op_founder_deny(head, args, stext):
    """(reason, token) for op aimed at the founder's personal account: --account my.1password.com, OP_ACCOUNT set to
    it (inline or exported), or `op account list` (which lists his accounts); else None."""
    if head == "export" and any(a.startswith("OP_ACCOUNT=") and FOUNDER_OP_ACCOUNT.search(a) for a in args):
        return "OP_ACCOUNT set to the founder's personal 1Password account", "export OP_ACCOUNT=…"
    if head != "op":
        return None
    if args[:2] == ["account", "list"] or args[:2] == ["accounts", "list"]:
        return "op account list (the founder's 1Password accounts)", "op account list"
    if any(FOUNDER_OP_ACCOUNT.search(a) for a in args) or re.search(r"(?<![\w])OP_ACCOUNT=\S*my\.1password\.com", stext or ""):
        return "op aimed at the founder's personal 1Password account (my.1password.com)", "op " + " ".join(args)[:60]
    return None


def _op_deny(head, args):
    """A bare `op` on an agent's command line: without OP_SERVICE_ACCOUNT_TOKEN it reaches the founder's 1Password
    desktop app and prompts him (RFC 0003 §1a, ask 29ea1baa). Only a committed script calls op, with the agents'
    service-account token. --version and --help reach nothing. In warn mode it is allowed and noted."""
    if head != "op" or (args and all(a in ("--version", "-v", "--help", "-h", "help") for a in args)):
        return None
    hit = ("a bare op command (it would reach the founder's 1Password; only a committed script calls op, with the "
           "agents' service-account token)", "op " + " ".join(args)[:60])
    if OP_BARE_MODE == "deny":
        return hit
    _WARNINGS.append(hit)
    return None


# Grants are the tenant's (G13; docs/registry.md, Organization rules; of_architect, 4a14716, 2026-11-09): an
# organization's rules are signed deny grants, so an agent that could issue or revoke a grant, sign with the tenant's
# grant key, or rewrite a grants file or the guard's kept denies could lift the rule that limits it.
# The pinned grant public key is the anchor every verifier on this machine trusts: an agent that wrote its own key
# there could sign grants this machine accepts (hand-over 4c7ac78f, 2026-11-11). So is the staged key `of grant rekey`
# reads.
_GRANT_FILES = re.compile(r"(?:^|/)(?:registry/grants\.yaml|\.local/state/of/guard-grants\.json|"
                          r"\.config/of/grant_ed25519\.pub|\.config/of/grant_1password\.pub)$")
_GRANT_KEY_NAME = re.compile(r"(?:^|/)grant_(?:ed25519|ed25519\.pub|1password\.pub)$")
# The owner's 1Password SSH agent: only his own `of grant`, in his terminal, signs through it; an agent naming its
# socket could put a Touch ID prompt in front of him for a signature he did not start.
_OWNER_AGENT = re.compile(r"2BUA8C4S2C\.com\.1password|com\.1password/t/agent\.sock", re.I)
_GRANT_SIGN_CODE = re.compile(r"\b(?:sign_ed25519|cmd_grant)\s*\(")
# G23 in code: of_tools' token reader or its CLI asked for the header (a tripwire; docs/guard.md G23 Known limits)
_TOOL_TOKEN_CODE = re.compile(r"\bof_tools\b[\s\S]*?\b(?:shared_token\s*\(|header\s*\(|main\s*\(\s*\[\s*['\"]header)")
# G24 in code: what only a person does, written by code (a tripwire; docs/guard.md G24): the asks popup's and the
# conductor chat's writers, the relay's typing as a person's channel, and an emit or record of the events that say a
# person answered, typed, or approved (of_server's trust review of organic-framework PR 168)
_PERSON_ACT_NAMES = r"['\"](?:of\.)?(?:agent_question_answered|human_prompt|prompt_origin|diff_approved)['\"]"
_PERSON_ACT_CODE = re.compile(
    r"\bof_asks_answer\b[\s\S]*?\b(?:record|record_answer|owner_mark|not_a_question|answer_blocked|deliver)\s*\("
    r"|\bof_asks_tui\b[\s\S]*?\bleave\s*\("   # the popup records its own answers and marks (of_review on PR 398)
    r"|\bof_asks_chat\b[\s\S]*?\b(?:record|answer)\s*\("
    r"|\bherdr_deliver\s*\("
    r"|\bof_hook\b[\s\S]*?\b(?:main|handle)\s*\("  # the collector's entry, fed a prompt (of_review on PR 170)
    r"|\b(?:emit|record)\s*\((?:\s*|[\s\S]{0,400}?\bkind\s*=\s*)" + _PERSON_ACT_NAMES +
    r"|\badd_event\s*\([\s\S]*?" + _PERSON_ACT_NAMES + r"|" + _PERSON_ACT_NAMES + r"[\s\S]*?\badd_event\s*\(")
_REMOVERS = {"rm", "unlink", "shred", "truncate", "mv"}
_COPIERS = {"cp", "install", "rsync", "ln", "ditto"}
_IN_PLACE = {"sed", "gsed", "perl", "ruby"}
GRANT_DENY = ("a change to signed grants, which only the tenant makes, with `of grant` in a plain terminal (an "
              "organization's rules are deny grants; propose the exact command to the owner as a task)")


def _grant_file(p):
    p = str(p or "").strip("'\"")
    if p and not p.startswith(("/", "~", "$")) and _DENY_CWD[0]:
        p = os.path.join(_DENY_CWD[0], p)
    return bool(p) and bool(_GRANT_FILES.search(os.path.normpath(_expanduser(p))))


def _write_targets(stext):
    """The files a simple command's output redirections write (`> f`, `>> f`, `&> f`, `2> f`)."""
    try:
        toks = _tokens(stext)
    except ValueError:
        return []
    out = [toks[k + 1] for k, w in enumerate(toks[:-1]) if w and set(w) <= _REDIR and ">" in w and
           not w.endswith("&") and toks[k + 1] not in _DEV_STREAMS and not toks[k + 1].isdigit()]
    # `>| f` (the `|` its own word), `exec 3> f` (the number a descriptor): of_review's write forms on PR 395
    out += [toks[k + 2] for k, w in enumerate(toks[:-2]) if w and set(w) <= _REDIR and ">" in w and toks[k + 1] == "|"]
    # `>& f` and `&> f`: stdout and stderr into a file, unless the word is a descriptor or `-` (`2>&1`, `>&-`); and
    # `>|f` with no space, whose `|` the tokens leave on the name (of_review's notes on organic-framework PR 397)
    out += [toks[k + 1] for k, w in enumerate(toks[:-1]) if w in (">&", "&>&") and not toks[k + 1].isdigit()
            and toks[k + 1] != "-" and toks[k + 1] not in _DEV_STREAMS]
    return [t[1:] if t.startswith("|") and len(t) > 1 else t for t in out if t != "|"]


_PY_VALUE_OPTS = {"-W", "-X", "--check-hash-based-pycs"}   # python options that take the next word as a value


_PY_VALUE_LETTERS = "cmWX"   # python's short options that take a value: the rest of the word, else the next word


def _py_walk(args):
    """How python reads ARGS, the getopt way (`-Im of_tools`, `-mof_tools`, `-Ic "…"`): ("module"|"code"|"script",
    the value, the words after it), or (None, None, []) (of_secrets-review on organic-framework PR 136)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        if a.startswith("--"):
            i += 2 if a in _PY_VALUE_OPTS else 1
            continue
        for j in range(1, len(a)):
            if a[j] in _PY_VALUE_LETTERS:
                rest = a[j + 1:]
                val, used = (rest, 1) if rest else ((args[i + 1] if i + 1 < len(args) else ""), 2)
                if a[j] == "m":
                    return "module", val, args[i + used:]
                if a[j] == "c":
                    return "code", val, args[i + used:]
                i += used
                break
        else:
            i += 1
    return ("script", args[i], args[i + 1:]) if i < len(args) else (None, None, [])


def _script_of(head, args):
    """(the script's or module's name, the words after it) when an interpreter runs one, past its own options:
    `python3 -I bin/of grant issue` -> ("of", ["grant", "issue"]), `python3 -m of_tools header` -> ("of_tools.py",
    ["header"]); else (head, args). Shared by G13 and G23 (of_review and of_secrets-review on organic-framework PR 136:
    one flag before the script had moved it out of the rule's place)."""
    if head not in _INTERPRETERS:
        return head, args
    if head.startswith("python"):
        kind, val, rest = _py_walk(args)
        if kind == "module":
            return f"{val}.py", rest
        if kind == "script":
            return os.path.basename(val), rest
        return head, args  # code (_code_deny reads it) or nothing to run
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        if args[i] == "-m" and i + 1 < len(args):
            return f"{args[i + 1]}.py", args[i + 2:]
        if args[i] in ("-c", "-e"):
            return head, args  # code: _code_deny reads it
        i += 2 if args[i] in _PY_VALUE_OPTS else 1
    if i < len(args):
        return os.path.basename(args[i]), args[i + 1:]
    return head, args


# G24 warns first: the code's own keepers ran its writers on live state 12 times in 21 days (debugging the popup and
# the relay's typing); it denies once they test them in an isolated HOME, as tests/isolate.py does.
PERSON_ACT_MODE = "warn"
PERSON_ACT_NOTE = ("code that writes what only a person does (an answer to an ask, a prompt or its origin, a diff's "
                   "approval, text typed as a person's) makes a record that a person acted when none did; test such "
                   "code in an isolated HOME. This is noted now and will be denied (docs/guard.md G24)")
PERSON_ACT_DENY = ("code that writes what only a person does: an answer to an ask, a prompt or its origin, a diff's "
                   "approval, or text typed as a person's (docs/guard.md G24). The person answers in the popup or in "
                   "their conductor's chat; a position closes its own ask with `of status working --answered <id>` "
                   "(on a question to the owner with --prompt <his prompt's event id>), "
                   "and messages another with `of send`")
TOOL_TOKEN_DENY = ("a shared tool server's access token, which only Claude Code reads, through the helper it runs "
                   "itself when it connects (docs/guard.md G23; docs/tools.md §4a)")


def _hook_entry(head, args):
    """G24: the collector's own entry (`of hook <event>`, `bin/of_hook.py`) run from an agent's command line. Fed a
    prompt for any session on stdin, it records that prompt as the person's (of_review on organic-framework PR 170).
    Claude Code runs the hook itself, as its own child, which no Bash call is."""
    head, words = _script_of(head, args)
    if any(w in ("-h", "--help") for w in words):
        return None
    if not ((head == "of" and words[:1] == ["hook"]) or head == "of_hook.py"):
        return None
    if PERSON_ACT_MODE == "deny":
        return PERSON_ACT_DENY, "of hook"
    note = ("the collector's own entry run from a command line", "of hook", PERSON_ACT_NOTE)
    if note not in _WARNINGS:  # 20 runs in 21 days, 17 of them probes in a temporary HOME: it warns first
        _WARNINGS.append(note)
    return None


FORWARD_DIR = "~/.ssh/of-forward"
FORWARD_DENY = ("the forward's own key and host pin (docs/guard.md G34): only its launchd unit uses them; "
                "`python3 deploy/host/forward.py keygen|pin-host|install` handles them without naming them")


def _forward_dir():
    return os.path.realpath(_expanduser(FORWARD_DIR))


_SSH_CARRIERS = ("ssh", "scp", "sftp", "autossh", "mosh")


def _forward_deny(head, args, shvars=None, cwd=None):
    """G34 on one simple command: any word that names the forward's key folder, `ssh -i <its key>` included, since
    a key used is a key the session holds for that call (of_review on organic-framework PR 350: a laptop session
    could open its own forward with it, a listen on the host that the pin makes useless but that can take the
    forward's socket). A word is read as written and with the line's variables filled in (a path held in a variable,
    or a part of one: of_secrets-review on organic-framework PR 397, hand-over b657e8bf), and as G35 reads a path
    (`_in_folders`): by real path without case, a glob, or a path relative to the line's folder (of_secrets-review on
    PR 397: only the literal lower-case name counted)."""
    box = {_forward_dir().casefold()}
    inside = bool(cwd) and _in_folders(cwd, box)
    words = [str(head), *map(str, args)]
    words += [x for x in (_expand_vars(w, shvars or {}) for w in list(words)) if x not in words]
    for w in words:
        sw = str(w).strip().strip("'\"")
        first = (sw.split(None, 1) or [""])[0]
        # split on spaces only a word that carries an ssh command or an ssh option (`rsync -e 'ssh -i …'`, `-o
        # 'IdentityFile …'`), never prose: a message or a document that names the folder is no use of it (the G34
        # replay: 17 false denies of messages and edits when every word was split)
        carried = os.path.basename(first) in _SSH_CARRIERS or re.fullmatch(r"(?i)identityfile|-[io]", first) \
            or re.match(r"-i[~/.$]", first)  # exact, never a prefix: a message starting `-important` is prose
        parts = re.split(r"[=:,\s]+", sw) if carried else (re.split(r"[=:,]", str(w)) if "=" in str(w) else [str(w)])
        _, eq, value = sw.partition("=")
        v0 = (value.strip().strip("'\"").split(None, 1) or [""])[0]
        if eq and (os.path.basename(v0) in _SSH_CARRIERS or re.fullmatch(r"(?i)identityfile|-[io]", v0)
                   or re.match(r"-i[~/.$]", v0)):  # a value after `=` that carries ssh, by its exact first word, for
            parts += re.split(r"[=:,\s]+", value.strip().strip("'\""))  # any name: --rsh=, ProxyCommand=,
            # ssh_command=, --ssh-common-args=, GIT_SSH_COMMAND= (of_review on organic-framework PR 430)
        parts += [x[2:] for x in parts if re.match(r"-[A-Za-z].", x)]  # an option glued to its value: `ssh -i<key>`
        for part in parts:
            p = part.strip("'\"")
            low = p.casefold()
            if not p or not (inside or "forward" in low or ".ssh" in low or any(ch in p for ch in "*?[")):
                continue
            try:
                if _in_folders(p, box, cwd):
                    return FORWARD_DENY, part
            except (OSError, ValueError):
                continue
    return None


def _tool_token_deny(head, args):
    """G23: `of tools header` from an agent's command line would print a shared tool server's token into its
    transcript. Claude Code runs that helper as its own child, which no Bash call is (of_server's trust review of
    organic-framework PR 132)."""
    head, words = _script_of(head, args)
    if any(w in ("-h", "--help") for w in words):
        return None
    if (head == "of" and words[:2] == ["tools", "header"]) or (head == "of_tools.py" and words[:1] == ["header"]):
        return TOOL_TOKEN_DENY, "of tools header"
    return None


# G31: `of` names its caller by these variables, and records an act as the owner's when both are unset (bin/of
# _asks_actor: an ask answered or withdrawn, a task closed). An agent's shell could clear them (of_secrets-review, msg
# 01a1ef00-6f57, 2026-11-18, on organic-framework PR 334, whose Codex training gate rests on that record).
ACTOR_VARS = ("CLAUDE_CODE_SESSION_ID", "OF_POSITION")
ACTOR_DENY = ("of run with the variables that name its caller cleared (docs/guard.md G31): with CLAUDE_CODE_SESSION_ID "
              "and OF_POSITION unset, of records the act as the owner's. An agent answers, withdraws and closes as "
              "itself: leave them as its session set them; an act that is the owner's waits for the owner")


def _actor_cleared(stext, env):
    """The ACTOR_VARS that one simple command runs with cleared: by the line's `unset` or `export NAME=` (ENV, an unset
    as ""), or by its own prefix: `env -i`, `env -`, `env -u NAME`, `env --unset=NAME`, `NAME=` (a later `NAME=value`
    sets it again)."""
    out = {k for k in ACTOR_VARS if k in (env or {}) and not (env or {}).get(k)}
    try:
        toks = _tokens(stext)
    except ValueError:
        return out
    prefix = toks[:max(0, len(toks) - len(_command_words_of(list(toks))))]
    in_env, k = False, 0
    while k < len(prefix):
        w = prefix[k]
        m = re.match(r"^([A-Za-z_]\w*)=(.*)$", w, re.S)
        if os.path.basename(w).lower() == "env":
            in_env = True
        elif m:
            if m.group(1) in ACTOR_VARS:
                (out.discard if m.group(2) else out.add)(m.group(1))
        elif in_env and w in ("-u", "--unset") and k + 1 < len(prefix):
            if prefix[k + 1] in ACTOR_VARS:
                out.add(prefix[k + 1])
            k += 1
        elif in_env and w.startswith("--unset="):
            if w.split("=", 1)[1] in ACTOR_VARS:
                out.add(w.split("=", 1)[1])
        elif in_env and w in ("-", "--ignore-environment"):
            out.update(ACTOR_VARS)
        elif in_env and w.startswith("-") and not w.startswith("--"):
            flags, _, rest = w[1:].partition("u")
            if "i" in flags:
                out.update(ACTOR_VARS)
            if rest in ACTOR_VARS:  # -uNAME
                out.add(rest)
        elif not w.startswith("-"):
            in_env = False  # a wrapper, or a wrapper option's value
        k += 1
    return out


# G31 over the whole line, a second layer behind bin/of's positive test for the owner (_asks_actor, `of access`): a line
# that runs one of `of`'s acts anywhere (a group, a `-c` body, a quoted string) and anywhere also holds a way to clear
# or replace the variables that name the caller is refused. Following each way one at a time missed a new one at each
# review (of_secrets-review and of_review on organic-framework PR 387: `unset …; ( of asks answered )`, a leading
# redirection, `env -P`, `sh -xc`, two bodies deep, `of access --org x grant`, `NAME="$NAME"`, `export -n`, `declare +x`,
# `exec -c`, `NAME=$EMPTY`, `X=NAME; unset $X`, ssh localhost, at, osascript, launchctl submit, python's subprocess).
_FRESH_ENV = frozenset("ssh at batch osascript launchctl systemd-run su sudo doas machinectl runuser setpriv "
                       "nsenter unshare chroot".split())
# a pseudo-terminal around the act: the way round bin/of's terminal test for the owner (of_server on hand-over 726902df)
_PTY_WORDS = frozenset("script expect unbuffer socat empty ptyrun tmux screen dtach abduco zpty".split())
_INTERPRETER_WORDS = frozenset("python python3 python2 pypy pypy3 node nodejs ruby perl php lua osascript "
                               "swift deno bun".split())


def _line_words(cmd):
    """The line's words as a shell may see them once its quotes are gone, quoted bodies included: quote marks and
    backslashes dropped (`'un'set` is unset), every operator and expansion character a separator."""
    flat = re.sub(r"[\\'\"]", "", cmd)
    return [w for w in re.split(r"[\s;&|()<>{}`$=,:\[\]]+", flat) if w]   # `:` too: socat's EXEC:'of …'


def _line_act(words):
    """Does the word stream run one of of's acts: `of asks answered|withdraw|answer <id>`, `of access … grant|revoke|
    link|record` (its options may come first)?"""
    for k, w in enumerate(words):
        if _fold_name(os.path.basename(w)) not in ("of", "of.py"):
            continue
        # every word up to the next `of`: options before the action may take values, any number of them (`--pane a
        # --text b --version 2 answered`: of_review on organic-framework PR 404 at 86a7b12)
        end = next((j for j in range(k + 1, len(words)) if _fold_name(os.path.basename(words[j])) in ("of", "of.py")),
                   len(words))
        rest = [_fold_name(x) for x in words[k + 1:end]]
        # no `--help` exception here: the words have lost their quotes, so a reason `"--help me"` read as the flag,
        # while argparse takes it as the reason and runs the act (of_review on organic-framework PR 399 at 7c776fe);
        # the per-command reading, which has the real words, keeps it (_actor_act)
        if rest[:1] == ["asks"] and any(x in ACTOR_ACTS for x in rest[1:]):
            # options before the action, with or without a value (`--json`, `--pane p1`, `--text hello`, `--pane=p1`:
            # of_review on organic-framework PR 404): any act word among the words after `asks`
            return True
        if rest[:1] == ["access"] and any(x in ("grant", "revoke", "link", "record") for x in rest[1:]):
            return True
        if _org_path_act(rest):
            return True
    return False


_PROSE_OF = frozenset({"send", "ask", "handover", "status", "event", "note", "diff", "loop"})


# interpreter code that can start a process: only then can an act named in its text run (the 21-day replay of hand-over
# 726902df: 153 of 384 new G31 denies were python edits of a document whose text names `of asks answered`)
_LAUNCH_CODE = re.compile(r"subprocess|os\.(?:system|exec|spawn|posix_spawn|popen)|\bpopen\b|\bpty\.|child_process|"
                          r"exec(?:File)?Sync|spawn(?:Sync)?\s*\(|\bsystem\s*\(|\bexec\s*\(|Open3|"
                          r"IO\.popen|Process\.|\bKernel\.|shell_exec|passthru|proc_open|NSTask|do shell script")
_LAUNCH_BACKTICK = re.compile(r"`|\bqx\b")   # a command in ruby, perl and php; in python or a document, text


def _launches(head, code):
    """Can interpreter HEAD's CODE start a process?"""
    return bool(_LAUNCH_CODE.search(code)) or (not str(head).startswith(("python", "pypy", "node")) and
                                               bool(_LAUNCH_BACKTICK.search(code)))


def _without_code_text(cmd):
    """CMD without the bodies of interpreter heredocs that cannot start a process (their text is data: a document an
    edit writes, a pattern it searches)."""
    try:
        docs = _scan(cmd)[2]
    except Exception:  # noqa: BLE001
        return cmd
    for _quoted, body, opener in docs:
        head = _fold_name(_head(opener))
        runs = head in _SHELLS or head in _FRESH_ENV or head in _PTY_WORDS or \
            ((head in _INTERPRETERS or _lang(head)) and _launches(head, body))
        if body and not runs:   # `cat > notes <<EOF`, a commit message, a test file: data, not a command
            cmd = cmd.replace(body, "", 1)
    return cmd


def _runners_after(pipe):
    """For each stage of PIPE, does a later stage run its text (a shell, at, ssh, a pty, an interpreter, xargs)? Read
    once from the pipe's end, so a long pipe costs one pass (of_review on PR 399: a lookahead per stage was quadratic)."""
    out, seen = [False] * len(pipe), False
    for idx in range(len(pipe) - 1, -1, -1):
        out[idx] = seen
        kind, stext = pipe[idx]
        try:
            s = _simple(stext) if kind == "cmd" else None
        except ValueError:
            seen = True
            continue
        head = _fold_name(s[0]) if s and s[0] != "<read" else ""
        if head in _SHELLS or head in _FRESH_ENV or head in _PTY_WORDS or head in _INTERPRETERS or head == "xargs":
            seen = True
    return out


def _substitutions(stext):
    """The command lines inside a stage's `$( … )` and backticks: they run even in a command that only carries text
    (`of send x "$(… of asks withdraw …)"`: of_review after organic-framework PR 399)."""
    try:
        return list(_scan(stext)[1])
    except Exception:  # noqa: BLE001
        return [stext]


# GNU sed runs a command with its `e` command, after any address and with its command glued to it (`1e`, `$eCMD`,
# `0~2e`, `/re/e`), or the s command's `e` flag among others (`ge`, `pe`, `Ie`); BSD sed has neither. A sed is text
# only when its program is parsed whole as s commands whose delimiter is no letter and whose flags hold no e: any other
# command, the e command among them, keeps it for the line reading (of_review on organic-framework PR 427 at b0c116f,
# after the lone-e reading missed an e touching a letter; of_server on 9b5de527).
_SED_ADDR = re.compile(r"(?:\d+(?:~\d+)?|\$|/(?:[^/\\\n]|\\.)*/[IM]*)"
                       r"(?:\s*,\s*(?:\d+|\$|/(?:[^/\\\n]|\\.)*/[IM]*|[+~]\d+))?\s*(?:!\s*)?")


def _sed_s_only(prog):
    """Is PROG nothing but s commands with a non-letter delimiter and flags without e?"""
    i, n, seen = 0, len(prog), False
    while i < n:
        while i < n and prog[i] in " \t\n;":
            i += 1
        if i >= n:
            break
        m = _SED_ADDR.match(prog, i)
        if m:
            i = m.end()
        if i + 1 >= n or prog[i] != "s":
            return False
        d = prog[i + 1]
        if d.isalnum() or d in "\\\n \t":
            return False
        j, parts = i + 2, 0
        while j < n and parts < 2:
            if prog[j] == "\\":
                j += 2
                continue
            if prog[j] == d:
                parts += 1
            j += 1
        if parts < 2:
            return False
        while j < n and prog[j] in "gpIiMm0123456789":
            j += 1
        if j < n and prog[j] == "w":   # w FILE runs to the line's end: a write, no command
            nl = prog.find("\n", j)
            j = n if nl < 0 else nl
        if j < n and prog[j] not in " \t\n;":
            return False   # an e flag, or anything else after the flags
        i, seen = j, True
    return seen


def _sed_run_texts(prog):
    """The command lines a sed program runs: each e command's text, and an s command's replacement when its flags
    hold e (GNU sed runs the pattern space it made), whatever the delimiter."""
    out, i, n = [], 0, len(prog)
    while i < n:
        while i < n and prog[i] in " \t\n;{}":
            i += 1
        if i >= n:
            break
        m = _SED_ADDR.match(prog, i)
        if m:
            i = m.end()
        if i >= n:
            break
        c = prog[i]
        if c == "e":
            end = n
            for sep in "\n;}":
                k = prog.find(sep, i + 1)
                if k >= 0:
                    end = min(end, k)
            out.append(prog[i + 1:end])
            i = end
            continue
        if c == "s" and i + 1 < n:
            d, j, parts, rep = prog[i + 1], i + 2, 0, []
            while j < n and parts < 2:
                if prog[j] == "\\" and j + 1 < n:
                    if parts == 1:
                        rep.append(prog[j + 1])
                    j += 2
                    continue
                if prog[j] == d:
                    parts += 1
                elif parts == 1:
                    rep.append(prog[j])
                j += 1
            k = j
            while k < n and (prog[k].isalnum()):
                k += 1
            if "e" in prog[j:k]:
                out.append("".join(rep))
            i = k
            continue
        k = min([x for x in (prog.find(sep, i) for sep in "\n;") if x >= 0] or [n])
        i = k + 1
    return [t for t in out if t.strip()]


def _sed_run_lines(cmd):
    """The command lines the line's sed programs run, read by G31 as lines of their own (of_server on a9d0635c: in
    `sed '$eof asks answered …'` the act's first word is glued to the e command, so no word on the line is `of`)."""
    if not re.search(r"\bg?sed\b", cmd):
        return []
    try:
        pipes = _split(cmd)
    except ValueError:
        return []
    out = []
    for pipe in pipes:
        for kind, stext in pipe:
            if kind != "cmd":
                continue
            try:
                toks = _tokens(stext)
            except ValueError:
                continue
            k = next((i for i, t in enumerate(toks) if os.path.basename(t) in ("sed", "gsed")), None)
            if k is None:
                continue
            for prog in _sed_programs(toks[k + 1:]):
                out += _sed_run_texts(prog)
    return out


def _sed_text(args):
    """Is this sed's program on the line and unable to run a command? `-f FILE`/`--file` puts it elsewhere."""
    if any(re.match(r"^-[A-Za-z]*f", a) or a.startswith("--file") for a in args):
        return False
    progs = _sed_programs(args)
    return bool(progs) and all(_sed_s_only(prog) for prog in progs)


def _sed_programs(args):
    """The program words of a sed call: each -e/--expression value, else its first operand (BSD's `-i ''` suffix is
    no operand)."""
    progs, expr, i = [], False, 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--expression"):
            progs.append(args[i + 1] if i + 1 < len(args) else "")
            expr, i = True, i + 2
            continue
        if a.startswith("--expression="):
            progs.append(a.split("=", 1)[1])
            expr = True
        elif re.match(r"^-[A-Za-z]*e.", a) and not a.startswith("--"):
            progs.append(a[a.index("e") + 1:])
            expr = True
        elif a == "-i" and i + 1 < len(args) and args[i + 1] == "":
            i += 2
            continue
        elif not a.startswith("-") and not expr and not progs:
            progs.append(a)
        i += 1
    return progs


def _without_prose(cmd):
    """CMD without the top-level commands that only carry text (`of send "…"`, `of ask`, `of handover`, `of status
    --note`, `git commit -m`, `gh … --body`, release/request.sh's note): naming a form there runs nothing. `echo` stays:
    `echo '…' | at now` runs it."""
    try:
        pipes = _split(cmd)
    except ValueError:
        return cmd
    kept = []
    for pipe in pipes:
        fed = _runners_after(pipe)
        for idx, (kind, stext) in enumerate(pipe):
            try:
                s = _simple(stext) if kind == "cmd" else None
            except ValueError:   # a line its rules cannot split: kept whole, never dropped as prose
                s = None
            if s and s[0] != "<read":
                head, args = s[0], s[1]
                h, words = _script_of(head, args)
                if (h == "of" and words[:1] and words[0] in _PROSE_OF) or (head == "git" and "commit" in args[:3]) \
                        or head == "gh" or os.path.basename(head) in ("request.sh", "commit.sh", "pcommit.sh"):
                    kept += _substitutions(stext)   # its text is prose, but a `$( … )` in it runs
                    continue
                if head in ("echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "ack") and not fed[idx]:
                    kept += _substitutions(stext)
                    continue   # text printed or written to a file; `echo '…' | at now` is kept
                if os.path.basename(head) in ("sed", "gsed") and not fed[idx] and _sed_text(args):
                    # a sed program is text it writes, unless its `e` command or the s command's `e` flag runs it
                    # (of_review's probe commit, 2026-11-19 23:15Z, of_architect msg 01a1f620-bc82: a header naming
                    # `of org set <org> path` beside `python3 -P` and `at` was read as an act and a launcher)
                    kept += _substitutions(stext)
                    continue
                if (head in _INTERPRETERS or _lang(head)) and h != "of" and \
                        not _launches(head, " ".join(args)):   # `python3 -c "…"` that starts no process
                    continue
                if h == "of" and words[:1] == ["asks"] and words[1:2] and words[1] in ACTOR_ACTS:
                    # its reason or its text is prose: the command is kept up to the ask's id, its prefix included
                    try:
                        toks = _tokens(stext)
                    except ValueError:
                        toks = []
                    k = next((i for i in range(len(toks) - 1) if toks[i + 1] == "asks" and
                              _fold_name(os.path.basename(toks[i])) in ("of", "of.py")), None)
                    if k is not None:
                        kept.append(" ".join(shlex.quote(x) for x in toks[:k + 4]))
                        continue
            kept.append(stext)
    return " ; ".join(kept)


# interpreter code that changes the environment of what it starts (python's env= or os.environ, node's env:, ruby's
# ENV or unsetenv_others, perl's %ENV, a pty or an exec with its own environment)
_ENV_CODE = re.compile(r"\benv\s*[=:]|environ|unsetenv|putenv|clearenv|\bENV\b|%ENV|\$ENV|pty\.|execve|execle|"
                       r"posix_spawn|unsetenv_others")


def _command_word(text, names, interpreters=False):
    """Does a word of NAMES stand where a command starts in TEXT (its start, after `;`, `&`, `|`, `(`, `{`, a backtick,
    `$(`, a newline or a quote that opens a body, past assignments and a folder)? A word in a message or a reason is
    prose (`… answered at 12:00` is no `at`: the 21-day replay of hand-over 726902df)."""
    for m in re.finditer(r"(?:^|[;&|({`\n]|\$\(|\b-c\s|\bexec\s|\bsudo\s|\bnohup\s|\btimeout\s+\S+\s)\s*"
                         r"(?:[A-Za-z_]\w*=\S*\s+)*(?:\S*/)?([A-Za-z][\w.+-]*)", text):
        w = _fold_name(m.group(1))
        if w in names or (interpreters and _interpreter_name(w) in names):
            return True
    return False


def _actor_line_deny(cmd):
    """(reason, what) when the line runs one of of's acts and holds a way to clear or replace its caller's variables
    anywhere (G31's line reading); else None. A help call with a clear on the line is refused too: the line's words
    cannot tell `--help` from a reason or an option value that holds it (`--help" me"`, `-- --help`, `label=--help`:
    of_review on organic-framework PR 399 at 7c776fe and 45eb458)."""
    runs = _sed_run_lines(cmd)
    if runs:   # what a sed runs is a command line of its own (of_server on a9d0635c)
        cmd = cmd + "\n" + "\n".join(runs)
    if not _line_act(_line_words(cmd)):   # no act of of anywhere on the raw line: nothing to read further (linear)
        return None
    cmd = _without_prose(_without_code_text(cmd))
    words = _line_words(cmd)
    if not _line_act(words):
        return None
    folded = [_fold_name(os.path.basename(w)) for w in words]
    flat = re.sub(r"[\\'\"]", "", cmd)
    why = None
    if any(re.search(rf"\b{v}=(?:\s|$|[;&|)]|\$)|\b(?:unset|export\s+-\w*n|declare|typeset|local|readonly)\b"
                     rf"[^;&|\n]*\b{v}\b|(?:-u\s*|--unset=?\s*){v}\b", flat) for v in ACTOR_VARS):
        why = "a variable that names its caller, cleared on the line"   # an empty value, unset, -u, export -n…
    elif any(f == "env" and k + 1 < len(words) and words[k + 1].startswith("-") for k, f in enumerate(folded)):
        why = "env with an option"
    elif re.search(r"(?<![\w-])exec\s+-\w*c", flat):
        why = "exec -c"
    elif re.search(r"(?<![\w-])(?:unset|export\s+-\w*n|declare\s+[+-]\w*|typeset\s+[+-]\w*)\b[^;&|\n]*\$", flat):
        why = "a variable cleared through an expansion"
    elif re.search(r"(?<![\w-])(?:export\s+-\w*n|declare\s+\+|typeset\s+\+)", flat):
        why = "a variable taken out of the environment"
    elif _command_word(flat, _FRESH_ENV):
        why = "a command that starts with an environment of its own"
    elif _command_word(flat, _PTY_WORDS):
        why = "a pseudo-terminal around it"
    elif _command_word(flat, _INTERPRETER_WORDS, interpreters=True) and _ENV_CODE.search(flat):
        k_of = next(k for k, w in enumerate(words) if _fold_name(os.path.basename(w)) in ("of", "of.py"))
        before = folded[k_of - 1] if k_of else ""
        if not (before in _INTERPRETER_WORDS or _interpreter_name(before) in _INTERPRETER_WORDS):
            why = "interpreter code that runs it"   # `python3 bin/of asks answered` itself stays the plain act
    return (ACTOR_DENY, "of's act with " + why) if why else None


# the acts `of` records with its caller (bin/of _asks_actor: an ask answered or withdrawn; `answer` is a person's
# answer given in a chat); `of status --done-task` needs the caller's position and fails without one
ACTOR_ACTS = ("answered", "withdraw", "answer", "deny")   # deny: of asks deny <card> (of_review on PR 443)


def _actor_act(words):
    # `of access grant|revoke|link|record` write a keeper's list or its approvals, and trust the caller's session the
    # same way (of_review on organic-framework PR 357)
    if any(w in ("--help", "-h") for w in words[2:4]):
        return False  # `of asks withdraw --help` records nothing (of_review on organic-framework PR 387)
    return (words[:1] == ["asks"] and len(words) > 1 and words[1] in ACTOR_ACTS) or \
        (words[:1] == ["access"] and len(words) > 1 and words[1] in ("grant", "revoke", "link", "record")) or \
        _org_path_act(words)


def _org_path_act(words):
    """`of org set <org> path <folder>`: the owner's, by the positive test (of_review on organic-framework PR 427)."""
    # by position, so `of org set path hold on` (an organization named path) stays the agents' hold (of_server); a
    # double dash is no positional for argparse, so it is dropped before the places are read (of_review on PR 427 at
    # 0159efd: `of org -- set ofb path`, `of org set -- ofb path`, `of org set ofb -- path` ran the act)
    words = [w for w in words if w != "--"]
    return words[:2] == ["org", "set"] and words[3:4] == ["path"]


def _actor_deny(head, args, stext, env):
    """(reason, what) for G31 on one simple command, else None: `of asks answered|withdraw|answer` (also as `python3
    bin/of`, or in a shell's `-c` code) with a variable that names its caller cleared, whatever HOME it is given (a
    HOME of its own can link its state to the real one)."""
    cleared = _actor_cleared(stext, env)
    if not cleared:
        return None
    what = "of with " + " and ".join(sorted(cleared)) + " cleared"
    h, words = _script_of(head, args)
    if h == "of" and _actor_act(words):
        return ACTOR_DENY, what
    if head in _RUN_SHELLS:
        i = next((n for n, a in enumerate(args) if a.startswith("-") and not a.startswith("--") and "c" in a[1:]), None)
        body = args[i + 1] if i is not None and i + 1 < len(args) else None
        try:
            segs = _segments(body) if body else []
        except ValueError:
            segs = []
        for seg in segs:
            w = _command_words(seg)
            h, words = _script_of(os.path.basename(w[0]), w[1:]) if w else ("", [])
            if h == "of" and _actor_act(words):
                return ACTOR_DENY, what
    return None


def _grant_deny(head, args, stext):
    """(reason, token) for G13 on one simple command, else None."""
    head, words = _script_of(head, args)  # python3 [-I …] bin/of grant issue
    if _OWNER_AGENT.search(stext):
        return GRANT_DENY, "the owner's 1Password SSH agent"
    if head == "of" and words[:1] == ["grant"] and len(words) > 1 and words[1] in ("issue", "revoke", "rekey") and \
            not any(w in ("-h", "--help") for w in words):
        return GRANT_DENY, "of grant " + words[1]
    # docs/remote.md §6: a passkey's enrollment window and its revocation are the owner's acts, signed with the grant
    # key like a grant (of_architect's hand-over e15207eb)
    if head == "of" and words[:1] == ["passkey"] and len(words) > 1 and words[1] in ("enroll", "revoke") and \
            not any(w in ("-h", "--help") for w in words):
        return GRANT_DENY, "of passkey " + words[1]
    if head == "ssh-keygen" and "-Y" in args and "sign" in args and \
            any(_GRANT_KEY_NAME.search(a) for a in [_opt_value(args, ("-f",)) or ""]):
        return GRANT_DENY, "ssh-keygen -Y sign with the tenant's grant key"
    hit = next((f for f in _changed_files(head, args, stext) if _grant_file(f)), None)
    return (GRANT_DENY, hit) if hit else None


def _target_dir(args):
    """The folder `-t DIR`, `-tDIR` or `--target-directory=DIR` names for cp, mv, install or ln, else None."""
    for k, a in enumerate(args):
        if a in ("-t", "--target-directory") and k + 1 < len(args):
            return args[k + 1]
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1]
        if a.startswith("-t") and len(a) > 2 and not a.startswith("--"):
            return a[2:]
    return None


def _download_targets(head, args):
    """The files curl's -o/--output or wget's -O/--output-document write, and wget's -P folder."""
    out, opts = [], (("-o", "--output") if head == "curl" else ("-O", "--output-document", "-P", "--directory-prefix"))
    for k, a in enumerate(args):
        for o in opts:
            if a == o and k + 1 < len(args):
                out.append(args[k + 1])
            elif a.startswith(o + "=") and o.startswith("--"):
                out.append(a.split("=", 1)[1])
            elif len(o) == 2 and a.startswith("-") and not a.startswith("--") and o[1] in a[1:]:
                rest = a[a.index(o[1], 1) + 1:]   # a cluster: `-qO f`, `-qOf`
                out.append(rest if rest else (args[k + 1] if k + 1 < len(args) else ""))
    return [x for x in out if x != "-"]


def _sed_w_files(args):
    """The files a sed program writes with its w command or the s command's w flag (`sed 's/a/b/w out'`)."""
    import re as _re
    progs, k = [], 0
    while k < len(args):
        a = args[k]
        if a in ("-e", "--expression") and k + 1 < len(args):
            progs.append(args[k + 1])
            k += 2
            continue
        if not a.startswith("-"):
            progs.append(a)
            break
        k += 1
    out = []
    for prog in progs:
        out += _re.findall(r"(?:^|[;\n{}]|/[gpIiMme0-9]*)\s*w\s+([^\s;}]+)", prog)
    return out


# Editors that write the files they are given (of_server, msg 01a1f9c3-11bd, on G36: sponge passed as tee does not):
# sponge writes its file argument from stdin; ed, ex and the vi family write theirs with a script's w (ed -s f <<< w,
# ex -sc wq f, vim -es); an option's value is no file. A read-only use of an editor on a protected file is refused
# too, the cheaper side.
_EDITORS = {"ed", "red", "ex", "vi", "vim", "nvim", "view", "gvim", "evim"}
_EDITOR_VALUE_OPTS = {"-c", "-S", "-u", "-U", "-i", "-T", "-w", "-W", "-s", "--cmd", "-p", "-o", "-O", "-t", "-q",
                      "--listen", "--server", "--remote"}


def _editor_files(head, args):
    """The files an editor is given: every operand, past the values of options that take one."""
    out, k = [], 0
    takes = {"-p"} if head in ("ed", "red") else _EDITOR_VALUE_OPTS - {"-s", "-p", "-o", "-O"}
    while k < len(args):
        a = args[k]
        if a == "--":
            out += args[k + 1:]
            break
        if a.startswith("+"):
            k += 1
            continue
        if a.startswith("-"):
            k += 2 if a in takes else 1
            continue
        out.append(a)
        k += 1
    return out


def _awk_inplace_files(args):
    """The files `awk -i inplace` (gawk's in-place extension) rewrites: the operands after the program, or all of them
    with -f."""
    inplace, ops, prog_file, k = False, [], False, 0
    while k < len(args):
        a = args[k]
        if a in ("-i", "--include") and k + 1 < len(args):
            inplace |= args[k + 1].startswith("inplace")
            k += 2
            continue
        if a.startswith(("-iinplace", "--include=inplace")):
            inplace = True
        elif a in ("-f", "--file", "-v", "--assign", "-F", "--field-separator", "-l", "--load", "-E", "--exec"):
            prog_file |= a in ("-f", "--file", "-E", "--exec")
            k += 2
            continue
        elif not a.startswith("-") or a == "-":
            ops.append(a)
        k += 1
    if not inplace:
        return []
    return [a for a in (ops if prog_file else ops[1:]) if "=" not in a.split("/")[0]]


def _awk_out_files(args):
    """The files an awk program prints to (`print > "f"`, `printf … >> "f"`)."""
    import re as _re
    prog = next((a for a in args if not a.startswith("-")), "")
    return _re.findall(r"""\bprintf?\b[^;\n}]*?>{1,2}\s*["']([^"']+)["']""", prog)


def _find_exec_writes(args):
    """The files `find … -exec CMD … ;` writes when CMD copies or moves (its destination), from each -exec part."""
    out, k = [], 0
    while k < len(args):
        if args[k] in ("-exec", "-execdir", "-ok", "-okdir"):
            j = k + 1
            while j < len(args) and args[j] not in (";", "\\;", "+"):
                j += 1
            part = args[k + 1:j]
            if part and os.path.basename(part[0]) in _COPIERS | {"mv"}:
                d = _target_dir(part[1:])
                ops = [a for a in part[1:] if not a.startswith("-") and a != "{}"]
                out += [d] if d is not None else ops[-1:]
            k = j
        k += 1
    return out


def _changed_files(head, args, stext):
    """The files one simple command writes, removes, moves over or changes in place (G13, G17)."""
    files = list(_write_targets(stext))
    if head in ("tee", "sponge") or head in _REMOVERS or \
            (head in _IN_PLACE and any(a.startswith("-i") or a == "-pi" for a in args)):
        files += [a for a in args if not a.startswith("-")]
    elif head in _EDITORS:
        files += _editor_files(head, args)
    elif head in _COPIERS and _target_dir(args) is not None:
        # `cp -t DIR src…`, `--target-directory=DIR`: each source lands under its name there (of_review on PR 395)
        d = _target_dir(args)
        files += [d] + [os.path.join(d, os.path.basename(a.rstrip("/"))) for a in args
                        if not a.startswith("-") and a != d]
    elif head in _COPIERS:
        ops = [a for a in args if not a.startswith("-")]
        files += ops[-1:]
        if len(ops) > 1 and (ops[-1].endswith("/") or os.path.isdir(_expanduser(ops[-1]))):
            # into a folder: each source lands under its own name (`cp /tmp/orgs.yaml ~/.config/of/`: of_secrets-review
            # on organic-framework PR 387)
            files += [os.path.join(ops[-1], os.path.basename(a.rstrip("/"))) for a in ops[:-1]]
    elif head == "find" and _find_exec_writes(args):
        files += _find_exec_writes(args)
    elif head == "find" and ("-delete" in args or any(
            a in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(args) and os.path.basename(args[k + 1]) in
            (_REMOVERS | _COPIERS | _IN_PLACE | {"tee", "chmod", "touch", "dd", "yq"}) for k, a in enumerate(args))):
        files += [a for a in args[:next((k for k, a in enumerate(args) if a.startswith(("-", "(", "!"))), len(args))]]
    elif head == "yq" and any(a in ("-i", "--inplace") or (a.startswith("-") and not a.startswith("--") and "i" in a)
                              for a in args):
        files += [a for a in args if not a.startswith("-")]
    elif head == "dd":
        files += [a[3:] for a in args if a.startswith("of=")]
    elif head in ("curl", "wget"):
        files += _download_targets(head, args)
    elif head in ("sed", "gsed"):
        files += _sed_w_files(args)
    elif head in ("awk", "gawk", "mawk", "nawk"):
        files += _awk_out_files(args) + _awk_inplace_files(args)
    elif head in ("chmod", "chflags", "xattr"):
        files += [a for a in args[1:] if not a.startswith("-")]
    elif head == "git" and args[:1] and args[0] in ("checkout", "restore", "rm", "mv"):
        files += [a for a in args[1:] if not a.startswith("-")]
    return files


# ------------------------------------------------- the release gate's hooks (G17)
# of_architect (msg 01a1cbed, 2026-11-11), after three leaking commits; AGENTS.md forbids --no-verify (d90e8f0). In a
# repository whose pre-commit hook is the release gate (installed by organic-framework-build release/install-hooks.sh,
# found by its marker line), an agent never skips the hooks or moves them: --no-verify (any subcommand), `commit -n`,
# `-c core.hooksPath=`, a change to core.hooksPath, or a write to .git/hooks/. Elsewhere nothing changes: a user
# organization's own repositories keep their own hook rules. Replay over 21 days (218,571 commands): no live flow in a
# gated repository; the hits were all in other repositories.
GATE_MARK = b"organic-framework-build release/install-hooks.sh"
HOOKS_DENY = ("the release gate's hooks, which nobody skips or changes in this repository (AGENTS.md; docs/guard.md "
              "G17): fix what the gate names and commit again; of_release keeps the hooks")
_GATED = {}
_COMMIT_VALUE_FLAGS = set("mFCctSu")   # a short flag of git commit that takes the rest of its cluster as a value


def _no_verify(a):
    """--no-verify, or an abbreviation git accepts for it (--no-veri, --no-verif; of_architect's review of PR 6)."""
    return a.startswith("--no-veri") and "--no-verify".startswith(a)


def _git_config_pairs(args, env):
    """[(key in lower case, value)] a git call sets for itself: `-c key=value` before its subcommand, and
    GIT_CONFIG_COUNT with GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n>, and GIT_CONFIG_PARAMETERS in its environment
    (of_architect's review of PR 6: core.hooksPath set through the environment)."""
    out, i = [], 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-c" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            out.append((k.lower(), v))
            i += 2
            continue
        if args[i] == "--config-env" and i + 1 < len(args) or args[i].startswith("--config-env="):
            # `--config-env=core.pager=VAR`: the value is VAR's, as the line sets it (of_secrets-review on PR 400)
            spec = args[i + 1] if args[i] == "--config-env" else args[i].split("=", 1)[1]
            k, _, var = spec.partition("=")
            out.append((k.lower(), str((env or {}).get(var) or "")))
            i += 2 if args[i] == "--config-env" else 1
            continue
        if args[i] in ("-C", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
            continue
        i += 1
    try:
        n = int((env or {}).get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    for j in range(min(max(n, 0), 64)):
        out.append((str(env.get(f"GIT_CONFIG_KEY_{j}") or "").lower(), str(env.get(f"GIT_CONFIG_VALUE_{j}") or "")))
    params = str((env or {}).get("GIT_CONFIG_PARAMETERS") or "")
    for k, inline, quoted in re.findall(r"'([^'=]+)(?:=([^']*))?'(?:='([^']*)')?", params):
        out.append((k.lower(), quoted or inline or ""))
    return out


def _config_files_set(env, word):
    """Does a config file the environment points git at (GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM, GIT_CONFIG) name
    WORD? /dev/null names nothing; a file that cannot be read counts as naming it."""
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG"):
        f = (env or {}).get(k)
        if not f or f == "/dev/null":
            continue
        try:
            with open(_expanduser(f), errors="replace") as fh:
                if word in fh.read(1 << 20).lower():
                    return True
        except OSError:
            return True
    return False


# Aliases (of_architect's second review of PR 6): git runs an alias for a subcommand it does not have, and gh for a
# command it does not have, so `git -c alias.p=push p` and `gh alias set m "pr merge" && gh m 12` must be read as what
# they run. git never lets an alias replace a built-in command, so only another word needs expanding.
GIT_BUILTINS = frozenset("""add am annotate apply archive bisect blame branch bugreport bundle cat-file check-attr
check-ignore check-mailmap check-ref-format checkout checkout-index cherry cherry-pick citool clean clone column commit
commit-graph commit-tree config count-objects credential describe diagnose diff diff-files diff-index diff-tree
difftool fast-export fast-import fetch fetch-pack filter-branch fmt-merge-msg for-each-ref for-each-repo format-patch
fsck gc get-tar-commit-id grep gui hash-object help hook http-backend index-pack init instaweb interpret-trailers log
ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file merge-index merge-tree mktag
mktree multi-pack-index mv name-rev notes pack-objects pack-redundant pack-refs patch-id prune prune-packed pull push
range-diff read-tree rebase receive-pack reflog refs remote repack replace replay request-pull rerere reset restore
rev-list rev-parse revert rm scalar send-email send-pack shortlog show show-branch show-index show-ref sparse-checkout
stage stash status stripspace submodule switch symbolic-ref tag unpack-file unpack-objects update-index update-ref
update-server-info upload-archive upload-pack var verify-commit verify-pack verify-tag version whatchanged worktree
write-tree lfs""".split())
GH_BUILTINS = frozenset("""alias api attestation auth browse cache co codespace completion config copilot extension gist
gpg-key help issue label org pr preview project release repo ruleset run search secret ssh-key status variable
workflow""".split())


def _config_sections(path, section):
    """{name in lower case: value} of one section of a git config file (`[alias]`), and its include paths."""
    out, inc, cur = {}, [], None
    try:
        lines = open(_expanduser(path), errors="replace").read(1 << 20).splitlines()
    except OSError:
        return out, inc
    for line in lines:
        t = line.strip()
        if t.startswith("["):
            cur = t.strip("[]").strip().lower()
            continue
        m = re.match(r"^([A-Za-z][\w-]*)\s*=\s*(.*)$", t)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if cur == section:
            out[m.group(1).lower()] = val
        elif cur == "include" and m.group(1).lower() == "path":
            inc.append(val)
    return out, inc


def _git_aliases(where, env, line):
    """{name: expansion} git would read for a call in WHERE with ENV: the line's own `git config alias.*` (LINE), the
    repository's config, the global and the system files, one include level each."""
    env = env or {}
    files = []
    g = _git_dir(where)
    if g:
        files.append(os.path.join(g, "config"))
    if env.get("GIT_CONFIG_GLOBAL"):
        files.append(env["GIT_CONFIG_GLOBAL"])
    else:
        xdg = env.get("XDG_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME") or "~/.config"
        files += ["~/.gitconfig", os.path.join(xdg, "git", "config")]
    if not env.get("GIT_CONFIG_NOSYSTEM"):
        files += [env.get("GIT_CONFIG_SYSTEM") or "/etc/gitconfig", "/opt/homebrew/etc/gitconfig",
                  "/usr/local/etc/gitconfig"]
    out = {}
    for f in reversed(files):  # later files win in git; read the lowest first so the repository's wins
        found, inc = _config_sections(f, "alias")
        for i in inc:
            out.update(_config_sections(os.path.join(os.path.dirname(_expanduser(f)), _expanduser(i))
                                        if not os.path.isabs(_expanduser(i)) else i, "alias")[0])
        out.update(found)
    out.update(line or {})
    return out


def git_expand(args, where, env=None, line=None, depth=0):
    """ARGS of a git call (after `git`) with an alias expanded: (args, None); (None, shell line) for a `!` alias;
    (None, None) when the subcommand is neither a built-in nor an alias the guard can read."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") else 1
    if i >= len(args) or args[i] in GIT_BUILTINS or depth > 5:
        return args, None
    sub = args[i].lower()
    conf = {k[len("alias."):]: v for k, v in _git_config_pairs(args, env or {}) if k.startswith("alias.")}
    val = conf.get(sub)
    if val is None:
        val = _git_aliases(where, env, line).get(sub)
    if val is None:
        return None, None
    if val.startswith("!"):
        return None, val[1:] + " " + " ".join(shlex.quote(a) for a in args[i + 1:])
    try:
        words = shlex.split(val)
    except ValueError:
        return None, None
    return git_expand(args[:i] + words + args[i + 1:], where, env, line, depth + 1)


def _gh_aliases(line):
    """{name: expansion} of gh's own aliases: `aliases:` in its config.yml ($GH_CONFIG_DIR, else the XDG folder or
    ~/.config/gh), and the line's `gh alias set` (LINE). Read without YAML: the hook may run without PyYAML."""
    d = os.environ.get("GH_CONFIG_DIR") or os.path.join(os.environ.get("XDG_CONFIG_HOME") or "~/.config", "gh")
    out, inside = {}, False
    try:
        lines = open(os.path.join(_expanduser(d), "config.yml"), errors="replace").read(1 << 20).splitlines()
    except OSError:
        lines = []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln[0].isspace():
            inside = ln.split("#")[0].strip() == "aliases:"
            continue
        m = re.match(r"^\s+([^:\s]+):\s*(.*)$", ln) if inside else None
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    out.update(line or {})
    return out


def gh_expand(args, line=None, depth=0):
    """ARGS of a gh call with an alias expanded, as git_expand: (args, None), (None, shell line), (None, None)."""
    k = 0
    while k < len(args) and args[k].startswith("-"):
        k += 2 if args[k] in ("-R", "--repo", "--hostname") else 1
    if k >= len(args) or args[k] in GH_BUILTINS or depth > 5:
        return args, None
    val = _gh_aliases(line).get(args[k])
    if val is None:
        return None, None
    rest = args[k + 1:]
    used = [int(n) for n in re.findall(r"\$(\d+)", val)]
    for n in sorted(set(used), reverse=True):
        val = val.replace(f"${n}", shlex.quote(rest[n - 1]) if n - 1 < len(rest) else "")
    rest = rest[max(used):] if used else rest
    if val.startswith("!"):
        return None, val[1:] + " " + " ".join(shlex.quote(a) for a in rest)
    try:
        words = shlex.split(val)
    except ValueError:
        return None, None
    return gh_expand(args[:k] + words + rest, line, depth + 1)


def _git_dir(path):
    """The git directory (the common one, for a worktree) of the repository holding PATH, or None."""
    p = os.path.abspath(_expanduser(path or "."))
    for _ in range(64):
        g = os.path.join(p, ".git")
        if os.path.isdir(g):
            return g
        if os.path.isfile(g):
            try:
                line = open(g).read().strip()
            except OSError:
                return None
            if not line.startswith("gitdir:"):
                return None
            d = line[7:].strip()
            d = d if os.path.isabs(d) else os.path.join(p, d)
            try:
                return os.path.normpath(os.path.join(d, open(os.path.join(d, "commondir")).read().strip()))
            except OSError:
                return d
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up
    return None


def gated(path):
    """Does the repository holding PATH run the release gate as its pre-commit hook?"""
    g = _git_dir(path)
    if not g:
        return False
    if g not in _GATED:
        try:
            with open(os.path.join(g, "hooks", "pre-commit"), "rb") as fh:
                _GATED[g] = GATE_MARK in fh.read(4096)
        except OSError:
            _GATED[g] = False
    return _GATED[g]


def _in_hooks(path, cwd):
    p = _expanduser(str(path).strip("'\""))
    p = p if os.path.isabs(p) else os.path.join(cwd or ".", p)
    p = os.path.normpath(p)
    return ("/.git/hooks/" in p + "/" or p.endswith("/.git/hooks")) and gated(p.split("/.git/hooks")[0] or "/")


# ------------------------------------------------- the code OF loads as plugins (G19)
# of_platform (msg 01a1cc0b, docs/plugins.md §2): installing a plugin is the owner's act, so an agent never writes to
# the code OF loads as one: each installed organic_framework.* entry point's package folder, and the shipped folder
# (bin/plugins) of an OF install. The framework's development copy (the release gate's hooks, G17) is the exception:
# there bin/plugins is source like the rest of bin/, written under the gate and review.
PLUGINS_DENY = ("the code Organic Framework loads as plugins, which only the owner installs or changes (docs/guard.md "
                "G19; docs/plugins.md §2)")
_PLUGIN_DIRS = []
# herdr's plugins run as the owner, and the OF plugin's config folder holds `of_home`, which decides the code its
# run.sh starts (of_platform's review of PR 24, msg 01a1cc8c). herdr's own plugin folders are never an agent's to
# write; the plugin's source, herdr-plugin/ beside bin/, is guarded like bin/plugins (source in the development copy).
HERDR_PLUGIN_DIRS = ("~/.config/herdr/plugins",)
HERDR_PLUGIN_ACTS = {"install", "uninstall", "update", "link", "unlink", "enable", "disable"}
HERDR_PLUGIN_MODE = "deny"  # the founder's yes linked the plugin (ask cad7e7ba); any change to it needs a new yes
HERDR_LAB = "~/.cache/of-herdr"  # of_herdr's labs: an isolated herdr, XDG_CONFIG_HOME under it (msg 01a1cca3)


# docs/guard.md G20 and G21 (2026-11-12, a user organization through its structure keeper; the founder's yes on its
# encrypted backup said the keys do not reach agents, ask f86a710c): what an organization's registry keeps from every
# agent session, with no position excepted. The hook sets both from the credentials catalogue's cache before the
# critical denies (of_credentials.cached_protection; no YAML on that path).
_ORG_PROTECTION = {"keychain": (), "code": ()}
KEYCHAIN_DENY = ("a macOS keychain item that an organization keeps from every agent session (its credentials "
                 "catalogue's keychain:; docs/guard.md G20); the program that needs it reads it itself")
PROTECTED_CODE_DENY = ("code that runs with an organization's secrets, which agents never change (its registry's "
                       "protected_code:; docs/guard.md G21)")
PROTECTED_RUN_DENY = ("code that runs with an organization's secrets, handed to a shell or sourced: run it by its own "
                      "path (its registry's protected_code:; docs/guard.md G21)")
# git subcommands that write files in their work tree whatever their options (G21: a protected worktree changes only
# by the owner's hand; 2026-11-16, a user organization's worktree at a reviewed merge)
_GIT_TREE_WRITES = {"merge", "pull", "checkout", "switch", "rebase", "cherry-pick", "revert", "am", "mv",
                    "checkout-index"}
_GIT_STASH_KEEPS = {"list", "show", "drop", "clear", "create", "store"}
_SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_RUN_SHELLS = (_SHELLS - {"eval"}) | {"fish", "tcsh", "csh", "mksh", "yash", "posh", "ash"}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
_STDIN_NAMES = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
_PARALLEL_VALUE_OPTS = {"-j", "--jobs", "-P", "--max-procs", "-S", "--sshlogin", "--sshloginfile", "--slf", "--tmpdir",
                        "--results", "--joblog", "-a", "--arg-file", "--delay", "--timeout", "--retries", "-n",
                        "--max-args", "-N", "--colsep", "-C", "-d", "--delimiter", "-E", "--eof", "--tagstring",
                        "--workdir", "--wd", "--env", "--memfree", "--load", "-l"}  # a shell handed its stdin by name reads it as its script
# git settings and variables whose value git runs as a shell line or a program (of_secrets-review on PR 249)
_GIT_RUN_KEYS = re.compile(r"^(?:core\.(?:pager|editor|fsmonitor|sshcommand|askpass)|pager\..+|diff\.external"
                           r"|sequence\.editor|gpg\.(?:[^.]+\.)?program|credential\.(?:[^.]+\.)?helper"
                           r"|(?:diff|merge|filter)\.[^.]+\.(?:command|driver|textconv|clean|smudge|process)"
                           r"|uploadpack\.packobjectshook)$")
_GIT_RUN_ENV = ("GIT_PAGER", "PAGER", "GIT_EDITOR", "EDITOR", "VISUAL", "GIT_SEQUENCE_EDITOR", "GIT_SSH_COMMAND",
                "GIT_SSH", "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS")
_PATH_TOKEN = re.compile(r"[^\s'\"`;|&()<>{}]*/[^\s'\"`;|&()<>{}]*")
_SEC_ARG_OPTS = set("acCdDGjlpPrst")  # security find-*-password options that take a value
# The caches these rules and the passkey check read: an agent that removed or rewrote one would lift them
# (of_review's probe on organic-framework PR 65: rm, an overwrite, and a forged version-2 cache that is never rebuilt).
_GUARD_STATE_FILES = re.compile(r"(?:^|/)\.local/state/of/(?:guard-credentials|passkey-revoked)\.json(?:\.\d+)?$")
GUARD_STATE_DENY = ("the guard's own cache of what organizations protect, which only OF writes (docs/guard.md G20, "
                    "G21)")


_GUARD_STATE_NAMES = ("guard-credentials.json", "passkey-revoked.json")


# G29: the program OF starts in place of Claude Code, for every session and probe with each account's sign-in, is the
# machine owner's to name: `~/.config/of/claude-bin` and the executable it names are never written by agents
# (of_server's review of organic-framework PR 270, which proposes that file)
LAUNCH_FILE = "~/.config/of/claude-bin"
LAUNCH_DENY = ("the program OF starts in place of Claude Code for every session, with each account's sign-in, which "
               "only the machine's owner names and writes (~/.config/of/claude-bin and the executable it names; "
               "docs/guard.md G29)")


# G33: OF's own configuration that decides, for every session, which registries the guard reads (orgs.yaml: their
# protected_code, tools_excluded and keychain names) and what the listener runs in the person's panes (views.yaml)
# is the owner's: agents change it through OF's own commands (of org set, of view add, of init), never by writing the
# file (docs/guard.md G33; the gap "OF's own configuration is written by any agent", 2026-11-18; of_review on
# organic-framework PR 345, msg 01a1efc7-283b, through of_herdr)
OWNER_CONFIG_FILES = ("orgs.yaml", "views.yaml")
OWNER_CONFIG_DENY = ("OF's own configuration, which only the machine's owner writes: orgs.yaml (which registries the "
                     "guard reads) and views.yaml (what runs in the person's panes). Agents change them through OF's "
                     "commands, which write the file from their own process: `of org set <org> hold on|off`, "
                     "`of view add|remove`, `of init`. A script or text that only names the file: change that file "
                     "with the Edit tool. A test's own copy: write it by its own folder's path, not through $HOME "
                     "(docs/guard.md G33)")
# A deny from 2026-11-19: the 21-day replay's live flow, agents editing orgs.yaml's hold: by sed for the founder, has
# `of org set` (2026-11-19, of_platform); its two false positives have the remedies the deny names (docs/guard.md G33)
OWNER_CONFIG_MODE = "deny"


# G35: the asks outbox is the owner's popup's (bin/of_asks_tui.py): its sender replays each file there as the owner's
# answer or mark, so a file an agent writes there would record an answer by the owner (of_platform, msg 01a1f4fc-801d,
# 2026-11-19, beside efc87597, which makes `of asks answered|withdraw` take the owner only in their terminal)
ASKS_OUTBOX_DIR = "~/.local/state/of/asks-outbox"
ASKS_OUTBOX_DENY = ("the owner's ask folders, the outbox and the drafts, which only his screens write: a draft there is "
                    "what he sends as his answer (docs/guard.md G35). An agent asks the owner with `of ask`, and closes an ask they "
                    "answered with `of status working --answered <id> --prompt <his prompt's event id>`")
ASKS_OUTBOX_MODE = "deny"


ASKS_OWNER_FOLDERS = ("asks-outbox", "asks-drafts", "asks-reply-drafts")


def _outboxes():
    """The owner's ask folders, real paths without case: the outbox and the two drafts folders (a draft the owner sends
    with D is his answer: of_review's note on organic-framework PR 398), under the default state folder and under
    OF_STATE when the hook's environment sets it (the screens read of_events.STATE_DIR)."""
    bases = [_expanduser("~/.local/state/of")]
    if os.environ.get("OF_STATE"):
        bases.append(_expanduser(os.environ["OF_STATE"]))
    return {os.path.realpath(os.path.join(b, f)).casefold() for b in bases for f in ASKS_OWNER_FOLDERS}


def _glob_match(path, pattern):
    """Does PATH match the shell glob PATTERN as the shell expands one: part by part, so `*`, `?` and `[…]` never cross a
    `/`; a part starting with `.` only by a pattern part starting with `.` (no dotglob); `**` any number of parts, as the
    Glob tool reads it. fnmatch alone lets `*` cross `/`, so `~/*` named every folder under the home (of_review on PR 395;
    the G34 replay, 118 false denies)."""
    import fnmatch
    ps, qs = path.split("/"), pattern.split("/")

    def match(i, j):
        if j == len(qs):
            return i == len(ps)
        if qs[j] == "**":
            return any(match(k, j + 1) for k in range(i, len(ps) + 1))
        if i == len(ps):
            return False
        a, b = ps[i], qs[j]
        if a.startswith(".") and not b.startswith(".") and a != b:
            return False
        return fnmatch.fnmatchcase(a, b) and match(i + 1, j + 1)
    return match(0, 0)


def _in_folders(path, boxes, cwd=None, removal=False):
    """Does PATH lie in one of BOXES (real paths, case-folded)? By real path, compared without case (the Mac's volume
    ignores it), a relative path by CWD. A glob counts when its folder is a box or lies in one, or when it names a box
    (with REMOVAL, or a recursive copy's destination, a folder above it too). G35's reading, shared with G34."""
    p = str(path or "").strip("'\"")
    if not p:
        return False
    if not p.startswith(("/", "~", "$")) and cwd:
        p = os.path.join(cwd, p)
    p = _expanduser(p)
    if any(ch in p for ch in "*?["):
        rp = _real_pattern(p).casefold().rstrip("/")
        d = os.path.dirname(rp)
        for box in boxes:
            chain = [box]
            while removal and os.path.dirname(chain[-1]) != chain[-1]:
                chain.append(os.path.dirname(chain[-1]))
            if any(_glob_match(c, rp) for c in chain):
                return True        # the pattern names the box, or a folder above it that is removed
            if _glob_match(box, d) or d == box or d.startswith(box + "/"):
                return True        # a file in it
        return False
    real = os.path.realpath(p).casefold()
    for box in boxes:
        if real == box or real.startswith(box + "/"):
            return True
        if removal and box.startswith(real.rstrip("/") + "/"):
            return True
    return False


def _in_outbox(path, cwd=None, removal=False):
    """Does PATH lie in the asks outbox (G35)? `_in_folders` over `_outboxes()`: `rm <outbox>/2*.json` removes queued
    answers (of_review on PR 395)."""
    return _in_folders(path, _outboxes(), cwd, removal)


def _forward_write(head, args, cwd, files, removal):
    """G34 for writes, as G35 reads one: a redirection, cp, mv, ln or install into the forward's folder, or a
    recursive copy or a removal over it (`echo x >> <it>/known_hosts` rewrites the host pin; of_secrets-review on
    organic-framework PR 397). The words G34 reads do not hold a redirection's target."""
    if _REMOTE_BODY[0]:
        return None
    box = {_forward_dir().casefold()}
    hit = next((t for t in _tree_targets(head, args, cwd) if _in_folders(t, box, cwd, removal=True)), None) or \
        next((f for f in files if _in_folders(f, box, cwd, removal)), None)
    return (FORWARD_DENY, hit) if hit else None


# G36: the guard's session index is the collector's (hand-over a18341af, of_server; of_secrets-review, msg
# 01a1f9a7-b29c): _guard_index reads ~/.local/state/of/index.json for a session's position, and with it that
# position's credential rules, so a session that rewrites its entry takes another position's rules. Only the hook
# writes it, through a temporary sibling (bin/of_hook.py _registry_index: index.json.<pid>.<ns>.tmp), outside the guard.
SESSION_INDEX = "index.json"
_SESSION_INDEX_TMP = re.compile(r"/index\.json\.\d+\.\d+\.tmp$")
SESSION_INDEX_DENY = ("the guard's session index, which only OF's collector writes: it maps each session to its "
                      "position, and with it the position's credential rules (docs/guard.md G36). Read it as you like; "
                      "the hook rebuilds it from the registries on its own")
SESSION_INDEX_MODE = "deny"


def _session_indexes():
    """The index's real path, case-folded, under the default state folder and under OF_STATE when the hook's
    environment sets it."""
    bases = [_expanduser("~/.local/state/of")]
    if os.environ.get("OF_STATE"):
        bases.append(_expanduser(os.environ["OF_STATE"]))
    return {os.path.realpath(os.path.join(b, SESSION_INDEX)).casefold() for b in bases}


def _in_session_index(path, cwd=None, removal=False):
    """Does PATH name the session index or one of its temporary siblings (G36)? As G35 reads a path: by real path
    without case, a glob as the shell expands it, a relative path by CWD, and with REMOVAL a folder above it."""
    boxes = _session_indexes()
    if _in_folders(path, boxes, cwd, removal):
        return True
    p = str(path or "").strip("'\"")
    if not p:
        return False
    if not p.startswith(("/", "~", "$")) and cwd:
        p = os.path.join(cwd, p)
    p = _expanduser(p)
    folders = {os.path.dirname(b) for b in boxes}
    if any(ch in p for ch in "*?["):
        rp = _real_pattern(p).casefold()
        return os.path.dirname(rp) in folders and _glob_match(SESSION_INDEX + ".1.1.tmp", os.path.basename(rp))
    real = os.path.realpath(p).casefold()
    return os.path.dirname(real) in folders and bool(_SESSION_INDEX_TMP.search(real))


def _session_index_write(head, args, cwd, files, removal):
    """G36 on one simple command: a write, removal, move or link over the session index or a temporary sibling, or a
    recursive copy or an extraction into its folder."""
    if _REMOTE_BODY[0]:
        return None
    hit = next((t for t in _tree_targets(head, args, cwd) if _in_session_index(t, cwd, removal=True)), None) or \
        next((f for f in files if _in_session_index(f, cwd, removal)), None)
    if not hit:
        return None
    if SESSION_INDEX_MODE == "deny":
        return SESSION_INDEX_DENY, hit
    _WARNINGS.append((SESSION_INDEX_DENY, hit, "a warning now, a deny once the replay shows no agent writes it"))
    return None


def _asks_outbox_write(head, args, cwd, files, removal):
    """G35 on one simple command: a write, removal, move or link into the asks outbox, or a recursive copy or an
    extraction into a folder that holds it."""
    if _REMOTE_BODY[0]:
        return None
    hit = next((t for t in _tree_targets(head, args, cwd) if _in_outbox(t, cwd, removal=True)), None) or \
        next((f for f in files if _in_outbox(f, cwd, removal)), None)
    if not hit:
        return None
    if ASKS_OUTBOX_MODE == "deny":
        return ASKS_OUTBOX_DENY, hit
    _WARNINGS.append((ASKS_OUTBOX_DENY, hit, "a warning now, a deny once the flows that write it have moved"))
    return None


def _owner_config_targets():
    d = os.environ.get("OF_CONFIG_DIR") or "~/.config/of"
    return [os.path.realpath(_expanduser(os.path.join(d, f))) for f in OWNER_CONFIG_FILES]


def _launch_targets():
    """The real paths of LAUNCH_FILE and of the executable its first line names, when it names one."""
    f = os.path.realpath(_expanduser(LAUNCH_FILE))
    out = [f]
    try:
        with open(f, errors="replace") as fh:
            named = fh.readline(4096).strip()
    except OSError:
        return out
    if named:
        out.append(os.path.realpath(_expanduser(named)))
    return out


def _launch_file(path, cwd=None, targets=None):
    """Does PATH name LAUNCH_FILE or the executable it names? By pattern for a glob; else by device and inode when it
    exists (a hard link made earlier is the same file), and by real path compared without case (the Mac's volume
    ignores case: `~/.CONFIG/of/claude-bin` is the file; of_secrets-review on organic-framework PR 273)."""
    p = str(path or "").strip("'\"")
    if not p:
        return False
    if not p.startswith(("/", "~", "$")) and cwd:
        p = os.path.join(cwd, p)
    p = _expanduser(p)
    targets = _launch_targets() if targets is None else targets
    if any(ch in p for ch in "*?["):
        import fnmatch
        rp = _real_pattern(p)
        return any(fnmatch.fnmatchcase(t.casefold(), rp.casefold()) for t in targets)
    try:
        st = os.stat(p)
        for t in targets:
            try:
                ts = os.stat(t)
            except OSError:
                continue
            if (st.st_dev, st.st_ino) == (ts.st_dev, ts.st_ino):
                return True
    except OSError:
        pass
    return os.path.realpath(p).casefold() in {t.casefold() for t in targets}


# > 0 while _bash_deny reads a command another machine runs: there `~` is that machine's home (G29 reads only this one's)
_REMOTE_BODY = [0]
# does the Bash line critical_deny reads run a remote command anywhere (ssh, a container's or a VM's exec)? Then no file
# on it is judged by this machine's content (G1.8.1): `ssh h python3 - <<'PY' … open('<names list>') …` reads the other
# machine's (of_review on organic-framework PR 332)
_LINE_REMOTE = [False]


def _remote_line(cmd):
    """Does the line run a remote command: by its text, and by its words once the shell's quotes are gone
    (`s's'h host cat <list>` is ssh: of_review on organic-framework PR 332), each name folded as G0.10 folds it."""
    if _REMOTE_LINE.search(cmd):
        return True
    try:  # split at the shell's operators too: `true;s's'h host …` is two commands (of_review on PR 332)
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split, lex.commenters = True, ""
        words = [p for w in lex for p in re.split(r"[`{}]", w) if p]
    except ValueError:
        return True  # a line the shell's rules cannot split: not read as a local one
    return any(_REMOTE_LINE.fullmatch(_fold_name(os.path.basename(w))) for w in words)
_REMOTE_LINE = re.compile(r"(?<![\w-])(?:ssh|autossh|docker|podman|nerdctl|kubectl|crictl|limactl|lima|multipass"
                          r"|vagrant|orbctl|orb)(?![\w-])", re.I)
_LOOPS = [{}]  # the `for` lists of the command critical_deny checks (G0.9)
_CAPPED = [None]  # the first reader that stopped at its depth cap in the command critical_deny checks (G0.11)


def _capped(reader):
    """Mark the call as read only to a limit by READER (G0.11), and answer "nothing found" as the reader would."""
    _CAPPED[0] = _CAPPED[0] or reader
    return None

# G0.11: a command nested deeper than a reader reads (its depth cap) was allowed unread: `_bash_deny` returned None past
# depth 4, so any denied command passed five levels down (of_review, msg 01a1f412-7113, and of_secrets-review, msg
# 01a1f440-6796, 2026-11-19; organic-framework PR 332). A reader that stops at its cap now marks the call, and
# critical_deny refuses it. The readers read twelve levels down (four until then: the 21-day replay found 149 commands,
# polling loops and kubectl pipelines, nested five to twelve levels), and `_bash_deny` reads at most READ_BUDGET bodies
# per call, so a command built to outlast the hook's limit (30 s for PreToolUse, DECIDING_TIMEOUT in bin/of_init.py), whose kill would lose the deny, is refused.
DEPTH_CAP = 12
READ_BUDGET = 400
_READS = [0]
DEPTH_CAP_MODE = "deny"
DEPTH_CAP_DENY = ("a command nested deeper than the guard reads (more than twelve levels of subshells, substitutions or "
                  "shell bodies inside one another, or more nested bodies than it reads in one call), so it cannot tell "
                  "what runs. Write it with fewer levels (docs/guard.md G0.11)")
_REDIR_CTX = [None]  # (folder, line variables) of the simple command _bash_deny reads, for its `<` targets (G0.9)


def _launch_write(head, args, cwd, files, removal, targets=None):
    """G29 as G21 reads a write, with the two targets as its protected entries: a folder above them removed, moved
    or replaced by a link (`ln -f`, `-n`), and a recursive copy or an extraction into a folder that holds one, count
    too (of_secrets-review and of_review on organic-framework PR 273). The path it hit, else None. A command another
    machine runs (ssh, a container's or a VM's exec: a lab VM resets its own `~/.config/of`) is not this Mac's."""
    if _REMOTE_BODY[0]:
        return None
    replacing = removal or (head == "ln" and any(
        a == "--force" or a == "--no-dereference" or (a.startswith("-") and not a.startswith("--") and
                                                       ("f" in a[1:] or "n" in a[1:])) for a in args))
    targets = _launch_targets() if targets is None else list(targets)
    saved = _ORG_PROTECTION["code"]
    _ORG_PROTECTION["code"] = tuple(targets)
    try:
        return next((t for t in _tree_targets(head, args, cwd) if _protected_code(t, cwd, removal=True)), None) or \
            next((f for f in files if _launch_file(f, cwd, targets) or _protected_code(f, cwd, replacing)), None)
    finally:
        _ORG_PROTECTION["code"] = saved


def _guard_state_file(path, cwd=None):
    """Does PATH name one of the guard's own cache files? By its real path, so a link to the state folder counts, and
    by pattern, so a glob the shell would expand to one counts (of_secrets-review on organic-framework PR 65)."""
    p = str(path or "").strip("'\"")
    if not p:
        return False
    if not p.startswith(("/", "~", "$")) and cwd:
        p = os.path.join(cwd, p)
    p = _expanduser(p)
    if _GUARD_STATE_FILES.search(os.path.normpath(p)):
        return True
    state = os.path.realpath(_expanduser("~/.local/state/of"))
    targets = [os.path.join(state, n) for n in _GUARD_STATE_NAMES]
    if any(ch in p for ch in "*?["):
        import fnmatch
        rp = _real_pattern(p)
        return any(fnmatch.fnmatchcase(t, rp) or fnmatch.fnmatchcase(t + ".0", rp) for t in targets)
    real = os.path.realpath(p)
    return real in targets or bool(re.match(re.escape(state) + r"/(?:guard-credentials|passkey-revoked)\.json\.\d+$",
                                            real))


def use_protection(keychain=(), code=()):
    """Set the keychain services (exact, or a prefix with a trailing *) and the protected code paths or globs."""
    _ORG_PROTECTION.update(keychain=tuple(sorted({str(k).lower() for k in keychain or () if str(k).strip()})),
                           code=tuple(sorted({str(c) for c in code or () if str(c).strip()})))


def _keychain_name(text):
    """The protected keychain service TEXT names (equal to it, or holds a protected prefix), else None."""
    t = str(text).lower()
    for k in _ORG_PROTECTION["keychain"]:
        if (k.endswith("*") and k[:-1] in t) or (not k.endswith("*") and (t == k or k in t)):
            return k
    return None


def _security_opts(args):
    """(flags, values) of a security find-*-password call: the short flags given, and every option value or operand,
    read as getopt reads them (-gs svc, -s svc -w, -ssvc)."""
    flags, values, k = set(), [], 0
    while k < len(args):
        a = args[k]
        if a == "--":
            values += args[k + 1:]
            break
        if a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            for j, ch in enumerate(a[1:], 1):
                if ch in _SEC_ARG_OPTS:
                    if a[j + 1:]:
                        values.append(a[j + 1:])
                    else:
                        values.append(args[k + 1] if k + 1 < len(args) else "")
                        k += 1
                    break
                flags.add(ch)
        else:
            values.append(a)
        k += 1
    return flags, values


def _security_deny(args):
    """G11 and G20 on `security ARGS`: a password printed (find-*-password -w or -g), every item exported, a dump with
    values (dump-keychain -d), interactive mode (its commands come from stdin, unseen), or any subcommand that names a
    keychain item an organization protects. security's own options before the subcommand (-h -i -l -p prompt -q -v)
    are skipped (of_secrets-review's review of organic-framework PR 65: `security -q find-generic-password -gw`)."""
    k = 0
    while k < len(args) and args[k].startswith("-") and args[k] != "--":
        if any(c in "ip" for c in args[k][1:]):  # -i, -p prompt: commands read from stdin, which the guard never sees
            return _prints("security -i (interactive mode)")
        k += 1
    args = args[k + 1:] if k < len(args) and args[k] == "--" else args[k:]
    sub = args[0] if args else ""
    for a in args[1:]:
        name = _keychain_name(a) or (_keychain_name(a[2:]) if a.startswith("-") and len(a) > 2 else None)
        if name:
            return KEYCHAIN_DENY, f"security {sub} {name}"
    if re.match(r"find-(generic|internet)-password$", sub):
        flags, _ = _security_opts(args[1:])
        for f in ("w", "g"):  # -g prints the password too, on stderr, which reaches the agent like stdout
            if f in flags:
                return _prints(f"security {sub} -{f}")
    if sub == "export":  # writes the keychain's items, private keys included, to a file or to stdout
        return _prints("security export")
    if sub == "dump-keychain" and any(a.startswith("-") and not a.startswith("--") and "d" in a[1:] for a in args[1:]):
        return _prints("security dump-keychain -d")  # -d prints every item's value
    return None


def _real_pattern(pattern):
    """PATTERN with the real path of its literal folder: /tmp and /var are links on macOS."""
    p = _expanduser(str(pattern))
    head = re.split(r"[*?[]", p, maxsplit=1)[0]
    stem = head if head.endswith("/") or head == p else os.path.dirname(head)
    return os.path.realpath(stem.rstrip("/") or "/") + p[len(stem.rstrip("/")):] if stem else p


def _protected_code(path, cwd, removal=False):
    """The protected_code entry PATH lies in (the entry itself, under it, or a match of its glob), else None. With
    REMOVAL (rm, rmdir, mv), a folder that holds an entry counts too: removing it removes the entry."""
    if not _ORG_PROTECTION["code"] or not path:
        return None
    import fnmatch
    p = _expanduser(str(path).strip("'\""))
    p = os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd or ".", p))
    chain = [p]
    while os.path.dirname(chain[-1]) != chain[-1]:
        chain.append(os.path.dirname(chain[-1]))
    for pat in _ORG_PROTECTION["code"]:
        rp = _real_pattern(pat).rstrip("/")
        if any(c == rp or fnmatch.fnmatchcase(c, rp) for c in chain):
            return pat
        stem = re.split(r"[*?[]", rp, maxsplit=1)[0]
        if removal and (stem.startswith(p.rstrip("/") + "/") or (stem == p and stem != rp)):
            return pat
    return None


def _tree_targets(head, args, cwd):
    """The folders one simple command may write anywhere under: a recursive cp's destination, rsync's and ditto's,
    and the folder tar or unzip extracts into (cwd when none is named)."""
    ops = [a for a in args if not a.startswith("-")]
    short = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    if head == "cp" and (set(short) & set("Rra") or "--recursive" in args or "--archive" in args):
        return ops[-1:]
    if head in ("rsync", "ditto"):
        return ops[-1:]
    if head == "git":
        wt = next((args[k + 1] for k, a in enumerate(args) if a == "--work-tree" and k + 1 < len(args)), None) or \
            next((a.split("=", 1)[1] for a in args if a.startswith("--work-tree=")), None)
        if wt and set(ops) & {"checkout", "restore", "reset", "stash", "merge", "pull", "switch", "clean", "rm", "mv",
                              "apply", "am", "rebase", "cherry-pick", "revert", "read-tree", "checkout-index"}:
            return [wt]   # `git --work-tree=<dir> checkout …` writes there (of_review on PR 395)
    if head in ("tar", "bsdtar", "gtar"):
        first = args[0] if args else ""
        extracting = "x" in short or "--extract" in args or "--get" in args or \
            (first and not first.startswith("-") and "x" in first)  # tar xf a.tar
        if not extracting:
            return []
        for i, a in enumerate(args):
            if a in ("-C", "--directory") and i + 1 < len(args):
                return [args[i + 1]]
            if a.startswith("--directory="):
                return [a.split("=", 1)[1]]
        return [cwd or "."]
    if head == "unzip":
        if set(short) & set("ltvZpz"):  # list, test, verbose, zipinfo, to stdout, comment: nothing is written
            return []
        for i, a in enumerate(args):
            if a == "-d" and i + 1 < len(args):
                return [args[i + 1]]
            if a.startswith("-d") and len(a) > 2:
                return [a[2:]]
        return [cwd or "."]
    return []


def _long_hit(rest, names):
    """Does a long option in REST name one of NAMES, whole or as the unique prefix git accepts (`--har` is
    `--hard`; of_secrets-review on PR 249)?"""
    for a in rest:
        if a == "--":
            return False
        if a.startswith("--") and len(a) > 2:
            name = a[2:].split("=", 1)[0]
            if any(n.startswith(name) for n in names):
                return True
    return False


def _git_writes_tree(sub, rest):
    """Does git subcommand SUB, with the words REST after it, write files in its work tree?"""
    shorts = set("".join(a[1:] for a in rest if a.startswith("-") and not a.startswith("--")))
    pos = [a for a in rest if not a.startswith("-")]
    if sub in ("checkout", "switch") and len(pos) <= 1 and not ({"f", "m"} & shorts) and \
            not _long_hit(rest, ("force", "discard-changes", "merge")) and \
            any(a in ("-b", "-B", "-c", "-C", "--orphan") or _long_hit([a], ("create", "force-create")) for a in rest):
        return False  # a new branch at HEAD, with no force or merge, changes no file (of_review: `checkout -f -b x`)
    if sub in _GIT_TREE_WRITES:
        return True
    if sub == "reset":
        return _long_hit(rest, ("hard", "merge", "keep"))
    if sub == "restore":
        return "W" in shorts or _long_hit(rest, ("worktree",)) or not ("S" in shorts or _long_hit(rest, ("staged",)))
    if sub == "apply":  # --apply turns applying back on after --stat or --check (of_review on PR 249)
        return _long_hit(rest, ("apply",)) or not _long_hit(rest, ("cached", "check", "stat", "numstat", "summary"))
    if sub == "rm":
        return not _long_hit(rest, ("cached",))
    if sub == "clean":
        return not ("n" in shorts or _long_hit(rest, ("dry-run",)))
    if sub == "stash":
        return not (pos and pos[0] in _GIT_STASH_KEEPS)
    if sub == "read-tree":
        return "u" in shorts
    if sub == "sparse-checkout":
        return not (pos and pos[0] in ("list", "check-rules"))
    if sub == "submodule":
        return bool(pos) and pos[0] in ("update", "deinit", "absorbgitdirs")
    if sub == "bisect":
        return not (pos and pos[0] in ("log", "view", "visualize", "help", "terms"))
    return False


def _git_outputs(sub, rest):
    """Files and folders a git call writes by name, wherever they are (of_secrets-review on PR 249): `--output` (log,
    diff, show and the rest), format-patch's `-o` and `--output-directory`, archive's `-o`, checkout-index's `--prefix`,
    merge-file's first file, bundle create's file, clone's and init's folders and `--separate-git-dir`."""
    out, pos, i = [], [], 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            pos += rest[i + 1:]
            break
        name, val = (a[2:].split("=", 1) + [None])[:2] if a.startswith("--") else (None, None)
        wants = ("output", "output-directory") + (("prefix",) if sub == "checkout-index" else ()) + \
            (("separate-git-dir",) if sub in ("clone", "init") else ()) + (("file",) if sub == "config" else ()) + \
            (("export-marks",) if sub in ("fast-export", "fast-import") else ())
        if name and any(n.startswith(name) for n in wants):
            if val is None and i + 1 < len(rest):
                val, i = rest[i + 1], i + 1
            out.append(val)
        elif sub == "config" and a == "-f" and i + 1 < len(rest):
            out.append(rest[i + 1])
            i += 1
        elif sub in ("format-patch", "archive") and a.startswith("-o") and not a.startswith("--"):
            if len(a) > 2:
                out.append(a[2:])
            elif i + 1 < len(rest):
                out.append(rest[i + 1])
                i += 1
        elif sub == "merge-file" and a == "-L" and i + 1 < len(rest):
            i += 1
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    if sub == "merge-file" and pos and not ("-p" in rest or _long_hit(rest, ("stdout",))):
        out.append(pos[0])
    elif sub == "bundle" and pos[:1] == ["create"] and len(pos) > 1:
        out.append(pos[1])
    elif sub == "submodule" and pos[:1] == ["add"] and len(pos) > 2:
        out.append(pos[2])
    elif sub in ("clone", "init"):
        out += pos
    return [o for o in out if o]

def _git_top(path):
    """The top of the work tree holding PATH: the nearest folder at or above it with a .git, else None (no repository
    there for git to write, or a folder the guard could not resolve, such as `cd $(dirname $f)/..`)."""
    q = os.path.abspath(_expanduser(path or "."))
    for _ in range(64):
        if os.path.exists(os.path.join(q, ".git")):
            return q
        up = os.path.dirname(q)
        if up == q:
            break
        q = up
    return None


def _tree_entry(top):
    """The protected_code entry the work tree at TOP lies in or holds, else None (of_review on PR 249: an entry that is
    a file, a glob or a folder below the top). An entry in a nested repository or worktree below TOP, one with its own
    .git, is that one's: git in TOP never writes it."""
    hit = _protected_code(top, None)
    if hit:
        return hit
    t = os.path.realpath(top).rstrip("/")
    for pat in _ORG_PROTECTION["code"]:
        stem = re.split(r"[*?[]", _real_pattern(pat), maxsplit=1)[0].rstrip("/")
        if not stem.startswith(t + "/"):
            continue
        q = stem if os.path.isdir(stem) else os.path.dirname(stem)
        while q.startswith(t + "/") and not os.path.exists(os.path.join(q, ".git")):
            q = os.path.dirname(q)
        if q == t:
            return pat
    return None


def _git_paths(sub, rest):
    """The paths a pathspec-limited write names (checkout, restore, rm and mv given paths), or None when it may write
    the whole tree: checkout with no `--`, a glob or magic pathspec, a pathspec file."""
    if sub not in ("checkout", "restore", "rm", "mv") or _long_hit(rest, ("pathspec-from-file",)):
        return None
    if "--" in rest:
        paths = rest[rest.index("--") + 1:]
    elif sub == "checkout":
        return None
    else:
        paths, i = [], 0
        while i < len(rest):
            if rest[i] in ("-s", "--source"):
                i += 1
            elif not rest[i].startswith("-"):
                paths.append(rest[i])
            i += 1
    if not paths or any(re.search(r"[*?[]", x) or x.startswith(":") for x in paths):
        return None
    return paths


def _git_lines(sub, rest):
    """Shell lines a git subcommand runs itself: submodule foreach, rebase --exec, bisect run."""
    pos = [a for a in rest if not a.startswith("-")]
    if sub == "submodule" and pos[:1] == ["foreach"]:
        return [" ".join(rest[rest.index("foreach") + 1:]).replace("--recursive ", "", 1)]
    if sub == "bisect" and pos[:1] == ["run"]:
        import shlex
        return [shlex.join(rest[rest.index("run") + 1:])]
    if sub == "rebase":
        out = []
        for k, a in enumerate(rest):
            if a in ("-x", "--exec") and k + 1 < len(rest):
                out.append(rest[k + 1])
            elif a.startswith("--exec="):
                out.append(a.split("=", 1)[1])
        return out
    return []


def _git_protected(args, cwd, env, shvars=None):
    """The protected_code entry or file a git call writes (G21), else None: its work tree (--work-tree,
    GIT_WORK_TREE, core.worktree, else the tree holding the folder it runs in after each -C) for a subcommand that
    writes it, when that tree lies in an entry or holds one, or the paths it names lie in one; a file it names as its
    output; a worktree it adds, moves or removes; `clean -ff`, which removes the nested worktrees below it too; and a
    line git runs (an alias, a pager, a driver, foreach, --exec) that writes or runs protected code."""
    import shlex
    args = [_expand_vars(a, shvars) for a in args]
    where, tree = cwd or ".", None
    dirs, _, _ = _git_targets(args)
    for opt, w in dirs:
        w = _expanduser(w.strip("'\""))
        if opt == "-C":
            where = os.path.join(where, w)
        elif opt == "--work-tree":
            tree = os.path.join(where, w)
    pairs = _git_config_pairs(args, env)
    if tree is None:
        tree = next((os.path.join(where, _expanduser(v)) for k, v in pairs if k == "core.worktree" and v), None)
    if tree is None and env.get("GIT_WORK_TREE"):
        tree = os.path.join(where, _expanduser(env["GIT_WORK_TREE"]))
    if tree is None and (any(opt == "--git-dir" for opt, _ in dirs) or env.get("GIT_DIR")):
        tree = where  # an explicit git dir with no work tree makes the folder git runs in the tree (of_secrets-review)
    top = tree or _git_top(where) or where
    runs = [(where, v[1:] if v.startswith("!") else v) for k, v in pairs if _GIT_RUN_KEYS.match(k)]
    runs += [(where, str(env[k])) for k in _GIT_RUN_ENV if env.get(k)]
    expanded, shell = git_expand(args, where, env, env.get("__git_aliases"))
    if shell is not None:  # a `!` alias runs its line at the work tree's top
        runs.append((top, shell))
    sub, rest = None, []
    if expanded is not None:
        _, sub, rest = _git_targets(expanded)
        sub = (sub or "").lower()
        runs += [(top, line) for line in _git_lines(sub, rest)]
    for at, line in runs:  # git runs these as shell lines or programs: what they do to protected code is G21's
        hit = _bash_deny(f"cd {shlex.quote(at)} && {line}", 1)
        if hit and hit[0] in (PROTECTED_CODE_DENY, PROTECTED_RUN_DENY):
            return hit[1]
        if hit and hit[0] == WRAPPER_BOUND_DENY:  # a line read only to a limit: never dropped (a depth cap marks itself)
            _capped("_git_protected")  # (G0.11; of_review on organic-framework PR 389)
    if expanded is None:
        return None
    hit = next((o for o in _git_outputs(sub, rest) if _protected_code(o, where)), None)
    if hit:
        return hit
    if sub in ("clone", "init") and _protected_code(where, cwd):
        return where
    if sub == "worktree":
        if rest[:1] and rest[0] in ("add", "move", "remove"):
            return next((p for p in rest[1:] if not p.startswith("-") and _protected_code(p, where, removal=True)),
                        None)
        return None
    if not _git_writes_tree(sub, rest):
        return None
    if sub == "clean" and "".join(a[1:] for a in rest if a.startswith("-") and not a.startswith("--")).count("f") + \
            sum(1 for a in rest if a.startswith("--f") and "force".startswith(a[2:])) >= 2:
        return where if _protected_code(where, cwd, removal=True) else None
    paths = _git_paths(sub, rest)
    if paths is not None:
        return next((x for x in paths if _protected_code(x, where, removal=True)), None)
    if not tree and _git_top(where) is None:  # no repository found: only a folder inside an entry counts
        return where if _protected_code(where, cwd) else None
    return top if _tree_entry(top) else None

def _stdin_redirect(stext, fd=0):
    """The file descriptor FD of a simple command is redirected from (`< file` and `0< file` for 0, `3< file` for 3),
    else None (G21; the older _stdin_file serves the other rules)."""
    try:
        toks = _tokens(stext or "")
    except ValueError:
        return None
    for k, t in enumerate(toks[:-1]):
        if t == "<" and ((k > 0 and toks[k - 1] == str(fd)) or (fd == 0 and (k == 0 or not toks[k - 1].isdigit()))):
            return toks[k + 1]
    return None

def _expand_vars(word, shvars):
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: str(shvars.get(m.group(1), m.group(0))), word) if shvars \
        else word


def _shell_runs_protected(head, args, cwd, stext=None, shvars=None, fed=None, depth=0):
    """A file under a protected_code entry that a shell runs (G21), else None: handed as its script or its $0, run by
    its -c code, on its stdin (`< file`, or FED: a protected file an earlier stage of its pipe names, `cat file |
    bash`, `echo file | xargs bash`), or read by `source` or `.`. A shell started that way runs its caller's code first
    (BASH_ENV, defined functions, `set -p`) in the process that then holds the organization's secret; run by its own
    path, the script's interpreter line decides (2026-11-16, a user organization's script with `#!/bin/bash -p`).
    A shell that only reads the file (`bash -c 'cat file'`, `bash lint.sh file`) is G21's read, allowed."""
    args = [_expand_vars(a, shvars) for a in args]
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_shell_runs_protected"
        return None
    if head == "parallel":  # `parallel bash ::: f`, `parallel -j 4 'bash {}' ::: f`, `echo f | parallel bash`
        cut = next((k for k, a in enumerate(args) if a in (":::", "::::", ":::+")), len(args))
        k = 0
        while k < cut and args[k].startswith("-"):
            k += 2 if args[k] in _PARALLEL_VALUE_OPTS else 1
        if k >= cut:
            return None
        words = args[k:cut]
        inputs = args[cut + 1:] if cut < len(args) else []
        for x in inputs or [None]:
            line = " ".join(w.replace("{}", x) if x and "{}" in w else w for w in words)
            if x and "{}" not in line:
                line += " " + x
            hit = _code_runs_protected(line, cwd, shvars, depth + 1)
            if hit:
                return hit
        first = words[0].split()[0] if words[0].split() else ""
        if not inputs and os.path.basename(first) in _RUN_SHELLS | {"source", "."} and "-c" not in " ".join(words):
            return fed() if callable(fed) else fed  # its inputs come on stdin
        return None
    if head == "eval":
        return _code_runs_protected(" ".join(args), cwd, shvars, depth + 1)
    if head == "flock" and any(a in ("-c", "--command") for a in args[:-1]):  # `flock /tmp/l -c 'bash f'`
        k = next(k for k, a in enumerate(args) if a in ("-c", "--command"))
        return _code_runs_protected(args[k + 1], cwd, shvars, depth + 1)
    if head == "flock" and any(a.startswith("--command=") for a in args):
        return _code_runs_protected(next(a for a in args if a.startswith("--command="))[10:], cwd, shvars, depth + 1)
    if head == "watch" and args and not any(a in ("-x", "--exec") for a in args):  # watch runs its words with sh -c
        k = 0
        while k < len(args) and args[k].startswith("-"):
            k += 2 if args[k] in ("-n", "--interval", "-d") else 1
        return _code_runs_protected(" ".join(args[k:]), cwd, shvars, depth + 1) if k < len(args) else None
    if head in ("busybox", "watch", "flock") and args:  # `busybox sh f`, `watch -x bash f`, `flock /tmp/l bash f`
        k = 0
        while k < len(args) and args[k].startswith("-"):
            k += 2 if args[k] in {"watch": ("-n", "--interval", "-d"),
                                  "flock": ("-w", "--wait", "--timeout", "-E", "--conflict-exit-code")}.get(head, ()) \
                else 1
        k += head == "flock"  # the lock file or descriptor
        return _shell_runs_protected(os.path.basename(args[k]), args[k + 1:], cwd, None, shvars, fed, depth + 1) \
            if k < len(args) else None
    if head in ("source", "."):
        if args[:1] and args[0] in _STDIN_NAMES:
            return _stdin_runs_protected(stext, cwd, fed)
        return next((p for p in args[:1] if _protected_code(p, cwd)), None)
    if head not in _RUN_SHELLS:
        return None
    code, stdin, i = False, False, 0
    while i < len(args) and args[i][:1] in "-+" and args[i] != "--":
        short = args[i][:1] == "-" and not args[i].startswith("--")
        code = code or (short and "c" in args[i])
        stdin = stdin or (short and "s" in args[i])
        i += 2 if args[i] in _SHELL_VALUE_OPTS else 1
    rest = args[i + (args[i:i + 1] == ["--"]):]
    if code:
        if len(rest) > 1 and _protected_code(rest[1], cwd):  # its $0
            return rest[1]
        return _code_runs_protected(rest[0], cwd, shvars, depth + 1) if rest else None
    m = re.fullmatch(r"/(?:dev|proc/self)/fd/([1-9]\d*)", rest[0]) if rest and not stdin else None
    if m:  # `bash /dev/fd/3 3< f`
        f = _stdin_redirect(stext, int(m.group(1)))
        return f if f and _protected_code(f, cwd) else None
    if rest and not stdin and rest[0] not in _STDIN_NAMES and rest[0] != "{}":
        return rest[0] if _protected_code(rest[0], cwd) else None
    return _stdin_runs_protected(stext, cwd, fed)  # its script comes on stdin (or, after xargs, `{}` from it)


def _fed(prior):
    """What earlier pipe stages PRIOR hand on, read when called: the first protected file one names, else None."""
    return (lambda: next(filter(None, (_names_protected(*x) for x in prior)), None)) if prior else None


def _stdin_runs_protected(stext, cwd, fed):
    """The protected file a command's stdin carries: `< file`, a here-string that is the file or whose substitution
    reads it (`<<< "$(cat file)"`), or FED from an earlier stage of its pipe; else None."""
    f = _stdin_redirect(stext)
    if f and _protected_code(f, cwd):
        return f
    try:
        here = _simple_g21(stext)[4] if stext else None
    except (TypeError, IndexError, ValueError):
        here = None
    if here:
        if _protected_code(here.strip(), cwd):
            return here.strip()
        for m in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", here):
            hit = _text_names_protected(m.group(1) or m.group(2) or "", cwd)
            if hit:
                return hit
    return fed() if callable(fed) else fed


_HEREDOC_OPEN = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")


def _cut_heredocs(text):
    """TEXT without its here-document bodies, for G21's read of a raw line: a body is data for the command it goes to
    (a shell's own body is read apart), and its lines are no commands (`won't` in a commit message)."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        out.append(lines[i])
        ends = [(m.group(1), m.group(3)) for m in _HEREDOC_OPEN.finditer(lines[i])]
        i += 1
        for dash, d in ends:
            while i < len(lines) and (lines[i].lstrip("\t") if dash else lines[i]) != d:
                i += 1
            i += 1
    return "\n".join(out)


def _simple_g21(stext):
    """_simple, or None when a stage cut from a raw line cannot be read (an open quote): G21 then skips that stage
    rather than fail the whole call over to the strict text match (the 21-day replay of PR 249's fix)."""
    try:
        return _simple(stext)
    except ValueError:
        return None


def _text_names_protected(text, cwd):
    """A file under a protected_code entry that some command in shell TEXT names or reads, else None."""
    try:
        pipes = _split(text)
    except ValueError:
        return None
    for pipe in pipes:
        for kind, st in pipe:
            s = _simple_g21(st) if kind == "cmd" else None
            if kind == "cmd" and not s:  # `$(< file)`: a redirection alone reads the file
                f = _stdin_redirect(st)
                if f and _protected_code(f, cwd):
                    return f
            hit = (s[1] if _protected_code(s[1], cwd) else None) if s and s[0] == "<read" else \
                _names_protected(s[0], s[1], st, cwd) if s else _text_names_protected(st, cwd) if kind == "sub" else None
            if hit:
                return hit
    return None


def _code_runs_protected(code, cwd, shvars=None, depth=0):
    """A protected file that shell CODE runs (G21): sourced, handed to a shell, piped or redirected into one, given to
    one as `<(…)`, or read by a substitution that is the whole command (`bash -c "$(cat file)"`), else None."""
    try:
        pipes = _split(code)
    except ValueError:
        return None
    here, shvars = cwd, dict(shvars or {})
    for pipe in pipes:
        prior, prev = [], None
        for kind, st in pipe:
            if kind == "sub":
                m = re.fullmatch(r"\s*([A-Za-z_]\w*)=[\"']?\$", prev[1]) if prev else None
                if m:  # `T=$(cat f)`: T holds the text a later `bash -c "$T"` or `eval "$T"` runs
                    shvars[m.group(1)] = "$(" + st + ")"
                if prev and (prev[1].strip() == "$" or (prev[1].rstrip().endswith("<") and prev[0] in
                                                        _RUN_SHELLS | {"source", "."})):
                    hit = _text_names_protected(st, here)
                    if hit:
                        return hit
                hit = _code_runs_protected(st, here, shvars, depth + 1) if depth < DEPTH_CAP else _capped(
                    "_code_runs_protected")
                if hit:
                    return hit
                prev = None
                continue
            m = re.match(r"^\s*`([^`]*)`", st)  # a backtick substitution as the command: its output is the code
            if m:
                hit = _text_names_protected(m.group(1), here)
                if hit:
                    return hit
            m = re.fullmatch(r"\s*([A-Za-z_]\w*)=([\"'])(.*)\2\s*", st, re.S)
            if m and ("$(" in m.group(3) or "`" in m.group(3)):  # `T="$(cat f)"`, kept whole by the split
                shvars[m.group(1)] = m.group(3)
            s1 = _simple_g21(st)
            if not s1 or s1[0] == "<read":
                prev = (None, st)  # an assignment alone (`T=$` before its substitution) is read by the next stage
                continue
            h, a = s1[0], s1[1]
            if h in ("cd", "pushd") and a:
                here = os.path.join(here or ".", _expanduser(_expand_vars(a[0], shvars)))
            # the code's own simple command: its level, not one deeper (of_review on organic-framework PR 391)
            hit = _shell_runs_protected(h, a, here, st, shvars, _fed(prior), depth) or \
                (_find_runs_protected(a, here) if h == "find" else None)
            if hit:
                return hit
            prior.append((h, a, st, here))
            prev = (h, st)
    return None

def _names_protected(head, args, stext, cwd):
    """A file under a protected_code entry that one pipe stage names as an argument or reads on its stdin, else None:
    what it may hand the next stage (G21)."""
    for a in [a for a in args if not a.startswith("-")] + [_stdin_redirect(stext)]:
        # `dd if=f`, and `git show HEAD:path` (a path in the repository, read from where git runs)
        for p in [a] + ([a.split("=", 1)[1]] if a and "=" in a else []) + \
                ([a.split(":", 1)[1]] if a and ":" in a and not a.startswith("/") else []) + \
                (_PATH_TOKEN.findall(a) if a else []):
            if p and _protected_code(p, cwd):
                return p
    return None


def _find_runs_protected(args, cwd):
    """`find <entry> … -exec bash {} ;` (and -execdir, -ok): a shell run on files found under an entry, or handed one
    by name (G21)."""
    roots = []
    for a in args:
        if a.startswith(("-", "(", "!")):
            break
        roots.append(a)
    for k, a in enumerate(args[:-1]):
        if a in _FIND_EXEC:
            h = os.path.basename(args[k + 1])
            if h in _RUN_SHELLS or h in ("source", ".", "busybox"):
                rest = args[k + 2:]
                rest = rest[:next((n for n, x in enumerate(rest) if x in (";", "+")), len(rest))]
                hit = _shell_runs_protected(h, [x for x in rest if x != "{}"], cwd)
                if hit:
                    return hit
                if "{}" in rest:
                    hit = next((r for r in roots if _protected_code(r, cwd, removal=True)), None)
                    if hit:
                        return hit
    return None

_LINE_LINKS = {}   # links made earlier on the line critical_deny checks: {the link's path: what it points to}


def _glob_now(word, cwd):
    """What a glob in WORD names now, as the shell would expand it; [] for a word with no glob."""
    w = str(word or "").strip("'\"")
    if not any(ch in w for ch in "*?["):
        return []
    import glob as _glob
    p = _expanduser(w if w.startswith(("/", "~", "$")) else os.path.join(cwd or ".", w))
    try:
        return _glob.glob(p)[:64]
    except Exception:  # noqa: BLE001
        return []


def _protected_code_deny(head, args, stext, cwd, env=None, shvars=None, fed=None):
    """(reason, path) for G21 on one simple command run in CWD with ENV (the line's exports and its own
    assignments), SHVARS (those and the line's shell variables) and FED (a protected file an earlier stage of its
    pipe names), else None; and the guard's own state files, always."""
    removal = head in ("rm", "rmdir", "mv", "unlink", "shred")
    files = _changed_files(head, args, stext) + ([a for a in args if not a.startswith("-")] if head == "rmdir" else [])
    # a path held in a line variable, as the shell fills it in (`d=$HOME/.config/of; cp x "$d/orgs.yaml"`:
    # of_secrets-review on hand-over 726902df); the written words stay too
    files += [x for x in (_expand_vars(f, shvars or {}) for f in files) if x not in files]
    files += [m for f in list(files) for m in _glob_now(f, cwd) if m not in files]   # `rm …/2099*.json`, `> …yam[l]`
    if head == "ln":
        ops = [a for a in args if not a.startswith("-")]
        if len(ops) >= 2:
            _LINE_LINKS[os.path.normpath(_expanduser(os.path.join(cwd or ".", ops[-1])))] = ops[0]
    for f in list(files):   # `ln -s <file> /tmp/x; echo y > /tmp/x` writes the file, and a link to a folder the files
        # under it (`ln -s ~/.config/of /tmp/c && echo y > /tmp/c/orgs.yaml`: of_review on PR 395)
        k = os.path.normpath(_expanduser(os.path.join(cwd or ".", f)))
        for link, to in list(_LINE_LINKS.items()):
            if k == link:
                files.append(to)
            elif k.startswith(link + "/"):
                files.append(os.path.join(to, k[len(link) + 1:]))
    state = next((f for f in files if _guard_state_file(f, cwd)), None)
    if state:
        return GUARD_STATE_DENY, state
    if not _ORG_PROTECTION["code"]:
        launch = _launch_write(head, args, cwd, files, removal)
        return (LAUNCH_DENY, launch) if launch else (_owner_config_write(head, args, cwd, files, removal) or
                                                     _asks_outbox_write(head, args, cwd, files, removal) or
                                                     _session_index_write(head, args, cwd, files, removal) or
                                                     _forward_write(head, args, cwd, files, removal))
    hit = _git_protected(args, cwd, env or {}, shvars) if head == "git" else None
    if hit:
        return PROTECTED_CODE_DENY, hit
    hit = _shell_runs_protected(head, args, cwd, stext, shvars, fed) or \
        (_find_runs_protected(args, cwd) if head == "find" else None)
    if hit:
        return PROTECTED_RUN_DENY, hit
    # a recursive copy or an extraction writes anywhere under its destination: a folder above an entry counts
    # (of_secrets-review's review of organic-framework PR 65: cp -R, rsync, ditto, tar -C, unzip -d)
    hit = next((t for t in _tree_targets(head, args, cwd) if _protected_code(t, cwd, removal=True)), None)
    if hit:
        return PROTECTED_CODE_DENY, hit
    hit = next((f for f in files if _protected_code(f, cwd, removal)), None)
    if hit:
        return PROTECTED_CODE_DENY, hit
    launch = _launch_write(head, args, cwd, files, removal)  # G29 after G21: where both hold, G21 names it
    return (LAUNCH_DENY, launch) if launch else (_owner_config_write(head, args, cwd, files, removal) or
                                                 _asks_outbox_write(head, args, cwd, files, removal) or
                                                 _session_index_write(head, args, cwd, files, removal) or
                                                 _forward_write(head, args, cwd, files, removal))


def _owner_config_write(head, args, cwd, files, removal):
    """G33 on one simple command: a write, removal, move or link over orgs.yaml or views.yaml, read as G29 reads one."""
    hit = _launch_write(head, args, cwd, files, removal, _owner_config_targets())
    if not hit:
        return None
    if OWNER_CONFIG_MODE == "deny":
        return OWNER_CONFIG_DENY, hit
    _WARNINGS.append((OWNER_CONFIG_DENY, hit, "a warning now, a deny once the flows that write it have moved"))
    return None


def _herdr_lab(path):
    lab = os.path.realpath(_expanduser(HERDR_LAB))
    p = os.path.realpath(_expanduser(str(path or "")))
    return bool(path) and (p == lab or p.startswith(lab + "/"))


def _plugin_dirs():
    if not _PLUGIN_DIRS:
        try:
            import of_family
            dirs = list(of_family.code_paths())
            dirs.append(os.path.join(os.path.dirname(of_family.SHIPPED.rstrip("/")), "..", "herdr-plugin"))
        except Exception:  # noqa: BLE001 - no loader, no plugin folders to keep
            dirs = []
        keep = [os.path.realpath(d) for d in dirs if not gated(d)]
        xdg = os.environ.get("XDG_CONFIG_HOME")
        herdr = list(HERDR_PLUGIN_DIRS) + ([os.path.join(xdg, "herdr", "plugins")] if xdg and not _herdr_lab(xdg)
                                           else [])
        _PLUGIN_DIRS.append(keep + [os.path.realpath(_expanduser(d)) for d in herdr])
    return _PLUGIN_DIRS[0]


def _in_plugin_code(path, cwd):
    p = _expanduser(str(path).strip("'\""))
    p = os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd or ".", p))
    return any(p == d or p.startswith(d.rstrip("/") + "/") for d in _plugin_dirs())


def _line_env(stext, env):
    """{name: value or None} in effect for one simple command: the session's environment, then ENV (the line's exports,
    an `unset` as ""), then the command's own `env -u NAME`, `env NAME=value` and leading NAME=value words. None means
    unset."""
    out = {k: v for k, v in os.environ.items()}
    for k, v in (env or {}).items():
        if isinstance(v, str):
            out[k] = v or None
    try:
        toks = _tokens(stext)
    except ValueError:
        toks = []
    k = 0
    while k < len(toks):
        w = toks[k]
        m = re.match(r"^([A-Za-z_]\w*)=(.*)$", w, re.S)
        if m:
            out[m.group(1)] = m.group(2) or None
        elif w in ("-u", "--unset") and k + 1 < len(toks):
            out[toks[k + 1]] = None
            k += 1
        elif w.startswith("--unset="):
            out[w.split("=", 1)[1]] = None
        elif not (os.path.basename(w) == "env" or w.startswith("-")):
            break
        k += 1
    return out


def _herdr_reached_is_lab(stext, env):
    """Does this herdr command reach of_herdr's lab herdr? herdr talks to the running herdr through its socket:
    HERDR_SOCKET_PATH, inherited from an agent's pane, is the owner's; only when it is unset does herdr place its
    socket under XDG_CONFIG_HOME (of_architect's review of PR 32, checked on herdr 0.9.5)."""
    if (env or {}).get("__lab_link"):  # the line made a link or a socket in the lab before this command
        return False
    e = _line_env(stext, env)
    sock = e.get("HERDR_SOCKET_PATH")
    if not sock:  # herdr then uses its socket under XDG_CONFIG_HOME, as the owner's under ~/.config
        xdg = e.get("XDG_CONFIG_HOME")
        if not _herdr_lab(xdg):
            return False
        sock = os.path.join(_expanduser(xdg), "herdr", "herdr.sock")
    # the socket must exist when the guard looks, its real path inside the lab: a link made later on the line, or one
    # that points out of the lab, reaches the owner's herdr (of_architect's second review of PR 32)
    path = _expanduser(sock)
    return os.path.lexists(path) and os.path.exists(path) and _herdr_lab(os.path.realpath(path))


def _makes_lab_link(head, args):
    """Does one simple command make a link or a socket under of_herdr's lab (ln; socat or nc listening on a Unix
    socket there)?"""
    def lab(a):
        a = a.split(":", 1)[1] if re.match(r"^(?:UNIX|unix)[\w-]*:", a) else a
        return _herdr_lab(a.split(",", 1)[0])
    if head == "ln":
        return any(lab(a) for a in args if not a.startswith("-"))
    if head == "socat":
        return any(re.match(r"^(?:UNIX|unix)[\w-]*:", a) and lab(a) for a in args)
    if head in ("nc", "ncat", "netcat"):
        return any("U" in a[1:] for a in args if a.startswith("-") and not a.startswith("--")) and \
            any(lab(a) for a in args if not a.startswith("-"))
    return False


def _plugins_deny(head, args, stext, cwd, env=None):
    """(reason, path) for G19 on one simple command run in CWD with ENV, else None. `herdr plugin install|link|…` is
    the owner's act, except when the command reaches of_herdr's isolated lab herdr (_herdr_reached_is_lab)."""
    if head == "herdr" and args[:1] == ["plugin"] and len(args) > 1 and args[1] in HERDR_PLUGIN_ACTS \
            and not _herdr_reached_is_lab(stext, env):
        hit = (PLUGINS_DENY, f"herdr plugin {args[1]}")
        if HERDR_PLUGIN_MODE == "deny":
            return hit
        _WARNINGS.append(hit)
        return None
    hit = next((f for f in _changed_files(head, args, stext) if _in_plugin_code(f, cwd)), None)
    return (PLUGINS_DENY, hit) if hit else None


def _hooks_deny(head, args, stext, cwd, env=None):
    """(reason, token) for G17 on one simple command run in CWD with ENV (the line's exports and its own
    assignments), else None."""
    if head != "git":
        hit = next((f for f in _changed_files(head, args, stext) if _in_hooks(f, cwd)), None)
        return (HOOKS_DENY, hit) if hit else None
    where0 = cwd or "."
    for k in range(len(args) - 1):  # git -C <dir> before the subcommand moves the repository
        if args[k] == "-C":
            where0 = os.path.join(where0, _expanduser(args[k + 1]))
    expanded, shell = git_expand(args, where0, env, (env or {}).get("__git_aliases"))
    if shell is not None:
        hit = _bash_deny(shell, 1) if gated(where0) else None
        return hit if hit else None
    if expanded is None:
        return (HOOKS_DENY, f"git {next((a for a in args if not a.startswith('-')), '')}: an alias or command the "
                            "guard cannot read") if gated(where0) else None
    args = expanded
    i, where, conf = 0, cwd or ".", []
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
            if a == "-C":
                where = os.path.join(where, _expanduser(args[i + 1]))
            elif a == "-c" or a == "--config-env":
                conf.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--config-env="):
            conf.append(a.split("=", 1)[1])
        i += 1
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else (None, [])
    rest = rest[:rest.index("--")] if "--" in rest else rest
    files = [f for f in _changed_files(head, args, stext) if _in_hooks(f, where)]
    if files:
        return HOOKS_DENY, files[0]
    if not sub or not gated(where):
        return None
    env = dict(env or {})
    if any(c.lower().startswith("core.hookspath") for c in conf) or \
            any(k == "core.hookspath" for k, _ in _git_config_pairs(args, env)) or \
            "hookspath" in str(env.get("GIT_CONFIG_PARAMETERS") or "").lower():
        return HOOKS_DENY, "git with core.hooksPath set for the call"
    if _config_files_set(env, "hookspath"):
        return HOOKS_DENY, "git with a config file that sets core.hooksPath"
    if sub == "config":
        low = [a.lower() for a in rest]
        key = next((k for k, a in enumerate(low) if a == "core.hookspath" or a.startswith("core.hookspath=")), None)
        writes = {"--unset", "--unset-all", "--add", "--replace-all", "set", "unset", "--edit", "-e", "edit"}
        if key is not None and (key + 1 < len(rest) or "=" in low[key] or writes & set(low[:key])):
            return HOOKS_DENY, "git config core.hooksPath"
        return None
    if any(_no_verify(a) for a in rest):
        return HOOKS_DENY, f"git {sub} --no-verify"
    if sub == "commit":
        for a in rest:
            if a.startswith("-") and not a.startswith("--"):
                for ch in a[1:]:
                    if ch == "n":
                        return HOOKS_DENY, "git commit -n"
                    if ch in _COMMIT_VALUE_FLAGS:
                        break
    return None


# git config values git runs through the shell: a pager, an editor, an ssh command, an external diff, a textconv, a
# filter, a merge driver, a gpg program, a `!` alias or helper (of_secrets-review, msg 01a1f4fe-b2ad: `git -c
# core.pager='cat <key>' log` passed on main); and the environment variables git and its pager read the same way
_GIT_SHELL_KEY = re.compile(r"^(?:core\.(?:pager|editor|sshcommand|fsmonitor|askpass|alternaterefscommand|hookspath)|"
                            r"sequence\.editor|diff\.external|diff\..+\.(?:textconv|command)|merge\..+\.driver|"
                            r"filter\..+\.(?:clean|smudge|process)|gpg(?:\..+)?\.program|pager\..+|"
                            r"interactive\.difffilter|man\..+\.cmd|browser\..+\.cmd|web\.browser|difftool\..+\.cmd|"
                            r"mergetool\..+\.cmd|tar\..+\.command|uploadpack\.packobjectshook|guitool\..+\.cmd|"
                            r"gc\.recentobjectshook|trailer\..+\.(?:cmd|command)|"
                            r"sendemail\.(?:.+\.)?(?:tocmd|cccmd|headercmd|smtpserver|sendmailcmd)|"
                            r"credential(?:\..+)?\.helper)$")   # a helper runs as a command even with no `!`
_GIT_BANG_KEY = re.compile(r"^(?:alias\.[^.]+|submodule\..+\.update)$")  # a shell command when it starts with !
# git config's own options and the words they take (`--file f`, `set`: of_review on organic-framework PR 400)
_GIT_CONFIG_VALUE_OPTS = frozenset("-f --file --blob -t --type --default --comment --value --url".split())
# git config's modes that set no value: their second word is a pattern or nothing (of_review on PR 400)
_GIT_CONFIG_READS = frozenset("--get --get-all --get-regexp --get-urlmatch --get-color --get-colorbool --unset "
                              "--unset-all --list -l --edit -e --remove-section --rename-section".split())


def _git_config_flags(words):
    """The option words git config gives itself, past the values its value options take (`--comment -e`, `--file -l`
    give no mode: of_secrets-review on organic-framework PR 403)."""
    out, k = set(), 0
    while k < len(words):
        if words[k] in _GIT_CONFIG_VALUE_OPTS:
            k += 2
            continue
        if not words[k].startswith("-"):
            break   # the key, or a subcommand: a mode word after it is a value-pattern, and git still sets the value
            # (`git config KEY VALUE -l`, `--show-origin`: of_review on organic-framework PR 403)
        out.add(words[k].split("=", 1)[0])
        k += 1
    return out
_GIT_CONFIG_SUBCOMMANDS = frozenset("set get unset list edit rename-section remove-section".split())
_GIT_SHELL_ENV = frozenset({"GIT_PAGER", "PAGER", "GIT_EDITOR", "EDITOR", "VISUAL", "GIT_SSH_COMMAND", "GIT_SSH",
                            "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SEQUENCE_EDITOR", "LESSOPEN",
                            "LESSCLOSE", "MANPAGER"})


def _git_shell_values(head, args, stext, env, depth):
    """A deny for one simple command when a git config value or an environment variable that git (or its pager) runs as
    a shell command holds one the guard denies: `git -c KEY=VALUE`, `git config [scope] KEY VALUE` (it persists, so a
    later call runs it), GIT_CONFIG_* pairs and `GIT_PAGER=… git …`."""
    values = [v for k, v in env.items() if k in _GIT_SHELL_ENV and v]
    if head == "git":
        values += [v.lstrip("!") for k, v in _git_config_pairs(args, env)
                   if _GIT_SHELL_KEY.match(k) or (_GIT_BANG_KEY.match(k) and v.startswith("!"))]
        if "config" in args and not _GIT_CONFIG_READS & _git_config_flags(args[args.index("config") + 1:]):
            rest, k = [], args.index("config") + 1
            while k < len(args):
                a = args[k]
                if a in _GIT_CONFIG_VALUE_OPTS:
                    k += 2
                    continue
                if a.startswith("-"):
                    k += 1
                    continue
                rest.append(a)
                k += 1
            if rest[:1] and rest[0] in _GIT_CONFIG_SUBCOMMANDS:
                rest = rest[1:] if rest[0] == "set" else []
            if len(rest) >= 2 and (_GIT_SHELL_KEY.match(rest[0].lower()) or
                                   (_GIT_BANG_KEY.match(rest[0].lower()) and rest[1].startswith("!"))):
                values.append(" ".join(rest[1:]).lstrip("!"))
    for v in values:
        hit = _bash_deny(v, depth + 1)
        if hit:
            return hit
    return None


def _bash_deny(cmd, depth=0, reaches_agent=True):
    """(reason, matched token) for a shell command line, or None. The rule (of_architect, 2026-11-06): deny
    what will execute or be read as data by the command, and never let a secret VALUE reach the transcript or
    a secret FILE leave its place; prose that is only carried is skipped. `reaches_agent` is whether this
    line's stdout is shown (vs. piped on, redirected to a file, or captured by a parent that does not show it).
    See RFC 0002 §4.1 for the known limits (unknown wrappers, indirection, interpreters dumping the env)."""
    _READS[0] += 1
    if _READS[0] > READ_BUDGET:  # G0.11: more nested bodies than one call reads
        _CAPPED[0] = _CAPPED[0] or "_bash_deny (read budget)"
        return None
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_bash_deny"
        return None
    text, subs, docs, _ = _scan(cmd)
    raw = _cut_heredocs(cmd) if _ORG_PROTECTION["code"] and ("<<<" in cmd or "$(" in cmd or "`" in cmd) else ""
    if "<<<" in raw or "$(" in raw or "`" in raw:
        # each $( ) becomes a placeholder below, so G21 reads the line as written here: `bash <<< "$(cat file)"`,
        # `bash -c "$(cat file)"`, `eval "$(cat file)"` (of_secrets-review and of_review on PR 249)
        named = _code_runs_protected(raw, _DENY_CWD[0])
        if named:
            return PROTECTED_RUN_DENY, named
    expandable = _expandable_secret_vars(cmd)
    for sub in subs:  # a $() / backtick value enters argv or is printed: treat it as reaching the agent
        hit = _bash_deny(sub, depth + 1, reaches_agent=True)
        if hit:
            return hit
    for quoted, body, opener in docs:
        head = _head(opener)
        remote, lang = None, _lang(head)
        if head in _REMOTE_HEADS:
            try:
                ws = _command_words(_segments(opener)[-1])
                remote = _remote_shell(os.path.basename(ws[0]), ws[1:]) if ws else None
                lang = next(filter(None, (_lang(os.path.basename(w)) for w in ws[1:])), None)
            except (ValueError, IndexError):
                remote = None
        if head in _SHELLS or remote == "shell":
            hit = _bash_deny(body, depth + 1)
            named = _code_runs_protected(body, _DENY_CWD[0]) if not hit and _ORG_PROTECTION["code"] else None
            hit = hit or ((PROTECTED_RUN_DENY, named) if named else None)
        elif head in _INTERPRETERS or remote == "code":
            hit = _code_deny(body, lang)
        elif not quoted:  # an unquoted delimiter runs $( ) in the body, whatever reads it
            hit = next(filter(None, (_bash_deny(s, depth + 1) for s in _scan(body)[1])), None)
            if not hit and (head == "tee" or (head == "cat" and ">" not in opener)):  # printed to stdout
                hit = next((_prints(m.group(0)) for m in _VAR_REF.finditer(body) if _secret_name(m.group(1))), None)
        else:
            hit = None
        if not hit and head not in _SHELLS:
            # a body fed to a doubted wrapper (`script -q -c sh /dev/null <<EOF`): a shell in it may read it as code
            # (of_review on organic-framework PR 332)
            try:
                seg = _segments(opener)[-1]
            except (ValueError, IndexError):
                seg = []
            if _wrapper_readings(list(seg)) or _line_doubted(text):  # `2>&1 <<END script -q`, `true && <<END …`
                hit = _readings_past_bound([body], depth) or _bash_deny(body, depth + 1)
        if hit:
            return hit
    try:
        pipes = _split(text)
    except ValueError:  # the line could not be parsed: the old strict text match, but only OUTSIDE quotes, so a
        bare = _strip_quoted(cmd)  # prose-carrying argument (of send "...", git commit -m "...") is not scanned
        m = CRITICAL_DENY_BASH.search(bare)
        if m:
            return _prints(m.group(0).strip(" ;&|("))
        mem = next((t for t in re.split(r"[\s;&|<>()]+", bare) if _MEMORY_CODE.match(t.strip("'\""))), None)
        if mem and (READERS.search(bare) or re.search(r"\b(?:dd|cp|scp|rsync|gcore|gdb|lldb)\b", bare)):
            return f"a command that reads memory or a core dump ({mem})", mem
        return _reads(READERS.search(bare) and _loose_secret_path(bare))
    here = _DENY_CWD[0]  # the directory a command runs in, following `cd` along the line (G17)
    here21 = here        # the same, with a variable set on the line read in its `cd` (G21 only, so other rules keep
    # the folder they read before; of_review on PR 249)
    exported = {}        # what `export` set earlier on the line (G17: GIT_CONFIG_* variables)
    line_vars = {}       # shell variables a bare `NAME=value` set earlier on the line (G6: a --raw path)
    loops = _LOOPS[0]    # `for NAME in WORDS` earlier on the line: what NAME holds in the body, a { } group or a
    # subshell in it included (G0.9), shared by every depth of one check
    lab_link = False     # a link or a socket made under of_herdr's lab earlier on the line (G19)
    for pipe in pipes:
        last = len(pipe) - 1
        cmds = []  # (head, args) of the simple-command stages, for the env-dump pipeline check
        prior = []  # earlier stages of this pipe (G21: `cat <script> | bash`), read only when a shell needs them
        prev = None  # the last simple command's (head, text): `bash <(cat <script>)` splits into it and a sub
        for idx, (kind, stext) in enumerate(pipe):
            if kind == "sub" and prev and _ORG_PROTECTION["code"] and prev[1].rstrip().endswith("<") \
                    and prev[0] in _RUN_SHELLS | {"source", "."}:
                named = _text_names_protected(stext, here21)
                if named:
                    return PROTECTED_RUN_DENY, named
            if kind == "sub":
                hit = _bash_deny(stext, depth + 1, reaches_agent=reaches_agent and idx == last)
                if hit:
                    return hit
                continue
            _loop_header(stext, loops)
            _REDIR_CTX[0] = (here, {**line_vars, **exported, **_assignments(stext)})
            # before the simple command: `script -qc '<line>' <file>` runs nothing after its file. Each reading is
            # judged one level down, in the folder this command runs in, a variable's `cd` included (`cd bin && script
            # -c …`, `cd "$d" && …`; of_review on organic-framework PR 332)
            readings = _wrapper_readings(_tokens_or_empty(stext))
            if readings:
                past = _readings_past_bound(readings, depth)
                if past:
                    return past
                saved = _DENY_CWD[0]
                try:
                    _DENY_CWD[0] = here21
                    for line in readings:
                        hit = _bash_deny(line, depth + 1, reaches_agent=reaches_agent)
                        if hit:
                            return hit
                finally:
                    _DENY_CWD[0] = saved
                _REDIR_CTX[0] = (here, {**line_vars, **exported, **_assignments(stext)})
            s = _simple(stext)
            if not s:
                line_vars.update(_assignments(stext))  # `P=…;` alone: a shell variable the line reads later
                if _write_targets(stext):  # `>> <file>` with no command writes too (G21, G29, G33: of_secrets-review
                    # on organic-framework PR 387)
                    hit = _protected_code_deny(":", [], stext, here21, {**exported, **_assignments(stext)},
                                               {**line_vars, **exported})
                    if hit:
                        return hit
                continue
            if s[0] == "<read":
                return (f"a command that reads memory or a core dump ({s[1]})", s[1]) if _memory_path(s[1]) \
                    else _reads(s[1])
            head, args, words, to_file, herestr = s
            cmds.append((head, args))
            stage_reaches = reaches_agent and idx == last and not to_file
            if head == "security":  # G11, G20
                hit = _security_deny(args)
                if hit:
                    return hit
            hit = _cluster_secret_deny(head, args, to_file, {**line_vars, **exported, **_assignments(stext)}) \
                or _memory_deny(head, args) \
                or _op_founder_deny(head, args, stext) or _op_deny(head, args) or _grant_deny(head, args, stext) or _tool_token_deny(head, args) or _hook_entry(head, args) \
                or _forward_deny(head, args, {**line_vars, **exported, **_assignments(stext)}, here) \
                or _actor_deny(head, args, stext, {**exported, **_assignments(stext)}) \
                or _hooks_deny(head, args, stext, here, {**exported, **_assignments(stext)}) \
                or _plugins_deny(head, args, stext, here, {**exported, **_assignments(stext),
                                                           **({"__lab_link": "1"} if lab_link else {})}) \
                or _protected_code_deny(head, args, stext, here21, {**exported, **_assignments(stext)},
                                        {**line_vars, **exported, **_assignments(stext)}, _fed(prior))
            if hit:
                return hit
            if _ORG_PROTECTION["code"]:
                prior.append((head, [_expand_vars(a, {**line_vars, **exported}) for a in args], stext, here21))
            prev = (head, stext)
            if head in ("cd", "pushd"):  # pushd moves like cd (of_review on PR 249)
                to = _expanduser(args[0]) if args and args[0] != "-" else _expanduser("~")
                here = to if os.path.isabs(to) else os.path.join(here or ".", to)
                to = _expanduser(_expand_vars(args[0], {**line_vars, **exported})) \
                    if args and args[0] != "-" else _expanduser("~")
                here21 = to if os.path.isabs(to) else os.path.join(here21 or ".", to)
            elif head == "export":
                exported.update(dict(a.split("=", 1) for a in args if re.match(r"^[A-Za-z_]\w*=", a)))
            elif head == "unset":
                exported.update({a: "" for a in args if re.match(r"^[A-Za-z_]\w*$", a)})  # "" is unset
            if _makes_lab_link(head, args):
                lab_link = True
            elif head == "git":
                words = [a for a in args if not a.startswith("-")]
                if words[:1] == ["config"] and len(words) >= 3 and words[1].lower().startswith("alias."):
                    exported.setdefault("__git_aliases", {})[words[1][len("alias."):].lower()] = " ".join(words[2:])
            remote = _remote_shell(head, args)
            if remote:
                files = [f for f in [_stdin_file(stext)] if f]
                if idx > 0 and pipe[idx - 1][0] == "cmd":
                    prev = _simple(pipe[idx - 1][1])
                    if prev and prev[0] == "cat":
                        files += [a for a in prev[1] if not a.startswith("-")]
                for f in files:
                    hit = _stdin_script_deny(f, remote, depth)
                    if hit:
                        return hit
            if head in ("echo", "printf") and stage_reaches:
                hit = next((_prints(m.group(0)) for w in words for m in _VAR_REF.finditer(w)
                            if m.group(0) in expandable), None)
                if hit:
                    return hit
            if herestr is not None:
                if head in _SHELLS:
                    hit = _in_folder(here21, _bash_deny, herestr, depth + 1)
                elif head in _INTERPRETERS:
                    hit = _code_deny(herestr, _lang(head))
                else:
                    hit = None
                if hit:
                    return hit
            remote_body = head in _REMOTE_HEADS  # ssh's remote command, a container's or a VM's exec
            for body in _nested(head, args):
                _REMOTE_BODY[0] += remote_body
                try:  # a local body runs in this command's folder (`cd ~/.config/of && sh -c '… >> views.yaml'`)
                    hit = _bash_deny(body, depth + 1) if remote_body else _in_folder(here21, _bash_deny, body, depth + 1)
                finally:
                    _REMOTE_BODY[0] -= remote_body
                if hit:
                    return hit
            if head in _INTERPRETERS:
                for flag in ("-c", "-e"):
                    if flag in args[:-1]:
                        hit = _code_deny(args[args.index(flag) + 1], _lang(head))
                        if hit:
                            return hit
                kind, code, _ = _py_walk(args) if head.startswith("python") else (None, None, None)
                if kind == "code":  # `-Ic "…"`, `-c"…"`: python's own reading (of_secrets-review on PR 136)
                    hit = _code_deny(code, "python")
                    if hit:
                        return hit
            if head == "git" or _GIT_SHELL_ENV & set(_assignments(stext)):
                hit = _git_shell_values(head, args, stext, {**exported, **_assignments(stext)}, depth)
                if hit:
                    return hit
            if head == "curl":
                hit = _curl_deny(args)
                if hit:
                    return hit
            elif head == "wget":
                hit = _wget_deny(args)
                if hit:
                    return hit
            elif re.search(r"__OFSUB|\$|`", head or ""):
                # a command name the shell fills in (`$(echo cat) <key>`, `$C <key>`): any program may run, so its
                # arguments are judged as a reader's (of_secrets-review, msg 01a1f4fe-b2ad, on main)
                for a in args:   # an option's `name=value` (`custom-columns=N:.spec.secret`) names no file
                    if not a.startswith("-") and "=" not in a.split("/")[0] and _secret_path(a):
                        return _reads(a)
            if head in _FILE_READERS:
                for a in _data_args(head, args):
                    if _secret_path(a) or _secret_here(a, here):
                        return _reads(a)
                    hit = _reader_key(a, here, loops, {**line_vars, **exported, **_assignments(stext)})  # G0.9, G1.13
                    if hit:
                        return hit
        # the environment printed to the agent: a value-dump stage with no names-only reducer after it
        sink_to_file = False
        if pipe and pipe[last][0] == "cmd":
            s = _simple(pipe[last][1])
            sink_to_file = bool(s) and s[0] != "<read" and s[3]
        for di, (h, a) in enumerate(cmds):
            if _env_value_dump(h, a) or _ps_env(h, a):
                if reaches_agent and not sink_to_file and not any(_names_only(hh, aa) for hh, aa in cmds[di + 1:]):
                    return _prints("ps e (environment); use `of capacity status`/`of who` for a config dir"
                                   if h == "ps" else ("env" if h == "env" else h))
                break
    return None


# G26 and G27 (2026-11-15; of_conductor, message 01a1de70-16f5, from that day's incident on the founder's Mac): a test
# command `sed … && T=<dir> && rm -rf $T && mkdir …; cd $T && git init -q && git commit …` ran with T unset, since the
# && chain broke on sed and `;` went on. `cd` went to the home folder and git init made a repository there; every git
# call under the home folder then walked all of it for three hours, and macOS logged denied reads of the person's
# photos, contacts and documents. The same line with `rm -rf $T/` would have deleted the home folder.
HOME_REPO_DENY = ("a git repository at the home folder: every git call under it would walk the whole home folder "
                  "and its photos, contacts and documents (docs/guard.md G26). Run git init, clone and commit in a "
                  "project's own folder")
UNSET_TARGET_DENY = ("{what} whose target rests on a variable that may be unset or empty when it runs ({names}): "
                     "unset, `cd` goes to the home folder and `rm -rf $T/` removes it (docs/guard.md G27). Write "
                     "${{NAME:?}}, or an absolute path, or set NAME earlier on the same && chain")
# An rm -r whose path, with the loose names empty, is the root, the home folder or a folder above it, every file in one
# of them, or everything in the working folder denies at once (a home folder removed is not undone). The rest warns
# first: the 21-day replay found them in live flows (`cd /p && W=… ; git worktree add … $W ; cd $W`), each rewritten in
# one step, and a cd that may reach the home folder before git init is G26's deny already.
UNSET_TARGET_MODE = {"rm -r home": "deny", "rm -r": "warn", "cd": "warn", "git": "warn"}
MAYBE_HOME = "\0maybe-home"   # where a cd whose target may be unset left the line: the home folder, perhaps
ESCAPE_DENY = "a pattern this machine's sed or awk does not read as written"
OWN_ADVICE = ("a git repository at the home folder", "cd whose target", "rm -r whose target", "git whose target",
              POD_ENV_DENY, SECRET_FETCH_DENY, ESCAPE_DENY, CODE_READS, ACTOR_DENY)
_EXPANSION = re.compile(r"\$\{([A-Za-z_]\w*)([^}]*)\}|\$([A-Za-z_]\w*)")
_LEAD_KEYWORDS = re.compile(r"^\s*(?:(?:then|do|else|elif|if|while|until|!|\{)\s+)+")
_CHECKED = re.compile(r"\$\{([A-Za-z_]\w*):?\?")   # ${NAME:?…}: past it, NAME is set (the shell stops otherwise)
_GIT_GLOBAL_VALUE = {"-c", "--exec-path", "--namespace", "--config-env", "--super-prefix", "--list-cmds"}
_GIT_INIT_VALUE = {"--template", "--separate-git-dir", "-b", "--initial-branch", "--object-format", "--ref-format"}
_GIT_CLONE_VALUE = {"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference",
                    "--reference-if-able", "--separate-git-dir", "-u", "--upload-pack", "-j", "--jobs", "--filter",
                    "--template", "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri",
                    "--ref-format", "--revision"}
_READ_VALUE = {"-p", "-d", "-n", "-N", "-t", "-u", "-a", "-i"}
_TEST_SET = {"-n", "-d", "-e", "-f", "-s", "-r", "-w", "-x", "-L", "-h", "-O", "-G", "-S", "-b", "-c", "-p"}


def owns_advice(why):
    """Does the reason carry its own advice (not the secret rules' "secrets stay in their files")?"""
    return why == GRANT_DENY or any(why.startswith(p) for p in OWN_ADVICE)


def _loose(word, known, empty, nounset):
    """The names in WORD whose expansion may be unset or empty where it runs: not ${NAME:?…} or ${NAME?…} (the shell
    stops), not ${NAME:-x}, ${NAME-x}, ${NAME:=x} or ${NAME=x} with a default that is never empty, and not a name set
    on every path to here (KNOWN) unless it was set empty (EMPTY). Under `set -u` only a name set empty is loose."""
    out = []
    for m in _EXPANSION.finditer(word):
        name, mod = m.group(1) or m.group(3), m.group(2) or ""
        if mod[:2] in (":?",) or mod[:1] == "?":
            continue
        op = mod[:2] if mod[:2] in (":-", ":=") else mod[:1] if mod[:1] in ("-", "=") else None
        defaulted = False
        if op is not None:
            default = mod[len(op):]
            if _EXPANSION.sub("", default) or (default and not _loose(default, known, empty, nounset)):
                continue  # a default with a literal part, or of names that are set: never empty
            defaulted = True  # ${T:-} is how a line gets past `set -u`: it stays loose (of_secrets-review, PR 218)
        if name in empty or (name not in known and (not nounset or defaulted)):
            out.append(name)
    return out


def _with_defaults(word, known):
    """WORD with each ${NAME:-x}, ${NAME-x}, ${NAME:=x} or ${NAME=x} of a name not KNOWN replaced by its default x:
    `rm -rf ${T:-$HOME}` removes the home folder when T is unset (of_review on PR 218)."""
    def one(m):
        name, op, default = m.group(1), m.group(2), m.group(3)
        return m.group(0) if name in known else default
    return re.sub(r"\$\{([A-Za-z_]\w*)(:?[-=])([^}]*)\}", one, word)


def _whole_folder(word):
    """Does WORD, relative, name everything in the working folder (`*`, `.`, `./*`, `.[!.]*`, `.*`)?"""
    w = word[2:] if word.startswith("./") else word
    return w in ("", ".", "./", "..", "../") or w.startswith(("*", "?", "[", ".*", ".[", ".?"))


def _resolve_dir(word, here, values):
    """The folder a literal or a known word names, or None: `~` and $HOME the home folder, a name set to a literal
    on the line its value, a relative path from HERE."""
    if here == MAYBE_HOME:
        here = None if word not in (".", "./") else MAYBE_HOME
        if here:
            return here
    w = re.sub(r"^(?:\$\{HOME\}|\$HOME)(?=/|$)", "~", word)
    for name, val in values.items():
        w = re.sub(r"\$\{%s\}|\$%s(?!\w)" % (name, name), lambda _m, v=val: v, w)
    if "$" in w or "__OFSUB" in w or "`" in w:
        return None
    w = _expanduser(w)
    if not os.path.isabs(w):
        if here is None:
            return None
        w = os.path.join(here, w)
    return os.path.normpath(w)


def _home_repo(where, loose):
    if where == MAYBE_HOME and loose:
        return (HOME_REPO_DENY + f"; here an earlier cd goes to the home folder when {', '.join(sorted(set(loose)))} "
                "is unset: write ${NAME:?} (G27)")
    return HOME_REPO_DENY


def _home_git(path):
    """Is PATH the home folder's git directory (`<home>/.git`), or the home folder itself?"""
    if path is None or path == MAYBE_HOME:
        return path == MAYBE_HOME
    try:
        return _is_home(path) or os.path.realpath(path) == os.path.join(
            os.path.realpath(_expanduser("~")), ".git")
    except (OSError, ValueError):
        return False


def _opt_word(args, name):
    """The value of option NAME (`--name v` or `--name=v`) in ARGS, or None."""
    for k, a in enumerate(args):
        if a == name and k + 1 < len(args):
            return args[k + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _is_home(path):
    if path == MAYBE_HOME:
        return True
    try:
        return path is not None and os.path.realpath(path) == os.path.realpath(_expanduser("~"))
    except (OSError, ValueError):
        return False


def _git_targets(args):
    """(global dirs [(option, word)], subcommand, its args) of a git call."""
    dirs, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "--git-dir", "--work-tree") and i + 1 < len(args):
            dirs.append((a, args[i + 1]))
            i += 2
        elif a.startswith(("--git-dir=", "--work-tree=")):
            dirs.append(tuple(a.split("=", 1)))
            i += 1
        elif a in _GIT_GLOBAL_VALUE and i + 1 < len(args):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return dirs, a, args[i + 1:]
    return dirs, None, []


def _positionals(args, value_opts):
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return out + args[i + 1:]
        if a.startswith("-"):
            i += 2 if a in value_opts else 1
            continue
        out.append(a)
        i += 1
    return out


def _recursive(args):
    """Does an rm call remove folders (-r, -R, --recursive, or a short cluster holding r or R)?"""
    for a in args:
        if a == "--":
            return False
        if a == "--recursive" or re.match(r"^-[a-zA-Z]*[rR][a-zA-Z]*$", a):
            return True
    return False


def _collapses(word, names):
    """Would WORD, with NAMES empty, name the root, the home folder or a folder above it, every file in one of them,
    or everything in the working folder?"""
    w = word
    for n in names:
        w = re.sub(r"\$\{%s[^}]*\}|\$%s(?!\w)" % (n, n), "", w)
    w = re.sub(r"^(?:\$\{HOME\}|\$HOME)(?=/|$)", "~", w)
    if not w:
        return False   # no path at all (`rm -rf $T`), or an empty one (`rm -rf "$T"`): nothing is removed
    if _whole_folder(w):
        return True    # *, ., ./*, .[!.]*: everything in the working folder
    w = re.sub(r"(?:/\*|/\.?)+$", "", w) or "/"   # a folder's contents count as the folder
    if "$" in w or "__OFSUB" in w or "*" in w:
        return False
    w = _expanduser(w)
    if not os.path.isabs(w):
        return False
    p, home = os.path.normpath(w), os.path.realpath(_expanduser("~"))
    return p == "/" or home == p or home.startswith(p.rstrip("/") + "/") or \
        _expanduser("~") == p or _expanduser("~").startswith(p.rstrip("/") + "/")


def _target_hit(what, names, subject, home=None, after_cd=()):
    if what == "rm -r" and (home if home is not None else _collapses(subject, names)):
        what = "rm -r home"
    reason = UNSET_TARGET_DENY.format(what="rm -r" if what == "rm -r home" else what,
                                      names=", ".join(sorted(set(names))))
    if what == "rm -r home":
        reason = reason.replace("may be unset or empty when it runs", "may be unset or empty when it runs, and then "
                                "names the root, the home folder or a folder above it, or everything in the working "
                                "folder", 1)
    if after_cd:
        reason = reason.replace("whose target rests on", "of the working folder after a cd whose target rests on", 1)
    mode = UNSET_TARGET_MODE.get(what, "deny") if isinstance(UNSET_TARGET_MODE, dict) else UNSET_TARGET_MODE
    if mode == "deny":
        return reason, subject
    note = (reason, subject, "For now this warns and the call ran; it will deny once these flows have moved")
    if note not in _WARNINGS:
        _WARNINGS.append(note)
    return None


def targets_deny(cmd, cwd=None, env=None):
    """G26, G27: (reason, subject) for a Bash command that would make the home folder a git repository, commit into
    one, or let a variable that may be unset or empty decide what `cd`, `rm -r` or git acts on; or None."""
    env = os.environ if env is None else env
    known = {k for k, v in env.items() if v}
    try:
        return _targets(cmd, cwd, known, set(), {}, False, known, 0)
    except Exception:  # noqa: BLE001 - a parser slip never denies a call (the replay: 0 errors in 276,401 commands)
        return None


_SUB_MARK = re.compile(r"__OFSUB(\d+)__")


def _targets(cmd, here, known, empty, values, nounset, exported, depth, bodies=None):
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_targets"
        return None
    text, subs, docs, _ = _scan(cmd)
    bodies = dict(bodies or {})
    numbered = list(range(len(bodies), len(bodies) + len(subs)))
    bodies.update(zip(numbered, subs))
    marks = iter(numbered)
    text = re.sub(r"__OFSUB__", lambda _m: f"__OFSUB{next(marks)}__", text)  # each $( ) is read where it runs
    for _quoted, body, opener in docs:  # a heredoc a shell runs: a new shell, with what is exported
        if _head(opener) in _SHELLS:
            hit = _targets(body, here, set(exported), set(), {}, False, exported, depth + 1)
            if hit:
                return hit
    ops = []
    pipes = _split(text, ops)
    ends, term = [None] * len(pipes), None   # what ends each pipeline's and-or list (a '&' backgrounds it)
    for i in range(len(pipes) - 1, -1, -1):
        ends[i] = term
        if ops[i] in (None, ";", "\n", "&"):
            term = ops[i]
    base, chain, empty, values, exported = set(known), set(), set(empty), dict(values), set(exported)
    loose_cd, prev_test, opaque = [], None, False
    # the open if and loop blocks, innermost last: each holds what its condition asserted (know), what its current
    # branch set (cur), whether that branch ended in exit or return, and its finished branches. At fi, what every branch
    # that goes on set holds after it; with no else, the branch where the condition failed counts, knowing what its
    # test denied (of_review and of_secrets-review on organic-framework PR 223)
    frames = []

    def close_branch(f):
        f["done"].append((f["cur"] | f["know"], f["exited"]))
        f.update(cur=set(), know=set(), exited=False)

    for i, pipe in enumerate(pipes):
        words0 = pipe[0][1].split() if pipe[0][0] == "cmd" else []
        kws = []
        for w in words0:
            if w not in ("if", "then", "elif", "else", "fi", "do", "done", "while", "until"):
                break
            kws.append(w)
        first = kws[0] if kws else (words0[0] if words0 else "")
        op = ops[i]
        settled = set()   # what a fi on this line leaves set after it
        for kw in kws:
            f = frames[-1] if frames else None
            if kw == "if":
                frames.append({"kind": "if", "know": set(), "cur": set(), "exited": False, "done": [], "else": False,
                               "conds": 1, "neg": set(), "start": i})
            elif kw == "then" and f and f["kind"] == "if":
                # what the test denied holds where it failed only when the condition is that one test: no && or ||
                # in it, no ! (of_secrets-review on PR 223: `if c && [ -z "$T" ]` fails with T set or not)
                single = prev_test and f["conds"] == 1 and f.get("start") == i - 1
                f.update(know=set(chain), cur=set(), exited=False, neg=set(prev_test[1]) if single else set())
            elif kw == "elif" and f and f["kind"] == "if":
                close_branch(f)
                f["conds"] += 1
            elif kw == "else" and f and f["kind"] == "if":
                close_branch(f)
                f.update(know=set(f["neg"]) if f["conds"] == 1 else set(), **{"else": True})
            elif kw == "fi" and f and f["kind"] == "if":
                close_branch(f)
                frames.pop()
                if not f["else"]:   # the condition failed: what its test denied holds there
                    f["done"].append((set(f["neg"]) if f["conds"] == 1 else set(), False))
                going = [held for held, exited in f["done"] if not exited]
                if going and ends[i] != "&":   # `fi &`: the whole if ran in the background
                    settled |= set.intersection(*going)
            elif kw == "do":
                frames.append({"kind": "loop", "know": set(chain), "cur": set(), "exited": False, "done": [],
                               "else": False, "conds": 0, "neg": set()})
            elif kw == "done" and f and f["kind"] == "loop":
                frames.pop()
        if first in ("then", "do"):        # runs when the condition before it held: as after &&
            op = "&&"
        elif first in ("else", "elif"):    # runs when it did not: as after ||
            op = "||"
        head_of_list = op in (None, ";", "\n", "&")
        if head_of_list or op == "||":
            chain = set()
        if settled:
            (frames[-1]["cur"] if frames else base).update(settled)
        now = base | chain | set().union(*(f["know"] | f["cur"] for f in frames))
        counts = head_of_list or op == "&&"            # what this pipeline sets holds for the rest of its && chain
        # ... and for the lines after it; inside a then, do or else block, until the block ends (a block may not run)
        persists = (head_of_list or first in ("then", "do", "else")) and ends[i] != "&" and len(pipe) == 1
        keep = frames[-1]["cur"] if frames else base
        sets, asserts, unsets, checked, loops, negs, is_test, exits = {}, set(), set(), set(), set(), set(), False, False
        if op == "||" and prev_test:
            chain = set(prev_test[1])      # `[ -z "$T" ] || …`: runs only when T is not empty
            now = base | chain | set().union(*(f["know"] | f["cur"] for f in frames))
        for kind, stext in pipe:
            if kind == "sub":
                hit = _targets(stext, here, now, empty, values, nounset, exported, depth + 1, bodies)
                if hit:
                    return hit
                continue
            try:
                toks = _tokens(stext)
            except ValueError:
                continue
            here_checked = set(_CHECKED.findall(stext))
            checked |= here_checked
            now, empty = now | here_checked, empty - here_checked
            for k in _SUB_MARK.findall(stext):   # a $( ) runs with the variables of where it stands
                if int(k) in bodies:
                    hit = _targets(bodies[int(k)], here, now, empty, values, nounset, exported, depth + 1, bodies)
                    if hit:
                        return hit
            if toks[:1] in (["for"], ["select"]) and len(toks) >= 3 and toks[2] == "in" and toks[3:]:
                loops.add(toks[1])         # a loop's body runs only with a word in its variable
                continue
            s = _simple(stext)
            if not s:
                sets.update(_assignments(_LEAD_KEYWORDS.sub("", stext)))   # `do T=x;`: the words after do
                continue
            if s[0] == "<read":
                continue
            head, args = s[0], s[1]
            pre = _assignments(stext)      # `T=x cmd $T`: the shell expands $T before T=x applies
            if head in ("cd", "pushd"):
                pos = args[args.index("--") + 1:] if "--" in args else \
                    [a for a in args if not re.match(r"^-[LPe@]+$", a)]
                word = pos[0] if pos else "~"
                bad = _loose(word, now, empty, nounset or opaque)
                if bad:
                    hit = _target_hit("cd", bad, word)
                    if hit:
                        return hit
                here = MAYBE_HOME if bad else None if word == "-" else _resolve_dir(word, here, values)
                loose_cd = bad
            elif head == "rm" and _recursive(args):
                for a in _positionals(args, ()):
                    bad = _loose(a, now, empty, nounset or opaque)
                    strict = _loose(a, now, empty, nounset) if opaque else bad   # a sourced file may not set it
                    filled = _with_defaults(a, now)
                    home = bool(strict or bad or filled != a) and _collapses(filled, sorted(set(strict) | set(bad)))
                    if not bad and not home and here == MAYBE_HOME and _whole_folder(a):
                        return _target_hit("rm -r", loose_cd, a, home=True, after_cd=loose_cd)
                    if bad or home:
                        hit = _target_hit("rm -r", sorted(set(bad) | set(strict)) or
                                          [m.group(1) for m in re.finditer(r"\$\{([A-Za-z_]\w*)", a)], a, home=home)
                        if hit:
                            return hit
                        break
            elif head == "git":
                dirs, sub_cmd, rest = _git_targets(args)
                words = [w for _, w in dirs]
                sep = _opt_word(rest, "--separate-git-dir")
                gitdirs = [w for o, w in dirs if o == "--git-dir"] + ([pre["GIT_DIR"]] if pre.get("GIT_DIR") else []) + \
                    ([sep] if sep else [])
                words += gitdirs[len([o for o, _ in dirs if o == "--git-dir"]):]
                if sub_cmd == "init":
                    words += _positionals(rest, _GIT_INIT_VALUE)[:1]
                elif sub_cmd == "clone":
                    words += _positionals(rest, _GIT_CLONE_VALUE)[1:2]
                bad = sorted({n for w in words for n in _loose(w, now, empty, nounset or opaque)})
                if bad:
                    hit = _target_hit("git", bad, " ".join(words))
                    if hit:
                        return hit
                where = here
                for o, w in dirs:
                    if o == "-C":
                        where = _resolve_dir(w, where, values) if w else where
                if sub_cmd in ("init", "clone", "commit") and \
                        any(_home_git(_resolve_dir(w, where, values)) for w in gitdirs):   # its git directory there
                    return _home_repo(where, loose_cd), f"git {sub_cmd}"
                if sub_cmd == "init":
                    pos = _positionals(rest, _GIT_INIT_VALUE)
                    target = _resolve_dir(pos[0], where, values) if pos else where
                    if _is_home(target) or ("--bare" in rest and _home_git(target)):
                        return _home_repo(where, loose_cd), "git init"
                elif sub_cmd == "clone":
                    pos = _positionals(rest, _GIT_CLONE_VALUE)
                    target = _resolve_dir(pos[1], where, values) if len(pos) > 1 else None
                    if _is_home(target) or ({"--bare", "--mirror"} & set(rest) and _home_git(target)):
                        return _home_repo(where, loose_cd), "git clone"
                elif sub_cmd == "commit" and where is not None:
                    if (_is_home(where) and (where != MAYBE_HOME or os.path.isdir(_expanduser("~/.git")))) or \
                            (where != MAYBE_HOME and _is_home(os.path.dirname(_git_dir(where) or "/"))):
                        return _home_repo(where, loose_cd), "git commit"
            elif head in ("export", "declare", "typeset", "local", "readonly"):
                for a in args:
                    m = re.match(r"^([A-Za-z_]\w*)(?:=(.*))?$", a, re.S)
                    if m:
                        if m.group(2) is not None:
                            sets[m.group(1)] = m.group(2)
                        if head == "export":
                            exported.add(m.group(1))
            elif head == "unset":
                unsets |= {a for a in args if re.match(r"^[A-Za-z_]\w*$", a)}
            elif head == "read":
                for a in _positionals(args, _READ_VALUE):
                    if re.match(r"^[A-Za-z_]\w*$", a):
                        loops.add(a)       # set in this pipeline's shell, a loop body included
            elif head in (".", "source", "eval") and counts:
                opaque = True              # it may set any name: cd and git trust it, an rm -r that would collapse does not
            elif head == "exit":   # `return` outside a function fails, and the line goes on
                exits = True
            elif head in ("[", "[[", "test") and "!" not in toks and "-o" not in args:
                is_test = True     # with a ! (before or inside) or an -o, it asserts nothing either way
                ws = [a for a in args if a not in ("]", "]]")]
                for k, a in enumerate(ws):
                    if (a in _TEST_SET and k + 1 < len(ws)) or len(ws) == 1:
                        target = ws[k + 1] if len(ws) > 1 else a
                        asserts |= {mm.group(1) or mm.group(3) for mm in _EXPANSION.finditer(target)}
                        break
                    if a == "-z" and k + 1 < len(ws):
                        if "-a" not in ws:   # `[ -z "$T" -a -d x ]` also fails with T empty (of_secrets-review, PR 223)
                            negs |= {mm.group(1) or mm.group(3) for mm in _EXPANSION.finditer(ws[k + 1])}
                        break
            elif head == "set" and any(a == "-u" or re.match(r"^-[a-zA-Z]*u", a) for a in args) or \
                    (head == "set" and "nounset" in args):
                if head_of_list:
                    nounset = True
            if head in _SHELLS:
                for k, a in enumerate(args[:-1]):
                    if a == "-c" or re.match(r"^-[a-zA-Z]*c$", a):
                        inner = exported | {n for n, v in pre.items() if v}
                        if not re.search(r"-[a-zA-Z]*c\s+'", stext):   # "…" or bare: this shell expands it first
                            inner |= now
                        hit = _targets(args[k + 1], here, inner, set(), {}, False, inner, depth + 1, bodies)
                        if hit:
                            return hit
                        break
        for name in unsets:
            for held in (base, chain, *(f["cur"] for f in frames), *(f["know"] for f in frames)):
                held.discard(name)
            values.pop(name, None)
        for name in loops:
            empty.discard(name)
            base.add(name)
            values.pop(name, None)
        for name in checked:
            empty.discard(name)
            if counts:
                chain.add(name)
            if persists:
                keep.add(name)
        for name, val in sets.items():
            if val == "" or (val and not _EXPANSION.sub("", val) and _loose(val, now, empty, nounset)):
                empty.add(name)       # set empty, or to what may be empty
                for held in (base, chain, *(f["cur"] for f in frames), *(f["know"] for f in frames)):
                    held.discard(name)
                continue
            empty.discard(name)
            if "$" not in val and "__OFSUB" not in val and "`" not in val:
                values[name] = val
            if counts:
                chain.add(name)
            if persists:
                keep.add(name)
        if counts or len(pipe) > 1:
            chain |= asserts
        if exits and prev_test and ends[i] != "&":   # `[ -n "$T" ] || exit 1;` and `[ -z "$T" ] && exit;`: past it, T is set
            keep |= prev_test[0] if op == "||" else prev_test[1] if op == "&&" else set()
        if exits and frames and persists:   # a branch that ends here goes on nowhere: it settles nothing at its fi
            frames[-1]["exited"] = True
        prev_test = (asserts, negs) if is_test and len(pipe) == 1 else None
    return None


# G30: a pattern the machine's sed or awk reads otherwise than it is written. macOS's sed and awk take \s, \S, \d, \D,
# \w and \W as the letters themselves, so a mask written with them does nothing, silently: on 2026-11-17 a key pair
# printed into a transcript through `sed -E` that was meant to mask it (a user organization's security keeper, through
# its structure keeper, msg 01a1e8ec-f1aa). GNU sed and awk read \s, \S, \w and \W, not \d or \D.
_ESCAPE_ANY = re.compile(r"(?<!\\)\\[sSdDwW]")
_ESCAPE_GNU = re.compile(r"(?<!\\)\\[dD]")
_FLAVOURS = {}  # the command word -> "gnu" or "bsd", once per process


_SYSTEM_BIN = ("/usr/bin/", "/bin/")  # the system's own programs: macOS keeps /usr/bin read-only, Linux root-owned


def _flavour(word):
    """'gnu' or 'bsd' for a sed or awk command word, else None. The guard never runs a program the command names
    (of_secrets-review on organic-framework PR 283: `<folder>/sed --version`, run here to tell the flavour, ran an
    agent's own script before any decision): `gsed` and `gawk` are GNU's by name, a word that resolves (by real path,
    PATH looked up without running anything) to a program in /usr/bin or /bin is asked its --version there, one
    installed as Homebrew's gnu-sed or gawk is GNU's by its path, and anything else is read as BSD's, the cautious
    reading."""
    name = os.path.basename(word)
    if name in ("gsed", "gawk"):
        return "gnu"
    if name not in ("sed", "awk"):
        return None
    if word not in _FLAVOURS:
        import shutil
        found = word if "/" in word else shutil.which(name)
        real = os.path.realpath(found) if found else ""
        out = ""
        if real.startswith(_SYSTEM_BIN) and os.path.basename(real) in ("sed", "awk", "gawk", "mawk", "nawk", "gsed"):
            import subprocess
            try:
                out = subprocess.run([real, "--version"], capture_output=True, text=True, timeout=3).stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
        elif re.search(r"/Cellar/(?:gnu-sed|gawk)/", real):
            out = "GNU"
        _FLAVOURS[word] = "gnu" if "GNU" in out else "bsd"
    return _FLAVOURS[word]


def escape_deny(cmd):
    """G30: (reason, subject) for a sed or awk run on this machine whose arguments hold an escape it reads as a letter,
    else None. Read through command_findings, so a command another machine runs (ssh, an exec) is not judged."""
    if "\\" not in cmd or ("sed" not in cmd and "awk" not in cmd):
        return None
    try:
        found = command_findings(cmd)
    except Exception:  # noqa: BLE001 - unread is not denied here
        return None
    for kind, word, args in found:
        if kind != "word" or os.path.basename(str(word)) not in ("sed", "awk", "gsed", "gawk"):
            continue
        hits = [m.group(0) for a in (args or []) for m in _ESCAPE_ANY.finditer(str(a))]
        if not hits:
            continue
        fl = _flavour(str(word))
        hits = hits if fl == "bsd" else [h for h in hits if _ESCAPE_GNU.fullmatch(h)]
        if hits:
            name = os.path.basename(str(word))
            return (f"{ESCAPE_DENY} ({name} takes {hits[0]} as the letter {hits[0][1]}, so a mask or a match written "
                    "with it silently does nothing): write [[:space:]] for \\s, [0-9] for \\d and [[:alnum:]_] for "
                    "\\w, and mask a secret only in a committed script (docs/guard.md G30)"), f"{name} {hits[0]}"
    return None


def critical_deny(tool, tool_input, cwd=None):
    """(reason, subject) for a call to deny, or None. Examples (.env.example, *.pub) stay allowed."""
    ti = tool_input or {}
    _WARNINGS.clear()
    _REMOTE_BODY[0] = 0
    _LOOPS[0] = {}
    _READINGS_SPENT[0] = 0
    _LINE_LINKS.clear()
    _REDIR_CTX[0] = None
    _CAPPED[0] = None
    _READS[0] = 0
    _LINE_REMOTE[0] = tool == "Bash" and _remote_line(ti.get("command") or "")
    if tool == "Bash":
        cmd = ti.get("command") or ""
        _DENY_CWD[0] = cwd
        try:
            hit = _actor_line_deny(cmd) or _bash_deny(cmd)
        except Exception:  # a parser slip must not deny every command: the old, stricter text match
            bare = _strip_quoted(cmd)
            m = CRITICAL_DENY_BASH.search(bare)
            hit = _prints(m.group(0).strip(" ;&|(")) if m else _reads(READERS.search(bare) and _loose_secret_path(bare))
        if not hit:
            hit = targets_deny(cmd, cwd)  # G26, G27
        if not hit:
            hit = escape_deny(cmd)  # G30
        if not hit and _CAPPED[0]:  # G0.11: a reader stopped at its depth cap, so what runs there is unread
            if DEPTH_CAP_MODE == "deny":
                hit = (DEPTH_CAP_DENY,)
            else:
                _WARNINGS.append((DEPTH_CAP_DENY, cmd, None))
        return (hit[0], cmd) if hit else None
    path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    _DENY_CWD[0] = cwd
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and _grant_file(path):
        return GRANT_DENY, path
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and _in_hooks(path, cwd):
        return HOOKS_DENY, path
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and _in_plugin_code(path, cwd):
        return PLUGINS_DENY, path
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and _protected_code(path, cwd):
        return PROTECTED_CODE_DENY, path
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and _guard_state_file(path, cwd):
        return GUARD_STATE_DENY, path
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob") and path and \
            _in_folders(str(path), {_forward_dir().casefold()}, cwd):  # G34, as G35 reads a path
        return FORWARD_DENY, path
    if tool == "Glob" and ti.get("pattern") and _in_folders(  # G34: the pattern, from its folder (PR 397)
            os.path.join(str(ti.get("path") or cwd or "."), str(ti["pattern"])), {_forward_dir().casefold()}, cwd):
        return FORWARD_DENY, str(ti["pattern"])
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and _launch_file(path, cwd):
        return LAUNCH_DENY, path
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and _in_session_index(path, cwd):  # G36
        if SESSION_INDEX_MODE == "deny":
            return SESSION_INDEX_DENY, path
        _WARNINGS.append((SESSION_INDEX_DENY, path, "a warning now, a deny once the replay shows no agent writes it"))
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and _in_outbox(path, cwd):  # G35
        if ASKS_OUTBOX_MODE == "deny":
            return ASKS_OUTBOX_DENY, path
        _WARNINGS.append((ASKS_OUTBOX_DENY, path, "a warning now, a deny once the flows that write it have moved"))
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path and \
            _launch_file(path, cwd, _owner_config_targets()):  # G33
        if OWNER_CONFIG_MODE == "deny":
            return OWNER_CONFIG_DENY, path
        _WARNINGS.append((OWNER_CONFIG_DENY, path, "a warning now, a deny once the flows that write it have moved"))
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep") and _secret_path(path):
        return f"a secret file ({os.path.basename(path)})", path
    if tool in ("Read", "Edit", "MultiEdit", "Grep") and path and _key_file(_local(path, cwd)):  # G1.13
        if KEY_READ_MODE["content"] == "deny":
            return f"a secret file ({os.path.basename(path)}): a private key by its content", path
        _WARNINGS.append((f"a secret file ({os.path.basename(path)}): a private key by its content", path, KEY_NOTE))
    if tool in ("Read", "Grep") and _memory_path(path):
        return f"memory or a core dump ({path})", path
    return None



_MENTION = re.compile(r"(?:^|[\s(])@([^\s@)<>]+)")


def prompt_secret_mention(prompt):
    """A secret path named as an @file mention in a prompt. Claude Code reads an @-mentioned file into the
    session (today a relayed message with @…/grafana-upsilon.token pulled the token in), so a prompt that
    names a secret path is a leak vector no tool-call deny rule sees at submit time. Returns the path or None."""
    for m in _MENTION.finditer(prompt or ""):
        p = m.group(1).rstrip(".,;:!?\"'`")
        if _secret_path(p):
            return p
    return None


# ------------------------------------------------------------------ kubectl calls (organization rules, O)
# What a kubectl call reaches and with which credential, for an organization's own rules (docs/guard.md, O).
# A user organization asked on 2026-11-08 (msg 01a1bd17) to deny exec into its CI runner pods and the cluster-admin
# kubeconfig for most of its positions. The rules are the organization's signed deny grants (docs/registry.md,
# Organization rules; of_architect, 4a14716); of_grants.guard_rules reads them, the hook applies them. The dict keys
# avoid the word "kubeconfig": a literal naming it reads as a secret path to G8 in an agent's `python -c`.

KUBE_DEFAULT_CONFIG = "~/.kube/config"
_KUBE_LOCAL = {"config", "completion", "help", "kustomize", "plugin", "options"}  # never reach the cluster
_KUBE_INTO_POD = {"exec", "attach", "cp", "debug", "port-forward"}


def _norm_path(p):
    p = re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", str(p).strip())
    return os.path.normpath(_expanduser(p))


def _assignments(stext):
    """NAME=value words before the command word of one simple command (`KUBECONFIG=x kubectl`, `env K=x kubectl`)."""
    out = {}
    try:
        toks = _tokens(stext)
    except ValueError:
        return out
    for w in toks:
        m = re.match(r"^([A-Za-z_]\w*)=(.*)$", w, re.S)
        if m:
            out[m.group(1)] = m.group(2)
        elif os.path.basename(w) in _WRAPPERS | {"env", "timeout"} or w.startswith("-") or w.isdigit():
            continue
        else:
            break
    return out


_KIND_ALIAS = {"po": "pods", "pod": "pods", "deploy": "deployments", "deployment": "deployments", "svc": "services",
               "service": "services", "sts": "statefulsets", "statefulset": "statefulsets", "ds": "daemonsets",
               "daemonset": "daemonsets", "cm": "configmaps", "configmap": "configmaps", "ns": "namespaces",
               "namespace": "namespaces", "no": "nodes", "node": "nodes", "pvc": "persistentvolumeclaims",
               "persistentvolumeclaim": "persistentvolumeclaims", "pv": "persistentvolumes",
               "persistentvolume": "persistentvolumes", "ing": "ingresses", "ingress": "ingresses", "job": "jobs",
               "cj": "cronjobs", "cronjob": "cronjobs", "rs": "replicasets", "replicaset": "replicasets",
               "ep": "endpoints", "sa": "serviceaccounts", "serviceaccount": "serviceaccounts", "secret": "secrets",
               "ev": "events", "event": "events", "csr": "certificatesigningrequests", "crd": "customresourcedefinitions",
               "hpa": "horizontalpodautoscalers", "netpol": "networkpolicies", "pdb": "poddisruptionbudgets",
               "sc": "storageclasses", "role": "roles", "rolebinding": "rolebindings", "clusterrole": "clusterroles",
               "clusterrolebinding": "clusterrolebindings"}
_KUBE_SUBVERB = {"rollout", "set", "auth", "certificate", "config", "plugin", "apply"}
# what `kubectl get all` lists
_KUBE_ALL = {"pods", "services", "deployments", "replicasets", "statefulsets", "daemonsets", "jobs", "cronjobs",
             "horizontalpodautoscalers", "replicationcontrollers"}
_KUBE_CLUSTER = {"nodes", "namespaces", "persistentvolumes", "storageclasses", "clusterroles", "clusterrolebindings",
                 "customresourcedefinitions", "certificatesigningrequests", "priorityclasses", "runtimeclasses",
                 "volumeattachments", "csidrivers", "csinodes", "mutatingwebhookconfigurations",
                 "validatingwebhookconfigurations", "apiservices", "ingressclasses", "(none)"}
_KUBE_KNOWN = set(_KIND_ALIAS.values()) | _KUBE_ALL | _KUBE_CLUSTER | {
    "limitranges", "resourcequotas", "podtemplates", "leases", "endpointslices", "controllerrevisions",
    "networkpolicies", "poddisruptionbudgets", "tokenreviews", "subjectaccessreviews", "bindings", "componentstatuses"}
# groups whose kinds are the API's own: a version and one of these is stripped (`deployments.v1.apps` is deployments)
_KUBE_BUILTIN_GROUPS = {"", "core", "apps", "batch", "autoscaling", "policy", "networking.k8s.io",
                        "rbac.authorization.k8s.io", "storage.k8s.io", "apiextensions.k8s.io", "certificates.k8s.io",
                        "coordination.k8s.io", "discovery.k8s.io", "events.k8s.io", "node.k8s.io",
                        "scheduling.k8s.io", "admissionregistration.k8s.io", "apiregistration.k8s.io",
                        "flowcontrol.apiserver.k8s.io", "resource.k8s.io", "authentication.k8s.io",
                        "authorization.k8s.io"}
_KUBE_SINGULAR = {"networkpolicy": "networkpolicies", "limitrange": "limitranges", "resourcequota": "resourcequotas",
                  "lease": "leases", "endpointslice": "endpointslices", "priorityclass": "priorityclasses",
                  "ingressclass": "ingressclasses", "apiservice": "apiservices"}


def fold_kind(kind):
    """(the kind as O1 compares it, whether it is known): case, alias and singular folded, a version and a group of
    the API's own stripped (`Secret`, `secret`, `secrets.v1` and `secrets.v1.` are secrets). A custom resource's
    plural keeps its group (`externalsecrets.external-secrets.io` is not secrets), and counts as known, since its
    group says what it is (of_architect, msg 01a1d7b2-a99a)."""
    k = str(kind).strip().lower().rstrip(".")
    name, _, rest = k.partition(".")
    parts = [p for p in rest.split(".") if p] if rest else []
    if parts and re.fullmatch(r"v\d+(?:(?:alpha|beta)\d+)?", parts[0]):
        parts = parts[1:]
    group = ".".join(parts)
    name = _KUBE_SINGULAR.get(name, _KIND_ALIAS.get(name, name))
    if group in _KUBE_BUILTIN_GROUPS:
        return name, name in _KUBE_KNOWN
    return f"{name}.{group}", True


def _kind_matches(want, have):
    """A rule's folded kind against a call's: equal, or the same plural where the call names no group (calls rarely
    do: `get externalsecrets` is externalsecrets.external-secrets.io's)."""
    return want == have or ("." in want and "." not in have and want.split(".", 1)[0] == have)
_KUBE_POD_VERBS = {"exec", "attach", "cp", "logs", "port-forward", "debug"}


def _kube_kinds(verb, pos, cut):
    """(the verb with its subcommand, [resource kinds]) of a kubectl call, for sizing a Role: `get pods,svc` ->
    pods and services; `logs deploy/x` -> deployments; `exec x` -> pods; `apply -f f` -> (from file); a kind held
    in a variable -> unparsed. Names are never kept."""
    sub = pos[1] if verb in _KUBE_SUBVERB and len(pos) > 1 and not (verb == "apply" and pos[1] != "view-last-applied") \
        else None
    label, rest = (f"{verb} {sub}", pos[2:]) if sub else (verb, pos[1:])
    raw = _kube_raw(cut)
    if raw is not None:
        return label, [raw[1] if raw[1] in ("unparsed", "(none)") else fold_kind(raw[1])[0]]
    if verb == "run":
        return label, ["pods"]
    if verb == "create" and rest[:1] == ["token"]:
        return label, ["serviceaccounts"]
    if verb in _KUBE_POD_VERBS:
        tgt = next((a for a in rest if ":" in a), "") if verb == "cp" else (rest[0] if rest else "")
        if verb != "cp" and "/" in tgt:  # `logs deploy/x`, `port-forward svc/x`: through that kind to its pods
            first = tgt.split("/")[0]
            if first and "$" not in first and re.match(r"^[A-Za-z][\w.-]*$", first):
                return label, sorted({fold_kind(first)[0], "pods"})
            return label, ["unparsed"]
        else:
            return label, ["pods"] if tgt else ["unparsed"]
    elif verb in ("cordon", "uncordon", "drain"):
        return label, ["nodes"]
    elif verb == "auth":
        return label, ["(none)"]  # `auth can-i get pods` asks about access; it reads no pods
    elif rest:
        first = rest[0].split("/")[0] if "/" in rest[0] else rest[0]
    else:
        return label, ["(from file)" if any(a in ("-f", "--filename", "-k", "--kustomize") or
                                            a.startswith(("--filename=", "-f=", "--kustomize="))
                                            for a in cut) else "(none)"]
    if not first or "$" in first or "__OFSUB__" in first or not re.match(r"^[A-Za-z][\w.,-]*$", first):
        return label, ["unparsed"]
    return label, [fold_kind(k)[0] for k in first.split(",") if k]


def _kubectl_call(args, env):
    cut = args[:args.index("--")] if "--" in args else args  # after `--` is the command run inside the pod
    pos = _positional(cut, _KUBECTL_VALUE)
    verb = pos[0] if pos else None
    cfg, src = _opt_value(cut, ("--kubeconfig",)), "--kubeconfig"
    if not cfg and env.get("KUBECONFIG"):
        cfg, src = env["KUBECONFIG"].split(":")[0], "KUBECONFIG"
    if not cfg:
        cfg, src = KUBE_DEFAULT_CONFIG, "default"
    if verb == "cp":
        targets = [a.split(":", 1)[0].rsplit("/", 1)[-1] for a in pos[1:] if ":" in a]
    elif verb in _KUBE_INTO_POD and len(pos) > 1:
        targets = [pos[1].rsplit("/", 1)[-1]]
    else:
        targets = []
    local = verb is None or verb in _KUBE_LOCAL or any(a in ("-h", "--help") for a in cut) or \
        (verb == "version" and any(a.startswith("--client") for a in cut))
    label, kinds = _kube_kinds(verb, pos, cut) if verb else (None, [])
    run = args[args.index("--") + 1:] if verb == "exec" and "--" in args else None  # what runs in the pod
    namespace = _opt_value(cut, ("-n", "--namespace")) or (_kube_raw(cut) or (None,))[0]
    return {"verb": verb, "targets": targets, "namespace": namespace, "exec_argv": run,
            "all_namespaces": any(a in ("-A", "--all-namespaces") for a in cut), "verb_label": label, "kinds": kinds,
            "config": _norm_path(cfg), "config_from": src, "context": _opt_value(cut, ("--context",)),
            "user": _opt_value(cut, ("--user",)), "local": local}


def kubectl_calls(cmd, env=None, depth=0):
    """Every kubectl call the local shell runs for a command line, as dicts: verb; targets (the pod names it enters:
    the first argument of exec, attach, debug and port-forward, the `pod:path` of cp); namespace; config (the
    kubeconfig in effect, a normalized path) and config_from (`--kubeconfig`, `KUBECONFIG`, `default`); context and
    user when named with --context/--user; local (a verb that never reaches the cluster). `env` is the session's
    environment. Followed: wrappers, assignments, `export`/`unset KUBECONFIG` earlier on the line, $( ), subshells,
    sh -c, eval, a heredoc read by a shell. Not followed: ssh and docker/kubectl exec, which run with another
    machine's or container's kubeconfig. Prose a command only carries (`of send "…"`, `git commit -m "…"`) is not
    a call."""
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "kubectl_calls"
        return []
    env = {k: v for k, v in (env or {}).items() if v}
    text, subs, docs, _ = _scan(cmd)
    out = []
    for sub in subs:
        out += kubectl_calls(sub, env, depth + 1)
    for _quoted, body, opener in docs:
        if _head(opener) in _SHELLS:
            out += kubectl_calls(body, env, depth + 1)
    try:
        pipes = _split(text)
    except ValueError:
        return out
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                out += kubectl_calls(stext, env, depth + 1)
                continue
            s = _simple(stext)
            if not s:
                bare = _assignments(stext)  # `KUBECONFIG=x` alone changes an exported variable only
                if "KUBECONFIG" in bare and "KUBECONFIG" in env:
                    env["KUBECONFIG"] = bare["KUBECONFIG"]
                continue
            if s[0] == "<read":
                continue
            head, args = s[0], s[1]
            if head == "export":
                for a in args:
                    if a.startswith("KUBECONFIG="):
                        env["KUBECONFIG"] = a.split("=", 1)[1]
                continue
            if head == "unset":
                if "KUBECONFIG" in args:
                    env.pop("KUBECONFIG", None)
                continue
            here = dict(env, **{k: v for k, v in _assignments(stext).items() if k == "KUBECONFIG"})
            if head == "kubectl":
                out.append(_kubectl_call(args, here))
            elif head in _SHELLS - {"eval"} and "-c" in args[:-1]:
                out += kubectl_calls(args[args.index("-c") + 1], here, depth + 1)
            elif head == "eval":
                out += kubectl_calls(" ".join(args), here, depth + 1)
    return out


def kubectl_rule_match(match, call):
    """What a kubectl matcher matched in one call, as a short phrase, or None. `match` has any of:
    verbs: [exec, …]            the call's verb is one of them;
    pods: ["runner-*"]          a target pod name matches a glob (a name held in a variable never matches);
    namespaces: [runners-jobs]
                                the call names one of these namespaces with -n/--namespace or in a --raw API path,
                                reads every namespace (-A), or names none and so relies on the kubeconfig's, which
                                the guard does not read, unless all its kinds are cluster-scoped (of_architect,
                                2026-11-14; a pod name held in a variable is still seen);
    kinds: [secrets, pods]      the call's resource kinds include one, both sides folded (`fold_kind`); `run`, `exec`
                                and the other verbs into a pod are pods, through another kind too (`logs deploy/x`);
                                `create token` is serviceaccounts; `get all` is the kinds it lists; a --raw path its
                                own kind; a kind held in a variable or read from a file (-f, -k) counts as a match:
                                a false positive over a miss (a user organization's signing-key Secrets, 2026-11-14);
    admin: {configs: [~/.kube/config], contexts: [...], users: [...]}
                                the call reaches the cluster (not a local verb) with one of these credentials:
                                the kubeconfig in effect (flag, KUBECONFIG, else the default), --context or --user.
    allow_exec: [{argv: [/usr/sbin/sshd, -i], pods: ["runner-0"]}]
                                an exec whose command after `--` is exactly one of these argv lists (into a pod
                                that matches the entry's pods, when given) is out of the rule; a command held in
                                a variable, or an exec without `--`, never matches (a user organization,
                                2026-11-11: two typed execs allowed into its runners, everything else flagged).
    Every other key given must match."""
    import fnmatch
    said = []
    verbs, pods, admin = match.get("verbs"), match.get("pods"), match.get("admin")
    if verbs is not None and call["verb"] not in verbs:
        return None
    if match.get("namespaces") is not None:
        if call["namespace"] in match["namespaces"]:
            said.append(f"kubectl {call['verb']} in namespace {call['namespace']}")
        elif call.get("all_namespaces"):
            said.append(f"kubectl {call['verb']} in every namespace (-A)")
        elif call["namespace"] is None and not set(call.get("kinds") or ["(none)"]) <= _KUBE_CLUSTER:
            # the kubeconfig's namespace, which the guard does not read: it may be one of these (of_architect)
            said.append(f"kubectl {call['verb']} with no -n, so the kubeconfig's namespace (give -n)")
        else:
            return None
    if match.get("kinds") is not None:
        want = {fold_kind(k)[0] for k in match["kinds"]}
        have = set(call.get("kinds") or [])
        if "all" in have:
            have |= _KUBE_ALL
        hit = sorted(h for h in have if any(_kind_matches(w, h) for w in want)) or \
            sorted(have & {"unparsed", "(from file)"})
        if not hit:
            return None
        said.append(f"kubectl {call['verb']} on {', '.join(hit)}")
    if pods is not None:
        hit = next((t for t in call["targets"] if any(fnmatch.fnmatchcase(t, str(g)) for g in pods)), None)
        if not hit:
            return None
        said.append(f"kubectl {call['verb']} into {hit}")
    if admin is not None:
        if call["local"]:
            return None
        if call["context"] and call["context"] in (admin.get("contexts") or []):
            said.append(f"kubectl --context {call['context']}")
        elif call["user"] and call["user"] in (admin.get("users") or []):
            said.append(f"kubectl --user {call['user']}")
        elif any(_norm_path(k) == call["config"] for k in admin.get("configs") or []):
            said.append({"default": "kubectl with no --kubeconfig or KUBECONFIG, so the default kubeconfig",
                         "KUBECONFIG": "kubectl with KUBECONFIG naming the admin kubeconfig"}.get(
                call["config_from"], "kubectl --kubeconfig naming the admin kubeconfig"))
        else:
            return None
    def allowed(a):
        return isinstance(a, dict) and list(a.get("argv") or []) == call["exec_argv"] and (
            a.get("pods") is None or any(fnmatch.fnmatchcase(t, str(g)) for t in call["targets"] for g in a["pods"]))
    if call["verb"] == "exec" and call.get("exec_argv") is not None and any(map(allowed, match.get("allow_exec") or [])):
        return None
    return "; ".join(said) or (f"kubectl {call['verb']}" if verbs is not None else None)


# ------------------------------------------------- organization rules: ssh destinations (O3)
# A user organization's interim control for a shared agent SSH key until per-position identities exist (msg
# 01a1d7b4-ac9f); of_architect's five conditions (msg 01a1d7b2-a99a's sibling, 01a1d7bd-cd8c): a false positive over a
# miss, aliases folded by reading the ssh config (never `ssh -G`, which runs Match exec), and the limits named.
_SSH_HEADS = {"ssh", "scp", "sftp", "rsync", "mosh", "sshfs", "autossh"}
_SSH_WORDS = re.compile(r"\b(?:ssh|scp|sftp|rsync|mosh|sshfs|autossh)\b")
_SSH_VALUE = {"-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-P", "-p", "-Q",
              "-R", "-S", "-W", "-w"}
_SSH_OPT_VALUE = {
    "ssh": _SSH_VALUE, "autossh": _SSH_VALUE | {"-M"},
    "scp": {"-c", "-D", "-F", "-i", "-J", "-l", "-o", "-P", "-S", "-X"},
    "sftp": {"-B", "-b", "-c", "-D", "-F", "-i", "-J", "-l", "-o", "-P", "-R", "-S", "-s", "-X"},
    "rsync": {"-e", "--rsh", "-T", "--temp-dir", "-f", "--filter", "--exclude", "--include", "--exclude-from",
              "--include-from", "--files-from", "--log-file", "--password-file", "--port", "--partial-dir",
              "--compare-dest", "--copy-dest", "--link-dest", "-B", "--block-size", "--bwlimit", "--max-size",
              "--min-size", "--timeout", "--contimeout", "--chmod", "--chown", "--usermap", "--groupmap", "--suffix",
              "--backup-dir", "-@", "--modify-window", "--out-format", "--info", "--debug", "--rsync-path",
              "--sockopts", "--outbuf", "--iconv", "--checksum-choice", "--compress-choice", "--compress-level",
              "--skip-compress", "--max-delete", "--stop-after", "--stop-at", "-M", "--remote-option"},
    "mosh": {"-p", "--port", "--ssh", "--server", "--client", "--predict", "--family", "--bind-server",
             "--experimental-remote-ip"},
    "sshfs": {"-o", "-p", "-F"},
}
_SSH_CONFIG_MAX = 1 << 20


def _ssh_host(word):
    """The host of `[user@]host[:port]`, `ssh://[user@]host[:port]/…`, `[v6]:path` or `host:path`, lowercased; None for
    a variable or a substitution (counted as a match by the caller)."""
    w = str(word)
    if "$" in w or "__OFSUB__" in w or "`" in w:
        return None
    m = re.match(r"^(?:ssh|sftp|scp|rsync)://(?:[^@/]*@)?(\[[^\]]+\]|[^:/?#]+)", w, re.I)
    if m:
        return m.group(1).strip("[]").lower()
    w = w.rsplit("@", 1)[-1] if "@" in w.split(":", 1)[0] or "@" in w.split("/", 1)[0] else w
    if w.startswith("["):
        return w[1:w.index("]")].lower() if "]" in w else w[1:].lower()
    return w.split(":", 1)[0].lower()


def _ssh_remote(word):
    """Is a scp/rsync/sshfs argument remote (`host:path`, `host::module`, `[v6]:path`, a URL)? A local path that
    holds a colon after a slash is not."""
    w = str(word)
    if re.match(r"^(?:ssh|sftp|scp|rsync)://", w, re.I):
        return True
    if w.startswith("[") and "]:" in w:
        return True
    head = w.split(":", 1)[0]
    return ":" in w and head != "" and "/" not in head and not w.startswith((".", "~"))


def _ssh_opts(value):
    """(key, value) of an ssh `-o` value: `Key=Value` or `Key Value`, key lowercased."""
    m = re.match(r"^\s*(\w+)\s*(?:=\s*|\s+)(.*)$", str(value))
    return (m.group(1).lower(), m.group(2).strip().strip("\"'")) if m else (str(value).lower(), "")


def _ssh_forward_target(spec):
    """The host a -L or -R forward connects to, read as OpenSSH's parse_fwd_field reads it: fields split on `:`, a
    bracketed `[v6]` kept whole, a field holding `/` a socket path. `[bind:]port:host:hostport` and `/socket:host:port`
    name the host before the last field; a last field that is a path, a dynamic forward or `port:/socket` name none;
    the old `port/host/hostport` form names its middle (of_review on organic-framework PR 165: `-L /tmp/s:gw:22`, a
    socket on one side and gw on the other, was missed)."""
    v = str(spec).strip()
    parts = re.findall(r"\[[^\]]*\]|[^:]+", v)
    if len(parts) >= 3:
        if "/" in parts[-1] or "/" in parts[-2]:
            return None  # the far side is a socket path
        host = parts[-2]
    elif len(parts) == 2 and "/" in parts[-1] and not parts[-1].startswith("/"):
        host = parts[-1].split("/", 1)[0]  # `2222 gw/22`, spaced and joined
    elif len(parts) == 1 and v.count("/") >= 2 and not v.startswith("/"):
        host = v.split("/")[-2]  # the old `[bind/]port/host/hostport` form
    else:
        return None
    return host.strip("[]") or None


def _ssh_argv(head, args):
    """The destinations one ssh-family call reaches, as [(word as typed, how)]: its destination, every -J or
    ProxyJump hop, -o HostName, each word of -o ProxyCommand, -W's host, and for scp, rsync and sshfs every remote
    argument; an rsync -e, a mosh --ssh or an sshfs ssh_command is read as the ssh command it is. `config`: -F."""
    vals = _SSH_OPT_VALUE.get(head, set())
    out, config, i, positional = [], None, 0, []

    def from_option(flag, v):
        nonlocal config
        if flag == "-J" or flag == "ProxyJump":
            for hop in str(v).split(","):
                if hop.strip() and hop.strip().lower() != "none":
                    out.append((hop.strip(), "via the jump host"))
        elif flag == "-F":
            config = v
        elif flag == "-W":
            out.append((str(v).rsplit(":", 1)[0], "forwarding to"))
        elif flag in ("-L", "-R") and head in ("ssh", "autossh"):  # a forward's target is reached: through it the
            target = _ssh_forward_target(v)  # key logs in again (`-L 2222:<host>:22`, then `ssh -p 2222 localhost`;
            if target:                       # of_secrets-review on organic-framework PR 157)
                out.append((target, "forwarding to"))
        elif flag == "-o" or (head == "sshfs" and flag == "-o"):
            for part in (str(v).split(",") if head == "sshfs" else [v]):
                key, val = _ssh_opts(part)
                if key == "hostname":
                    out.append((val, "with -o HostName"))
                elif key == "proxyjump":
                    from_option("ProxyJump", val)
                elif key in ("localforward", "remoteforward"):  # `[bind:]port host:hostport`, the -L form spaced
                    from_option("-L", ":".join(val.split(None, 1)) if " " in val.strip() else val)
                elif key == "proxycommand":
                    out.extend((w, "in its -o ProxyCommand") for w in val.split() if not w.startswith("-")
                               and w not in ("%h", "%p", "%r") and w.lower() != "none")
                elif key == "ssh_command":
                    out.extend(_ssh_argv("ssh", val.split()[1:])[0])
        elif flag in ("-e", "--rsh") or flag == "--ssh":
            words = str(v).split()
            if words and os.path.basename(words[0]) in ("ssh", "autossh"):
                sub, _ = _ssh_argv("ssh", words[1:] + ["__RSYNC_PLACEHOLDER__"])
                out.extend(d for d in sub if d[0] != "__RSYNC_PLACEHOLDER__")

    while i < len(args):
        a = args[i]
        if a == "--":
            positional += args[i + 1:]
            break
        if a.startswith("--"):
            if "=" in a:
                from_option(*a.split("=", 1))
            elif a in vals and i + 1 < len(args):
                from_option(a, args[i + 1])
                i += 1
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:  # a cluster, the getopt way: `-4vJ jump`, `-vp 22`, `-avze 'ssh …'`
            for k in range(1, len(a)):  # (of_review and of_secrets-review on organic-framework PR 152)
                flag = "-" + a[k]
                if flag in vals:
                    if k + 1 < len(a):
                        from_option(flag, a[k + 1:])  # -Jhost, -oKey=Value, -p22
                    elif i + 1 < len(args):
                        from_option(flag, args[i + 1])
                        i += 1
                    break
            i += 1
            continue
        positional.append(a)
        if head in ("ssh", "autossh") and len(positional) == 1:  # OpenSSH reads options after the destination too,
            i += 1                                              # up to the remote command (of_secrets-review, PR 157)
            continue
        if head in ("ssh", "autossh", "sftp", "mosh"):  # the destination; what follows is the remote command
            break
        i += 1
    if head in ("ssh", "autossh", "sftp", "mosh"):
        if positional:
            out.append((positional[0], "to"))
    else:
        out.extend((p, "to") for p in positional if _ssh_remote(p) or "$" in p or "__OFSUB__" in p)
    return out, config


def ssh_calls(cmd, depth=0):
    """Every ssh-family call (ssh, scp, sftp, rsync, mosh, sshfs, autossh) the local shell runs for a command line, as
    {head, destinations: [(word, how)], config: the -F file or None}. Followed as kubectl_calls follows: wrappers,
    $( ), subshells, sh -c, eval, a heredoc read by a shell. Prose a command only carries is not a call."""
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "ssh_calls"
        return []
    text, subs, docs, _ = _scan(cmd)
    out = []
    for sub in subs:
        out += ssh_calls(sub, depth + 1)
    for _quoted, body, opener in docs:
        if _head(opener) in _SHELLS:
            out += ssh_calls(body, depth + 1)
    try:
        pipes = _split(text)
    except ValueError:
        return out
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                out += ssh_calls(stext, depth + 1)
                continue
            s = _simple(stext)
            if not s or s[0] == "<read":
                continue
            head, args = s[0], s[1]
            if head in _SSH_HEADS:
                dests, config = _ssh_argv(head, args)
                out.append({"head": head, "destinations": dests, "config": config, "args": list(args)})
            elif head in _SHELLS - {"eval"} and "-c" in args[:-1]:
                out += ssh_calls(args[args.index("-c") + 1], depth + 1)
            elif head == "eval":
                out += ssh_calls(" ".join(args), depth + 1)
    return out


_SSH_SETS = ("hostname", "proxyjump", "proxycommand", "port")  # where a call goes
_SSH_FWD = ("localforward", "remoteforward", "dynamicforward", "permitlocalcommand")  # what rides a connection past it


def _ssh_config_blocks(path, depth=0, seen=None, opaque=None, base=None):
    """[{patterns, hostname, proxyjump, proxycommand}] of an ssh config's Host blocks, in order, Include followed (globs;
    a relative path is under ~/.ssh, or /etc/ssh for the system's config). Each file is opened without blocking and
    read only when it is a regular file (a FIFO given to -F would hang the hook). Match blocks are not evaluated: their
    conditions run commands, and the guard never does (`ssh -G` would). OPAQUE, a list, gets the reason when the guard
    cannot tell where a call goes: a Match block that sets HostName, ProxyJump or ProxyCommand, or a file it cannot
    read (of_secrets-review on organic-framework PR 152). Read as text; never printed."""
    import glob
    seen = seen if seen is not None else set()
    path = _expanduser(path)
    base = base or (os.path.dirname(path) if path.startswith("/etc/ssh") else _expanduser("~/.ssh"))
    if depth > 8 or path in seen:
        return []
    seen.add(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        if opaque is not None:
            opaque.append(f"its ssh config {os.path.basename(path)} cannot be read")
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            if opaque is not None:
                opaque.append(f"its ssh config {os.path.basename(path)} is not a plain file")
            return []
        text = os.read(fd, _SSH_CONFIG_MAX).decode("utf-8", "replace")
    except OSError:
        return []
    finally:
        os.close(fd)
    out, block = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, val = _ssh_opts(line)
        if key == "host":
            block = {"patterns": val.split()}
            out.append(block)
        elif key == "match":
            block = "match"
        elif key == "include":
            for pat in val.split():
                pat = _expanduser(pat)
                if not os.path.isabs(pat):
                    pat = os.path.join(base, pat)
                for p in sorted(glob.glob(pat)):
                    out += _ssh_config_blocks(p, depth + 1, seen, opaque, base)
        elif key in _SSH_FWD:  # a Match block's counts for every host (of_secrets-review); a forward's every value
            if block == "match":
                out.append({"patterns": ["*"], key: [val] if key in _SSH_FWD[:2] else val})
            else:
                if block is None:
                    block = {"patterns": ["*"]}
                    out.append(block)
                if key in _SSH_FWD[:2]:  # LocalForward and RemoteForward add up across lines and blocks
                    block.setdefault(key, []).append(val)
                else:
                    block.setdefault(key, val)
        elif key in _SSH_SETS:
            if block == "match":
                if opaque is not None and f"a Match block sets {key}" not in opaque:
                    opaque.append(f"a Match block sets {key}")
                continue
            if block is None:
                block = {"patterns": ["*"]}
                out.append(block)
            block.setdefault(key, val)
    return out


def _ssh_pattern_match(patterns, name):
    import fnmatch
    hit = False
    for p in patterns:
        neg = p.startswith("!")
        if fnmatch.fnmatchcase(name, (p[1:] if neg else p).lower()):
            if neg:
                return False
            hit = True
    return hit


_SSH_VAR = re.compile(r"\$\{[^}]*\}|\$\w+|\$[@*#?$!0-9-]|__OFSUB__\w*|`[^`]*`")


def _globs_meet(a, b, limit=256):
    """Can one name match both globs A and B (`*` and `?`; other characters literal)? `agent-*-node` and
    `agent-us-node` meet; `agent-*-node` and `bastion-node` do not. A table, row by row, never a recursion: a word
    of 1,200 characters raised RecursionError, and the hook then lost every organization rule of that line (of_review
    and of_secrets-review on organic-framework PR 152). A glob longer than LIMIT counts as meeting: a false positive
    over a miss; a DNS name is at most 253 characters, so every real case stays exact and the worst pair small."""
    a, b = re.sub(r"\*+", "*", a), re.sub(r"\*+", "*", b)
    if len(a) > limit or len(b) > limit:
        return True
    n, m = len(a), len(b)
    row = [False] * (m + 1)  # row[j]: do a[i+1:] and b[j:] meet; first the empty a
    row[m] = True
    for j in range(m - 1, -1, -1):
        row[j] = b[j] == "*" and row[j + 1]
    for i in range(n - 1, -1, -1):
        cur = [False] * (m + 1)
        cur[m] = a[i] == "*" and row[m]
        for j in range(m - 1, -1, -1):
            if a[i] == "*":
                cur[j] = row[j] or cur[j + 1]
            elif b[j] == "*":
                cur[j] = cur[j + 1] or row[j]
            else:
                cur[j] = (a[i] == "?" or b[j] == "?" or a[i] == b[j]) and row[j + 1]
        row = cur
    return row[0]


def ssh_resolver(config=None):
    """name -> the HostName the ssh config in effect gives it (the first matching Host block that sets one, `%h` the
    name), or None. The config: -F when given, else the user's own and the system's (aliases are folded: an alias is
    the common miss, of_architect). Also `.hops(name)`: [(word, how)] of the first ProxyJump and the first
    ProxyCommand a matching block sets, ssh keeping the first value it gets (of_secrets-review on PR 152: a bastion
    alias reached a denied jump); `.blocks`; `.opaque`: why the guard cannot tell where a call goes, if it cannot."""
    opaque = []
    blocks = (_ssh_config_blocks(config, opaque=opaque) if config else
              _ssh_config_blocks("~/.ssh/config", opaque=opaque) + _ssh_config_blocks("/etc/ssh/ssh_config",
                                                                                        opaque=opaque))

    def first(name, key):
        return next((b[key] for b in blocks if b.get(key) and _ssh_pattern_match(b["patterns"], name)), None)

    def resolve(name):
        hostname = first(name, "hostname")
        return hostname.replace("%h", name).replace("%%", "%").lower() if hostname else None

    def hops(name):
        jump, command = first(name, "proxyjump"), first(name, "proxycommand")
        return _ssh_hops(jump, command)
    resolve.blocks, resolve.hops, resolve.opaque = blocks, hops, opaque
    return resolve


def _ssh_hops(jump, command):
    """[(word, how)] a ProxyJump value and a ProxyCommand value reach."""
    out = [(h.strip(), "via the jump host") for h in str(jump or "").split(",")
           if h.strip() and h.strip().lower() != "none"]
    out += [(w, "in its ProxyCommand") for w in str(command or "").split()
            if not w.startswith("-") and "%" not in w and w.lower() != "none"]
    return out


def _ssh_reach(host, resolve, depth=0):
    """[(name, note)] a literal destination reaches: itself, its HostName, and the jumps its config block names, each
    of those followed the same way (at most 3 deep)."""
    out = [(host.rstrip("."), None)]
    if resolve is None or depth > 3:
        return out
    hostname = resolve(host)
    if hostname:
        out.append((hostname.rstrip("."), f"HostName {hostname}"))
    for word, how in (getattr(resolve, "hops", None) or (lambda n: []))(host):
        h = _ssh_host(word)
        if h is None:
            out.append(("*", "its config's jump held in a variable"))
            continue
        out += [(n, f"{how} {h}" + (f", {note}" if note else "")) for n, note in _ssh_reach(h, resolve, depth + 1)]
    for b in getattr(resolve, "blocks", []) or []:  # the forwards its config gives it, every matching block's
        if _ssh_pattern_match(b["patterns"], host):    # (of_secrets-review on PR 165)
            for spec in [x for k in _SSH_FWD[:2] for x in (b.get(k) or [])]:
                target = _ssh_forward_target(":".join(spec.split(None, 1)) if " " in spec.strip() else spec)
                if target:
                    out.append((target.lower(), f"its config forwarding to {target}"))
    return out


# allow_jump (2026-11-14; a user organization's platform owner allows one position one exact jump through a denied
# gateway, msg 01a1d7d4-ed7a; of_architect's shape and four conditions, msg 01a1d7da-a30e): an entry
# {via: [hosts], to: [host:port], positions: [p]} in the same signed ssh scope. A call stays out of the rule for p only
# when every contact with a listed host is a jump from `via` to exactly one `to`, read strictly from the call's chain;
# on any doubt the call stays in the rule, as for kubectl's allow_exec.
_SSH_PORT_FLAG = {"ssh": "-p", "autossh": "-p", "scp": "-P", "sftp": "-P"}


class _Doubt(Exception):
    pass


def _ssh_hop(word, default_port=None):
    """(host, port or None) of a jump hop `[user@]host[:port]` or a `ssh://` URL; a variable is a doubt."""
    if "$" in word or "__OFSUB__" in word or "`" in word:
        raise _Doubt(word)
    host = _ssh_host(word)
    m = re.match(r"^(?:ssh://)?(?:[^@/]*@)?(\[[^\]]+\]|[^:/]+):(\d+)$", word.strip(), re.I)
    return host, (m.group(2) if m else default_port)


def _ssh_chain_of(head, args):
    """{dests: [words], jumps: [words] or None, proxycommand: str or None, port: str or None} of one call, strictly:
    a form the reader does not know (mosh, sshfs, -o HostName, a jump and a ProxyCommand together) is a doubt."""
    if head not in ("ssh", "autossh", "scp", "sftp", "rsync"):
        raise _Doubt(head)
    vals = _SSH_OPT_VALUE.get(head, set())
    out = {"dests": [], "jumps": None, "proxycommand": None, "port": None, "forwards": False, "config": None}

    def option(flag, v):
        if flag in ("-L", "-R", "-D", "-W", "-O") and head in ("ssh", "autossh"):
            out["forwards"] = True  # through an allowed jump it reaches past it (of_secrets-review on PR 157)
        if flag == "-F":
            out["config"] = str(v)
        if flag == "-J":
            out["jumps"] = (out["jumps"] or []) + [h for h in str(v).split(",") if h.strip()]
        elif flag == _SSH_PORT_FLAG.get(head):
            out["port"] = str(v)
        elif flag == "-o":
            key, val = _ssh_opts(v)
            if key == "proxyjump":
                option("-J", val)
            elif key == "proxycommand":
                out["proxycommand"] = val
            elif key == "port":
                out["port"] = val
            elif key == "hostname":
                raise _Doubt("-o HostName")
            elif key in _SSH_FWD or key == "localcommand":  # a forward or a local command rides the jump past it
                out["forwards"] = True
        elif flag in ("-e", "--rsh"):
            words = str(v).split()
            if not words or os.path.basename(words[0]) != "ssh":
                raise _Doubt("rsync -e")
            inner = _ssh_chain_of("ssh", words[1:] + ["__DEST__"])
            out["jumps"], out["proxycommand"], out["port"] = inner["jumps"], inner["proxycommand"], inner["port"]
    i, positional = 0, []
    while i < len(args):
        a = args[i]
        if a == "--":
            positional += args[i + 1:]
            break
        if a.startswith("--"):
            if "=" in a:
                option(*a.split("=", 1))
            elif a in vals and i + 1 < len(args):
                option(a, args[i + 1])
                i += 1
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            for k in range(1, len(a)):
                flag = "-" + a[k]
                if flag in vals:
                    if k + 1 < len(a):
                        option(flag, a[k + 1:])
                    elif i + 1 < len(args):
                        option(flag, args[i + 1])
                        i += 1
                    break
            i += 1
            continue
        positional.append(a)
        if head in ("ssh", "autossh") and len(positional) == 1:  # options after the destination, as OpenSSH reads
            i += 1
            continue
        if head in ("ssh", "autossh", "sftp"):
            break
        i += 1
    if head in ("ssh", "autossh", "sftp"):
        out["dests"] = positional[:1]
    else:
        out["dests"] = [p for p in positional if _ssh_remote(p) or "$" in p or "__OFSUB__" in p]
    if out["jumps"] is not None and out["proxycommand"] is not None:
        raise _Doubt("a jump and a ProxyCommand")
    return out


def _ssh_tools(resolve):
    """(first, real, plain) over one folded config: the first value a matching block sets; the name ssh connects to
    (its HostName, else the name typed); a hop checked for a jump or a forward of its own (a doubt)."""
    def first(n, k):
        return next((b[k] for b in getattr(resolve, "blocks", []) or []
                     if b.get(k) and _ssh_pattern_match(b["patterns"], n)), None) if resolve else None

    def real(host):
        hostname = resolve(host) if resolve else None
        return (hostname or host).rstrip(".").lower()

    def unforwarded(host):  # a forward or a local command its config sets rides the connection past the jump
        if any(first(host, k) for k in _SSH_FWD[:3]) or \
                str(first(host, "permitlocalcommand") or "no").lower() in ("yes", "true"):
            raise _Doubt(f"{host} forwards")
        return host

    def plain(hop):  # a jump hop reached by its own jump (its config's ProxyJump or ProxyCommand) is a doubt: ssh
        # connects to that one first, which this reading does not follow (of_review on PR 157)
        if any(str(first(hop, k) or "none").lower() != "none" for k in ("proxyjump", "proxycommand")):
            raise _Doubt(f"{hop} has its own jump")
        return unforwarded(hop)
    return first, real, plain, unforwarded


def _ssh_contacts(call, resolve):
    """[(host as typed, the name ssh connects to, (next host, next port) for a jump, else None for a contact)] of
    every host one call reaches, read strictly (raises _Doubt): the jumps of -J, -o ProxyJump or the folded config's
    ProxyJump, each to the hop right after it, the last to the destination; a ProxyCommand only in its
    `ssh … -W <to> <via>` form (%h and %p the destination), its hops named under its own -F config when it has one;
    the destination itself a contact. A port is the hop's own `:port`, else the folded config's Port; only the
    destination takes -p; else 22 (of_architect's conditions 1 and 2; of_review on PR 157: `via` and `to` are both
    the names ssh connects to, never an alias a config points elsewhere)."""
    chain = _ssh_chain_of(call["head"], call.get("args") or [])
    first, real, plain, unforwarded = _ssh_tools(resolve)
    out = []
    for dword in chain["dests"]:
        dest = unforwarded(_ssh_hop(dword)[0])
        dport = chain["port"] or first(dest, "port") or "22"
        jumps, command = chain["jumps"], chain["proxycommand"]
        if jumps is None and command is None:
            jumps = [h for h in str(first(dest, "proxyjump") or "").split(",") if h.strip()] or None
            command = first(dest, "proxycommand") if jumps is None else None
        if jumps and str(jumps[0]).lower() == "none":
            jumps = None
        if jumps:
            hops = [_ssh_hop(h) for h in jumps]
            hops = [(plain(h), p or first(h, "port") or "22") for h, p in hops] + [(dest, dport)]
            out += [(hops[k][0], real(hops[k][0]), (real(hops[k + 1][0]), hops[k + 1][1]))
                    for k in range(len(hops) - 1)]
        elif command and command.lower() != "none":
            words = command.split()
            if not words or os.path.basename(words[0]) != "ssh":
                raise _Doubt("a ProxyCommand that is not ssh -W")
            sub = _ssh_chain_of("ssh", words[1:])
            w = next((words[k + 1] for k in range(1, len(words) - 1) if words[k] == "-W"), None) or \
                next((x[2:] for x in words[1:] if x.startswith("-W") and len(x) > 2), None)
            if not w or not sub["dests"]:
                raise _Doubt("a ProxyCommand without -W")
            to_host, _, to_port = w.replace("%h", real(dest)).replace("%p", dport).rpartition(":")
            sfirst, sreal, splain, _ = _ssh_tools(ssh_resolver(sub["config"]) if sub.get("config") else resolve)
            via_host = splain(_ssh_hop(sub["dests"][0])[0])
            inner = [_ssh_hop(h) for h in sub["jumps"] or []]
            hops = [(splain(h), p or sfirst(h, "port") or "22") for h, p in inner] + [
                (via_host, sub["port"] or sfirst(via_host, "port") or "22")]
            out += [(hops[k][0], sreal(hops[k][0]), (sreal(hops[k + 1][0]), hops[k + 1][1]))
                    for k in range(len(hops) - 1)]
            out.append((hops[-1][0], sreal(hops[-1][0]), (to_host.strip("[]").lower(), to_port)))  # the gateway
            # resolves a -W target itself
        out.append((dest, real(dest), None))
    return out


def _ssh_allowed(match, call, resolve, position):
    """Does an allow_jump entry for POSITION cover every contact this call makes with a listed host? Any doubt: no."""
    entries = [e for e in match.get("allow_jump") or [] if position and position in (e.get("positions") or [])]
    if not entries:
        return False
    import fnmatch
    globs = [str(h).lower() for h in match.get("hosts") or []]

    def names(host):
        n = [host.rstrip(".")]
        hostname = resolve(host) if resolve else None
        return n + ([hostname.rstrip(".")] if hostname else [])

    def listed(host):
        return any(fnmatch.fnmatchcase(n, g) for n in names(host) for g in globs)
    try:
        if _ssh_chain_of(call["head"], call.get("args") or [])["forwards"]:
            return False  # a forward rides the allowed jump past it: never the exact use allowed
        contacts = _ssh_contacts(call, resolve)
    except _Doubt:
        return False
    for host, real_host, to in contacts:
        if not (listed(host) or any(fnmatch.fnmatchcase(real_host, g) for g in globs)):
            continue
        if to is None:
            return False
        if not any(any(fnmatch.fnmatchcase(real_host, str(v).lower()) for v in e.get("via") or [])
                   and any(_ssh_to_matches(t, to) for t in e.get("to") or []) for e in entries):
            return False
    return True


def _ssh_to_matches(entry_to, to):
    """An allow_jump `to` (`host:port`, the port 22 when left out) against a jump's next hop (the name ssh connects
    to, port): never the alias typed, which a config could point anywhere (of_secrets-review on PR 157)."""
    import fnmatch
    host, _, port = str(entry_to).rpartition(":") if re.search(r":\d+$", str(entry_to)) else (str(entry_to), "", "22")
    return port == str(to[1]) and fnmatch.fnmatchcase(str(to[0]), host.strip("[]").lower())


def ssh_rule_match(match, call, resolve=None, position=None):
    """What an ssh matcher matched in one call, as a short phrase, or None. `match`: {hosts: [globs]}, compared
    case-insensitively with each destination the call reaches (`_ssh_argv`), as typed and through the ssh config: its
    HostName, and the ProxyJump and ProxyCommand its block sets (`_ssh_reach`). A destination held in a variable
    matches wherever its literal parts can fit a listed host. A config the guard cannot read through, or one whose
    Match block sets where a call goes, makes the call a match: a false positive over a miss."""
    hit = _ssh_rule_hit(match, call, resolve)
    if hit and match.get("allow_jump") and not getattr(resolve, "opaque", None) and \
            _ssh_allowed(match, call, resolve, position):
        return None  # every contact with a listed host is a jump this position is allowed (allow_jump)
    return hit


def _ssh_rule_hit(match, call, resolve=None):
    import fnmatch
    globs = [str(h).lower() for h in match.get("hosts") or []]
    opaque = getattr(resolve, "opaque", None)
    if opaque and call["destinations"]:
        return f"{call['head']}: {opaque[0]}, so the guard cannot tell where it goes"
    for word, how in call["destinations"]:
        host = _ssh_host(word)
        if host is None:  # held in a variable: only what its literal parts can fit (a user organization, msg
            # 01a1d7c9-9bc8: `agent-$h-node` loops are never `bastion-node`); a bare variable can be any host
            pattern = re.sub(r"\*+", "*", _ssh_host(_SSH_VAR.sub("*", str(word))) or "*")
            names = [(pattern, None)]
            for b in getattr(resolve, "blocks", []) or []:
                if any(_globs_meet(p.lower(), pattern) for p in b["patterns"] if not p.startswith("!")):
                    if b.get("hostname"):
                        names.append((b["hostname"].replace("%h", pattern).lower(), f"HostName {b['hostname']}"))
                    names += [((_ssh_host(w) or "*"), f"{h} {w}") for w, h in _ssh_hops(b.get("proxyjump"),
                                                                                       b.get("proxycommand"))]
            for n, note in names:
                g = next((g for g in globs if _globs_meet(n.rstrip("."), g)), None)
                if g:
                    return (f"{call['head']} {how} {pattern}, a destination held in a variable that may be {g}"
                            + (f" ({note})" if note else ""))
            continue
        whole = str(word).rsplit("@", 1)[-1].lower()  # a bare IPv6 destination (`ssh fe80::1`)
        names = _ssh_reach(host, resolve) + ([(whole, None)] if whole != host else [])
        for n, note in names:
            if any(fnmatch.fnmatchcase(n, g) for g in globs):
                return f"{call['head']} {how} {host}" + (f" ({note})" if note else "")
    return None


def org_rule_hits(tool, tool_input, rules, position=None, env=None, resolver=None):
    """[(rule, matched phrase)] for the rules of the caller's organization that apply to this tool call. A rule is
    {id, mode: warn|deny, reason, subject?, except: [positions], kubectl: {matcher}, ssh: {hosts} or cli:
    {names, env, code, allow}} (of_grants.org_rule makes one from a deny grant); a position in `except` is out of it,
    and a rule whose subject names one position applies to that position only. Only Bash; the kubectl (O1), ssh (O3)
    and cli (O4) matchers. An O4 match that only warns (a command word held in a variable) comes back with the
    rule in warn mode, whatever the grant says. RESOLVER (tests): config -> the alias resolver, ssh_resolver by
    default."""
    if tool != "Bash" or not rules:
        return []
    cmd = (tool_input or {}).get("command") or ""
    calls, scalls, findings, resolvers, hits = None, None, None, {}, []
    for r in rules:
        sub = r.get("subject")
        if (position and position in (r.get("except") or [])) or (sub not in (None, "*") and sub != position):
            continue
        if not any(isinstance(r.get(k), dict) for k in ("kubectl", "ssh", "cli")):
            continue
        try:  # each rule alone: an error while one matches counts as its match, and never loses the others
            # (of_review and of_secrets-review on organic-framework PR 152: a RecursionError lost them all)
            if isinstance(r.get("cli"), dict):
                if findings is None:
                    findings = command_findings(cmd)
                got = commands_rule_match(r["cli"], findings, position, cmd)
                if got and got[1]:  # a variable word never denies (condition 1), and its note is not shown:
                    r = dict(r, mode="record")  # none of 2,044 in the replay was an AWS call (msg 01a1db6d-f7e4)
                m = got[0] if got else None
            elif isinstance(r.get("kubectl"), dict):
                if calls is None:  # a line the parser cannot read counts as a match, as a matching error does
                    try:            # (PR 152's notes: 25 of 31,216 kubectl lines and 4 of 12,185 ssh lines in 21 days)
                        calls = kubectl_calls(cmd, env) if "kubectl" in cmd else []
                    except Exception as e:  # noqa: BLE001
                        calls = e
                if isinstance(calls, Exception):
                    raise calls
                m = next(filter(None, (kubectl_rule_match(r["kubectl"], c) for c in calls)), None)
            else:
                if scalls is None:
                    try:
                        scalls = ssh_calls(cmd) if _SSH_WORDS.search(cmd) else []
                    except Exception as e:  # noqa: BLE001
                        scalls = e
                if isinstance(scalls, Exception):
                    raise scalls
                m = None
                for c in scalls:
                    if c["config"] not in resolvers:
                        try:
                            resolvers[c["config"]] = (resolver or ssh_resolver)(c["config"])
                        except Exception:  # noqa: BLE001 - an unreadable config folds nothing
                            resolvers[c["config"]] = None
                    m = ssh_rule_match(r["ssh"], c, resolvers[c["config"]], position)
                    if m:
                        break
        except Exception as e:  # noqa: BLE001
            m = f"a call the guard could not read ({type(e).__name__}), counted as a match"
        if m:
            hits.append((r, m if len(m) <= 300 else m[:297] + "..."))  # a long word never fills the record
    return hits


# ------------------------------------------------- O4: an organization's command-line deny (scope cli)
# docs/registry.md, Scope cli (2026-11-14; a user organization's request, msg 01a1db4f-f9f0; of_architect's
# conditions, msg 01a1db57-17f9). Unlike kubectl and ssh it reads every Bash call of the organization's positions, so a
# command word held in a variable warns and never denies (condition 1), and a line the parser cannot read is a match
# only when it names a listed command.
_PKG_RUNNERS = {"npx": (), "bunx": (), "uvx": (), "pnpx": (), "pnpm": ("dlx", "exec"), "yarn": ("dlx", "exec"),
                "pipx": ("run",), "npm": ("exec",)}
_ENV_SETTERS = {"export", "env", "declare", "typeset", "local", "readonly"}
# a package a runner fetches, by the command it installs (npx aws-cdk, pipx run awscli, uvx --from awscli aws)
_PKG_BINS = {"aws-cdk": "cdk", "awscli": "aws", "aws-sam-cli": "sam", "cdktf-cli": "cdktf"}


_PARALLEL_VALUE = {"-j", "--jobs", "-S", "--sshlogin", "-a", "--arg-file", "-I", "--colsep", "-N", "-n", "--delay",
                   "--timeout", "--joblog", "--results", "--tmpdir", "-P", "--max-procs", "-L", "--max-lines", "-s",
                   "--max-chars", "-E", "--eof", "--retries", "--load", "--memfree", "--tag-string", "--workdir", "--wd",
                   "-d", "--delimiter"}
# environment runners that run their command here (of_review on PR 178): head -> (its run subcommands, the options
# that take a value); the command is what follows, or what follows `--`
_ENV_RUNNERS = {
    "uv": (("run",), {"--with", "--with-requirements", "--with-editable", "--from", "--python", "-p", "--project",
                      "--directory", "--env-file", "--extra", "--group", "--package", "--index", "--index-url",
                      "--default-index", "-w"}),
    "poetry": (("run",), {"-C", "--directory", "-P", "--project"}), "pdm": (("run",), {"-p", "--project"}),
    "hatch": (("run",), {"-e", "--env"}), "pipenv": (("run",), set()), "rye": (("run",), {"--pyproject"}),
    "devbox": (("run",), {"-c", "--config", "-e", "--env"}), "direnv": (("exec",), set()),
    "mise": (("exec", "x"), {"-C", "--cd", "-j", "--jobs"}), "asdf": (("exec",), set()),
    "conda": (("run",), {"-n", "--name", "-p", "--prefix", "--cwd"}),
    "mamba": (("run",), {"-n", "--name", "-p", "--prefix", "--cwd"}),
    "micromamba": (("run",), {"-n", "--name", "-p", "--prefix", "--cwd"}),
    "bundle": (("exec",), set()), "rbenv": (("exec",), set()), "pyenv": (("exec",), set()),
}
_LOCAL_RUNNERS = {"watch", "find", "parallel", "su", "nix", "nix-shell", *_ENV_RUNNERS}


def _local_runner_command(head, args):
    """The command line one of _LOCAL_RUNNERS runs on this machine, or None."""
    if head == "watch":
        k = 0
        while k < len(args) and args[k].startswith("-"):
            k += 2 if args[k] in ("-n", "--interval", "-q", "--equexit") else 1
        return " ".join(shlex.quote(a) for a in args[k:]) or None
    if head == "find":
        cmds = []
        for k, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                end = next((j for j in range(k + 1, len(args)) if args[j] in (";", "+")), len(args))
                cmds.append(" ".join(shlex.quote(x if x != "{}" else IN_HOLE) for x in args[k + 1:end]))  # a found path
        return "; ".join(cmds) or None
    if head == "parallel":
        k = 0
        while k < len(args) and args[k].startswith("-"):
            k += 2 if args[k] in _PARALLEL_VALUE else 1
        end = next((j for j in range(k, len(args)) if args[j] in (":::", "::::", ":::+")), len(args))
        return " ".join(args[k:end]) or None
    if head in _ENV_RUNNERS:
        subs, value = _ENV_RUNNERS[head]
        k = 0
        while k < len(args) and args[k].startswith("-"):  # options before the subcommand
            k += 2 if args[k] in value else 1
        if k >= len(args) or args[k] not in subs:
            return None
        rest = args[k + 1:]
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        else:
            j = 0
            if head == "direnv" and rest:  # `direnv exec DIR cmd …`
                j = 1
            while j < len(rest) and rest[j].startswith("-"):
                j += 2 if rest[j] in value else 1
            rest = rest[j:]
        return " ".join(shlex.quote(a) for a in rest) or None
    if head == "nix" and args[:1] in (["shell"], ["develop"]):  # `nix shell pkgs -c cmd …`
        for k, a in enumerate(args):
            if a in ("-c", "--command"):
                return " ".join(shlex.quote(x) for x in args[k + 1:]) or None
        return None
    if head == "nix-shell":
        for k, a in enumerate(args):
            if a in ("--run", "--command") and k + 1 < len(args):
                return args[k + 1]
        return None
    if head == "su":
        for k, a in enumerate(args):
            if a in ("-c", "--command") and k + 1 < len(args):
                return args[k + 1]
            if a.startswith("--command="):
                return a.split("=", 1)[1]
    return None


def _py_module(args):
    """The module of `python -m MODULE` (also `-Im MODULE`, `-mMODULE`, after `-X utf8` or `-W ignore`), or None."""
    k = 0
    while k < len(args):
        a = args[k]
        if not a.startswith("-") or a in ("-c", "-"):
            return None
        if a in ("-X", "-W", "--check-hash-based-pycs"):  # their value is no module (of_review on PR 178)
            k += 2
            continue
        m = re.match(r"^-[A-Za-z]*m(\S*)$", a)
        if m:
            return m.group(1) or (args[k + 1] if k + 1 < len(args) else None)
        k += 1
    return None


def _code_modules(code):
    """The modules interpreter code imports: Python's `import a, b.c as d`, `from x import`, `__import__('x')`,
    `import_module('x')`; Node's `require('x')`, `import … from 'x'`, `import('x')`."""
    t = str(code or "")
    out = set()
    # anywhere, also after `:` or inside a string run by exec (of_review on PR 178): `if 1: import boto3`
    for m in re.finditer(r"\bimport\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)", t):
        out |= {w.strip() for w in m.group(1).split(",") if w.strip()}
    for m in re.finditer(r"(?:^|[;\n])\s*import\s+([^;\n#]+)", t):
        for part in m.group(1).split(","):
            w = part.strip().split()[0] if part.strip() else ""
            if w and not w.startswith(("'", '"', "{", "*")):
                out.add(w)
    for rx in (r"\bfrom\s+([\w.]+)\s+import\b", r"__import__\s*\(\s*['\"]([^'\"]+)",
               r"import_module\s*\(\s*['\"]([^'\"]+)", r"\brequire\s*\(\s*['\"]([^'\"]+)",
               r"\bfrom\s+['\"]([^'\"]+)['\"]", r"\bimport\s*\(\s*['\"]([^'\"]+)", r"\bimport\s+['\"]([^'\"]+)"):
        out |= {m.group(1) for m in re.finditer(rx, t)}
    return out


def _cmd_var(word):
    return "$" in word or "__OFSUB__" in word or "`" in word


def command_findings(cmd, depth=0):
    """[(kind, a, b)] of one Bash line, for O4: ('word', command word as typed, args) for every simple command run on
    this machine (through wrappers, sh -c, eval, substitutions, heredocs fed to a shell, and package runners); ('env',
    NAME, None) for a variable assigned or exported on the line; ('code', text, language) for interpreter code
    (-c, -e, a here-string or a heredoc); ('unparsed', line, None) when the line cannot be read. A remote command (ssh,
    docker exec, kubectl exec) runs elsewhere and is not read."""
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "command_findings"
        return []
    out = []
    try:
        text, subs, docs, _ = _scan(cmd)
        pipes = _split(text)
    except (ValueError, IndexError, RecursionError):
        return [("unparsed", str(cmd), None)]
    for sub in subs:
        out += command_findings(sub, depth + 1)
    for _, body, opener in docs:
        head = _head(opener)
        if head in _SHELLS:
            out += command_findings(body, depth + 1)
        elif head in _INTERPRETERS:
            out.append(("code", body, _lang(head) or head))
    for pipe in pipes:
        for kind, raw in pipe:
            if kind == "sub":
                out += command_findings(raw, depth + 1)
                continue
            try:
                toks = _tokens(_ansi_c(raw))
            except (ValueError, IndexError):
                out.append(("unparsed", raw, None))
                continue
            k = 0
            while k < len(toks) and re.match(r"^[A-Za-z_]\w*=", toks[k] or ""):  # NAME=… before the command
                out.append(("env", toks[k].split("=", 1)[0], None))
                k += 1
            try:
                s = _simple(raw)
            except (ValueError, IndexError):
                s = None
            if not s or not isinstance(s[1], list):
                continue
            head, args, words = s[0], list(s[1]), s[2] or []
            first = words[0] if words else head
            if head in _ENV_SETTERS:
                opts = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
                exported = head == "export" or "x" in opts
                if not (head == "export" and "n" in opts):  # `export -n NAME` unexports (of_review on PR 178)
                    out += [("env", a.split("=", 1)[0], None) for a in args if not a.startswith("-")
                            and (re.match(r"^[A-Za-z_]\w*=", a) or (exported and re.match(r"^[A-Za-z_]\w*$", a)))]
                if head == "env":  # `env NAME=v cmd …`: the command after the assignments runs too
                    rest = [a for a in args if not re.match(r"^[A-Za-z_]\w*=", a) and not a.startswith("-")]
                    if rest:
                        out += command_findings(" ".join(shlex.quote(a) for a in rest), depth + 1)
                continue
            if head in _SHELLS:
                if head == "eval":
                    out += command_findings(" ".join(args), depth + 1)
                else:
                    flags = [i for i, a in enumerate(args) if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a)]
                    if flags and flags[0] + 1 < len(args):
                        out += command_findings(args[flags[0] + 1], depth + 1)
                    elif s[4] is not None:  # `bash <<< 'aws s3 ls'`
                        out += command_findings(s[4], depth + 1)
                    elif not [a for a in args if not a.startswith("-")]:  # a shell reading its script from stdin
                        at = pipe.index((kind, raw))
                        try:
                            prev = _simple(pipe[at - 1][1]) if at > 0 and pipe[at - 1][0] == "cmd" else None
                        except (ValueError, IndexError):
                            prev = None
                        if prev and prev[0] in ("echo", "printf") and isinstance(prev[1], list):
                            out += command_findings(" ".join(prev[1]).replace("\\n", "\n"), depth + 1)
                        elif at > 0:
                            out.append(("unparsed", str(cmd), None))  # `curl … | sh`: read only for a listed name
                continue
            if head in _LOCAL_RUNNERS:  # watch, find -exec, parallel, su -c: they run their command here
                body = _local_runner_command(head, args)
                if body and head == "parallel" and "{}" not in body:
                    body += " " + IN_HOLE   # parallel adds its input's items, as xargs does
                if body:
                    out += command_findings(body, depth + 1)
                out.append(("word", first, args))
                continue
            if head in _PKG_RUNNERS:
                subs_ok = _PKG_RUNNERS[head]
                rest = list(args)
                if subs_ok:
                    if not rest or rest[0] not in subs_ok:
                        out.append(("word", first, args))
                        continue
                    rest = rest[1:]
                rest = [a for a in rest if not a.startswith("-")]
                if rest:
                    pkg = re.sub(r"(?<=.)@[^/]*$", "", rest[0])  # a version (`aws-cdk@2`), never a scope's @
                    bin_ = "cdk" if pkg.startswith("@aws-cdk/") else _PKG_BINS.get(pkg, pkg.rsplit("/", 1)[-1])
                    out.append(("word", bin_, rest[1:]))
                continue
            at = next((i for i, t in enumerate(toks) if t == first), None)
            if at is not None and any(os.path.basename(t) == "xargs" for t in toks[:at]):
                args = args + [IN_HOLE]   # xargs adds its input's items: a word the call may still get
            mod = _py_module(args) if _lang(head) else None
            if mod:  # `python3 -m awscli …` is the AWS CLI (of_review on PR 178)
                out.append(("word", _PKG_BINS.get(mod, mod), args[args.index(mod) + 1:] if mod in args else []))
            out.append(("word", first, args))
            if s[4] is not None and head in _INTERPRETERS:
                out.append(("code", s[4], _lang(head) or head))
            if head in _INTERPRETERS:
                for flag in ("-c", "-e"):
                    if flag in args[:-1]:
                        out.append(("code", args[args.index(flag) + 1], _lang(head) or head))
                if head.startswith("python"):
                    kind_, code, _ = _py_walk(args)
                    if kind_ == "code":
                        out.append(("code", code, "python"))
    return out


def _cmd_option_values(args, name):
    """The value of every occurrence of option NAME or an abbreviation of it (`--prof` for `--profile`): argparse takes
    any unambiguous prefix and keeps the last occurrence (of_secrets-review and of_review on organic-framework PR 178).
    None for an occurrence without a value."""
    out = []
    for k, a in enumerate(args):
        if not a.startswith("--") or a == "--":
            continue
        key, eq, val = a.partition("=")
        if len(key) > 2 and name.startswith(key):
            out.append(val if eq else (args[k + 1] if k + 1 < len(args) else None))
    return out


def _cmd_value_fits(value, pattern):
    """A glob against a value; a URL glob is compared by scheme and host, so `*` cannot run into the path."""
    import fnmatch
    from urllib.parse import urlsplit
    if "://" in pattern:
        try:
            v, p = urlsplit(value), urlsplit(pattern)
            return bool(v.hostname) and v.scheme == p.scheme and fnmatch.fnmatchcase(v.hostname, (p.hostname or ""))
        except ValueError:
            return False
    return fnmatch.fnmatchcase(value, pattern)


def _cmd_allowed(scope, name, args, position):
    """Does an allow entry of the scope let POSITION run NAME with ARGS? Every option it lists must be present with
    a listed value (globs), anywhere on the call; argv, when given, must be the call's first words. A value held in a
    variable is a doubt: no."""
    for e in scope.get("allow") or []:
        if not position or position not in (e.get("positions") or []):
            continue
        if e.get("tool") and str(e["tool"]).casefold() != name.casefold():
            continue
        if e.get("argv") and [str(x) for x in e["argv"]] != args[:len(e["argv"])]:
            continue
        ok = True
        for opt_, values in (e.get("options") or {}).items():
            vs = _cmd_option_values(args, opt_)
            if not vs or any(v is None or _cmd_var(v) or not any(_cmd_value_fits(v, str(p)) for p in values or [])
                             for v in vs):
                ok = False
                break
        if ok:
            return True
    return False


_LOOKUP = re.compile(r"^\s*(?:which|command\s+-v|type\s+-[pP]|realpath|readlink\s+-f)\s+['\"]?([^\s'\"]+)['\"]?\s*$")


def _resolve_word(word, line):
    """(command name, known) for a command word that holds a variable: the literal file name after the last `/`
    (`$HOME/bin/aws`); the literal value a same-line assignment gives the variable (`T=aws; $T`); the name a
    lookup substitution names (`$(which aws)`). (None, False) when the line does not say."""
    base = word.rsplit("/", 1)[-1]
    if base and not _cmd_var(base) and "/" in word:
        return base, True
    m = re.match(r"^\$\{?(\w+)\}?$", word)
    if m:
        sets = re.findall(r"(?:^|[\s;&|(])" + re.escape(m.group(1)) + r"=(\$\([^)]*\)|`[^`]*`|'[^']*'|\"[^\"]*\"|[^\s;&|]*)", line)
        if sets:
            v = sets[-1].strip("'\"")
            sub = re.match(r"^(?:\$\(|`)(.*?)(?:\)|`)$", v)
            if sub:
                look = _LOOKUP.match(sub.group(1))
                return (os.path.basename(look.group(1)), True) if look else (None, False)
            if v and not _cmd_var(v):
                return os.path.basename(v), True
        return None, False
    if "__OFSUB__" in word or "`" in word:
        try:
            subs = _scan(line)[1]
        except (ValueError, IndexError, RecursionError):
            return None, False
        looks = [_LOOKUP.match(x) for x in subs]
        if subs and all(looks):
            got = [os.path.basename(x.group(1)) for x in looks]
            return got[0], True
    return None, False


_MATCH_HOLE = re.compile(r"\$\{[^}]*\}|\$\w+|\$[@*#?$!0-9-]|__OFSUB\d*__|__OFIN__|`[^`]*`|\{\}")
IN_HOLE = "__OFIN__"   # what xargs or parallel adds from its input (of_secrets-review on organic-framework PR 237)


def _call_glob(word):
    """A call's word as a glob: a variable, a $( ) or a shell glob in it may hold anything, so only its literal parts
    must fit (as the ssh matcher reads a host)."""
    return re.sub(r"\[[^\]]*\]", "?", _MATCH_HOLE.sub("*", str(word)))


def _fits(word, alternatives):
    """Can WORD (a call's, read as a glob) be one of ALTERNATIVES (`a|b*`)?"""
    return any(_globs_meet(_call_glob(word), alt) for alt in str(alternatives).split("|") if alt != "")


def _match_option_values(args, spelling, guesses=None):
    """Every value the call gives option SPELLING, in every spelling: `-n v`, `-nv`, `-n=v`, a short cluster holding
    it (`-inprod`, `-xn v`), `--name v`, `--name=v` and an unambiguous abbreviation of a long one. None for one without
    a value (a doubt). A value read inside a cluster is a guess (`-fingress.yaml` may be -f alone): its index goes
    into GUESSES, so a rule that lists '' still fits it as not given (of_secrets-review on PR 237)."""
    out = []
    long_ = spelling.startswith("--")
    for k, a in enumerate(args):
        nxt = args[k + 1] if k + 1 < len(args) else None
        if a == "--":
            break
        if long_:
            if not a.startswith("--"):
                continue
            key, eq, val = a.partition("=")
            if len(key) > 2 and spelling.startswith(key):
                out.append(val if eq else nxt)
        elif re.match(r"^-[^-]", a):
            ch = spelling[1:]
            if a == spelling:
                out.append(nxt)
            elif a.startswith(spelling + "="):
                out.append(a[len(spelling) + 1:])
            elif a.startswith(spelling):
                out.append(a[len(spelling):])
            elif re.match(r"^-[A-Za-z0-9]", a) and ch in a[2:]:   # a cluster: `-inprod` is -i, then -n prod
                rest = a[a.index(ch, 2) + len(ch):]
                if guesses is not None:
                    guesses.add(len(out))
                out.append(rest[1:] if rest.startswith("=") else rest if rest else nxt)
    return out


def _cmd_matched(scope, name, args, line=""):
    """O4 `match` (docs/registry.md, Scope cli; of_architect, msg 01a1e245-c3a5): "hard", "soft" or None. With no match
    list, every call of a listed name is hard; else one an entry fits, every key of it. `words`: an ordered
    subsequence of the call's words that are not options, each a set of alternatives (`|`) and globs, so an unknown
    option's value never shifts it. `options`: each listed spelling read anywhere on the line; `''` stands for not
    given. A value in a variable fits where its literal parts can: a doubt matches, as a deny must. Only the first
    word an entry names (the subcommand) must be literal: one that fits only through a variable is soft, recorded and
    never denied, as a command word held in a variable is (condition 1; the replay of a user organization's helm calls:
    `helm get $R -n $NS` would have read $R as `upgrade`)."""
    entries = scope.get("match")
    if not entries:
        return "hard"
    words = [a for a in args if not a.startswith("-") or a == "-"]
    best = None
    for e in entries:
        if e.get("tool") and name and str(e["tool"]).casefold() != name.casefold():
            continue
        want, k, literal_head = list(e.get("words") or []), 0, True
        for w in words:
            if k == 0 and want and re.fullmatch(r"\$\{?\w+\}?", w):
                said = _resolve_word(w, line)[0] if line else None  # `S=upgrade; helm $S …`: only the subcommand
                if said is not None:  # is read so; a wrong reading only moves a record to nothing (of_secrets-review)
                    if _fits(said, want[0]):
                        k += 1
                    continue
            if k < len(want) and _fits(w, want[k]):
                if k == 0:
                    literal_head = _call_glob(w) == w
                    if not literal_head and any(_call_glob(x) == x and _fits(x, want[0]) for x in words):
                        continue  # a literal word further on fits the head: take that one
                k += 1
        if k < len(want):
            continue
        ok = True
        for spellings, values in (e.get("options") or {}).items():
            got, guessed = [], set()
            for sp in str(spellings).split("|"):
                if sp:
                    g = set()
                    vs = _match_option_values(args, sp, g)
                    guessed |= {len(got) + i for i in g}
                    got += vs
            vals = [str(v) for v in values or []]
            if not got or ("" in vals and len(guessed) == len(got)):
                ok = "" in vals or (bool(got) and any(v is None or any(_fits(v, p) for p in vals if p != "")
                                                      for v in got))
            else:
                ok = any(v is None or any(_fits(v, p) for p in vals if p != "") for v in got)
            if not ok:
                break
        if ok:
            if not want or literal_head:
                return "hard"
            best = "soft"
    return best


def commands_rule_match(scope, findings, position, line=""):
    """(what matched, soft) for O4, or None. soft: a command word held in a variable, which only warns (of_architect's
    condition 1), unless its value is set to a listed name on the same line or its literal parts end in one."""
    names = {str(n).casefold() for n in scope.get("names") or []}  # a case-insensitive disk finds `AWS` as aws
    env = {str(n) for n in scope.get("env") or []}
    mods = [str(m) for m in scope.get("code") or []]
    soft = None
    for kind, a, b in findings:
        if kind == "unparsed":
            if any(re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", str(a)) for n in names):
                return "a line the guard could not read that names a listed command, counted as a match", False
            continue
        if kind == "env" and a in env:
            return f"{a} set on the command line", False
        if kind == "code":
            got = _code_modules(a)
            for m in mods:
                if any(x == m or x.startswith(m + ".") or x.startswith(m + "/") for x in got):
                    return f"code importing {m}", False
            for n in env:
                if re.search(r"(?:environ|putenv|process\.env|ENV)\W{0,4}" + re.escape(n) + r"\b", str(a)):
                    return f"code setting {n}", False
            continue
        if kind != "word":
            continue
        word, args = str(a), [str(x) for x in (b or [])]
        if _cmd_var(word):
            target, known = _resolve_word(word, line)
            target = target.casefold() if target else target
            if target in names:
                m = _cmd_matched(scope, target, args, line)
                if m and not _cmd_allowed(scope, target, args, position):
                    if m == "hard":
                        return f"{target} {' '.join(args)}".strip()[:200], False
                    soft = soft or (f"{target} {' '.join(args)}".strip()[:200], True)
                continue
            if not known and _cmd_matched(scope, "", args, line):  # the line does not say what runs: a warning, never a
                soft = soft or (f"a command word held in a variable ({word[:60]})", True)  # deny (condition 1)
            continue
        name = os.path.basename(word).casefold()
        m = _cmd_matched(scope, name, args, line) if name in names else None
        if m and not _cmd_allowed(scope, name, args, position):
            if m == "hard":
                return f"{name} {' '.join(args)}".strip()[:200], False
            soft = soft or (f"{name} {' '.join(args)}".strip()[:200], True)
    return soft


# ------------------------------------------------- the public repository's writes on GitHub (G18)
# AGENTS.md (the founder, 2026-11-11 about 19:11Z; cards 626ac155, 9800f623): every feature reaches main through a pull
# request, and only of_release merges and pushes. Every agent's git and gh act as the founder's GitHub account, so
# only the guard can tell positions apart. framework_writes() names what a command line would change on a GitHub
# repository of the organic-framework owner; the hook allows it to of_release only (bin/of_hook.py). Reading,
# comments and reviews stay open to everyone. A user organization's own repositories are never matched.
RELEASE_OWNER = "organic-framework"
_GH_URL = re.compile(r"(?:github\.com[:/]+|^)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.I)
_GH_READ_PATHS = re.compile(r"/(?:issues|pulls)/(?:\d+/)?comments(?:/\d+)?$|/pulls/\d+/reviews(?:/\d+)?$|"
                            r"/(?:issues|pulls)/comments/\d+(?:/reactions)?$|/issues/\d+/reactions$")
_GQL_WRITES = re.compile(r"\b(mergePullRequest|enablePullRequestAutoMerge|mergeBranch|updateRefs?|createRef|deleteRef|"
                         r"createCommitOnBranch|revertPullRequest|(?:create|update|delete)BranchProtectionRule|"
                         r"(?:create|update|delete)RepositoryRuleset|updateRepository|archiveRepository|"
                         r"deleteRepository|transferRepository)\b")
_GH_WRITES = {"pr": {"merge"}, "release": {"create", "edit", "delete", "upload", "delete-asset"},
              "repo": {"edit", "delete", "archive", "unarchive", "rename", "sync"},
              "workflow": {"run", "enable", "disable"}, "run": {"rerun", "cancel", "delete"},
              "secret": {"set", "delete", "remove"}, "variable": {"set", "delete", "remove"},
              "ruleset": {"create", "edit", "delete"}, "cache": {"delete"}}
_GH_VALUE = {"-R", "--repo", "-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "-H", "--header",
             "--jq", "-q", "--template", "-t", "--hostname", "--cache", "-p", "--preview", "-b", "--body", "-m",
             "--subject", "--match-head-commit", "--author-email", "--ref", "-r", "--title", "--notes", "-n",
             "--target", "--branch", "-B", "--head", "-a", "--app", "-e", "--env", "-o", "--org"}


def _framework_slug(text):
    """Is TEXT (a URL, `owner/name` or a path `repos/owner/name/...`) a GitHub repository of RELEASE_OWNER?"""
    t = str(text or "").strip().strip("'\"")
    t = re.sub(r"^/?repos/", "", t)
    parts = t.split("/")
    if "://" in t or t.startswith("git@") or "github.com" in t:
        m = _GH_URL.search(t.split("?")[0])
        return bool(m) and m.group(1).lower() == RELEASE_OWNER
    return len(parts) >= 2 and parts[0].lower() == RELEASE_OWNER and bool(parts[1])


def _remotes(where):
    """{remote name: [url, pushurl…]} of the repository holding WHERE, from its git config."""
    g = _git_dir(where)
    out = {}
    if not g:
        return out
    try:
        text = open(os.path.join(g, "config"), encoding="utf-8", errors="replace").read()
    except OSError:
        return out
    name = None
    for line in text.splitlines():
        m = re.match(r'\s*\[remote\s+"([^"]+)"\]', line)
        if m:
            name = m.group(1)
            continue
        if line.strip().startswith("["):
            name = None
        m = re.match(r"\s*(?:push)?url\s*=\s*(\S+)", line)
        if name and m:
            out.setdefault(name, []).append(m.group(1))
    return out


_PUSH_VALUE = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}


def _git_push_target(args, where, added=None, conf=()):
    """Does `git push …` (ARGS after `push`) go to a framework repository? ADDED: remotes the same line added or
    re-pointed ({name: [url]}); CONF: (key, value) the call sets for itself. A remote name nobody can resolve, in a
    repository with a framework remote, counts as the framework (of_architect's review of PR 6: `-o x`, a remote
    added on the line)."""
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in _PUSH_VALUE and i + 1 < len(args):
            if a == "--repo":
                pos.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--repo="):
            pos.append(a.split("=", 1)[1])
        elif a.startswith("-o") and len(a) > 2 or a.startswith("--push-option="):
            pass
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    remotes = {k: list(v) for k, v in _remotes(where).items()}
    for name, urls in (added or {}).items():
        remotes[name] = list(urls)
    for k, v in conf:
        m = re.match(r"^remote\.(.+)\.(?:push)?url$", k)
        if m:
            remotes.setdefault(m.group(1), []).append(v)
        if re.match(r"^url\..*\.(?:push)?insteadof$", k) and RELEASE_OWNER in k.lower():
            return True  # the call rewrites some address to the framework's
    target = pos[0] if pos else None
    if target and ("/" in target or ":" in target) and target not in remotes:
        return _framework_slug(target)
    if target and target not in remotes:
        return any(_framework_slug(u) for us in remotes.values() for u in us)
    urls = remotes.get(target or "origin") or ([u for us in remotes.values() for u in us] if not target else [])
    return any(_framework_slug(u) for u in urls)


def _gh_write(args, where):
    """What a `gh …` call (ARGS after gh) would change on a framework repository, or None."""
    words, repo, method, fields, k = [], None, None, False, 0
    while k < len(args):
        a = args[k]
        key, _, inline = a.partition("=") if a.startswith("--") else (a, "", "")
        if key in _GH_VALUE:
            val = inline if inline else (args[k + 1] if k + 1 < len(args) else "")
            if key in ("-R", "--repo"):
                repo = val
            elif key in ("-X", "--method"):
                method = val.upper()
            elif key in ("-f", "-F", "--field", "--raw-field", "--input"):
                fields = True
            k += 1 if inline else 2
            continue
        if not a.startswith("-"):
            words.append(a)
        k += 1
    if not words:
        return None
    if words[0] == "api" and len(words) > 1:
        path = words[1].split("?")[0]
        method = method or ("POST" if fields else "GET")
        if method == "GET" or path.lstrip("/") == "graphql" and not re.search(r"\bmutation\b", " ".join(args)):
            return None
        if path.lstrip("/") == "graphql":
            body = " ".join(args)
            m = _GQL_WRITES.search(body)
            if m:  # a node id hides the repository, so these count wherever they point (review of PR 6)
                return f"gh api graphql {m.group(1)}"
            if RELEASE_OWNER in body.lower() and not re.search(r"\b(addComment|addPullRequestReview\w*|"
                                                                 r"addReaction)\b", body):
                return "gh api graphql mutation"
            return None
        here_fw = "{owner}" in path and any(_framework_slug(u) for us in _remotes(where).values() for u in us)
        if not path.lstrip("/").startswith("repos/") or not (here_fw or _framework_slug(path)) \
                or _GH_READ_PATHS.search(path):
            return None
        return f"gh api -X {method} {re.sub(r'^/?repos/', '', path)}"
    sub = words[1] if len(words) > 1 else None
    if sub not in _GH_WRITES.get(words[0], set()):
        return None
    if repo is not None:
        return f"gh {words[0]} {sub} -R {repo}" if _framework_slug(repo) else None
    return f"gh {words[0]} {sub}" if any(_framework_slug(u) for us in _remotes(where).values() for u in us) else None


def framework_writes(tool, tool_input, cwd=None, depth=0):
    """[what] a tool call would change on a GitHub repository of the organic-framework owner (G18): a git push to it,
    a merge, a release, a workflow run, a secret or variable, a repository setting, a write through gh api (a
    status, a ruleset, a ref, a file). Comments and reviews are not writes here. The repository is the one the
    command names (a URL, -R, an api path) or the one it runs in (the session's directory, `cd`, `git -C`)."""
    if tool == "Bash" and depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "framework_writes"
    if tool != "Bash" or depth > DEPTH_CAP:
        return []
    cmd = (tool_input or {}).get("command") or ""
    if "git" not in cmd and "gh" not in cmd:
        return []
    try:
        text, subs, docs, _ = _scan(cmd)
        pipes = _split(text)
    except Exception:  # noqa: BLE001 - nothing parsed, nothing named
        return []
    out = []
    for sub in subs:
        out += framework_writes(tool, {"command": sub}, cwd, depth + 1)
    for _quoted, body, opener in docs:
        if _head(opener) in _SHELLS:
            out += framework_writes(tool, {"command": body}, cwd, depth + 1)
    here, added, exported, git_al, gh_al = cwd, {}, {}, {}, {}
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                out += framework_writes(tool, {"command": stext}, here, depth + 1)
                continue
            s = _simple(stext)
            if not s or s[0] == "<read":
                continue
            head, args = s[0], s[1]
            if head == "cd":
                to = _expanduser(args[0]) if args and args[0] != "-" else _expanduser("~")
                here = to if os.path.isabs(to) else os.path.join(here or ".", to)
            elif head == "export":
                exported.update(dict(a.split("=", 1) for a in args if re.match(r"^[A-Za-z_]\w*=", a)))
            elif head in _SHELLS - {"eval"} and "-c" in args[:-1]:
                out += framework_writes(tool, {"command": args[args.index("-c") + 1]}, here, depth + 1)
            elif head == "git":
                env_here = {**exported, **_assignments(stext)}
                fw_here = any(_framework_slug(u) for us in {**_remotes(here or "."), **added}.values() for u in us)
                expanded, shell = git_expand(args, here or ".", env_here, git_al)
                if shell is not None:
                    out += framework_writes(tool, {"command": shell}, here, depth + 1)
                    continue
                if expanded is None:
                    if fw_here:
                        out.append(f"git {next((a for a in args if not a.startswith('-')), '')} (an alias or "
                                   "command the guard cannot read)")
                    continue
                args = expanded
                i, where = 0, here or "."
                while i < len(args) and args[i].startswith("-"):
                    if args[i] in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(args):
                        if args[i] == "-C":
                            where = os.path.join(where, _expanduser(args[i + 1]))
                        i += 2
                        continue
                    i += 1
                sub, rest = (args[i], args[i + 1:]) if i < len(args) else (None, [])
                words = [a for a in rest if not a.startswith("-")]
                if sub == "config" and len(words) >= 2 and words[0].lower().startswith("alias."):
                    git_al[words[0][len("alias."):].lower()] = " ".join(words[1:])  # an alias set earlier on the line
                if sub == "remote" and words[:1] in (["add"], ["set-url"]) and len(words) >= 3:
                    added[words[1]] = [words[2]]  # a remote added or re-pointed earlier on the line
                elif sub == "config" and len(words) >= 2 and re.match(r"^remote\..+\.(?:push)?url$", words[0].lower()):
                    added.setdefault(words[0].split(".", 1)[1].rsplit(".", 1)[0], []).append(words[1])
                elif sub == "push" and _git_push_target(rest, where, added, _git_config_pairs(
                        args, {**exported, **_assignments(stext)})):
                    out.append("git push")
            elif head == "gh":
                plain = [a for a in args if not a.startswith("-")]
                if plain[:2] == ["alias", "set"] and len(plain) >= 4:
                    gh_al[plain[2]] = plain[3]  # an alias set earlier on the line
                    continue
                expanded, shell = gh_expand(args, gh_al)
                if shell is not None:
                    out += framework_writes(tool, {"command": shell}, here, depth + 1)
                    continue
                if expanded is None:
                    named = next((args[j + 1] for j in range(len(args) - 1) if args[j] in ("-R", "--repo")), None)
                    if (named and _framework_slug(named)) or any(_framework_slug(u) for us in _remotes(
                            here or ".").values() for u in us):
                        out.append(f"gh {plain[0] if plain else ''} (an alias or extension the guard cannot read)")
                    continue
                w = _gh_write(expanded, here or ".")
                if w:
                    out.append(w)
    return out


# ------------------------------------------------------------------ files under a credential root (G14)
# docs/registry.md, Credentials (of_architect, 8ef7bc0, 2026-11-11, from a user organization): under each
# organization's credential root a position may read or use only the files a credential it lists names. This part
# finds the local files a tool call reads or uses; of_credentials decides. Reading includes using: a path passed to
# any command (`--kubeconfig`, `ssh -i`, `curl -H @file`, a script's argument) counts, a write destination does not,
# and neither does a command that shows only names or metadata (ls, stat, test, wc, echo). A directory counts as
# its files for a command that reads it recursively. ssh's remote command and an exec into a container run
# elsewhere: their words are not local paths.
_META_HEADS = {"ls", "stat", "test", "[", "[[", "du", "chmod", "chown", "touch", "mkdir", "rmdir", "realpath",
               "readlink", "dirname", "basename", "wc", "file", "echo", "printf", "cd", "pushd", "popd", "mv", "rm",
               "unlink", "shred", "trash", "which", "type", "true", "false", "exit", "export", "unset", "local",
               "read", "sleep", "date", "set"}
_RECURSE_ALWAYS = {"rg", "ag", "tar", "zip", "rsync", "7z", "du"}
_DEST_LAST = {"cp", "scp", "rsync", "install", "ln", "ditto"}
_PATHISH = re.compile(r"^(?:~|\$\{?HOME\}?|/|\.{1,2}/|[\w.@%+-]+/)")
_CRED_WALK_CAP = 5000


def _cred_word(w):
    """The path inside one argument word: --opt=PATH, @PATH, name=@PATH, if=PATH; else the word."""
    m = re.match(r"^(?:--?[\w-]+=|[\w.-]*=@|@|if=)(.+)$", w)
    return m.group(1) if m else w


def _cred_expand(w, env, cwd):
    w = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), m.group(0)), w)
    w = re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", w)
    if "$" in w or "`" in w or not w or "\n" in w or " " in w:
        return None
    w = _expanduser(w)
    if not os.path.isabs(w):
        if not cwd or not _PATHISH.match(w) and "/" not in w and not os.path.exists(os.path.join(cwd, w)):
            return None
        w = os.path.join(cwd, w)
    return os.path.normpath(w)


def _cred_files(p, recursive):
    """The files a path names: a glob's matches, a directory's files when read recursively (capped), the file."""
    import glob as _glob
    if any(c in p for c in "*?["):
        out = []
        for q in _glob.glob(p, recursive=True)[:_CRED_WALK_CAP]:
            out += _cred_files(q, recursive) if os.path.isdir(q) else [q]
        return out
    if os.path.isdir(p):
        if not recursive:
            return []
        out = []
        for d, dirs, files in os.walk(p):
            dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
            out += [os.path.join(d, f) for f in files]
            if len(out) >= _CRED_WALK_CAP:
                break
        return out[:_CRED_WALK_CAP]
    return [p]


def _cred_args(head, args):
    """The argument words that may be local paths the command reads or uses."""
    if head in _META_HEADS:
        return []
    if head == "find":
        return [a for a in args if not a.startswith("-")] if any(a in ("-exec", "-execdir", "-ok") for a in args) \
            else []
    if head == "ssh":  # only option values (-i key, -F config, -o IdentityFile=…): the rest runs remotely
        out, i = [], 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in _SSH_VALUE_OPTS and i + 1 < len(args):
                out.append(args[i + 1])
                i += 2
            else:
                out.append(args[i])
                i += 1
        return out
    if head in _RUNNERS and any(a in ("exec", "shell", "run") for a in args):
        k = next(k for k, a in enumerate(args) if a in ("exec", "shell", "run"))
        return args[:k]  # the flags before the verb (--kubeconfig, --context); what runs inside is remote
    if head == "tee":
        return []
    if head == "git":  # a commit message is text, not a file (`-F file` is still read)
        return [a for k, a in enumerate(args) if not (k and args[k - 1] in ("-m", "--message"))
                and not a.startswith("--message=")]
    if head in _DEST_LAST:
        pos = [k for k, a in enumerate(args) if not a.startswith("-")]
        return [a for k, a in enumerate(args) if not pos or k != pos[-1]]
    return list(args)


def credential_refs(tool, tool_input, cwd=None, interesting=None, depth=0):
    """[(path, how)]: local files this tool call reads or uses, absolute and normalized, for which `interesting(path)`
    is true (a path under a credential root or one a credential names). `how` is the command word or the tool."""
    ti = tool_input or {}
    keep = interesting or (lambda p: True)
    if tool in ("Read", "Grep", "Edit", "MultiEdit", "NotebookEdit"):
        p = _cred_expand(str(ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""), {}, cwd)
        if not p or not keep(p):
            return []
        files = _cred_files(p, recursive=tool == "Grep")
        return [(f, tool) for f in files if keep(f)]
    if tool != "Bash":
        return []
    return [(p, h) for p, h in _cred_bash(str(ti.get("command") or ""), cwd, dict(os.environ), keep, depth)]


def _cred_bash(cmd, cwd, env, keep, depth):
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_cred_bash"
        return []
    out = []
    try:
        text, subs, docs, _ = _scan(cmd)
    except Exception:  # noqa: BLE001 - nothing found is the fail-open answer for an organization's rule
        return []
    for sub in subs:
        out += _cred_bash(sub, cwd, env, keep, depth + 1)
    for quoted, body, opener in docs:
        head = _head(opener)
        if head in _SHELLS:
            out += _cred_bash(body, cwd, env, keep, depth + 1)
        elif head in _INTERPRETERS:
            out += _cred_code(body, cwd, env, keep)
    try:
        pipes = _split(text)
    except ValueError:
        return out
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                out += _cred_bash(stext, cwd, env, keep, depth + 1)
                continue
            try:
                toks = _tokens(stext)
            except ValueError:
                continue
            assigns = _assignments(stext)
            s = _simple(stext)
            if not s:
                env.update({k: v for k, v in assigns.items() if "$(" not in v})  # `S=~/.config/x; cat $S/y`
                continue
            if s[0] == "<read":
                p = _cred_expand(s[1], env, cwd)
                if p and keep(p):
                    out += [(f, "<") for f in _cred_files(p, False) if keep(f)]
                continue
            head, args, words, to_file, herestr = s
            local = dict(env, **assigns)
            if head in ("cd", "pushd") and args:
                d = _cred_expand(args[-1], local, cwd)
                cwd = d if d else cwd
                continue
            for k, w in enumerate(toks[:-1]):  # input redirections the parser kept for itself: `< file`
                if w == "<":
                    p = _cred_expand(toks[k + 1], local, cwd)
                    if p and keep(p):
                        out += [(f, "<") for f in _cred_files(p, False) if keep(f)]
            if head != "ssh" and head not in _RUNNERS:  # sh -c, eval, sudo/timeout/xargs sh -c: run here
                for body in _nested(head, args):
                    out += _cred_bash(body, cwd, local, keep, depth + 1)
            if head in _INTERPRETERS:
                for flag in ("-c", "-e"):
                    if flag in args[:-1]:
                        out += _cred_code(args[args.index(flag) + 1], cwd, local, keep)
            if herestr is not None and head in _INTERPRETERS:
                out += _cred_code(herestr, cwd, local, keep)
            recursive = head in _RECURSE_ALWAYS or bool(_RECURSIVE & set(args)) or (
                head == "find" and any(a in ("-exec", "-execdir", "-ok") for a in args))
            for w in _cred_args(head, args):
                p = _cred_expand(_cred_word(w), local, cwd)
                if p and keep(p):
                    out += [(f, head) for f in _cred_files(p, recursive) if keep(f)]
    return out


def _cred_code(code, cwd, env, keep):
    """Path literals in interpreter code, as G8 reads them (a triple-quoted block is carried text)."""
    carried = re.sub(r"'''.*?'''|\"\"\".*?\"\"\"", " ", code, flags=re.S)
    out = []
    for m in _LITERAL.finditer(carried):
        lit = m.group(2).strip()
        if " " in lit or "/" not in lit:
            continue
        p = _cred_expand(lit, env, cwd)
        if p and keep(p):
            out += [(f, "code") for f in _cred_files(p, False) if keep(f)]
    return out


# ------------------------------------------------------------------ scratch quota (G15)
# A user organization's platform_owner (msg 01a1c8f3) and of_platform (msg 01a1c950, cec3833), 2026-11-11: free
# disk fell from 46 to 28 GiB in a night, nearly all of it live sessions' scratch folders (worktrees of 1.5 GB, data
# copies); six positions freed about 17 GB only after being told. A session the pulse lists in scratch-quota.json
# (over 6 GiB until back under 3 GiB) may read and delete in its scratch folder, never write into it.
_SCRATCH_READ_DELETE = {
    "ls", "du", "df", "find", "cat", "head", "tail", "less", "more", "grep", "rg", "egrep", "fgrep", "wc", "stat",
    "file", "tree", "pwd", "cd", "pushd", "popd", "echo", "printf", "test", "[", "[[", "true", "false", "sleep", "which",
    "type", "rm", "rmdir", "unlink", "trash", "shred", "sort", "uniq", "cut", "awk", "jq", "yq", "diff", "cmp", "md5",
    "shasum", "sha256sum", "xxd", "od", "strings", "basename", "dirname", "realpath", "readlink", "date", "of", "herdr",
    "ps", "kill", "lsof", "sed", "git", "exit", "export", "unset", "set", "local", "read", "wait", ".", "source",
    "chmod", "tr", "break", "continue", "return", "gh", "kubectl", "k", "curl", "wget", "tar", "umask"}
_GIT_READ_DELETE = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "ls-tree", "remote", "clean",
                    "gc", "prune", "describe", "blame", "grep", "shortlog", "cat-file", "for-each-ref", "config",
                    "reflog", "rm", "help", "version"}
_COPY_HEADS = {"cp", "mv", "rsync", "scp", "install", "ln", "ditto"}


def _under_roots(p, roots):
    return bool(p) and any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def _scratch_path(w, env, cwd):
    """An argument word as an absolute path, when it can be one (the word itself, --opt=VALUE, NAME=VALUE)."""
    w = re.sub(r"^(?:--?[\w-]+=|[\w.-]*=@|@|(?:of|if)=)", "", w)
    w = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), m.group(0)), w)
    w = re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", w)
    if not w or "$" in w or "`" in w or "\n" in w:
        return None
    w = _expanduser(w)
    if not os.path.isabs(w):
        if not cwd or w.startswith("-") or not (w.startswith(".") or "/" in w):
            return None
        w = os.path.join(cwd, w)
    return os.path.normpath(w)


def _git_reads_or_deletes(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    sub = args[i] if i < len(args) else ""
    if sub == "worktree":
        return (args[i + 1:i + 2] or [""])[0] in ("list", "remove", "prune")
    if sub == "stash":
        return (args[i + 1:i + 2] or [""])[0] in ("list", "show", "drop", "clear")
    return sub in _GIT_READ_DELETE


def _scratch_target(head, args, env, cwd, roots):
    """For a command that mostly reads (gh, kubectl, curl, wget, tar), the path it writes into the folder, or None:
    `gh run|release download -D`, `gh repo clone`, `kubectl cp` to a local path, `curl -o/-O/--output-dir`,
    `wget -O/-P` (or into the working folder), `tar -x` into it, `tar -c -f` an archive in it."""
    def p(w):
        q = _scratch_path(w, env, cwd)
        return q if _under_roots(q, roots) else None

    def val(*names):
        for k, a in enumerate(args):
            for n in names:
                if a == n and k + 1 < len(args):
                    return args[k + 1]
                if n.startswith("--") and a.startswith(n + "="):
                    return a.split("=", 1)[1]
                if not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                    return a[len(n):]
        return None
    here = _under_roots(cwd, roots)
    pos = [a for a in args if not a.startswith("-")]
    if head == "gh":
        if pos[:2] in (["run", "download"], ["release", "download"]):
            d = val("-D", "--dir")
            return p(d) if d else (cwd if here else None)
        if pos[:2] == ["repo", "clone"]:
            return p(pos[3]) if len(pos) > 3 else (cwd if here else None)
        return None
    if head in ("kubectl", "k") or head in _RUNNERS:  # kubectl cp, docker cp: to a local path
        if pos[:1] == ["cp"] and len(pos) >= 3 and ":" not in pos[-1]:
            return p(pos[-1]) or (cwd if here and not os.path.isabs(pos[-1]) else None)
        return None
    if head == "curl":
        o = val("-o", "--output")
        if o:
            return p(o)
        d = val("--output-dir")
        if d:
            return p(d)
        return cwd if here and any(a in ("-O", "--remote-name", "-J") or (re.match(r"^-[a-zA-Z]*O", a) and
                                                                         not a.startswith("--")) for a in args) else None
    if head == "wget":
        o = val("-O", "--output-document")
        if o:
            return p(o) if o != "-" else None
        d = val("-P", "--directory-prefix")
        return p(d) if d else (cwd if here else None)
    if head == "tar":
        mode = next((a.lstrip("-") for a in args if a and not a.startswith("--") and re.match(r"^-?[A-Za-z]+$", a)), "")
        if "x" in mode or "--extract" in args or "--get" in args:
            d = val("-C", "--directory")
            return p(d) if d else (cwd if here else None)
        if "c" in mode or "r" in mode or "u" in mode or "--create" in args:
            f = val("-f", "--file")
            if f is None:  # `tar -czf out.tgz …`: the archive is the word after the bundled flags
                k = next((k for k, a in enumerate(args) if a.lstrip("-") == mode), None)
                f = args[k + 1] if k is not None and "f" in mode and k + 1 < len(args) else None
            return p(f) if f else None
        return None
    return None


def scratch_write(tool, tool_input, cwd, roots):
    """What in this tool call writes into one of `roots` (a session's scratch folders), or None. Write, Edit,
    MultiEdit and NotebookEdit there; in Bash, an output redirection into it, a copy whose destination is in it, any
    other command that names a path in it or runs with its working folder there, unless the command only reads or
    deletes (rm, git worktree remove, du, ls, cat, grep, sed without -i, git status …)."""
    roots = [os.path.normpath(r) for r in roots or [] if r]
    if not roots:
        return None
    ti = tool_input or {}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = _scratch_path(str(ti.get("file_path") or ti.get("notebook_path") or ""), {}, cwd)
        return f"{tool} {os.path.basename(p)}" if _under_roots(p, roots) else None
    if tool != "Bash":
        return None
    return _scratch_bash(str(ti.get("command") or ""), cwd, dict(os.environ), roots, 0)


def _scratch_bash(cmd, cwd, env, roots, depth):
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_scratch_bash"
        return None
    try:
        text, subs, docs, _ = _scan(cmd)
    except Exception:  # noqa: BLE001 - an unparsed line is left to the critical rules
        return None
    for sub in subs:
        hit = _scratch_bash(sub, cwd, env, roots, depth + 1)
        if hit:
            return hit
    for _quoted, body, opener in docs:
        if _head(opener) in _SHELLS:
            hit = _scratch_bash(body, cwd, env, roots, depth + 1)
            if hit:
                return hit
    try:
        pipes = _split(text)
    except ValueError:
        return None
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                hit = _scratch_bash(stext, cwd, env, roots, depth + 1)
                if hit:
                    return hit
                continue
            try:
                toks = _tokens(stext)
            except ValueError:
                continue
            assigns = _assignments(stext)
            local = dict(env, **assigns)
            for k, w in enumerate(toks[:-1]):  # `> file`, `>> file`, `&> file`: an output redirection into the folder
                if w and set(w) <= _REDIR and ">" in w and not w.endswith("&"):
                    p = _scratch_path(toks[k + 1], local, cwd)
                    if _under_roots(p, roots):
                        return f"output written to {os.path.basename(p)}"
            s = _simple(stext)
            if not s:
                env.update({k: v for k, v in assigns.items() if "$(" not in v})
                continue
            if s[0] == "<read":
                continue
            head, args = s[0], s[1]
            if head.startswith("__OFSUB__"):
                continue  # a $( ) as the command word: its own command was checked above
            if head in ("cd", "pushd") and args:
                d = _scratch_path(args[-1], local, cwd)
                cwd = d or cwd
                continue
            if head in _SHELLS or (head not in ("ssh",) and head not in _RUNNERS):
                for body in _nested(head, args) if head != "ssh" and head not in _RUNNERS else ():
                    hit = _scratch_bash(body, cwd, local, roots, depth + 1)
                    if hit:
                        return hit
            if head in ("gh", "kubectl", "k", "curl", "wget", "tar") or (head in _RUNNERS and args[:1] == ["cp"]):
                t = _scratch_target(head, args, local, cwd, roots)
                if t:
                    return f"{head} writing {os.path.basename(t)}"
                continue
            if head == "ssh" or head in _RUNNERS:
                continue  # it runs elsewhere
            if head in _COPY_HEADS:
                pos = [a for a in args if not a.startswith("-")]
                dest = _scratch_path(pos[-1], local, cwd) if pos else None
                if _under_roots(dest, roots) or (len(pos) == 1 and _under_roots(cwd, roots)):
                    return f"{head} into the folder"
                continue
            reads = head in _SCRATCH_READ_DELETE and not (head == "sed" and any(
                a == "-i" or a.startswith(("-i", "--in-place")) for a in args)) and not (
                head == "git" and not _git_reads_or_deletes(args))
            if reads:
                continue
            here = _under_roots(cwd, roots)
            named = next((p for p in (_scratch_path(a, local, cwd) for a in args) if _under_roots(p, roots)), None)
            if head == "git" and not named:  # git -C <dir>
                i = next((k for k, a in enumerate(args[:-1]) if a == "-C"), None)
                here = _under_roots(_scratch_path(args[i + 1], local, cwd), roots) if i is not None else here
            if named or here:
                sub = f" {args[0]}" if head == "git" and args else ""
                return f"{head}{sub} {'on ' + os.path.basename(named) if named else 'run in the folder'}"
    return None


# ------------------------------------------------------------------ regulators and authorities (G16)
# The founder through of_conductor (hand-over 630f29e9, 2026-11-11): an agent never contacts a regulator or an
# authority without his explicit word. A call that would send something to one is found here: mail to its address,
# an HTTP write to its host (or carrying its address, as a mail API does), interpreter code that posts or mails there,
# and an MCP connector or browser acting on its address or portal. An authority is a government domain of any
# country, a host in bin/rules/authorities.txt, or a contact an organization lists in its registry's
# `authorities:` (of_architect, msg 01a1caa6; the hook passes them through use_authorities). The hook denies it
# unless a signed grant from the tenant names that one contact for the position (docs/registry.md, Grants, scope
# `contacts`). Reading their public pages stays open (WebFetch, a plain GET, a citation in a document): 21 days of
# calls held only such reads.
AUTHORITIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "authorities.txt")
_GOV_HOST = re.compile(r"(?:^|\.)(?:gov|mil)$|(?:^|\.)(?:gov|gob|gouv|govt|go|gv|gub)\.[a-z]{2}$")
_MAIL_ADDR = re.compile(r"(?<![\w.+-])[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])", re.I)
_SCHEME_URL = re.compile(r"\b(?:https?|wss?|ftp)://(?:[^\s/@'\"<>]+@)?((?:[a-z0-9-]+\.)+[a-z]{2,})\.?(?![\w-])", re.I)
_HOSTLIKE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)
_MAIL_HEADS = {"mail", "mailx", "nail", "s-nail", "sendmail", "ssmtp", "msmtp", "swaks", "mutt", "neomutt", "mpack",
               "himalaya", "aerc", "sendemail", "mailsend", "osascript"}
_HTTP_HEADS = {"curl", "curlie", "wget", "http", "https", "xh", "xhs"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CODE_SENDS = re.compile(r"\.(?:post|put|patch|delete)\s*\(|smtplib|send_?mail|send_message|[(,]\s*(?:data|json|files)"
                         r"\s*=|method\s*[=:]\s*['\"](?:POST|PUT|PATCH|DELETE)|request\s*\(\s*['\"](?:POST|PUT|PATCH|"
                         r"DELETE)", re.I)  # urllib's Request(url) alone reads (2026-11-11 replay: CISA's catalogue)
_MCP_READS = {"get", "list", "search", "read", "fetch", "find", "query", "describe", "view", "show", "lookup",
              "retrieve", "count", "status", "download", "summarize", "summary", "inspect", "preview", "screenshot",
              "text", "info", "history", "logs"}
_MCP_WRITES = {"send", "reply", "forward", "compose", "draft", "post", "submit", "create", "update", "put", "patch",
               "delete", "upload", "publish", "share", "invite", "navigate", "goto", "open", "click", "type", "fill",
               "form", "press", "write", "set", "schedule", "add", "move", "execute", "run", "javascript", "evaluate",
               "computer", "message", "email", "mail", "call", "act"}
_URL_KEYS = {"url", "uri", "href", "link", "domain", "host", "website", "site", "endpoint"}
_authority_cache = {}
_ORG_AUTHORITIES = {"hosts": frozenset(), "addresses": frozenset()}


def use_authorities(contacts):
    """The organizations' own authorities (their registries' `authorities:` contacts: hosts and e-mail addresses),
    added to the framework's list for this process."""
    contacts = {str(c).strip().lower().strip(".") for c in contacts or () if str(c).strip()}
    _ORG_AUTHORITIES.update(hosts=frozenset(c for c in contacts if "@" not in c),
                            addresses=frozenset(c for c in contacts if "@" in c))


def authorities():
    """The host suffixes of AUTHORITIES_FILE, re-read when it changes, and the organizations' own hosts."""
    try:
        stamp = os.path.getmtime(AUTHORITIES_FILE)
    except OSError:
        stamp = None
    if _authority_cache.get("stamp") != stamp:
        hosts = set()
        try:
            with open(AUTHORITIES_FILE, encoding="utf-8") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip().lower().strip(".")
                    if line:
                        hosts.add(line)
        except OSError:
            pass
        _authority_cache.update(stamp=stamp, hosts=hosts)
    return _authority_cache["hosts"] | _ORG_AUTHORITIES["hosts"]


def authority_host(host):
    """Is this host a regulator's or an authority's: a government domain of any country, or one listed?"""
    h = (host or "").lower().strip(".")
    if "." not in h:
        return False
    if _GOV_HOST.search(h):
        return True
    parts, listed = h.split("."), authorities()
    return any(".".join(parts[i:]) in listed for i in range(len(parts)))


def _url_host(word):
    """The host of a URL written with or without its scheme (`portal.vara.ae/form`), else None."""
    w = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(word).strip("'\"<>()"), flags=re.I)
    w = re.split(r"[/?#]", w, maxsplit=1)[0].rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    return w.lower() if _HOSTLIKE.fullmatch(w) else None


def authority_targets(text):
    """[(contact, kind)] in a text: an authority's e-mail address ('mail') and the host of a URL with a scheme ('web')."""
    out = [(m.group(0).lower(), "mail") for m in _MAIL_ADDR.finditer(text)
           if authority_host(m.group(1)) or m.group(0).lower() in _ORG_AUTHORITIES["addresses"]]
    out += [(m.group(1).lower(), "web") for m in _SCHEME_URL.finditer(text) if authority_host(m.group(1))]
    return list(dict.fromkeys(out))


def _mentions_authority(text):
    return any(authority_host(m.group(0)) for m in _HOSTLIKE.finditer(text)) or any(
        m.group(0).lower() in _ORG_AUTHORITIES["addresses"] for m in _MAIL_ADDR.finditer(text))


def _http_writes(head, args):
    """Does this HTTP client call send data or use a writing method?"""
    if head in ("curl", "curlie"):
        for k, a in enumerate(args):
            if a in ("-d", "-F", "-T", "--json") or a.startswith(("--data", "--form", "--upload-file", "--json")):
                return True
            if re.fullmatch(r"-[a-zA-Z]*[dFT][a-zA-Z0-9]*", a):  # bundled short flags: -sd, -sSF
                return True
            method = (args[k + 1] if a in ("-X", "--request") and k + 1 < len(args) else
                      a[2:] if a.startswith("-X") and len(a) > 2 else a.split("=", 1)[1] if a.startswith("--request=")
                      else "")
            if method.strip("'\"").upper() in _WRITE_METHODS:
                return True
        return False
    if head == "wget":
        return any(a.startswith(("--post-data", "--post-file", "--body-data", "--body-file")) or (
            a.startswith("--method") and a.split("=", 1)[-1].upper() in _WRITE_METHODS) for a in args)
    return any(a.upper() in _WRITE_METHODS or a in ("-f", "--form") or re.match(r"^[\w.-]+(?:=|:=|=@|@)", a)
               for a in args if not a.startswith(("http://", "https://")))


def _authority_code(code, how):
    """Interpreter code that names an authority's address or URL and posts or mails."""
    found = authority_targets(code)
    return [(t, how) for t, _ in found] if found and _CODE_SENDS.search(code) else []


def _authority_bash(cmd, depth=0):
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "_authority_bash"
    if depth > DEPTH_CAP or not _mentions_authority(cmd):
        return []
    out = []
    try:
        text, subs, docs, _ = _scan(cmd)
        pipes = _split(text)
    except Exception:  # noqa: BLE001 - unparsed: a mail or HTTP command beside an authority's address counts
        words = set(re.findall(r"[\w.-]+", cmd))
        if words & (_MAIL_HEADS | _HTTP_HEADS):
            out += [(t, "unparsed command") for t, _ in authority_targets(cmd)]
        return out
    for sub in subs:
        out += _authority_bash(sub, depth + 1)
    heads = set()
    for quoted, body, opener in docs:
        head = _head(opener)
        heads.add(head)
        if head in _SHELLS:
            out += _authority_bash(body, depth + 1)
        elif head in _INTERPRETERS:
            out += _authority_code(body, head)
        elif head in _HTTP_HEADS:  # `curl -d @- <<EOF`: the heredoc is the request body
            out += [(t, head) for t, kind in authority_targets(body) if kind == "mail"]
    env = {}
    for pipe in pipes:
        for kind, stext in pipe:
            if kind == "sub":
                out += _authority_bash(stext, depth + 1)
                continue
            assigns = _assignments(stext)
            env.update({k: v for k, v in assigns.items() if "$(" not in v})
            s = _simple(stext)
            if not s or s[0] == "<read":
                continue
            head, args = s[0], [re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1), m.group(0)), a) for a in s[1]]
            heads.add(head)
            for body in _nested(head, args):  # sh -c, eval, and ssh's or an exec's remote command too
                out += _authority_bash(body, depth + 1)
            if head in _INTERPRETERS:
                for flag in ("-c", "-e"):
                    if flag in args[:-1]:
                        out += _authority_code(args[args.index(flag) + 1], head)
                if s[4] is not None:
                    out += _authority_code(s[4], head)
            if head in _HTTP_HEADS and _http_writes(head, args):
                urls = [a.split("=", 1)[1] if a.startswith("--url=") else a for a in args
                        if not a.startswith("-") or a.startswith("--url=")]
                out += [(h, head) for h in (_url_host(u) for u in urls) if h and authority_host(h)]
                out += [(t, head) for t, kind in authority_targets(" ".join(args)) if kind == "mail"]
    if heads & _MAIL_HEADS:  # a mail command in the line: every authority address in it is a recipient
        out += [(t, "mail") for t, kind in authority_targets(cmd) if kind == "mail"]
    return out


def _strings(o, key=None):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(v, str(k).lower())
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v, key)
    elif isinstance(o, str):
        yield key, o


def authority_contacts(tool, tool_input):
    """[(contact, how)] a tool call would reach at a regulator or authority (docs/guard.md G16): an e-mail address,
    or a host for a portal or an API. Empty for reads: WebFetch, WebSearch, a plain GET, Read, a file written."""
    if not isinstance(tool_input, dict):
        return []
    if tool == "Bash":
        return list(dict.fromkeys(_authority_bash(str(tool_input.get("command") or ""))))
    if not str(tool).startswith("mcp__"):
        return []
    words = set(re.split(r"[^a-z0-9]+", str(tool).split("__", 2)[-1].lower())) - {""}
    if not words & _MCP_WRITES and words & _MCP_READS:
        return []
    out = []
    for key, value in _strings(tool_input):
        out += [(t, tool) for t, _ in authority_targets(value)]
        if key in _URL_KEYS:
            h = _url_host(value)
            if h and authority_host(h):
                out.append((h, tool))
    return list(dict.fromkeys(out))


# G25: typing into a pane is declared, never taken for a person's words (of_server's trust review of
# organic-framework PR 168; of_architect's conditions, msg 01a1d9ab-6b08). The hook gives origin human to any text
# that reaches a pane undeclared, so text an agent's command types through herdr's CLI would count as the person's
# prompt (closing their asks, a loop's decided yes). The guard declares it instead of denying it: 160 distinct agent
# commands typed into other panes in 21 days, from about ten positions, live flows all. Text it can read is declared
# by its digest; typing it cannot read (keys, a substitution, a variable) is declared for the whole target pane
# ("doubt downgrades"). A declaration only downgrades a prompt to injected.
_HERDR_TYPING = {("pane", "send-text"): "text", ("pane", "run"): "text", ("agent", "prompt"): "text",
                 ("pane", "send-keys"): "keys", ("agent", "send-keys"): "keys"}
_HERDR_REMOTE_OPTS = ("--machine", "--remote")  # typing on another machine: not declared here (docs/guard.md G25)
# the typing subcommands' own options (`herdr agent prompt <target> <text> [--wait] [--until STATUS]... [--timeout
# MS]`): a value an option takes is never the target (of_secrets-review on PR 170); an option not known here makes the
# target and the text a doubt
_HERDR_OPT_VALUE = {"--until", "--timeout"}
_HERDR_OPT_FLAG = {"--wait"}
_TMUX_GLOBAL_VALUE = {"-L", "-S", "-f", "-T"}
_HERDR_PANE = re.compile(r"^w\w+:p\w+$")
_OWN_PANE = re.compile(r"^\$\{?HERDR_PANE_ID\}?$")
_BRACKETED_PASTE = re.compile(r"\x1b\[20[01]~")


def typed_digest(text):
    """The digest a declaration and the hook compare (of_architect's condition 2: normalized on both sides): bracketed
    paste markers, CR and CRLF, and the trailing Enter or newline removed, then stripped."""
    import hashlib
    t = _BRACKETED_PASTE.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(t.encode()).hexdigest()


def _unread(word):
    return "$" in word or "__OFSUB__" in word or "`" in word


def typing_declarations(cmd, depth=0):
    """[{target, kind, sha256}] for each place a shell command types into a pane through herdr's CLI: target is
    ('pane', id), ('agent', name or id), ('own', None) for $HERDR_PANE_ID, or ('unknown', None); sha256 is the
    typed text's typed_digest, or None when the guard cannot read it (keys, a substitution, a variable). Pure: the
    hook writes the declarations (of_hook._declare_typing), so a replay can call it."""
    if depth > DEPTH_CAP:
        _CAPPED[0] = _CAPPED[0] or "typing_declarations"
        return []
    out = []
    try:
        text, subs, docs, _ = _scan(cmd)
        pipes = _split(text)
    except (ValueError, IndexError, RecursionError):
        return [{"target": ("unknown", None), "kind": "unparsed", "sha256": None}] if re.search(r"\b(?:herdr|tmux)\b", str(cmd)) else []
    for sub in subs:
        out += typing_declarations(sub, depth + 1)
    for _, body, opener in docs:
        if _head(opener) in _SHELLS:
            out += typing_declarations(body, depth + 1)
    for pipe in pipes:
        for kind, raw in pipe:
            if kind == "sub":
                out += typing_declarations(raw, depth + 1)
                continue
            try:
                s = _simple(raw)
            except (ValueError, IndexError):
                s = None
            if not s or not isinstance(s[1], list):
                continue
            if s[0] in _SHELLS:  # bash -c '…', sh -c, eval: the string is the command (of_review on PR 170)
                if s[0] == "eval":
                    out += typing_declarations(" ".join(s[1]), depth + 1)
                else:
                    flags = [k for k, a in enumerate(s[1]) if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a)]
                    if flags and flags[0] + 1 < len(s[1]):
                        out += typing_declarations(s[1][flags[0] + 1], depth + 1)
                continue
            if s[0] == "tmux":
                out += _tmux_typing(list(s[1]))
                continue
            if s[0] != "herdr":
                continue
            args = list(s[1])
            remote = False
            while args and args[0].startswith("--"):
                opt = args.pop(0)
                remote = remote or opt.split("=", 1)[0] in _HERDR_REMOTE_OPTS
                if "=" not in opt and args:
                    args.pop(0)
            how = _HERDR_TYPING.get(tuple(args[:2]))
            if not how or remote:
                continue
            rest, doubt, k = [], False, 2
            while k < len(args):
                a = args[k]
                if a in _HERDR_OPT_VALUE:
                    k += 2
                    continue
                if a.startswith("-") and a != "-" and not (a.startswith("--") and "=" in a) and a not in _HERDR_OPT_FLAG:
                    doubt = True  # an option not known here: which word is the target is a doubt
                if not a.startswith("-") or a == "-":
                    rest.append(a)
                k += 1
            target = rest[0] if rest else ""
            if _OWN_PANE.match(target) and not doubt:
                tgt = ("own", None)
            elif doubt or not target or _unread(target) or re.search(r"[{}*?\[\]]", target):
                tgt = ("unknown", None)  # also `{}` from xargs and a glob (of_review on PR 170)
            elif args[0] == "pane" or _HERDR_PANE.match(target):
                tgt = ("pane", target)
            else:
                tgt = ("agent", target)
            payload = rest[1:]
            readable = how == "text" and not doubt and len(payload) == 1 and not _unread(payload[0]) and payload[0] != "-"
            out.append({"target": tgt, "kind": " ".join(args[:2]),
                        "sha256": typed_digest(payload[0]) if readable else None})
    return out


def _tmux_typing(args):
    """G25 for `tmux send-keys` (how agents type on the remote surface; of_review on organic-framework PR 170):
    [{target ('tmux', pane or target) or ('tmux-own', None), kind, sha256}], one for each send-keys of a command
    sequence (`\\;`, `';'`, or a word that ends in `;`). Options are read as getopt reads them, letter by letter, so
    `-lt %3` and `-lt%3` name %3. Literal text (-l, one word) is read; keys are typing the guard cannot read."""
    while args and (args[0] in _TMUX_GLOBAL_VALUE or re.match(r"^-[2CDluvV]+$", args[0])):
        args = args[2:] if args[0] in _TMUX_GLOBAL_VALUE else args[1:]
    commands, cur = [], []
    for w in args:
        if w == ";":
            commands.append(cur)
            cur = []
        elif w.endswith(";") and not w.endswith("\\;"):
            cur.append(w[:-1])
            commands.append(cur)
            cur = []
        else:
            cur.append(w)
    commands.append(cur)
    out = []
    for cmd in commands:
        if not cmd or cmd[0] not in ("send-keys", "send"):
            continue
        target, literal, keys, k, doubt = None, False, [], 1, False
        while k < len(cmd):
            a = cmd[k]
            if a == "--":
                keys += cmd[k + 1:]
                break
            if a.startswith("-") and len(a) > 1 and not keys:  # getopt: options until the first key
                for n, ch in enumerate(a[1:], 1):
                    if ch in "cNt":  # takes a value: the rest of the word, else the next word
                        val = a[n + 1:]
                        if not val and k + 1 < len(cmd):
                            k += 1
                            val = cmd[k]
                        target = val if ch == "t" else target
                        break
                    literal = literal or ch == "l"
                    doubt = doubt or ch in "HKX" or ch not in "FHKlMRX"
                k += 1
                continue
            keys.append(a)
            k += 1
        if target is None:
            tgt = ("tmux-own", None)
        elif _unread(target) or re.search(r"[{}*?\[\]]", target):
            tgt = ("unknown", None)
        else:
            tgt = ("tmux", target)
        readable = literal and not doubt and len(keys) == 1 and not _unread(keys[0])
        out.append({"target": tgt, "kind": "tmux send-keys", "sha256": typed_digest(keys[0]) if readable else None})
    return out