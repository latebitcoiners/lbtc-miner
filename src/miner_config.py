"""miner_config.py — persistent settings for LBTC-PRO v60."""
import os, json

_OLD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miner_config.json")
_DIR = os.path.join(os.path.expanduser("~"), "LBTC")
CONFIG_PATH = os.path.join(_DIR, "miner_config.json")
if not os.path.exists(CONFIG_PATH) and os.path.exists(_OLD_PATH):
    try:
        os.makedirs(_DIR, exist_ok=True)
        import shutil; shutil.copyfile(_OLD_PATH, CONFIG_PATH)
    except Exception:
        pass

_DEFAULT = {
    "cpu_percent": 100,
    "gpu_percent": 100,
}

def load():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                d = json.load(f)
            for k, v in _DEFAULT.items():
                d.setdefault(k, v)
            return d
    except Exception:
        pass
    return dict(_DEFAULT)

def save(cfg):
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
