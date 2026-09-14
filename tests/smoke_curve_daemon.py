"""Verifica o daemon da curva sem tocar no hardware.

Roda --status, --once e --restore-auto com sensores e escrita dublês, e
confere que a curva aplica o valor certo e devolve o automático no fim.
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "main"))

import nitro_core as core  # noqa: E402
import fan_curve as fc  # noqa: E402
import curve_daemon as daemon  # noqa: E402

tmp = tempfile.TemporaryDirectory()
home = Path(tmp.name)
applied = []

core.SensorReading  # existe
def readings():
    return [
        core.SensorReading("cpu_temp", "CPU temperature", "temp", 62.0, "°C"),
        core.SensorReading("gpu_temp", "GPU temperature", "temp", None, "°C"),
        core.SensorReading("board_temp", "Board temperature", "temp", 45.0, "°C"),
        core.SensorReading("cpu_fan", "CPU fan", "fan", 3000, "RPM"),
        core.SensorReading("gpu_fan", "GPU fan", "fan", 2500, "RPM"),
    ]

for name, value in (
    ("user_home", lambda: home),
    ("can_control", lambda: True),
    ("supports", lambda _attr: True),
    ("sensor_readings", readings),
    ("set_fan_speed", lambda cpu, gpu: applied.append((cpu, gpu))),
):
    mock.patch.object(core, name, value).start()

# 1. configuração inicial: desligada; --once ainda calcula/apresenta
print("--- --status (antes de existir estado) ---")
assert daemon.main(["--status"]) == 0

# 2. liga a curva e roda um tick
config = fc.load_config()
config.enabled = True
fc.save_config(config)

print("--- --once ---")
assert daemon.main(["--once"]) == 0
cpu_esperado = fc.speed_for(62.0, config.points("cpu"))
assert applied == [(cpu_esperado, fc.DEFAULT_GPU_IDLE_SPEED)], applied
print(f"aplicado: {applied[-1]} (CPU pela curva, GPU idle 40%)")

# 3. estado publicado para a GUI
estado = fc.read_state()
assert estado["source"] == "once" and estado["applied"] == list(applied[-1]), estado
assert "CPU 62 °C" in estado["description"], estado["description"]
assert estado["notes"] == ["gpu-idle"], estado["notes"]
print("estado publicado:", estado["description"])

# 4. --restore-auto devolve ao firmware
print("--- --restore-auto ---")
assert daemon.main(["--restore-auto"]) == 0
assert applied[-1] == (core.FAN_AUTO, core.FAN_AUTO), applied

# 5. --status agora mostra o último relatório
print("--- --status (com estado) ---")
assert daemon.main(["--status"]) == 0

# 6. laço principal: aplica, e devolve o automático ao receber SIGTERM
import os
import signal
import threading
import time  # noqa: E402

applied.clear()
config = fc.load_config()
config.enabled = True
fc.save_config(config)


def _stop_soon():
    time.sleep(1.5)
    os.kill(os.getpid(), signal.SIGTERM)


print("--- laço principal (SIGTERM após 1,5 s) ---")
threading.Thread(target=_stop_soon, daemon=True).start()
assert daemon.run_loop(0.4, verbose=True) == 0
assert applied, "o laço deveria ter aplicado a curva"
assert applied[-1] == (core.FAN_AUTO, core.FAN_AUTO), applied
print(f"laço OK: {len(applied)} escritas, última {applied[-1]} (automático no encerramento)")

# 7. com o lock ocupado, um segundo aplicador se recusa a escrever
held = fc.CurveLock()
held.acquire()
try:
    assert daemon.run_loop(0.4, verbose=False) == 1
    print("concorrência OK: segundo aplicador recusou com o lock ocupado")
finally:
    held.release()

print("DAEMON OK: curva aplicada, estado publicado, automático restaurado")
tmp.cleanup()