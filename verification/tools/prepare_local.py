from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[2]
folder = root / "code/mobile_application/src/services/db"
target = folder / "firebaseConfig.ts"
if not target.exists():
    shutil.copyfile(folder / "firebaseConfig.example.ts", target)
    print("Local Firebase configuration template created")
