"""The structural diff (docs/handover.md §3, §4 step 2): one format for every structural change.

v1 applies the kinds the build team needs first: spawn (a new position), mandate_rewrite (fields
of an existing position, and its brief) and swap_holder (a position's live holder). A diff is a
YAML file with a top-level `diff:` mapping.

  of diff check <file>      validate: format, predicted present (review optional), the kind's changes, the
                            registry as it would be after the change (names, duplicates, enums,
                            units), metric names against of_studio's catalogue when it has one
  of diff propose <file>    record of.diff_proposed (the diff and its content hash) and ask the owner
  of diff approve <id>      of.diff_approved: only the owner, from a plain shell on the owner's
                            machine (not an agent session)
  of diff apply <id|file>   apply as narrow text edits (comments kept) to the organization's
                            registry and briefs; write of.diff_applied with the review due; the
                            structure keeper commits them as the single writer

Editing is textual on purpose: the registries are hand-written with comments, which a YAML dump
would lose. Every edit is re-parsed and checked before it is written.
"""

import copy
import datetime as dt
import hashlib
import json
import os
import re

import yaml

KINDS_V1 = ("spawn", "mandate_rewrite", "swap_holder", "delegate", "goal_change")  # v2 ops: amend_delegation, agents_md
KINDS_ALL = ("split", "merge", "spawn", "close", "park", "swap_holder", "reassign_owner", "delegate",
             "move_workplace", "handover_rule", "mandate_rewrite", "grant_change", "goal_change")
GOAL_OPS = ("add", "rewrite", "close")
CLOSED = {"goals": ("achieved", "dropped"), "conditions": ("dropped",)}
TIERS = ("low", "medium", "high")
POSITION_FIELDS = ("name", "system", "unit", "role", "kind", "throughput", "owner", "summary", "status", "enrolled",
                   "mandate", "mandate_exit", "mandate_functions", "response_window", "serves", "note",
                   "contribution", "waiting_on", "holder", "aliases", "owns")
REWRITE_FIELDS = ("summary", "status", "throughput", "mandate", "mandate_exit", "mandate_functions", "serves",
                  "note", "contribution", "role", "response_window", "enrolled", "unit", "owns")


def load(path):
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    d = doc.get("diff") if isinstance(doc, dict) else None
    if not isinstance(d, dict):
        raise ValueError(f"{path}: no top-level `diff:` mapping")
    return json.loads(json.dumps(d, ensure_ascii=False, default=str))  # dates as text: what the event stores


def digest(d):
    """The full SHA-256 of the diff's canonical JSON. An approval binds this, so it must resist a collision: a 64-bit
    prefix let a diff's author find two diffs with one digest in about 2^32 hashes, have the benign one approved and
    apply the other (of_server, 2026-11-12). People are shown its first 16 characters."""
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# ------------------------------------------------------------------ format

def check_format(d, catalogue=None):
    """Problems in the diff itself (before looking at any registry)."""
    out = []
    if not re.match(r"^sd-[a-z0-9][a-z0-9._-]{2,80}$", str(d.get("id") or "")):
        out.append("id must look like sd-<date>-<slug>")
    kind = d.get("kind")
    if kind not in KINDS_ALL:
        out.append(f"kind {kind!r} is not one of {', '.join(KINDS_ALL)}")
    elif kind not in KINDS_V1:
        out.append(f"kind {kind} is specified but not applicable yet (v1: {', '.join(KINDS_V1)})")
    if d.get("tier") not in TIERS:
        out.append(f"tier must be one of {', '.join(TIERS)}")
    for k in ("author", "hypothesis", "org"):
        if not d.get(k):
            out.append(f"{k} is required")
    pred = d.get("predicted")
    if not isinstance(pred, list) or not pred:
        out.append("predicted is required: the signal deltas the review checks (the fixer has nothing to check otherwise)")
    else:
        for i, p in enumerate(pred):
            if not isinstance(p, dict) or not p.get("metric") or "to" not in p:
                out.append(f"predicted[{i}] needs metric and to")
            elif catalogue is not None and str(p["metric"]).split("{")[0] not in catalogue:
                out.append(f"predicted[{i}]: metric {p['metric'].split('{')[0]!r} is not in of_studio's catalogue")
    rv = d.get("review")  # optional (founder, ask 53628150, 2026-11-10): required review dates were filled with invented
    # deadlines; a review, when wanted, is after an event ("after the first 20 tasks"), not a made-up day
    if rv is not None and (not isinstance(rv, dict) or not rv.get("after")):
        out.append("review, when given, is {after: \"<the event after which the prediction is checked>\", "
                   "compare: predicted}")
    if not d.get("reversal"):
        out.append("reversal is required: how the change is taken back")
    if d.get("tier") in ("medium", "high") and "owner" not in (d.get("approvals") or []):
        out.append("a medium or high tier diff needs approvals: [owner]")
    ch = d.get("changes")
    if kind == "goal_change":  # docs/handover.md §3: goals and conditions, medium tier at least
        if not isinstance(ch, dict) or not (ch.get("goals") or ch.get("conditions")):
            out.append("changes.goals or changes.conditions is required")
        if d.get("tier") not in ("medium", "high"):
            out.append("a goal_change is medium tier at least: the owner approves the exact text")
    elif not isinstance(ch, dict) or not ch.get("registry"):
        out.append("changes.registry is required")
    return out


# ------------------------------------------------------------------ text edits

def _positions_span(lines):
    """(start, end) line indexes of the `positions:` block (end exclusive)."""
    start = next((i for i, ln in enumerate(lines) if re.match(r"^positions:\s*(#.*)?$", ln)), None)
    if start is None:
        raise ValueError("the registry has no top-level positions: list")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_][\w-]*:", lines[i])), len(lines))
    return start, end


def _entry_span(lines, name):
    """(start, end) of the list entry `  - name: <name>` (end exclusive, trailing blank lines excluded)."""
    s, e = _positions_span(lines)
    starts = [i for i in range(s + 1, e) if re.match(r"^  - ", lines[i])]
    for k, i in enumerate(starts):
        m = re.match(r"^  - name:\s*['\"]?([^'\"#\s]+)", lines[i])
        if m and m.group(1) == name:
            j = starts[k + 1] if k + 1 < len(starts) else e
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            return i, j
    raise ValueError(f"no position {name!r} in the registry")


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    if "\n" in value.strip():  # several lines: a literal block, as people write them
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")  # reads back exactly
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str)


def _render_field(key, value, indent=4):
    text = yaml.dump({key: value}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=110,
                     default_flow_style=False)
    return [" " * indent + ln for ln in text.rstrip("\n").split("\n")]


def _render_entry(value):
    out = []
    for k in [k for k in POSITION_FIELDS if k in value] + [k for k in value if k not in POSITION_FIELDS]:
        out += _render_field(k, value[k])
    out[0] = "  - " + out[0][4:]
    return out


def add_position(text, value):
    lines = text.split("\n")
    _, e = _positions_span(lines)
    j = e
    while j > 0 and not lines[j - 1].strip():
        j -= 1
    new = [""] + _render_entry(value)
    lines[j:j] = new
    return "\n".join(lines)


def set_fields(text, name, fields):
    """Set fields of one position in place: a field's existing lines (with its nested or folded
    continuation) are replaced, comments elsewhere stay; a missing field goes after `name`."""
    lines = text.split("\n")
    s, e = _entry_span(lines, name)
    for key, value in fields.items():
        s, e = _entry_span(lines, name)
        at = None
        for i in range(s, e):
            body = lines[i][4:] if i > s else lines[i][4:]
            if re.match(rf"^{re.escape(key)}:(\s|$)", body):
                at = i
                break
        if at is None:
            lines[s + 1:s + 1] = _render_field(key, value) if value is not None else []
            continue
        k = at + 1
        while k < e and (lines[k].startswith("      ") or (lines[k].strip() == "" and k + 1 < e
                                                               and lines[k + 1].startswith("      "))):
            k += 1
        repl = _render_field(key, value) if value is not None else []
        if at == s:  # the field sits on the `  - ` line itself (only name does)
            raise ValueError("the name of a position is changed by a rename diff, not here")
        lines[at:k] = repl
    return "\n".join(lines)


def _top_span(lines, key):
    """(start, end) of a top-level `key:` block, or None."""
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(key)}:\s*(#.*)?$", ln)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_][\w-]*:", lines[i])), len(lines))
    return start, end


def pending_decided(d):
    """What a goal_change writes into `decided` until `of diff apply` puts the approval's id there."""
    return f"pending approval of {d.get('id')}"


def set_top_entry(text, block, key, value):
    """Write the entry `key` of the top-level mapping `block` (goals:, conditions:) as `value`, replacing the entry
    when it exists (its own comments go with it) and appending it, or the block, when not; the rest is untouched."""
    lines = text.split("\n")
    rendered = [f"  {key}:"] + [ln for k, v in value.items() for ln in _render_field(k, v)]
    span = _top_span(lines, block)
    if span is None:
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines + ["", f"{block}:"] + rendered) + "\n"
    start, end = span
    at = next((i for i in range(start + 1, end) if re.match(rf"^  {re.escape(str(key))}:\s*(#.*)?$", lines[i])), None)
    if at is None:
        last = max([i for i in range(start + 1, end) if lines[i].strip()] or [start])
        return "\n".join(lines[:last + 1] + rendered + lines[last + 1:])
    stop = next((i for i in range(at + 1, end) if re.match(r"^  \S", lines[i]) or re.match(r"^\S", lines[i])), end)
    while stop > at + 1 and not lines[stop - 1].strip():
        stop -= 1
    return "\n".join(lines[:at] + rendered + lines[stop:])


def set_unit_owner(text, unit, owner):
    """`units: <unit>: owner: X` -> owner, in place."""
    lines = text.split("\n")
    span = _top_span(lines, "units")
    if not span:
        raise ValueError("the registry has no units: block")
    s, e = span
    at = next((i for i in range(s + 1, e) if re.match(rf"^  {re.escape(unit)}:\s*(#.*)?$", lines[i])), None)
    if at is None:
        raise ValueError(f"no unit {unit!r} in units:")
    end = next((i for i in range(at + 1, e) if re.match(r"^  \S", lines[i])), e)
    k = next((i for i in range(at + 1, end) if re.match(r"^    owner:", lines[i])), None)
    if k is None:
        lines[at + 1:at + 1] = [f"    owner: {owner}"]
    else:
        comment = re.search(r"\s+#.*$", lines[k])
        lines[k] = f"    owner: {owner}" + (comment.group(0) if comment else "")
    return "\n".join(lines)


def add_delegation(text, record):
    """Append one record to the top-level `delegations:` list, creating it at the end if missing."""
    lines = text.split("\n")
    item = yaml.safe_dump([record], sort_keys=False, allow_unicode=True, width=110).rstrip("\n").split("\n")
    item = ["  " + ln for ln in item]
    span = _top_span(lines, "delegations")
    if span is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "# Units delegated to another owner (docs/handover.md §2), applied by `of diff apply`.",
                  "delegations:"] + item + [""]
        return "\n".join(lines)
    s, e = span
    j = e
    while j > s + 1 and not lines[j - 1].strip():
        j -= 1
    lines[j:j] = item
    return "\n".join(lines)


AMEND_UNDER = (None, "envelope", "reporting")
AMEND_FIXED = ("id", "unit", "from_owner", "to_owner", "diff")  # a new delegation: op delegate
AMEND_APPEND = ("systems", "positions")  # widened only (of_architect, 2026-11-06); narrowing is a reverse diff


def _block_end(lines, start, indent, stop):
    """The first line after `start` (before `stop`) that ends start's block: indented `indent` or less, except
    a sequence item at exactly `indent` ("- x"), which belongs to the key above it as yaml.safe_dump writes
    a list (acme_org, 2026-11-06: an amended escalate_when kept its old items)."""
    def ends(ln):
        ind = len(ln) - len(ln.lstrip(" "))
        if not ln.strip() or ln.lstrip().startswith("#") or ind > indent:
            return False
        return not (ind == indent and ln.lstrip().startswith("- "))
    return next((i for i in range(start + 1, stop) if ends(lines[i])), stop)


def amend_delegation(text, rid, under, fields):
    """Set fields of the delegation record `rid` (at its top level, or under its `envelope:` or
    `reporting:` mapping), in place: an existing key's lines are replaced, a new key goes after the
    mapping's last line; every other line and comment stays (diff v2, of_architect 2026-11-06)."""
    lines = text.split("\n")
    span = _top_span(lines, "delegations")
    if not span:
        raise ValueError("the registry has no delegations: block")
    s, e = span
    starts = [i for i in range(s + 1, e) if re.match(rf"^  - id:\s*['\"]?{re.escape(rid)}['\"]?\s*(#.*)?$", lines[i])]
    if len(starts) != 1:
        raise ValueError(f"delegation {rid!r} appears {len(starts)} times in delegations:")
    at = starts[0]
    end = next((i for i in range(at + 1, e) if re.match(r"^  - ", lines[i])), e)
    while end > at + 1 and not lines[end - 1].strip():
        end -= 1
    if under:
        u = next((i for i in range(at + 1, end) if re.match(rf"^    {re.escape(under)}:\s*(#.*)?$", lines[i])), None)
        if u is None:
            raise ValueError(f"delegation {rid} has no {under}: mapping")
        lo, hi, ind = u + 1, _block_end(lines, u, 4, end), 6
    else:
        lo, hi, ind = at + 1, end, 4
    for key, value in fields.items():
        new = [" " * ind + ln for ln in yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True,
                                                         width=110).rstrip("\n").split("\n")]
        k = next((i for i in range(lo, hi) if re.match(rf"^{' ' * ind}{re.escape(key)}:", lines[i])), None)
        if k is not None:
            k_end = _block_end(lines, k, ind, hi)
            try:  # the same value already there (perhaps wrapped differently): leave its lines alone
                if yaml.safe_load("\n".join(ln[ind:] for ln in lines[k:k_end])) == {key: value}:
                    continue
            except yaml.YAMLError:
                pass
            lines[k:k_end] = new
            hi += len(new) - (k_end - k)
        else:
            j = hi
            while j > lo and not lines[j - 1].strip():
                j -= 1
            lines[j:j] = new
            hi += len(new)
    return "\n".join(lines)


def _list_edit(lines, lo, hi, ind, key, fn):
    """Replace the list under `key:` (at indent `ind`, between lines lo and hi) by fn(old list), in place, in the
    style it was written: a flow list on the key's line (its comment kept), or block items below it. Returns the
    change in line count; raises ValueError when the key is missing."""
    at = next((i for i in range(lo, hi) if re.match(rf"^{' ' * ind}{re.escape(key)}:(\s|$)", lines[i])), None)
    if at is None:
        raise ValueError(f"no {key}: list")
    head = lines[at][ind + len(key) + 1:]
    comment = re.search(r"\s+#[^\]]*$", head)
    flow = head[:comment.start()] if comment else head
    if flow.strip():
        old = yaml.safe_load(flow) or []
        new = fn(list(old))
        lines[at] = f"{' ' * ind}{key}: [{', '.join(str(x) for x in new)}]" + (comment.group(0) if comment else "")
        return 0
    k, items, item_ind = at + 1, [], None
    while k < hi:
        ln = lines[k]
        m = re.match(r"^( *)- (.*)$", ln)
        if not m or len(m.group(1)) < ind or (item_ind is not None and len(m.group(1)) != item_ind):
            break
        item_ind = len(m.group(1))
        items.append(yaml.safe_load(m.group(2)))
        k += 1
    new = fn(items)
    item_ind = ind if item_ind is None else item_ind
    lines[at + 1:k] = [f"{' ' * item_ind}- {x}" for x in new]
    return len(new) - len(items)


def move_system(text, system, frm, to):
    """Move `system` from units.<frm>.systems to units.<to>.systems, and from the systems list of a delegation of
    unit `frm` (if it names it) to that of a delegation of unit `to` (if there is one), in place; every other line
    and comment stays (acme_org, 2026-11-11: payouts moved from shop to company by hand)."""
    lines = text.split("\n")
    span = _top_span(lines, "units")
    if not span:
        raise ValueError("the registry has no units: block")
    for unit, fn in ((frm, lambda xs: [x for x in xs if x != system]),
                     (to, lambda xs: xs if system in xs else xs + [system])):
        s, e = _top_span(lines, "units")
        at = next((i for i in range(s + 1, e) if re.match(rf"^  {re.escape(unit)}:\s*(#.*)?$", lines[i])), None)
        if at is None:
            raise ValueError(f"no unit {unit!r} in units:")
        end = next((i for i in range(at + 1, e) if re.match(r"^  \S", lines[i])), e)
        _list_edit(lines, at + 1, end, 4, "systems", fn)
    span = _top_span(lines, "delegations")
    if span:
        for unit, fn in ((frm, lambda xs: [x for x in xs if x != system]),
                         (to, lambda xs: xs if system in xs else xs + [system])):
            s, e = _top_span(lines, "delegations")
            starts = [i for i in range(s + 1, e) if re.match(r"^  - id:", lines[i])]
            for j, st in enumerate(starts):
                end = starts[j + 1] if j + 1 < len(starts) else e
                if any(re.match(rf"^    unit:\s*['\"]?{re.escape(unit)}['\"]?\s*(#.*)?$", lines[i]) for i in range(st, end)):
                    try:
                        _list_edit(lines, st + 1, end, 4, "systems", fn)
                    except ValueError:
                        pass  # a delegation without a systems list
    return "\n".join(lines)


def move_position(text, position, frm, to, set_unit=True):
    """Set `position`'s unit to `to`, and move its name from the positions list of each delegation of unit `frm` to
    that of each delegation of unit `to`, in place, each list in its own style; every other line and comment stays
    (docs/handover.md, move_position; a user organization, 2026-11-11: a position moved between two units delegated
    to the same owner was still counted in the old unit's budget). set_unit=False: the position is in `to` already (its
    unit follows its system) and only the delegation lists are behind."""
    if set_unit:
        text = set_fields(text, position, {"unit": to})
    lines = text.split("\n")
    if _top_span(lines, "delegations"):
        for unit, fn in ((frm, lambda xs: [x for x in xs if x != position]),
                         (to, lambda xs: xs if position in xs else xs + [position])):
            s, e = _top_span(lines, "delegations")
            starts = [i for i in range(s + 1, e) if re.match(r"^  - id:", lines[i])]
            for j, st in enumerate(starts):
                end = starts[j + 1] if j + 1 < len(starts) else e
                if any(re.match(rf"^    unit:\s*['\"]?{re.escape(unit)}['\"]?\s*(#.*)?$", lines[i]) for i in range(st, end)):
                    try:
                        _list_edit(lines, st + 1, end, 4, "positions", fn)
                    except ValueError:
                        pass  # a delegation without a positions list
    return "\n".join(lines)


def _unit_delegation(doc, unit):
    """The unit's active delegation: the last record of the unit whose to_owner is the unit's owner, else its last
    record; None when the unit is not delegated (docs/registry.md, Delegations)."""
    recs = [x for x in doc.get("delegations") or [] if isinstance(x, dict) and x.get("unit") == unit]
    owner = ((doc.get("units") or {}).get(unit) or {}).get("owner")
    act = [x for x in recs if x.get("to_owner") == owner]
    return (act or recs or [None])[-1]


UNIT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def add_unit(text, uid, name, owner, systems):
    """Append `units: <uid>: {name, owner, systems}` at the end of the units: block (created at the end of the file
    when missing); every other line stays (acme_org, 2026-11-11: units company and compliance were added by hand in the
    apply commit of a spawn diff)."""
    lines = text.split("\n")
    block = [f"  {uid}:"] + ["    " + ln for ln in yaml.safe_dump({"name": name, "owner": owner}, sort_keys=False,
                                                                      allow_unicode=True, width=110).rstrip("\n").split("\n")]
    block.append(f"    systems: [{', '.join(str(x) for x in systems)}]")
    span = _top_span(lines, "units")
    if span is None:
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines + ["", "units:"] + block + [""])
    s, e = span
    j = e
    while j > s + 1 and not lines[j - 1].strip():
        j -= 1
    lines[j:j] = block
    return "\n".join(lines)


def check_owns(owns, doc):
    """Problems with an owns list a diff sets (RFC 0007: [{system, method, decides}], known system and kind)."""
    import of_model
    if not isinstance(owns, list):
        return ["owns is a list of {system, method, decides}"]
    systems, methods, out = doc.get("systems") or {}, of_model.methods_of(doc), []
    for k, o in enumerate(owns):
        if not isinstance(o, dict) or not o.get("system") or not o.get("method") or \
                not isinstance(o.get("decides", False), bool) or set(o) - {"system", "method", "decides"}:
            out.append(f"owns[{k}] must be {{system, method, decides: true|false}}")
            continue
        if o["system"] not in systems:
            out.append(f"owns[{k}]: {o['system']!r} is not a system")
        if o["method"] not in methods:
            out.append(f"owns[{k}]: {o['method']!r} is not a kind of work ({', '.join(methods)})")
    return out


def insert_line(text, after, line):
    """Insert `line` (one or more lines) after the one line that starts with `after` (leading spaces
    ignored). Idempotent: a text already present is not inserted again."""
    lines = text.split("\n")
    new = line.rstrip("\n").split("\n")
    if new[0] in lines:
        return text
    hits = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(after.strip())]
    if len(hits) != 1:
        raise ValueError(f"the anchor {after[:60]!r} matches {len(hits)} lines (it must match exactly one)")
    at = hits[0]
    end = at + 1  # after the anchor's whole bullet: its continuation lines are indented deeper
    ind = len(lines[at]) - len(lines[at].lstrip())
    while end < len(lines) and lines[end].strip() and len(lines[end]) - len(lines[end].lstrip()) > ind:
        end += 1
    lines[end:end] = new
    return "\n".join(lines)


def _escalate_rx():
    import of_classify
    return of_classify.ESCALATE


# ------------------------------------------------------------------ kinds

def plan(d, doc):
    """[(registry text edit function, description)] and [brief writes] for the diff, checked against
    the parsed registry `doc` of the organization."""
    kind, ch = d.get("kind"), d.get("changes") or {}
    names = {p.get("name") for p in doc.get("positions") or []}
    edits, briefs, problems = [], [], []
    delegations = {(x or {}).get("id") for x in doc.get("delegations") or []}
    for i, c in enumerate(ch.get("registry") or []):
        if not isinstance(c, dict):
            problems.append(f"changes.registry[{i}] must be a mapping")
            continue
        op = c.get("op")
        if op == "amend_delegation":  # v2: any kind may carry it (the approval covers its exact text)
            fields, under = c.get("set") or {}, c.get("under")
            if not c.get("id") or not isinstance(fields, dict) or not fields:
                problems.append(f"changes.registry[{i}]: amend_delegation takes {{op, id, under?: envelope|reporting, "
                                "set: {...}}")
                continue
            if c["id"] not in delegations:
                problems.append(f"changes.registry[{i}]: no delegation {c['id']!r}")
            if under not in AMEND_UNDER:
                problems.append(f"changes.registry[{i}]: under is envelope or reporting (or none: the record itself)")
            fixed = [k for k in fields if not under and k in AMEND_FIXED]
            if fixed:
                problems.append(f"changes.registry[{i}]: {', '.join(fixed)} define the delegation; a new one is op delegate")
            rec = next((x for x in doc.get("delegations") or [] if (x or {}).get("id") == c["id"]), None) or {}
            for k in (AMEND_APPEND if not under else ()):
                if k in fields:
                    new_v, old_v = fields[k], rec.get(k) or []
                    if not isinstance(new_v, list):
                        problems.append(f"changes.registry[{i}]: {k} is a list")
                    elif [x for x in old_v if x not in new_v]:
                        problems.append(f"changes.registry[{i}]: {k} may only grow; it would drop "
                                        f"{', '.join(map(str, (x for x in old_v if x not in new_v)))} "
                                        "(narrowing a delegated unit is a reverse diff)")
            if under == "envelope" and fields.get("critical_denies", "inherited") != "inherited":
                problems.append(f"changes.registry[{i}]: the delegator's critical denies stay in force (inherited)")
            edits.append((lambda t, r=c["id"], u=under, f=fields: amend_delegation(t, r, u, f),
                          f"delegation {c['id']}: {'.'.join(filter(None, [under]))}{'.' if under else ''}"
                          f"{', '.join(fields)}"))
            continue
        if op == "add_unit":  # any kind may carry it: a spawn or a delegation that needs a new unit
            units, sysmap = doc.get("units") or {}, doc.get("systems") or {}
            uid, sids = c.get("id"), c.get("systems")
            added = {(x.get("value") or {}).get("name") for x in ch.get("registry") or [] if isinstance(x, dict)
                     and x.get("op") == "add"}
            if not uid or not c.get("name") or not c.get("owner") or not isinstance(sids, list) or not sids:
                problems.append(f"changes.registry[{i}]: add_unit takes {{op, id, name, owner, systems: [...]}}")
                continue
            if not UNIT_ID.match(str(uid)):
                problems.append(f"changes.registry[{i}]: {uid!r} is not a unit id ({UNIT_ID.pattern})")
            if uid in units:
                problems.append(f"changes.registry[{i}]: unit {uid} exists already")
            if c["owner"] not in names | added:
                problems.append(f"changes.registry[{i}]: owner {c['owner']!r} is not a position (or one this diff adds)")
            for x in sids:
                if x not in sysmap:
                    problems.append(f"changes.registry[{i}]: {x!r} is not a system")
                home = next((u for u, r in units.items() if x in ((r or {}).get("systems") or [])), None)
                moving = any(isinstance(m, dict) and m.get("op") == "move_system" and m.get("system") == x
                             for m in ch.get("registry") or [])
                if home and not moving:
                    problems.append(f"changes.registry[{i}]: {x} is in unit {home}; a system sits in one unit (add "
                                    f"it to {uid} here and move it out of {home} with op move_system, or leave it)")
            edits.insert(0, (lambda t, u=uid, n=c["name"], o=c["owner"], x=list(sids):
                             add_unit(t, u, n, o, [y for y in x if not any(
                                 isinstance(m, dict) and m.get("op") == "move_system" and m.get("system") == y
                                 for m in ch.get("registry") or [])]),
                             f"add unit {uid} (owner {c['owner']})"))
            continue
        if op == "move_system":  # any kind may carry it: a system and its keeper change unit together
            units = doc.get("units") or {}
            sid, frm, to = c.get("system"), c.get("from_unit"), c.get("to_unit")
            if not sid or not frm or not to:
                problems.append(f"changes.registry[{i}]: move_system takes {{op, system, from_unit, to_unit}}")
                continue
            new_units = {x.get("id") for x in ch.get("registry") or [] if isinstance(x, dict)
                         and x.get("op") == "add_unit"}
            for u in (frm, to):
                if u not in units and not (u == to and u in new_units):
                    problems.append(f"changes.registry[{i}]: no unit {u!r}")
            if sid not in (doc.get("systems") or {}):
                problems.append(f"changes.registry[{i}]: {sid!r} is not a system")
            if frm in units and sid not in ((units[frm] or {}).get("systems") or []):
                problems.append(f"changes.registry[{i}]: unit {frm} does not list {sid}")
            if frm == to:
                problems.append(f"changes.registry[{i}]: from_unit and to_unit are the same")
            touched = [x.get("id") for x in doc.get("delegations") or [] if isinstance(x, dict)
                       and x.get("unit") in (frm, to)]
            if touched and d.get("tier") != "high":
                problems.append(f"changes.registry[{i}]: moving a system changes delegation {', '.join(touched)}: a "
                                "high tier diff")
            edits.append((lambda t, a=sid, b=frm, e=to: move_system(t, a, b, e), f"move system {sid}: {frm} -> {to}"
                          + (f" (and delegation {', '.join(touched)})" if touched else "")))
            continue
        if op == "move_position":  # docs/handover.md: one diff only when no authority changes hands
            units = doc.get("units") or {}
            pn, frm, to = c.get("position"), c.get("from_unit"), c.get("to_unit")
            if not pn or not frm or not to:
                problems.append(f"changes.registry[{i}]: move_position takes {{op, position, from_unit, to_unit}}")
                continue
            p = next((x for x in doc.get("positions") or [] if isinstance(x, dict) and x.get("name") == pn), None)
            if p is None:
                problems.append(f"changes.registry[{i}]: no position {pn!r}")
                continue
            for u in (frm, to):
                if u not in units:
                    problems.append(f"changes.registry[{i}]: no unit {u!r}")
            if frm == to:
                problems.append(f"changes.registry[{i}]: from_unit and to_unit are the same")
            here = p.get("unit") or next((u for u, r in units.items() if p.get("system") in ((r or {}).get("systems")
                                                                                               or [])), None)
            a, b = _unit_delegation(doc, frm), _unit_delegation(doc, to)
            # the position may be in `to` already (its unit follows its system) with only frm's list behind (acme_org,
            # 2026-11-11): then the op is the lists' cleanup alone
            cleanup = here == to and pn in ((a or {}).get("positions") or [])
            if here != frm and not cleanup:
                problems.append(f"changes.registry[{i}]: {pn} is in unit {here or 'none'}, not {frm}"
                                + ("" if here != to else f", and {frm}'s delegation does not list it"))
            if (a is None) != (b is None) or (a and b and (a.get("from_owner"), a.get("to_owner"))
                                              != (b.get("from_owner"), b.get("to_owner"))):
                problems.append(f"changes.registry[{i}]: units {frm} and {to} are not delegated by the same delegator "
                                f"to the same delegatee, so moving {pn} changes who holds authority over it: a reverse "
                                f"diff of {frm}'s delegation, then a high-tier delegate or amend_delegation for {to}")
            if d.get("tier") not in ("medium", "high"):
                problems.append(f"changes.registry[{i}]: move_position is medium tier at least: the owner approves "
                                f"that {pn} falls under {to}'s envelope and budget")
            edits.append((lambda t, a_=pn, b_=frm, e_=to, u_=not cleanup or p.get("unit") not in (None, to):
                          move_position(t, a_, b_, e_, set_unit=u_),
                          f"{'list' if cleanup else 'move'} position {pn}: {frm} -> {to}"
                          + (f" (delegations {a.get('id')} -> {b.get('id')})" if a and b else "")))
            continue
        if kind == "spawn":
            v = c.get("value") or {}
            if op != "add" or not isinstance(v, dict):
                problems.append(f"changes.registry[{i}]: spawn takes {{op: add, value: {{position}}}}")
                continue
            missing = [k for k in ("name", "system", "role", "kind", "owner", "summary", "status") if not v.get(k)]
            if missing:
                problems.append(f"changes.registry[{i}]: the new position needs {', '.join(missing)}")
            if v.get("name") in names:
                problems.append(f"changes.registry[{i}]: position {v.get('name')!r} exists already")
            if (v.get("holder") or {}).get("session"):
                problems.append(f"changes.registry[{i}]: a spawned position starts without a holder session; "
                                "bind it with a swap_holder diff or `of apply` once its agent runs")
            edits.append((lambda t, v=v: add_position(t, v), f"add position {v.get('name')}"))
        elif kind in ("mandate_rewrite", "swap_holder"):
            fields = c.get("fields") or {}
            if op != "set" or not c.get("name") or not isinstance(fields, dict) or not fields:
                problems.append(f"changes.registry[{i}]: {kind} takes {{op: set, name: <position>, fields: {{...}}}}")
                continue
            if c["name"] not in names:
                problems.append(f"changes.registry[{i}]: no position {c['name']!r}")
            allowed = ("holder",) if kind == "swap_holder" else REWRITE_FIELDS
            bad = [k for k in fields if k not in allowed]
            if bad:
                problems.append(f"changes.registry[{i}]: {kind} may set only {', '.join(allowed)} (not {', '.join(bad)})")
            if "owns" in fields:
                problems += [f"changes.registry[{i}]: {c['name']}.{x}" for x in check_owns(fields["owns"], doc)]
            if kind == "swap_holder":
                h = fields.get("holder") or {}
                if not isinstance(h, dict) or not h.get("session") or not h.get("runtime"):
                    problems.append(f"changes.registry[{i}]: swap_holder sets holder {{runtime, session, workplace}}")
            edits.append((lambda t, n=c["name"], f=fields: set_fields(t, n, f), f"set {', '.join(fields)} of {c['name']}"))
    if kind == "delegate":
        units = doc.get("units") or {}
        positions = {p.get("name"): p for p in doc.get("positions") or []}
        if d.get("tier") != "high":
            problems.append("a delegation is a high tier diff")
        for i, c in enumerate(ch.get("registry") or []):
            if isinstance(c, dict) and c.get("op") == "amend_delegation":
                continue
            if not isinstance(c, dict) or c.get("op") != "delegate":
                problems.append(f"changes.registry[{i}]: delegate takes {{op: delegate, unit, to_owner, envelope, "
                                "reporting, review, way_back}")
                continue
            unit, to = c.get("unit"), c.get("to_owner")
            if unit not in units:
                problems.append(f"changes.registry[{i}]: no unit {unit!r}")
                continue
            frm = (units[unit] or {}).get("owner")
            if c.get("from_owner") and c["from_owner"] != frm:
                problems.append(f"changes.registry[{i}]: unit {unit} is owned by {frm}, not {c['from_owner']}")
            if to not in positions:
                problems.append(f"changes.registry[{i}]: to_owner {to!r} is not a position (an agent or a person's)")
            elif to == frm:
                problems.append(f"changes.registry[{i}]: {to} owns {unit} already")
            env = c.get("envelope")
            if not isinstance(env, dict) or not env:
                problems.append(f"changes.registry[{i}]: envelope is required (grants, budget, risk_appetite, "
                                "critical_denies: inherited)")
            elif env.get("critical_denies", "inherited") != "inherited":
                problems.append(f"changes.registry[{i}]: the delegator's critical denies stay in force (inherited)")
            esc = ((c.get("reporting") or {}).get("escalate_when"))
            if not isinstance(esc, list) or not esc:
                problems.append(f"changes.registry[{i}]: reporting.escalate_when is required: when the delegator "
                                "is asked at all")
            else:
                for x in esc:
                    if not _escalate_rx().match(str(x).strip()):
                        problems.append(f"changes.registry[{i}]: escalate_when {x!r} is not understood (ask.blocking, "
                                        "ask_unanswered > 2h, budget_used > 80%, issue.severity >= high, envelope_breach, "
                                        "prod_change{affects:[...]}, money, security_incident, irreversible, "
                                        "window_go{systems:[...]})")
            ra = (env or {}).get("risk_appetite") if isinstance(env, dict) else None
            if ra is not None and ra not in ("low", "medium", "high"):
                problems.append(f"changes.registry[{i}]: risk_appetite {ra!r} is not one of low, medium, high")
            if not c.get("way_back"):
                problems.append(f"changes.registry[{i}]: way_back is required (reverse-diff)")
            rec = {"id": c.get("id") or f"del-{d.get('id', '')[3:]}", "unit": unit,
                   "systems": list((units[unit] or {}).get("systems") or []),
                   "positions": sorted(n for n, p in positions.items()
                                       if p.get("system") in ((units[unit] or {}).get("systems") or [])),
                   "from_owner": frm, "to_owner": to, "envelope": env, "reporting": c.get("reporting"),
                   "review": str(c.get("review") or ""), "way_back": c.get("way_back"), "diff": d.get("id")}
            edits.append((lambda t, u=unit, o=to: set_unit_owner(t, u, o), f"unit {unit}: owner {frm} -> {to}"))
            edits.append((lambda t, r=rec: add_delegation(t, r), f"delegations: {rec['id']}"))
    for i, b in enumerate(ch.get("briefs") or []):
        if not isinstance(b, dict) or b.get("op") != "write" or not b.get("path") or not (b.get("text") or b.get("from")):
            problems.append(f"changes.briefs[{i}]: {{op: write, path: registry/briefs/<name>.md, text: ... | from: <file>}}")
            continue
        if not str(b["path"]).startswith("registry/briefs/") or ".." in str(b["path"]):
            problems.append(f"changes.briefs[{i}]: briefs live under registry/briefs/")
            continue
        briefs.append(b)
    for i, a in enumerate(ch.get("agents_md") or []):  # v2: one rule line in the organization's AGENTS.md
        if not isinstance(a, dict) or a.get("op") != "insert_line" or not a.get("after") or not a.get("text"):
            problems.append(f"changes.agents_md[{i}]: {{op: insert_line, after: <the start of one line>, text: ...}}")
            continue
        briefs.append(dict(a, path="AGENTS.md"))
    if kind == "goal_change":
        edits += _goal_edits(d, doc, problems)
    elif ch.get("goals") or ch.get("conditions"):
        problems.append("changes.goals and changes.conditions belong to a goal_change diff")
    for k in ("grants", "routing", "desk"):
        if ch.get(k):
            problems.append(f"changes.{k}: not applied by v1; split it into its own diff later")
    return edits, briefs, problems


def _goal_edits(d, doc, problems):
    """docs/handover.md §3, goal_change: {op: add | rewrite | close, id, value} on goals and conditions. add writes
    the node, rewrite sets the given fields over the existing node, close sets its state (achieved or dropped for a
    goal, dropped for a condition). Each node's `decided` becomes the approval's id at apply; until then it names
    this diff. The goal checks (bin/of_goals.py) then run on the registry as it would be."""
    ch, edits = d.get("changes") or {}, []
    for block in ("goals", "conditions"):
        have = doc.get(block) if isinstance(doc.get(block), dict) else {}
        for i, c in enumerate(ch.get(block) or []):
            tag = f"changes.{block}[{i}]"
            if not isinstance(c, dict) or c.get("op") not in GOAL_OPS or not c.get("id"):
                problems.append(f"{tag}: takes {{op: {' | '.join(GOAL_OPS)}, id, value: {{...}}}}")
                continue
            gid, op, v = str(c["id"]), c["op"], c.get("value") or {}
            if not isinstance(v, dict):
                problems.append(f"{tag}: value must be a mapping")
                continue
            if "decided" in v:
                problems.append(f"{tag}: decided is written by `of diff apply` (the approval's id), not by the diff")
                continue
            if op == "add":
                if gid in have:
                    problems.append(f"{tag}: {gid} exists already; rewrite it")
                    continue
                node = dict(v)
            elif gid not in have or not isinstance(have[gid], dict):
                problems.append(f"{tag}: no {block[:-1]} {gid!r} to {op}")
                continue
            elif op == "rewrite":
                if not v:
                    problems.append(f"{tag}: rewrite needs the fields it sets")
                    continue
                node = dict(have[gid], **v)
            else:
                state = v.get("state")
                if state not in CLOSED[block]:
                    problems.append(f"{tag}: close sets state to {' or '.join(CLOSED[block])}")
                    continue
                node = dict(have[gid], state=state)
            node["decided"] = pending_decided(d)
            edits.append((lambda t, b=block, k=gid, n=node: set_top_entry(t, b, k, n), f"{op} {block[:-1]} {gid}"))
    return edits


def stamp_decided(text, d, approval_id):
    """At apply: every `decided` this goal_change wrote names the approval's id instead of the diff."""
    return text.replace(f"decided: {pending_decided(d)}", f"decided: {approval_id}")


def preview(d, registry_text):
    """(new registry text, problems). The new text parses, and the edits did what they claim."""
    doc = yaml.safe_load(registry_text) or {}
    edits, _, problems = plan(d, doc)
    text = registry_text
    for fn, what in edits:
        try:
            text = fn(text)
        except ValueError as e:
            problems.append(f"{what}: {e}")
    try:
        new = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return text, problems + [f"the edited registry does not parse: {e}"]
    before = {p.get("name"): p for p in doc.get("positions") or []}
    after = {p.get("name"): p for p in new.get("positions") or []}
    for c in (d.get("changes") or {}).get("registry") or []:
        if not isinstance(c, dict):
            continue
        if c.get("op") == "add":
            v = c.get("value") or {}
            if after.get(v.get("name")) != v:
                problems.append(f"after the edit, position {v.get('name')} does not read back as written")
        elif c.get("op") == "set" and c.get("name") in after:
            for k, v in (c.get("fields") or {}).items():
                if after[c["name"]].get(k) != v:
                    problems.append(f"after the edit, {c['name']}.{k} does not read back as written")
    for c in (d.get("changes") or {}).get("registry") or []:
        if isinstance(c, dict) and c.get("op") == "delegate" and c.get("unit") in (new.get("units") or {}):
            if (new["units"][c["unit"]] or {}).get("owner") != c.get("to_owner"):
                problems.append(f"after the edit, unit {c['unit']} is not owned by {c.get('to_owner')}")
            if not any((x or {}).get("unit") == c["unit"] and (x or {}).get("to_owner") == c.get("to_owner")
                       for x in new.get("delegations") or []):
                problems.append(f"after the edit, delegations has no record for {c['unit']}")
    for c in (d.get("changes") or {}).get("registry") or []:
        if isinstance(c, dict) and c.get("op") == "move_system":
            nu = new.get("units") or {}
            if c.get("system") not in ((nu.get(c.get("to_unit")) or {}).get("systems") or []) or \
                    c.get("system") in ((nu.get(c.get("from_unit")) or {}).get("systems") or []):
                problems.append(f"after the edit, {c.get('system')} is not in unit {c.get('to_unit')} alone")
    moved = {c.get("from_unit") for c in (d.get("changes") or {}).get("registry") or [] if isinstance(c, dict)
             and c.get("op") == "move_system"} | {c.get("to_unit") for c in (d.get("changes") or {}).get("registry")
                                                  or [] if isinstance(c, dict) and c.get("op") == "move_system"}
    delegated = {c.get("unit") for c in (d.get("changes") or {}).get("registry") or [] if isinstance(c, dict)
                 and c.get("op") == "delegate"}
    for c in (d.get("changes") or {}).get("registry") or []:
        if isinstance(c, dict) and c.get("op") == "add_unit" and c.get("id"):
            delegated.add(c["id"])
            got = (new.get("units") or {}).get(c["id"]) or {}
            if (got.get("name"), got.get("owner")) != (c.get("name"), c.get("owner")) or \
                    set(got.get("systems") or []) != set(c.get("systems") or []):
                problems.append(f"after the edit, unit {c['id']} does not read back as written")
    for c in (d.get("changes") or {}).get("registry") or []:
        if isinstance(c, dict) and c.get("op") == "move_position":
            pn, frm, to = c.get("position"), c.get("from_unit"), c.get("to_unit")
            got = next((x for x in new.get("positions") or [] if isinstance(x, dict) and x.get("name") == pn), {})
            unit = got.get("unit") or next((u for u, r in (new.get("units") or {}).items()
                                            if got.get("system") in ((r or {}).get("systems") or [])), None)
            if unit != to:
                problems.append(f"after the edit, {pn} is not in unit {to}")
            for x in new.get("delegations") or []:
                if isinstance(x, dict) and "positions" in x and (
                        (x.get("unit") == frm and pn in (x.get("positions") or [])) or
                        (x.get("unit") == to and pn not in (x.get("positions") or []))):
                    problems.append(f"after the edit, delegation {x.get('id')} does not list {pn} as moved")
    for u, rec in (new.get("units") or {}).items():
        if u not in moved | delegated and rec != (doc.get("units") or {}).get(u):
            problems.append(f"the edit changed unit {u}, which the diff does not name")
    owns_set = {c.get("name") for c in (d.get("changes") or {}).get("registry") or [] if isinstance(c, dict)
                and c.get("op") == "set" and "owns" in (c.get("fields") or {})}
    if owns_set:  # RFC 0007: at most one position decides a cell; a diff may not make a second one
        import of_model
        pos = {}
        for p in new.get("positions") or []:
            good = [{"system": str(o["system"]), "method": str(o["method"]), "decides": o.get("decides") is True}
                    for o in p.get("owns") or [] if isinstance(o, dict) and o.get("system") and o.get("method")]
            pos[p.get("name")] = dict(p, owns=good, status=p.get("status") or "active")
        cells = of_model.cells_of(pos, {k: v for k, v in (new.get("systems") or {}).items() if isinstance(v, dict)},
                                  of_model.methods_of(new))
        for r in cells["two_deciders"]:
            if owns_set & set(r["deciders"]):
                problems.append(f"after the edit, {r['system']} x {r['method']} has {len(r['deciders'])} deciders "
                                f"({', '.join(r['deciders'])}); at most one decides (RFC 0007): make the others doers "
                                "in the same diff")
    after_del = {(x or {}).get("id"): x or {} for x in new.get("delegations") or []}
    for c in (d.get("changes") or {}).get("registry") or []:
        if isinstance(c, dict) and c.get("op") == "amend_delegation" and c.get("id") in after_del:
            rec = after_del[c["id"]]
            got = (rec.get(c["under"]) or {}) if c.get("under") else rec
            for k, v in (c.get("set") or {}).items():
                if got.get(k) != v:
                    problems.append(f"after the edit, delegation {c['id']} {k} does not read back as written")
    before_del = {(x or {}).get("id"): x or {} for x in doc.get("delegations") or []}
    named = {c.get("id") for c in (d.get("changes") or {}).get("registry") or [] if isinstance(c, dict)}
    named |= {rid for rid, rec in before_del.items() if rec.get("unit") in moved}  # move_system edits their systems
    pmoved = {u for c in (d.get("changes") or {}).get("registry") or [] if isinstance(c, dict)
              and c.get("op") == "move_position" for u in (c.get("from_unit"), c.get("to_unit"))}
    named |= {rid for rid, rec in before_del.items() if rec.get("unit") in pmoved}  # move_position edits their lists
    for rid, rec in before_del.items():
        if rid not in named and rid in after_del and after_del[rid] != rec:
            problems.append(f"the edit changed delegation {rid}, which the diff does not name")
    untouched = set(before) - {c.get("name") or c.get("position") for c in (d.get("changes") or {}).get("registry")
                               or [] if isinstance(c, dict)}
    for n in untouched:
        if before[n] != after.get(n):
            problems.append(f"the edit changed position {n}, which the diff does not name")
    if set(before) - set(after):
        problems.append("the edit dropped positions: " + ", ".join(sorted(set(before) - set(after))))
    return text, problems


def review_due(d, now=None):
    """The review's date when the author named a number of days; None otherwise. OF never invents one (founder,
    ask 53628150: "remove them completely, they only get in the way")."""
    m = re.search(r"(\d+)\s*days?", str((d.get("review") or {}).get("after") or ""))
    if not m:
        return None
    return ((now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(days=int(m.group(1)))).date().isoformat()


def copy_diff(d):
    return copy.deepcopy(d)
