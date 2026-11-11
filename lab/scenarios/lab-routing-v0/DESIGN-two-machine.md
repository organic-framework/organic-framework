# lab-routing-v0 — the two-machine layer (S2→S3)

Step 6 of `docs/server-several-machines.md` (hand-over `1b6a8e88` from `of_server`; owner `of_lab`; verifier `of_server`).
This is the **two-machine** layer of `lab-routing-v0`; the single-machine relay-into-pane faults are in `README.md`
(the relay regression suite). Both run on lab VMs, never on the founder's Mac (`docs/lab.md` §9; the Mac runs
`limactl` only, the heavy work is inside the isolated VMs).

## Why

One organization on two machines must coordinate through the server's **coordination feed** (`of_coord`,
`GET /v1/coord`, pulled by every relay — `ca4fea5`), while each machine acts only on the positions it holds, and no
record crosses into an organization a machine did not join.

## Topology

Three Lima VMs on the lab's `user-v2` network (`lab/image/lab-net.yaml`; vmnet shared mode isolates VMs from each
other, so `user-v2` is required for VM↔VM), egress locked down with `lockdown-labnet.sh` **applied detached**
(`docs/lab.md`: a lockdown from the open control session hangs it for hours):

- **server** — of-server for org `lab-p2`, from `deploy/` via `lab-profile2-v0/server-setup.sh` (k3s + a dedicated
  ClickHouse, option B). NodePort `31820`.
- **client A** and **client B** — each `lab-profile2-v0/client-setup.sh <role> <server_url>`, enrolled to the server
  for `lab-p2` (joined) and carrying `lab-p2-other` (**not** joined — the authorization/routing control).

The shared `lab-p2` registry has two positions with fixed session ids (so routing is exact):
`lab_owner` = `1111…` and `lab_colleague` = `2222…`. The two-machine split: **`lab_owner` is held live on A**
(its stub session runs on A), **`lab_colleague` on B**. Each machine's relay publishes presence for its own live
session; the other machine learns the remote holder through presence and the feed. `of apply`/`of spawn` stay local.

Both relays run (`of relay run`) so the coord loop pulls (`COORD_EVERY` ≈ 10 s); every check waits on the pull, not a
fixed sleep.

## The five checks (each a pass/fail with its own oracle)

| # | Check | Drive | Oracle (independent of the code under test) |
|---|---|---|---|
| 1 | an ask filed on B shows in the asks view on A | on B, `lab_colleague` files `of ask "<q>"` → ship | on A, `of asks --json` lists that ask id, attributed to `lab_colleague`, after A's relay pulls coord; its text is the masked feed text |
| 2 | a state declared on B shows in `of who` and the pulse's view on A | on B, `lab_colleague` `of status working --note …` → ship | on A, `of who --json` shows `lab_colleague` working, as a **remote** holder (machine = B, with person/age); `of_pulse.project()` on A places it |
| 3 | a hand-over A→B is offered in B's pane | on A, `lab_owner` `of handover propose lab_colleague "<title>" --done-when …` → ship; B idle | the offer is typed into **B's** pane only (B's stub records the injected prompt naming the hand-over id); **A types nothing** (A does not hold `lab_colleague`) — needs `of_platform` `40a78229` (delivered 2026-11-10 21:55Z) |
| 4 | a wait from A on B's position gets exactly one nudge, typed on B | on A, `lab_owner` waits on `lab_colleague` (`of status waiting --on lab_colleague`, with an open ask/message); run the pulse on both | **exactly one** nudge reaches B's pane; **zero** on A; a second pulse before B reads it adds **no** second nudge (R3/backoff). One holder ⇒ one machine nudges |
| 5 | 0 records in the wrong organization | on each client, a stub session in `lab-p2-other` (session `3333…`, not joined) does work → `of ship` | on the **server**, the `of_lab_p2` database holds **0** events whose `session_id` is the `lab-p2-other` session; A's store holds **0** coord records tagged `lab-p2-other`; the server refused the unjoined org (authorization) |

Checks 1, 2, 5 run on the feed and routing alone (runnable since `ca4fea5`). Checks 3, 4 need the hold-aware pulse and
offer (build step 2, `of_platform` `40a78229`), now delivered — so all five are in scope.

## Oracle independence

As in `capacity-stall-v0`, the pass/fail is read from ground truth the scenario sets, never from the code under test:
which machine holds which position (where each stub session runs), which org each machine joined (its enroll), and the
ids it filed. A check fails by name.

## Run (one command once built; gated by `oflab preflight`)

`OFLAB_MAX_VMS=3 lab/bin/oflab up routing-srv && … up routing-a && … up routing-b`, then the orchestration copies and
runs `server-setup.sh` / `client-setup.sh`, drives the five checks, and writes `reports/lab-routing-v0 <date>.md` with
each check passed or failed. Preflight refuses under memory pressure or high load, so the three VMs start only when the
Mac has headroom (they must not compete with production agents).

## Status (2026-11-11)

Blocked only on Mac load: `oflab preflight` refused at load 17.41 (lab ceiling cores×0.7 = 7.0; memory 36% is fine),
because many production agents are active. The design and the three checks that never needed `40a78229` are ready; the
run starts the moment load drops below the ceiling.
