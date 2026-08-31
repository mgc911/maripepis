"""El demonio que sostiene la sesión de WhatsApp. Y que ahora sí envía.

Aquí cambia el listón respecto a `test_whatsapp.py`. Allí lo que se probaba era
que la herramienta **no** enviase; esto envía de verdad, así que lo que hay que
probar es lo contrario: que cuando dice que ha enviado sea porque ha enviado, y
que en todas las ramas en que se niega **no haya tocado la red**.

Los tests corren sin `neonize` instalado —es una dependencia opcional— así que
la biblioteca se finge entera. No es hacer trampa: lo que se prueba aquí es la
lógica de decidir, que es nuestra; que whatsmeow hable el protocolo se comprobó
en el spike, y eso no se prueba con un test unitario.
"""

from __future__ import annotations

import socket
import sqlite3
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from maripepis.whatsapp import cliente, daemon, protocol
from maripepis.whatsapp.daemon import Demonio, vinculado


# --- El destino ------------------------------------------------------------

@pytest.mark.parametrize(("destino", "esperado"), [
    ("34600112233", ("34600112233", "s.whatsapp.net")),   # una persona
    ("120363012345678901@g.us", ("120363012345678901", "g.us")),  # un grupo
    ("120363-1600000000@g.us", ("120363-1600000000", "g.us")),    # y los antiguos
    # Nueve cifras se aceptan tal cual, y es a propósito: aquí abajo no se sabe de
    # qué país es nadie, y hay países cuyo número entero tiene ocho o nueve. El
    # prefijo lo pone `tools.whatsapp.numero`, que es quien tiene la config. Este
    # módulo solo comprueba la forma, y la barrera de «a quién» es la agenda.
    ("600112233", ("600112233", "s.whatsapp.net")),
    ("+34600112233", ("", "")),       # aquí ya llega normalizado, sin «+»
    ("34600112233@s.whatsapp.net", ("", "")),   # el servidor de personas no se escribe
    ("pepe@g.us", ("", "")),          # un grupo no tiene nombre, tiene número
    ("", ("", "")),
    ("../../etc/passwd", ("", "")),
])
def test_partes_destino(destino, esperado):
    assert protocol.partes_destino(destino) == esperado


def test_un_json_roto_no_revienta():
    assert protocol.decode(b"{esto no es json") == {"accion": "?"}
    assert protocol.decode(b'["una", "lista"]') == {"accion": "?"}


def test_el_socket_sale_del_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv(protocol.SOCKET_ENV, str(tmp_path / "otro.sock"))
    assert protocol.socket_path() == str(tmp_path / "otro.sock")
    assert protocol.socket_path("~/puesto.sock").startswith("/")   # la config manda


# --- La sesión en disco ----------------------------------------------------

def test_sin_fichero_no_hay_sesion(tmp_path):
    assert vinculado(tmp_path / "no-existe.sqlite3") is False


def test_una_base_sin_dispositivos_no_esta_vinculada(tmp_path):
    """El caso de después de `vincular` a medias: la base está, el móvil no."""
    ruta = tmp_path / "session.sqlite3"
    con = sqlite3.connect(ruta)
    con.execute("create table whatsmeow_device (jid text)")
    con.commit()
    con.close()
    assert vinculado(ruta) is False

    con = sqlite3.connect(ruta)
    con.execute("insert into whatsmeow_device values ('34600112233')")
    con.commit()
    con.close()
    assert vinculado(ruta) is True


def test_una_base_que_no_es_de_whatsmeow_no_cuela(tmp_path):
    ruta = tmp_path / "cualquier-cosa.sqlite3"
    sqlite3.connect(ruta).execute("create table otra (x)")
    assert vinculado(ruta) is False


# --- Andamio ---------------------------------------------------------------

class ClienteFalso:
    """Lo justo de `neonize` para que el demonio crea que hay sesión."""

    def __init__(self) -> None:
        self.enviados: list[tuple] = []
        self.revocados: list[str] = []

    def send_message(self, jid, texto):            # noqa: ANN001
        self.enviados.append((jid.User, jid.Server, texto))
        return SimpleNamespace(ID="MSG-1")

    def revoke_message(self, chat, sender, mid):   # noqa: ANN001
        self.revocados.append(mid)
        return SimpleNamespace(ID="REV-1")

    def get_me(self):
        return SimpleNamespace(PushName="Manu",
                               JID=SimpleNamespace(User="34600000000", Server="s.whatsapp.net"))

    def get_joined_groups(self):
        def grupo(nombre, user):
            return SimpleNamespace(GroupName=SimpleNamespace(Name=nombre),
                                   JID=SimpleNamespace(User=user, Server="g.us"))
        return [grupo("Familia", "1"), grupo("Familia política", "2"), grupo("Pádel", "3")]


@pytest.fixture(autouse=True)
def neonize_falso(monkeypatch):
    """`neonize` no está instalado: se finge lo poquísimo que usa el demonio."""
    jid = types.ModuleType("neonize.utils.jid")
    jid.build_jid = lambda user, server="s.whatsapp.net": SimpleNamespace(User=user, Server=server)
    jid.JIDToNonAD = lambda j: j
    for nombre, mod in (("neonize", types.ModuleType("neonize")),
                        ("neonize.utils", types.ModuleType("neonize.utils")),
                        ("neonize.utils.jid", jid)):
        monkeypatch.setitem(sys.modules, nombre, mod)


@pytest.fixture
def demonio(tmp_path):
    """Un demonio con sesión falsa ya conectada, sin hilos ni red."""
    d = Demonio({"sesion": str(tmp_path / "s.sqlite3"), "socket": str(tmp_path / "s.sock")})
    d._cli = ClienteFalso()
    d._listo.set()
    return d


# --- Lo que se niega a hacer ----------------------------------------------

@pytest.mark.parametrize(("req", "trozo"), [
    ({"accion": "enviar", "destino": "pepe", "texto": "hola"}, "no me cuadra"),
    ({"accion": "enviar", "destino": "34600112233", "texto": "   "}, "no hay texto"),
    ({"accion": "enviar", "destino": "34600112233", "texto": "x" * 1001}, "tope"),
    ({"accion": "bailar"}, "no sé qué es"),
])
def test_se_niega_y_no_toca_la_red(demonio, req, trozo):
    resp = demonio.handle(req)
    assert resp["ok"] is False
    assert trozo in resp["error"]
    assert demonio._cli.enviados == []       # lo importante: no ha salido nada


def test_sin_sesion_no_inventa_un_envio(tmp_path, monkeypatch):
    """Si la sesión no está lista, se dice. No se espera diez segundos por gusto."""
    monkeypatch.setattr("maripepis.whatsapp.daemon.ESPERA_SESION", 0.01)
    d = Demonio({"sesion": str(tmp_path / "s.sqlite3"), "socket": str(tmp_path / "s.sock")})
    resp = d.handle({"accion": "enviar", "destino": "34600112233", "texto": "hola"})
    assert resp["ok"] is False
    assert "no está lista" in resp["error"]


def test_el_freno_corta_un_bucle(demonio):
    """Seis mensajes en un minuto son un fallo, no una conversación."""
    for i in range(6):
        assert demonio.handle(
            {"accion": "enviar", "destino": "34600112233", "texto": f"va el {i}"})["ok"]

    resp = demonio.handle({"accion": "enviar", "destino": "34600112233", "texto": "y el séptimo"})
    assert resp["ok"] is False
    assert "he parado" in resp["error"]
    assert len(demonio._cli.enviados) == 6


def test_revocar_sin_haber_enviado_nada(demonio):
    resp = demonio.handle({"accion": "revocar"})
    assert resp["ok"] is False
    assert "constancia" in resp["error"]
    assert demonio._cli.revocados == []


def test_si_whatsapp_lo_rechaza_no_se_da_por_enviado(demonio):
    def revienta(*_a, **_k):
        raise RuntimeError("sin conexión")

    demonio._cli.send_message = revienta
    resp = demonio.handle({"accion": "enviar", "destino": "34600112233", "texto": "hola"})
    assert resp["ok"] is False
    assert "no lo ha aceptado" in resp["error"]
    assert demonio._ultimo is None           # y no queda nada que «retirar»


# --- Lo que sí hace --------------------------------------------------------

def test_enviar_a_una_persona(demonio):
    resp = demonio.handle({"accion": "enviar", "destino": "34600112233", "texto": "llego en diez"})
    assert resp == {"ok": True, "id": "MSG-1", "destino": "34600112233@s.whatsapp.net"}
    assert demonio._cli.enviados == [("34600112233", "s.whatsapp.net", "llego en diez")]


def test_enviar_a_un_grupo(demonio):
    """Lo que el enlace `whatsapp://` nunca pudo: un grupo no tiene teléfono."""
    resp = demonio.handle({"accion": "enviar", "destino": "120363000@g.us", "texto": "voy"})
    assert resp["ok"] is True
    assert demonio._cli.enviados[0][:2] == ("120363000", "g.us")


def test_revocar_lo_ultimo(demonio):
    """La red de seguridad que sustituye al Enter que antes dabas tú."""
    demonio.handle({"accion": "enviar", "destino": "34600112233", "texto": "uy"})
    resp = demonio.handle({"accion": "revocar"})
    assert resp["ok"] is True
    assert demonio._cli.revocados == ["MSG-1"]
    # Y no se retira dos veces: lo segundo ya no existe.
    assert demonio.handle({"accion": "revocar"})["ok"] is False


def test_los_grupos_solo_con_filtro(demonio):
    """269 grupos no son una agenda. Sin filtro se da el recuento y ya."""
    resp = demonio.handle({"accion": "grupos"})
    assert resp["ok"] is True
    assert resp["total"] == 3
    assert resp["grupos"] == []              # ni uno: la lista entera no sale de aquí

    resp = demonio.handle({"accion": "grupos", "filtro": "famili"})
    assert [g["nombre"] for g in resp["grupos"]] == ["Familia", "Familia política"]
    assert resp["grupos"][0]["jid"] == "1@g.us"


def test_estado(demonio):
    resp = demonio.handle({"accion": "estado"})
    assert resp["ok"] and resp["conectado"] is True
    assert resp["nombre"] == "Manu"


# --- Por el socket, que es como se usa de verdad ---------------------------

def test_ida_y_vuelta_por_el_socket(demonio):
    """Del cliente al demonio y de vuelta, sin fingir el transporte."""
    mio, suyo = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    hilo = threading.Thread(target=demonio._serve_conn, args=(suyo,), daemon=True)
    hilo.start()

    mio.sendall(protocol.encode({"accion": "enviar", "destino": "34600112233", "texto": "hola"}))
    mio.shutdown(socket.SHUT_WR)
    buf = b""
    while b"\n" not in buf:
        trozo = mio.recv(4096)
        if not trozo:
            break
        buf += trozo
    mio.close()
    hilo.join(timeout=5)

    assert protocol.decode(buf)["id"] == "MSG-1"


def test_sin_demonio_se_nota(tmp_path):
    """«No hay demonio» y «el demonio dice que no» no son lo mismo."""
    assert cliente.pedir({"accion": "ping"}, path=str(tmp_path / "no-hay.sock")) is None


# --- La sesión, cuando el arranque se tuerce -------------------------------
#
# El caso real: systemd levanta este servicio antes de que haya DNS, `connect()`
# se estrella con un «lookup web.whatsapp.com» y el hilo de la sesión se acababa
# ahí. El demonio seguía vivo —atendiendo el socket, contestando que la sesión no
# está lista— y `Restart=on-failure` no lo veía nunca, porque el proceso no se
# muere. Estuvo tres horas y media así hasta que un wasap se quedó sin salir.

def sesion_de_pega(monkeypatch, guion):
    """`neonize.client` fingido: `connect()` hace lo que diga `guion`.

    Cada elemento es o un texto —y entonces `connect()` lanza con ese motivo— o
    ``None``, que es «conecta»: llama al handler de `ConnectedEv` como haría la
    biblioteca y vuelve, que es lo que pasa cuando la sesión se corta.
    """
    creados = []

    class NewClientFalso:
        def __init__(self, nombre):                # noqa: ANN001
            self.nombre = nombre
            self.handler = None
            creados.append(self)

        def event(self, _ev):                      # noqa: ANN001
            def registra(fn):
                self.handler = fn
                return fn
            return registra

        def connect(self):
            paso = guion.pop(0)
            if paso is not None:
                raise RuntimeError(paso)
            self.handler(ClienteFalso(), None)

    mod_cliente = types.ModuleType("neonize.client")
    mod_cliente.NewClient = NewClientFalso
    mod_eventos = types.ModuleType("neonize.events")
    mod_eventos.ConnectedEv = object()
    monkeypatch.setitem(sys.modules, "neonize.client", mod_cliente)
    monkeypatch.setitem(sys.modules, "neonize.events", mod_eventos)
    return creados


@pytest.fixture
def esperas(monkeypatch):
    """Las siestas del hilo, apuntadas en vez de dormidas."""
    dormido = []
    monkeypatch.setattr(daemon.time, "sleep", dormido.append)
    return dormido


class Guion(list):
    """Lo que hará cada `connect()`, y un aviso cuando se gasta uno.

    Es una lista para que `sesion_de_pega` la use igual, y algo más para poder
    pararle el demonio al hilo desde fuera: sin eso el bucle no termina nunca,
    que es justo lo que se le pide en producción.
    """

    def __init__(self, pasos) -> None:
        super().__init__(pasos)
        self.gastados: list = []
        self.al_gastar = lambda: None

    def pop(self, i=0):
        paso = super().pop(i)
        self.gastados.append(paso)
        self.al_gastar()
        return paso


def arranca(tmp_path, guion, para_tras):
    """Un demonio en marcha que se para solo tras `para_tras` intentos."""
    d = Demonio({"sesion": str(tmp_path / "s.sqlite3"), "socket": str(tmp_path / "s.sock")})
    d._running = True

    def basta():
        if len(guion.gastados) >= para_tras:
            d._running = False

    guion.al_gastar = basta
    return d, guion.gastados


def test_un_arranque_sin_red_no_deja_al_demonio_sin_sesion(tmp_path, monkeypatch, esperas):
    """Falla dos veces por DNS y a la tercera entra: el hilo tiene que seguir ahí."""
    guion = Guion(["lookup web.whatsapp.com: Temporary failure in name resolution",
                   "lookup web.whatsapp.com: Temporary failure in name resolution",
                   None])
    sesion_de_pega(monkeypatch, guion)
    d, intentos = arranca(tmp_path, guion, para_tras=3)

    d._hilo_sesion()

    assert len(intentos) == 3                      # no se rindió en el primer fallo
    assert esperas == [5.0, 10.0]                  # y esperó más cada vez


def test_la_espera_entre_intentos_tiene_techo(tmp_path, monkeypatch, esperas):
    """WhatsApp caído de verdad no se arregla llamando cada cinco segundos."""
    guion = Guion(["no hay red"] * 12)
    sesion_de_pega(monkeypatch, guion)
    d, _ = arranca(tmp_path, guion, para_tras=12)

    d._hilo_sesion()

    assert esperas == sorted(esperas)              # siempre hacia arriba
    assert max(esperas) == daemon.MAX_ESPERA_REINTENTO


def test_una_sesion_que_aguantó_devuelve_la_espera_a_cero(tmp_path, monkeypatch, esperas):
    """Una caída tras horas conectado no es el caso del que crece la espera."""
    guion = Guion(["no hay red", "no hay red", None, "se cortó"])
    sesion_de_pega(monkeypatch, guion)
    d, _ = arranca(tmp_path, guion, para_tras=4)

    d._hilo_sesion()

    # La tercera siesta es de 5 y no de 20: entre medias hubo sesión.
    assert esperas == [5.0, 10.0, 5.0]


def test_al_parar_el_demonio_el_hilo_no_reintenta(tmp_path, monkeypatch, esperas):
    """Un `systemctl stop` no puede quedarse esperando a la siguiente vuelta."""
    guion = Guion(["no hay red"])
    sesion_de_pega(monkeypatch, guion)
    d, _ = arranca(tmp_path, guion, para_tras=1)

    d._hilo_sesion()

    assert esperas == []                           # ni siestas ni cliente nuevo
