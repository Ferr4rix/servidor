"""Compila Instalar.exe e Jogar.exe (Windows) a partir de launcher/main.go.

Precisa do Go (https://go.dev/dl/), em qualquer sistema (compila para Windows
de qualquer lado):  python compilar_launchers.py
Os .exe vão para a pasta do cliente (jogoV0.6/cliente, ao lado do main.py).
"""
import os
import shutil
import subprocess
import sys

from caminhos import CLIENTE, TOOLS

SRC = TOOLS / "launcher"


def main():
    go = shutil.which("go") or "/usr/local/go/bin/go"
    env = dict(os.environ, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0")
    env.setdefault("GOCACHE", str(SRC / ".gocache"))
    for name, flags in [("Instalar.exe", "-X main.mode=instalar"),
                        ("Jogar.exe", "-X main.mode=jogar -H=windowsgui")]:   # sem janela preta
        out = CLIENTE / name
        subprocess.run([go, "build", "-trimpath", "-ldflags", f"-s -w {flags}", "-o", str(out), "."],
                       cwd=SRC, env=env, check=True)
        print(f"{name}: {out.stat().st_size // 1024} KB")
    shutil.rmtree(SRC / ".gocache", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
