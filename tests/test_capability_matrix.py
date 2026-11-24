import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestCapabilityMatrix(unittest.TestCase):
    def test_covers_state_engine_and_skills(self):
        text = (ROOT / ".agent" / "adapters" / "_capability_matrix.md").read_text(encoding="utf-8")
        self.assertIn("MCP_FALLBACK", text)
        self.assertIn("SKILLS_DISCOVERY", text)

    def test_covers_code_graph(self):
        text = (ROOT / ".agent" / "adapters" / "_capability_matrix.md").read_text(encoding="utf-8")
        self.assertIn("CODE_GRAPH_MANUAL", text)


if __name__ == "__main__":
    unittest.main()
