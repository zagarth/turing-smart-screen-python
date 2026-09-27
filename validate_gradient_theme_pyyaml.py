from pathlib import Path
import yaml

p = Path(r"F:\Scratch\tester\turing-smart-screen-python\res\themes\Gradient\theme.yaml")
text = p.read_text(encoding="utf-8")
try:
    data = yaml.safe_load(text)
    print("ok", data.get("display", {}).get("DISPLAY_ORIENTATION"))
except Exception as e:
    print("error", repr(e))
    raise
