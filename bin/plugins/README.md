# Shipped plugins

Implementations of the plugin families that OF ships (docs/plugins.md; the mechanism is `bin/of_family.py`).

- One folder per family, `bin/plugins/<family>/`, and one module per implementation, `<name>.py`, whose `PLUGIN` is the class. The class has every method its family lists (`of_family.FAMILIES`), takes the choice's settings as one dict, and may define a classmethod `detect()` that says, without side effects, whether its product is on this machine.
- A package outside this repository adds an implementation through the entry point group `organic_framework.<family>`.
- The owner chooses: `~/.config/of/plugins.yaml` (`<family>: <name>`, or `{name: …, <settings>}`), or an organization's registry `plugins:` key, which wins for that organization's data. `of plugins` lists the families, their implementations and the choice in force; `of plugins detect` proposes from what this machine has.
- Every implementation passes its family's contract (`tests/contracts/`).
- The guard denies agents writes to this folder and to installed plugin packages: installing a plugin is the owner's act.
