#!/usr/bin/env python3
"""Copy the repository's Markdown into the docs site (Starlight), the way GitHub shows it.

The pages stay where they are (README.md, FEATURES.md, CHANGELOG.md, docs/**): the repository is the source,
and this script makes the site's copy before each build. For each page in PAGES it
- drops the blocks between `<!-- site:skip -->` and `<!-- /site:skip -->` (the README's header image);
- takes the first `# ` heading as the page's title and drops it from the body (Starlight prints the title);
- rewrites a link to another page of the site to that page's address, keeping its #anchor;
- rewrites a link to any other file or folder of the repository to that file on GitHub;
- sets the page's "Edit page" link to its own source file on GitHub.
It also writes the sidebar (src/generated/sidebar.json), which astro.config.mjs reads.

It fails, and writes nothing, when a Markdown file under docs/ is in no group of PAGES, or when a link points to
nothing in the repository. Only the Python standard library: it runs on the CI runner and on any machine.

Settings, from the environment (the GitHub Actions workflow sets them from actions/configure-pages):
  OF_SITE_BASE   the site's base path, e.g. /organic-framework ('' or / for a site at the domain's root)
  OF_REPO_URL    the public repository, e.g. https://github.com/organic-framework/organic-framework
  OF_REPO_BRANCH the branch the edit links open (main)

Usage: python3 site/scripts/sync.py [--check]   (--check: report only, write nothing)
"""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
REPO = os.path.dirname(SITE)
OUT = os.path.join(SITE, "src", "content", "docs")
SIDEBAR = os.path.join(SITE, "src", "generated", "sidebar.json")
KEEP = {"index.mdx"}  # the landing page is written by hand, in site/src/content/docs/

# The sidebar, by the four kinds of documentation (Diátaxis, diataxis.fr): learn and start, do one task,
# understand why, look up the machinery; then the design records. Each page: (source, slug, sidebar label).
PAGES = [
    ("Start here", [
        ("README.md", "quickstart", "Quickstart"),
        ("docs/guide.md", "guide", "Guide"),
        ("FEATURES.md", "features", "What works today"),
        ("CHANGELOG.md", "changelog", "Changelog"),
    ]),
    ("How-to", [
        ("docs/onboarding.md", "onboarding", "Onboard an organization"),
        ("docs/close.md", "close", "Close a finished position"),
        ("docs/remote-access.md", "remote-access", "Reach your view from a phone"),
        ("docs/environments.md", "environments", "Choose an environment"),
        ("docs/rollout.md", "rollout", "Pace a rollout"),
    ]),
    ("Concepts", [
        ("docs/mission.md", "mission", "Mission"),
        ("docs/framework.md", "framework", "What OF consists of"),
        ("docs/ontology.md", "ontology", "Ontology"),
        ("docs/ontology-a-core.md", "ontology-a-core", "Ontology: the core"),
        ("docs/method.md", "method", "Method"),
        ("docs/systems.md", "systems", "Systems"),
        ("docs/conductor.md", "conductor", "The conductor"),
        ("docs/desk.md", "desk", "The desk"),
        ("docs/handover.md", "handover", "Hand-overs and diffs"),
        ("docs/works.md", "works", "Works"),
        ("docs/herdr.md", "herdr", "herdr"),
        ("docs/measure.md", "measure", "Measure before and after"),
        ("docs/business-case.md", "business-case", "The business case"),
        ("docs/computation.md", "computation", "From documents to computation"),
    ]),
    ("Reference", [
        ("docs/glossary.md", "glossary", "Glossary"),
        ("docs/registry.md", "registry", "Position registry"),
        ("docs/events.md", "events", "Event log"),
        ("docs/guard.md", "guard", "The guard"),
        ("docs/server.md", "server", "of-server"),
        ("docs/server-several-machines.md", "server-several-machines", "Several machines"),
        ("docs/storage.md", "storage", "Storage"),
        ("docs/capacity.md", "capacity", "Capacity"),
        ("docs/agent-secrets.md", "agent-secrets", "Agents' secrets"),
        ("docs/tools.md", "tools", "Tools of a position"),
        ("docs/studio.md", "studio", "Model and Studio"),
        ("docs/diagnose.md", "diagnose", "Anti-pattern catalogue"),
        ("docs/lab.md", "lab", "The lab"),
    ]),
    ("How OF is built", [
        ("docs/methodology.md", "methodology", "Methodology"),
        ("docs/success.md", "success", "How we see progress"),
        ("docs/onboarding-practice.md", "onboarding-practice", "Onboarding practice"),
    ]),
    ("Design records (RFCs)", [
        ("docs/rfc/0001-organic-framework-platform.md", "rfc/0001-platform", "0001 The platform"),
        ("docs/rfc/0002-secrets-in-traces.md", "rfc/0002-secrets-in-traces", "0002 Secrets in traces"),
        ("docs/rfc/0003-web-hands.md", "rfc/0003-web-hands", "0003 Web hands"),
        ("docs/rfc/0004-tool-profiles.md", "rfc/0004-tool-profiles", "0004 Tool profiles"),
        ("docs/rfc/0005-development-loops.md", "rfc/0005-development-loops", "0005 Development loops"),
        ("docs/rfc/0006-position-memory.md", "rfc/0006-position-memory", "0006 Position memory"),
        ("docs/rfc/0007-system-architect-and-cells.md", "rfc/0007-system-architect", "0007 The system architect"),
    ]),
]
# A page's title is its first heading, except here.
TITLES = {"README.md": "Quickstart"}
# Markdown under docs/ that is not a page of the site, with the reason.
NOT_PAGES = {}

LINK = re.compile(r"(!?\[[^\]]*\])\(([^)\s]+)((?:\s+\"[^\"]*\")?)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
# A block only GitHub shows (the README's header image and its links): the site has its own landing page.
SKIP = re.compile(r"<!-- site:skip -->.*?<!-- /site:skip -->\n*", re.S)


def settings(env=os.environ):
    base = env.get("OF_SITE_BASE")  # set but empty means the root, as astro.config.mjs reads it
    base = ("/organic-framework" if base is None else base).strip()
    base = "" if base in ("", "/") else "/" + base.strip("/")
    repo = (env.get("OF_REPO_URL") or "https://github.com/organic-framework/organic-framework").rstrip("/")
    return base, repo, env.get("OF_REPO_BRANCH") or "main"


def page_index():
    return {src: slug for _, items in PAGES for src, slug, _ in items}


def rewrite_target(target, src, pages, base, repo, branch, exists):
    """The address a link in `src` should have on the site, or None when it points to nothing."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
        return target  # a URL, mailto: or an anchor in the same page
    path, _, anchor = target.partition("#")
    rel = os.path.normpath(os.path.join(os.path.dirname(src), path)).replace(os.sep, "/")
    if rel.startswith("../") or rel == "..":
        return None  # outside the repository
    frag = "#" + anchor if anchor else ""
    if rel in pages:
        return f"{base}/{pages[rel]}/{frag}"
    kind = exists(rel)
    if kind == "file":
        return f"{repo}/blob/{branch}/{rel}{frag}"
    if kind == "dir":
        return f"{repo}/tree/{branch}/{rel.rstrip('/')}"
    return None


def rewrite_body(text, src, pages, base, repo, branch, exists, broken):
    """Rewrite every link outside code (fenced blocks and inline code spans)."""
    out, fenced = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        code = [m.span() for m in re.finditer(r"(`+)[^`]*?\1", line)]  # inline code spans

        def sub(m):
            if any(a <= m.start() < b for a, b in code):
                return m.group(0)  # a link written inside code is text, not a link
            new = rewrite_target(m.group(2), src, pages, base, repo, branch, exists)
            if new is None:
                broken.append((src, m.group(2)))
                return m.group(0)
            return f"{m.group(1)}({new}{m.group(3)})"
        out.append(LINK.sub(sub, line))
    return "\n".join(out)


def split_title(text):
    """(title, body without that heading) from the first `# ` heading outside code."""
    lines, fenced = text.split("\n"), False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("# "):
            body = lines[:i] + lines[i + 1:]
            if i < len(body) and not body[i].strip():
                del body[i]
            return line[2:].strip(), "\n".join(body).lstrip("\n")
    return None, text


def description(body, limit=160):
    """The first plain paragraph, as one line of text, for the page's meta description."""
    para, fenced = [], False
    for line in body.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
            continue
        s = line.strip()
        if fenced or s.startswith(("#", "|", ">", "-", "*", "<", "1.")) and not para:
            continue
        if not s:
            if para:
                break
            continue
        para.append(s)
    text = " ".join(para)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*]", "", text)
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def build(repo_dir=REPO, env=os.environ):
    """{slug: page text}, sidebar, problems. Reads only; writes nothing."""
    base, repo, branch = settings(env)
    pages = page_index()
    problems = []

    def exists(rel):
        p = os.path.join(repo_dir, rel)
        return "file" if os.path.isfile(p) else "dir" if os.path.isdir(p) else None

    for root, _, files in os.walk(os.path.join(repo_dir, "docs")):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo_dir).replace(os.sep, "/")
            if f.endswith(".md") and rel not in pages and rel not in NOT_PAGES:
                problems.append(f"{rel}: in no group of PAGES (site/scripts/sync.py); add it, or to NOT_PAGES")
    out, broken = {}, []
    for src, slug in pages.items():
        path = os.path.join(repo_dir, src)
        if not os.path.isfile(path):
            problems.append(f"{src}: listed in PAGES, missing in the repository")
            continue
        with open(path, encoding="utf-8") as fh:
            title, body = split_title(SKIP.sub("", fh.read()))
        title = TITLES.get(src) or title
        if not title:
            problems.append(f"{src}: no `# ` heading to take the title from")
            continue
        body = rewrite_body(body, src, pages, base, repo, branch, exists, broken)
        front = {"title": title, "editUrl": f"{repo}/edit/{branch}/{src}"}
        desc = description(body)
        if desc:
            front["description"] = desc
        head = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in front.items()) + "---\n\n"
        out[slug] = head + body.rstrip("\n") + "\n"
    problems += [f"{src}: link to nothing in the repository: {target}" for src, target in broken]
    sidebar = [{"label": label, "items": [{"label": lab, "slug": slug} for _, slug, lab in items]}
               for label, items in PAGES]
    return out, sidebar, problems


def write(out, sidebar, out_dir=OUT, sidebar_path=SIDEBAR):
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):  # the last run's copies go; the hand-written landing page stays
        if name in KEEP:
            continue
        p = os.path.join(out_dir, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    for slug, text in out.items():
        p = os.path.join(out_dir, slug + ".md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    os.makedirs(os.path.dirname(sidebar_path), exist_ok=True)
    with open(sidebar_path, "w", encoding="utf-8") as fh:
        json.dump(sidebar, fh, ensure_ascii=False, indent=1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out, sidebar, problems = build()
    for p in problems:
        print(f"sync: {p}", file=sys.stderr)
    if problems:
        print(f"sync: {len(problems)} problem(s); nothing written", file=sys.stderr)
        return 1
    if "--check" not in argv:
        write(out, sidebar)
    print(f"sync: {len(out)} pages" + (" checked" if "--check" in argv else f" written to {os.path.relpath(OUT)}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
