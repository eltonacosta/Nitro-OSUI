"""Verifica a escolha de toolchain do setup/dkms.conf, kernel por kernel.

O MAKE fixava LLVM=1 e o rebuild automático quebrava assim que um kernel GCC
aparecia — o clang não entende -mindirect-branch=..., caso do
linux-cachyos-lts, e o DKMS registrava 'install ... exited 10'. O teste
fonteia o conf como o próprio DKMS faz (bash, com kernel_source_dir,
kernel_config, kernelver e dkms_tree definidos) e confere o MAKE[0]
resultante nos cenários reais. Nada é compilado nem instalado.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONF = RAIZ / "setup" / "dkms.conf"
INSTALADOR = RAIZ / "setup" / "install.sh"

tmp = Path(tempfile.mkdtemp(prefix="nitro-dkms-"))
try:
    def kernel_tree(config=None, autoconf=None):
        """Árvore falsa de headers: .config e/ou include/config/auto.conf."""
        tree = tmp / f"kernel-{len(list(tmp.iterdir()))}"
        tree.mkdir()
        if config is not None:
            (tree / ".config").write_text(f"{config}\n")
        if autoconf is not None:
            cfg_dir = tree / "include" / "config"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "auto.conf").write_text(f"{autoconf}\n")
        return tree

    def source_conf(tree, kernel_config=None, kernelver="1.0-test"):
        """Fonteia o dkms.conf num shell como o DKMS; devolve o resultado."""
        lines = [
            "set -u",  # estrito: variável ausente no conf tem de ter default
            "dkms_tree=/var/lib/dkms",
            "PACKAGE_NAME=linuwu_sense",
            "PACKAGE_VERSION=1.0",
            f'kernel_source_dir="{tree}"',
        ]
        if kernel_config is not None:
            lines.append(f'kernel_config="{kernel_config}"')
        if kernelver is not None:
            lines.append(f'kernelver="{kernelver}"')
        lines.append(f'. "{CONF}"')
        lines.append('printf "%s" "${MAKE[0]}"')
        return subprocess.run(["bash", "-c", "\n".join(lines)],
                              capture_output=True, text=True)

    def check(label, tree, kernel_config, expect_llvm, kernelver="1.0-test"):
        result = source_conf(tree, kernel_config, kernelver)
        assert result.returncode == 0, (label, result.stderr)
        command = result.stdout
        assert "modules" in command and "linuwu_sense/1.0/build" in command, (label, command)
        has_llvm = "LLVM=1 modules" in command
        assert has_llvm == expect_llvm, (label, expect_llvm, command)
        print(f"OK: {label} -> {'LLVM=1' if has_llvm else 'make padrão (GCC)'}")

    # 1. kernel Clang com .config (CachyOS padrão / Arch): precisa de LLVM=1
    clang_tree = kernel_tree(config="CONFIG_CC_IS_CLANG=y")
    check("Clang via kernel_config (.config)", clang_tree,
          clang_tree / ".config", expect_llvm=True)

    # 2. kernel Clang que só traz include/config/auto.conf (estilo Debian):
    #    o DKMS aponta kernel_config para o auto.conf
    clang_auto_tree = kernel_tree(autoconf="CONFIG_CC_IS_CLANG=y")
    check("Clang via kernel_config (auto.conf)", clang_auto_tree,
          clang_auto_tree / "include" / "config" / "auto.conf", expect_llvm=True)

    # 3. kernel GCC (linux-cachyos-lts, distros em geral): sem LLVM=1 — era
    #    exatamente aqui que o MAKE fixo derrubava o rebuild
    gcc_tree = kernel_tree(config="CONFIG_CC_IS_GCC=y")
    check("GCC via kernel_config (.config)", gcc_tree,
          gcc_tree / ".config", expect_llvm=False)

    # 4. DKMS sem a variável kernel_config (versões antigas): cai nos caminhos
    #    da própria árvore do kernel
    check("Clang sem kernel_config, só auto.conf", clang_auto_tree,
          kernel_config=None, expect_llvm=True)
    check("GCC sem kernel_config, só .config", gcc_tree,
          kernel_config=None, expect_llvm=False)

    # 5. nenhum arquivo de configuração no kernel: make padrão, sem quebrar
    check("sem config no kernel (make padrão)", kernel_tree(),
          tmp / "nao-existe", expect_llvm=False)

    # 6. kernelver ausente (DKMS antigo fonteando o conf): não pode quebrar
    check("sem kernelver", gcc_tree, gcc_tree / ".config", kernelver=None,
          expect_llvm=False)

    # 7. CLEAN continua sem LLVM (limpar não compila) e no mesmo diretório
    limpo = subprocess.run(
        ["bash", "-c",
         f'set -u\ndkms_tree=/var/lib/dkms\nPACKAGE_NAME=linuwu_sense\n'
         f'PACKAGE_VERSION=1.0\nkernel_source_dir="{gcc_tree}"\n'
         f'. "{CONF}"\nprintf "%s" "$CLEAN"'],
        capture_output=True, text=True)
    assert limpo.returncode == 0, limpo.stderr
    assert "LLVM" not in limpo.stdout, limpo.stdout
    assert "/linuwu_sense/1.0/build clean" in limpo.stdout, limpo.stdout
    print("OK: CLEAN sem LLVM e no mesmo diretório de build")

    # 8. sintaxe do conf e do instalador
    for script in (CONF, INSTALADOR):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, (script, result.stderr)
    print("OK: dkms.conf e install.sh sem erro de sintaxe")

    # 8b. resolve_headers_pkg: o pacote de headers segue o dono do kernel em
    #     execução (pacman -Qoq), e não um padrão de sufixo — era assim que
    #     o linux-cachyos-lts acabava com linux-lts-headers, do kernel errado
    fakebin = tmp / "fakebin"
    fakebin.mkdir()
    uname = fakebin / "uname"
    uname.write_text("#!/bin/sh\necho \"$FAKE_KVER\"\n")
    uname.chmod(0o755)
    pacman = fakebin / "pacman"
    pacman.write_text("#!/bin/sh\n[ -e \"$FAKE_PACMAN_OK\" ] && echo linux-cachyos-lts\nexit 0\n")
    pacman.chmod(0o755)
    flag_ok = tmp / "pacman-ok"
    flag_no = tmp / "pacman-nao"
    snippet = subprocess.run(
        ["sed", "-n", "/^resolve_headers_pkg()/,/^}/p", str(INSTALADOR)],
        capture_output=True, text=True, check=True).stdout
    assert "resolve_headers_pkg" in snippet

    def headers_pkg(kver, pacman_ok):
        flag = flag_ok if pacman_ok else flag_no
        if pacman_ok:
            flag_ok.write_text("")
        script = "\n".join([
            "set -u",
            f'export PATH="{fakebin}:$PATH"',
            f'export FAKE_KVER="{kver}"',
            f'export FAKE_PACMAN_OK="{flag}"',
            'P_HEADERS="auto:pacman"',
            snippet,
            'printf "%s" "$(resolve_headers_pkg)"',
        ])
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert headers_pkg("9.9.9-teste", True) == "linux-cachyos-lts-headers", "pacman -Qoq ignorado"
    assert headers_pkg("9.9.9-teste", False) == "linux-headers", "fallback genérico"
    assert headers_pkg("9.9.9-cachyos-lts1", False) == "linux-cachyos-lts-headers", "flavour LTS do CachyOS"
    assert headers_pkg("9.9.9-lts", False) == "linux-lts-headers", "kernel LTS padrão"
    print("OK: headers seguem o dono do kernel (pacman -Qoq) e os flavours corretos")

    # 9. o instalador publica o MESMO conf como override do DKMS em
    #    /etc/dkms/<módulo>-<versão>.conf (lido depois do dkms.conf da
    #    árvore, e depois de um /etc/dkms/<módulo>.conf antigo): um override
    #    manual antigo não pode shadowar a correção. Um dkms de mentira no
    #    PATH mantém o teste hermético (não depende do dkms da máquina).
    dkms_fake = fakebin / "dkms"
    dkms_fake.write_text("#!/bin/sh\nexit 0\n")
    dkms_fake.chmod(0o755)
    texto = INSTALADOR.read_text()
    assert ('cp "$SCRIPT_SELF_DIR/dkms.conf" "/etc/dkms/${DKMS_NAME}-${DKMS_VERSION}.conf"'
            in texto), "o instalador não copia o dkms.conf para /etc/dkms/<módulo>-<versão>.conf"
    result = subprocess.run(
        [str(INSTALADOR), "--driver-only", "--dry-run", "--distro=cachyos"],
        capture_output=True, text=True,
        env=dict(os.environ, NITROCTL_UI="plain", NITROCTL_YES="1",
                 PATH=f"{fakebin}:{os.environ.get('PATH', '')}"))
    saida = result.stdout + result.stderr
    assert result.returncode == 0, saida
    assert "/etc/dkms/linuwu_sense-1.0.conf" in saida, saida
    print("OK: instalador publica /etc/dkms/linuwu_sense-1.0.conf (override versionado)")

    print("DKMS-CONF OK: toolchain acompanha o kernel alvo (Clang -> LLVM=1, GCC -> padrão)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
