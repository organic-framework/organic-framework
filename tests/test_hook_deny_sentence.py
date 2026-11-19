"""Every deny of a tool call ends with the sentence that says not to go round it (bin/of_hook.py NO_WAY_ROUND).

of_secrets-review on organic-framework PR 387: six deny paths lacked it, though the commit said "every deny". The ask
dialog's deny names its own way (`of ask`), and the @mention block stops a prompt, not a call; those two keep their
own text."""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class DenySentenceTest(unittest.TestCase):

    def test_every_tool_call_deny_carries_it(self):
        with open(os.path.join(HERE, "..", "bin", "of_hook.py"), encoding="utf-8") as fh:
            src = fh.read()
        missing = []
        for m in re.finditer(r'"permissionDecision": "deny",\s*"permissionDecisionReason":', src):
            block = src[m.end():src.find("}}", m.end())]
            if block.strip().startswith("why"):  # the reason built just above, or the sentence added here
                block += src[src.rfind("\n    why = ", 0, m.start()):m.start()]
            if "NO_WAY_ROUND" not in block and "ASK_DIALOG_DENY" not in block:
                missing.append(src.count("\n", 0, m.start()) + 1)
        self.assertEqual(missing, [], "deny paths without the last sentence (line numbers)")
        self.assertIn('"behavior": "deny", "message": f"Organic Framework critical deny: {hit[0]}." + NO_WAY_ROUND', src)


if __name__ == "__main__":
    unittest.main()
