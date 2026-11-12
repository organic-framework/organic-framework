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
import warnings

import of_classify

CRITICAL_DENY_BASH = re.compile(
    r"(?:^|[;&|(\s])(?:printenv\b|env\s*(?:$|[|;>&])|export\s+-p\b|set\s*(?:$|[|;>&])|"
    r"echo\s+[^|;&]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\w*|"
    r"security\s+find-(?:generic|internet)-password\b[^|;&]*-w)", re.I)
READERS = re.compile(r"(?:^|[;&|(\s])(?:cat|less|more|head|tail|bat|strings|xxd|od|base64|jq|yq|sed|awk|grep|rg|"
                     r"cp|scp|curl|python3?|node)\b", re.I)


_READER_WORDS = {"cat", "less", "more", "head", "tail", "bat", "strings", "xxd", "od", "base64", "jq", "yq", "sed",
                 "awk", "grep", "rg", "python", "python3", "node", "tar", "zip", "gzip", "gunzip", "gpg", "openssl"}
_COPY = {"cp", "scp", "rsync"}
_FILE_READERS = _READER_WORDS | _COPY
_COPY_VALUE_OPTS = {"-i", "-F", "-o", "-c", "-l", "-P", "-S", "-e", "--rsh", "-T", "--exclude", "--include",
                    "--chmod", "-f", "--filter", "--files-from", "--out-format"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "eval"}
_INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "php", "osascript"}
_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "stdbuf", "xargs", "builtin", "doas"}
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
_CRED_WORD = re.compile(r"(?:^|[._-])(?:password|passwd|secrets?|tokens?|credentials?|creds|header|auth|authkey"
                        r"|apikey|api-key|dsn)(?=$|[._-])|.\.env$", re.I)
_NO_CRED = re.compile(r"(?:^|[._-])(?:example|sample|template|placeholder|empty)(?=$|[._-])"
                      r"|\.(?:py|pyc|sh|bash|zsh|go|rs|js|mjs|ts|tsx|md|j2|tpl|tf|rb|pl|lua|html|css|lock|sum|mod)$",
                      re.I)
# name.env anywhere else warns first (G1.9): 225 reads in 21 days were live flows (etcd.env over ssh, pins.env,
# grep -c on a key), and some printed a value (sed -n 's/^DISCOVERY_DB_URL=//p'). It denies once they have moved.
ENV_FILE_MODE = "warn"
_ENV_FILE = re.compile(r"(?:^|/)([^/]*[^/.][^/]*\.env)$")
ENV_FILE_NOTE = ("an env file holds values: read names only through a committed script, never print a value; "
                 "name.env files will be secret paths once these flows have moved (docs/guard.md G1.9)")


def _org_config_secret(p):
    m = _ORG_CONFIG_FILE.search(p)
    return bool(m) and bool(_CRED_WORD.search(m.group(1))) and not _NO_CRED.search(m.group(1))


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
    return (of_classify.secret_path(p) or bool(_EXTRA_SECRET.search(q)) or _org_config_secret(q)
            or _env_file(q))


def _reads(p):
    return (f"a command that reads a secret file ({os.path.basename(p.rstrip('/')) or p})", p) if p else None


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
        if not set(m.group(1).lower()) & {"f", "t"}:
            # its value too, escapes decoded: "\x7e/.ssh/…" or a backslash-newline inside the string hid a path
            # (of_review on organic-framework PR 97)
            try:
                with warnings.catch_warnings():  # an invalid escape ("\$") warns on stderr, which a hook must not
                    warnings.simplefilter("ignore")
                    value = ast.literal_eval(s)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                value = None
            value = value.decode("latin-1") if isinstance(value, bytes) else value
            if isinstance(value, str) and value != bodies[0]:
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
    if _GRANT_SIGN_CODE.search(bare):  # G13: signing a grant from code (of_grants.sign_ed25519, of's cmd_grant)
        return GRANT_DENY, _GRANT_SIGN_CODE.search(bare).group(0)
    for lit in lits:
        if _keychain_name(lit):  # G20: a keychain item an organization protects, named in code (Security framework)
            return KEYCHAIN_DENY, _keychain_name(lit)
        if " " not in lit and _secret_path(lit):
            return _reads(lit)
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
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    segs, cur = [], []
    for tok in lex:
        if tok and set(tok) <= _SEPS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    return segs + ([cur] if cur else [])


def _command_words(words):
    """Drop shell keywords, assignments, wrappers and `env VAR=x` prefixes: the words of the command that
    actually runs (`for h in a b; do ssh h 'cat x'; done` runs ssh)."""
    if words and words[0] in ("for", "case", "select", "function", "done", "fi", "esac", "in", "}"):
        return []  # a loop header, a function header or a closer runs nothing itself
    i = 0
    while i < len(words):
        w = os.path.basename(words[i])
        if words[i] in ("do", "then", "else", "elif", "if", "while", "until", "!", "{", "time", "coproc"):
            i += 1
        elif re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        elif w in _WRAPPERS or w == "timeout":
            i += 1
            while i < len(words) and (words[i].startswith("-") or (w == "timeout" and words[i][:1].isdigit())):
                i += 1
        elif w == "env" and any(not x.startswith("-") and "=" not in x for x in words[i + 1:]):
            i += 1  # env [opts] [VAR=x] cmd ...: the command after it runs
            while i < len(words) and (words[i].startswith("-") or "=" in words[i]):
                i += 1
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


def _tokens(text):
    import shlex
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars="<>&")
    lex.whitespace = " \t\r\n"
    lex.whitespace_split = True
    return list(lex)


def _split(text):
    """[pipelines]; each pipeline is a list of stages; a stage is ('cmd', raw) or ('sub', inner raw). Splits on
    ; && || & newline and top-level ( ); a single '|' (not '||') keeps the pipeline. Quotes are honored."""
    pipes, pipe, buf, i, n = [], [], [], 0, len(text)
    single = double = False

    def flush_cmd():
        s = "".join(buf).strip()
        if s:
            pipe.append(("cmd", s))
        buf.clear()

    def flush_pipe():
        flush_cmd()
        if pipe:
            pipes.append(pipe[:])
            pipe.clear()

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
            if text[i:i + 2] == "||":
                flush_pipe()
                i += 2
            else:
                flush_cmd()
                i += 2 if text[i:i + 2] == "|&" else 1
        elif c == "&":
            if text[i:i + 2] == "&&":
                flush_pipe()
                i += 2
            elif "".join(buf).rstrip().endswith(">"):  # >& redirect, not a separator
                buf.append(c)
                i += 1
            else:
                flush_pipe()
                i += 1
        elif c in ";\n":
            flush_pipe()
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
        if "-o" in args:  # grep -o prints only what matched
            pat = next((a for a in args if not a.startswith("-")), "").strip("'\"")
            if "=" not in pat:
                return True  # a name pattern, no value
            left = pat.split("=", 1)[0].lstrip("^")  # `NAME=[^ ]*`: one named value, allowed iff NAME is not secret
            return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", left)) and not _secret_name(left)
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
                  "--client-certificate", "--certificate-authority"}


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


def _kubectl_secret_deny(args):
    """Cluster reads that print a stored Secret's values (of_architect, 2026-11-06, after three real leaks,
    the last from a go-template/jsonpath that fell back to printing the whole object). For a Secret kind
    (any alias/namespace, -A, comma lists, `all`): allow only -o name, the default table and `describe`
    (which lists keys with sizes); deny every other -o, plus get -w/--watch, edit and apply view-last-applied.
    For a pod kind, -o yaml|json is denied too (the spec carries container env values)."""
    nonopt = _positional(args, _KUBECTL_VALUE)
    sub = nonopt[0] if nonopt else None
    has_secret = any(p in ("secret", "secrets", "all") for a in nonopt for p in re.split(r"[,/]", a))
    has_pod = any(p in ("pod", "pods", "po") for a in nonopt for p in re.split(r"[,/]", a))
    out = (_opt_value(args, ("-o", "--output")) or "").split("=")[0]
    if sub == "edit" and has_secret:
        return _prints("kubectl edit secret")
    if sub == "apply" and "view-last-applied" in args and has_secret:
        return _prints("kubectl apply view-last-applied secret")
    if sub == "create" and "secret" in nonopt and any(a.startswith("--dry-run") for a in args) and out in ("yaml", "json"):
        return _prints("kubectl create secret --dry-run -o " + out)
    if sub == "get" and has_secret:
        if any(a in ("-w", "--watch") for a in args):
            return _prints("kubectl get secret --watch")
        if out and out != "name":  # only -o name and the default table; describe (sizes) is allowed separately
            return _prints(f"kubectl get secret -o {out}")  # jsonpath/go-template fall back to the whole object
    if sub in ("get", "describe") and has_pod and out in ("yaml", "json"):  # the pod spec carries env values
        return _prints(f"kubectl {sub} pod -o {out}")
    return None


def _cluster_secret_deny(head, args, to_file):
    """A command that prints a stored secret value from a cluster or a secret manager. Writing it to a file is
    the owner's to allow, so a `>` redirect is exempt (of_architect, 2026-11-06)."""
    if head in ("wg", "awg"):  # WireGuard/Amnezia: showconf and the private/preshared keys (plain `show` is public)
        if args[:1] == ["showconf"]:
            return _prints(f"{head} showconf")
        if args[:1] == ["show"] and any(a in ("private-key", "preshared-key", "preshared-keys") for a in args):
            return _prints(f"{head} show private-key")
        return None
    if to_file:
        return None
    if head == "kubectl":
        return _kubectl_secret_deny(args)
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


def _committed_clean(path):
    """Is this local file tracked by git and unchanged? (a committed script: the sanctioned pattern)"""
    import subprocess
    d = os.path.dirname(os.path.abspath(path)) or "."
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        tracked = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", os.path.basename(path)],
                                 capture_output=True, timeout=5, env=env).returncode == 0
        clean = tracked and subprocess.run(["git", "-C", d, "diff", "--quiet", "HEAD", "--", os.path.basename(path)],
                                           capture_output=True, timeout=5, env=env).returncode == 0
        return clean
    except (OSError, ValueError):
        return False


def _stdin_script_deny(path, kind, depth):
    """A local file sent as the script of a remote shell or interpreter: allowed when committed and unchanged,
    otherwise its text is checked like a heredoc body."""
    p = os.path.expanduser(path)
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
_REMOVERS = {"rm", "unlink", "shred", "truncate", "mv"}
_COPIERS = {"cp", "install", "rsync", "ln", "ditto"}
_IN_PLACE = {"sed", "gsed", "perl", "ruby"}
GRANT_DENY = ("a change to signed grants, which only the tenant makes, with `of grant` in a plain terminal (an "
              "organization's rules are deny grants; propose the exact command to the owner as a task)")


def _grant_file(p):
    p = str(p or "").strip("'\"")
    if p and not p.startswith(("/", "~", "$")) and _DENY_CWD[0]:
        p = os.path.join(_DENY_CWD[0], p)
    return bool(p) and bool(_GRANT_FILES.search(os.path.normpath(os.path.expanduser(p))))


def _write_targets(stext):
    """The files a simple command's output redirections write (`> f`, `>> f`, `&> f`, `2> f`)."""
    try:
        toks = _tokens(stext)
    except ValueError:
        return []
    return [toks[k + 1] for k, w in enumerate(toks[:-1]) if w and set(w) <= _REDIR and ">" in w and
            not w.endswith("&") and toks[k + 1] not in _DEV_STREAMS and not toks[k + 1].isdigit()]


def _grant_deny(head, args, stext):
    """(reason, token) for G13 on one simple command, else None."""
    words = args
    if head in _INTERPRETERS and args and os.path.basename(args[0]) == "of":  # python3 bin/of grant issue
        head, words = "of", args[1:]
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


def _changed_files(head, args, stext):
    """The files one simple command writes, removes, moves over or changes in place (G13, G17)."""
    files = list(_write_targets(stext))
    if head == "tee" or head in _REMOVERS or (head in _IN_PLACE and any(a.startswith("-i") or a == "-pi" for a in args)):
        files += [a for a in args if not a.startswith("-")]
    elif head in _COPIERS:
        files += [a for a in args if not a.startswith("-")][-1:]
    elif head == "dd":
        files += [a[3:] for a in args if a.startswith("of=")]
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
        if args[i] in ("-C", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
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
            with open(os.path.expanduser(f), errors="replace") as fh:
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
        lines = open(os.path.expanduser(path), errors="replace").read(1 << 20).splitlines()
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
            out.update(_config_sections(os.path.join(os.path.dirname(os.path.expanduser(f)), os.path.expanduser(i))
                                        if not os.path.isabs(os.path.expanduser(i)) else i, "alias")[0])
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
        lines = open(os.path.join(os.path.expanduser(d), "config.yml"), errors="replace").read(1 << 20).splitlines()
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
    p = os.path.abspath(os.path.expanduser(path or "."))
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
    p = os.path.expanduser(str(path).strip("'\""))
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
_SEC_ARG_OPTS = set("acCdDGjlpPrst")  # security find-*-password options that take a value
# The caches these rules and the passkey check read: an agent that removed or rewrote one would lift them
# (of_review's probe on organic-framework PR 65: rm, an overwrite, and a forged version-2 cache that is never rebuilt).
_GUARD_STATE_FILES = re.compile(r"(?:^|/)\.local/state/of/(?:guard-credentials|passkey-revoked)\.json(?:\.\d+)?$")
GUARD_STATE_DENY = ("the guard's own cache of what organizations protect, which only OF writes (docs/guard.md G20, "
                    "G21)")


_GUARD_STATE_NAMES = ("guard-credentials.json", "passkey-revoked.json")


def _guard_state_file(path, cwd=None):
    """Does PATH name one of the guard's own cache files? By its real path, so a link to the state folder counts, and
    by pattern, so a glob the shell would expand to one counts (of_secrets-review on organic-framework PR 65)."""
    p = str(path or "").strip("'\"")
    if not p:
        return False
    if not p.startswith(("/", "~", "$")) and cwd:
        p = os.path.join(cwd, p)
    p = os.path.expanduser(p)
    if _GUARD_STATE_FILES.search(os.path.normpath(p)):
        return True
    state = os.path.realpath(os.path.expanduser("~/.local/state/of"))
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
    p = os.path.expanduser(str(pattern))
    head = re.split(r"[*?[]", p, maxsplit=1)[0]
    stem = head if head.endswith("/") or head == p else os.path.dirname(head)
    return os.path.realpath(stem.rstrip("/") or "/") + p[len(stem.rstrip("/")):] if stem else p


def _protected_code(path, cwd, removal=False):
    """The protected_code entry PATH lies in (the entry itself, under it, or a match of its glob), else None. With
    REMOVAL (rm, rmdir, mv), a folder that holds an entry counts too: removing it removes the entry."""
    if not _ORG_PROTECTION["code"] or not path:
        return None
    import fnmatch
    p = os.path.expanduser(str(path).strip("'\""))
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


def _protected_code_deny(head, args, stext, cwd):
    """(reason, path) for G21 on one simple command, else None; and the guard's own state files, always."""
    removal = head in ("rm", "rmdir", "mv", "unlink", "shred")
    files = _changed_files(head, args, stext) + ([a for a in args if not a.startswith("-")] if head == "rmdir" else [])
    state = next((f for f in files if _guard_state_file(f, cwd)), None)
    if state:
        return GUARD_STATE_DENY, state
    if not _ORG_PROTECTION["code"]:
        return None
    # a recursive copy or an extraction writes anywhere under its destination: a folder above an entry counts
    # (of_secrets-review's review of organic-framework PR 65: cp -R, rsync, ditto, tar -C, unzip -d)
    hit = next((t for t in _tree_targets(head, args, cwd) if _protected_code(t, cwd, removal=True)), None)
    if hit:
        return PROTECTED_CODE_DENY, hit
    hit = next((f for f in files if _protected_code(f, cwd, removal)), None)
    return (PROTECTED_CODE_DENY, hit) if hit else None


def _herdr_lab(path):
    lab = os.path.realpath(os.path.expanduser(HERDR_LAB))
    p = os.path.realpath(os.path.expanduser(str(path or "")))
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
        _PLUGIN_DIRS.append(keep + [os.path.realpath(os.path.expanduser(d)) for d in herdr])
    return _PLUGIN_DIRS[0]


def _in_plugin_code(path, cwd):
    p = os.path.expanduser(str(path).strip("'\""))
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
        sock = os.path.join(os.path.expanduser(xdg), "herdr", "herdr.sock")
    # the socket must exist when the guard looks, its real path inside the lab: a link made later on the line, or one
    # that points out of the lab, reaches the owner's herdr (of_architect's second review of PR 32)
    path = os.path.expanduser(sock)
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
            where0 = os.path.join(where0, os.path.expanduser(args[k + 1]))
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
                where = os.path.join(where, os.path.expanduser(args[i + 1]))
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


def _bash_deny(cmd, depth=0, reaches_agent=True):
    """(reason, matched token) for a shell command line, or None. The rule (of_architect, 2026-11-06): deny
    what will execute or be read as data by the command, and never let a secret VALUE reach the transcript or
    a secret FILE leave its place; prose that is only carried is skipped. `reaches_agent` is whether this
    line's stdout is shown (vs. piped on, redirected to a file, or captured by a parent that does not show it).
    See RFC 0002 §4.1 for the known limits (unknown wrappers, indirection, interpreters dumping the env)."""
    if depth > 4:
        return None
    text, subs, docs, _ = _scan(cmd)
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
        elif head in _INTERPRETERS or remote == "code":
            hit = _code_deny(body, lang)
        elif not quoted:  # an unquoted delimiter runs $( ) in the body, whatever reads it
            hit = next(filter(None, (_bash_deny(s, depth + 1) for s in _scan(body)[1])), None)
            if not hit and (head == "tee" or (head == "cat" and ">" not in opener)):  # printed to stdout
                hit = next((_prints(m.group(0)) for m in _VAR_REF.finditer(body) if _secret_name(m.group(1))), None)
        else:
            hit = None
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
    exported = {}        # what `export` set earlier on the line (G17: GIT_CONFIG_* variables)
    lab_link = False     # a link or a socket made under of_herdr's lab earlier on the line (G19)
    for pipe in pipes:
        last = len(pipe) - 1
        cmds = []  # (head, args) of the simple-command stages, for the env-dump pipeline check
        for idx, (kind, stext) in enumerate(pipe):
            if kind == "sub":
                hit = _bash_deny(stext, depth + 1, reaches_agent=reaches_agent and idx == last)
                if hit:
                    return hit
                continue
            s = _simple(stext)
            if not s:
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
            hit = _cluster_secret_deny(head, args, to_file) or _memory_deny(head, args) \
                or _op_founder_deny(head, args, stext) or _op_deny(head, args) or _grant_deny(head, args, stext) \
                or _hooks_deny(head, args, stext, here, {**exported, **_assignments(stext)}) \
                or _plugins_deny(head, args, stext, here, {**exported, **_assignments(stext),
                                                           **({"__lab_link": "1"} if lab_link else {})}) \
                or _protected_code_deny(head, args, stext, here)
            if hit:
                return hit
            if head == "cd":
                to = os.path.expanduser(args[0]) if args and args[0] != "-" else os.path.expanduser("~")
                here = to if os.path.isabs(to) else os.path.join(here or ".", to)
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
                    hit = _bash_deny(herestr, depth + 1)
                elif head in _INTERPRETERS:
                    hit = _code_deny(herestr, _lang(head))
                else:
                    hit = None
                if hit:
                    return hit
            for body in _nested(head, args):
                hit = _bash_deny(body, depth + 1)
                if hit:
                    return hit
            if head in _INTERPRETERS:
                for flag in ("-c", "-e"):
                    if flag in args[:-1]:
                        hit = _code_deny(args[args.index(flag) + 1], _lang(head))
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
            elif head in _FILE_READERS:
                for a in _data_args(head, args):
                    if _secret_path(a):
                        return _reads(a)
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


def critical_deny(tool, tool_input, cwd=None):
    """(reason, subject) for a call to deny, or None. Examples (.env.example, *.pub) stay allowed."""
    ti = tool_input or {}
    _WARNINGS.clear()
    if tool == "Bash":
        cmd = ti.get("command") or ""
        _DENY_CWD[0] = cwd
        try:
            hit = _bash_deny(cmd)
        except Exception:  # a parser slip must not deny every command: the old, stricter text match
            bare = _strip_quoted(cmd)
            m = CRITICAL_DENY_BASH.search(bare)
            hit = _prints(m.group(0).strip(" ;&|(")) if m else _reads(READERS.search(bare) and _loose_secret_path(bare))
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
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep") and _secret_path(path):
        return f"a secret file ({os.path.basename(path)})", path
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
    return os.path.normpath(os.path.expanduser(p))


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
_KUBE_POD_VERBS = {"exec", "attach", "cp", "logs", "port-forward", "debug"}


def _kube_kinds(verb, pos, cut):
    """(the verb with its subcommand, [resource kinds]) of a kubectl call, for sizing a Role: `get pods,svc` ->
    pods and services; `logs deploy/x` -> deployments; `exec x` -> pods; `apply -f f` -> (from file); a kind held
    in a variable -> unparsed. Names are never kept."""
    sub = pos[1] if verb in _KUBE_SUBVERB and len(pos) > 1 and not (verb == "apply" and pos[1] != "view-last-applied") \
        else None
    label, rest = (f"{verb} {sub}", pos[2:]) if sub else (verb, pos[1:])
    if verb in _KUBE_POD_VERBS:
        tgt = next((a for a in rest if ":" in a), "") if verb == "cp" else (rest[0] if rest else "")
        if verb != "cp" and "/" in tgt:
            first = tgt.split("/")[0]
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
    return label, [_KIND_ALIAS.get(k.lower(), k.lower()) for k in first.split(",") if k]


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
    return {"verb": verb, "targets": targets, "namespace": _opt_value(cut, ("-n", "--namespace")), "exec_argv": run,
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
    if depth > 4:
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
                                the call names one of these namespaces with -n/--namespace (a pod name held in a
                                variable is still seen; a call that relies on the kubeconfig's namespace is not);
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
        if call["namespace"] not in match["namespaces"]:
            return None
        said.append(f"kubectl {call['verb']} in namespace {call['namespace']}")
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


def org_rule_hits(tool, tool_input, rules, position=None, env=None):
    """[(rule, matched phrase)] for the rules of the caller's organization that apply to this tool call. A rule is
    {id, mode: warn|deny, reason, subject?, except: [positions], kubectl: {matcher}} (of_grants.org_rule makes one
    from a deny grant); a position in `except` is out of it, and a rule whose subject names one position applies to
    that position only. Only Bash and the kubectl matcher so far."""
    if tool != "Bash" or not rules:
        return []
    cmd = (tool_input or {}).get("command") or ""
    calls, hits = None, []
    for r in rules:
        sub = r.get("subject")
        if not isinstance(r.get("kubectl"), dict) or (position and position in (r.get("except") or [])) or \
                (sub not in (None, "*") and sub != position):
            continue
        if calls is None:
            try:
                calls = kubectl_calls(cmd, env)
            except Exception:  # noqa: BLE001 - a parser slip never denies on an organization's rule
                calls = []
        m = next(filter(None, (kubectl_rule_match(r["kubectl"], c) for c in calls)), None)
        if m:
            hits.append((r, m))
    return hits


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
    if tool != "Bash" or depth > 4:
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
                to = os.path.expanduser(args[0]) if args and args[0] != "-" else os.path.expanduser("~")
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
                            where = os.path.join(where, os.path.expanduser(args[i + 1]))
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
    w = os.path.expanduser(w)
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
    if depth > 4:
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
    w = os.path.expanduser(w)
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
    if depth > 4:
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
    if depth > 4 or not _mentions_authority(cmd):
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
