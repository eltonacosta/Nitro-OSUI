#!/usr/bin/env python3
"""Camada de acesso ao driver Linuwu-Sense, compartilhada pelo CLI e pela GUI.

Toda leitura e escrita no sysfs fica concentrada aqui para que main.py (CLI) e
gtk_app.py (GTK4) não dupliquem caminhos nem regras de validação.
"""
from __future__ import annotations

import os
import pwd
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

MODULE_NAME = "linuwu_sense"

# O driver expõe os atributos do modelo em um destes dois pontos de montagem,
# a depender de como o módulo foi carregado.
SENSE_BASES = (
    Path(f"/sys/module/{MODULE_NAME}/drivers/platform:acer-wmi/acer-wmi"),
    Path("/sys/devices/platform/acer-wmi"),
)
MODEL_DIRS = ("nitro_sense", "predator_sense")

PLATFORM_PROFILE = Path("/sys/firmware/acpi/platform_profile")
PLATFORM_PROFILE_CHOICES = Path("/sys/firmware/acpi/platform_profile_choices")

PROFILE_LABELS = {
    "balanced-performance": "Performance",
    "performance": "Turbo",
    "low-power": "Eco",
    "balanced": "Balanced",
    "quiet": "Quiet",
}

FAN_AUTO = 0
FAN_MIN = 1
FAN_MAX = 100

# Atributos de liga/desliga expostos pela interface do CLI.
FLAG_ATTRS = ("backlight_timeout", "battery_limiter", "lcd_override")

# Intervalo do monitor de sensores na GUI (ms).
SENSOR_POLL_MS = 800

# O driver publica RPM e temperaturas via hwmon com nome "acer", como
# subdispositivo de /sys/devices/platform/acer-wmi. Os canais seguem os
# IDs do firmware Predator v4 (ver linuwu_sense.c):
#   temp1 = CPU, temp2 = die da GPU, temp3 = termistor da placa.
HWMON_NAME = "acer"
SENSOR_LABELS = (
    ("cpu_temp", "CPU temperature"),
    ("gpu_temp", "GPU temperature"),
    ("board_temp", "Board temperature"),
    ("cpu_fan", "CPU fan"),
    ("gpu_fan", "GPU fan"),
)

_MILLIDEGREE = 1000


@dataclass(frozen=True)
class SensorReading:
    """Uma leitura do hwmon: value None significa sensor ausente/adormecido."""

    key: str
    label: str
    kind: str  # "temp" ou "fan"
    value: Optional[Union[float, int]]
    unit: str

    @property
    def text(self) -> str:
        if self.value is None:
            return "n/a"
        if self.kind == "temp":
            return f"{self.value:.0f} °C"
        return f"{self.value} RPM"


class DriverMissing(OSError):
    """O driver Linuwu-Sense não está instalado ou não expõe a interface."""


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_root() -> bool:
    return os.geteuid() == 0


def _writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def can_control() -> bool:
    """Diz se dá para escrever no hardware sem elevar privilégio.

    Verdadeiro para root ou para membro do grupo nitroctl com a regra
    udev aplicada. A GUI usa isto (não is_root) para decidir se os
    controles nascem habilitados.
    """
    if is_root():
        return True
    try:
        target = sense_dir()
    except DriverMissing:
        return False
    return _writable(target / "fan_speed") or _writable(PLATFORM_PROFILE)


def driver_base() -> Optional[Path]:
    """Diretório do driver que contém uma subpasta de modelo, se houver."""
    if not SENSE_BASES:
        return None
    for base in SENSE_BASES:
        for model in MODEL_DIRS:
            if (base / model).is_dir():
                return base
    return None


def sense_dir() -> Path:
    if not SENSE_BASES:
        raise DriverMissing("Lista de bases do driver vazia; verifique SENSE_BASES.")
    base = driver_base()
    if base is None:
        bases = " nem em ".join(str(base) for base in SENSE_BASES)
        raise DriverMissing(
            f"Driver Linuwu-Sense não encontrado em {bases}. "
            "Instale o driver antes de usar o nitroctl."
        )
    for model in MODEL_DIRS:
        candidate = base / model
        if candidate.is_dir():
            return candidate
    raise DriverMissing("Diretório do modelo não encontrado.")


def model_name() -> str:
    try:
        return sense_dir().name
    except DriverMissing:
        return ""


def supports(attr: str) -> bool:
    """Indica se o modelo atual expõe determinado atributo."""
    try:
        return (sense_dir() / attr).is_file()
    except DriverMissing:
        return False


def features() -> list[str]:
    try:
        return sorted(item.name for item in sense_dir().iterdir() if item.is_file())
    except (DriverMissing, OSError):
        return []


def read_attr(attr: str) -> str:
    path = sense_dir() / attr
    return path.read_text().strip()


def read_attr_or_none(attr: str) -> Optional[str]:
    try:
        return read_attr(attr)
    except (DriverMissing, OSError):
        return None


def write_attr(attr: str, value) -> None:
    """Escreve um valor no sysfs. Propaga OSError/PermissionError para quem chama."""
    (sense_dir() / attr).write_text(f"{value}")


def read_flag(attr: str) -> Optional[bool]:
    """Lê um atributo 0/1. Devolve None quando ausente ou com valor inesperado."""
    raw = read_attr_or_none(attr)
    if raw in ("0", "1"):
        return raw == "1"
    return None


def set_flag(attr: str, enabled: bool) -> None:
    write_attr(attr, 1 if enabled else 0)


def thermal_profiles() -> list[tuple[str, str]]:
    """Pares (valor do kernel, rótulo de exibição) na ordem informada pelo firmware."""
    raw = PLATFORM_PROFILE_CHOICES.read_text().split()
    return [(mode, PROFILE_LABELS.get(mode, mode)) for mode in raw]


def current_thermal_profile() -> Optional[str]:
    try:
        return PLATFORM_PROFILE.read_text().strip()
    except OSError:
        return None


def set_thermal_profile(raw_mode: str) -> None:
    if raw_mode not in [mode for mode, _ in thermal_profiles()]:
        raise ValueError(f"Perfil térmico inválido: {raw_mode}")
    PLATFORM_PROFILE.write_text(f"{raw_mode}\n")


def fan_speed() -> Optional[tuple]:
    """Velocidades atuais (cpu, gpu); 0 significa controle automático."""
    raw = read_attr_or_none("fan_speed")
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def valid_fan_speed(speed) -> bool:
    try:
        speed = int(speed)
    except (TypeError, ValueError):
        return False
    return speed == FAN_AUTO or FAN_MIN <= speed <= FAN_MAX


def set_fan_speed(cpu: int, gpu: int) -> None:
    cpu, gpu = int(cpu), int(gpu)
    if not valid_fan_speed(cpu) or not valid_fan_speed(gpu):
        raise ValueError(f"Velocidade fora da faixa permitida (0 = automático, {FAN_MIN}-{FAN_MAX}).")
    write_attr("fan_speed", f"{cpu},{gpu}")


def hwmon_dir() -> Optional[Path]:
    """Diretório hwmon do driver (nome 'acer'), se o kernel o expôs."""
    for base in (Path("/sys/devices/platform/acer-wmi/hwmon"), Path("/sys/class/hwmon")):
        try:
            candidates = sorted(base.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            try:
                if (candidate / "name").read_text().strip() == HWMON_NAME:
                    return candidate
            except OSError:
                continue
    return None


def _read_hwmon_number(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def sensor_readings() -> list[SensorReading]:
    """RPM das ventoinhas + temperaturas do EC, na ordem de SENSOR_LABELS.

    Leituras ausentes viram value None (ex.: GPU suspensa devolve 0, que
    significa 'sem sensor', não '0 °C' / '0 RPM'). Nunca levanta: sem
    hwmon, devolve lista vazia para a GUI mostrar 'n/a'.
    """
    directory = hwmon_dir()
    if directory is None:
        return []
    raw = {
        "temp1": _read_hwmon_number(directory / "temp1_input"),
        "temp2": _read_hwmon_number(directory / "temp2_input"),
        "temp3": _read_hwmon_number(directory / "temp3_input"),
        "fan1": _read_hwmon_number(directory / "fan1_input"),
        "fan2": _read_hwmon_number(directory / "fan2_input"),
    }
    by_key = {
        "cpu_temp": raw["temp1"],
        "gpu_temp": raw["temp2"],
        "board_temp": raw["temp3"],
        "cpu_fan": raw["fan1"],
        "gpu_fan": raw["fan2"],
    }
    readings = []
    for key, label in SENSOR_LABELS:
        value = by_key[key]
        if value is not None and value <= 0:
            value = None
        if "temp" in key:
            readings.append(SensorReading(key, label, "temp",
                                         value / _MILLIDEGREE if value is not None else None,
                                         "°C"))
        else:
            readings.append(SensorReading(key, label, "fan", value, "RPM"))
    return readings


def user_home() -> Path:
    """Home do usuário real, mesmo quando o programa roda elevado.

    Cobre sudo (SUDO_USER) e pkexec (PKEXEC_UID), que não define SUDO_USER.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user and os.environ.get("PKEXEC_UID"):
        try:
            sudo_user = pwd.getpwuid(int(os.environ["PKEXEC_UID"])).pw_name
        except (KeyError, ValueError):
            sudo_user = None
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            return Path(f"/home/{sudo_user}")
    return Path.home()


def real_user_name() -> str:
    """Nome do usuário real por trás da elevação (SUDO_USER/PKEXEC_UID)."""
    name = os.environ.get("SUDO_USER")
    if not name and os.environ.get("PKEXEC_UID"):
        try:
            name = pwd.getpwuid(int(os.environ["PKEXEC_UID"])).pw_name
        except (KeyError, ValueError):
            name = ""
    if not name:
        # Sem elevação: o "usuário real" é quem está rodando agora mesmo.
        try:
            name = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            name = ""
    return name or ""


def adopt_ownership(target: Path, top: Optional[Path] = None) -> None:
    """Rodando como root, devolve `target` (e os pais até `top`) ao usuário real.

    Instalações antigas criaram ~/.config/nitroctl como root; sem isto, a GUI
    rodando como usuário não consegue mais gravar ali.
    """
    if not is_root():
        return
    name = real_user_name()
    if not name or name == "root":
        return
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return
    targets = [target]
    if top is not None:
        current = target if target.is_dir() else target.parent
        while True:
            targets.append(current)
            if current == top or current.parent == current:
                break
            current = current.parent
    for item in dict.fromkeys(targets):
        try:
            os.chown(item, entry.pw_uid, entry.pw_gid)
        except OSError:
            pass


def config_dir() -> Path:
    return user_home() / ".config" / "nitroctl"


def save_config() -> tuple[list[str], list[tuple[str, str]]]:
    """Copia os atributos atuais para ~/.config/nitroctl.

    Devolve (salvos, pulados), em que pulados são pares (nome, motivo).
    """
    target_dir = config_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(
            f"sem permissão para criar {target_dir} ({exc}). "
            f"Corrija o dono uma vez: sudo chown -R "
            f"{real_user_name() or '$USER'} {config_dir()}"
        ) from exc
    saved: list[str] = []
    skipped: list[tuple[str, str]] = []
    for item in sorted(sense_dir().iterdir()):
        if not item.is_file():
            continue
        try:
            (target_dir / item.name).write_text(item.read_text().strip())
            saved.append(item.name)
        except OSError as exc:
            skipped.append((item.name, str(exc)))
    adopt_ownership(target_dir, config_dir())
    return saved, skipped


def load_config() -> tuple[list[str], list[tuple[str, str]]]:
    """Reaplica em sysfs os valores guardados em ~/.config/nitroctl.

    Devolve (aplicados, pulados), em que pulados são pares (nome, motivo).
    Só reaplica atributos que o modelo atual expõe; arquivos órfãos
    (de outro modelo ou de versão antiga do driver) são pulados em vez
    de criar nós inválidos no sysfs.
    """
    source_dir = config_dir()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Nenhuma configuração salva em {source_dir}")
    try:
        current = sense_dir()
    except DriverMissing:
        raise
    applied: list[str] = []
    skipped: list[tuple[str, str]] = []
    for item in sorted(source_dir.iterdir()):
        if not item.is_file():
            continue
        target = current / item.name
        if not target.is_file():
            skipped.append((item.name, "atributo não exposto por este modelo; ignorado"))
            continue
        try:
            target.write_text(item.read_text().strip())
            applied.append(item.name)
        except OSError as exc:
            skipped.append((item.name, str(exc)))
    return applied, skipped