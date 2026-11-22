# Bench in OF: packages, settings, hooks

Bench is the package manager OF runs on: it knows a package, its version, where it came from and what it depends on.
It is its own project, `organic-framework/bench`, kept by `of_bench`, with its own documentation, tests and command
(`bench`). OF is a framework on it: what a package's `main.lua` means is OF's (`bin/of_method.py`). In Ruby's words,
Bench is RubyGems and Bundler, and OF is Rails. The design is organic-framework-build's
`reports/framework/metamethod/10-metamethod-v2.md` and `12-metamethod.md`; the first slice is `11-first-slice.md`.

## Setting it up

- **Bench's checkout.** Clone `organic-framework/bench` to `~/.local/share/bench`, or anywhere and name it with
  `of owner set bench <folder>` (the owner's alone, in a plain terminal: OF runs its code). `of owner show` says where
  OF looks. OF never takes the path from an environment variable. Without a checkout, `of bench` says where it looked,
  and an organization without a `Benchfile.lock` naming a package runs as before.
- **A Lua runtime.** Bench reads every Lua file in a closed environment of its own: Lua 5.4, LuaJIT 2.1 or Lua 5.1
  (macOS: `brew install luajit`; Debian or Ubuntu: `apt install lua5.4`).
- **Protection.** The checkout is code OF imports, the pulse included, outside any sandbox, so it is protected as OF's
  own checkout is: every session's sandbox denies writes to it (`of_spawn.of_checkouts`), and so does the guard.

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

The first package is `of-asks` (`organic-framework/of-asks`, kept by `of_bench`; at work `bin/of_asks_package.py`,
`of_asks`'s): `of ask` calls the hooks before the module "file", and a bare `ask_unanswered` rule rises after the
setting `escalate_after`, only where the organization's `Benchfile.lock` names the package.

## Code

`bin/of_benchpath.py` (where Bench is), `bin/of_method.py` (main.lua's words, settings, hooks), `cmd_bench` in
`bin/of`. Tests: `tests/test_of_method.py`, which take a Bench checkout from `OF_TEST_BENCH` (read by the tests only)
and skip without one.
