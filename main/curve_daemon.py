#!/usr/bin/env python3
"""Daemon da curva de ventoinha do nitroctl.

Roda em segundo plano (autostart da sessão) e aplica a curva configurada, para
que ela continue valendo com a GUI fechada. A cada tick:

  * relê a configuração se o arquivo mudou (edição na GUI vale na hora);
  * com a curva ligada, lê as temperaturas e escreve `fan_speed`;
  * com a curva desligada, devolve o controle ao firmware (0,0) uma única vez;
  * publica o estado em ~/.cache/nitroctl/curve.state.json para a GUI exibir.

Um flock garante um único aplicador por vez: se a GUI estiver escrevendo, o
daemon sai com aviso em vez de brigar pelo mesmo arquivo do driver.

Uso:
    nitroctl-curve                 # fica rodando até SIGTERM/SIGINT
    nitroctl-curve --status        # mostra configuração e estado e sai
    nitroctl-curve --once          # aplica um tick e sai (teste/diagnóstico)
    nitroctl-curve --restore-auto  # devolve o controle ao firmware e sai
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fan_curve  # noqa: E402
import nitro_core as core  # noqa: E402

_running = True


def _stop(_signum, _frame):
    global _running
    _running = False


def _config_mtime() -> float:
    try:
        return fan_curve.config_path().stat().st_mtime
    except OSError:
        return 0.0


def _load_or_default(verbose: bool):
    try:
        return fan_curve.load_config()
    except fan_curve.CurveConfigError as exc:
        print(f"nitroctl-curve: configuração inválida ({exc}); usando padrões", file=sys.stderr)
        return fan_curve.default_config()


def show_status() -> int:
    config = _load_or_default(verbose=False)
    print(f"configuração: {fan_curve.config_path()}")
    print(f"curva habilitada: {'sim' if config.enabled else 'não'}")
    print(f"GPU idle: {config.gpu_idle_speed}% | fail-safe: {config.fail_safe_speed}% | "
          f"histerese: {config.hysteresis_c} °C | intervalo mínimo: {config.min_write_interval_s} s")
    for device in fan_curve.DEVICES:
        levels = " · ".join(f"{temp} °C→{speed}%" for temp, speed in config.points(device))
        print(f"  {device.upper()}: {levels}")

    state = fan_curve.read_state()
    if not state:
        print("estado: nenhum registro (o daemon ainda não rodou)")
        return 0
    age = fan_curve.state_age(state)
    age_text = f"{age:.0f} s atrás" if age is not None else "sem timestamp"
    print(f"estado ({state.get('source', '?')}, {age_text}): {state.get('description') or '—'}")
    print(f"  aplicado: {state.get('applied') or 'automático'}")
    if state.get("error"):
        print(f"  erro: {state['error']}")
    return 0


def apply_once(verbose: bool) -> int:
    config = _load_or_default(verbose=verbose)
    engine = fan_curve.CurveEngine(config)
    error = engine.tick()
    outcome = engine.last_outcome
    fan_curve.write_state(outcome, engine.last_applied, error, source="once")
    if outcome is not None:
        print(outcome.describe())
    if error:
        print(f"nitroctl-curve: {error}", file=sys.stderr)
        return 1
    if engine.last_applied is None:
        print("nada a aplicar (valor já em vigor ou intervalo mínimo não vencido)")
    return 0


def restore_auto() -> int:
    engine = fan_curve.CurveEngine(fan_curve.default_config())
    error = engine.restore_auto()
    fan_curve.write_state(None, None, error, source="restore-auto")
    if error:
        print(f"nitroctl-curve: {error}", file=sys.stderr)
        return 1
    print("ventoinhas devolvidas ao controle automático do firmware (0,0)")
    return 0


def run_loop(tick_seconds: float, verbose: bool) -> int:
    if not core.supports("fan_speed"):
        print("nitroctl-curve: este dispositivo não expõe 'fan_speed'; nada a fazer.", file=sys.stderr)
        return 1
    if not core.can_control():
        print("nitroctl-curve: sem permissão de escrita no driver.\n"
              "Rode './setup/install.sh --driver-only' uma vez para habilitar o acesso.",
              file=sys.stderr)
        return 1

    lock = fan_curve.CurveLock()
    if not lock.acquire():
        print("nitroctl-curve: outro processo já aplica a curva (lock ocupado).", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    config = _load_or_default(verbose)
    engine = fan_curve.CurveEngine(config)
    mtime = _config_mtime()
    applied_something = False
    if verbose:
        print(f"nitroctl-curve: iniciado (curva {'ligada' if config.enabled else 'desligada'})",
              file=sys.stderr)

    try:
        while _running:
            if _config_mtime() != mtime:
                mtime = _config_mtime()
                try:
                    config = fan_curve.load_config()
                    engine.config = config
                    if verbose:
                        print("nitroctl-curve: configuração recarregada", file=sys.stderr)
                except fan_curve.CurveConfigError as exc:
                    print(f"nitroctl-curve: configuração inválida ignorada ({exc})", file=sys.stderr)

            if config.enabled:
                error = engine.tick()
                applied_something = engine.last_applied is not None
                fan_curve.write_state(engine.last_outcome, engine.last_applied, error, source="daemon")
                if verbose and error:
                    print(f"nitroctl-curve: {error}", file=sys.stderr)
            elif applied_something:
                # Transição ligada -> desligada: devolve ao firmware uma vez.
                engine.restore_auto()
                applied_something = False
                fan_curve.write_state(None, None, None, source="daemon")
                if verbose:
                    print("nitroctl-curve: curva desligada; controle devolvido ao firmware",
                          file=sys.stderr)

            # Dorme em fatias curtas para reagir rápido ao SIGTERM.
            remaining = tick_seconds
            while _running and remaining > 0:
                step = min(0.25, remaining)
                time.sleep(step)
                remaining -= step
    finally:
        if applied_something:
            engine.restore_auto()
        lock.release()
        if verbose:
            print("nitroctl-curve: encerrado", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitroctl-curve",
        description="Aplica a curva de ventoinha do nitroctl em segundo plano.",
    )
    parser.add_argument("--status", action="store_true", help="mostra configuração e estado e sai")
    parser.add_argument("--once", action="store_true", help="aplica um tick e sai")
    parser.add_argument("--restore-auto", action="store_true", help="devolve o controle ao firmware e sai")
    parser.add_argument("--tick-seconds", type=float, default=fan_curve.TICK_SECONDS,
                        help=f"período entre verificações (padrão: {fan_curve.TICK_SECONDS} s)")
    parser.add_argument("--verbose", action="store_true", help="log detalhado em stderr")
    args = parser.parse_args(argv)

    if args.status:
        return show_status()
    if args.once:
        return apply_once(args.verbose)
    if args.restore_auto:
        return restore_auto()
    return run_loop(max(0.5, args.tick_seconds), args.verbose)


if __name__ == "__main__":
    sys.exit(main())