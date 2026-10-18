import json
from pathlib import Path

XZ = json.loads((Path(__file__).parent / "xiaozhi-c0fe42a.json").read_text(encoding="utf-8"))
