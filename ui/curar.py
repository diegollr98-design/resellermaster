"""ui/curar.py — LA CREMALLERA CON PESTILLO (RESELLERMASTER).

Reemplaza a `ui/confirmacion.py` (borrado en el mismo cambio). Diseño
ganador de un panel adversarial de 21 agentes (5 diseños independientes +
15 jueces), aprobado por Diego.

## El modelo de datos — es el 90% del diseño (`core/grouping.py`)
Las fotos CON EXIF del lote forman una secuencia con ORDEN TOTAL. Entre dos
fotos consecutivas hay una COSTURA, identificada por `(foto_id_izq,
foto_id_der)` — **nunca por índice**. Cada costura está ABIERTA (productos
distintos) o CERRADA (mismo producto). `core.grouping.particion()` es una
función PURA: dado el orden total y las costuras abiertas, los grupos son
los runs contiguos entre costuras abiertas. **Es TODA la lógica de
negocio.** Este módulo (`ui/`) sólo la llama y renderiza — nada de negocio
vive aquí (`.claude/rules/file-organization.md`).

**Consecuencia clave:** el espacio de estados alcanzable son EXACTAMENTE
las particiones contiguas. Esta pantalla expone UNA sola mutación:
`toggle(costura)` (aquí, como abrir/cerrar). No existe ningún widget cuyo
argumento sea una foto suelta — ni "mover", ni "destino", ni multi-
selección. Meter la foto del producto 2 en el 7 no está "mal hecho": no
está en el espacio de estados. NO se debe romper esto añadiendo un "mover
foto" por comodidad.

## El pestillo
Un click en una costura ABIERTA nunca fusiona nada directamente: abre un
`@st.dialog` (`_dialog_fusionar`) que enseña los DOS GRUPOS ENTEROS y
pregunta SÍ/NO. La mutación real (`core.grouping.particion` + `guardar_
agrupacion`) vive SÓLO dentro del modal. Por qué: tras varias uniones
seguidas la página colapsa y el siguiente botón sube bajo el ratón quieto
— un click reflejo fusionaría dos productos reales. Con el pestillo, un
mis-click sólo abre un panel que Diego cierra.

Descoser (abrir una costura ya cerrada, dentro de una tarjeta) SÍ es de un
click sin modal: sobre-cortar es la operación segura de la asimetría de
`truth-loop.md` §E, deshacer siempre disponible.

## Persistencia y errores (`[INC-006]`, `decision-making.md` §13)
Nada vive en `st.session_state` salvo un mensaje de error de un callback
(nunca la agrupación en sí — esa se relee de `store.cargar_lote()` en
CADA render, nunca de un estado guardado en memoria). Toda mutación va en
un callback `on_click` o dentro del `@st.dialog` — nunca se escribe la key
de un widget ya instanciado en el mismo rerun. `store.guardar_agrupacion`
siempre en `try/except StoreError`: si falla, NO se muta nada en pantalla
(la relectura del store en el siguiente render es la única fuente de
verdad) y el error se loguea + pinta con `st.error` — nunca sube como
traceback a la pantalla de Diego.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.grouping import agrupar, costuras_abiertas_de, particion
from core.images import (
    TAMANO_MINIATURA_DEFECTO,
    nombre_miniatura,
    obtener_o_crear_miniatura,
)
from core.store import DEFAULT_DATA_DIR, LoteStore, StoreError

logger = logging.getLogger(__name__)

_DIR_CACHE_MINIATURAS = DEFAULT_DATA_DIR / "cache" / "miniaturas"

# REGLA 1 de la tarea: nunca se pinta el número de un hueco corto (está
# ANTI-correlacionado con la respuesta correcta, medido). Única excepción:
# una pausa larga de verdad — >= 10 min — es la única señal temporal no
# ambigua del lote real de Diego (hay una de 2735 s).
_UMBRAL_PAUSA_LARGA_SEGUNDOS = 600.0

# Key de sesión (NO de widget) para el aviso "log + marca" de un callback
# `on_click` que falló contra el store: escribir aquí desde un callback es
# legal (no es la key de ningún widget); llamar a `st.error` DENTRO del
# callback no lo es (Streamlit descarta la salida de un callback) — mismo
# patrón que `ui/confirmacion.py::_KEY_AVISO_DESTINO_PERDIDO`, que existía
# antes de este módulo.
_KEY_ERROR_STORE = "_curar_error_store"


# --------------------------------------------------------------------------
# Miniaturas: idéntico patrón al de la pantalla anterior (cacheado en disco
# + `st.cache_data` en memoria de sesión). Medido: un rerun con 33
# miniaturas cacheadas cuesta 0,14 s — no hace falta `st.fragment`.
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _miniatura_de(ruta: str, hash_conocido: str) -> str:
    candidato = _DIR_CACHE_MINIATURAS / nombre_miniatura(hash_conocido, TAMANO_MINIATURA_DEFECTO)
    if candidato.exists():
        return str(candidato)
    return str(obtener_o_crear_miniatura(Path(ruta), _DIR_CACHE_MINIATURAS, TAMANO_MINIATURA_DEFECTO))


def _render_imagen_segura(foto: dict, **kwargs_st_image: object) -> None:
    """`st.image(_miniatura_de(...))` sin poder tumbar la pantalla — un
    fichero con la cabecera intacta pero los píxeles truncados revienta al
    decodificar (`core.images.obtener_o_crear_miniatura`); se loguea
    (ruidoso, nunca en silencio) y se pinta un error EN ESA TARJETA, el
    resto de la pantalla sigue viva. Reusa el patrón de
    `ui/confirmacion.py::_render_imagen_segura` (módulo que este reemplaza)."""
    try:
        ruta_miniatura = _miniatura_de(foto["ruta"], foto["hash"])
    except Exception as exc:  # noqa: BLE001 — frontera "una foto del lote", ver docstring.
        logger.exception("No se pudo generar la miniatura de %s", foto["ruta"])
        st.error(f"⚠️ No se pudo generar la miniatura de {Path(foto['ruta']).name}: {exc}")
        return
    st.image(ruta_miniatura, **kwargs_st_image)


def _registrar_error(mensaje: str) -> None:
    st.session_state[_KEY_ERROR_STORE] = mensaje


def _mostrar_error_pendiente() -> None:
    mensaje = st.session_state.pop(_KEY_ERROR_STORE, None)
    if mensaje:
        st.error(mensaje)


# --------------------------------------------------------------------------
# Orden total y hueco entre dos fotos. Sólo lee `timestamp_exif` (ISO 8601,
# ordena bien como texto) — si falta en CUALQUIERA de las dos, el hueco es
# `None` (nunca se inventa un número): el lote sin EXIF degrada apoyándose
# en el nombre de fichero para el orden, y sin ninguna señal de hueco.
# --------------------------------------------------------------------------
def _orden_cronologico(fotos: list[dict]) -> list[dict]:
    return sorted(fotos, key=lambda f: (f["timestamp_exif"] or "", Path(f["ruta"]).name))


def _hueco_segundos(foto_izq: dict, foto_der: dict) -> float | None:
    if not foto_izq.get("timestamp_exif") or not foto_der.get("timestamp_exif"):
        return None
    a = datetime.fromisoformat(foto_izq["timestamp_exif"])
    b = datetime.fromisoformat(foto_der["timestamp_exif"])
    return (b - a).total_seconds()


# --------------------------------------------------------------------------
# Propuesta inicial — `agrupar()` (v5) sigue proponiendo el estado de
# arranque para las fotos recién ingeridas (sin `producto_id` todavía).
# Nunca re-agrupa una foto que ya tiene producto (confirmado o no): sólo
# rellena el hueco de las huérfanas, igual que hacía `ui/confirmacion.py`.
# --------------------------------------------------------------------------
def _proponer_grupo_inicial(store: LoteStore, lote_id: str, estado: dict) -> None:
    fotos_sin_grupo = [f for f in estado["fotos"] if f["producto_id"] is None]
    if not fotos_sin_grupo:
        return
    paths = [Path(f["ruta"]) for f in fotos_sin_grupo]
    ruta_a_id = {f["ruta"]: f["id"] for f in fotos_sin_grupo}
    propuestos = agrupar(paths)
    nuevos_grupos = [[ruta_a_id[str(p)] for p in g.fotos] for g in propuestos]
    grupos_actuales_no_confirmados = [
        list(p["fotos"]) for p in estado["productos"] if not p["confirmado"]
    ]
    grupos_completos = grupos_actuales_no_confirmados + nuevos_grupos
    if not grupos_completos:
        return
    try:
        store.guardar_agrupacion(lote_id, grupos_completos)
    except StoreError as exc:
        logger.exception("No se pudo proponer la agrupación inicial del lote %s", lote_id)
        st.error(f"No se pudo generar la propuesta inicial de agrupación: {exc}")


# --------------------------------------------------------------------------
# Estado de la cremallera, RECALCULADO en cada llamada a partir del store
# (nunca de un cierre/closure capturado en un render anterior — evita el
# fallo de estado obsoleto entre el momento en que se pinta un botón y el
# momento en que Diego lo pulsa, con otro rerun de por medio).
# --------------------------------------------------------------------------
def _grupos_ilegibles_actuales(estado: dict) -> list[list[str]]:
    return [[f["id"]] for f in estado["fotos"] if not f["legible"]]


def _estado_cremallera(
    store: LoteStore, lote_id: str
) -> tuple[dict, list[str], list[list[str]], set[tuple[str, str]]]:
    estado = store.cargar_lote(lote_id)
    legibles_no_confirmadas = [f for f in estado["fotos"] if f["legible"] and not f["confirmada"]]
    fotos_ordenadas = [f["id"] for f in _orden_cronologico(legibles_no_confirmadas)]

    ids_curables = set(fotos_ordenadas)
    grupo_de_foto = {
        fid: p["id"]
        for p in estado["productos"]
        if not p["confirmado"]
        for fid in p["fotos"]
        if fid in ids_curables
    }
    costuras = costuras_abiertas_de(fotos_ordenadas, grupo_de_foto)
    grupos = particion(fotos_ordenadas, costuras)
    return estado, fotos_ordenadas, grupos, costuras


def _guardar_particion(
    store: LoteStore,
    lote_id: str,
    estado: dict,
    nuevos_grupos_curables: list[list[str]],
    *,
    desde_callback: bool,
) -> bool:
    """Escribe la partición completa (curables + ilegibles, cada ilegible
    en su propio grupo de 1) vía `store.guardar_agrupacion` — que
    REEMPLAZA toda la propuesta no confirmada del lote de una vez
    (`core/store.py`), así que hay que pasar SIEMPRE el conjunto completo,
    nunca sólo el grupo que cambió.

    Sólo devuelve `True` si la escritura tuvo éxito — el llamador NUNCA
    debe asumir que la mutación se aplicó si esto devuelve `False`
    (`decision-making.md` §13: UI y store no pueden divergir en silencio).
    """
    grupos_completos = nuevos_grupos_curables + _grupos_ilegibles_actuales(estado)
    if not grupos_completos:
        return True
    try:
        store.guardar_agrupacion(lote_id, grupos_completos)
        return True
    except StoreError as exc:
        logger.exception("No se pudo guardar la agrupación del lote %s", lote_id)
        mensaje = f"No se pudo guardar el cambio de agrupación: {exc}"
        if desde_callback:
            _registrar_error(mensaje)
        else:
            st.error(mensaje)
        return False


# --------------------------------------------------------------------------
# Mutaciones. DESCOSER (abrir una costura cerrada) es de un click, sin
# modal: sobre-cortar es la operación SEGURA de la asimetría de
# `truth-loop.md` §E. Vive en un callback `on_click` (nunca en el cuerpo
# del script, `[INC-006]`).
# --------------------------------------------------------------------------
def _accion_abrir_costura(store: LoteStore, lote_id: str, izq: str, der: str) -> None:
    estado, fotos_ordenadas, _grupos, costuras = _estado_cremallera(store, lote_id)
    nuevas_costuras = set(costuras) | {(izq, der)}
    nuevos_grupos = particion(fotos_ordenadas, nuevas_costuras)
    _guardar_particion(store, lote_id, estado, nuevos_grupos, desde_callback=True)


def _cerrar_costura(store: LoteStore, lote_id: str, izq: str, der: str) -> bool:
    """La única mutación que FUSIONA dos grupos. Vive detrás del pestillo:
    sólo se llama desde dentro de `_dialog_fusionar`, nunca directamente
    desde un botón del cuerpo del script."""
    estado, fotos_ordenadas, _grupos, costuras = _estado_cremallera(store, lote_id)
    nuevas_costuras = set(costuras) - {(izq, der)}
    nuevos_grupos = particion(fotos_ordenadas, nuevas_costuras)
    return _guardar_particion(store, lote_id, estado, nuevos_grupos, desde_callback=False)


def _accion_descartar(store: LoteStore, lote_id: str, foto_id: str) -> None:
    """Callback `on_click` de "🗑 Descartar" para una foto ILEGIBLE. Único
    camino de escritura para esto (`core.store.descartar_foto`); la
    guardia real vive ahí, no aquí."""
    try:
        store.descartar_foto(lote_id, foto_id)
    except StoreError as exc:
        logger.exception("No se pudo descartar la foto %s del lote %s", foto_id, lote_id)
        _registrar_error(f"No se pudo descartar la foto: {exc}")


def _costuras_propuestas_inicialmente(
    fotos_ordenadas: list[str], fotos_por_id: dict[str, dict]
) -> set[tuple[str, str]]:
    """Las costuras que `core.grouping.agrupar()` dejaría ABIERTAS si
    propusiera la agrupación de este lote AHORA MISMO — recalculado sobre
    el EXIF real de los ficheros, nunca leído de lo que Diego ya editó en
    el store. `agrupar()` es una función PURA de la fecha EXIF de cada
    fichero (`core/grouping.py`, docstring del módulo): llamarla de nuevo
    sobre el mismo conjunto de fotos reproduce EXACTAMENTE la propuesta que
    `_proponer_grupo_inicial` ya persistió — no hace falta guardar nada
    aparte, y sobre todo: no hace falta *re-derivar* el umbral por otra
    vía. Mismo patrón que usa el gate `tests/test_curar.py::
    test_el_gate_...` para comparar la propuesta inicial contra la verdad.

    ## `[listing-audit]` HALLAZGO 2 (2026-07-14) — el "tocado" NO puede
    re-thresholdear el hueco por su cuenta. La versión vieja de
    `_grupo_fue_fusionado` recalculaba `hueco >= UMBRAL_HUECO_SEGUNDOS`
    de forma independiente; con EXIF DEGENERADO (timestamps idénticos:
    ver `[INC-005]`, `core.grouping._agrupar_por_tiempo`) la propuesta
    inicial corta TODO (cajón de INCIERTAS, cada foto sola) pero el hueco
    real entre dos fotos que Diego fusionó a mano es 0s — `0 >= 15` es
    `False`, así que la revisión final decía "no fusionaste nada" cuando
    Diego había fusionado el lote entero. El predicado tiene que salir de
    la MISMA fuente que generó la propuesta (`agrupar`/
    `costuras_abiertas_de`), nunca de un umbral re-aplicado a mano."""
    paths = [Path(fotos_por_id[fid]["ruta"]) for fid in fotos_ordenadas]
    ruta_a_id = {str(p): fid for fid, p in zip(fotos_ordenadas, paths)}
    propuestos = agrupar(paths)
    grupo_de_foto: dict[str, int] = {}
    for indice, grupo in enumerate(propuestos):
        for p in grupo.fotos:
            grupo_de_foto[ruta_a_id[str(p)]] = indice
    return costuras_abiertas_de(fotos_ordenadas, grupo_de_foto)


def _grupo_fue_fusionado(
    grupo: list[str], costuras_propuestas_inicialmente: set[tuple[str, str]]
) -> bool:
    """`True` si este grupo contiene, por dentro, una costura que la
    propuesta automática (`_costuras_propuestas_inicialmente`, recalculada
    sobre el EXIF real) habría dejado ABIERTA — es decir, Diego la cerró a
    mano. Es la definición de "tocado" para el modal de revisión: REGLA 4
    de la tarea, "enseña sólo los productos que Diego fusionó". Ver
    HALLAZGO 2 en el docstring de `_costuras_propuestas_inicialmente`: a
    propósito NO recalcula ningún umbral aquí — sólo compara contra la
    fuente que produjo la propuesta."""
    if len(grupo) <= 1:
        return False
    return any(
        (izq, der) in costuras_propuestas_inicialmente for izq, der in zip(grupo, grupo[1:])
    )


def _accion_confirmar_todo(store: LoteStore, lote_id: str) -> bool:
    estado = store.cargar_lote(lote_id)
    fotos_legibles_ids = {f["id"] for f in estado["fotos"] if f["legible"]}
    # Un producto formado SÓLO por una foto ilegible (así es como
    # `core.grouping._grupos_ilegibles` los propone: siempre solos) no se
    # confirma — no es un producto vendible, es un fichero que Diego
    # todavía tiene que descartar o resolver.
    a_confirmar = [
        p["id"]
        for p in estado["productos"]
        if not p["confirmado"] and any(fid in fotos_legibles_ids for fid in p["fotos"])
    ]
    for producto_id in a_confirmar:
        try:
            store.confirmar_producto(producto_id)
        except StoreError as exc:
            logger.exception("No se pudo confirmar el producto %s del lote %s", producto_id, lote_id)
            st.error(f"No se pudo confirmar un producto: {exc}")
            return False
    return True


# --------------------------------------------------------------------------
# Render de fotos en fila (filmstrip) — reutilizado por la tarjeta de un
# grupo, por el modal del pestillo y por el modal de revisión final.
# --------------------------------------------------------------------------
def _render_filmstrip(foto_ids: list[str], fotos_por_id: dict[str, dict], ancho: int) -> None:
    columnas = st.columns(max(len(foto_ids), 1))
    for i, fid in enumerate(foto_ids):
        with columnas[i]:
            _render_imagen_segura(fotos_por_id[fid], width=ancho)
            st.caption(Path(fotos_por_id[fid]["ruta"]).name)


# --------------------------------------------------------------------------
# EL PESTILLO — un click en una costura ABIERTA nunca fusiona: abre este
# modal, que enseña los DOS GRUPOS ENTEROS y pregunta. La mutación real
# vive SÓLO aquí dentro.
# --------------------------------------------------------------------------
@st.dialog("¿Es el mismo producto?", width="large")
def _dialog_fusionar(
    store: LoteStore,
    lote_id: str,
    fotos_por_id: dict[str, dict],
    izq: str,
    der: str,
    grupo_izq: list[str],
    grupo_der: list[str],
) -> None:
    st.write(f"**Grupo A** — {len(grupo_izq)} foto(s)")
    _render_filmstrip(grupo_izq, fotos_por_id, ancho=220)
    st.divider()
    st.write(f"**Grupo B** — {len(grupo_der)} foto(s)")
    _render_filmstrip(grupo_der, fotos_por_id, ancho=220)
    st.divider()

    col_si, col_no = st.columns(2)
    with col_si:
        if st.button("🔗 SÍ, ES EL MISMO", type="primary", use_container_width=True):
            if _cerrar_costura(store, lote_id, izq, der):
                st.rerun()
    with col_no:
        if st.button("✗ NO, SON DISTINTOS", use_container_width=True):
            st.rerun()


@st.dialog("Revisión antes de confirmar", width="large")
def _dialog_confirmar(
    store: LoteStore,
    lote_id: str,
    grupos_actuales: list[list[str]],
    fotos_por_id: dict[str, dict],
    costuras_propuestas_inicialmente: set[tuple[str, str]],
) -> None:
    tocados = [
        g for g in grupos_actuales if _grupo_fue_fusionado(g, costuras_propuestas_inicialmente)
    ]
    if tocados:
        st.warning(
            f"Revisa estos {len(tocados)} grupo(s) que fusionaste antes de confirmar "
            "— el resto son cortes que hizo el algoritmo, sin tu intervención."
        )
        for grupo in tocados:
            st.write(f"**{len(grupo)} foto(s)**")
            _render_filmstrip(grupo, fotos_por_id, ancho=180)
            st.divider()
    else:
        st.info("No fusionaste ningún grupo: todos los cortes son del algoritmo.")

    st.caption(f"Se confirmarán {len(grupos_actuales)} producto(s) en total.")
    if st.button("✅ Confirmar todo", type="primary", use_container_width=True):
        if _accion_confirmar_todo(store, lote_id):
            st.rerun()


# --------------------------------------------------------------------------
# Tarjeta de un grupo: sus fotos en fila. Los seams INTERIORES (siempre
# cerrados, por construcción de `particion()`) se pintan como una
# "cicatriz" (┊) con un botón "✂" de un click, sin modal — descoser es la
# operación segura. Caso general: cero código especial para 1 foto, 2
# fotos o N fotos.
# --------------------------------------------------------------------------
def _render_tarjeta_grupo(
    store: LoteStore, lote_id: str, grupo: list[str], fotos_por_id: dict[str, dict]
) -> None:
    with st.container(border=True):
        st.caption(f"{len(grupo)} foto(s)")
        if len(grupo) == 1:
            _render_imagen_segura(fotos_por_id[grupo[0]], width=130)
            st.caption(Path(fotos_por_id[grupo[0]]["ruta"]).name)
            return

        anchos: list[int] = []
        for i in range(len(grupo)):
            anchos.append(4)
            if i < len(grupo) - 1:
                anchos.append(1)
        columnas = st.columns(anchos)

        idx = 0
        for i, fid in enumerate(grupo):
            with columnas[idx]:
                _render_imagen_segura(fotos_por_id[fid], width=130)
                st.caption(Path(fotos_por_id[fid]["ruta"]).name)
            idx += 1
            if i < len(grupo) - 1:
                izq_id, der_id = fid, grupo[i + 1]
                with columnas[idx]:
                    st.write("┊")
                    st.button(
                        "✂",
                        key=f"descoser_{izq_id}_{der_id}",
                        help="Separar aquí — deshacer siempre disponible, es la operación segura.",
                        on_click=_accion_abrir_costura,
                        args=(store, lote_id, izq_id, der_id),
                    )
                idx += 1


def _render_costura_abierta(
    store: LoteStore,
    lote_id: str,
    fotos_por_id: dict[str, dict],
    izq_id: str,
    der_id: str,
    grupo_izq: list[str],
    grupo_der: list[str],
) -> None:
    """La franja entre dos tarjetas. REGLA 1 de la tarea: nunca se pinta el
    número de un hueco corto (anti-correlacionado con la respuesta
    correcta, medido) — la única excepción es una pausa >= 10 min, la
    única señal temporal no ambigua del lote real de Diego."""
    hueco = _hueco_segundos(fotos_por_id[izq_id], fotos_por_id[der_id])
    _col_izq, col_medio, _col_der = st.columns([1, 2, 1])
    with col_medio:
        if hueco is not None and hueco >= _UMBRAL_PAUSA_LARGA_SEGUNDOS:
            st.caption(f"⏸ PAUSA LARGA ({hueco / 60:.0f} min) — casi seguro otro producto")
        else:
            st.caption("✂ — — — — — — — — — — — — —")
        if st.button(
            "🔗 ¿el mismo producto?",
            key=f"seam_{izq_id}_{der_id}",
            use_container_width=True,
        ):
            _dialog_fusionar(store, lote_id, fotos_por_id, izq_id, der_id, grupo_izq, grupo_der)
    st.divider()


def _render_grupo_confirmado(producto: dict, fotos_por_id: dict[str, dict]) -> None:
    fotos_grupo = [fotos_por_id[fid] for fid in producto["fotos"] if fid in fotos_por_id]
    with st.container(border=True):
        st.caption(f"🔒 Confirmado — {len(fotos_grupo)} foto(s) · grupo `{producto['id'][:8]}`")
        columnas = st.columns(min(len(fotos_grupo), 6) or 1)
        for i, foto in enumerate(fotos_grupo):
            with columnas[i % len(columnas)]:
                _render_imagen_segura(foto, use_container_width=True)


def _render_ilegibles(store: LoteStore, lote_id: str, ilegibles: list[dict]) -> None:
    """CABECERA: un fichero ilegible nunca entra en un grupo (`guardar_
    agrupacion` lanzaría `FotoIlegibleError`) — único camino: descartarla.
    El contador de fotos no cuadra hasta que Diego actúe: ruidoso, no
    silencioso (`truth-loop.md` §E: "lo que el modelo no pueda casar va a
    un cajón de INCIERTAS")."""
    if not ilegibles:
        return
    with st.expander(f"⚠️ {len(ilegibles)} fichero(s) ILEGIBLE(S) — hay que descartarlos", expanded=True):
        for foto in ilegibles:
            col_img, col_info = st.columns([1, 3])
            with col_img:
                _render_imagen_segura(foto, width=100)
            with col_info:
                st.write(Path(foto["ruta"]).name)
                st.caption(foto["error_lectura"] or "motivo no registrado")
                st.button(
                    "🗑 Descartar del lote",
                    key=f"descartar_{foto['id']}",
                    on_click=_accion_descartar,
                    args=(store, lote_id, foto["id"]),
                )


def _avisar_exif_lote(resumen: dict[str, int]) -> None:
    sin_exif = resumen.get("sin_exif", 0)
    total = resumen.get("total", 0)
    if sin_exif == 0 or total == 0:
        return
    st.error(
        f"⚠️ {sin_exif} de {total} fotos sin fecha (vienen de WhatsApp, que la borra). "
        "Pásalas por cable: la app no puede agruparlas bien sin fecha."
    )


# --------------------------------------------------------------------------
# Entrada de la pantalla
# --------------------------------------------------------------------------
def render(store: LoteStore, lote_id: str) -> None:
    estado = store.cargar_lote(lote_id)

    st.header("2. Curar agrupación")
    st.caption(f"Lote «{estado['lote']['nombre']}» — {len(estado['fotos'])} foto(s).")

    if not estado["fotos"]:
        st.info("Este lote todavía no tiene fotos. Ve a «Ingesta» para añadirlas.")
        return

    _mostrar_error_pendiente()
    _avisar_exif_lote(store.resumen_exif_lote(lote_id))

    # Propuesta automática para fotos recién llegadas sin grupo todavía.
    # Nunca re-agrupa fotos que ya están en un producto (confirmado o no).
    _proponer_grupo_inicial(store, lote_id, estado)
    estado = store.cargar_lote(lote_id)

    ilegibles = [f for f in estado["fotos"] if not f["legible"]]
    _render_ilegibles(store, lote_id, ilegibles)

    fotos_por_id = {f["id"]: f for f in estado["fotos"]}
    _, fotos_ordenadas, grupos_actuales, _ = _estado_cremallera(store, lote_id)
    confirmados = [p for p in estado["productos"] if p["confirmado"]]

    # REGLA 2 de la tarea: el contador de productos es información, NUNCA
    # un checksum de validación (colisiona: una fusión mala + un corte de
    # más que sobrevive también cuadra).
    st.caption(
        f"→ {len(grupos_actuales) + len(confirmados)} producto(s) con este estado "
        f"({len(grupos_actuales)} por confirmar, {len(confirmados)} ya confirmados)."
    )

    if not fotos_ordenadas:
        if not ilegibles:
            st.info("No hay fotos por curar en este lote.")
    else:
        for i, grupo in enumerate(grupos_actuales):
            _render_tarjeta_grupo(store, lote_id, grupo, fotos_por_id)
            if i < len(grupos_actuales) - 1:
                grupo_der = grupos_actuales[i + 1]
                _render_costura_abierta(
                    store, lote_id, fotos_por_id, grupo[-1], grupo_der[0], grupo, grupo_der
                )

    if confirmados:
        with st.expander(f"🔒 Confirmados ({len(confirmados)})", expanded=False):
            for producto in confirmados:
                _render_grupo_confirmado(producto, fotos_por_id)

    st.divider()
    if grupos_actuales:
        if st.button("✅ Confirmar agrupación", type="primary", use_container_width=True):
            # Recalculada AQUÍ, al pulsar — no en cada rerun: es la propuesta
            # que el algoritmo haría AHORA (`_costuras_propuestas_inicialmente`),
            # independiente de los toggles de Diego. Ver HALLAZGO 2.
            costuras_propuestas = _costuras_propuestas_inicialmente(fotos_ordenadas, fotos_por_id)
            _dialog_confirmar(store, lote_id, grupos_actuales, fotos_por_id, costuras_propuestas)
    else:
        st.caption("No hay grupos pendientes de confirmar.")
