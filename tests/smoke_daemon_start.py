"""Verifica start_curve_daemon (instalador) sem depender de processos reais.

Extrai a função do install.sh, aponta BIN_DIR para um diretório temporário com
um launcher falso e troca o pgrep por um falso controlado por arquivo-flags:
  * o launcher é executado quando o pgrep falso não encontra daemon;
  * é ignorado quando o pgrep falso encontra um.

O pgrep real deixaria o teste refém da máquina — um daemon verdadeiro da curva
rodando na sessão faria o caso 1 falhar ("launcher não foi executado").
"""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "setup" / "install.sh"

tmp = Path(tempfile.mkdtemp(prefix="nitro-daemon-"))
try:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    fakebin = tmp / "fakebin"
    fakebin.mkdir()
    flag = tmp / "daemon-vivo"
    pgrep = fakebin / "pgrep"
    pgrep.write_text(f"""#!/bin/sh
# Falso pgrep: "encontra daemon" enquanto o arquivo-flags existir.
[ -e {flag} ]
""")
    pgrep.chmod(0o755)

    marcador = tmp / "rodou.txt"
    launcher = bin_dir / "nitroctl-curve"
    launcher.write_text(f"#!/bin/sh\nprintf 'iniciado\\n' >> {marcador}\n")
    launcher.chmod(0o755)

    script = tmp / "teste.sh"
    script.write_text(f"""#!/bin/bash
set -u
export PATH={fakebin}:$PATH
BIN_DIR={bin_dir}
log() {{ :; }}
snippet="$(sed -n '/^start_curve_daemon()/,/^}}/p' "{INSTALADOR}")"
eval "$snippet"
start_curve_daemon
""")
    script.chmod(0o755)

    result = subprocess.run([str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for _ in range(20):
        if marcador.exists():
            break
        time.sleep(0.1)
    assert marcador.exists(), "o launcher não foi executado"
    assert marcador.read_text().count("iniciado") == 1, marcador.read_text()
    print("caso 1 OK: daemon é iniciado quando não há nenhum rodando")

    # agora o pgrep falso "encontra" um daemon: precisa ser ignorado
    flag.write_text("")
    before = marcador.read_text().count("iniciado")
    result = subprocess.run([str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    time.sleep(0.3)
    assert marcador.read_text().count("iniciado") == before, "não deveria iniciar de novo"
    print("caso 2 OK: não sobe um segundo daemon")

    print("DAEMON-START OK: lógica de subir o daemon validada")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
