from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
folder = root / "code/FYP_alin1_SmartUAVRescueSystem_Mobile_APP-main/services/db"
target = folder / "firebaseConfig.ts"
if not target.exists():
    shutil.copyfile(folder / "firebaseConfig.example.ts", target)
    print("Local Firebase configuration template created")
