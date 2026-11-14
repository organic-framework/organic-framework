"""Classifiers shared by the live collector (of hook) and the history import (of import).

Every label is versioned. Two families live side by side:
- "fa-2026-11-05": of_field-analyst's founder-load baseline rules, PINNED VERBATIM from the first user
  organization's compute_metrics.py and extract_events.py (sha256 prefixes: compute_metrics cd28cf057ffc14c9); the studio gate
  reproduces the baseline with exactly these. Do not edit this block; add a new version instead. The one exception:
  the words that name one organization's sessions and tools (TERMS below) come from that machine's
  ~/.config/of/classify.json, so the published code names no organization; with the file in place the patterns
  match exactly what they matched before.
- "of-v1": the platform's own labels (origin from the harness's metadata when present).
Questions: "q-v0" (the baseline's definition) and "q-v1" (the collector's heuristic).
"""

import collections
import json
import os
import re


def _load_terms(path=None):
    """{name_refs, capacity_words, owner_names, runner_commands}: an organization's own words, as regex fragments,
    from ~/.config/of/classify.json (OF_CLASSIFY_TERMS overrides the path). JSON, not YAML: the guard's hook imports
    this module and must run under an interpreter without PyYAML. Missing or unreadable: empty lists."""
    path = path or os.environ.get("OF_CLASSIFY_TERMS") or os.path.expanduser("~/.config/of/classify.json")
    try:
        with open(path) as fh:
            doc = json.load(fh) or {}
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    return {k: [str(x) for x in (doc.get(k) or [])] for k in
            ("name_refs", "capacity_words", "owner_names", "runner_commands")}


TERMS = _load_terms()


def _alt(words):
    return "".join("|" + w for w in words)

FA = "fa-2026-11-05"
# sha256 prefixes of the source files the pinned blocks were copied from (checked by `of model gate`)
PINNED = {"compute_metrics.py": "cd28cf057ffc14c9", "extract_events.py": "373d479f9fba06d6"}
OF = "of-v1"

# ---- pinned: compute_metrics.py lines 23-36, verbatim ----
INJECTED = re.compile(r'^(Лимит пополнен|Твой ход оборвался|\[внешний таймер|<pasted_content id="d7b9"> \[внешний таймер|Владелец перен[её]с эту сессию|eport in Russian|Проверка PR|Продолжение после смены аккаунта|Continuation after)', re.I)
BRIEF = re.compile(r'^(Ты — |You are the session|You are the |Read the brief|Прочитай бриф|Ты — отдельная|Задача от владельца|Привет! Ты новый аккаунт|Привет, ты забираешь)', re.I)
CATS = collections.OrderedDict([
    ('status_poll', r'(как (у нас )?(тут |там )?дела|как дела|тут работа продолжается|что ты ждешь|чего (мы )?ждем|чего так долго|сколько ждать|что у нас (тут |еще )?осталось|^ну что\??$|ну что,? как|какой прогресс|что у тебя осталось|ну ч[её],? как|что дальше|что еще можно|когда мы будем)'),
    ('routing', r'(передай|скажи (сессии|node|not|ей |ему )|отдай (это )?в|попроси .{0,40}сесси|посоветуйся с сессией|спроси у .{0,30}сесси|запусти .{0,40}(пейн|pane|сесси)|сетапь новый пейн|унеси|перенеси .{0,30}таб|сессию закрываем|возвращаем эту зону|отдельн\w+ (пейн|pane|сесси))'),
    ('relay_paste', r'<pasted_content'),
    ('access', r'(1password|в arc|открыл .{0,20}вкладк|выдал .{0,20}доступ|дал доступ|я дал |готов дать|пароль|логин|\bsso\b|warp|mcp|ключ в|токен графан|паспорт)'),
    ('capacity_tooling', r'(лимит|аккаунт|апгрейд|zellij|zellige|пейн|в таб|соседний таб|layout|лэйаут|сетк|место на диске|место заканчивается|комп начал|грузит|dangerously|перезапусти агент' + _alt(TERMS["capacity_words"]) + ')'),
])
APPROVE = re.compile(r'^\s*(да|ок|ok|окей|оки|давай|делай|го|мержи|смержи|разрешаю|подтверждаю|согласен|доделывай|продолжай|1[.)/] ?(да|ok|ок|давай|делай))\b', re.I)
EXPLICIT = re.compile(r'\b(разрешаю|подтверждаю)\b', re.I)
RELAYED = re.compile(r"(owner'?s? (yes|said|asked|approved|words|order|decision|request)|владел\w+ (сказал|просил|разрешил|одобрил)|with the owner'?s|his yes|«да»|owner: «|from the owner|handoff from the owner)", re.I)
NAMEREF = re.compile(r'(сесси[июей] (по |про )|(в |эту |ту )сесси[юи] [a-z]|k8s[- ]migration|кубер\w* migration|миграции кубера|сесси[юи] миграции|спросить у .{0,20}сесси|как нет, если есть сессия' + _alt(TERMS['name_refs']) + ')', re.I)
NAMEFAIL = re.compile(r"(no session of that name|can'?t reach you directly|cannot reach|not reachable|unreachable|is not listed|which session|какая сессия|не нашёл сессию)", re.I)

# ---- pinned: compute_metrics.py classify(), verbatim ----
def classify(p):
    t = p['text'].strip()
    if t.startswith('[Request interrupted'): return 'interrupt'
    if INJECTED.match(t): return 'injected'
    if BRIEF.match(t) or (p['first'] and p['n'] > 400): return 'brief'
    if '[FIRING]' in t[:300]: return 'alert_paste'
    return 'founder'
# ---- pinned: extract_events.py lines 14-15, verbatim (sha256 prefix 373d479f9fba06d6) ----
SECRET = re.compile(r'(?i)(sk-[a-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|glsa_[A-Za-z0-9_]{20,}|xox[bpa]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+|postgres(ql)?://\S+|0x[0-9a-fA-F]{64})')
def clean(s, n): return SECRET.sub('[REDACTED]', (s or '').replace('\n', ' '))[:n]
# ---- end of pinned block ----

# The prefixes and kinds that extract_events.py drops before counting (lines 77-80, restated as data).
FA_SKIP_KINDS = ("task-notification", "auto-continuation")
FA_SKIP_PREFIXES = ('<task-notification', 'Your claude.ai usage limit', 'This session is being continued', '<system-reminder>',
                    '<local-command', '<command-', '<bash-', 'Caveat:')


def fa_extracted(text, origin_kind=None, is_meta=False, project=None):
    """Would extract_events.py emit this user entry as a prompt? (peer, meta, notifications and
    harness boilerplate are dropped there before anything is counted; so are whole projects under
    -private-tmp: extract_events.py line 31, `if proj.startswith('-private-tmp'): continue`)."""
    if project and str(project).startswith("-private-tmp"):
        return False
    st_ = (text or "").strip()
    if origin_kind == "peer" or "cross-session-message" in st_[:200] or relay_sender(st_):
        return False
    if is_meta or origin_kind in FA_SKIP_KINDS:
        return False
    return bool(st_) and not st_.startswith(FA_SKIP_PREFIXES)


def fa_prompt(text, first=False, origin_kind=None, is_meta=False, project=None):
    """The baseline's labels for a user prompt, computed exactly as extract_events.py and
    compute_metrics.py do: on clean(strip(text), 400), with n = len(strip(text))."""
    st_ = (text or "").strip()
    t, n = clean(st_, 400), len(st_)
    kind = classify({"text": t, "first": first, "n": n})
    cats = [c for c, rx in CATS.items() if re.search(rx, t.lower(), re.I)]
    short = bool(APPROVE.match(t)) and n < 120
    explicit = bool(EXPLICIT.search(t))
    return {"extracted": fa_extracted(text, origin_kind, is_meta, project), "kind": kind, "n": n, "cats": cats,
            "approval": "both" if short and explicit else ("short" if short else ("explicit" if explicit else None)),
            "nameref": bool(NAMEREF.search(t)), "namefail": bool(NAMEFAIL.search(t))}


PEER_OPEN = re.compile(r"^.*?<cross-session-message[^>]*>", re.S)
RQ = "rq-v0"
_REQUEST = re.compile(r"(\?|прошу|просьба|можешь|можете|нужн[оаы]|please|could you|can you|request|ask)", re.I)


def rq_v0(text):
    """rq-v0: is a received peer message a request? of_field-analyst's calibrate_antipatterns.py:78-82,
    on clean(body, 300) of the body after the cross-session-message wrapper (as for `relayed`)."""
    body = PEER_OPEN.sub("", (text or "").strip(), count=1)
    return bool(_REQUEST.search(clean(body, 300)))


def fa_message_in(text):
    """extract_events.py lines 74-77: RELAYED on clean(body, 300), where body is the whole user text
    with everything up to the cross-session-message opening tag removed."""
    body = PEER_OPEN.sub("", (text or "").strip(), count=1)
    return {"relayed": bool(RELAYED.search(clean(body, 300)))}


# ---- questions ----
_ASK_PHRASES = re.compile(
    r"(?i)(?:(?:тво(?:его|ё|й)|ваш(?:его|е|и))\s*[«\"“]?\s*да\b"
    r"|жд(?:у|ём|ем|ут)\s+(?:тво|ваш)\w*\s+(?:ответ|решени|подтвержд|«?да)"
    r"|нужн[аоы]?\s+(?:тво|ваш)\w*\s+(?:решени|ответ|подтвержд|одобрени)"
    r"|без\s+тво\w*\s+[«\"“]?да"
    r"|awaiting your|waiting for your (?:answer|decision|approval|go-ahead)"
    r"|need your (?:decision|approval|go-ahead|answer))")


def q_v0(last_text, ask_tool=False):
    """The baseline: '?' in the last 250 characters of the last assistant text, or AskUserQuestion."""
    return bool(ask_tool) or "?" in (last_text or "")[-250:]


def q_v1(last_text, ask_tool=False):
    """The collector's heuristic: q-v0 minus API errors, plus implicit asks without '?'."""
    if ask_tool:
        return True
    t = last_text or ""
    if not t.strip():
        return False
    head = t.lstrip()[:200]
    if head.startswith("API Error") or "API Error:" in head:
        return False
    return "?" in t[-250:] or bool(_ASK_PHRASES.search(t[-600:]))


# ---- q-v2 (2026-11-06): the question is in the closing paragraphs, said to the owner ----
_CODE_URL = re.compile(r"`[^`]*`|https?://\S+|«[^»]{0,200}»|\"[^\"]{0,200}\"")
_OWNER_REQUEST = re.compile(
    r"(?i)(?:подтверди|выбери|решение за тоб|решать тебе|решать вам|разрешишь|разрешите|одобришь|одобрите"
    r"|это вопрос к теб|это вопрос к вам|нужно тво[её]\s+(?:решение|«?да)|нужно ваше\s+(?:решение|«?да)\s+сейчас"
    r"|please confirm|your call|need your (?:decision|approval|go-ahead|answer)|which do you (?:want|prefer)"
    r"|do you want me to)")
# a reminder of an ask already declared, or a condition on a later yes, is not a new question
_REMINDER = re.compile(r"(?i)(?:of asks?|`of ask`|через of ask|в of asks|вопрос в `?of asks|d[0-9a-f]{7})")
_CONDITIONAL_YES = re.compile(r"(?i)(?:после|без|only after|without)\s+(?:тво|ваш|your)\w*\s+[«\"]?(?:да|yes|go)")


def _closing_paragraphs(t, n=2, limit=700):
    paras = [p.strip() for p in re.split(r"\n\s*\n", t.strip()) if p.strip()]
    tail = paras[-n:]
    while tail and len("\n\n".join(tail)) > limit and len(tail) > 1:
        tail = tail[1:]
    return tail


def q_v2(last_text, ask_tool=False):
    """q-v2: a question to the owner at the end of a turn. Only the closing paragraphs count; a "?"
    counts outside code, links and quotes; or an explicit request to the owner (confirm, choose, your
    decision, это вопрос к тебе). Statements of plans ("проверю", "will check"), conditions on a later
    yes ("после твоего «да»", "без твоего «да» не …") and reminders of an ask already declared with
    `of ask` are not questions."""
    if ask_tool:
        return True
    t = last_text or ""
    if not t.strip() or t.lstrip()[:200].startswith("API Error") or "API Error:" in t.lstrip()[:200]:
        return False
    for para in reversed(_closing_paragraphs(t)):
        plain = _CODE_URL.sub(" ", para)
        if _REMINDER.search(para) and "?" not in plain:
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", plain)
        if any(sen.rstrip().endswith("?") for sen in sentences):
            return True
        if _OWNER_REQUEST.search(plain) and not _CONDITIONAL_YES.search(plain):
            return True
    return False


# q-v3 (2026-11-07, of_architect; acc-v0 step 2: reports leave the queue): of_capacity's report "Принято: …" with the
# steps of an already declared task ("С вас остаётся задача 7e8d779f: … подтвердить Touch ID") opened a heuristic ask.
# Only the imperative asks (подтверди, подтвердите; not the infinitive in a task's steps), and a paragraph that names a
# declared task or ask by its id is a reminder, not a new question.
_OWNER_REQUEST_V3 = re.compile(_OWNER_REQUEST.pattern.replace("(?:подтверди|", "(?:подтверди(?:те)?\\b|", 1))
_REMINDER_V3 = re.compile(_REMINDER.pattern[:-1] + r"|(?:задач\w*|task|вопрос\w*|ask)\s+`?[0-9a-f]{8}\b)")


def q_v3(last_text, ask_tool=False):
    """q-v2 with the imperative-only request and task/ask-id reminders (see above)."""
    if ask_tool:
        return True
    t = last_text or ""
    if not t.strip() or t.lstrip()[:200].startswith("API Error") or "API Error:" in t.lstrip()[:200]:
        return False
    for para in reversed(_closing_paragraphs(t)):
        plain = _CODE_URL.sub(" ", para)
        if _REMINDER_V3.search(para) and "?" not in plain:
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", plain)
        if any(sen.rstrip().endswith("?") for sen in sentences):
            return True
        if _OWNER_REQUEST_V3.search(plain) and not _CONDITIONAL_YES.search(plain):
            return True
    return False


# q-v4 (2026-11-07, the founder: "почему этого вопроса нет?"). acme_throughput-owner closed a turn with
# "**Вопрос к тебе.** Кто делает … : (а) … (б) … Рекомендую (а) … Но решать, трогать ли зону Вовы, тебе." and later
# "жду твоего ответа, кто делает …: (а) … или (б) …": a request for the owner's decision with no "?" and no q-v3
# phrase. q-v4 adds those requests, unless the paragraph says nothing is needed from the owner.
assert _OWNER_REQUEST_V3.pattern.startswith("(?i)")
_OWNER_REQUEST_V4 = re.compile(
    "(?im)" + _OWNER_REQUEST_V3.pattern[4:]
    + r"|(?:^\s*(?:[-*•]\s*)?(?:\*\*)?|[.!:—]\s*(?:\*\*)?|\*\*)вопрос\s+(?:к\s+)?(?:тебе|вам)\b|реш(?:ать|ение)\b[^.!?\n]{0,60}?(?:\bтебе\b|за\s+тобой|\bтво[её]\b)"
    + r"|\bтебе\s+(?:решать|выбирать)|\bжд(?:у|ём|ем|ёт|ет)\s+тво(?:его|ей|й)\s+(?:ответ|решени|выбор|«?да)"
    + r"|(?:question|decision)\s+for\s+you|\bup\s+to\s+you\b|waiting\s+(?:for|on)\s+your\s+(?:answer|decision|call|reply|go)")
# a paragraph that names an ask or task id (8 hex characters) without "?" reminds of a declared ask
_REMINDER_V4 = re.compile(_REMINDER_V3.pattern[:-1] + r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8}\b)")
_NOTHING_NEEDED_V4 = re.compile(r"(?i)(?:не нужно|не надо|не требуется|ничего решать|ничего не нужно|no need|"
                                r"nothing (?:for you )?to decide|nothing needed from you)")


def q_v4(last_text, ask_tool=False):
    """q-v3 plus a request for the owner's decision without a question mark (see above)."""
    if ask_tool:
        return True
    t = last_text or ""
    if not t.strip() or t.lstrip()[:200].startswith("API Error") or "API Error:" in t.lstrip()[:200]:
        return False
    for para in reversed(_closing_paragraphs(t)):
        plain = _CODE_URL.sub(" ", para)
        if _REMINDER_V4.search(para) and "?" not in plain:
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", plain)
        if any(sen.rstrip().endswith("?") for sen in sentences):
            return True
        if _request_v4(plain):
            return True
    return False


def _request_v4(plain):
    """A request for the owner's decision whose own sentence neither puts it after a later yes nor says nothing is
    needed (a list paragraph can hold "не надо" in another item)."""
    for m in _OWNER_REQUEST_V4.finditer(plain):
        start = max(plain.rfind(x, 0, m.start()) for x in ".!?\n") + 1
        ends = [i for i in (plain.find(x, m.end()) for x in ".!?\n") if i >= 0]
        sen = plain[start:min(ends) if ends else len(plain)]
        if not _CONDITIONAL_YES.search(sen) and not _NOTHING_NEEDED_V4.search(sen):
            return True
    return False


# q-v5 (2026-11-10, the founder: "Вот был вопрос, а он не попал в панель"). ci_keeper asked the founder two questions
# in its pane about the registry cache (devops-k8s #1063), at 15:20 and again at 15:37, and no version fired on
# either. AGENTS.md ends every turn with an `of status …` line, so it filled the last 250 characters (q-v0, q-v1) and
# one of the two closing paragraphs (q-v2..q-v4); at 15:37 one more sentence ("После этого понесу apply …") pushed
# the questions out. At 15:20 they sat mid-message under "**Что решить тебе:**", with no "?". A replay of 6,820 closing
# messages (2026-11-08..10) found "?" in the last 250 characters of only 25.
# The judge (bin/of_judge.py) decides on every candidate, so this stage is for recall:
# 1. trailing `of status` paragraphs are set aside before the closing window is taken;
# 2. a heading that turns to the owner ("Что решить тебе:", "Вопросы к тебе:", "Нужно от тебя:") counts anywhere;
# 3. "ждёт двух твоих ответов", "решить тебе" count as requests;
# 4. the status line's own note counts when it says the agent awaits the owner ("awaiting owner answers");
# 5. no reminder skip: any 8 hex characters (a commit such as 4ceb691f in ci_keeper's note) read as a filed ask's id.
#    The judge gets the agent's open asks and clears a reminder of one (j-v2).
_STATUS_CMD = re.compile(r"(?:^|`|\$\s*)(?:bin/)?of\s+status\b")


def _is_status_para(para):
    """A paragraph that is only the closing `of status …` line (in backticks or a code block, maybe after "Статус:")."""
    if not _STATUS_CMD.search(para):
        return False
    rest = re.sub(r"```.*?```|`[^`]*`", " ", para, flags=re.S)
    rest = re.sub(r"(?im)^\s*\$?\s*(?:bin/)?of\s+status\b.*$", " ", rest)
    return not re.sub(r"(?i)\b(?:статус|status)\b|[\W_]", "", rest)


def _split_status(t):
    """(the text without its trailing status paragraphs, those paragraphs)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (t or "").strip()) if p.strip()]
    status = []
    while paras and _is_status_para(paras[-1]):
        status.insert(0, paras.pop())
    return "\n\n".join(paras), status


_OWNER_REQUEST_V5 = re.compile(
    _OWNER_REQUEST_V4.pattern
    + r"|\bжд(?:у|ём|ем|ёт|ет|ут)\s+(?:\w+\s+){1,2}?тво(?:его|ей|их|й|е)\s+(?:ответ|решени|выбор|«?да)"
    + r"|\bрешить\s+(?:тебе|вам)\b|\b(?:тебе|вам)\s+решить\b")
_OWNER_HEADING_V5 = re.compile(
    r"(?im)^[ \t]*(?:[-*•]\s*|\d+[.)]\s*)?(?:\*\*|__)?[ \t]*"
    r"(?:что\s+(?:нужно\s+)?(?:решить|ответить|выбрать)\s+(?:тебе|вам)|(?:тебе|вам)\s+(?:решить|ответить|выбрать|решать)"
    r"|(?:от\s+(?:тебя|вас))\s+нужн\w*|нужн\w*\s+от\s+(?:тебя|вас)|вопрос\w*\s+к\s+(?:тебе|вам)|решени\w*\s+за\s+(?:тобой|вами)"
    r"|(?:open\s+)?(?:decisions?|questions?)\s+for\s+you|for\s+you\s+to\s+decide|needed\s+from\s+you)"
    r"[^\n]{0,40}?:[ \t]*(?:\*\*|__)?[ \t]*(?:\n|$)")
_NONE_ITEM_V5 = re.compile(r"(?i)^\W*(?:нет|ничего|none|nothing|n/a)\b")
_NOTE_WAITS_OWNER_V5 = re.compile(
    r"(?i)\b(?:await\w*|waiting\s+(?:for|on)|pending|needs?)\s+(?:the\s+)?(?:owner|founder)(?:'s)?\b"
    r"|\b(?:owner|founder)(?:'s)?\s+(?:answers?|decisions?|yes|approval|reply|call|go-ahead)\b"
    r"(?!\s+(?:in\s+hand|received|given|granted|obtained|got)\b)"
    r"|\bжд\w*\s+(?:ответ\w*|решени\w*|«?да»?)\s+(?:от\s+)?(?:владельц|основател)\w*")


_NOTHING_NEEDED_V5 = re.compile(_NOTHING_NEEDED_V4.pattern[:-1] + r"|ничего не прошу|сейчас не прошу|no action needed|nothing to ask)")


def _heading_v5(t):
    """An owner-facing heading followed by items that are not "нет"/"nothing"."""
    for m in _OWNER_HEADING_V5.finditer(t):
        block = re.split(r"\n\s*\n", t[m.end():], maxsplit=1)[0].strip()
        if not block or _NONE_ITEM_V5.search(block) or _NOTHING_NEEDED_V5.search(block[:200]):
            continue
        return True
    return False


def _note_waits_owner(status_paras):
    return any(_NOTE_WAITS_OWNER_V5.search(para) for para in status_paras)


def q_v5(last_text, ask_tool=False):
    """q-v4 past the closing `of status` line, plus owner headings anywhere and a status note that awaits the owner."""
    if ask_tool:
        return True
    t = last_text or ""
    if not t.strip() or t.lstrip()[:200].startswith("API Error") or "API Error:" in t.lstrip()[:200]:
        return False
    body, status = _split_status(t)
    for para in reversed(_closing_paragraphs(body, n=3, limit=1200)):
        plain = _CODE_URL.sub(" ", para)
        sentences = re.split(r"(?<=[.!?…])\s+", plain)
        if any(sen.rstrip().endswith("?") for sen in sentences):
            return True
        if _request_v5(plain):
            return True
    return _heading_v5(body) or _note_waits_owner(status)


def _request_v5(plain):
    for m in _OWNER_REQUEST_V5.finditer(plain):
        start = max(plain.rfind(x, 0, m.start()) for x in ".!?\n") + 1
        ends = [i for i in (plain.find(x, m.end()) for x in ".!?\n") if i >= 0]
        sen = plain[start:min(ends) if ends else len(plain)]
        if not _CONDITIONAL_YES.search(sen) and not _NOTHING_NEEDED_V5.search(sen):
            return True
    return False


_OPTION_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\*\*)?\(?([абвгaAbBcCdD])\)\s*(?:\*\*)?\s*(.+)$")


def options_of(last_text, limit=9):
    """The options a closing question lists as (а)/(б)/(в) or (a)/(b)/(c) lines, in order; [] when none. Inline
    options ("(а) наши позиции или (б) Вова") count too when the closing paragraphs have no option lines."""
    paras = _closing_paragraphs(last_text or "", n=3, limit=1500)
    out = []
    for para in paras:
        for line in para.splitlines():
            m = _OPTION_LINE.match(line)
            if m:
                out.append(re.sub(r"\*\*", "", m.group(2)).strip(" ;:"))
    if not out and paras:
        parts = re.split(r"\(([абвгabcd])\)\s*", paras[-1])
        if len(parts) >= 5:  # text, a, opt, b, opt[, …]
            out = [re.sub(r"\*\*", "", parts[i + 1]).strip(" ;:,.").removesuffix(" или").removesuffix(" or").strip(" ;:,.")
                   for i in range(1, len(parts) - 1, 2)]
    return [o for o in out if o][:limit] if len(out) >= 2 else []


def questions(last_text, ask_tool=False):
    return {"q-v0": q_v0(last_text, ask_tool), "q-v1": q_v1(last_text, ask_tool), "q-v2": q_v2(last_text, ask_tool),
            "q-v3": q_v3(last_text, ask_tool), "q-v4": q_v4(last_text, ask_tool), "q-v5": q_v5(last_text, ask_tool)}


ASK_VERSION = "q-v5"  # the version that opens heuristic asks in the owner's queue (q-v0..q-v4 stay as labels)
ASK_JUDGE = q_v5      # the same version, to judge a stored closing text written before it
REPLY_VERSION = "q-v1"  # the version that pairs a prompt's reply_to with a heuristic question: pinned by the gate


# ---- of-v1 ----
_OWNER_WORDS = re.compile(
    r"(?i)(?:founder|owner|владел|основател" + _alt(TERMS["owner_names"]) + r")\w*\s+(?:said|says|asked|approved|wants|сказал|просил|разрешил|одобрил|хочет|велел)"
    r"|(?:founder|owner)'s (?:yes|approval|words)|(?:«|\")да(?:»|\")\s+(?:от\s+)?(?:владельца|основателя)")


def carries_owner_words(text):
    return bool(text and _OWNER_WORDS.search(text))


def origin_from_record(rec):
    """Origin of a user prompt from the harness's own metadata (history), else None."""
    o = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    kind, turn = o.get("kind"), rec.get("turnOrigin")
    if kind == "peer" or turn == "peer":
        return "peer"
    if kind == "task-notification" or turn in ("task_notification", "scheduled") or rec.get("scheduledTaskId"):
        return "scheduled"
    if turn == "system" or (rec.get("isMeta") and kind != "human"):
        return "system"
    if rec.get("promptSource") == "sdk" or rec.get("entrypoint") == "sdk-cli":
        return "system"  # claude -p / the SDK: automation, not the owner
    if kind == "human" or turn == "human" or rec.get("promptSource") in ("typed", "queued"):
        return "human"
    return None


def of_prompt(text, origin=None, injected=False):
    t = (text or "").lstrip()
    if origin is None:
        if t.startswith("<cross-session-message"):
            origin = "peer"
        elif t.startswith(("<task-notification", "<scheduled", "<system-reminder")):
            origin = "scheduled"
        else:
            origin = "human"
    if origin == "human" and (injected or INJECTED.match(t)):
        origin = "injected"
    if origin == "human" and t.startswith("<pasted_content"):
        origin = "paste"
    return {"origin": origin, "interrupt": t.startswith("[Request interrupted"), "alert_paste": "[FIRING]" in t[:300]}


def prompt_labels(text, first=False, origin=None, injected=False, origin_kind=None, is_meta=False, project=None):
    return {FA: fa_prompt(text, first, origin_kind, is_meta, project), OF: of_prompt(text, origin, injected)}


SUBAGENT_NAME = re.compile(r"^a[0-9a-f]{16}$")


def from_kind(sender):
    """subagent for an in-process subagent's id (`a` + 16 hex, never a position), else peer."""
    return "subagent" if SUBAGENT_NAME.match(str(sender or "")) else "peer"


# ---- escalation tags (docs/handover.md §2.1, 2026-11-06) ----
TAGS = ("prod_change", "money", "security_incident", "irreversible", "window_go")
RISK = ("low", "medium", "high")  # risk_appetite, ordered; attenuation compares by this order
ESCALATE = re.compile(
    r"^(?:ask\.blocking|ask_unanswered\s*>\s*\d+\s*[mh]|ask_due\s*<\s*\d+\s*[dh]|budget_used\s*>\s*\d+%|"
    r"issue\.severity\s*>=\s*(?:low|medium|high|critical)|envelope[ _]breach|"
    r"(?:prod_change|window_go)(?:\{\s*(?:affects|systems)\s*:\s*\[[\w ,.-]*\]\s*\})?|"
    r"money|security_incident|irreversible)$")


def parse_tags(text):
    """`prod_change:storefront+storefront-hosts,money` -> [{"tag": "prod_change", "systems": [...]}, {"tag": "money"}]."""
    out = []
    for part in [x.strip() for x in (text or "").split(",") if x.strip()]:
        tag, _, systems = part.partition(":")
        if tag not in TAGS:
            raise ValueError(f"unknown tag {tag!r} (one of {', '.join(TAGS)})")
        out.append({"tag": tag, "source": "declared",
                    **({"systems": [x for x in systems.replace("+", " ").split() if x]} if systems else {})})
    return out


_TAG_RX = {
    "money": re.compile(r"(?i)(?:\$\s?\d|\d\s?(?:usd|usdc|usdt|eur)\b|деньг|оплат|платеж|invoice|budget|бюджет|перевод средств)"),
    "prod_change": re.compile(r"(?i)(?:\bprod\b|production|прод\b|продакшн|деплой|deploy|выкат|раскат|merge (?:to|into) main|мерж)"),
    "security_incident": re.compile(r"(?i)(?:security incident|инцидент безопасности|утечк|leak(?:ed)? (?:key|secret)|compromis|взлом|cve-\d)"),
    "irreversible": re.compile(r"(?i)(?:необратим|irreversible|drop table|удален\w* (?:таблиц|баз|данных)|delete (?:the )?(?:database|table|bucket)|force[- ]push|rm -rf)"),
}
_DESTRUCTIVE = re.compile(r"(?:\brm\s+-[a-z]*r[a-z]*f|\bDROP\s+(?:TABLE|DATABASE)|kubectl\s+delete|git\s+push\s+[^\n]*--force|"
                          r"\bterraform\s+destroy|\btruncate\s+table)", re.I)


TAG_VERSION = "tg-v1"
# tg-v1 (acme_org, 2026-11-09): a mention negated within three words before it ("Research only; no production change",
# "не трогает прод") is no tag; tg-v0 tagged it, and the guess escalated a routine routing request to the founder.
_NEGATION = re.compile(r"(?i)(?:\b(?:no|not|non|without|never|nothing|none|zero|neither|nor)\b|n't\b|"
                       r"(?<![а-яё])(?:не|нет|без|ни|никак\w*|ничего)(?![а-яё]))")


def _negated(text, start):
    """Whether a negation stands at most three words before `start` in `text`, in the same clause."""
    before = re.split(r"[.;:!?\n]", text[max(0, start - 60):start])[-1]
    words = re.findall(r"[\w'-]+", before)[-4:]
    return bool(words) and bool(_NEGATION.search(" ".join(words)))


def tags_of(text, systems=None):
    """Heuristic tags on an ask's text (a classifier: the agent can set them with of ask --tags).
    Each carries source: guessed and the method version, so an escalation it fires says so. A tag counts when one
    of its mentions is not negated. `systems`: the asker's system, which a guessed tag names (it never meets an
    `affects` list without them; acme_org, 2026-11-09)."""
    text = text or ""
    out = []
    for t, rx in _TAG_RX.items():
        if any(not _negated(text, m.start()) for m in rx.finditer(text)):
            out.append({"tag": t, "source": "guessed", "method": TAG_VERSION, **({"systems": list(systems)} if systems else {})})
    return out


def destructive(command):
    return bool(_DESTRUCTIVE.search(command or ""))


# ---- relay deliveries (2026-11-06) ----
_RELAY = re.compile(r"^(?:<pasted_content[^>]*>\s*)?Message from (\S+) \(via relay\b", re.S)


def relay_sender(text):
    """`org:position` when a prompt is a relay delivery typed into the pane (Claude Code may wrap it as a
    paste), else None. Such a prompt is a peer's message, never the owner's."""
    m = _RELAY.match((text or "").lstrip())
    return m.group(1) if m else None


def relay_body(text):
    t = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", text or "")
    return re.sub(r"\s*</pasted_content[^>]*>\s*$", "", t).strip()


def relay_header(text):
    """(authority_class, header line) of a relay delivery: `none`, `verified`, … from
    "Message from X (via relay, authority: <class>[: reason]; machine …; msg …)"."""
    first = relay_body(text).splitlines()[0] if relay_body(text) else ""
    m = re.search(r"authority:\s*([\w-]+)", first)
    return (m.group(1) if m else None), first


def relay_reply_to(text):
    """The id a relayed message answers ("…; msg M, reply to R)"), or None."""
    m = re.search(r"\breply to ([0-9A-Za-z][\w.-]{7,})\)", relay_header(text)[1] or "")
    return m.group(1) if m else None


# ---- secret paths (RFC 0002 §4.1): one list for the guard and for the Claude Code deny rules ----
# of_server, 2026-11-11 (Acme's sec_keeper via acme_org, msg 01a1c87f): Grafana header files, 36 *.password,
# 6 *.secret.json and a *.client-secret passed the guard (docs/guard.md G1.7). name.env is the guard's own (G1.8, G1.9):
# denied inside ~/.config/<org>, a warning elsewhere, where 225 reads in 21 days were live flows (etcd.env, pins.env).
_CRED_SUFFIXES = ("*.password", "*.header", "*.client-secret", "*.secret_access_key", "*.secrets", "*.creds",
                  "*.secret.json", "*.secret.yaml", "*.secret.yml")
# cloud CLIs' cached sign-ins (G1.12; of_architect's condition 5, msg 01a1db57-17f9): the AWS SSO session's token and
# the role credentials the CLI derives from it, and gcloud's and Azure's token stores, read by whoever runs as the user
_CLOUD_CACHES = (".aws/sso/cache/**", ".aws/cli/cache/**", ".aws/login/cache/**", ".config/gcloud/credentials.db",
                 ".config/gcloud/access_tokens.db", ".config/gcloud/application_default_credentials.json",
                 ".config/gcloud/legacy_credentials/**", ".azure/msal_token_cache.json", ".azure/msal_token_cache.bin",
                 ".azure/accessTokens.json", ".azure/service_principal_entries.json")
SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.p12", "*.pfx", "*.jks",
                # of_server, 2026-11-07: the native rules denied id_ecdsa but the guard let `cat` read it
                "id_ecdsa*", "id_dsa*",
                "*.keystore", ".aws/credentials", ".netrc", "secrets/**",
                # of_architect, 2026-11-06 (of_platform's token.secret gap): tokens, service accounts, kubeconfigs
                "*.secret", "*.token", "*token*.txt", "credentials.json", "service-account*.json",
                "*.kubeconfig", "kubeconfig*",
                # private keys without the id_ prefix (the tenant grant key grant_ed25519), and OF's own secrets
                "*_ed25519", "*_rsa", ".config/of/clickhouse/**", ".config/of/grant_ed25519",
                # of_platform's pre-redaction byte copies of transcripts (c90c161): 24 h of unredacted secrets
                ".local/state/of/redact-backup/**",
                # the secrets broker's service-account tokens (docs/agent-secrets.md §5.3, of_architect's review a):
                # on the founder's Mac the broker shares the agents' OS user, so the guard denies the whole folder
                ".config/of/service-accounts/**",
                # a user organization's security keeper (OFF-020, 2026-11-11): a funded private key in plaintext in an
                # untracked executors.txt at a checkout's root; and a known misspelled secrets file (OFF-001)
                "executors*.txt", "*secrtets*", *_CRED_SUFFIXES, *_CLOUD_CACHES)
SECRET_EXCEPT = (".env.example", ".env.sample", ".env.template", "*.pub", "*.example.txt")


def _glob_rx(g):
    out, i = "", 0
    while i < len(g):
        if g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return out


_SECRET_RX = re.compile(r"(?:^|/)(?:" + "|".join(_glob_rx(g) for g in SECRET_GLOBS) + r")$")
_EXCEPT_RX = re.compile(r"(?:^|/)(?:" + "|".join(_glob_rx(g) for g in SECRET_EXCEPT) + r")$")


def secret_path(path):
    p = (path or "").strip().strip("'\"")
    return bool(p) and bool(_SECRET_RX.search(p)) and not _EXCEPT_RX.search(p)


# Claude Code's deny rules win over allow rules, so they cannot carry the guard's exceptions
# (.env.example, *.pub): the native layer names the usual real variants exactly; the guard, which
# has the exceptions, covers every other .env.* and key file.
DENY_READ_GLOBS = (".env", ".env.local", ".env.prod", ".env.production", ".env.dev", ".env.development",
                   ".env.staging", ".env.test", "*.pem", "*.key", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "*.p12", "*.pfx",
                   "*.jks", "*.keystore", ".aws/credentials", ".netrc", "secrets/**",
                   "*.secret", "*.token", "*token*.txt", "credentials.json", "service-account*.json",
                   "*.kubeconfig", "kubeconfig*", "*_ed25519", "*_rsa", ".config/of/clickhouse/**",
                   ".config/of/grant_ed25519", ".local/state/of/redact-backup/**", ".config/of/service-accounts/**",
                   "executors.txt", *_CRED_SUFFIXES, *_CLOUD_CACHES)


def deny_rules():
    """Claude Code permission deny rules (no interpreter needed: the critical layer)."""
    rules = [f"Read(**/{g})" for g in DENY_READ_GLOBS]
    return rules + ["Bash(printenv:*)", "Bash(env)", "Bash(cat .env)", "Bash(cat **/.env)"]


# ---- command_class (RFC 0005 rule 2.20, babysitting model; of_field-analyst, docs/diagnose.md §4.1) ----
_RUNNER_CMD = re.compile(r"(?:\bbacktest" + _alt(TERMS["runner_commands"]) + ")", re.I)
_MONITOR_CMD = re.compile(
    r"(?:\bsleep\s+\d|\buntil\s|\bwatch\s|\btail\s+-[a-zA-Z]*[fF]|\bjournalctl\b[^|;]*\s-f\b|\blogs\s+[^|;]*-f\b"
    r"|\bgh\s+run\s+watch\b|--watch\b|\bherdr\s+(?:pane|agent)\s+wait\b|\bwait-output\b)")


def command_class(tool, command):
    """runner (a job on a runner: a backtest, or the organization's runner commands in TERMS), monitor (polling or following: sleep loops, until, watch,
    tail -f, logs -f, gh run watch, herdr wait), or other. Non-Bash tools are other."""
    if tool != "Bash" or not command:
        return "other"
    if _RUNNER_CMD.search(command):
        return "runner"
    if _MONITOR_CMD.search(command):
        return "monitor"
    return "other"


# ---- a typed approval (docs/remote.md §3 row 2: a diff approved from the conductor's chat) ----
# The person's own words are the authority; a no, a "not" or a condition ("yes, but …") is never a yes.
APPROVAL_YES = re.compile(r"(?i)(?<!\w)(yes|approve[ds]?|go ahead|ok|okay|да|одобряю|одобрено|согласен|подтверждаю)(?!\w)")
APPROVAL_NOT = re.compile(r"(?i)(?<!\w)(no|not|don'?t|but|wait|нет|не|но|однако|подожди)(?!\w)")


def approves(text):
    """A plain yes: an approval word and no refusal, negation or condition."""
    return bool(text and APPROVAL_YES.search(text) and not APPROVAL_NOT.search(text))

