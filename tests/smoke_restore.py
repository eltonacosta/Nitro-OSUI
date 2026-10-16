"""Verifica a reconfiguração da sessão (nitroctl-restore) sem tocar no hardware.

Ciclo completo: 'Save configuration' guarda os atributos do driver e o perfil
térmico; depois de um "boot" simulado (o driver volta aos padrões) o restore
reaplica tudo. Também cobre: nada salvo (silencioso), driver ausente, falta de
permissão de escrita, fan_speed ignorado com a curva ligada e perfil térmico
que o firmware não oferece.
"""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "main"))

import fan_curve as fc  # noqa: E402
import nitro_core as core  # noqa: E402
import restore_config as restore  # noqa: E402

tmp = tempfile.TemporaryDirectory()
home = Path(tmp.name)

driver = home / "sys" / "nitro_sense"
driver.mkdir(parents=True)

# Valores "vivos" do driver simulado.
DEFAULTS = {
    "backlight_timeout": "1",
    "battery_calibration": "0",
    "battery_limiter": "0",
    "fan_speed": "0,0",
    "usb_charging": "100",
}
CHOSEN = {"fan_speed": "55,60", "battery_limiter": "1"}

thermal = {"mode": "performance", "choices": ["low-power", "balanced", "performance"]}
state = {"driver_present": True, "can_write": True}


def boot_driver():
    """Recria os atributos do driver como a firmware faz no boot."""
    for item in list(driver.iterdir()):
        if item.is_file():
            item.unlink()
    for name, value in DEFAULTS.items():
        (driver / name).write_text(value)
    thermal["mode"] = "low-power"  # a firmware volta ao padrão dela


def driver_values():
    return {p.name: p.read_text().strip() for p in driver.iterdir() if p.is_file()}


def fake_set_thermal_profile(mode):
    if mode not in thermal["choices"]:
        raise ValueError(f"Perfil térmico inválido: {mode}")
    thermal["mode"] = mode


for name, value in (
    ("user_home", lambda: home),
    ("is_root", lambda: False),
    ("can_control", lambda: state["can_write"]),
    ("driver_base", lambda: home / "sys" if state["driver_present"] else None),
    ("sense_dir", lambda: driver),
    ("current_thermal_profile", lambda: thermal["mode"]),
    ("set_thermal_profile", fake_set_thermal_profile),
):
    mock.patch.object(core, name, value).start()


def run_restore(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = restore.main(args)
    return code, out.getvalue(), err.getvalue()


# 1. nada salvo: sai 0, em silêncio
code, out, err = run_restore(["--wait", "0"])
assert (code, out, err) == (0, "", ""), (code, out, err)
print("caso 1 OK: sem configuração salva, sai em silêncio")

# 2. o usuário ajusta o hardware e salva
boot_driver()
for name, value in CHOSEN.items():
    (driver / name).write_text(value)
thermal["mode"] = "performance"
saved, skipped = core.save_config()
assert {"fan_speed", "battery_limiter", "backlight_timeout", "usb_charging",
        "platform_profile"} <= set(saved), saved
assert not skipped, skipped
assert (core.config_dir() / "platform_profile").read_text().strip() == "performance"
print(f"caso 2 OK: save guardou {len(saved)} valores, incluindo o perfil térmico")

# 3. "reboot" + restore devolve tudo
boot_driver()
assert driver_values()["fan_speed"] == "0,0"
code, out, err = run_restore(["--wait", "0", "--verbose"])
assert code == 0, err
values = driver_values()
assert values["fan_speed"] == "55,60", values
assert values["battery_limiter"] == "1", values
assert thermal["mode"] == "performance", thermal
assert "aplicado platform_profile" in out, out
print("caso 3 OK: restore reaplicou os atributos e o perfil térmico após o reboot")

# 4. curva ligada: fan_speed salvo é ignorado (quem aplica é o daemon)
config = fc.load_config()
config.enabled = True
fc.save_config(config)
boot_driver()
code, out, err = run_restore(["--wait", "0", "--verbose"])
assert code == 0, err
values = driver_values()
assert values["fan_speed"] == "0,0", values          # continua o padrão
assert values["battery_limiter"] == "1", values      # o resto reaplicou
assert "pulado fan_speed" in out and "nitroctl-curve" in out, out
print("caso 4 OK: com a curva ligada, o fan_speed salvo é ignorado")

# 5. perfil salvo que o firmware não oferece: pulado, sem derrubar o restore
(core.config_dir() / "platform_profile").write_text("turbo\n")
boot_driver()
code, out, err = run_restore(["--wait", "0", "--verbose"])
assert code == 0, err
assert "pulado platform_profile" in out, out
assert thermal["mode"] == "low-power", thermal
print("caso 5 OK: perfil térmico não oferecido é pulado com aviso")

# 6. driver ausente: erro acionável e código 1
state["driver_present"] = False
code, out, err = run_restore(["--wait", "0"])
assert code == 1 and "indisponível" in err, (code, out, err)
state["driver_present"] = True
print("caso 6 OK: driver ausente -> código 1 com mensagem acionável")

# 7. sem permissão de escrita: mesmo caminho de erro
state["can_write"] = False
code, out, err = run_restore(["--wait", "0"])
assert code == 1 and "indisponível" in err, (code, out, err)
state["can_write"] = True
print("caso 7 OK: sem permissão de escrita -> código 1")

# 8. resumo de uma linha quando há o que aplicar (sem --verbose)
boot_driver()
code, out, err = run_restore(["--wait", "0"])
assert code == 0 and "valores reaplicados" in out, (code, out, err)
print("caso 8 OK: resumo de uma linha no modo padrão")

# 9. --wait inválido (nan/infinito/negativo) não pode travar o login
import threading  # noqa: E402

resultado = {}
state["driver_present"] = False
espera = threading.Thread(
    target=lambda: resultado.update(valor=restore.wait_for_driver(float("nan"))))
espera.start()
espera.join(timeout=5)
assert not espera.is_alive(), "wait_for_driver(nan) travou"
assert resultado.get("valor") is False, resultado
assert restore.wait_for_driver(-3) is False
espera = threading.Thread(
    target=lambda: resultado.update(inf=restore.wait_for_driver(float("inf"))))
espera.start()
espera.join(timeout=5)
assert not espera.is_alive(), "wait_for_driver(inf) travou"
assert resultado.get("inf") is False, resultado
state["driver_present"] = True
print("caso 9 OK: --wait inválido cai em 'sem espera' (não trava)")

# 10. comando não é preferência: battery_calibration fica fora do restore
boot_driver()
(driver / "battery_calibration").write_text("1")  # "ligado" na sessão anterior
saved, _ = core.save_config()
assert "battery_calibration" in saved, saved
boot_driver()                                     # reboot: volta a 0
code, out, err = run_restore(["--wait", "0", "--verbose"])
assert code == 0, err
assert driver_values()["battery_calibration"] == "0", driver_values()
assert "pulado battery_calibration" in out, out
print("caso 10 OK: comando (calibração) não é reaplicado sozinho no login")

# 11. curva com JSON inválido: não derruba nem trava o restore do login
boot_driver()
(driver / "battery_limiter").write_text("1")   # escolha do usuário
core.save_config()                             # guarda com 1
broken = core.config_dir() / "curves" / "fan_curve.json"
broken.parent.mkdir(parents=True, exist_ok=True)
broken.write_text(
    '{"enabled": true, "curves": {'
    '"cpu": {"temps": ["a", 40, 50, 60, 70, 80, 90], "speeds": [20, 30, 40, 55, 70, 85, 100]}, '
    '"gpu": {"temps": [30, 40, 50, 60, 70, 80, 90], "speeds": [20, 30, 40, 55, 70, 85, 100]}}}'
)
assert restore.curve_enabled() is False, "config inválida deveria contar como curva desligada"
boot_driver()
code, out, err = run_restore(["--wait", "0"])
assert code == 0, err
assert driver_values()["battery_limiter"] == "1", driver_values()  # o resto reaplicou
print("caso 11 OK: curva com JSON inválido não derruba o restore")

# 12. dispositivo sem perfil térmico (ACPI ausente): save não inventa "pulado"
with mock.patch.object(core, "PLATFORM_PROFILE", Path(tmp.name) / "sem-platform-profile"):
    saved, skipped = core.save_config()
assert "platform_profile" not in [name for name, _ in skipped], skipped
assert "platform_profile" not in saved, saved
print("caso 12 OK: sem platform_profile no firmware, o save não reporta item pulado")

# 13. instalador: autostart da reconfiguração com Exec absoluto (e --no-autostart)
import subprocess  # noqa: E402

INSTALADOR = RAIZ / "setup" / "install.sh"
snippet = subprocess.run(
    ["sed", "-n", "/^install_restore_autostart()/,/^}/p", str(INSTALADOR)],
    capture_output=True, text=True, check=True).stdout
assert "install_restore_autostart" in snippet, snippet

home_instalador = Path(tmp.name) / "home-instalador"
bin_instalador = home_instalador / ".local" / "bin"


def roda_autostart(no_autostart):
    script = "\n".join([
        "set -u",
        f'HOME="{home_instalador}"',
        f'BIN_DIR="{bin_instalador}"',
        f"OPT_NO_AUTOSTART={no_autostart}",
        "OPT_DRY_RUN=0",
        f'SCRIPT_SELF_DIR="{RAIZ / "setup"}"',
        "log() { :; }",
        snippet,
        "install_restore_autostart",
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


result = roda_autostart(0)
assert result.returncode == 0, result.stderr
desktop = home_instalador / ".config" / "autostart" / "nitroctl-restore.desktop"
assert desktop.is_file(), "autostart da reconfiguração não foi escrito"
conteudo = desktop.read_text()
assert f"Exec={bin_instalador}/nitroctl-restore" in conteudo, conteudo
desktop.unlink()
roda_autostart(1)
assert not desktop.exists(), "--no-autostart deveria pular o autostart"
print("caso 13 OK: autostart aponta para o binário do usuário; --no-autostart respeitado")

print("RESTORE OK: configuração salva volta depois do reboot")
tmp.cleanup()
