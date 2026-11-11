"""A fixture for the questions screen (asks-screen-v0): two lab organizations and 60 open cards, never the owner's.

python3 fixture.py OUT   writes OUT/config/of/orgs.yaml, OUT/org-*/registry/positions.yaml and OUT/state (the
event store). Run the screen with OF_CONFIG_DIR=OUT/config/of OF_STATE=OUT/state.
Cards: 8 blocking (Holds work), 6 money or production (2 money), 5 tasks (For your hands), 41 others; ages spread
from 2 minutes to 3 days; numbered titles "Q01".."Q60" so a screen read says which card is where.
"""

import os
import sys
import time

OUT = os.path.abspath(sys.argv[1])
os.makedirs(os.path.join(OUT, "config", "of"), exist_ok=True)
os.makedirs(os.path.join(OUT, "state"), exist_ok=True)
os.environ["OF_CONFIG_DIR"] = os.path.join(OUT, "config", "of")
os.environ["OF_STATE"] = os.path.join(OUT, "state")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "bin"))
import of_events  # noqa: E402

ORGS = {"acme-lab": ["storefront_operator", "storefront_dev", "payments_keeper"],
        "globex-lab": ["k8s_keeper", "data_analyst", "billing_operator"]}
with open(os.path.join(OUT, "config", "of", "orgs.yaml"), "w") as fh:
    fh.write("orgs:\n" + "".join(f"  - name: {o}\n    path: {OUT}/org-{o}\n    workspace: {o.split('-')[0]}\n"
                                 for o in ORGS))
for org, names in ORGS.items():
    os.makedirs(os.path.join(OUT, f"org-{org}", "registry"), exist_ok=True)
    with open(os.path.join(OUT, f"org-{org}", "registry", "positions.yaml"), "w") as fh:
        fh.write(f"tenant: lab\norganization: {org}\nsystems:\n  lab:\n    name: Lab\n    kind: target\n"
                 f"    keeper: {names[0]}\npositions:\n")
        for n in names:
            fh.write(f"  - name: {n}\n    system: lab\n    role: {n.split('_')[1]}\n    kind: keeper\n"
                     f"    owner: founder\n    summary: 'a lab position'\n    status: active\n")

WORDS = ("amber basil cedar delta ember fjord garnet harbor indigo juniper kestrel lumen meadow nickel orchid pepper "
         "quartz raven saffron tundra umber violet willow xenon yarrow zephyr anchor beacon cobalt dune falcon glacier "
         "heron iris jasper koala lagoon marble nectar opal").split()
now = time.time()
for i in range(60):
    org = "acme-lab" if i % 2 == 0 else "globex-lab"
    pos = ORGS[org][i % 3]
    blocking, tags, task = i < 8, [], None
    if 8 <= i < 14:
        tags = ["money"] if i < 10 else ["prod_change:storefront"]
    if 14 <= i < 19:
        task = ["Open the vendor's dashboard", "Press Rotate on the API key", "Say done here"]
    # distinct words per card: of asks merges open questions whose words overlap by 60% or more (of_asks.group)
    w = [WORDS[(i * k + k * k) % len(WORDS)] + str(i + 1) for k in (1, 3, 5, 7)]
    text = (f"Q{i + 1:02d} {w[0]} {w[1]} {w[2]} {w[3]}: " + ("rotate this key by hand" if task else
                                                             "approve this refund?" if tags == ["money"] else
                                                             "ship this release?" if tags else "keep it or move it?")
            + (" " + " ".join(WORDS[(i + k) % len(WORDS)] + str(i + 1) for k in range(40)) if i % 7 == 3 else ""))
    payload = {"detected_by": "declared", "source": "declared", "from_position": pos, "to": "founder", "tags": tags,
               "tags_declared": tags, "context": {"why_now": "a lab card", "evidence": [f"lab://card/{i + 1}"]},
               "blocking": blocking, "topic_key": f"lab{i}",
               "questions": [{"question": text, "options": ["yes (recommended: the lab says so)", "no", "later"]
                              if not task else []}], "classifications": {"q-v0": True, "q-v1": True}}
    if task:
        payload.update(task=True, steps=task, command=None, where="machine")
    rec = of_events.record("agent_question", payload, position=pos, org=org, session=f"lab-session-{pos}")
    age = 120 + (i * 4513) % (3 * 86400)
    rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(now - age))
    assert of_events.append(rec), "the fixture store refused a card"
print(f"fixture: 2 organizations, 60 cards in {OUT}")
