# Bench in OF: packages, settings, hooks

Bench is the package manager OF runs on: it knows a package, its version, where it came from and what it depends on.
It is its own project, `organic-framework/bench`, kept by `of_bench`, with its own documentation, tests and command
(`bench`). OF is a framework on it: what a package's `main.lua` means is OF's (`bin/of_method.py`). In Ruby's words,
Bench is RubyGems and Bundler, and OF is Rails. The design is organic-framework-build's
`reports/framework/metamethod/10-metamethod-v2.md` and `12-metamethod.md`; the first slice is `11-first-slice.md`.

## Setting it up

- **Bench's checkout.** Clone `organic-framework/bench` to `~/.local/share/bench`, or anywhere and name it with
  `of owner set bench <folder>` (the owner's alone, in a plain terminal: OF runs its code), and check out the commit
  this OF is tested with, `54e4cc352a3fe5b5c476bb9a5fe2b1bc80a54c18`: Bench's first slice (its pull request 1;
  CI pins the same commit). `of owner show` says where OF looks. OF never takes the path from an environment variable.
  Without a checkout, `of bench` says where it looked, and an organization without a `Benchfile.lock` naming a package
  runs as before.
- **Bench's outer sandbox.** Bench evaluates only inside an operating-system sandbox of its own: `sandbox-exec` on
  macOS, which is there; on Linux `bubblewrap` (`apt install bubblewrap`) in a scope of the user's systemd manager
  (`systemd-run --user`). Ubuntu 24.04's AppArmor keeps bwrap from making user namespaces until
  `kernel.apparmor_restrict_unprivileged_userns` is 0, which is the machine owner's to set. It cannot start inside an
  agent session's sandbox.
- **A Lua runtime.** Bench reads every Lua file in a closed environment of its own: Lua 5.4, Lua 5.5, LuaJIT 2.1 or
  Lua 5.1 (macOS: `brew install luajit`; Debian or Ubuntu: `apt install lua5.4`).
- **Protection.** The checkout is code OF imports and runs wherever `of bench` runs, outside any session's sandbox too,
  so it is protected as OF's own checkout is: every session's sandbox denies writes to it (`of_spawn.of_checkouts`), and
  so does the guard. `of owner set bench` refuses a development clone (one that runs the release gate), since positions
  write those.
- **The reader's closedness.** OF evaluates an organization's `config/settings/*.lua` and `extensions/*/*.lua` through
  Bench's reader when someone runs `of bench` (nothing in the pulse does yet); those files come from the organization's
  repository, which agents write. Where that can happen differs by system. On Linux a session's sandbox keeps Bench's
  sandbox from starting, so only a process outside every session, such as the owner's terminal, evaluates. On macOS a
  session has no sandbox of its own, so an agent that runs `of bench install`, `hook` or `read` evaluates the
  organization's Lua from its session, inside Bench's `sandbox-exec`. Their safety rests on two layers. The reader: a
  Lua process of its own given no file, with no `io`, `os`, `load`, `require` beyond the package's sources or `debug`,
  and budgets on instructions, memory and wall time. Around it, Bench's operating-system sandbox (above): Bench
  evaluates only inside it and refuses when it cannot start. Both layers and their tests live in
  `organic-framework/bench` (its `docs/bench.md`, "The reader", and `tests/test_bench.py`'s hostile set, one test per
  case), and the guard's keeper passed them in that repository's pull request 1.

## The commands

`of bench <command> [--org <organization>] …` runs Bench's commands with OF as the framework: `install`
(`--frozen`), `check`, `settings <package>`, `hook <package> <module> --input '<json>'`, `fork <package>`,
`rebase <package>`, `test <package>`, `read <file>`. `--org` takes the organization's folder from `orgs.yaml`; else
`--project <folder>`, else the current folder. With OF as the framework, `install` also reads each package's
`main.lua`, checks the organization's settings and hooks against it, and writes what it checked into the lock.
Plain `bench install` installs with no framework.

## OF's part: settings and hooks

A package's `main.lua` declares its settings and the modules an organization may extend:

```lua
setting "escalate_after" { type = "duration", default = "2h" }
module "file" { on = command "ask", run = function(q, ctx) … end }
extendable { "file" }
```

Setting types: `string`, `int`, `number`, `bool`, `duration` (`"30m"`, `"2h"`, `"1d"`, `"1h30m"`), `language`
(`"en"`), `enum` (with `values = { … }`).

- **Tuning.** `config/settings/<package>.lua` in the organization's folder returns its values. The install refuses
  a name the package does not offer and a value of the wrong type, and writes nothing. At work
  `of_method.settings(project, package)` gives the values as installed (a duration in seconds), and refuses when the
  file changed since the install.
- **A hook.** A file under `extensions/<package>/` names when it runs (`before`, `after`, `around`, `instead`), the
  module, the package's major version it was written for (`written_for = "of-asks 1"`) and its function, which
  returns `"pass"`, `"stop"`, `{ decision = "hand", to = … }` or `{ decision = "hold", until = … }`. The install
  refuses a hook on a module that is not extendable or written for another major version.
  `of_method.before(project, package, module, input)` runs the hooks in file-name order; the first that does not pass
  decides, and the result names the hook. A hook whose file changed since the install, that the reader stopped, or
  that returns no decision stops the module: a hook before a module is a gate.

The first package is `of-asks` (`organic-framework/of-asks`, kept by `of_bench`). With `of_asks`'s change
(organic-framework PR 574, open; `bin/of_asks_package.py`), `of ask` calls the hooks before the module "file", and a
bare `ask_unanswered` rule rises after the setting `escalate_after`, only where the organization's `Benchfile.lock`
names the package; until it merges, nothing outside `of bench` evaluates through Bench.

## Code

`bin/of_benchpath.py` (where Bench is), `bin/of_method.py` (main.lua's words, settings, hooks), `cmd_bench` in
`bin/of`. Tests: `tests/test_of_method.py`, which take a Bench checkout from `OF_TEST_BENCH` (read by the tests only)
and skip without one, or where Bench's sandbox cannot start (inside a session's). CI's unit job checks out Bench at
`54e4cc352a3fe5b5c476bb9a5fe2b1bc80a54c18` into `OF_TEST_BENCH`, with bubblewrap installed, so these tests run there and
never skip. A newer Bench is pinned here and in `.github/workflows/tests.yml` together.
