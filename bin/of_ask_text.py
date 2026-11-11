"""Text rules of an ask: no estimate of effort or duration (founder, 2026-11-11).

He reads an ask's card without the agent's context. Estimates such as "около 15 минут твоих рук" or "1–2 дня
подготовки" confuse him, and he does not want them ("не озвучивал сроки"). A fact stays: a deadline with its source
(a legal due date, a booked window, a limit's reset, a session's or a token's expiry), a setting ("блокировка на
90 дней"), what happened ("~7 ч без US", "за 34 дня не подтвердил"), and an option's consequence or risk, such as a
downtime ("простой около минуты"), which AGENTS.md asks for.

An estimate, which `of ask` refuses:
  - of someone's work: an effort noun after it ("15 минут твоих рук", "1–2 дня подготовки", "of your time"), a task
    or steps sized by it ("задача на минуту", "Шаги (около 5 минут)"), an effort verb before it ("займёт около
    45 минут", "will take"), a need right before it ("нужно около 15–30 минут");
  - the Russian inverted approximation, always a guess: "минут на пять", "минут 10";
  - an approximation (около, ~, about) or a range ("1–2 дня") in a clause that addresses the owner ("от тебя",
    "твоя часть", "you") or says what is needed ("нужно", "need");
  - an approximation in a parenthesis of its own ("(~2 мин)"), unless the clause names a consequence or says it
    was observed ("простой", "откат", "занял", "уже"), or in a parenthesis it opens or closes ("(одна команда, около 2 минут)"), unless the clause names a
    consequence or something observed.
A deadline, a frequency or a limit ("через 3 часа", "в течение 5 дней", "раз в 5–10 минут", "до 10 секунд") and a
past duration ("назад", "уже", "заняло") pass, unless sized as work.

His rule has no exceptions by channel (of_conductor, 2026-11-11). An agent's own words are refused, with the phrases
and how to rewrite: `of ask` (question, options, steps; bin/of cmd_ask), the owner's question of `of diff propose
--ask`, and the note of `of status --reopen-task`, which shows on his card. A question OF files from an agent's pane
is never refused, since that would lose it: `drop` cuts the estimate from the card (bin/of_judge.py file_pane_ask).
OF's own card texts are held to the rule by tests/test_ask_text.py. `python3 bin/of_ask_text.py open` lists the open
asks that hold one, by position, for their owners.
"""
import re
import sys

_NUM = (r"(?:\d+(?:[.,]\d+)?|½|пар[уаы]|несколько|полтора|полторы|один|одну|одна|два|две|три|четыре|пять|шесть|"
        r"десять|пятнадцать|двадцать|тридцать|сорок|a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|"
        r"a\s+few|a\s+couple\s+of|several|half\s+an?)")
_UNIT = (r"(?:секунд[аыу]?|сек\.?|минут[аыу]?|мин\.?|час(?:а|ов|ик)?|ч\.?|дн(?:я|ей)|день|сут(?:ки|ок)|"
         r"недел[яиюь]|месяц(?:а|ев)?|seconds?|secs?|minutes?|mins?|hours?|hrs?|h|days?|weeks?|months?)")
_HALF = r"(?:пол(?:часа|дня|минуты|недели|года))"
# a duration: an optional range start, a number (or none: a bare unit), the unit; "полчаса" is one word
_DUR = re.compile(
    r"(?<![\w.])(?:(?:от\s+)?(?P<lo>\d+(?:[.,]\d+)?)\s*(?:[-–—]|до|to)\s*)?(?:(?P<num>" + _NUM + r")\s*)?"
    r"(?P<unit>" + _HALF + "|" + _UNIT + r")(?!\w)", re.I)
_ABBR = ("h", "ч", "ч.", "мин", "мин.", "сек", "сек.")

# the Russian inverted approximation: "минут на пять", "часа через два", "минут 10"
_INVERTED = re.compile(r"(?<!\d)(?<!\d\s)(?<!\w)(?:минут[уы]?|час(?:а|ов)?|дн(?:я|ей)|недел[иью])\s+(?:"
                       r"(?:на|через|за)\s+(?:\d+|пару|пять|два|две|три|четыре|десять|пятнадцать|двадцать|тридцать|"
                       r"сорок)(?!\w)(?![-–.:/]\d)|"
                       # "минут 10", "минуты две": only at a phrase's end, so "недели 11-02" or "минуты 0 отправок" pass
                       r"(?:\d+|пару|пять|две|два|три|десять|пятнадцать|двадцать|тридцать)(?=\s*(?:[),;!?\n]|\.(?!\d)|$)))",
                       re.I)
_APPROX = r"(?:около|примерно|приблизительно|порядка|где-то|почти|ориентировочно|~|≈|about|around|roughly|approx\.?|" \
          r"approximately|circa)"
_APPROX_END = re.compile(_APPROX + r"\s*$", re.I)
_EFFORT_VERB = re.compile(r"\b(?:займ[её]т|занима\w*|потребу\w*|уйд[её]т|отним\w*|will\s+take|would\s+take|"
                          r"should\s+take|takes)\b[^.;:!?\n]{0,25}$", re.I)
# a need right before an approximate or bare duration ("нужно около 15 минут", "нужна минута"), not a quantity
# ("нужны 60 дней данных")
_NEED_BEFORE = re.compile(r"\b(?:нужн[оаы]?|нужен|надо|needs?|need)\s+(?:" + _APPROX + r"\s*)?$", re.I)
_EFFORT_AFTER = re.compile(r"^\s*(?:(?:твоих|ваших|его|своих|моих|чьих-то)\s+рук|(?:твоего|вашего|его)\s+времени|рук\b|"
                           r"(?:ручной\s+)?работы\s+(?:твоей|твоих|вашей|агента)|подготовки|настройки|установки|"
                           r"of\s+(?:your|his|my)\s+(?:hands|time)|hands-on|of\s+(?:work|prep\w*|setup|effort))",
                           re.I)
_SIZED = re.compile(r"\b(?:задач\w*|шаг(?:и|ов|а)?|дел[оау]|task\w*|steps?|job)\s*\(?\s*(?:(?:примерно|около|всего|на)\s+)*"
                    r"(?:" + _APPROX + r"\s*)?$", re.I)
_PAST_AFTER = re.compile(r"^\s*(?:назад|ago|тому)\b", re.I)
_PAST_BEFORE = re.compile(r"(?:\b(?:за\s+(?:последн|прошл)\w*|last|past|уже|already|заняло|занял[аи]?|ушло|длил\w*|"
                          r"lasted|took|since|for\s+the\s+last)\b)[^.;:!?\n]{0,20}$", re.I)
_DEADLINE_BEFORE = re.compile(r"(?:\b(?:через|в\s+течение|не\s+позже|не\s+позднее|не\s+более|within|in|until|до|"
                              r"no\s+later\s+than|каждые|каждый|every|раз\s+в|по)\s+(?:чем\s+(?:через\s+)?)?"
                              r"(?:" + _APPROX + r"\s*)?)$", re.I)
# a clause that addresses the owner or says what is needed: an approximate duration in it sizes his work
_YOU = re.compile(r"\b(?:ты|тебе|тебя|тобой|тво[йяеюи]\w*|вам|вас|ваш\w*|от\s+них|you|your|нужн\w*|нужен|надо|"
                  r"needs?|часть)\b", re.I)
# a clause that names a consequence (an option's risk, which AGENTS.md asks for): its durations are facts
_IMPACT = re.compile(r"просто[йяи]|простаива|недоступ|не\s+торгует|без\s+торговли|без\s+пакетов|без\s+[A-Za-z]|"
                     r"downtime|outage|offline|unavailable|degrad|деградир|обрыв|разрыв|перерыв|пропус|задерж|"
                     r"запаздыва|холодн\w*\s+старт|cold\s+start|блокир|откат|rollback|дольше|быстрее|медленнее|"
                     r"longer|faster|slower|→|\bсто[ия]т\b|\bлеж[аи]т\b", re.I)
# a clause that says what was observed
_OBSERVED = re.compile(r"\bназад\b|\bago\b|вчера|сегодня|ночью|за\s+ночь|\bбыл[аио]?\b|\bбыли\b|\bш[ёе]л\b|"
                       r"\bшл[аи]\b|занял|ушло|прошл|последн|\bуже\b|каждый\s+раз|при\s+каждом|в\s+среднем|медиан|"
                       r"\bпик\b|\bhad\b|\bwas\b|\bwere\b|\btook\b|lasted|\bsince\b|including", re.I)
# a clause that names a dated fact or a term the owner set (an ask id, his rule)
_TERM = re.compile(r"\b\d{2}-\d{2}\b|\d{1,2}:\d{2}\s*(?:Z|UTC)|\b[0-9a-f]{8}\b|(?:тво\w+|ваш\w+|по)\s+правил",
                   re.I)
_CLAUSE = re.compile(r"[.;!?\n]")


def _clause(text, start, end, width=80):
    """The text around a match, to its clause's start and end (at most `width` characters each way)."""
    before = text[max(0, start - width):start]
    before = before[max((m.end() for m in _CLAUSE.finditer(before)), default=0):]
    after = text[end:end + width]
    m = _CLAUSE.search(after)
    return before, after[:m.start()] if m else after


# What people type, used by the asks popup (bin/of_asks_tui.py), kept here with the other patterns of the founder's
# words so the screens hold English only (AGENTS.md, Language; organic-framework 5462bab):
RECOMMENDED = re.compile(r"(?i)рекоменд|recommend")  # the option the asker recommends, copied by c
YES_KEYS = "yYдД"  # the keys that confirm "Done?" for a task, on a Latin or a Russian keyboard layout


def estimates(text):
    """The phrases of `text` that estimate effort or duration, in order (the module's docstring has the marks)."""
    return [(text or "")[a:b] for a, b in _spans(text or "")]


def _spans(text):
    """[(start, end)] of the estimates in `text`, in order."""
    inverted = list(_INVERTED.finditer(text))
    out = [m.span() for m in inverted]
    for m in _DUR.finditer(text):
        if any(i.start() <= m.start() < i.end() for i in inverted):
            continue
        unit, num, ranged = m.group("unit"), m.group("num"), bool(m.group("lo"))
        half = bool(re.match(_HALF, unit, re.I))
        if not num and not half and (ranged or unit.lower() in _ABBR):
            continue  # a bare abbreviation ("ч", "h") is a letter, not a duration
        before, after = _clause(text, m.start(), m.end())
        if _PAST_AFTER.match(after) or _PAST_BEFORE.search(before):
            continue
        approx = _APPROX_END.search(before)
        need = _NEED_BEFORE.search(before) and (approx or ranged or not num)
        work = _EFFORT_AFTER.match(after) or _SIZED.search(before) or _EFFORT_VERB.search(before) or need
        if not work:
            if _DEADLINE_BEFORE.search(before):
                continue  # a deadline, a frequency or a limit: "через 3 часа", "в течение 5 дней", "раз в 5 минут"
            if not (approx or ranged):
                continue  # a plain duration states a fact: "Токен действует 12 часов"
            around = before + " " + after
            alone = approx and re.search(r"\(\s*" + _APPROX + r"\s*$", before, re.I) and re.match(r"^\s*\)", after)
            if alone and not (_IMPACT.search(around) or _OBSERVED.search(around)):
                out.append((m.start() - len(approx.group(0)), m.end()))
                continue  # "(~2 мин)": a size of his hands, whatever date or id the clause names
            if _IMPACT.search(around) or _OBSERVED.search(around) or _TERM.search(around):
                continue  # a consequence or something observed: "простой около минуты", "~7 ч без US"
            paren = approx and (re.search(r"\(\s*" + _APPROX + r"\s*$", before, re.I) or re.match(r"^\s*\)", after))
            if not (_YOU.search(around) or paren):
                continue
        out.append((m.start() - (len(approx.group(0)) if approx else 0), m.end()))
    return sorted(out)


_WIDEN_LEFT = re.compile(r"(?:\b(?:примерно|около|приблизительно|порядка|где-то|всего|на|about|around|roughly)\s+|"
                         r"[~≈]\s*)*$", re.I)
_WIDEN_RIGHT = re.compile(r"^(?:\s+(?:(?:твоих|ваших|его|своих)\s+рук|(?:твоего|вашего|его)\s+времени|подготовки|"
                          r"of\s+(?:your|his)\s+(?:hands|time)|hands-on))?", re.I)


def drop(text):
    """`text` without its estimates, for a card OF files from an agent's own words (a question left in its pane),
    which must never be refused or lost: "Ротировать ключ (~5 мин)?" becomes "Ротировать ключ?"."""
    text = text or ""
    spans = _spans(text)
    if not spans:
        return text
    out, last = [], 0
    for a, b in spans:
        a = max(last, a - len(_WIDEN_LEFT.search(text[last:a]).group(0)))
        b += len(_WIDEN_RIGHT.match(text[b:]).group(0))
        out.append(text[last:a])
        last = b
    t = "".join(out) + text[last:]
    t = re.sub(r"\(\s*[,;:]?\s*\)", "", t)          # an emptied parenthesis
    t = re.sub(r"\(\s*[,;]\s*", "(", t)
    t = re.sub(r"\s*[,;]\s*\)", ")", t)
    t = re.sub(r"[,;]\s*(?=[,;.:!?])", "", t)
    t = re.sub(r"[ \t]+([,.;:!?)])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def check(question, options=(), steps=None):
    """None, or the refusal of an ask whose question, options or steps estimate effort or duration."""
    found = []
    for part in [question or ""] + list(options or ()) + [steps or ""]:
        for e in estimates(part):
            if e not in found:
                found.append(e)
    if not found:
        return None
    shown = ", ".join(f"«{e.strip()}»" for e in found[:4]) + (" …" if len(found) > 4 else "")
    return (f"refused: the ask estimates time ({shown}); the founder wants no estimates of effort or duration "
            "(2026-11-11). Drop them: name the steps or the work instead. A deadline stays as a fact with its "
            "source, best as a clock time: «окно в 10:00 UTC», «лимит сбросится в 18:00 UTC», «срок по закону — "
            "5 дней с …», or --due.")


def open_asks(conn=None):
    """[(position, ask id, [estimates])] of the open asks to the founder whose text, options or steps hold one."""
    import of_asks
    import of_asks_answer
    import of_store
    conn = conn or of_store.connect()
    out = []
    for a in of_asks.for_owner(of_asks.project(of_asks.load(conn))):
        if a.get("state") != "open":
            continue
        pl = of_asks_answer.ask_details(conn, a["event_id"])[0] or {}
        q = (pl.get("questions") or [{}])[0]
        found = []
        for part in [q.get("question") or a.get("text") or ""] + list(q.get("options") or []) + [pl.get("steps") or ""]:
            found += [e for e in estimates(part) if e not in found]
        if found:
            out.append((a.get("position") or "?", a["event_id"], found))
    return sorted(out)


def main(argv):
    if argv[:1] != ["open"]:
        print("usage: python3 bin/of_ask_text.py open   (open asks to the founder that estimate time, by position)")
        return 2
    rows = open_asks()
    for pos, eid, found in rows:
        print(f"{pos:<24} {eid[-8:]}  " + ", ".join(f"«{e.strip()}»" for e in found))
    print(f"{len(rows)} open ask(s) estimate time")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
