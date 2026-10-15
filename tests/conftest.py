import os
import sys
import tempfile
from pathlib import Path

# Must run before dealradar.config is imported so tests never touch your real database.
os.environ["DEAL_RADAR_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ.pop("TINYFISH_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
