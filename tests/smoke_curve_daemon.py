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

profile_state = {"value": "balanced"}

for name, value in (
    ("user_home", lambda: home),
    ("can_control", lambda: True),
    ("supports", lambda _attr: True),
    ("sensor_readings", readings),
    ("set_fan_speed", lambda cpu, gpu: applied.append((cpu, gpu))),
    ("current_thermal_profile", lambda: profile_state["value"]),
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

# 7. com o lock ocupado, o daemon espera a vez e assume quando liberar
import threading as _threading

applied.clear()
config = fc.load_config()
config.enabled = True
fc.save_config(config)

held = fc.CurveLock()
held.acquire()
daemon._running = True        # o caso anterior encerrou o laço via SIGTERM
resultado = {}


def _roda():
    resultado["rc"] = daemon.run_loop(0.4, verbose=True)


thread = _threading.Thread(target=_roda, daemon=True)
thread.start()

time.sleep(1.5)
assert not applied, "não deveria escrever enquanto espera o lock"
print("espera OK: não escreve com o lock ocupado pela GUI")

held.release()
time.sleep(daemon.LOCK_WAIT_SECONDS + 1.5)
assert applied, "deveria assumir a aplicação depois do lock liberado"
daemon._running = False       # encerra o laço (o teste roda em thread)
thread.join(timeout=8)
assert resultado.get("rc") == 0, resultado
assert applied[-1] == (core.FAN_AUTO, core.FAN_AUTO), applied
print(f"assunção OK: assumiu após o lock e restaurou o automático ({applied[-1]})")

# 8. troca de perfil térmico: a curva reescreve em vez de confiar no dedupe
applied.clear()
config = fc.load_config()
config.enabled = True
config.min_write_interval_s = 0   # o teste não pode esperar 5 s
fc.save_config(config)
profile_state["value"] = "balanced"
daemon._running = True
resultado = {}
thread = _threading.Thread(target=lambda: resultado.update(rc=daemon.run_loop(0.4, verbose=False)),
                           daemon=True)
thread.start()
time.sleep(1.0)
assert len(applied) == 1, f"dedupe deveria segurar as repetições: {applied}"
profile_state["value"] = "quiet"  # restore do login/GUI trocou o perfil
time.sleep(1.0)
assert len(applied) >= 2, "a curva deveria reaplicar depois da troca de perfil"
daemon._running = False
thread.join(timeout=8)
assert resultado.get("rc") == 0, resultado
print(f"perfil OK: perfil trocado por fora fez a curva reaplicar ({len(applied)} escritas)")

# 9. janela do intervalo mínimo: mesmo com o perfil invalidado, desligar a
#    curva ou encerrar o daemon precisa devolver o automático (0,0)
applied.clear()
config = fc.load_config()
config.enabled = True
config.min_write_interval_s = 5   # a reaplicação vai ficar pendente
fc.save_config(config)
profile_state["value"] = "balanced"
daemon._running = True
resultado = {}
thread = _threading.Thread(target=lambda: resultado.update(rc=daemon.run_loop(0.4, verbose=False)),
                           daemon=True)
thread.start()
time.sleep(1.0)
assert len(applied) == 1, applied
profile_state["value"] = "quiet"  # troca de perfil: invalida, reaplicação pendente
time.sleep(0.4)
config = fc.load_config()         # usuário desliga a curva dentro da janela
config.enabled = False
fc.save_config(config)
time.sleep(1.5)
assert applied[-1] == (core.FAN_AUTO, core.FAN_AUTO), \
    f"handback (0,0) não pode ser pulado: {applied}"
daemon._running = False
thread.join(timeout=8)
assert resultado.get("rc") == 0, resultado
print("handback OK: desligar dentro da janela do intervalo mínimo devolveu o automático")

print("DAEMON OK: curva aplicada, estado publicado, automático restaurado")
tmp.cleanup()