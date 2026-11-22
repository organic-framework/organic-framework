"""Coordination across machines: which events every machine of an organization sees.

docs/server-several-machines.md §4.2 (accepted by of_architect, 2026-11-10). Asks, declared states, hand-overs
and holder changes are projections of each machine's own event store. The organization's server copies these
events, masked, into a feed (`GET /v1/coord`), and every machine's relay pulls the feed into its store, so the
projections see the whole organization. Everything else a projection reads (prompts, turns, pane-level facts)
stays on the machine where it happened: only that machine acts on its panes (§4.3).

`tests/test_coord.py` fails when a projection reads an event name that is in neither list.
"""

# Shared through the organization's server.
EVENTS = frozenset({
    # asks to the founder or a unit owner, and their life
    "of.agent_question", "of.agent_question_judged", "of.agent_question_answered", "of.ask_escalated",
    "of.ask_withdrawn", "of.ask_amended", "of.escalation_due", "of.question_labelled",
    # the owner's own moves on a card in his questions screens (of_asks_inbox): a card handed to a position, whose
    # machine types it in (of_asks_answer.pending), and a card put off till later, on every machine he opens them on
    # (docs/contours.md §3: his machine joins each organization's server and holds none of its positions)
    "of.ask_handed", "of.ask_snoozed",
    # declared states and stalls
    "of.state_declared", "of.stall_detected", "of.nudge_sent",
    "of.ask_ref",  # a card id given to an ask filed before ids existed: the same on every machine
    "of.booking_judged",  # work booked with no basis: the machine that holds the position types the note
    # who holds what, and mandates
    "of.holder_changed", "of.holder_bound", "of.holder_unbound", "of.mandate_changed",
    # owners' labels and structural diffs
    "of.issue_labelled", "of.diff_approved", "of.diff_applied",
    # development loops: each machine's pulse tells the owner, the doers and the conductor it holds of a stalled loop
    # (docs/diagnose.md 2.29), so it needs the loop's events wherever they were recorded (of_platform, 2026-11-16)
    "of.loop_hypothesis", "of.loop_run", "of.loop_decision", "of.loop_cost", "of.loop_run_located", "of.loop_wait",
    # each machine's spend per hour, account and position, so `of capacity spend --by` on any machine shows them all
    # (of_capacity, 2026-11-22: the founder found the agent host's spend missing from the laptop's day and hour views)
    "of.spend_hours",
})
PREFIXES = ("of.handover_", "of.diff_")

# Read by projections, kept on their machine: facts about one session, pane or process.
LOCAL_ONLY = frozenset({
    "of.human_prompt", "of.prompt_origin", "of.injected_prompt", "of.context_injected", "of.paste_sourced",
    "of.agent_message_in", "of.agent_message_out",  # the relay's own copies are on the server already
    "of.turn_end", "of.turn_error", "of.session_start", "of.session_end", "of.compaction", "of.continuation",
    "of.permission_request", "of.permission_denied", "of.permission_resolved",  # a dialog in a pane: answered there
    "of.mandate_check", "of.pulse", "of.capacity_snapshot", "of.artifact_read",
    "of.attention", "of.attention_routed",  # herdr's notices and the desk on this machine
    "of.work_record",  # imported from git and GitHub on each machine
})

RETENTION_S = 30 * 86400  # the server keeps the feed this long; a new machine backfills it
RAW_REF = "coord:"  # raw_ref prefix of an event pulled from the feed: it is never shipped back


def shared(name):
    """Is this event part of the organization's coordination feed?"""
    return name in EVENTS or any(name.startswith(p) for p in PREFIXES)
