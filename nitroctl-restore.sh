#!/bin/sh
# Reaplica a configuração salva do nitroctl no início da sessão.
#
# Instalado pelo setup como ~/.local/bin/nitroctl-restore e iniciado pela
# entrada de autostart (~/.config/autostart/nitroctl-restore.desktop): os
# atributos do driver voltam aos padrões da firmware a cada boot, então o
# que foi salvo precisa ser reaplicado depois que o módulo carrega.
#
# Não precisa de root: a regra udev dá ao usuário a posse dos nós do driver.
# Argumentos úteis: --wait SEGUNDOS, --verbose.
set -u

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
APP="$SCRIPT_DIR/main/restore_config.py"

if [ ! -f "$APP" ]; then
    printf 'nitroctl-restore: %s não encontrado.\n' "$APP" >&2
    exit 1
fi

exec python3 "$APP" "$@"
