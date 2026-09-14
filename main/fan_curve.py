#!/usr/bin/env python3
"""Motor da curva de ventoinha do nitroctl, compartilhado por CLI, GUI e daemon.

O driver Linuwu-Sense não tem curva própria: o firmware só oferece controle
automático (0) ou velocidade fixa (1-100) por ventoinha. A curva é, portanto,
aplicada por software: este módulo lê as temperaturas do hwmon e escreve o
valor correspondente em `fan_speed`.

Regras de segurança embutidas:
  * Piso de 20% — nenhum nível e nenhum resultado de interpolação fica abaixo;
  * GPU suspensa (temp2 = 0) usa a velocidade de idle, não a curva;
  * Falha real de leitura (CPU ilegível, ou nenhum sensor) sobe tudo para o
    valor de fail-safe;
  * Escrita só quando o valor muda e com intervalo mínimo — o driver não tem
    rate limiting e cada escrita dispara 1-3 chamadas ACPI no EC.

O arquivo de configuração fica em ~/.config/nitroctl/curves/fan_curve.json
(subpasta de propósito: o save/load de atributos do driver ignora diretórios).
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import nitro_core as core

LEVELS = 7
MIN_SPEED = 20
MAX_SPEED = 100
MIN_TEMP = 20
MAX_TEMP = 110

DEFAULT_TEMPS = (30, 40, 50, 60, 70, 80, 90)
DEFAULT_SPEEDS = (20, 30, 40, 55, 70, 85, 100)

# GPU suspensa (dGPU dormindo) é o estado normal em uso leve: velocidade fixa
# baixa em vez de perseguir uma temperatura que não existe.
DEFAULT_GPU_IDLE_SPEED = 40

# Falha real de leitura: melhor ventilar demais do que não ventilar.
DEFAULT_FAIL_SAFE_SPEED = 75

# Histerese na descida: só reduz a velocidade quando a temperatura caiu ao
# menos isso desde a última mudança, evitando oscilar em cima de um limiar.
DEFAULT_HYSTERESIS_C = 2

# Intervalo mínimo entre escritas, em segundos.
DEFAULT_MIN_WRITE_INTERVAL_S = 5

# Período do tick do daemon e do laço da GUI, em segundos.
TICK_SECONDS = 2

DEVICES = ("cpu", "gpu")


class CurveConfigError(ValueError):
    """Configuração de curva inválida."""


class CurveConfig:
    """Configuração completa da curva (persistida em JSON)."""

    def __init__(
        self,
        enabled: bool = False,
        curves: Optional[dict] = None,
        gpu_idle_speed: int = DEFAULT_GPU_IDLE_SPEED,
        fail_safe_speed: int = DEFAULT_FAIL_SAFE_SPEED,
        hysteresis_c: int = DEFAULT_HYSTERESIS_C,
        min_write_interval_s: int = DEFAULT_MIN_WRITE_INTERVAL_S,
    ):
        # curves: {"cpu": [(temp, speed) x7], "gpu": [(temp, speed) x7]}
        self.enabled = bool(enabled)
        self.curves = curves if curves is not None else default_curves()
        self.gpu_idle_speed = int(gpu_idle_speed)
        self.fail_safe_speed = int(fail_safe_speed)
        self.hysteresis_c = int(hysteresis_c)
        self.min_write_interval_s = int(min_write_interval_s)

    def points(self, device: str) -> list:
        return list(self.curves[device])

    def replace_points(self, device: str, points) -> None:
        self.curves[device] = [(int(t), int(s)) for t, s in points]

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "enabled": self.enabled,
            "gpu_idle_speed": self.gpu_idle_speed,
            "fail_safe_speed": self.fail_safe_speed,
            "hysteresis_c": self.hysteresis_c,
            "min_write_interval_s": self.min_write_interval_s,
            "curves": {
                device: {
                    "temps": [t for t, _ in self.curves[device]],
                    "speeds": [s for _, s in self.curves[device]],
                }
                for device in DEVICES
            },
        }


def default_curves() -> dict:
    return {device: list(zip(DEFAULT_TEMPS, DEFAULT_SPEEDS)) for device in DEVICES}


def default_config() -> CurveConfig:
    return CurveConfig()


def config_path() -> Path:
    return core.config_dir() / "curves" / "fan_curve.json"


def cache_dir() -> Path:
    return core.user_home() / ".cache" / "nitroctl"


def lock_path() -> Path:
    return cache_dir() / "curve.lock"


def state_path() -> Path:
    return cache_dir() / "curve.state.json"


def _speed_range_error(name: str, speeds) -> Optional[str]:
    for speed in speeds:
        if not MIN_SPEED <= int(speed) <= MAX_SPEED:
            return f"{name}: velocidade {speed}% fora da faixa {MIN_SPEED}-{MAX_SPEED}%"
    return None


def validate(config: CurveConfig) -> None:
    """Levanta CurveConfigError se a configuração não puder ser aplicada."""
    for name, value in (("gpu_idle_speed", config.gpu_idle_speed),
                        ("fail_safe_speed", config.fail_safe_speed)):
        if not MIN_SPEED <= value <= MAX_SPEED:
            raise CurveConfigError(
                f"{name}={value} fora da faixa {MIN_SPEED}-{MAX_SPEED}"
            )
    if config.hysteresis_c < 0:
        raise CurveConfigError("hysteresis_c não pode ser negativo")
    if config.min_write_interval_s < 0:
        raise CurveConfigError("min_write_interval_s não pode ser negativo")

    for device in DEVICES:
        points = config.curves.get(device)
        if points is None:
            raise CurveConfigError(f"curva ausente para {device}")
        if len(points) != LEVELS:
            raise CurveConfigError(
                f"{device}: esperados {LEVELS} níveis, encontrados {len(points)}"
            )
        temps = [int(t) for t, _ in points]
        speeds = [int(s) for _, s in points]
        for temp in temps:
            if not MIN_TEMP <= temp <= MAX_TEMP:
                raise CurveConfigError(
                    f"{device}: temperatura {temp} °C fora da faixa {MIN_TEMP}-{MAX_TEMP}"
                )
        for index in range(1, len(temps)):
            if temps[index] <= temps[index - 1]:
                raise CurveConfigError(
                    f"{device}: temperaturas devem ser estritamente crescentes "
                    f"({temps[index - 1]} °C seguido de {temps[index]} °C)"
                )
        error = _speed_range_error(device, speeds)
        if error:
            raise CurveConfigError(error)


def load_config(path: Optional[Path] = None) -> CurveConfig:
    """Lê o JSON; devolve a configuração padrão se o arquivo não existir."""
    target = path or config_path()
    try:
        raw = json.loads(target.read_text())
    except FileNotFoundError:
        return default_config()
    except (OSError, json.JSONDecodeError) as exc:
        raise CurveConfigError(f"não foi possível ler {target}: {exc}")

    curves = {}
    for device in DEVICES:
        entry = (raw.get("curves") or {}).get(device)
        if not isinstance(entry, dict):
            raise CurveConfigError(f"{target}: seção 'curves.{device}' ausente")
        temps = entry.get("temps")
        speeds = entry.get("speeds")
        if not isinstance(temps, list) or not isinstance(speeds, list):
            raise CurveConfigError(f"{target}: 'temps'/'speeds' de {device} devem ser listas")
        if len(temps) != len(speeds):
            raise CurveConfigError(f"{target}: {device} tem {len(temps)} temperaturas e {len(speeds)} velocidades")
        curves[device] = [(int(t), int(s)) for t, s in zip(temps, speeds)]

    config = CurveConfig(
        enabled=bool(raw.get("enabled", False)),
        curves=curves,
        gpu_idle_speed=raw.get("gpu_idle_speed", DEFAULT_GPU_IDLE_SPEED),
        fail_safe_speed=raw.get("fail_safe_speed", DEFAULT_FAIL_SAFE_SPEED),
        hysteresis_c=raw.get("hysteresis_c", DEFAULT_HYSTERESIS_C),
        min_write_interval_s=raw.get("min_write_interval_s", DEFAULT_MIN_WRITE_INTERVAL_S),
    )
    validate(config)
    return config


def save_config(config: CurveConfig, path: Optional[Path] = None) -> None:
    """Valida e grava o JSON, criando a pasta se preciso.

    Erros de permissão viram mensagem acionável: a pasta ~/.config/nitroctl
    pode ter ficado com dono root de uma instalação antiga, e aí a GUI
    rodando como usuário não consegue criar a subpasta curves/.
    """
    validate(config)
    target = path or config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
    except PermissionError as exc:
        owner = core.real_user_name() or "$USER"
        raise CurveConfigError(
            f"sem permissão para gravar {target} ({exc}). "
            f"Corrija o dono uma vez: sudo chown -R {owner} {core.config_dir()}"
        ) from exc
    except OSError as exc:
        raise CurveConfigError(f"não foi possível gravar {target}: {exc}") from exc
    # Rodando elevado, o arquivo nasce do root: devolve ao usuário real para
    # que a próxima execução como usuário consiga gravá-lo.
    core.adopt_ownership(target, core.config_dir())


def speed_for(temp: float, points) -> int:
    """Velocidade para a temperatura, por interpolação linear entre níveis.

    Abaixo do primeiro nível usa a primeira velocidade, acima do último usa a
    última. O resultado é preso em MIN_SPEED-MAX_SPEED (piso de 20% garantido).
    """
    ordered = sorted(points)
    temps = [t for t, _ in ordered]
    speeds = [s for _, s in ordered]

    if temp <= temps[0]:
        value = speeds[0]
    elif temp >= temps[-1]:
        value = speeds[-1]
    else:
        value = speeds[-1]
        for index in range(1, len(temps)):
            if temp <= temps[index]:
                low_t, high_t = temps[index - 1], temps[index]
                low_s, high_s = speeds[index - 1], speeds[index]
                span = high_t - low_t
                if span <= 0:
                    value = high_s
                else:
                    ratio = (temp - low_t) / span
                    value = low_s + ratio * (high_s - low_s)
                break

    return int(round(min(MAX_SPEED, max(MIN_SPEED, value))))


class CurveLock:
    """Trava de escrita exclusiva (flock) — só um aplicador por vez."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or lock_path()
        self._handle = None

    def acquire(self, blocking: bool = False) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "w")
        except OSError:
            return False
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle, flags)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None


class CurveOutcome:
    """Resultado de um tick: o que foi calculado e por quê."""

    def __init__(self, cpu_speed, gpu_speed, cpu_temp, gpu_temp, notes):
        self.cpu_speed = cpu_speed
        self.gpu_speed = gpu_speed
        self.cpu_temp = cpu_temp
        self.gpu_temp = gpu_temp
        self.notes = notes

    @property
    def pair(self):
        return (self.cpu_speed, self.gpu_speed)

    def describe(self) -> str:
        parts = []
        if self.cpu_temp is None:
            parts.append(f"CPU sem leitura → {self.cpu_speed}% (fail-safe)")
        else:
            parts.append(f"CPU {self.cpu_temp:.0f} °C → {self.cpu_speed}%")
        if self.gpu_temp is None:
            label = "fail-safe" if "fail-safe" in self.notes else "idle"
            parts.append(f"GPU sem leitura → {self.gpu_speed}% ({label})")
        else:
            parts.append(f"GPU {self.gpu_temp:.0f} °C → {self.gpu_speed}%")
        return " · ".join(parts)


def compute_outcome(config: CurveConfig, cpu_temp, gpu_temp) -> CurveOutcome:
    """Calcula as velocidades a partir das temperaturas (função pura).

    cpu_temp/gpu_temp valem None quando o sensor não responde.
    """
    notes = []
    if cpu_temp is None:
        # Falha real: sem temperatura de CPU não há curva confiável.
        notes.append("fail-safe")
        return CurveOutcome(config.fail_safe_speed, config.fail_safe_speed,
                            None, gpu_temp, notes)

    cpu_speed = speed_for(cpu_temp, config.points("cpu"))
    if gpu_temp is None:
        # GPU suspensa: estado normal em uso leve, não é falha.
        notes.append("gpu-idle")
        gpu_speed = config.gpu_idle_speed
    else:
        gpu_speed = speed_for(gpu_temp, config.points("gpu"))
    return CurveOutcome(cpu_speed, gpu_speed, cpu_temp, gpu_temp, notes)


class CurveEngine:
    """Aplica a curva: lê sensores, calcula, escreve com dedupe e histerese."""

    def __init__(self, config: CurveConfig, reader: Optional[Callable] = None,
                 applier: Optional[Callable] = None, clock: Optional[Callable] = None):
        self.config = config
        self._reader = reader or core.sensor_readings
        self._applier = applier or core.set_fan_speed
        self._clock = clock or time.monotonic
        self.last_applied = None          # (cpu, gpu) escrito por último
        self.last_write_time = None
        self.last_error = None
        self.last_outcome = None
        self._last_change_temp = {}       # device -> temperatura da última mudança

    def temperatures(self):
        """(cpu_temp, gpu_temp); None quando o sensor não responde."""
        values = {}
        for reading in self._reader():
            values[reading.key] = reading.value
        return values.get("cpu_temp"), values.get("gpu_temp")

    def _filter_hysteresis(self, device: str, temp, speed: int, previous: int) -> int:
        """Segura a descida até a temperatura cair hysteresis_c desde a mudança.

        Subidas passam direto: calor é o caso urgente. Sem leitura (temp None)
        não há histerese a aplicar.
        """
        if temp is None or speed >= previous:
            return speed
        changed_at = self._last_change_temp.get(device)
        if changed_at is not None and temp > changed_at - self.config.hysteresis_c:
            return previous
        return speed

    def tick(self) -> Optional[str]:
        """Um passo da curva. Devolve mensagem de erro, ou None em caso de sucesso."""
        if not core.supports("fan_speed"):
            self.last_error = "este dispositivo não expõe 'fan_speed'"
            return self.last_error

        cpu_temp, gpu_temp = self.temperatures()
        outcome = compute_outcome(self.config, cpu_temp, gpu_temp)

        pair = outcome.pair
        if self.last_applied is not None:
            pair = (
                self._filter_hysteresis("cpu", cpu_temp, pair[0], self.last_applied[0]),
                self._filter_hysteresis("gpu", gpu_temp, pair[1], self.last_applied[1]),
            )
            outcome.cpu_speed, outcome.gpu_speed = pair

        self.last_outcome = outcome
        if pair == self.last_applied:
            self.last_error = None
            return None
        now = self._clock()
        if (self.last_write_time is not None
                and now - self.last_write_time < self.config.min_write_interval_s):
            return None
        try:
            self._applier(*pair)
        except (OSError, ValueError) as exc:
            self.last_error = f"falha ao aplicar a curva: {exc}"
            return self.last_error
        self.last_applied = pair
        # Guarda as temperaturas que justificaram esta mudança: é a referência
        # da histerese para a próxima descida.
        self._last_change_temp["cpu"] = cpu_temp
        self._last_change_temp["gpu"] = gpu_temp
        self.last_write_time = now
        self.last_error = None
        return None

    def restore_auto(self) -> Optional[str]:
        """Devolve o controle ao firmware (0,0)."""
        try:
            self._applier(core.FAN_AUTO, core.FAN_AUTO)
        except (OSError, ValueError) as exc:
            self.last_error = f"falha ao restaurar o automático: {exc}"
            return self.last_error
        self.last_applied = None
        self.last_write_time = None
        self._last_change_temp.clear()
        self.last_error = None
        return None


def write_state(outcome: Optional[CurveOutcome], applied, error: Optional[str],
                source: str) -> None:
    """Estado compartilhado (o daemon escreve, a GUI lê para exibir)."""
    payload = {
        "ts": time.time(),
        "source": source,
        "applied": list(applied) if applied else None,
        "error": error,
        "description": outcome.describe() if outcome is not None else None,
        "notes": outcome.notes if outcome is not None else [],
        "cpu_temp": outcome.cpu_temp if outcome is not None else None,
        "gpu_temp": outcome.gpu_temp if outcome is not None else None,
    }
    target = state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload) + "\n")
        core.adopt_ownership(target, cache_dir())
    except OSError:
        pass


def read_state() -> Optional[dict]:
    try:
        return json.loads(state_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def state_age(state: dict) -> Optional[float]:
    """Segundos desde a última atualização do estado, se houver timestamp."""
    timestamp = state.get("ts") if state else None
    if not timestamp:
        return None
    return max(0.0, time.time() - float(timestamp))


def _demo() -> None:
    """Execução direta: mostra o cálculo para temperaturas de exemplo."""
    config = load_config()
    print(f"config: {config_path()}")
    print(f"habilitada: {config.enabled} | idle GPU: {config.gpu_idle_speed}% | "
          f"fail-safe: {config.fail_safe_speed}%")
    for temp in (25, 45, 55, 75, 95):
        print(f"  {temp} °C -> CPU {speed_for(temp, config.points('cpu'))}% | "
              f"GPU {speed_for(temp, config.points('gpu'))}%")
    print(f"GPU sem leitura -> {compute_outcome(config, 60, None).describe()}")
    print(f"CPU sem leitura -> {compute_outcome(config, None, None).describe()}")


if __name__ == "__main__":
    _demo()