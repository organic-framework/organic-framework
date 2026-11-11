"""Add one blocking card to the fixture store while the screen is open: python3 addcard.py N [AGE_S [WORDS]] (OF_STATE
set). An age older than the selected card sorts the new card above it."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "bin"))
import time  # noqa: E402
import of_events  # noqa: E402
n = int(sys.argv[1])
age = float(sys.argv[2]) if len(sys.argv) > 2 else 0
words = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # a card longer than the screen, to scroll it
tail = (" " + " ".join(f"line{n}w{k}" for k in range(words))) if words else ""
rec = of_events.record("agent_question", {"detected_by": "declared", "source": "declared", "from_position": "k8s_keeper",
                                  "to": "founder", "tags": [], "tags_declared": [], "blocking": True,
                                  "topic_key": f"new{n}", "context": {"why_now": "arrived during the check"},
                                  "questions": [{"question": f"N{n:02d} arrived{n} while{n} reading{n}: stop it?" + tail,
                                                 "options": ["yes", "no"]}],
                                  "classifications": {"q-v0": True, "q-v1": True}},
               position="k8s_keeper", org="globex-lab", session="lab-session-k8s_keeper")
rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() - age))
assert of_events.append(rec)
