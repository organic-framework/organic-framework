"""The docs site's copy of the repository's Markdown (site/scripts/sync.py, of_docs): every page in the sidebar, no
link to nothing, and each link rewritten to the page's address or to the file on GitHub."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "site", "scripts"))
import sync  # noqa: E402

REPO = "https://github.com/organic-framework/organic-framework"
PAGES = {"README.md": "quickstart", "docs/guard.md": "guard", "docs/rfc/0002-secrets-in-traces.md": "rfc/0002"}
FILES = {"bin/of": "file", "bin": "dir", "LICENSE": "file"}


def rewrite(target, src="docs/guide.md", base="/organic-framework"):
    return sync.rewrite_target(target, src, PAGES, base, REPO, "main", FILES.get)


class SiteTest(unittest.TestCase):
    def test_the_repository_as_it_is(self):
        # every Markdown file under docs/ has a place in the sidebar, and no link points to nothing
        out, sidebar, problems = sync.build(env={"OF_SITE_BASE": "/organic-framework"})
        self.assertEqual(problems, [])
        self.assertIn("quickstart", out)
        self.assertTrue(out["guide"].startswith('---\ntitle: "Guide: running your organization on Organic Framework"\n'))
        self.assertIn(f'editUrl: "{REPO}/edit/main/docs/guide.md"', out["guide"])
        self.assertEqual(len({s for _, items in sync.PAGES for _, s, _ in items}), len(out))  # slugs are unique
        self.assertEqual([g["label"] for g in sidebar][0], "Start here")

    def test_links(self):
        self.assertEqual(rewrite("guard.md#o"), "/organic-framework/guard/#o")
        self.assertEqual(rewrite("../README.md"), "/organic-framework/quickstart/")
        self.assertEqual(rewrite("rfc/0002-secrets-in-traces.md"), "/organic-framework/rfc/0002/")
        self.assertEqual(rewrite("guard.md", base=""), "/guard/")
        self.assertEqual(rewrite("../bin/of"), f"{REPO}/blob/main/bin/of")
        self.assertEqual(rewrite("../bin/"), f"{REPO}/tree/main/bin")
        self.assertEqual(rewrite("https://herdr.dev"), "https://herdr.dev")
        self.assertEqual(rewrite("#section"), "#section")
        self.assertIsNone(rewrite("missing.md"))
        self.assertIsNone(rewrite("../../outside.md"))

    def test_code_is_left_alone(self):
        broken = []
        text = ("See [`guard.md`](guard.md) and `[x](guard.md)`.\n```\n[y](guard.md)\n```\n[gone](gone.md)")
        got = sync.rewrite_body(text, "docs/guide.md", PAGES, "", REPO, "main", FILES.get, broken)
        self.assertEqual(got.split("\n")[0], "See [`guard.md`](/guard/) and `[x](guard.md)`.")
        self.assertEqual(got.split("\n")[2], "[y](guard.md)")
        self.assertEqual(broken, [("docs/guide.md", "gone.md")])

    def test_title_and_description(self):
        title, body = sync.split_title("```\n# not a title\n```\n# The guard\n\nIt stops `cat` of a *secret* file.\n")
        self.assertEqual(title, "The guard")
        self.assertEqual(sync.description(body), "It stops cat of a secret file.")

    def test_settings(self):
        self.assertEqual(sync.settings({})[0], "/organic-framework")
        self.assertEqual(sync.settings({"OF_SITE_BASE": ""})[0], "")  # set but empty: the root, as Astro reads it
        self.assertEqual(sync.settings({"OF_SITE_BASE": "/docs/"})[0], "/docs")


if __name__ == "__main__":
    unittest.main()
