from pathlib import Path
import json

test_path = Path(r"D:\PhilosophicalZombie\ConsciousnessRealm\embedding\slices.json")

with open(test_path, "r", encoding="utf-8") as f:
    test_data = json.load(f)
with open(test_path, "w", encoding="utf-8") as f:
    json.dump(test_data, f, ensure_ascii=False, indent=4)
