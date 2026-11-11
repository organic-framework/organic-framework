import sys, os
sys.path.insert(0, "/home/lab/organic-framework/bin")
import of_client, of_events
url, position, session = sys.argv[1], sys.argv[2], sys.argv[3]
c = of_client.Client(url, key_path="/home/lab/.config/of/machine_ed25519",
                     state_dir="/home/lab/.local/state/of")
c.machine_id = of_events.machine_id()
c._authed("PUT", "/v1/presence", body={"holders": [{"org": "t", "position": position,
          "session_id": session, "kind": "agent", "pane": "w1:p1"}]})
print("presented", position, session[:8], "as", c.machine_id[:8])
