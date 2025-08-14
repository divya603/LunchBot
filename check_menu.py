import json
from pathlib import Path

menu = json.loads(Path("menu.json").read_text(encoding="utf-8"))
print(f"✅ Loaded {len(menu)} menu items.")
print(json.dumps(menu[:3], indent=2))
