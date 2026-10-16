#!/usr/bin/env python3
"""Reaplica a configuração salva do nitroctl no início da sessão.

Os atributos do driver (bateria, RGB do teclado, ventoinhas…) voltam aos
padrões da firmware a cada boot: o módulo é recarregado e relê o EC. Este
comando devolve, uma vez por login, o que foi guardado com 'Save
configuration' (~/.config/nitroctl) — inclusive o perfil térmico. A curva de
ventoinha continua a cargo do nitroctl-curve.

Com a curva ligada, o fan_speed salvo é ignorado: quem manda nas ventoinhas
é o daemon, e o valor salvo costuma ser só um instantâneo da própria curva.

Uso (instalado como 'nitroctl-restore'; roda no autostart da sessão):
    nitroctl-restore               # espera o driver (até 30 s) e reaplica
    nitroctl-restore --wait 0      # não espera (instalação/diagnóstico)
    nitroctl-restore --verbose     # mostra cada atributo aplicado ou pulado
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fan_curve  # noqa: E402
import nitro_core as core  # noqa: E402

# Tempo padrão de espera pelo driver: em boots rápidos o autostart pode rodar
# antes de o módulo terminar de carregar.
DEFAULT_WAIT_SECONDS = 30
POLL_SECONDS = 2.0

FAN_SPEED_SKIP_REASON = "a curva de ventoinha está ligada; quem aplica é o nitroctl-curve"

# Atributos que são comando (1 = executa agora), não preferência: reaplicá-los
# a cada login dispararia o efeito de novo — a calibração da bateria, por
# exemplo, reiniciaria o ciclo. Ficam fora do restore automático; o 'Load
# configuration' manual continua fiel ao que foi salvo.
COMMAND_ATTRS = ("battery_calibration",)
COMMAND_SKIP_REASON = "comando: reaplicado apenas pelo 'Load configuration' manual"


def curve_enabled() -> bool:
    """Diz se a curva está ligada (config ausente ou inválida conta como não)."""
    try:
        return fan_curve.load_config().enabled
    except (fan_curve.CurveConfigError, OSError, TypeError, ValueError, OverflowError):
        return False


def wait_for_driver(timeout: float) -> bool:
    """Espera o driver aparecer e ficar gravável, no máximo `timeout` segundos."""
    # NaN/infinito/negativo vira 0 (sem espera): um --wait inválido não pode
    # travar o login num laço infinito.
    timeout = timeout if math.isfinite(timeout) and timeout > 0 else 0.0
    deadline = time.monotonic() + timeout
    while True:
        if core.driver_base() is not None and core.can_control():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def restore(wait: float, verbose: bool) -> int:
    source = core.config_dir()
    if not source.is_dir():
        if verbose:
            print(f"nitroctl-restore: nada salvo em {source}; nada a fazer")
        return 0
    if not wait_for_driver(wait):
        print("nitroctl-restore: driver Linuwu-Sense indisponível ou sem "
              "permissão de escrita; nada aplicado. Rode "
              "'./setup/install.sh --driver-only' para recompilar/habilitar.",
              file=sys.stderr)
        return 1
    skip = {name: COMMAND_SKIP_REASON for name in COMMAND_ATTRS}
    if curve_enabled():
        skip["fan_speed"] = FAN_SPEED_SKIP_REASON
    try:
        applied, skipped = core.load_config(skip=skip)
    except (core.DriverMissing, FileNotFoundError) as exc:
        print(f"nitroctl-restore: {exc}", file=sys.stderr)
        return 1
    if verbose:
        for name in applied:
            print(f"nitroctl-restore: aplicado {name}")
        for name, reason in skipped:
            print(f"nitroctl-restore: pulado {name}: {reason}")
    elif applied:
        print(f"nitroctl-restore: {len(applied)} valores reaplicados")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitroctl-restore",
        description="Reaplica a configuração salva do nitroctl (autostart da sessão).",
    )
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS,
                        help=f"segundos de espera pelo driver (padrão: {DEFAULT_WAIT_SECONDS:.0f})")
    parser.add_argument("--verbose", action="store_true",
                        help="mostra cada atributo aplicado ou pulado")
    args = parser.parse_args(argv)
    return restore(args.wait, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
