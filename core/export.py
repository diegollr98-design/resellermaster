"""EL EXPORT: de "ficha que Diego confirmó" a un payload por plataforma con
los literales YA TRADUCIDOS a Wallapop/Vinted (`.claude/rules/architecture.md`
"El flujo", etapa `exportar`; `docs/seeds/fase-3-export.md`).

Este es el ~66% del tiempo de Diego, medido por el panel del 2026-07-16, y
cuesta **0 €**: no llama a ningún LLM ni a ningún proveedor. Es tablas de
mapeo (`core/schema.py`) aplicadas a valores que Diego YA CONFIRMÓ.

LA DISTINCIÓN QUE ORDENA ESTE MÓDULO:
- **BLOQUEA** (`ExportBloqueadoError`) lo que publicaría una MENTIRA, o algo
  que Diego no ha visto todavía. No es recuperable desde aquí: hay que
  volver a la ficha.
- **AVISA** (`PayloadPlataforma.avisos`) lo que sólo FALTA (un campo
  obligatorio que el pipeline nunca produce, un enum sin verificar). Diego
  lo elige en la plataforma en un par de segundos; nunca se inventa un
  valor para rellenarlo.

Bloqueos duros, sin flag de escape (`decision-making.md` §12):
1. La ficha no está confirmada por Diego (`campos["confirmada"] is not True`)
   — es lo que sostiene el giro null→mejor-intento de `truth-loop.md` §A.2:
   los valores "mejor intento" sólo son legítimos porque Diego los revisó
   con el píxel delante. Exportar algo que nadie confirmó tira esa premisa.
2-3. Título/descripción no pasan `schema.validar_texto` (email, enlace,
   marca ajena, mayúsculas excesivas, longitud fuera de rango...) —
   `[INC-013]`: el sanitizador llevaba días escrito y nadie lo llamaba
   desde el export. Cablearlo es esta tarea. **OJO, límite honesto**: el
   filtro de marca ajena (`schema._MARCAS_COMUNES_HEURISTICA`) es
   BEST-EFFORT sobre ~31 marcas conocidas -- medido contra el golden set
   real de Diego, 4 de 5 marcas (`Umbro`, `Lufthous`, `Original Marines`,
   `New Age`) NO lo disparan. Un payload SIN violaciones no significa
   "sin marca ajena" -- por eso `construir_payload` añade SIEMPRE un aviso
   recordándolo (nunca confíes en el silencio de este filtro).
   `[listing-audit] BLOQUEANTE, 2026-07-17` (FIX 1): `marca=None`/`""`
   solía SALTARSE este chequeo entero (`schema.validar_texto` tenía un
   `if marca_seleccionada:` que desactivaba `_detectar_otra_marca` cuando
   NO había marca -- justo el caso donde ninguna marca está exenta).
   Corregido en `schema._detectar_otra_marca`: `None`/`""` ya no eximen
   nada, así que "Sin marca" mencionando "Nike" en la descripción BLOQUEA.
4. La categoría (`campos["categoria"]["valor"]`) está ausente o no es una
   `CategoriaTipo` válida — sin ella no se puede elegir la tabla de
   literales de estado correcta (moda vs resto en Wallapop; electrónica vs
   resto en Vinted), y elegir la equivocada ES publicar mal.

Nunca guardamos nada en disco ni copiamos fotos: eso ya lo hace
`core/images.py::exportar_producto` desde la UI. Este módulo sólo calcula
el payload en memoria.

## `[listing-audit] BLOQUEANTE, 2026-07-17` -- la confianza anti-correlacionada
Una sudadera ROTA (`estado=PARA_REPARAR`, `desperfectos` confirmado) se
publicaba con `estado` marcado `traducido=True` ("no re-decidas") en
Wallapop/moda y en Vinted fuera de electrónica -- el literal ("En
condiciones aceptables"/"Satisfactorio") es CORRECTO (el más bajo
disponible, sesgo oficial de Vinted) pero se LEE como "funciona". Y
`desperfectos`, que Diego SÍ confirmó, no llegaba al payload -- se perdía
en silencio, siendo el campo que evita la devolución. Quinta vez que la
confianza sale anti-correlacionada con el riesgo en este repo
(`[INC-005]/[INC-009]/[INC-010]`, `decision-making.md` §16). Fijado con:
`schema.fidelidad_estado` (genérico, no caso a caso -- ver su docstring),
`desperfectos` ahora es un `CampoExportado` en ambas plataformas, y
`aviso_coherencia` (`[INC-011]`, ficha Frankenstein) se propaga a
`payload.avisos` en vez de perderse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import images, schema

logger = logging.getLogger(__name__)

Plataforma = Any  # "wallapop" | "vinted" -- ver schema.Plataforma; sin import circular de Literal aquí

_PLATAFORMAS_VALIDAS: frozenset[str] = frozenset({"wallapop", "vinted"})


class ExportBloqueadoError(Exception):
    """El export se detuvo porque publicar tal cual sería una mentira o algo
    que Diego no ha visto. `violaciones` lleva cada motivo en texto, para
    que la UI los liste todos de una vez en vez de que Diego los descubra
    uno a uno."""

    def __init__(self, violaciones: list[str]):
        self.violaciones = list(violaciones)
        super().__init__("; ".join(self.violaciones) or "export bloqueado")


@dataclass(frozen=True)
class CampoExportado:
    """Un campo estructurado ya listo para pegar en una plataforma concreta.

    `traducido=True` significa "este valor, tal cual, es lo que hay que
    pegar/seleccionar en la plataforma — Diego no tiene que re-decidir
    nada". `traducido=False` significa "esto es el dato crudo que tenemos;
    la plataforma pide otra cosa (un `size_id`, un enum sin verificar...) y
    Diego tiene que elegirlo a mano" — `nota` explica por qué.
    """

    nombre: str
    etiqueta: str
    valor: str | None
    traducido: bool
    nota: str | None = None


@dataclass(frozen=True)
class PayloadPlataforma:
    plataforma: str
    titulo: str
    descripcion: str
    campos: tuple[CampoExportado, ...]
    fotos: tuple[Path, ...]
    fotos_excluidas: tuple[Path, ...]
    avisos: tuple[str, ...]


# ============================================================================
# Helpers de lectura sobre la ficha confirmada
# ============================================================================


def _valor_campo(campos: dict[str, Any], nombre: str) -> str | None:
    datos = campos.get(nombre)
    if not isinstance(datos, dict):
        return None
    valor = datos.get("valor")
    if isinstance(valor, str):
        valor = valor.strip() or None
    return valor


def _validar_confirmada(producto: dict[str, Any]) -> dict[str, Any]:
    """Devuelve el dict raíz `producto["campos"]` si la ficha está
    confirmada; si no, `ExportBloqueadoError` — sin excepción."""
    campos_raiz = producto.get("campos")
    if not isinstance(campos_raiz, dict) or campos_raiz.get("confirmada") is not True:
        raise ExportBloqueadoError(
            [
                "La ficha no está confirmada por Diego (producto['campos']['confirmada'] "
                "!= True). El giro null->mejor-intento (truth-loop.md §A.2) sólo es "
                "legítimo porque Diego revisa cada campo con el píxel delante antes de "
                "publicar: exportar sin confirmar publicaría un valor que nadie ha visto."
            ]
        )
    return campos_raiz


# ============================================================================
# Estado: la ÚNICA traducción real (pasa por una tabla verificada)
# ============================================================================


def _campo_estado(
    campos: dict[str, Any], plataforma: str, categoria: str, avisos: list[str]
) -> CampoExportado:
    valor_ui = _valor_campo(campos, "estado")
    canonico = schema.parsear_estado(valor_ui)
    etiqueta = "status_id" if plataforma == "vinted" else "estado"
    if canonico is None:
        avisos.append(
            f"estado: no confirmado (o no reconocido) — es OBLIGATORIO en {plataforma}, "
            "elígelo a mano en la plataforma. Nunca se adivina un nivel."
        )
        return CampoExportado(nombre="estado", etiqueta=etiqueta, valor=None, traducido=False)

    if plataforma == "vinted":
        literal = schema.mapear_estado_vinted(canonico, categoria)
    else:
        literal = schema.mapear_estado_wallapop(canonico, categoria)

    # `[listing-audit] BLOQUEANTE, 2026-07-17`: el literal puede ser correcto
    # (el más bajo disponible) y AUN ASÍ comunicar un nivel mejor que el real
    # (p.ej. PARA_REPARAR compartiendo literal con un nivel funcional). Eso
    # NO cambia el literal -- sigue siendo el forzado por la plataforma --
    # sólo baja `traducido` y avisa CON el texto delante, nunca en silencio.
    nota_fidelidad = schema.fidelidad_estado(canonico, categoria, plataforma)
    if nota_fidelidad is not None:
        avisos.append(f"estado: {nota_fidelidad}")
        return CampoExportado(
            nombre="estado", etiqueta=etiqueta, valor=literal, traducido=False, nota=nota_fidelidad
        )
    return CampoExportado(nombre="estado", etiqueta=etiqueta, valor=literal, traducido=True)


# ============================================================================
# Desperfectos: texto libre que Diego confirmó a mano -- es literalmente el
# campo que evita una devolución. Nunca se pierde en silencio (`[listing-
# audit] BLOQUEANTE, 2026-07-17`): sale como campo en AMBAS plataformas (no
# hay una etiqueta estructurada propia en ninguna tabla de `product.md`, así
# que se enseña como texto libre para que Diego lo pegue en la descripción o
# lo tenga a la vista) + un aviso que le recuerda comprobar que la
# descripción lo menciona.
# ============================================================================


def _campo_desperfectos(campos: dict[str, Any], avisos: list[str]) -> CampoExportado | None:
    valor = _valor_campo(campos, "desperfectos")
    if valor is None:
        return None
    avisos.append(
        "desperfectos: confirmaste un desperfecto -- comprueba que la descripción lo "
        f"menciona antes de publicar: {valor!r}"
    )
    return CampoExportado(
        nombre="desperfectos",
        etiqueta="desperfectos (texto libre, sin campo estructurado propio)",
        valor=valor,
        traducido=False,
        nota="Diego lo confirmó a mano. Sin este texto en la descripción, el comprador no se entera.",
    )


# ============================================================================
# Marca: obligatoria+"Sin marca" en Vinted, opcional en Wallapop. Nunca una
# marca plausible (product.md implicación #4).
# ============================================================================


def _campo_marca(campos: dict[str, Any], plataforma: str, avisos: list[str]) -> CampoExportado:
    valor = _valor_campo(campos, "marca")
    if plataforma == "vinted":
        if valor is None:
            return CampoExportado(
                nombre="marca",
                etiqueta="brand",
                valor="Sin marca",
                traducido=True,
                nota='Vinted exige brand; sin marca legible/confirmada se usa el valor '
                'válido "Sin marca" (product.md #4) -- nunca una marca plausible.',
            )
        return CampoExportado(nombre="marca", etiqueta="brand", valor=valor, traducido=True)

    # Wallapop: opcional.
    if valor is None:
        avisos.append("marca: no confirmada -- opcional en Wallapop, puedes dejarla en blanco.")
        return CampoExportado(nombre="marca", etiqueta="marca", valor=None, traducido=False)
    return CampoExportado(nombre="marca", etiqueta="marca", valor=valor, traducido=True)


# ============================================================================
# Talla: en Vinted pide un `size_id` de un size group [NO VERIFICADO] --
# `mapear_talla_a_vinted` está sin implementar A PROPÓSITO (product.md
# HUECOS) y NO se llama aquí. En Wallapop es un string LIBRE COMBINADO
# (product.md implicación #2: `"XS / 34 / 6"`), no un valor tal-cual: sigue
# siendo el mismo dato crudo que en Vinted, así que se marca igual
# (`traducido=False` + nota) -- misma asimetría que ya se evitaba bien en
# Vinted, corregida aquí (`[listing-audit], 2026-07-17`, FIX 5).
# ============================================================================

_NOTA_TALLA_VINTED = (
    "Vinted pide un size_id de un size group por categoría; el enum "
    "completo está [NO VERIFICADO] en product.md (requiere token de la API "
    "de ontologías de Vinted). Se enseña el valor crudo -- elige la talla a "
    "mano en la plataforma."
)

_NOTA_TALLA_WALLAPOP = (
    "Wallapop usa un string COMBINADO (p.ej. \"XS / 34 / 6\", product.md "
    "implicación #2), no necesariamente el valor crudo detectado. Revisa/"
    "compón la talla en el formato de Wallapop antes de pegarla."
)


def _campo_talla(campos: dict[str, Any], plataforma: str, avisos: list[str]) -> CampoExportado:
    valor = _valor_campo(campos, "talla")
    if valor is None:
        avisos.append(
            f"talla: no confirmada -- revísala/añádela a mano en {plataforma} si aplica."
        )

    if plataforma == "vinted":
        return CampoExportado(
            nombre="talla",
            etiqueta="size_id",
            valor=valor,
            traducido=False,
            nota=_NOTA_TALLA_VINTED,
        )
    return CampoExportado(
        nombre="talla", etiqueta="size", valor=valor, traducido=False, nota=_NOTA_TALLA_WALLAPOP
    )


# ============================================================================
# Composición: opcional y sin enum verificado en Vinted (máx 3 materiales);
# EL CAMPO NO EXISTE en moda de Wallapop (product.md: "SIN material").
# ============================================================================

_NOTA_COMPOSICION = (
    "[NO VERIFICADO] el enum cerrado de materiales no está confirmado en "
    "product.md. Se enseña el valor crudo -- elige el material exacto a "
    "mano en la plataforma."
)


def _campo_composicion(
    campos: dict[str, Any], plataforma: str, categoria: str
) -> CampoExportado | None:
    """`valor` sale SIEMPRE `None` desde 2026-07-17: Diego decidió quitar
    "composicion" de la ficha (`ui/ficha.py`/`core/extract.py` ya no la
    piden ni la producen -- "solo aplicaba a ropa y no es realmente
    importante"). Esta función se deja tal cual porque `materiales` SÍ es
    un campo válido de Vinted (hasta 3, opcional) -- `_valor_campo` degrada
    sola a `None` porque la clave "composicion" ya nunca está en `campos`,
    así que Diego simplemente ve el hueco y lo rellena a mano si quiere,
    sin que este módulo tenga que cambiar nada."""
    if plataforma == "wallapop" and categoria == "moda":
        # product.md: "moda: brand, size, color, condition -- SIN material".
        return None
    valor = _valor_campo(campos, "composicion")
    etiqueta = "materiales" if plataforma == "vinted" else "material"
    return CampoExportado(
        nombre="composicion", etiqueta=etiqueta, valor=valor, traducido=False,
        nota=_NOTA_COMPOSICION,
    )


# ============================================================================
# Avisos genéricos: campos OBLIGATORIOS de la plataforma que el pipeline
# nunca produce -- derivados de las tablas declarativas de `core/schema.py`,
# nunca de una lista hardcodeada aquí (contrato de la tarea).
# ============================================================================

_CUBIERTOS_POR_CAMPO: dict[str, frozenset[str]] = {
    "vinted": frozenset({"title", "description", "fotos", "brand", "status_id"}),
    "wallapop": frozenset({"title", "description", "fotos", "estado"}),
}


def _avisos_obligatorios_sin_cubrir(plataforma: str) -> list[str]:
    tabla = schema.VINTED_CAMPOS if plataforma == "vinted" else schema.WALLAPOP_CAMPOS
    cubiertos = _CUBIERTOS_POR_CAMPO[plataforma]
    avisos: list[str] = []
    for campo_schema in tabla:
        if not campo_schema.obligatorio or campo_schema.nombre in cubiertos:
            continue
        detalle = campo_schema.nota or "lo eliges/rellenas a mano en la plataforma."
        if campo_schema.nombre in ("catalog_id", "categoria"):
            detalle = (
                "el pipeline sólo produce una categoría interna amplia "
                f"({schema.CATEGORIAS}), no la hoja real del árbol de {plataforma}. "
                "Elige la categoría exacta a mano en la plataforma."
            )
        elif campo_schema.nombre in ("price", "precio"):
            detalle = "el precio nunca sale de aquí (costura 2, core/pricing.py): eligelo con los comparables."
        avisos.append(f"{campo_schema.nombre} es obligatorio en {plataforma}: {detalle}")
    return avisos


# ============================================================================
# Rastro de procedencia: campos publicados que Diego NO revisó con el píxel
# delante. `[listing-audit] BLOQUEANTE, 2026-07-17` (FIX 3): la confirmación
# en bloque (`ui/ficha.py::_construir_confirmado`, `modo_bloque=True`)
# preserva `fuente="inferido"` en los campos que Diego no tocó -- el rastro
# existe en los datos, pero el export nunca lo miraba (`grep -c fuente
# core/export.py` daba 0). Sin esto, la promesa de `truth-loop.md` §A.2
# ("un 'confirmar todo' a ciegas tumbaría la premisa del giro null->mejor-
# intento") era falsa: el export no dejaba ver qué se aceptó sin mirar.
# No bloquea -- Diego ELIGIÓ poder confirmar en bloque -- pero tiene que
# VERSE, o el rastro no sirve de nada.
# ============================================================================


def _avisos_procedencia_no_revisada(campos: dict[str, Any]) -> list[str]:
    nombres = sorted(
        nombre
        for nombre, datos in campos.items()
        if isinstance(datos, dict) and datos.get("fuente") == "inferido"
    )
    if not nombres:
        return []
    return [
        f"{len(nombres)} campo(s) se publican sin que los hayas revisado tú "
        f"(confirmados en bloque, fuente='inferido'): {', '.join(nombres)}."
    ]


# ============================================================================
# Fotos: orden + límite de la plataforma (core/images.py, ya existe)
# ============================================================================


def _fotos_plataforma(
    producto: dict[str, Any], fotos_por_id: dict[str, dict[str, Any]], plataforma: str
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    fids = producto.get("fotos") or []
    rutas: list[Path] = []
    for fid in fids:
        info = fotos_por_id.get(fid)
        if not info or not info.get("ruta"):
            # Nunca un fallback silencioso (decision-making.md §13): un id de
            # foto que el producto referencia y que no existe en
            # `fotos_por_id` es un bug de quien llama, no un dato ausente.
            raise ValueError(
                f"export: la foto {fid!r} referenciada por el producto no está en "
                "fotos_por_id (o no tiene 'ruta')"
            )
        rutas.append(Path(info["ruta"]))

    ordenadas = [fo.ruta for fo in images.sugerir_orden(rutas)]
    limite = images.LIMITE_FOTOS_PLATAFORMA[plataforma]
    return tuple(ordenadas[:limite]), tuple(ordenadas[limite:])


# ============================================================================
# La función pública
# ============================================================================


def construir_payload(
    producto: dict[str, Any], fotos_por_id: dict[str, dict[str, Any]], plataforma: str
) -> PayloadPlataforma:
    """`producto` confirmado (agrupación + ficha) -> payload listo para
    pegar en `plataforma` ("wallapop" | "vinted").

    Lanza `ExportBloqueadoError` si publicar tal cual mentiría o expondría
    algo que Diego no ha visto (ver docstring del módulo). Todo lo demás
    que falte entra en `avisos`, nunca se inventa.
    """
    if plataforma not in _PLATAFORMAS_VALIDAS:
        raise ValueError(f"plataforma desconocida: {plataforma!r}; válidas: wallapop, vinted")

    campos_raiz = _validar_confirmada(producto)
    campos = campos_raiz.get("campos", {})
    if not isinstance(campos, dict):
        campos = {}

    marca_para_sanitizar = _valor_campo(campos, "marca")
    titulo = _valor_campo(campos, "titulo") or ""
    descripcion = _valor_campo(campos, "descripcion") or ""
    categoria = _valor_campo(campos, "categoria")

    bloqueos: list[str] = []

    for viol in schema.validar_texto(titulo, plataforma, marca_para_sanitizar, "title"):
        bloqueos.append(f"título: {viol.mensaje}")
    for viol in schema.validar_texto(descripcion, plataforma, marca_para_sanitizar, "description"):
        bloqueos.append(f"descripción: {viol.mensaje}")

    if not schema.es_categoria_valida(categoria):
        bloqueos.append(
            f"categoría ausente o no reconocida ({categoria!r}): sin ella no se puede "
            "elegir la tabla de literales de estado correcta para esta plataforma."
        )

    if bloqueos:
        raise ExportBloqueadoError(bloqueos)

    avisos: list[str] = []

    # `[INC-011]` LA FICHA FRANKENSTEIN, arriba del todo y textual -- si dos
    # productos se fusionaron, esto es lo primero que Diego tiene que ver,
    # no algo que se pierda entre avisos de "elige la categoría a mano".
    aviso_coherencia = campos_raiz.get("aviso_coherencia")
    if aviso_coherencia:
        avisos.append(f"⚠️ FICHA FRANKENSTEIN -- posible mezcla de productos: {aviso_coherencia}")

    # El filtro de marca ajena es BEST-EFFORT (schema._MARCAS_COMUNES_HEURISTICA,
    # ~31 marcas conocidas; medido: 4/5 marcas reales del golden set de Diego
    # NO lo disparan). Que no haya saltado NO es garantía -- se avisa siempre,
    # nunca en silencio.
    avisos.append(
        "el filtro de marca ajena en título/descripción es best-effort sobre "
        "~31 marcas conocidas (no una lista cerrada) -- repasa el texto tú mismo."
    )

    avisos.extend(_avisos_procedencia_no_revisada(campos))

    campo_marca = _campo_marca(campos, plataforma, avisos)
    campo_talla = _campo_talla(campos, plataforma, avisos)
    campo_estado = _campo_estado(campos, plataforma, categoria, avisos)
    campo_composicion = _campo_composicion(campos, plataforma, categoria)
    campo_desperfectos = _campo_desperfectos(campos, avisos)

    campos_exportados = [campo_marca, campo_talla, campo_estado]
    if campo_composicion is not None:
        campos_exportados.append(campo_composicion)
    if campo_desperfectos is not None:
        campos_exportados.append(campo_desperfectos)

    avisos.extend(_avisos_obligatorios_sin_cubrir(plataforma))

    fotos, fotos_excluidas = _fotos_plataforma(producto, fotos_por_id, plataforma)
    if not fotos:
        avisos.append("no hay fotos para exportar en este producto.")
    if fotos_excluidas:
        avisos.append(
            f"{len(fotos_excluidas)} foto(s) por encima del límite de {plataforma} "
            f"({images.LIMITE_FOTOS_PLATAFORMA[plataforma]}) -- no se incluyen."
        )

    return PayloadPlataforma(
        plataforma=plataforma,
        titulo=titulo,
        descripcion=descripcion,
        campos=tuple(campos_exportados),
        fotos=fotos,
        fotos_excluidas=fotos_excluidas,
        avisos=tuple(avisos),
    )
