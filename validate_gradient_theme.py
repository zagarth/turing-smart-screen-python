from pathlib import Path
import ruamel.yaml

p = Path(r"F:\Scratch\tester\turing-smart-screen-python\res\themes\Gradient\theme.yaml")
yaml = ruamel.yaml.YAML(typ="safe")
data = yaml.load(p.read_text(encoding="utf-8"))
print("ok", data["display"]["DISPLAY_ORIENTATION"])
