# Organic Framework shell hooks, sourced from ~/.zshrc (one line: source ~/organic-framework/bin/of-shell.zsh).
#
# herdr resumes an agent after its own restart by typing a bare `claude --resume <id>` into the pane's shell, which
# starts it on ~/.claude, outside its permission mode (2026-11-10: 38 sessions). In a herdr pane, for exactly that
# form and without CLAUDE_CONFIG_DIR, run the line `of resume-command` gives: the account holding the session's
# newest transcript, its model and effort, --dangerously-skip-permissions when it ran so. With no answer after two
# tries it does not start: a bare resume runs the session without its deny rules, tool profile and sandbox (hand-over
# af34d473, 2026-11-22). Anything else runs claude unchanged.
claude() {
  if [[ $# -eq 2 && $1 == --resume && -z ${CLAUDE_CONFIG_DIR:-} && -n ${HERDR_PANE_ID:-} ]]; then
    local line try
    for try in 1 2; do
      line=$("${OF_HOME:-$HOME/organic-framework}/bin/of" resume-command "$2" 2>/dev/null)
      [[ -n $line ]] && break
    done
    if [[ -n $line ]]; then
      print -r -- "of: $line"
      eval "$line"
      return
    fi
    print -u2 -r -- "of: no resume line for session $2 (of resume-command gave none, twice). A bare start would run it without its deny rules, tool profile and sandbox, so it does not start. Run: of resume-command $2"
    return 1
  fi
  command claude "$@"
}
