import json, subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agent" / "scripts" / "state_manager.py"


def _initialize(requested):
    p = subprocess.Popen(["python3", str(SCRIPT)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": requested, "capabilities": {},
                      "clientInfo": {"name": "t", "version": "1"}}}
    out, _ = p.communicate(json.dumps(req) + "\n", timeout=10)
    # Guard: a server crash produces empty stdout; fail with a legible message
    # rather than an IndexError on splitlines()[0].
    assert out.strip(), "el servidor no emitió stdout (posible crash del servidor MCP)"
    return json.loads(out.splitlines()[0])["result"]["protocolVersion"]


class TestMcpProtocolNegotiation(unittest.TestCase):
    def test_echoes_supported_version(self):
        # Exact match: client requests a known supported version → echoes it.
        self.assertEqual(_initialize("2025-06-18"), "2025-06-18")

    def test_newer_than_all_returns_newest_supported(self):
        # Client requests a version newer than all supported (e.g. far future).
        # MCP convention: return the newest supported version so the client can
        # decide whether to continue. Previously (incorrectly) returned "2024-11-05".
        self.assertEqual(_initialize("9999-01-01"), "2025-06-18")

    def test_middle_version_returns_highest_le(self):
        # Client requests a version between two supported ones (2024-11-05 < 2025-03-26 < 2025-06-18).
        # Highest supported ≤ requested is "2025-03-26".
        self.assertEqual(_initialize("2025-03-26"), "2025-03-26")

    def test_echoes_oldest_supported_version(self):
        # Client requests the oldest supported version → echoes it.
        self.assertEqual(_initialize("2024-11-05"), "2024-11-05")


if __name__ == "__main__":
    unittest.main()
