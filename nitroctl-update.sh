#!/bin/sh
# Atualiza a instalação local do nitroctl com um único comando.
#
# O que faz:
#   1. descobre a árvore de origem gravada na instalação (~/.local/share/
#      nitroctl/.install-source) e, se ela for um repositório git com remote,
#      roda 'git pull --ff-only' nela;
#   2. roda 'setup/install.sh --update' dessa árvore: sincroniza os arquivos,
#      recria os atalhos, a entrada do menu e o autostart, e corrige o dono
#      dos arquivos de configuração se algum ficou como root.
#
# Não instala dependências, não mexe no driver/DKMS e não pede senha — a menos
# que encontre arquivos de configuração pertencentes a outro usuário, quando
# pede a senha uma vez para corrigir.
#
# Uso:
#   nitroctl-update            # atualiza do jeito padrão
#   NITROCTL_SRC=... nitroctl-update
set -u

SRC="${NITROCTL_SRC:-$HOME/.local/share/nitroctl}"
ORIGIN_FILE="$SRC/.install-source"

warn() { printf 'nitroctl-update: %s\n' "$1" >&2; }

# 1. árvore de origem registrada na instalação
source_tree=""
if [ -r "$ORIGIN_FILE" ]; then
    source_tree="$(cat "$ORIGIN_FILE" 2>/dev/null)"
fi

if [ -n "$source_tree" ] && [ -d "$source_tree/setup" ]; then
    if [ -d "$source_tree/.git" ] && command -v git >/dev/null 2>&1; then
        if ! git -C "$source_tree" pull --ff-only; then
            warn "não deu para atualizar do remote; sincronizando a árvore local como está"
        fi
    fi
    exec "$source_tree/setup/install.sh" --update "$@"
fi

# 2. instalação feita por 'git clone': atualiza no lugar e reaplica os atalhos
if [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
    if ! git -C "$SRC" pull --ff-only; then
        warn "não deu para atualizar do remote em $SRC; segue com o que está aí"
    fi
    exec "$SRC/setup/install.sh" --update "$@"
fi

warn "não encontrei a árvore de origem (procurei em ${source_tree:-$ORIGIN_FILE}).
Clone o repositório e rode ./setup/install.sh uma vez:
    git clone https://github.com/eltonacosta/nitroctl.git ~/nitroctl
    ~/nitroctl/setup/install.sh"
exit 1