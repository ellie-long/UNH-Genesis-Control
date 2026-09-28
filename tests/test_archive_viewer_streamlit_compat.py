from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ArchiveViewerStreamlitCompat(unittest.TestCase):
    def test_deprecated_container_width_removed(self):
        for rel in ("archive/archive_viewer.py", "archive/pages/2_ssRF_Sessions.py"):
            text = (ROOT / rel).read_text()
            self.assertNotIn("use_container_width", text)
            self.assertIn('width="stretch"', text)

if __name__ == "__main__":
    unittest.main()
