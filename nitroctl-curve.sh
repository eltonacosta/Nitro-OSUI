#!/bin/sh
# Roda o daemon da curva de ventoinha do nitroctl.
#
# Instalado pelo setup como ~/.local/bin/nitroctl-curve e iniciado
# automaticamente pela entrada de autostart (~/.config/autostart/
# nitroctl-curve.desktop), para a curva continuar valendo com a GUI fechada.
#
# Não precisa de root: a regra udev dá ao usuário a posse dos nós do driver.
# Argumentos úteis: --status, --once, --restore-auto, --verbose.
set -u

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
APP="$SCRIPT_DIR/main/curve_daemon.py"

if [ ! -f "$APP" ]; then
    printf 'nitroctl-curve: %s não encontrado.\n' "$APP" >&2
    exit 1
fi

exec python3 "$APP" "$@"