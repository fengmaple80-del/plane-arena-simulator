import json, hashlib
from pathlib import Path
ASSETS = Path(__file__).resolve().parents[2] / 'data/monizhan'
ASSET = ASSETS / 'forms.json'
def load_enemy(path=ASSET):
    return json.loads(path.read_bytes())
def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
