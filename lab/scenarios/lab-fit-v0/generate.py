#!/usr/bin/env python3
"""lab-fit-v0: generate labelled dispatches for the fictional roster (roster.json) with a larger model.

Writes cases.json: [{id, sender, receiver, text, label, owner}], label in of_fit.VERDICTS. Run once, review the file,
commit it; the scorer (run.py) reads the committed file, so a score is reproducible. Costs a few cents (Sonnet).

    python3 lab/scenarios/lab-fit-v0/generate.py [--per-kind 12]
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
import of_capacity  # noqa: E402
import of_case  # noqa: E402

KINDS = {
    "in_mandate": "new work that plainly belongs to the receiver's own mandate",
    "other_position": "new work that plainly belongs to ANOTHER position of the roster (not the receiver): set owner "
                      "to that position. Make some of them subtle: the work uses the receiver's data or sounds close "
                      "to its field, but answers another position's question",
    "no_position": "new work that no position's mandate covers (a missing role: e.g. legal, hiring, tax, a new "
                   "venue's market making, customer support)",
    "not_work": "a message that hands out no new work: a thanks, a status, a reply to the receiver's question, a "
                "review of what it delivered, a rule for reporting",
}

SCHEMA = {"type": "object", "required": ["cases"], "additionalProperties": False,
          "properties": {"cases": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["receiver", "text", "owner"],
              "properties": {"receiver": {"type": "string"}, "text": {"type": "string"}, "owner": {"type": "string"}}}}}}


def ask(prompt, model="sonnet"):
    d = of_case.account()
    argv = [of_capacity.claude_bin(), "-p", prompt, "--model", model, "--tools", "", "--json-schema",
            json.dumps(SCHEMA), "--output-format", "json", "--no-session-persistence", "--settings",
            '{"disableAllHooks": true}', "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "3"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=of_capacity.probe_env(d))
    j = json.loads(r.stdout)
    return (j.get("structured_output") or {}).get("cases") or [], j.get("total_cost_usd") or 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-kind", type=int, default=12)
    a = ap.parse_args()
    roster = json.load(open(os.path.join(HERE, "roster.json")))
    names = [p["name"] for p in roster["positions"]]
    cases, cost = [], 0.0
    for kind, what in KINDS.items():
        prompt = (f"Roster of an organization of AI agents:\n{json.dumps(roster['positions'], indent=1)}\n\n"
                  f"Write {a.per_kind} realistic messages that the manager {roster['manager']} sends to positions "
                  f"under it (receivers: any position but {roster['manager']} and acme_org). Each message is {what}. "
                  "Write like a busy manager: 2 to 6 sentences, concrete numbers and names, no label words such as "
                  "'mandate' or 'your role'. Vary the receivers. owner: the position whose mandate the work belongs "
                  "to; the receiver for in_mandate, another position for other_position, empty for no_position and "
                  "not_work.")
        got, c = ask(prompt)
        cost += c
        for x in got:
            if x.get("receiver") not in names:
                continue
            cases.append({"id": f"{kind}-{len(cases):03d}", "sender": roster["manager"], "receiver": x["receiver"],
                          "text": x["text"], "label": kind,
                          "owner": x.get("owner") if kind in ("in_mandate", "other_position") else ""})
    with open(os.path.join(HERE, "cases.json"), "w") as fh:
        json.dump(cases, fh, ensure_ascii=False, indent=1)
    print(f"{len(cases)} cases, ${cost:.3f}")


if __name__ == "__main__":
    main()
