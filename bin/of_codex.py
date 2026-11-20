"""Codex as a position's harness (hand-over 9ee1db9f; organic-framework-build reports/codex-main-harness-plan-2026-11-20.md):
the machine's managed requirements file, which holds the guard and the recorder as managed hooks (W3 of
reports/codex-guard-w2-w3-2026-11-20.md, of_server's decision).

Codex 0.170 runs a hook from the user's or a project's layer only when it is trusted, and `codex exec` skips an
untrusted one silently; hooks in the system requirements file run trusted by policy and no agent can change them.
On Linux and macOS that file is /etc/codex/requirements.toml (codex-rs/config/src/loader/mod.rs at rust-v0.170.0), so it
is written by the machine's owner once, with sudo. It holds every Codex run of every user on the machine, of subagent's
included, so only what holds for all of them is pinned there; the rest goes into each position's own layer
(NOT_PINNED says what and why)."""

import os

REQUIREMENTS_PATH = "/etc/codex/requirements.toml"   # codex-rs/config/src/loader/mod.rs, rust-v0.170.0
# The events OF records and guards; Codex 0.170 has no StopFailure, Notification, PermissionDenied or PostToolUseFailure
MANAGED_HOOK_EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                       "PermissionRequest", "PreCompact")
# a PreToolUse hook that times out lets the call through (of_server's W2): the guard gets as long as Claude Code gives it
HOOK_TIMEOUT_S = 30
# the hosts a position's commands may reach (the Claude Code sandbox's of_spawn.SANDBOX_DOMAINS, W3 point 5)
DEFAULT_DOMAINS = ("github.com", "api.github.com", "codeload.github.com", "uploads.github.com",
                   "objects.githubusercontent.com", "raw.githubusercontent.com",
                   "api.openai.com", "chatgpt.com", "auth.openai.com")

NOT_PINNED = (
    ("allowed_web_search_modes", "of subagent's research runs use web_search=live on this machine; each position's "
                                 "layer sets web_search=disabled unless its tool profile names it (W3 point 7)"),
    ("the permission profile (filesystem deny list)", "it is the position's own (its folders, its credentials), so it "
                                                      "goes in the position's layer, in a CODEX_HOME the profile denies "
                                                      "to the session itself (W3 point 4)"),
    ("shell_environment_policy", "not a requirements key at rust-v0.170.0: each position's layer sets inherit=none "
                                 "with its include_only list"),
    ("mcp_servers, apps, plugins", "per position, from of_tools' codex_layer (W7)"),
    ("the model", "per position, in its layer"),
)


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def requirements_toml(of_bin, domains=DEFAULT_DOMAINS):
    """The text of /etc/codex/requirements.toml: managed hooks only, OF's hooks on every event it records (the guard
    on every tool call, no matcher), interactive exec off (write_stdin skips the hook), approvals never, no
    full-access sandbox, the network through Codex's proxy with every Unix socket reachable (of, herdr, the relay) and
    only the allowed domains, the account's browser, computer use, appshots and remote control off."""
    lines = [
        "# Written by `of codex requirements` (Organic Framework); W3 of the Codex positions plan. The machine's owner",
        "# installs it once with sudo; it holds every Codex run of every user here.",
        "allow_managed_hooks_only = true",
        'allowed_approval_policies = ["never"]',
        'allowed_sandbox_modes = ["read-only", "workspace-write"]',
        "allow_browser_and_computer_use = false",
        "allow_appshots = false",
        "allow_remote_control = false",
        "",
        "[features]",
        "hooks = true",
        "unified_exec = false",
        "unified_exec_tty = false",
        "network_proxy = true",
        "",
        "[network_proxy]",
        "enabled = true",
        "dangerously_allow_all_unix_sockets = true",
        "managed_allowed_domains_only = true",
        "",
        "[network_proxy.domains]",
    ]
    lines += [f"{_q(d)} = \"allow\"" for d in domains]
    for ev in MANAGED_HOOK_EVENTS:
        lines += ["", f"[[hooks.{ev}]]", "", f"[[hooks.{ev}.hooks]]", 'type = "command"',
                  f"command = {_q(f'{of_bin} hook {ev}')}", f"timeout = {HOOK_TIMEOUT_S}"]
    return "\n".join(lines) + "\n"


def default_of_bin():
    """The `of` the hooks run: the user's link, else this checkout's."""
    link = os.path.expanduser("~/.local/bin/of")
    return link if os.path.exists(link) else os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
