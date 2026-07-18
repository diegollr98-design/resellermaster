"""COSTURA 2 -- `PriceEngine`: el precio nunca sale de un modelo
(`.claude/rules/architecture.md` SS Costura 2, `.claude/rules/truth-loop.md`
SS D).

Regla madre, no negociable: el precio sale de COMPARABLES OBSERVADOS con
su URL, o no sale. "Unos 20-25e" sin fuente es una mentira plausible --
exactamente el fallo que este proyecto existe para evitar.

v1 (esto, coste 0e): el motor NO TASA -- CONSTRUYE LA CONSULTA. `buscar()`
arma los terminos de busqueda a partir SOLO de atributos con
`fuente="foto"` o `fuente="diego"` (nunca "inferido": buscar por un dato
inventado devuelve comparables del producto equivocado, que es la peor
forma posible de "tener comparables") y genera los enlaces de busqueda de
Wallapop y Vinted. Diego abre el enlace, mira los comparables reales con
sus ojos, y fija el precio. `precio` es SIEMPRE `None` -- estructuralmente
imposible que esta funcion, o el dataclass que devuelve, produzcan un
numero (ver `ConsultaPrecio.__post_init__`, mismo patron que
`Campo.__post_init__` en `core/schema.py`).

El atajo del EAN: si el producto tiene un codigo de barras legible en una
foto (los productos de caja lo llevan; medido en el golden set: el
masajeador tiene EAN 8445061029720), ese codigo es un identificador unico
y global. Un comparable encontrado por EAN es el MISMO producto exacto,
sin match visual ni difuso -- es la guardia de "es el mismo producto" que
pide truth-loop.md SS D, gratis y por construccion. Se marca en la salida
como `tipo_match="exacto"`. Sin EAN (toda la ropa del golden set) la
busqueda es por texto y el resultado es "parecido", nunca "el mismo":
`tipo_match="aproximado"`, sin disfrazarlo.

Contrato con `core/extract.py` (Fase 2, todavia no existe): `buscar()`
recibe un dict `nombre_campo -> Campo` -- el mismo `Campo` de
`core/schema.py`, con su procedencia obligatoria. Los nombres de campo que
esta funcion sabe leer son los de `CAMPOS_TERMINO_BUSQUEDA` mas
`CAMPO_EAN`, definidos abajo. Quien construya `extract.py` debe poblar el
dict con esas claves (o un subconjunto -- todas son opcionales); claves
que no reconoce simplemente se ignoran, nunca se inventan valores para
ellas.

v2 (`tasar()`, coste 0e, decidido 2026-07-17): LEE la busqueda publica POR
TEXTO (nunca EAN: nadie lo pone en los anuncios) via un `Buscador` inyectable
y devuelve MEDIANA + RANGO + las URLs de ~15 comparables PARECIDOS. Nunca "el
precio de tu producto"; nunca un numero sin `>= N_MINIMO_COMPARABLES` (conjunto
inmutable). LEER != PUBLICAR (`architecture.md`, aprobado por Diego): GET plano,
volumen domestico, sin stealth, nada de escribir. La red vive SOLO en las
implementaciones de `Buscador` (p.ej. `BuscadorWallapop`) -- `buscar()` (v1)
sigue sin tocar red, y los tests de `tasar()` inyectan un doble.

Busqueda por imagen (Google Lens/SerpAPI): DESCARTADA, no reabrir -- ver el
final del fichero y `architecture.md` (todas las APIs exigen URL publica; la
app es local; y la ropa generica no tiene identidad unica).
"""

from __future__ import annotations

import json
import logging
import statistics
import unicodedata
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote_plus

from core.schema import Campo, Evidencia, TallaWallapop

logger = logging.getLogger(__name__)

# ============================================================================
# CONTRATO DE ENTRADA -- que claves de `atributos` sabe leer esta costura
# ============================================================================

AtributosProducto = dict[str, Campo]

# Nombre de la clave que lleva el codigo de barras/EAN, si existe.
CAMPO_EAN: str = "ean"

# IDENTIFICATIVOS: campos que por si solos identifican DE QUE producto se
# trata. MODIFICADORES: acompanan pero no identifican -- una talla o un
# color sin marca/modelo/tipo no dicen que producto es (buscar solo "M" en
# Wallapop devuelve el catalogo entero: comparables de CUALQUIER cosa que
# tenga talla M). Una consulta sin ningun identificativo no es una consulta
# valida, aunque tenga modificadores -- ver `buscar()`.
CAMPOS_IDENTIFICATIVOS: tuple[str, ...] = ("marca", "modelo", "tipo")
CAMPOS_MODIFICADORES: tuple[str, ...] = ("talla",)

# Orden de prioridad para construir el termino de busqueda POR TEXTO
# (cuando no hay EAN). product.md: "marca + modelo/tipo + talla si aplica".
CAMPOS_TERMINO_BUSQUEDA: tuple[str, ...] = CAMPOS_IDENTIFICATIVOS + CAMPOS_MODIFICADORES

# Fuentes que SI pueden entrar en una busqueda. "inferido" y "comparable"
# quedan fuera a proposito: un valor inferido no es un hecho observado del
# producto, y un valor "comparable" viene de OTRO anuncio, no de este.
_FUENTES_BUSCABLES: frozenset[str] = frozenset({"foto", "diego"})

TipoMatch = Literal["exacto", "aproximado"]

_MOTIVO_V1_NO_TASA: str = (
    "v1 no tasa: abre el enlace y fija el precio tu (core/pricing.py "
    "informa, no tasa -- truth-loop.md SS D)."
)
_MOTIVO_SIN_DATOS: str = (
    "sin marca ni tipo de producto legibles: no hay con que buscar."
)
_MOTIVO_SOLO_MODIFICADORES: str = (
    "solo hay talla/color legibles, que no identifican el producto: "
    "no hay con que buscar."
)


# ============================================================================
# SALIDA -- `ConsultaPrecio`
# ============================================================================


@dataclass(frozen=True)
class ConsultaPrecio:
    """Lo que `buscar()` devuelve. NUNCA lleva un precio.

    `urls_busqueda`: enlaces listos para abrir, por plataforma
        (`{"wallapop": "...", "vinted": "..."}`). Vacio si no hubo con
        que construir la consulta.
    `terminos`: el texto usado para buscar (el EAN si existia, si no la
        combinacion marca/modelo/tipo/talla). Vacio si no hubo datos.
    `precio`: SIEMPRE `None`. Ver `__post_init__` -- construir esta
        clase con un precio distinto de `None` es un `ValueError`, no
        un valor que alguien pueda colar por descuido.
    `motivo`: por que no hay precio (siempre, v1 no tasa nunca) o por
        que ni siquiera se pudo construir la consulta.
    `tipo_match`: `"exacto"` si la consulta se construyo con EAN
        (mismo producto garantizado), `"aproximado"` si fue por texto
        (parecido, NO el mismo), `None` si no hubo consulta.
    """

    urls_busqueda: dict[str, str]
    terminos: str
    precio: None
    motivo: str
    tipo_match: TipoMatch | None = None

    def __post_init__(self) -> None:
        if self.precio is not None:
            raise ValueError(
                "ConsultaPrecio.precio debe ser None SIEMPRE: el precio "
                "nunca sale de este motor (truth-loop.md SS D). Si tienes "
                "un numero, viene de otro sitio y no pertenece aqui."
            )
        if self.tipo_match is not None and self.tipo_match not in ("exacto", "aproximado"):
            raise ValueError(f"tipo_match invalido: {self.tipo_match!r}")


# ============================================================================
# URLs de busqueda -- FORMATO VERIFICADO contra las webs reales (no inventado)
# ============================================================================
# Wallapop: https://es.wallapop.com/search?keywords=<termino>
#   (el path publico "/app/search?keywords=..." redirige 307 a este; se usa
#   el destino final, no el redirect).
# Vinted:   https://www.vinted.es/catalog?search_text=<termino>
# Ambos verificados con una peticion HTTP real (200 OK) el 2026-07-14. Solo
# se usa el parametro de texto libre -- ningun filtro adicional (categoria,
# talla, precio) esta verificado, y una URL con un parametro inventado que
# no filtra es peor que una simple (asi lo pide la tarea).

_URL_WALLAPOP: str = "https://es.wallapop.com/search?keywords={termino}"
_URL_VINTED: str = "https://www.vinted.es/catalog?search_text={termino}"


def _urls_para_termino(termino: str) -> dict[str, str]:
    codificado = quote_plus(termino)
    return {
        "wallapop": _URL_WALLAPOP.format(termino=codificado),
        "vinted": _URL_VINTED.format(termino=codificado),
    }


# ============================================================================
# Lectura de `Campo` -- solo entra lo observado (foto/diego), nunca lo inferido
# ============================================================================


def _valor_buscable(campo: Campo | None) -> str | None:
    """Extrae un string de un `Campo`, o `None` si no es usable para buscar.

    No usable: el campo no existe, `valor` es `None`/vacio, o la
    `fuente` no esta en `_FUENTES_BUSCABLES` (es decir, es "inferido" o
    "comparable" -- ver comentario en `_FUENTES_BUSCABLES`).
    """

    if campo is None or campo.valor is None:
        return None
    if campo.fuente not in _FUENTES_BUSCABLES:
        return None

    valor = campo.valor
    # La talla puede venir como `TallaWallapop` (string libre combinado,
    # core/schema.py) en vez de un str plano.
    if isinstance(valor, TallaWallapop):
        valor = valor.valor

    texto = str(valor).strip()
    return texto if texto else None


# ============================================================================
# La funcion publica
# ============================================================================


def _terminos_texto(producto: AtributosProducto) -> tuple[list[str], bool]:
    """Las partes del termino de busqueda POR TEXTO (marca/modelo/tipo/talla,
    en ese orden, solo las observadas foto/diego) y si hay algun
    IDENTIFICATIVO. Fuente unica compartida por `buscar()` (v1) y `tasar()`
    (v2) -- el filtro por procedencia (nunca "inferido") vive en UN sitio."""
    partes = [
        valor
        for nombre in CAMPOS_TERMINO_BUSQUEDA
        if (valor := _valor_buscable(producto.get(nombre))) is not None
    ]
    hay_identificativo = any(
        _valor_buscable(producto.get(nombre)) is not None
        for nombre in CAMPOS_IDENTIFICATIVOS
    )
    return partes, hay_identificativo


def buscar(producto: AtributosProducto) -> ConsultaPrecio:
    """Construye la consulta de comparables para `producto`. NUNCA tasa.

    `producto`: dict `nombre_campo -> Campo` -- ver claves reconocidas
    en `CAMPO_EAN` y `CAMPOS_TERMINO_BUSQUEDA`. Un campo con
    `fuente="inferido"` (o "comparable") se ignora aunque tenga
    `valor` -- nunca entra en la busqueda.

    Prioridad: si hay un EAN legible y observado, es el termino
    principal y el match se marca "exacto". Si no, se combina
    marca/modelo/tipo/talla (los que existan, en ese orden) y el match
    se marca "aproximado" -- PERO solo si al menos uno de los
    IDENTIFICATIVOS (`CAMPOS_IDENTIFICATIVOS`: marca/modelo/tipo) esta
    presente. Talla y color son MODIFICADORES: acompanan, nunca
    sostienen una consulta ellos solos -- buscar solo "M" en Wallapop
    devuelve el catalogo entero, que es un comparable falso con
    apariencia de estar respaldado (truth-loop.md SS D.2: un
    comparable que no es el mismo producto no cuenta). Si no hay EAN
    ni ningun identificativo -> no hay con que buscar: `urls_busqueda`
    vacio, `terminos` vacio, `motivo` explicito, `tipo_match=None`.
    """

    ean = _valor_buscable(producto.get(CAMPO_EAN))
    if ean is not None:
        return ConsultaPrecio(
            urls_busqueda=_urls_para_termino(ean),
            terminos=ean,
            precio=None,
            motivo=_MOTIVO_V1_NO_TASA,
            tipo_match="exacto",
        )

    partes, hay_identificativo = _terminos_texto(producto)

    if not partes:
        return ConsultaPrecio(
            urls_busqueda={},
            terminos="",
            precio=None,
            motivo=_MOTIVO_SIN_DATOS,
            tipo_match=None,
        )

    if not hay_identificativo:
        # Solo hay modificadores (talla/color): ninguno identifica el
        # producto. Abstenerse es el resultado CORRECTO aqui --
        # decision-making.md SS 16, el default cae del lado barato.
        return ConsultaPrecio(
            urls_busqueda={},
            terminos="",
            precio=None,
            motivo=_MOTIVO_SOLO_MODIFICADORES,
            tipo_match=None,
        )

    terminos = " ".join(partes)
    return ConsultaPrecio(
        urls_busqueda=_urls_para_termino(terminos),
        terminos=terminos,
        precio=None,
        motivo=_MOTIVO_V1_NO_TASA,
        tipo_match="aproximado",
    )


# ============================================================================
# v2 -- TASAR POR COMPARABLES REALES (mediana de parecidos). Coste 0e.
# `truth-loop.md` SS D.2 + `architecture.md` Costura 2 v2 (decidido 2026-07-17).
# ============================================================================
# Lee la BUSQUEDA PUBLICA POR TEXTO de Wallapop/Vinted (marca+tipo+talla,
# NUNCA por EAN -- Diego verifico que nadie lo pone en los anuncios), coge los
# primeros ~15 resultados y devuelve MEDIANA + RANGO + las URLs. NUNCA "el
# precio de tu producto": SIEMPRE "mediana de N PARECIDOS" (cohorte), porque
# una busqueda por texto no garantiza identidad -- `truth-loop.md` SS D.2.
#
# LO QUE NO CAMBIA (conjunto inmutable): sin comparables reales citados, no
# hay numero. Con `n < N_MINIMO_COMPARABLES` -> `mediana=None` + motivo. La
# mediana es un estadistico sobre datos REALES cada uno con su URL (Diego
# abre los 15 y lo comprueba), no un numero salido de un modelo -- por eso
# es honesta y por eso PUEDE tener un numero, a diferencia de `ConsultaPrecio`.
#
# LEER != PUBLICAR (`architecture.md`, aprobado por Diego 2026-07-17): GET
# HTTP plano a los endpoints publicos, volumen domestico, PROHIBIDO stealth/
# anti-deteccion, NADA de escribir/tocar una cuenta. El acceso a red vive
# detras de la interfaz `Buscador` (inyectable) -> los tests no tocan la red,
# y cambiar de fuente es una decision reversible, no una dependencia esparcida
# (misma disciplina que `LLMEngine`).

N_MINIMO_COMPARABLES: int = 5
LIMITE_COMPARABLES: int = 15

_NOTA_PRECIO_PEDIDO: str = (
    "Es la mediana de lo que OTROS PIDEN por artículos parecidos, no de lo que "
    "se VENDIÓ (los precios de venta no son públicos). El óptimo lo dice el "
    "mercado: si no se vende en ~2 semanas, baja."
)
_MOTIVO_SIN_TERMINOS: str = (
    "no hay con qué buscar comparables (sin marca ni tipo legibles/confirmados)."
)
# `[listing-audit] SERIO, 2026-07-17`: para TASAR (dar un número) hace falta un
# identificativo FUERTE (marca o modelo). `tipo` solo ("sudadera") no es una
# cohorte -- es el catálogo entero de sudaderas de toda marca/talla/estado, y
# su mediana no representa ESTE producto (`truth-loop.md` §D.2: la cohorte es
# "marca + tipo + talla"). `buscar()` v1 sí acepta tipo solo (solo construye
# una URL, Diego mira), pero un NÚMERO exige más.
CAMPOS_IDENTIFICATIVOS_FUERTES: tuple[str, ...] = ("marca", "modelo")
_MOTIVO_COHORTE_AMPLIA: str = (
    "cohorte demasiado amplia: sólo hay un tipo de prenda genérico ({tipo}) sin "
    "marca ni modelo -> la mediana sería del catálogo entero, no de tu producto. "
    "Abre la búsqueda y decide tú."
)


def _motivo_pocos(n: int) -> str:
    return (
        f"solo {n} comparable(s) parecido(s) (< {N_MINIMO_COMPARABLES}): no hay "
        "muestra suficiente para una mediana fiable. Abre la búsqueda y decide tú."
    )


@dataclass(frozen=True)
class Tasacion:
    """Lo que `tasar()` devuelve para UNA plataforma. Es "mediana de N
    PARECIDOS", nunca "el precio de tu producto".

    `mediana`/`minimo`/`maximo`: `None` si no hubo `>= N_MINIMO_COMPARABLES`
        comparables -- entonces NO se da numero (conjunto inmutable).
    `comparables`: los anuncios reales usados, cada uno con URL y precio --
        Diego los abre y lo comprueba. Es lo que hace honesto el numero.
    `url_busqueda`: el enlace a la busqueda completa, para ver el resto.
    `tipo_match`: siempre "aproximado" en la via por texto (parecidos).
    `motivo`: por que no hay mediana (pocos comparables / sin terminos), o la
        nota "precio pedido, no de venta" cuando si la hay.
    """

    plataforma: str
    terminos: str
    comparables: tuple[Comparable, ...]
    mediana: float | None
    minimo: float | None
    maximo: float | None
    url_busqueda: str
    tipo_match: TipoMatch | None
    motivo: str

    @property
    def n(self) -> int:
        return len(self.comparables)


class Buscador(Protocol):
    """Interfaz de LECTURA de comparables. Inyectable -> los tests usan un
    doble y NO tocan la red. Una implementacion NUNCA escribe ni toca una
    cuenta: solo lee resultados publicos (`architecture.md`, LEER!=PUBLICAR)."""

    def buscar_comparables(self, terminos: str) -> list[Comparable]:
        ...


class BuscadorWallapop:
    """Lee la busqueda publica de Wallapop por TEXTO (GET plano, sin stealth,
    sin tocar cuenta). Endpoint verificado el 2026-07-17: `GET
    api.wallapop.com/api/v3/search?keywords=...&source=search_box` -> 200 con
    `data.section.payload.items[]` (title, price, web_slug). Cachea por
    terminos en memoria (un lote consulta cada producto una vez; no se
    machaca el endpoint)."""

    _ENDPOINT = "https://api.wallapop.com/api/v3/search"
    _ITEM_URL = "https://es.wallapop.com/item/{slug}"

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout
        self._cache: dict[str, list[Comparable]] = {}

    def buscar_comparables(self, terminos: str) -> list[Comparable]:
        if terminos in self._cache:
            return self._cache[terminos]
        url = f"{self._ENDPOINT}?keywords={quote_plus(terminos)}&source=search_box"
        req = urllib.request.Request(  # noqa: S310 -- URL fija https, sin input de red
            url, headers={"Accept-Language": "es_ES", "X-DeviceOS": "0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:  # noqa: S310
                datos = json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            # Fallo de red/parseo NUNCA inventa comparables (decision-making.md
            # SS13): se loguea y se devuelve vacio -> `tasar` -> mediana=None.
            logger.warning("Búsqueda de comparables en Wallapop falló para %r: %s", terminos, exc)
            return []
        comparables = self._parsear(datos)
        self._cache[terminos] = comparables
        return comparables

    def _parsear(self, datos: dict) -> list[Comparable]:
        # `[listing-audit] SERIO, 2026-07-17`: el contrato es "parseo raro -> []",
        # por construcción, no porque la UI lo atrape aguas abajo. Se guarda la
        # forma de `items` (no lista, o items no-dict si el endpoint cambia) --
        # antes `for it in items` con items=str/None/[None] lanzaba AttributeError.
        try:
            items = datos["data"]["section"]["payload"]["items"]
        except (KeyError, TypeError):
            return []
        if not isinstance(items, list):
            return []
        out: list[Comparable] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            precio = self._precio(it.get("price"))
            slug = it.get("web_slug")
            if precio is None or precio <= 0 or not slug:
                continue
            out.append(
                Comparable(
                    url=self._ITEM_URL.format(slug=slug),
                    precio=precio,
                    titulo=str(it.get("title") or "").strip() or None,
                )
            )
        return out

    @staticmethod
    def _precio(bruto: object) -> float | None:
        # `[listing-audit] MENOR`: `bool` es subclase de `int` -> `True` colaba
        # como 1,0 € y contaminaba la mediana. Se excluye explícitamente.
        if isinstance(bruto, bool):
            return None
        if isinstance(bruto, (int, float)):
            return float(bruto)
        if isinstance(bruto, dict):  # por si algun dia viene {amount, currency}
            for k in ("amount", "cash", "price"):
                v = bruto.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
        return None


# Tipos de prenda para derivar `tipo` del título confirmado (mejora mucho el
# comparable: "Reebok sudadera XXL" vs "Reebok XXL", que mezcla de todo). Es
# un término de BÚSQUEDA, no un atributo publicado -- Diego confirmó el título.
_TIPOS_PRENDA: tuple[str, ...] = (
    "sudadera", "camiseta", "camisa", "pantalon", "vaquero", "jeans", "falda",
    "chaqueta", "abrigo", "vestido", "zapatilla", "zapato", "bota", "jersey",
    "chandal", "cazadora", "parka", "polo", "top", "body", "pijama", "bikini",
    "banador", "short", "bermuda", "mono", "leggings", "sujetador", "calcetin",
)


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def _campo_desde_dict(datos: Any) -> Campo | None:
    """Reconstruye un `Campo` desde su forma persistida (`store`/`extract.
    serializar`), o `None` si no es usable para buscar (sin valor, o fuente no
    buscable). Nunca lanza por una evidencia mal formada -- degrada a `None`."""
    if not isinstance(datos, dict) or datos.get("valor") is None:
        return None
    fuente = datos.get("fuente")
    if fuente not in _FUENTES_BUSCABLES:
        return None
    evidencia = None
    ev = datos.get("evidencia")
    if isinstance(ev, dict) and ev.get("fichero"):
        bbox = ev.get("bbox")
        try:
            evidencia = Evidencia(fichero=ev["fichero"], bbox=tuple(bbox) if bbox else None)
        except (ValueError, TypeError):
            return None
    try:
        return Campo(
            valor=datos["valor"], fuente=fuente,
            confianza=datos.get("confianza", "baja"), evidencia=evidencia,
        )
    except ValueError:
        return None


def atributos_desde_campos(campos: dict[str, Any]) -> AtributosProducto:
    """Arma la consulta de precio desde una ficha CONFIRMADA (el dict
    `producto["campos"]["campos"]` que persiste `store`). Toma marca/modelo/
    talla/ean con su procedencia REAL (nunca "inferido"), y DERIVA `tipo` del
    título confirmado (un término de búsqueda, marcado `fuente="diego"` porque
    Diego confirmó el título). No inventa nada: si el título no trae un tipo de
    prenda reconocible, no hay `tipo` y se busca solo por marca."""
    atributos: AtributosProducto = {}
    for nombre in (*CAMPOS_IDENTIFICATIVOS, *CAMPOS_MODIFICADORES, CAMPO_EAN):
        if nombre == "tipo":
            continue  # se deriva del título abajo, no viene como campo propio
        campo = _campo_desde_dict(campos.get(nombre))
        if campo is not None:
            atributos[nombre] = campo

    titulo_campo = campos.get("titulo")
    titulo = titulo_campo.get("valor") if isinstance(titulo_campo, dict) else None
    if isinstance(titulo, str):
        t = _sin_acentos(titulo.lower())
        for tipo in _TIPOS_PRENDA:
            if tipo in t:
                atributos["tipo"] = Campo(valor=tipo, fuente="diego", confianza="media")
                break
    return atributos


def tasar(
    producto: AtributosProducto,
    buscador: Buscador,
    plataforma: str = "wallapop",
) -> Tasacion:
    """Mediana de comparables PARECIDOS para `producto`, leyendo la busqueda
    publica via `buscador` (inyectado -> testeable sin red).

    Reusa `_terminos_texto` (misma procedencia que `buscar()`: nunca
    "inferido", exige un identificativo). SIEMPRE por TEXTO, nunca por EAN
    (el seed: nadie lo pone en los anuncios). Sin terminos, o con
    `< N_MINIMO_COMPARABLES` comparables -> `mediana=None` + motivo: NUNCA un
    numero sin muestra (conjunto inmutable). El resultado es "mediana de N
    parecidos", jamas "el precio de tu producto"."""
    partes, hay_identificativo = _terminos_texto(producto)

    if not partes or not hay_identificativo:
        return Tasacion(
            plataforma=plataforma, terminos="", comparables=(), mediana=None,
            minimo=None, maximo=None, url_busqueda="", tipo_match=None,
            motivo=_MOTIVO_SIN_TERMINOS,
        )

    # `[listing-audit] SERIO`: sin marca ni modelo, la cohorte es el catálogo
    # entero -> no se tasa (número plausible que no representa el producto).
    tiene_fuerte = any(
        _valor_buscable(producto.get(n)) is not None for n in CAMPOS_IDENTIFICATIVOS_FUERTES
    )
    if not tiene_fuerte:
        tipo = _valor_buscable(producto.get("tipo")) or "genérico"
        return Tasacion(
            plataforma=plataforma, terminos=" ".join(partes), comparables=(), mediana=None,
            minimo=None, maximo=None, url_busqueda="", tipo_match=None,
            motivo=_MOTIVO_COHORTE_AMPLIA.format(tipo=tipo),
        )

    terminos = " ".join(partes)
    url_busqueda = _urls_para_termino(terminos).get(plataforma, "")
    comparables = tuple(buscador.buscar_comparables(terminos)[:LIMITE_COMPARABLES])

    if len(comparables) < N_MINIMO_COMPARABLES:
        return Tasacion(
            plataforma=plataforma, terminos=terminos, comparables=comparables,
            mediana=None, minimo=None, maximo=None, url_busqueda=url_busqueda,
            tipo_match="aproximado", motivo=_motivo_pocos(len(comparables)),
        )

    precios = [c.precio for c in comparables]
    return Tasacion(
        plataforma=plataforma, terminos=terminos, comparables=comparables,
        mediana=round(statistics.median(precios), 2),
        minimo=round(min(precios), 2), maximo=round(max(precios), 2),
        url_busqueda=url_busqueda, tipo_match="aproximado", motivo=_NOTA_PRECIO_PEDIDO,
    )


# ============================================================================
# v-futuro -- BUSQUEDA POR IMAGEN. DESCARTADA, no reabrir. Misma costura.
# ============================================================================
# architecture.md SS Costura 2: "SerpAPI/Google Lens devuelve comparables
# con {url, precio, similitud_visual} y el motor propone un rango
# observado. Mismo contrato, mismo sitio." No hay reverse image search
# gratuita (Bing Visual Search se retiro en ago-2025; Google Lens solo via
# SerpAPI, de pago) -- implementar esto a ojo, sin la fuente pagada
# detras, produciria comparables inventados. Mismo patron que
# `mapear_talla_a_vinted` en core/schema.py: interfaz tipada, cuerpo
# `NotImplementedError`, no relleno a ojo.


@dataclass(frozen=True)
class Comparable:
    """Un comparable real observado en el mercado.

    `url`: el anuncio concreto (Diego puede abrirlo y comprobarlo -- eso es
        lo que hace honesta la mediana, `truth-loop.md` SS D.2).
    `precio`: el precio que el anuncio PIDE (nunca el de venta: los precios
        de venta no son publicos -- ver `_NOTA_PRECIO_PEDIDO`).
    `titulo`: el titulo del anuncio, para que Diego vea de un vistazo si el
        comparable es del mismo tipo de prenda o coló otra cosa.
    `similitud_visual`: score de match por imagen -- SOLO lo usaria la via
        de imagen (DESCARTADA, ver abajo). En la via por texto es `None`.
    """

    url: str
    precio: float
    titulo: str | None = None
    similitud_visual: float | None = None


def buscar_comparables_por_imagen(
    producto: AtributosProducto,
    fotos: tuple[str, ...],
) -> tuple[Comparable, ...]:
    """TODO (v2, de pago): reverse image search real via SerpAPI/Google Lens.

    No implementado a proposito. No existe ninguna alternativa
    GRATUITA de busqueda inversa por imagen (Bing Visual Search se
    retiro en ago-2025; Google Lens solo es accesible via SerpAPI, de
    pago -- architecture.md). Implementar esto con una heuristica local
    (por ejemplo CLIP) ya se midio y se descarto para clasificar tipo
    de foto en este mismo repo (`[INC-004]`, truth-loop.md SS E) --
    no hay razon para esperar que funcione mejor aqui. Cerrar esta
    funcion exige: (1) decision explicita de Diego de pagar por la API,
    (2) medir el coste real por producto, igual que se hizo con
    Haiku 4.5 para vision (~1 ct/producto). Hasta entonces, cualquier
    comparable devuelto aqui seria un dato inventado.
    """

    raise NotImplementedError(
        "buscar_comparables_por_imagen: v2 de pago, pendiente de decision "
        "explicita de Diego (SerpAPI/Google Lens). No implementar a ojo -- "
        "ver docstring."
    )
