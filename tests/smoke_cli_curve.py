"""Smoke test do CLI da curva (roda isolado, sem tocar no hardware).

Entra no menu 7, mostra o status, edita o nível 1 da CPU e sai. Quando as
respostas acabam, o input levanta EOFError — o mesmo que Ctrl+D, que o ask()
trata como cancelamento. Assim o teste nunca fica preso em loop.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "main"))

# Smoke test manual (o unittest discover só pega test*.py, então este roda
# sob demanda):  python3 tests/smoke_cli_curve.py

import nitro_core as core  # noqa: E402
import fan_curve as fc  # noqa: E402

tmp = tempfile.TemporaryDirectory()
home = Path(tmp.name)

patches = [
    ("user_home", lambda: home),
    ("can_control", lambda: True),
    ("is_root", lambda: False),
    ("is_linux", lambda: True),
    ("driver_base", lambda: Path("/sys/devices/platform/acer-wmi")),
    ("features", lambda: ["fan_speed"]),
    ("supports", lambda attr: True),
    ("set_fan_speed", lambda cpu, gpu: applied.append((cpu, gpu))),
]
applied = []
for name, value in patches:
    mock.patch.object(core, name, value).start()

import main as cli  # noqa: E402

# 7 = menu da curva | 2 = editar CPU | 35, 25 = nível 1 |
# enter x12 = manter os níveis 2..7 (temp + velocidade de cada) |
# 4 = status (depois da edição) | enter = continuar | b = voltar | q = sair
respostas = (["7", "2", "35", "25"] + [""] * 12 + ["4", "", "b", "q"])
fila = list(respostas)


def fake_input(prompt=""):
    print(prompt, end="")
    if not fila:
        raise EOFError
    return fila.pop(0)


saida = io.StringIO()
with mock.patch("builtins.input", side_effect=fake_input):
    with contextlib.redirect_stdout(saida):
        cli.run()

texto = saida.getvalue()
assert "Fan curve" in texto, "menu da curva não apareceu"
assert "Level 1 temperature" in texto, "edição de níveis não rodou"
assert "CPU: 35 °C->25%" in texto, "nível editado não apareceu no status"
salvo = fc.load_config()
assert salvo.points("cpu")[0] == (35, 25), salvo.points("cpu")
assert salvo.points("cpu")[1] == (40, 30), "nível 2 deveria ficar intacto"
assert salvo.points("gpu") == list(zip(fc.DEFAULT_TEMPS, fc.DEFAULT_SPEEDS)), "GPU não deveria mudar"
assert salvo.enabled is False
print("CLI OK: edição persistiu, níveis restantes intactos, GPU intacta")
tmp.cleanup()