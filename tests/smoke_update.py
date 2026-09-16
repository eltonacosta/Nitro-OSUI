"""Verifica o fluxo de atualização com um comando (nitroctl-update).

Usa árvores falsas em diretório temporário: nenhuma instalação real é tocada.
Cobre os três caminhos do script:
  1. árvore de origem registrada em .install-source -> roda install.sh --update
  2. instalação por git clone (sem .install-source) -> git pull + install.sh --update
  3. nada encontrado -> erro explicativo
Também confere que o --update do instalador não faz perguntas.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
UPDATE = RAIZ / "nitroctl-update.sh"

tmp = Path(tempfile.mkdtemp(prefix="nitro-update-"))
try:
    # ---- árvore de origem falsa, com install.sh que só registra os argumentos
    source = tmp / "source"
    (source / "setup").mkdir(parents=True)
    registro = tmp / "chamadas.txt"
    (source / "setup" / "install.sh").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + str(registro) + "\nexit 0\n"
    )
    (source / "setup" / "install.sh").chmod(0o755)

    # ---- caso 1: .install-source aponta para a árvore
    src_dir = tmp / "instalado"
    src_dir.mkdir()
    (src_dir / ".install-source").write_text(str(source) + "\n")

    env = dict(os.environ, NITROCTL_SRC=str(src_dir))
    result = subprocess.run([str(UPDATE)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert registro.read_text().strip() == "--update", registro.read_text()
    print("caso 1 OK: sincroniza a partir da árvore registrada (install.sh --update)")

    # ---- caso 2: sem .install-source, mas com .git -> pull + install.sh --update
    registro.unlink()
    clone = tmp / "clone"
    (clone / "setup").mkdir(parents=True)
    (clone / "setup" / "install.sh").write_text(
        "#!/bin/sh\nprintf 'local %s\\n' \"$*\" >> " + str(registro) + "\nexit 0\n"
    )
    (clone / "setup" / "install.sh").chmod(0o755)
    remoto = tmp / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remoto)], check=True)
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)

    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", str(remoto)], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-qu", "origin", "HEAD"], check=True)

    env = dict(os.environ, NITROCTL_SRC=str(clone))
    result = subprocess.run([str(UPDATE)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "local --update" in registro.read_text(), registro.read_text()
    print("caso 2 OK: clone git com remote -> pull e reaplica com --update")

    # ---- caso 2b: clone sem remote -> avisa e ainda reaplica
    sem_remote = tmp / "sem-remote"
    (sem_remote / "setup").mkdir(parents=True)
    (sem_remote / "setup" / "install.sh").write_text(
        "#!/bin/sh\n"
        "printf 'semremote %s\\n' \"$*\" >> " + str(registro) + "\n"
        "exit 0\n"
    )
    (sem_remote / "setup" / "install.sh").chmod(0o755)
    subprocess.run(["git", "init", "-q", str(sem_remote)], check=True)

    env = dict(os.environ, NITROCTL_SRC=str(sem_remote))
    result = subprocess.run([str(UPDATE)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "semremote --update" in registro.read_text(), registro.read_text()
    assert "segue com o que está aí" in result.stderr, result.stderr
    print("caso 2b OK: clone sem remote avisa mas conclui")

    # ---- caso 3: nada encontrado
    vazio = tmp / "vazio"
    vazio.mkdir()
    env = dict(os.environ, NITROCTL_SRC=str(vazio))
    result = subprocess.run([str(UPDATE)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "não encontrei a árvore de origem" in result.stderr, result.stderr
    print("caso 3 OK: sem origem, erro explicativo e código 1")

    # ---- caso 4: --update do instalador real não faz perguntas (stdin fechado)
    instalador = RAIZ / "setup" / "install.sh"
    result = subprocess.run([str(instalador), "--update", "--dry-run"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            env=dict(os.environ, NITROCTL_UI="plain"))
    saida = result.stdout + result.stderr
    assert result.returncode == 0, saida
    assert "Continuar?" not in saida, saida
    assert "Baixar/atualizar" not in saida, saida
    print("caso 4 OK: --update não pergunta nada")

    print("UPDATE OK: fluxo de atualização com um comando validado")
finally:
    shutil.rmtree(tmp, ignore_errors=True)