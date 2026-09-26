"""
Teste rapido de linha de comando (sem interface web).

Exemplos:
    python testar_conexao.py                          # USB, vai para Sao Paulo e volta
    python testar_conexao.py --modo wifi              # usa o ultimo iPhone pareado
    python testar_conexao.py --lat 48.8584 --lng 2.2945 --manter

" --manter" deixa a simulacao ativa ao final (para conferir no app Maps).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from servidor import conectar, desconectar, limpar, teleportar


async def principal() -> int:
    parser = argparse.ArgumentParser(description="Testa conexao + teleporte no iPhone.")
    parser.add_argument("--modo", choices=["usb", "wifi"], default="usb")
    parser.add_argument("--udid", default=None)
    parser.add_argument("--lat", type=float, default=-23.5505)
    parser.add_argument("--lng", type=float, default=-46.6333)
    parser.add_argument("--manter", action="store_true",
                        help="nao voltar ao GPS real ao final")
    args = parser.parse_args()

    try:
        print("1/3 Conectando...")
        estado = await conectar(args.modo, args.udid)
        print("    Conectado:", estado["dispositivo"])

        print(f"2/3 Teleportando para {args.lat}, {args.lng}...")
        await teleportar(args.lat, args.lng)
        print("    OK! Confira o app Maps no iPhone.")

        if not args.manter:
            await asyncio.sleep(4)
            print("3/3 Voltando ao GPS real...")
            await limpar()
        await desconectar()
    except Exception as exc:  # noqa: BLE001 - mensagem amigavel no terminal
        detalhe = getattr(exc, "detail", None) or str(exc)
        print("Falhou:", detalhe, file=sys.stderr)
        return 1

    print("Feito.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
