"""COSTURA 3 — `ListingSchema`: los campos de cada plataforma x categoria
viven en UN sitio, declarativo (`.claude/rules/architecture.md`).

Este modulo declara CUATRO cosas, en este orden:

1. La estructura de procedencia (`Campo`, `Evidencia`) — el corazon del
   proyecto (`.claude/rules/truth-loop.md` SS A). Ningun campo de una
   ficha existe sin decir de donde salio. Un `Campo` con
   `fuente="foto"` y sin `evidencia` es un bug, no un dato: se hace
   IMPOSIBLE de construir (lanza `ValueError`), no un warning.
   Un `valor=None` es un resultado CORRECTO, no un error.

2. El enum canonico de ESTADO + las tablas de mapeo a Vinted y
   Wallapop. Los literales NO coinciden entre plataformas
   (`.claude/rules/product.md`, verificado contra fuentes primarias).
   Donde el mapeo es ambiguo se aplica el sesgo oficial de Vinted:
   "si no sabes que estado elegir, elige el mas bajo".

3. El esquema declarativo de campos por plataforma x categoria.
   Anadir una categoria es anadir una entrada a un dict, no editar
   logica.

4. El sanitizador de texto (`validar_texto` / `es_exportable`) — una
   defensa CON DIENTES: bloquea el export, no avisa
   (`.claude/rules/decision-making.md`).

Regla dura de este fichero: los huecos marcados `[NO VERIFICADO]` en
`product.md` (enums completos de `package_size`, colores, materiales
y *size groups* de Vinted; limite real de caracteres de la
descripcion de Wallapop) NO se rellenan a ojo. Se modelan como texto
libre con un TODO explicito, o se deja la interfaz tipada sin
implementar. Inventarse un enum cerrado aqui es exactamente el fallo
que este proyecto existe para evitar.

Sin dependencias externas. Stdlib puro. Nada de red.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Literal

# ============================================================================
# 1. LA ESTRUCTURA DE PROCEDENCIA (truth-loop.md SS A)
# ============================================================================

Fuente = Literal["foto", "diego", "comparable", "inferido"]
Confianza = Literal["alta", "media", "baja"]

_FUENTES_VALIDAS: frozenset[str] = frozenset({"foto", "diego", "comparable", "inferido"})
_CONFIANZAS_VALIDAS: frozenset[str] = frozenset({"alta", "media", "baja"})


@dataclass(frozen=True)
class Evidencia:
    """Que foto (y, opcionalmente, que region de esa foto) prueba un valor.

    `fichero`: nombre o ruta de la foto de origen, p.ej. "IMG_0421.jpg".
    `bbox`: region `(x, y, w, h)` en pixeles dentro de esa foto.
            Opcional — a veces basta con senalar la foto entera (p.ej.
            una talla legible en toda la etiqueta) sin recortar una
            region concreta.
    """

    fichero: str
    bbox: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if not self.fichero or not self.fichero.strip():
            raise ValueError(
                "Evidencia.fichero no puede estar vacio: una evidencia sin "
                "fichero no es evidencia."
            )
        if self.bbox is not None:
            if len(self.bbox) != 4 or any(v < 0 for v in self.bbox):
                raise ValueError(
                    f"Evidencia.bbox invalido: {self.bbox!r} "
                    "(se espera (x, y, w, h) con valores >= 0)"
                )


@dataclass
class Campo:
    """Un atributo de la ficha con su procedencia OBLIGATORIA.

    Invariante no negociable (truth-loop.md SS A.1): `fuente="foto"`
    sin `evidencia` es un bug, no un dato. Se hace imposible de
    construir: `__post_init__` lanza `ValueError`, no emite un
    warning. Si el modelo no puede senalar donde ve el valor, la
    fuente correcta es "inferido", no "foto".

    `valor=None` es un resultado CORRECTO (truth-loop.md SS A.2): un
    campo vacio le cuesta a Diego 5 segundos rellenarlo; un campo
    plausible y falso le cuesta una devolucion. Ante la duda: `None` +
    `confianza="baja"`.

    Nota para quien use esto en `core/extract.py`: un valor
    "inferido" (p.ej. "parece de algodon") nunca se pega tal cual en
    un campo estructurado (marca, talla, material, medidas) —
    truth-loop.md SS A.3. Ese matiz vive en la descripcion libre, no
    aqui: este dataclass solo garantiza la procedencia, no decide
    donde se renderiza el valor.
    """

    valor: Any | None
    fuente: Fuente
    confianza: Confianza
    evidencia: Evidencia | None = None

    def __post_init__(self) -> None:
        if self.fuente not in _FUENTES_VALIDAS:
            raise ValueError(
                f"fuente invalida: {self.fuente!r}. "
                f"Debe ser una de {sorted(_FUENTES_VALIDAS)}"
            )
        if self.confianza not in _CONFIANZAS_VALIDAS:
            raise ValueError(
                f"confianza invalida: {self.confianza!r}. "
                f"Debe ser una de {sorted(_CONFIANZAS_VALIDAS)}"
            )
        if self.fuente == "foto" and self.evidencia is None:
            raise ValueError(
                "Campo con fuente='foto' sin evidencia: esto es un bug, no "
                "un dato (truth-loop.md SS A.1). Si el modelo no puede "
                "senalar donde ve el valor, la fuente correcta es "
                "'inferido', no 'foto'."
            )


# ============================================================================
# 2. ENUM CANONICO DE ESTADO + MAPEOS A VINTED Y WALLAPOP
# ============================================================================

# Categorias que cambian el conjunto de literales disponible en cada
# plataforma (product.md: Wallapop distingue moda/resto; Vinted solo
# distingue electronica para "Necesita reparacion").
CategoriaTipo = Literal["moda", "electronica", "hogar", "libros", "otros"]


class EstadoCanonico(IntEnum):
    """Escala interna, ordenada de MEJOR (0) a PEOR (6).

    No es un literal de ninguna plataforma: es el punto de encuentro
    para mapear a Wallapop y Vinted, cuyos literales de estado NO
    coinciden en ningun nivel (product.md, implicacion #1). Que sea un
    `IntEnum` ordenado es lo que permite codificar el sesgo "ante la
    duda, el estado mas bajo" (regla oficial de Vinted, help/50) de
    forma verificable: cuando la plataforma de destino no tiene un
    literal exacto para un nivel, el mapeo baja en la escala en vez de
    subir.
    """

    PRECINTADO = 0  # sin abrir / en su caja original (solo Wallapop "resto")
    NUEVO = 1  # nunca usado
    COMO_NUEVO = 2  # usado, sin senales visibles de uso
    MUY_BUENO = 3  # senales de uso minimas
    BUENO = 4  # uso visible, funcional y presentable
    ACEPTABLE = 5  # uso evidente, funcional
    PARA_REPARAR = 6  # no funciona correctamente / muy desgastado


# --- Literales EXACTOS verificados en product.md. No "corregir". -----------

VINTED_ESTADOS: tuple[str, ...] = (
    "Nuevo",
    "Como nuevo",
    "Muy bueno",
    "Bueno",
    "Satisfactorio",
    "Necesita reparación",  # solo electronica
)

WALLAPOP_ESTADOS_MODA: tuple[str, ...] = (
    "Nuevo",
    "Sin estrenar",
    "Como nuevo",
    "Buen estado",  # NO "Bueno"
    "En condiciones aceptables",  # NO "Aceptable"
)

WALLAPOP_ESTADOS_RESTO: tuple[str, ...] = WALLAPOP_ESTADOS_MODA + (
    "Sin abrir",
    "En su caja",
    "Lo ha dado todo",
)
# product.md: "La lista visible depende de la categoria. Observado, no
# enum garantizado." — estos 8 literales son los observados en 149
# anuncios vivos, no una garantia contractual de la API.

# --- Mapeo canonico -> Wallapop ---------------------------------------------
# "Sin estrenar" y "En su caja" existen como opciones validas en la UI de
# Wallapop pero no se generan automaticamente: el pipeline nunca puede
# distinguir por foto "nunca usado" de "nunca estrenado, sin tags" con
# fiabilidad, asi que no se les asigna un nivel canonico propio. Elegir
# el literal mas conservador (mas bajo) cuando no hay uno exacto es el
# sesgo pedido explicitamente para el mapeo ambiguo.

_WALLAPOP_MODA: dict[EstadoCanonico, str] = {
    EstadoCanonico.PRECINTADO: "Nuevo",  # moda no distingue "sin abrir"; techo = Nuevo
    EstadoCanonico.NUEVO: "Nuevo",
    EstadoCanonico.COMO_NUEVO: "Como nuevo",
    EstadoCanonico.MUY_BUENO: "Buen estado",  # sin equivalente exacto -> mas bajo
    EstadoCanonico.BUENO: "Buen estado",
    EstadoCanonico.ACEPTABLE: "En condiciones aceptables",
    EstadoCanonico.PARA_REPARAR: "En condiciones aceptables",  # moda no tiene "lo ha dado todo"
}

_WALLAPOP_RESTO: dict[EstadoCanonico, str] = {
    # [NO VERIFICADO] product.md no distingue el matiz semantico entre
    # "Sin abrir" y "En su caja" (dos literales distintos observados).
    # Se elige "Sin abrir" por defecto para PRECINTADO. TODO: revisar
    # si algun dia se verifica la diferencia real entre ambos.
    EstadoCanonico.PRECINTADO: "Sin abrir",
    EstadoCanonico.NUEVO: "Nuevo",
    EstadoCanonico.COMO_NUEVO: "Como nuevo",
    EstadoCanonico.MUY_BUENO: "Buen estado",
    EstadoCanonico.BUENO: "Buen estado",
    EstadoCanonico.ACEPTABLE: "En condiciones aceptables",
    EstadoCanonico.PARA_REPARAR: "Lo ha dado todo",
}


# --- Parseo: lo que Diego ELIGE en la UI -> el enum canonico ----------------
# `ui/ficha.py::_OPCIONES_ESTADO` ofrece los seis literales de abajo (mas
# "(sin elegir)"). NO son `EstadoCanonico` ni son literales de Wallapop: son
# los nombres de Vinted, que es la escala que Diego tiene en la cabeza. El
# puente entre lo que el elige y el enum interno es esta tabla, y es un
# diccionario a proposito: un `getattr`/normalizacion por texto adivinaria
# ante un valor desconocido, y aqui adivinar es exactamente el fallo.
#
# Dos ausencias deliberadas:
# - `PRECINTADO` no es alcanzable desde la UI: distinguir "sin abrir" de
#   "nuevo" por una foto no es fiable (mismo motivo que _WALLAPOP_MODA).
# - "Para reparar" es el literal de la UI; el de Vinted es "Necesita
#   reparación" (product.md) y lo produce `mapear_estado_vinted`. No
#   "corregir" esta asimetria: son dos vocabularios distintos y este es el
#   punto donde se traducen.

ESTADO_UI_A_CANONICO: dict[str, EstadoCanonico] = {
    "Nuevo": EstadoCanonico.NUEVO,
    "Como nuevo": EstadoCanonico.COMO_NUEVO,
    "Muy bueno": EstadoCanonico.MUY_BUENO,
    "Bueno": EstadoCanonico.BUENO,
    "Satisfactorio": EstadoCanonico.ACEPTABLE,
    "Para reparar": EstadoCanonico.PARA_REPARAR,
}


def parsear_estado(valor: str | None) -> EstadoCanonico | None:
    """Lo que Diego eligio en la UI -> `EstadoCanonico`, o `None`.

    Devuelve `None` para `None`, `"(sin elegir)"`, cadena vacia o
    cualquier valor no reconocido — **nunca adivina un nivel**. Un
    estado ausente le cuesta a Diego elegirlo en la plataforma (2 s);
    un estado adivinado es la causa numero uno de devoluciones y puede
    hacer que Vinted OCULTE el anuncio (product.md, help/50). Quien
    llama decide que hacer con el `None`; lo que no puede es recibir un
    nivel inventado.
    """

    if valor is None:
        return None
    return ESTADO_UI_A_CANONICO.get(valor.strip())


CATEGORIAS: tuple[CategoriaTipo, ...] = ("moda", "electronica", "hogar", "libros", "otros")


def es_categoria_valida(valor: object) -> bool:
    """`True` solo si `valor` es uno de los `CategoriaTipo` conocidos."""

    return isinstance(valor, str) and valor in CATEGORIAS


def mapear_estado_wallapop(estado: EstadoCanonico, categoria: CategoriaTipo) -> str:
    """Estado canonico -> literal EXACTO de Wallapop para esa categoria.

    `categoria == "moda"` usa la lista corta (5 literales); cualquier
    otra categoria usa la lista larga observada (8 literales,
    `WALLAPOP_ESTADOS_RESTO`).
    """

    tabla = _WALLAPOP_MODA if categoria == "moda" else _WALLAPOP_RESTO
    return tabla[estado]


# --- Mapeo canonico -> Vinted ------------------------------------------------

_VINTED_GENERAL: dict[EstadoCanonico, str] = {
    EstadoCanonico.PRECINTADO: "Nuevo",  # Vinted no distingue "sin abrir"; techo = Nuevo
    EstadoCanonico.NUEVO: "Nuevo",
    EstadoCanonico.COMO_NUEVO: "Como nuevo",
    EstadoCanonico.MUY_BUENO: "Muy bueno",
    EstadoCanonico.BUENO: "Bueno",
    EstadoCanonico.ACEPTABLE: "Satisfactorio",
    # "Necesita reparación" solo existe para electronica (ver funcion).
    # Fuera de electronica no hay literal para "no funciona": el suelo
    # de la escala general es "Satisfactorio" -> aplicamos el sesgo del
    # estado mas bajo disponible.
    EstadoCanonico.PARA_REPARAR: "Satisfactorio",
}


def mapear_estado_vinted(estado: EstadoCanonico, categoria: CategoriaTipo) -> str:
    """Estado canonico -> literal EXACTO de Vinted para esa categoria.

    "Necesita reparación" (help/50) solo es un literal valido para
    electronica; para cualquier otra categoria, `PARA_REPARAR` baja al
    literal mas bajo de la escala general ("Satisfactorio"), siguiendo
    la regla oficial de Vinted: ante la duda, el estado mas bajo.
    """

    if categoria == "electronica" and estado == EstadoCanonico.PARA_REPARAR:
        return "Necesita reparación"
    return _VINTED_GENERAL[estado]


# --- Fidelidad del mapeo: ¿el literal comunica un nivel MEJOR que el real? --
# `[listing-audit] BLOQUEANTE, 2026-07-17`: una sudadera ROTA (PARA_REPARAR)
# se publicaba en Wallapop/moda y en Vinted (fuera de electronica) con el
# MISMO literal que un producto simplemente "usado pero aceptable" -- y
# `core/export.py` lo marcaba `traducido=True` ("no re-decidas"), la
# insignia de MAS confianza justo en el output MAS peligroso. Quinta vez que
# la confianza sale anti-correlacionada con el riesgo en este repo
# (`[INC-005]/[INC-009]/[INC-010]`, `decision-making.md` SS16).
#
# La regla NO es ordinal a secas (un peldano de diferencia SIEMPRE seria
# "peor" en terminos estrictos: "Nuevo" tambien cubre PRECINTADO Y NUEVO;
# "Buen estado" tambien cubre MUY_BUENO Y BUENO -- y esos son ambiguedades
# de VOCABULARIO de la plataforma, no mentiras: todos los niveles que
# comparten esos literales son FUNCIONALES, un comprador no sale perdiendo
# nada que le importe). La regla que SI importa es funcional: `PARA_REPARAR`
# es el UNICO nivel de `EstadoCanonico` que documenta "no funciona
# correctamente" (ver su propio docstring). Un mapeo es peligroso
# exactamente cuando ese nivel comparte literal con un nivel que SI
# funciona -- ese es el eje que le importa a un comprador y el que genera
# devoluciones garantizadas.
#
# Generico y no caso-a-caso a proposito: si algun dia se anade OTRO nivel
# no funcional a `EstadoCanonico`, basta con anadirlo a este frozenset --
# `fidelidad_estado()` lo detecta solo en CUALQUIER tabla/categoria/
# plataforma nueva, sin que nadie tenga que acordarse de este incidente.
_ES_NO_FUNCIONAL: frozenset[EstadoCanonico] = frozenset({EstadoCanonico.PARA_REPARAR})


def _tabla_efectiva(categoria: CategoriaTipo, plataforma: Plataforma) -> dict[EstadoCanonico, str]:
    """Literal EXACTO de `plataforma`/`categoria` para CADA `EstadoCanonico`,
    construido llamando a los mapeos REALES (`mapear_estado_vinted`/
    `mapear_estado_wallapop`) -- nunca se re-implementa la tabla aparte, así
    que no puede divergir de lo que de verdad se publica."""
    if plataforma == "vinted":
        return {estado: mapear_estado_vinted(estado, categoria) for estado in EstadoCanonico}
    if plataforma == "wallapop":
        return {estado: mapear_estado_wallapop(estado, categoria) for estado in EstadoCanonico}
    raise ValueError(f"plataforma desconocida: {plataforma!r}")


def fidelidad_estado(
    estado: EstadoCanonico, categoria: CategoriaTipo, plataforma: Plataforma
) -> str | None:
    """`None` si el literal que se publicaría para `estado` en
    `plataforma`/`categoria` es SEGURO: comunica un nivel igual o peor que
    el real, nunca mejor. Devuelve una NOTA (str) cuando no lo es -- hoy,
    el único caso medido es un `estado` NO FUNCIONAL (`_ES_NO_FUNCIONAL`)
    cuyo literal también se usa para un nivel que SÍ funciona: el literal
    elegido (el más bajo disponible, correcto según el sesgo oficial de
    Vinted) igualmente se LEE como "funciona", y un comprador que sólo mire
    el estado no se entera del defecto.

    Quien llama (`core/export.py`) NO cambia el literal por esto -- sigue
    siendo el correcto y forzado por el vocabulario de la plataforma --
    sólo baja `traducido` a `False` y añade la nota como aviso, para que
    Diego sepa que tiene que declarar el defecto a mano (en la descripción,
    en `desperfectos`) porque el estado solo no lo va a comunicar.
    """
    tabla = _tabla_efectiva(categoria, plataforma)
    literal = tabla[estado]

    # -- Red de seguridad ORDINAL, independiente del eje funcional. ----------
    # El criterio funcional de abajo caza el unico fallo MEDIDO hoy
    # (`PARA_REPARAR` compartiendo literal con un nivel funcional), pero por
    # si solo no es mas que un `if PARA_REPARAR` con mejor vocabulario: no
    # cazaria una edicion futura de las tablas que mapeara, p.ej.,
    # ACEPTABLE -> "Como nuevo" (dos niveles FUNCIONALES, ningun cruce de la
    # frontera, y aun asi el comprador recibe algo bastante peor de lo que
    # leyo). Y `product.md` marca el enum de estado de Wallapop como
    # "[NO VERIFICADO] observado, no garantizado": estas tablas VAN a cambiar
    # cuando alguien lo verifique -- no es un futuro hipotetico, es un TODO
    # escrito en el repo.
    #
    # Umbral en 2 peldanos, y esta MEDIDO, no elegido a ojo: sobre las tablas
    # vigentes genera CERO avisos nuevos (el salto maximo entre niveles
    # funcionales es de 1). Un salto de 1 es redondeo de vocabulario
    # ("Buen estado" cubre MUY_BUENO y BUENO; "Nuevo" cubre PRECINTADO y
    # NUEVO) y marcarlo convertiria el aviso en ruido, que es como se muere
    # una defensa (`decision-making.md` SS12). Un salto de >=2 es otra cosa.
    #
    # Las dos reglas son COMPLEMENTARIAS, no redundantes: el fallo real
    # (PARA_REPARAR -> ACEPTABLE) salta UN peldano, asi que la ordinal sola
    # no lo cazaria; y la funcional sola no cazaria el ACEPTABLE ->
    # "Como nuevo" de arriba. Hacen falta las dos.
    comunica = min(otro for otro in tabla if tabla[otro] == literal)
    if int(estado) - int(comunica) >= 2:
        return (
            f'el literal que se publica ("{literal}") comunica "{comunica.name}", '
            f'pero el nivel real es "{estado.name}" -- {int(estado) - int(comunica)} '
            f"peldanos peor. La tabla de mapeo de {plataforma}/'{categoria}' presenta "
            "el producto MEJOR de lo que es: revisala antes de publicar."
        )

    if estado not in _ES_NO_FUNCIONAL:
        return None

    comparte_con_funcional = any(
        otro != estado and tabla[otro] == literal
        for otro in tabla
        if otro not in _ES_NO_FUNCIONAL
    )
    if not comparte_con_funcional:
        return None

    return (
        f'el nivel real es NO FUNCIONAL ("{estado.name}"), pero {plataforma} no tiene '
        f"un literal propio para eso en la categoría '{categoria}': el literal que se "
        f'publica ("{literal}") es correcto (el más bajo disponible) pero TAMBIÉN se '
        "usa para un nivel que sí funciona -- un comprador que sólo lea el estado no "
        "se entera del defecto. Declara el desperfecto en la descripción."
    )


# ============================================================================
# 3. ESQUEMA DECLARATIVO DE CAMPOS POR PLATAFORMA x CATEGORIA
# ============================================================================
#
# Anadir una categoria = anadir una entrada a `WALLAPOP_ATRIBUTOS_POR_CATEGORIA`
# (o extender los `CampoSchema` de Vinted, que no varian por categoria salvo
# `size_id` condicional). No hace falta tocar logica en ningun sitio.

# Titulo compartido: el limite duro es el de Vinted (100 chars) — product.md
# implicacion #5: "Generar uno que sirva para ambas".
TITULO_MAX_CHARS: int = 100

VINTED_FOTOS_MAX: int = 20
WALLAPOP_FOTOS_MAX: int = 10
WALLAPOP_FOTOS_MAX_PRO: int = 50  # con cuenta PRO

VINTED_COLORES_MAX: int = 2
VINTED_MATERIALES_MAX: int = 3

# Tramo de peso de envio de Wallapop — lista cerrada, verificada.
WALLAPOP_TRAMOS_PESO_KG: tuple[int, ...] = (2, 5, 10, 20, 30)


@dataclass(frozen=True)
class LimiteTexto:
    """Limite de longitud de un campo de texto. `None` = sin limite conocido."""

    minimo: int | None = None
    maximo: int | None = None


@dataclass(frozen=True)
class CampoSchema:
    """Declaracion de UN campo de UNA plataforma. Datos, no logica.

    `tipo` es informativo (usado por `extract.py`/UI para decidir cómo
    rellenarlo), no un validador. Los tipos `*_no_verificado` marcan
    campos cuyo enum cerrado real no esta confirmado en `product.md` —
    se modelan como texto libre a proposito: NO se inventa la lista de
    valores permitidos.
    """

    nombre: str
    obligatorio: bool
    tipo: str
    limite: LimiteTexto | None = None
    maximo_items: int | None = None
    condicional: str | None = None
    nota: str | None = None


# --- Vinted ------------------------------------------------------------------
# Fuente: OpenAPI `ItemProperties` + help/375 (product.md). NO varia por
# categoria salvo `size_id` (condicional a que la categoria tenga size group).

VINTED_CAMPOS: tuple[CampoSchema, ...] = (
    CampoSchema("title", True, "texto", limite=LimiteTexto(5, TITULO_MAX_CHARS)),
    CampoSchema("description", True, "texto", limite=LimiteTexto(5, 2000)),
    CampoSchema("fotos", True, "imagenes", maximo_items=VINTED_FOTOS_MAX),
    CampoSchema(
        "catalog_id",
        True,
        "categoria",
        nota="arbol, hoja obligatoria, profundidad 3-4",
    ),
    CampoSchema(
        "brand",
        True,
        "marca",
        nota='lista + crear marca; "Sin marca" es un valor VALIDO, nunca ausente',
    ),
    CampoSchema("status_id", True, "estado"),
    CampoSchema("price", True, "precio", nota="minimo 1; nunca lo pone el LLM (costura 2)"),
    CampoSchema(
        "package_size_id",
        True,
        "enum_no_verificado",
        nota="[NO VERIFICADO] enum cerrado real requiere token de la API de "
        "ontologias de Vinted. TODO: cerrar cuando exista fuente verificada; "
        "hasta entonces, texto libre + aviso en UI.",
    ),
    CampoSchema(
        "size_id",
        False,
        "talla_no_implementado",
        condicional="obligatorio si la categoria tiene size group",
        nota="ver mapear_talla_a_vinted() — interfaz sin implementar a proposito",
    ),
    CampoSchema(
        "colores",
        False,
        "lista_enum_no_verificado",
        maximo_items=VINTED_COLORES_MAX,
        nota="[NO VERIFICADO] enum cerrado no confirmado. Texto libre + aviso.",
    ),
    CampoSchema(
        "materiales",
        False,
        "lista_enum_no_verificado",
        maximo_items=VINTED_MATERIALES_MAX,
        nota="[NO VERIFICADO] enum cerrado no confirmado. Texto libre + aviso.",
    ),
    CampoSchema("largo_cm", False, "numero"),
    CampoSchema("ancho_cm", False, "numero"),
)


# --- Wallapop ------------------------------------------------------------------
# Fuente: ayuda oficial + type_attributes de la API en vivo (product.md).
# "title"/"description" usan los mismos nombres canonicos que Vinted para que
# el sanitizador (SS 4) pueda mirar el limite por nombre de campo sin
# importar la plataforma.

WALLAPOP_CAMPOS: tuple[CampoSchema, ...] = (
    CampoSchema("title", True, "texto", limite=LimiteTexto(1, TITULO_MAX_CHARS)),
    CampoSchema("categoria", True, "categoria", nota="18 raices, hasta 5 niveles"),
    CampoSchema(
        "description",
        True,
        "texto",
        limite=LimiteTexto(1, 600),
        nota="[NO VERIFICADO] limite real no confirmado; anuncios vivos con "
        "668 chars. Recomendacion operativa de product.md: <=600. Verificar "
        "al pegar.",
    ),
    CampoSchema("fotos", True, "imagenes", maximo_items=WALLAPOP_FOTOS_MAX),
    CampoSchema("medidas", False, "texto"),
    CampoSchema("estado", True, "estado"),
    CampoSchema("marca", False, "marca"),
    CampoSchema("hashtags", False, "lista_texto", nota="campo estructurado propio"),
    CampoSchema("precio", True, "precio", nota="nunca lo pone el LLM (costura 2)"),
    CampoSchema(
        "tramo_peso_kg",
        True,
        "enum",
        nota=f"lista cerrada verificada: {WALLAPOP_TRAMOS_PESO_KG}",
    ),
    CampoSchema("ubicacion", True, "texto"),
)

# Atributos por tipo de producto (empirico, API en vivo). Anadir una
# categoria nueva = anadir una entrada aqui.
WALLAPOP_ATRIBUTOS_POR_CATEGORIA: dict[CategoriaTipo, tuple[str, ...]] = {
    "moda": ("brand", "size", "color", "condition"),  # SIN material
    "electronica": ("brand", "model", "storage_capacity", "color", "condition"),
    "hogar": ("height_cm", "width_cm", "length_cm", "material", "color", "is_bulky"),
    "libros": ("isbn", "author", "publisher", "language", "book_format"),
    # "otros": sin atributos estructurados adicionales verificados todavia.
}


def _campo_por_nombre(campos: tuple[CampoSchema, ...], nombre: str) -> CampoSchema | None:
    for c in campos:
        if c.nombre == nombre:
            return c
    return None


# ============================================================================
# INTERFAZ DE TALLA — SIN IMPLEMENTAR A PROPOSITO
# ============================================================================
# product.md implicacion #2: Wallapop usa un string libre combinado
# ("XS / 34 / 6"); Vinted usa un `size_id` dentro de un *size group* por
# categoria. Los size groups/size_ids completos de Vinted estan en
# [NO VERIFICADO] (product.md HUECOS: "requieren token de la API de
# ontologias"). Modelar el mapeo a ojo produciria un size_id inventado —
# el fallo exacto que este proyecto existe para evitar. Se deja la
# interfaz tipada; la implementacion espera a la fuente verificada.


@dataclass(frozen=True)
class TallaWallapop:
    """Talla tal y como la usa Wallapop: un string libre combinado."""

    valor: str  # p.ej. "XS / 34 / 6"


@dataclass(frozen=True)
class TallaVinted:
    """Talla tal y como la usa Vinted: un size_id dentro de un size group."""

    size_group_id: int
    size_id: int


def mapear_talla_a_vinted(talla: TallaWallapop, catalog_id: int) -> TallaVinted:
    """TODO [NO VERIFICADO, product.md HUECOS]: no implementado a proposito.

    Cerrar cuando exista el enum verificado de size groups/size_ids de
    Vinted (requiere token de su API de ontologias). Hasta entonces,
    cualquier valor devuelto aqui seria un size_id inventado.
    """

    raise NotImplementedError(
        "mapear_talla_a_vinted: pendiente del enum verificado de size "
        "groups de Vinted (product.md HUECOS). No implementar a ojo."
    )


# ============================================================================
# 4. SANITIZADOR DE TEXTO — DEFENSA CON DIENTES
# ============================================================================
# Vinted valida en el backend y OCULTA el anuncio si no pasa. Codigos reales
# de `ItemValidationError` (product.md): CONTAINS_EMAIL, EXCESSIVE_UPPERCASE,
# EXCESSIVE_SYMBOLS, UNALLOWED_SYMBOLS, LONG_WORDS, TOO_SHORT, TOO_LONG.
# Ademas prohibe enlaces externos y mencionar marcas distintas a la
# seleccionada — verificado como regla de contenido, aunque product.md marca
# como [NO VERIFICADO] si Wallapop la aplica igual de forma explicita. Este
# sanitizador la aplica a AMBAS plataformas por defecto (postura defensiva:
# el coste de un falso positivo es que Diego edita una frase; el coste de un
# falso negativo es un anuncio oculto o sancionado).
#
# `validar_texto` DEVUELVE violaciones; no lanza. Quien la llama (export,
# UI) es quien debe BLOQUEAR — por eso se ofrece tambien `es_exportable`,
# para que sea imposible "olvidarse" de comprobar la lista.

CodigoViolacion = Literal[
    "CONTAINS_EMAIL",
    "EXCESSIVE_UPPERCASE",
    "EXCESSIVE_SYMBOLS",
    "UNALLOWED_SYMBOLS",
    "LONG_WORDS",
    "TOO_SHORT",
    "TOO_LONG",
    "CONTAINS_LINK",
    "MENTIONS_OTHER_BRAND",
]

# CONTAINS_LINK y MENTIONS_OTHER_BRAND son codigos INTERNOS (no confirmados
# como nombres literales de `ItemValidationError` de Vinted) que aplican las
# dos prohibiciones de contenido explicitas de product.md: "prohibido enlaces
# externos" y "prohibido mencionar/hashtaggear una marca distinta a la
# seleccionada".

Plataforma = Literal["vinted", "wallapop"]
CampoTexto = Literal["title", "description"]


@dataclass(frozen=True)
class Violacion:
    codigo: CodigoViolacion
    mensaje: str


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_SIMBOLO_REPETIDO_RE = re.compile(r"([^\w\s])\1{3,}")
_PUNTUACION_PERMITIDA = ".,;:!¿?¡'\"()€%/&-\n\r\t"
_LONGITUD_MAXIMA_PALABRA = 30
_RATIO_MAYUSCULAS_MAXIMO = 0.5
_MINIMO_LETRAS_PARA_CHEQUEO_MAYUSCULAS = 10
_RATIO_SIMBOLOS_MAXIMO = 0.15
_MINIMO_CHARS_PARA_CHEQUEO_SIMBOLOS = 20

# Lista heuristica de marcas comunes en ropa/electronica de segunda mano.
# NO exhaustiva, NO verificada contra ninguna fuente oficial, y NO es la
# fuente de verdad de marcas (esa es el campo `brand` estructurado, que en
# Vinted es "lista + crear marca": abierto, no enumerable de forma cerrada).
# Sirve solo de defensa best-effort contra menciones flagrantes de OTRA
# marca en texto libre. Exige coincidencia de palabra completa para
# minimizar falsos positivos.
_MARCAS_COMUNES_HEURISTICA: frozenset[str] = frozenset(
    {
        "nike", "adidas", "puma", "zara", "h&m", "mango", "levi's", "levis",
        "gucci", "prada", "louis vuitton", "apple", "samsung", "xiaomi",
        "sony", "lego", "ikea", "the north face", "reebok", "new balance",
        "converse", "vans", "calvin klein", "tommy hilfiger", "ralph lauren",
        "hugo boss", "michael kors", "coach", "bershka", "pull&bear",
        "stradivarius", "desigual",
    }
)


def _limite_texto(plataforma: Plataforma, campo: CampoTexto) -> LimiteTexto | None:
    tabla = VINTED_CAMPOS if plataforma == "vinted" else WALLAPOP_CAMPOS
    campo_schema = _campo_por_nombre(tabla, campo)
    return campo_schema.limite if campo_schema else None


def _es_caracter_permitido(c: str) -> bool:
    if c.isalpha() or c.isdigit() or c.isspace():
        return True
    return c in _PUNTUACION_PERMITIDA


def _caracteres_no_permitidos(texto: str) -> list[str]:
    vistos: list[str] = []
    for c in texto:
        if not _es_caracter_permitido(c) and c not in vistos:
            vistos.append(c)
    return vistos


def _tiene_simbolos_excesivos(texto: str) -> bool:
    if _SIMBOLO_REPETIDO_RE.search(texto):
        return True
    total = len(texto)
    if total < _MINIMO_CHARS_PARA_CHEQUEO_SIMBOLOS:
        return False
    simbolos = sum(1 for c in texto if not (c.isalnum() or c.isspace()))
    return (simbolos / total) > _RATIO_SIMBOLOS_MAXIMO


def _tiene_mayusculas_excesivas(texto: str) -> bool:
    letras = [c for c in texto if c.isalpha()]
    if len(letras) < _MINIMO_LETRAS_PARA_CHEQUEO_MAYUSCULAS:
        return False
    mayusculas = sum(1 for c in letras if c.isupper())
    return (mayusculas / len(letras)) > _RATIO_MAYUSCULAS_MAXIMO


def _palabra_demasiado_larga(texto: str) -> str | None:
    for palabra in texto.split():
        limpio = palabra.strip(string.punctuation)
        if len(limpio) > _LONGITUD_MAXIMA_PALABRA:
            return limpio
    return None


def _detectar_otra_marca(texto: str, marca_seleccionada: str) -> str | None:
    texto_normalizado = f" {texto.lower()} "
    marca_normalizada = marca_seleccionada.strip().lower()
    for marca in _MARCAS_COMUNES_HEURISTICA:
        if marca == marca_normalizada:
            continue
        patron = r"\b" + re.escape(marca) + r"\b"
        if re.search(patron, texto_normalizado):
            return marca
    return None


def validar_texto(
    texto: str,
    plataforma: Plataforma,
    marca_seleccionada: str | None,
    campo: CampoTexto = "description",
) -> list[Violacion]:
    """Revisa `texto` contra las reglas de contenido de `plataforma`.

    NO lanza y NO bloquea por si misma: devuelve la lista de
    violaciones encontradas (vacia = texto limpio). El bloqueo real lo
    hace quien exporta, usando `es_exportable()` — ver esa funcion.
    """

    if plataforma not in ("vinted", "wallapop"):
        raise ValueError(f"plataforma desconocida: {plataforma!r}")

    violaciones: list[Violacion] = []

    limite = _limite_texto(plataforma, campo)
    if limite is not None:
        if limite.minimo is not None and len(texto) < limite.minimo:
            violaciones.append(
                Violacion(
                    "TOO_SHORT",
                    f"'{campo}' tiene {len(texto)} chars, minimo {limite.minimo}.",
                )
            )
        if limite.maximo is not None and len(texto) > limite.maximo:
            violaciones.append(
                Violacion(
                    "TOO_LONG",
                    f"'{campo}' tiene {len(texto)} chars, maximo {limite.maximo}.",
                )
            )

    if _EMAIL_RE.search(texto):
        violaciones.append(Violacion("CONTAINS_EMAIL", "El texto contiene un email."))

    if _URL_RE.search(texto):
        violaciones.append(
            Violacion("CONTAINS_LINK", "El texto contiene un enlace externo (prohibido).")
        )

    no_permitidos = _caracteres_no_permitidos(texto)
    if no_permitidos:
        violaciones.append(
            Violacion(
                "UNALLOWED_SYMBOLS",
                f"Caracteres no permitidos: {''.join(no_permitidos)!r}",
            )
        )

    if _tiene_simbolos_excesivos(texto):
        violaciones.append(
            Violacion("EXCESSIVE_SYMBOLS", "Uso excesivo/repetido de simbolos o puntuacion.")
        )

    if _tiene_mayusculas_excesivas(texto):
        violaciones.append(
            Violacion("EXCESSIVE_UPPERCASE", "Mas del 50% de las letras estan en mayuscula.")
        )

    palabra_larga = _palabra_demasiado_larga(texto)
    if palabra_larga is not None:
        violaciones.append(
            Violacion(
                "LONG_WORDS",
                f"Palabra de {len(palabra_larga)} chars sin espacios: {palabra_larga!r}",
            )
        )

    if marca_seleccionada:
        otra_marca = _detectar_otra_marca(texto, marca_seleccionada)
        if otra_marca is not None:
            violaciones.append(
                Violacion(
                    "MENTIONS_OTHER_BRAND",
                    f"Menciona '{otra_marca}', distinta de la marca seleccionada "
                    f"('{marca_seleccionada}').",
                )
            )

    return violaciones


def es_exportable(
    texto: str,
    plataforma: Plataforma,
    marca_seleccionada: str | None,
    campo: CampoTexto = "description",
) -> bool:
    """`True` solo si `validar_texto` no encuentra ninguna violacion.

    Esto es la parte "con dientes": un texto que no pasa NO se
    exporta. Quien integra el export debe llamar a esto (o a
    `validar_texto` y comprobar la lista) antes de escribir a
    `core/store.py` / generar el CSV — nunca avisar y dejar pasar.
    """

    return len(validar_texto(texto, plataforma, marca_seleccionada, campo)) == 0
