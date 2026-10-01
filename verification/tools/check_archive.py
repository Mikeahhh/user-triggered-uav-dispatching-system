import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
manifest = json.loads((root / "verification/records/file_manifest.json").read_text())
failed = []
for item in manifest["files"]:
    path = root / item["path"]
    if not path.is_file():
        failed.append(item["path"])
        continue
    content = path.read_bytes()
    if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
        failed.append(item["path"])
print(json.dumps({"checked": len(manifest["files"]), "all_passed": not failed, "failed": failed}, indent=2))
raise SystemExit(1 if failed else 0)
