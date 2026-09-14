#!/bin/sh
# Abre a interface gráfica nativa (GTK4) do nitroctl.
#
# A escrita no sysfs exige root. O próprio app (main/gtk_app.py) decide:
# se o hardware já é gravável (dono dos nós via regra udev
# setup/99-nitroctl.rules), abre direto como usuário; senão se reexecuta
# via pkexec, pedindo senha. Este launcher só repassa os argumentos.
#
# Sem sessão gráfica (sem DISPLAY nem WAYLAND_DISPLAY) o pkexec não tem como
# pedir a senha: cai para sudo -E no terminal. Se sudo também não existir,
# pkexec vira a última tentativa e o erro do sistema é mostrado.
set -u

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
APP="$SCRIPT_DIR/main/gtk_app.py"

if [ ! -f "$APP" ]; then
    printf 'nitroctl-gui: %s não encontrado.\n' "$APP" >&2
    exit 1
fi

elevate_terminal() {
    if command -v sudo >/dev/null 2>&1; then
        exec sudo -E python3 "$APP" "$@"
    fi
    if command -v pkexec >/dev/null 2>&1; then
        exec pkexec env "DISPLAY=${DISPLAY:-}" "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}" python3 "$APP" --no-elevate "$@"
    fi
    printf 'nitroctl-gui: sem sessão gráfica e sem sudo/pkexec; rode como root.\n' >&2
    exit 1
}

if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    elevate_terminal "$@"
fi

exec python3 "$APP" "$@"
