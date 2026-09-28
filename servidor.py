"""
Larinha Teleporter — servidor mínimo
====================================

Fork simplificado e em pt-BR do LocWarp (MIT, © keezxc1223).

Faz uma única coisa: mudar a localização simulada (GPS) de um iPhone.

- USB: com o cabo plugado (via usbmuxd / Apple Devices ou iTunes no Windows).
- Wi-Fi: sem cabo, se o iPhone já foi pareado na rede uma vez.
- iOS 17+ (usa DvtProvider + LocationSimulation da pymobiledevice3).
- Sem Electron, sem nuvem, sem senha: FastAPI + uma página web.

Rodar:  python servidor.py   (ou use o iniciar.bat)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.usbmux import list_devices

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

API_HOST = "127.0.0.1"   # troque para "0.0.0.0" se quiser controlar de outro aparelho
API_PORT = 8777
WEB_DIR = Path(__file__).resolve().parent / "web"
PASTA_ESTADO = Path.home() / ".larinha-teleporter"
ARQUIVO_ESTADO = PASTA_ESTADO / "estado.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("larinha-teleporter")

app = FastAPI(title="Larinha Teleporter", version="1.0.0")


# ---------------------------------------------------------------------------
# Estado da conexão (um iPhone por vez)
# ---------------------------------------------------------------------------

class Conexao:
    def __init__(self) -> None:
        self.udid: str | None = None
        self.nome: str = ""
        self.ios: str = ""
        self.transporte: str = ""       # "USB" ou "Wi-Fi"
        self.tunel: PreferredRsdTunnel | None = None
        self.rsd = None                 # RemoteServiceDiscoveryService
        self.dvt: DvtProvider | None = None
        self.sim: LocationSimulation | None = None
        self.lat: float | None = None
        self.lng: float | None = None

    @property
    def conectado(self) -> bool:
        return self.dvt is not None


conexao = Conexao()
trava = asyncio.Lock()  # serializa as operações (o canal DVT é único)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _parse_versao(texto: str) -> tuple[int, ...]:
    try:
        return tuple(int(parte) for parte in texto.split("."))
    except (ValueError, AttributeError):
        return (0, 0)


def _ler_estado() -> dict:
    with suppress(Exception):
        return json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))
    return {}


def _salvar_estado(udid: str | None = None) -> None:
    estado = _ler_estado()
    if udid:
        estado["ultimo_udid"] = udid
    with suppress(Exception):
        PASTA_ESTADO.mkdir(parents=True, exist_ok=True)
        ARQUIVO_ESTADO.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def _ultimo_udid() -> str | None:
    return _ler_estado().get("ultimo_udid")


def _mensagem_erro(exc: Exception) -> str:
    """Converte exceções comuns em mensagens curtas e acionáveis em pt-BR."""
    nome = type(exc).__name__
    texto = str(exc)

    if nome in ("NotPairedError", "MuxException", "ConnectionFailedToUsbmuxdError",
                "ConnectionFailedError", "NoDeviceConnectedError", "DeviceNotFoundError"):
        return ('iPhone não encontrado (ou não confiado). Plugue o cabo, desbloqueie a tela '
                'e toque em "Confiar neste computador".')

    if nome == "UserspaceTunnelUnavailableError" or "RemotePairing" in texto:
        return ("Túnel do Wi-Fi recusado. Confira: 1) iPhone e PC na mesma rede; "
                "2) pareamento Wi-Fi feito (com o cabo uma vez: "
                "python -m pymobiledevice3 lockdown wifi-connections on); "
                "3) Developer Mode ligado.")

    if nome in ("InvalidServiceError", "DeveloperModeIsDisabledError"):
        return ("Serviço de desenvolvedor indisponível. Ligue o Modo Desenvolvedor em "
                "Ajustes → Privacidade e Segurança → Modo Desenvolvedor e reconecte.")

    if nome == "DeviceHasPasscodeSetError":
        return "Desbloqueie a tela do iPhone e tente de novo."

    return f"Falha ({nome}): {texto}"


def _status_dict() -> dict:
    return {
        "conectado": conexao.conectado,
        "dispositivo": (
            {
                "udid": conexao.udid,
                "nome": conexao.nome,
                "ios": conexao.ios,
                "transporte": conexao.transporte,
            }
            if conexao.udid else None
        ),
        "simulado": (
            {"lat": conexao.lat, "lng": conexao.lng}
            if conexao.lat is not None else None
        ),
    }


def _exigir_conectado() -> None:
    if not conexao.conectado:
        raise HTTPException(409, "Nenhum iPhone conectado. Clique em Conectar primeiro.")


# ---------------------------------------------------------------------------
# Conexão / desconexão
# ---------------------------------------------------------------------------

async def _fechar_tudo() -> None:
    """Fecha DVT e túnel, mantendo os dados do último aparelho no estado."""
    dvt, tunel = conexao.dvt, conexao.tunel
    conexao.dvt = conexao.tunel = conexao.rsd = conexao.sim = None

    if dvt is not None:
        with suppress(Exception):
            await dvt.__aexit__(None, None, None)
    if tunel is not None:
        with suppress(Exception):
            await tunel.aclose()


async def _abrir_canal(udid: str) -> None:
    """Abre túnel userspace (sem admin) + sessão DVT para o aparelho."""
    tunel = PreferredRsdTunnel(serial=udid)
    try:
        rsd = await tunel.aopen()
        dvt = DvtProvider(rsd)
        await dvt.__aenter__()
    except Exception:
        with suppress(Exception):
            await tunel.aclose()
        raise
    conexao.tunel, conexao.rsd, conexao.dvt = tunel, rsd, dvt


async def _identificar(udid: str | None) -> tuple[str | None, str, str]:
    """Lê nome e versão do iOS via lockdown (USB ou Wi-Fi pareado)."""
    lockdown = await create_using_usbmux(serial=udid, autopair=True)
    try:
        valores = lockdown.all_values
        return (
            lockdown.udid,
            valores.get("DeviceName") or "iPhone",
            valores.get("ProductVersion") or "?",
        )
    finally:
        with suppress(Exception):
            await lockdown.close()


async def conectar(modo: str, udid: str | None = None) -> dict:
    if modo not in ("usb", "wifi"):
        raise HTTPException(400, "modo deve ser 'usb' ou 'wifi'")

    async with trava:
        await _fechar_tudo()

        if modo == "wifi":
            udid = udid or _ultimo_udid()
            if not udid:
                raise HTTPException(
                    400, "Conecte uma vez pelo USB antes de usar o Wi-Fi.")

        # Identificação (para USB, também confirma que o aparelho está visível).
        try:
            udid, nome, ios = await _identificar(udid)
        except Exception as exc:
            if modo == "usb":
                raise HTTPException(404, _mensagem_erro(exc))
            log.warning("Sem identificação via usbmux (%s); seguindo direto para o túnel.",
                        type(exc).__name__)
            nome, ios = "iPhone", "?"

        if udid is None:
            raise HTTPException(404, "Nenhum iPhone encontrado.")

        if ios != "?" and _parse_versao(ios) < (17, 0):
            raise HTTPException(400, f"iOS {ios} não é suportado (precisa 17+).")

        transporte = "USB" if modo == "usb" else "Wi-Fi"
        try:
            await _abrir_canal(udid)
        except Exception as exc:
            log.error("Falha ao abrir canal: %s", exc)
            raise HTTPException(502, _mensagem_erro(exc))

        conexao.udid, conexao.nome, conexao.ios, conexao.transporte = (
            udid, nome, ios, transporte)
        _salvar_estado(udid)
        log.info("Conectado: %s (iOS %s) via %s", nome, ios, transporte)
        return _status_dict()


async def desconectar() -> dict:
    async with trava:
        if conexao.sim is not None:
            with suppress(Exception):
                await conexao.sim.clear()
        await _fechar_tudo()
        conexao.lat = conexao.lng = None
        log.info("Desconectado.")
        return _status_dict()


# ---------------------------------------------------------------------------
# Localização
# ---------------------------------------------------------------------------

async def _garantir_sim() -> LocationSimulation:
    if conexao.sim is None:
        sim = LocationSimulation(conexao.dvt)
        await sim.connect()
        conexao.sim = sim
    return conexao.sim


async def _reconectar() -> None:
    """Reabre o canal inteiro após uma queda (cabo, tela bloqueada, etc.)."""
    udid = conexao.udid
    if not udid:
        raise HTTPException(409, "Sem aparelho para reconectar.")
    await _fechar_tudo()
    await _abrir_canal(udid)


async def teleportar(lat: float, lng: float) -> dict:
    async with trava:
        _exigir_conectado()
        try:
            sim = await _garantir_sim()
            await sim.set(lat, lng)
        except Exception as exc:
            log.warning("Canal caiu no teleporte (%s); reconectando...", type(exc).__name__)
            try:
                await _reconectar()
                sim = await _garantir_sim()
                await sim.set(lat, lng)
            except HTTPException:
                raise
            except Exception as exc2:
                await _fechar_tudo()
                raise HTTPException(502, _mensagem_erro(exc2))
        conexao.lat, conexao.lng = lat, lng
        log.info("Localização simulada: %.6f, %.6f", lat, lng)
        return _status_dict()


async def limpar() -> dict:
    async with trava:
        _exigir_conectado()
        if conexao.sim is None:
            conexao.lat = conexao.lng = None
            return _status_dict()
        try:
            await conexao.sim.clear()
        except Exception:
            try:
                await _reconectar()
                await _garantir_sim()
                await conexao.sim.clear()
            except HTTPException:
                raise
            except Exception as exc:
                await _fechar_tudo()
                raise HTTPException(502, _mensagem_erro(exc))
        conexao.lat = conexao.lng = None
        log.info("Voltou ao GPS real.")
        return _status_dict()


# ---------------------------------------------------------------------------
# API HTTP
# ---------------------------------------------------------------------------

class PedidoConectar(BaseModel):
    modo: str = "usb"
    udid: str | None = None


class PedidoLocal(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


@app.get("/")
async def pagina() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/status")
async def rota_status() -> dict:
    return _status_dict()


@app.get("/dispositivos")
async def rota_dispositivos() -> dict:
    try:
        brutos = await list_devices()
    except Exception as exc:
        return {"dispositivos": [], "erro": _mensagem_erro(exc)}

    saida = []
    for bruto in brutos:
        item = {
            "udid": bruto.serial,
            "transporte": getattr(bruto, "connection_type", "USB"),
            "nome": "iPhone",
            "ios": "?",
            "conectado": bruto.serial == conexao.udid,
        }
        with suppress(Exception):
            udid, nome, ios = await _identificar(bruto.serial)
            item.update({"udid": udid or bruto.serial, "nome": nome, "ios": ios})
        saida.append(item)
    return {"dispositivos": saida}


@app.post("/conectar")
async def rota_conectar(pedido: PedidoConectar) -> dict:
    return await conectar(pedido.modo, pedido.udid)


@app.post("/desconectar")
async def rota_desconectar() -> dict:
    return await desconectar()


@app.post("/teleportar")
async def rota_teleportar(pedido: PedidoLocal) -> dict:
    return await teleportar(pedido.lat, pedido.lng)


@app.post("/limpar")
async def rota_limpar() -> dict:
    return await limpar()


if __name__ == "__main__":
    log.info("Larinha Teleporter em http://%s:%s", API_HOST, API_PORT)
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="warning")
