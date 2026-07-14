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

v2 (si algun dia se paga): comparables automaticos por busqueda de imagen
via SerpAPI/Google Lens -- ver `buscar_comparables_por_imagen` mas abajo,
detras de la MISMA interfaz. No hay reverse image search gratuita (Bing
Visual Search se retiro en ago-2025).

Sin dependencias externas. Stdlib puro. Nada de red -- `buscar()` solo
CONSTRUYE URLs, nunca las llama.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote_plus

from core.schema import Campo, TallaWallapop

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

    partes = [
        valor
        for nombre in CAMPOS_TERMINO_BUSQUEDA
        if (valor := _valor_buscable(producto.get(nombre))) is not None
    ]
    hay_identificativo = any(
        _valor_buscable(producto.get(nombre)) is not None
        for nombre in CAMPOS_IDENTIFICATIVOS
    )

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
# v2 -- SIN IMPLEMENTAR A PROPOSITO. Misma interfaz, detras de la costura.
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
    """Un comparable real observado en el mercado (v2, de pago).

    `url`: el anuncio concreto. `precio`: el precio observado en ese
    anuncio. `similitud_visual`: score de match por imagen -- es la
    guardia de "es el mismo producto" cuando no hay EAN (truth-loop.md
    SS D punto 2).
    """

    url: str
    precio: float
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
