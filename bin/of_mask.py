"""Masking at the boundary (RFC 0002 §4.2): find secrets in text and replace each with
`[secret:<kind>:<hmac8>]`, where hmac8 is the first 8 hex digits of HMAC-SHA256 under the
tenant purpose key `of/secret/v1`. Owned by of_server; runs only on the server.

The detector:
- the gitleaks 8.30.1 default ruleset, vendored in rules/ (MIT, see rules/LICENSE-gitleaks):
  rule regex, keywords prefilter, secretGroup, entropy, the global and per-rule allowlists
  (regexes, stopwords; path allowlists do not apply to records). Go RE2 syntax is translated;
- contextual rules: `evm-private-key` (64 hex next to key words; bare 64-hex stays, since
  transaction and block hashes are data), `url-password` (scheme://user:pass@), and
  `seed-phrase` (12 to 24 consecutive BIP-39 English words).

Overlapping findings are merged into one span (the masker errs towards masking). No value is
ever logged or returned: findings carry kind, HMAC and position only.
"""

import hashlib
import hmac
import json
import math
import os
import re
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(HERE, "rules", "gitleaks-8.30.1.toml")
BIP39_FILE = os.path.join(HERE, "rules", "bip39-english.txt")
LABEL = "of/secret/v1"
TAG_RX = re.compile(r"\[secret:[a-z0-9-]+:[0-9a-f]{8}\]")

# Kinds outside gitleaks. Higher priority wins the kind of a merged span.
CONTEXTUAL = ("evm-private-key", "url-password", "seed-phrase")
PRIORITY = {"private-key": 0, "evm-private-key": 1, "seed-phrase": 2, "url-password": 3, "generic-api-key": 9}

# ------------------------------------------------------------ RE2 -> Python

_POSIX = {"alnum": "a-zA-Z0-9", "alpha": "a-zA-Z", "digit": "0-9", "lower": "a-z", "upper": "A-Z",
          "space": r"\s", "xdigit": "0-9A-Fa-f", "word": r"\w", "punct": r"!-/:-@\[-`{-~"}


def re2_to_py(rx):
    """Translate the RE2 features gitleaks uses that Python's re lacks or reads differently:
    a flag group like `(?i)` in mid-pattern (RE2: to the end of the enclosing group), POSIX
    classes like `[[:alnum:]]`, and `\\z`."""
    rx = re.sub(r"\[:(\w+):\]", lambda m: _POSIX[m.group(1)], rx)
    rx = rx.replace(r"\z", r"\Z")
    out, stack, i, n = [], [[]], 0, len(rx)  # stack: the flag wraps opened in each open group
    in_class = False
    while i < n:
        c = rx[i]
        if c == "\\":
            out.append(rx[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append(c)
            i += 1
            if i < n and rx[i] == "^":
                out.append("^")
                i += 1
            if i < n and rx[i] == "]":  # a leading ] is a literal
                out.append("]")
                i += 1
            continue
        m = re.match(r"\(\?([a-zA-Z]+)\)", rx[i:])
        if m and (i > 0 or len(stack) > 1):
            out.append(f"(?{m.group(1)}:")
            stack[-1].append(m.group(1))
            i += m.end()
            continue
        if c == "|" and stack[-1]:
            # RE2: the flag holds to the end of the group, later alternatives included; close
            # the wraps before the bar and reopen them after it.
            out.append(")" * len(stack[-1]) + "|" + "".join(f"(?{f}:" for f in stack[-1]))
            i += 1
            continue
        if c == "(":
            stack.append([])
            out.append(c)
            i += 1
            continue
        if c == ")":
            out.append(")" * len(stack.pop()) + ")")
            i += 1
            continue
        out.append(c)
        i += 1
    out.append(")" * len(stack.pop()))
    return "".join(out)


# ------------------------------------------------------------------ rules

class Rule:
    __slots__ = ("id", "rx", "keywords", "entropy", "group", "allow")

    def __init__(self, rid, rx, keywords=(), entropy=0.0, group=0, allow=()):
        self.id, self.rx, self.keywords, self.entropy, self.group, self.allow = rid, rx, keywords, entropy, group, allow


class Allow:
    __slots__ = ("target", "regexes", "stopwords")

    def __init__(self, target, regexes, stopwords):
        self.target, self.regexes, self.stopwords = target, regexes, stopwords

    def allows(self, secret, match, line):
        text = {"match": match, "line": line}.get(self.target, secret)
        if any(r.search(text) for r in self.regexes):
            return True
        low = secret.lower()
        return any(s in low for s in self.stopwords)


def _allow(a):
    if a.get("condition", "OR").upper() == "AND" and a.get("paths"):
        return None  # an AND with a path can never hold for a record
    regexes = [re.compile(re2_to_py(r)) for r in a.get("regexes") or []]
    stopwords = [s.lower() for s in a.get("stopwords") or []]
    if not regexes and not stopwords:
        return None
    return Allow(a.get("regexTarget", "secret"), regexes, stopwords)


def load_gitleaks(path=RULES_FILE):
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    glob = _allow(cfg.get("allowlist") or {})
    rules = []
    for r in cfg["rules"]:
        if "regex" not in r:  # path-only rules (files by name) have nothing to match in a record
            continue
        allow = [a for a in (_allow(x) for x in r.get("allowlists") or []) if a]
        if glob:
            allow.append(glob)
        # The lazy identifier prefix only widens the match leftwards; Python's backtracking makes it
        # cost ~50 steps per position. Drop it and restore the full match for allowlists (_widen).
        rx = r["regex"].replace(LAZY_PREFIX, "")
        kws = () if r["id"] in NO_KEYWORD else tuple(k.lower() for k in r.get("keywords") or ())
        rules.append(Rule(r["id"], re.compile(re2_to_py(rx)), kws,
                          float(r.get("entropy") or 0), int(r.get("secretGroup") or 0), tuple(allow)))
    return rules


LAZY_PREFIX = r"[\w.-]{0,50}?"
# Rules whose pattern is distinctive without its keyword: a token pasted without the vendor's
# name would slip past gitleaks' keyword prefilter (the masker errs towards masking).
NO_KEYWORD = {"airtable-personnal-access-token", "facebook-access-token"}
_IDENT = re.compile(r"[\w.-]{1,50}$")


def _widen(text, start):
    """Where gitleaks' match would start: up to 50 identifier characters further left."""
    m = _IDENT.search(text, max(0, start - 50), start)
    return m.start() if m else start


def shannon(s):
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


# Contextual rules (RFC 0002 §4.2). The value is group "v".
# EVM private keys: a 64-hex value is a key only by its context, found in a short window before
# it (transaction and block hashes are 64-hex too, and they are data).
_HEX_VALUE = re.compile(r"(?<![0-9A-Za-z])(?:0[xX])?[0-9a-fA-F]{64}(?![0-9A-Za-z])")
_KEY_PHRASE = re.compile(r"(?i)(?:private|secret|seed|mnemonic|signer|signing|wallet|deployer|owner|operator)"
                         r"[\w -]{0,20}?key\w*[\s\"'`:=\\]{1,8}$")
_KEY_IDENT = re.compile(r"([A-Za-z_][\w.-]{0,63})[\s\"'`:=\\]{1,8}$")
_KEY_WORDS = re.compile(r"(?i)key|pk|priv|secret|seed|mnemonic|signer")
_NOT_KEY = re.compile(r"(?i)sha|hash|digest|checksum|code|request|tx|block|commit|root|public|pub_|pubkey|address|id$")


_PUBLIC_BEFORE = re.compile(r"(?i)\b(?:public|pub|verifying|verification)[\s_-]*$")
_HEX64_ONLY = re.compile(r"(?:0[xX])?[0-9a-fA-F]{64}")


def _evm_keys(text):
    out = []
    for m in _HEX_VALUE.finditer(text):
        window = text[max(0, m.start() - 80):m.start()]
        if _KEY_PHRASE.search(window):
            out.append((*m.span(), "evm-private-key"))
            continue
        ident = _KEY_IDENT.search(window)
        before = window[:ident.start()][-24:] if ident else ""
        if ident and _KEY_WORDS.search(ident.group(1)) and not _NOT_KEY.search(ident.group(1)) \
                and not _PUBLIC_BEFORE.search(before):
            out.append((*m.span(), "evm-private-key"))
    return out


_URL_PASSWORD = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@\"'<>]+:(?P<v>[^@\s/\"'<>]+)@")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_WORDS = re.compile(r"\b[A-Za-z]{3,8}\b")
# No secret we claim fits in fewer characters, context included (the shortest: `pk=` + 64 hex,
# `a://u:p@`, gitleaks' shortest rule match). Shorter strings are still covered by the pass over
# the whole serialized record in Masker.json_text.
MIN_LEN = 8
_SEP = re.compile(r"(?:[\s,;]|\\n|\d{1,2}[.):])*")
# Twelve or more words of _WORDS, each gap a _SEP: where a seed phrase can be.
_WORD_RUN = re.compile(r"\b[A-Za-z]{3,8}\b(?:(?:[\s,;]|\\n|\d{1,2}[.):])*\b[A-Za-z]{3,8}\b){11,}")


def _trie_lookahead(words):
    """One regex that, at each position, matches the longest of `words` starting there: a
    prefix trie, so each position costs a few character tests instead of one search per word."""
    root = {}
    for w in words:
        node = root
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def emit(node):
        alts = [re.escape(ch) + emit(sub) for ch, sub in sorted(node.items()) if ch]
        if not alts:
            return ""
        body = "(?:" + "|".join(alts) + ")"
        return body + "?" if "" in node else body
    return re.compile("(?=(" + emit(root) + "))")


class Detector:
    def __init__(self, rules=None, wordlist=None):
        self.rules = load_gitleaks() if rules is None else rules
        if wordlist is None:
            with open(BIP39_FILE) as fh:
                wordlist = fh.read().split()
        self.bip39 = frozenset(wordlist)
        # Keyword prefilter: each keyword is one substring search over the text; only the rules
        # of the keywords present run (gitleaks does the same with Aho-Corasick).
        self.by_kw = {}
        self.always = []
        for i, r in enumerate(self.rules):
            for k in r.keywords:
                self.by_kw.setdefault(k, []).append(i)
            if not r.keywords:
                self.always.append(i)
        kws = sorted(self.by_kw)
        self.kw_rx = _trie_lookahead(kws) if kws else None
        # A keyword found at a position is the longest one starting there; every keyword inside
        # it occurs too.
        self.kw_inside = {k: frozenset(j for j in kws if j in k) for k in kws}
        # Keywords JSON never escapes (no quote, backslash or control character): found in a
        # decoded string only where they also stand in the serialized text, unless the text
        # uses \u or \/ escapes (Masker.json_text).
        self.kw_plain = all(k.isprintable() and '"' not in k and "\\" not in k for k in kws)

    def keywords(self, low):
        """The keywords that occur in `low` (lowercased text), in one scan."""
        found = set()
        if self.kw_rx is None:
            return found
        seen = set()
        for m in self.kw_rx.finditer(low):
            g = m.group(1)
            if g and g not in seen:
                seen.add(g)
                found |= self.kw_inside[g]
        return found

    def kinds(self):
        return sorted({r.id for r in self.rules} | set(CONTEXTUAL))

    def find(self, text, keywords=None):
        """[(start, end, kind)] of secret values in text, merged where they overlap. `keywords`:
        the keywords present in the text when the caller knows them already."""
        if not text or len(text) < MIN_LEN:
            return []
        if keywords is None:
            keywords = self.keywords(text.lower())
        todo = set(self.always)
        for k in keywords:
            todo.update(self.by_kw[k])
        hits = []
        for i in sorted(todo):
            r = self.rules[i]
            for m in r.rx.finditer(text):
                g = r.group
                if not g and m.re.groups:
                    g = next((i for i in range(1, m.re.groups + 1) if m.group(i)), 0)
                s, e = m.span(g)
                secret = text[s:e]
                if not secret or (r.entropy and shannon(secret) <= r.entropy):
                    continue
                if r.allow:
                    line = text[text.rfind("\n", 0, s) + 1:(text.find("\n", e) + 1 or len(text) + 1) - 1]
                    match = text[_widen(text, m.start()):m.end()]
                    if any(a.allows(secret, match, line) for a in r.allow):
                        continue
                if r.id == "generic-api-key" and _HEX64_ONLY.fullmatch(secret):
                    continue  # a bare 64-hex value is judged by its context in _evm_keys, like a tx hash
                hits.append((s, e, r.id))
        if _HEX64.search(text):
            hits += _evm_keys(text)
        if "://" in text:
            hits += [(*m.span("v"), "url-password") for m in _URL_PASSWORD.finditer(text)]
        if len(text) >= 47:  # twelve words of three letters and their spaces
            hits += self._seed_phrases(text)
        return _merge(hits)

    def _seed_phrases(self, text):
        """Runs of at least twelve BIP-39 words. Only stretches of twelve or more joined words
        can hold one, so the word-by-word check runs inside those stretches only."""
        out = []
        for r in _WORD_RUN.finditer(text):
            out += self._seed_run(text, r.start(), r.end())
        return out

    def _seed_run(self, text, pos, endpos):
        out, run, last_end = [], [], None
        for m in _WORDS.finditer(text, pos, endpos):
            w = m.group(0).lower()
            joined = last_end is not None and _SEP.fullmatch(text, last_end, m.start()) is not None
            if w in self.bip39 and (joined or not run):
                run.append(m.span())
            else:
                if len(run) >= 12:
                    out.append((run[0][0], run[-1][1], "seed-phrase"))
                run = [m.span()] if w in self.bip39 else []
            last_end = m.end()
        if len(run) >= 12:
            out.append((run[0][0], run[-1][1], "seed-phrase"))
        return out


def _merge(hits):
    hits.sort()
    out = []
    for s, e, k in hits:
        if out and s < out[-1][1]:
            ps, pe, pk = out[-1]
            out[-1] = (ps, max(pe, e), min(pk, k, key=lambda x: PRIORITY.get(x, 5)))
        else:
            out.append((s, e, k))
    return out


# ----------------------------------------------------------------- masking

def canonical(kind, value):
    """The form a value is tagged by, so one secret keeps one tag however it is written:
    an EVM key with or without 0x and in any case; a seed phrase with any spacing or numbering."""
    if kind == "evm-private-key":
        v = value.lower()
        return v[2:] if v.startswith("0x") else v
    if kind == "seed-phrase":
        return " ".join(w.lower() for w in _WORDS.findall(value))
    return value


class Masker:
    """Mask strings and JSON documents. `findings` accumulates {(kind, hmac): count} for the
    current record until `take()`; values are never kept."""

    def __init__(self, key, detector=None):
        self.key = key
        self.detector = detector or Detector()
        self.findings = {}

    def tag(self, kind, value):
        h = hmac.new(self.key, canonical(kind, value).encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:8]
        self.findings[(kind, h)] = self.findings.get((kind, h), 0) + 1
        return f"[secret:{kind}:{h}]"

    def take(self):
        f, self.findings = self.findings, {}
        return f

    def text(self, s):
        spans = self.detector.find(s)
        if not spans:
            return s
        out, at = [], 0
        for a, b, kind in spans:
            out.append(s[at:a])
            out.append(self.tag(kind, s[a:b]))
            at = b
        out.append(s[at:])
        return "".join(out)

    def json_text(self, raw):
        """Mask a JSON document given as text, keeping it valid JSON. Two passes, one detector
        call each: over the serialized text, for contexts that span JSON structure
        (`"PRIVATE_KEY": "0x…"`), and over the decoded strings joined by newlines, for content
        that escaping hides (JSON inside a string, PEM blocks). Values found in the serialized
        text are masked wherever they occur; spans found in the joined text are masked in place,
        across string boundaries when they cross one."""
        if not raw:
            return raw
        try:
            doc = json.loads(raw)
        except (ValueError, TypeError):
            return self.text(raw)
        strings = []
        _collect(doc, strings)
        if not strings:
            return raw
        extra = []
        kw_raw = self.detector.keywords(raw.lower())
        for a, b, kind in self.detector.find(raw, kw_raw):
            v = raw[a:b]
            try:
                v = json.loads(f'"{v}"')
            except ValueError:
                pass
            if len(v) >= 8:
                extra.append((v, kind))
        joined = "\n".join(strings)
        kw_joined = None
        if self.detector.kw_plain and "\\u" not in raw and "\\/" not in raw:
            low = joined.lower()
            kw_joined = {k for k in kw_raw if k in low}
        spans = self.detector.find(joined, kw_joined)
        if not spans and not extra:
            return raw
        out = _cut(strings, spans, lambda a, b, kind: self.tag(kind, joined[a:b]))
        if extra:
            extra.sort(key=lambda vk: -len(vk[0]))
            tags = {}
            for i, x in enumerate(out):
                for v, kind in extra:
                    if v in x:
                        if v not in tags:
                            tags[v] = self.tag(kind, v)
                        x = x.replace(v, tags[v])
                out[i] = x
        it = iter(out)
        return json.dumps(_rebuild(doc, it), ensure_ascii=False, separators=(",", ":"))


def _collect(x, out):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, list):
        for v in x:
            _collect(v, out)
    elif isinstance(x, dict):
        for k, v in x.items():
            out.append(k)
            _collect(v, out)


def _rebuild(x, it):
    if isinstance(x, str):
        return next(it)
    if isinstance(x, list):
        return [_rebuild(v, it) for v in x]
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            k2 = next(it)
            out[k2] = _rebuild(v, it)
        return out
    return x


def _cut(strings, spans, tag):
    """Apply spans over "\n".join(strings) back to each string. A span is tagged once; every
    string it touches loses its part of the span, and the first gets the tag."""
    if not spans:
        return list(strings)
    out, base, si = [], 0, 0
    tags = [None] * len(spans)
    for s in strings:
        end = base + len(s)
        pieces, at = [], 0
        while si < len(spans) and spans[si][0] < end:
            a, b, kind = spans[si]
            lo, hi = max(a, base) - base, min(b, end) - base
            if tags[si] is None:
                tags[si] = tag(a, b, kind)
                pieces.append(s[at:lo] + tags[si])
            else:
                pieces.append(s[at:lo])
            at = max(at, hi)
            if b <= end:
                si += 1
            else:
                break  # the span continues into the next string
        pieces.append(s[at:])
        out.append("".join(pieces))
        base = end + 1  # the newline between strings
    return out


# Which fields of a routed row carry content, per table; all are JSON text.
CONTENT = {"event": ("payload", "record"), "transcript_record": ("raw",), "hook_record": ("raw",),
           "runtime_record": ("raw",)}


def mask_row(masker, table, row):
    """A copy of the row with its content fields masked, and {(kind, hmac): count}."""
    out = dict(row)
    for f in CONTENT.get(table, ()):
        if isinstance(out.get(f), str) and out[f]:
            out[f] = masker.json_text(out[f])
    return out, masker.take()


_DETECTOR = None


def detector():
    """One shared detector per process: compiling the ruleset takes a moment."""
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = Detector()
    return _DETECTOR


if __name__ == "__main__":  # the kinds this masker claims, one per line (for lab-secrets-v0)
    print("\n".join(Detector().kinds()))
