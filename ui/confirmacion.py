"""ui/confirmacion.py — Pantalla 2: Confirmación de grupos (RESELLERMASTER).

**La pantalla que importa** (`truth-loop.md` §E): el clustering de
`core/grouping.py` PROPONE, Diego CONFIRMA. Este módulo sólo renderiza esa
propuesta y traduce los clicks de Diego en llamadas a
`core.store.LoteStore` — la agrupación real, la que cuenta, vive siempre
en el store (`fotos.producto_id`), nunca en `st.session_state`.

Reglas de diseño no negociables (dadas por la tarea, no inventadas aquí):
  - El error tiene que ser VISIBLE: fotos de un grupo juntas y a tamaño
    suficiente (no miniaturas de 60px).
  - Los grupos `confianza="baja"`/`"media"` van arriba y destacados, con
    su `motivo`. Los de `"alta"` se pueden confirmar en bloque sin mirar
    foto a foto.
  - Mover una foto, partir un grupo, fusionar dos grupos: pocos clicks.
  - Al confirmar, `store.confirmar_producto()` — hecho, no se re-agrupa.
  - Reanudable: todo se recalcula a partir del store en cada rerun, nunca
    de un estado guardado sólo en memoria.

## Nota sobre `core/grouping.py`
Este fichero se escribió mientras `core/grouping.py` estaba siendo
implementado EN PARALELO por otro agente. Se importa contra el contrato
publicado en la tarea (`Grupo`, `agrupar`). Si al ejecutar esto
`core/grouping.py` todavía no existe, se usa el *fallback* de más abajo:
NO es clustering real (1 foto = 1 grupo, `confianza="baja"` siempre) —
existe sólo para que la pantalla no esté rota mientras se termina de
escribir el módulo real, y avisa fuerte por log cada vez que se activa.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import streamlit as st

from core.images import (
    TAMANO_MINIATURA_DEFECTO,
    nombre_miniatura,
    obtener_o_crear_miniatura,
    sugerir_orden,
)
from core.store import DEFAULT_DATA_DIR, LoteStore

logger = logging.getLogger(__name__)

try:
    from core.grouping import Grupo, agrupar
except ImportError:  # pragma: no cover — ver nota de módulo, arriba.
    from dataclasses import dataclass as _dataclass

    @_dataclass(frozen=True)
    class Grupo:  # type: ignore[no-redef]
        fotos: list[Path]
        confianza: Literal["alta", "media", "baja"]
        motivo: str

    def agrupar(fotos: list[Path]) -> list[Grupo]:  # type: ignore[no-redef]
        """FALLBACK explícito — `core/grouping.py` no está disponible
        todavía. No agrupa nada de verdad: cada foto es su propio grupo,
        con `confianza="baja"` para forzar la revisión manual de Diego (el
        mismo sesgo que pide `truth-loop.md`: ante la duda, mínima
        confianza, nunca una agrupación inventada)."""
        logger.warning(
            "core/grouping.py no está disponible; usando fallback trivial "
            "(1 foto = 1 grupo). Esto NO es clustering real — sustituir en "
            "cuanto core/grouping.py exista."
        )
        return [
            Grupo(
                fotos=[f],
                confianza="baja",
                motivo=(
                    "core/grouping.py aún no está implementado: fallback "
                    "trivial, revisar y agrupar a mano."
                ),
            )
            for f in fotos
        ]


_DIR_CACHE_MINIATURAS = DEFAULT_DATA_DIR / "cache" / "miniaturas"
_ORDEN_CONFIANZA = {"baja": 0, "media": 1, "alta": 2}
_MOTIVO_AJUSTE_MANUAL = "Grupo ajustado manualmente por Diego."


# --------------------------------------------------------------------------
# Miniaturas: cacheadas en disco (core.images) + cacheadas en memoria de
# sesión (st.cache_data) para no releer/rehashear JPEGs de varios MB en
# cada rerun de Streamlit (rerunea en cada click).
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _miniatura_de(ruta: str, hash_conocido: str) -> str:
    """Ruta a la miniatura de `ruta`, usando el hash YA calculado en la
    ingesta (evita que `obtener_o_crear_miniatura` tenga que releer el
    fichero de origen entero sólo para recalcular su sha256 en cada
    rerun, cuando ya lo tenemos guardado en el store)."""
    candidato = _DIR_CACHE_MINIATURAS / nombre_miniatura(hash_conocido, TAMANO_MINIATURA_DEFECTO)
    if candidato.exists():
        return str(candidato)
    return str(obtener_o_crear_miniatura(Path(ruta), _DIR_CACHE_MINIATURAS, TAMANO_MINIATURA_DEFECTO))


# --------------------------------------------------------------------------
# Lookup de confianza/motivo: recalcula la propuesta del algoritmo sobre
# las fotos AÚN NO confirmadas del lote y la indexa por el conjunto de
# foto_id que agruparía. Es sólo una ETIQUETA informativa para lo que ya
# vive en el store — el store, no esto, decide qué foto está en qué
# producto. Cacheado por el contenido exacto de fotos-sin-confirmar para
# no recalcular en cada click si nada relevante cambió.
# --------------------------------------------------------------------------
@st.cache_data(show_spinner="Analizando fotos para proponer grupos…")
def _lookup_confianza(fotos_no_confirmadas: tuple[tuple[str, str], ...]) -> dict[frozenset, Grupo]:
    if not fotos_no_confirmadas:
        return {}
    ruta_a_id = {ruta: foto_id for foto_id, ruta in fotos_no_confirmadas}
    paths = [Path(ruta) for _, ruta in fotos_no_confirmadas]
    propuestos = agrupar(paths)
    lookup: dict[frozenset, Grupo] = {}
    for grupo in propuestos:
        ids = frozenset(ruta_a_id[str(p)] for p in grupo.fotos)
        lookup[ids] = grupo
    return lookup


# --------------------------------------------------------------------------
# Escritura al store: SIEMPRE se reconstruye la lista COMPLETA de grupos
# no confirmados antes de llamar a `guardar_agrupacion` — ese método
# REEMPLAZA toda la propuesta no confirmada del lote de una vez
# (`core/store.py`), así que pasar sólo el grupo que cambió borraría el
# resto sin querer.
# --------------------------------------------------------------------------
def _grupos_actuales_no_confirmados(estado: dict) -> list[list[str]]:
    return [list(p["fotos"]) for p in estado["productos"] if not p["confirmado"]]


def _proponer_grupo_inicial(store: LoteStore, lote_id: str, estado: dict) -> None:
    """Fotos sin `producto_id` todavía (recién ingeridas) reciben una
    propuesta automática de `agrupar()`. Es sólo eso, una PROPUESTA: no
    confirma nada, Diego sigue teniendo que aprobar cada grupo."""
    fotos_sin_grupo = [f for f in estado["fotos"] if f["producto_id"] is None]
    if not fotos_sin_grupo:
        return
    paths = [Path(f["ruta"]) for f in fotos_sin_grupo]
    ruta_a_id = {f["ruta"]: f["id"] for f in fotos_sin_grupo}
    propuestos = agrupar(paths)
    nuevos_grupos = [[ruta_a_id[str(p)] for p in g.fotos] for g in propuestos]
    grupos_completos = _grupos_actuales_no_confirmados(estado) + nuevos_grupos
    if grupos_completos:
        store.guardar_agrupacion(lote_id, grupos_completos)


def _mover_fotos(
    store: LoteStore,
    lote_id: str,
    estado: dict,
    foto_ids_a_mover: list[str],
    producto_destino_id: str | None,
) -> None:
    """Mueve `foto_ids_a_mover` fuera de donde estén ahora. Si
    `producto_destino_id` es `None` (o ya no existe tras el movimiento),
    forma un grupo NUEVO con ellas — así es como se "parte" un grupo en
    dos con el mismo mecanismo que mover fotos sueltas."""
    a_mover = set(foto_ids_a_mover)
    grupos_por_producto = {
        p["id"]: [fid for fid in p["fotos"] if fid not in a_mover]
        for p in estado["productos"]
        if not p["confirmado"]
    }
    # Nunca se deja un grupo vacío colgando.
    grupos_por_producto = {pid: fotos for pid, fotos in grupos_por_producto.items() if fotos}

    if producto_destino_id and producto_destino_id in grupos_por_producto:
        grupos_por_producto[producto_destino_id] = (
            grupos_por_producto[producto_destino_id] + foto_ids_a_mover
        )
        grupos_finales = list(grupos_por_producto.values())
    else:
        grupos_finales = list(grupos_por_producto.values()) + [foto_ids_a_mover]

    store.guardar_agrupacion(lote_id, grupos_finales)


def _limpiar_seleccion(foto_ids: list[str]) -> None:
    """Desmarca los checkboxes `sel_{foto_id}` de `foto_ids`.

    SOLO es legal llamar a esto desde un callback `on_click` (ver
    `_accion_mover`/`_accion_fusionar`, justo abajo) — nunca desde el
    cuerpo normal del script. Streamlit prohíbe escribir la key de un
    widget DESPUÉS de haberlo instanciado en el mismo rerun
    (`StreamlitAPIException`), y los checkboxes ya se instanciaron más
    arriba en `_render_grupo` antes de llegar aquí si esto se llamara
    desde el cuerpo del script.
    """
    for fid in foto_ids:
        st.session_state[f"sel_{fid}"] = False


def _accion_mover(
    store: LoteStore,
    lote_id: str,
    foto_ids_candidatas: list[str],
    producto_destino_id: str | None,
) -> None:
    """Callback `on_click` del botón "Mover N foto(s)".

    Por qué un callback y no el `if st.button(...):` de antes: los
    callbacks corren en la fase `on_script_will_rerun`, ANTES de que el
    script vuelva a instanciar ningún widget de este rerun
    (`widget_ids_this_run` está vacío en ese punto) — así que aquí SÍ es
    legal escribir las keys `sel_*` vía `_limpiar_seleccion`, al contrario
    que en el cuerpo del script tras la línea que crea los checkboxes.
    `st.session_state` ya trae, en este punto, el valor fresco que el
    navegador acaba de enviar para cada checkbox, así que la selección se
    relee aquí dentro (`foto_ids_candidatas` filtrado por su estado
    ACTUAL) en vez de fiarse de una lista capturada en el render anterior.
    No hace falta `st.rerun()`: Streamlit rerunea solo tras un callback.
    """
    seleccionadas = [
        fid for fid in foto_ids_candidatas if st.session_state.get(f"sel_{fid}", False)
    ]
    if not seleccionadas:
        return
    estado_actual = store.cargar_lote(lote_id)
    _mover_fotos(store, lote_id, estado_actual, seleccionadas, producto_destino_id)
    _limpiar_seleccion(seleccionadas)


def _accion_fusionar(
    store: LoteStore,
    lote_id: str,
    foto_ids_grupo: list[str],
    producto_destino_id: str,
) -> None:
    """Callback `on_click` del botón "🔗 Fusionar".

    Fusionar mueve TODO el grupo, se haya marcado alguna foto o no — por
    eso `foto_ids_grupo` es la lista completa del grupo, no una selección.
    El bug que esto arregla (`truth-loop.md` §E, superficie `agrupacion`):
    antes de este callback, fusionar NUNCA limpiaba `sel_*`, así que si
    Diego había marcado una foto de este grupo en un render anterior (p.
    ej. para considerarla suelta y luego decidirse por fusionar el grupo
    entero), esa key seguía en `True` y esa foto aparecía PREMARCADA en
    el grupo destino tras la fusión, sin que él la hubiera tocado ahí.
    Mismo motivo que `_accion_mover` para por qué esto es legal aquí y no
    en el cuerpo del script: corre antes de que se instancie ningún
    widget de este rerun.
    """
    estado_actual = store.cargar_lote(lote_id)
    _mover_fotos(store, lote_id, estado_actual, foto_ids_grupo, producto_destino_id)
    _limpiar_seleccion(foto_ids_grupo)


def _etiqueta_grupo(producto: dict) -> str:
    return f"Grupo {producto['id'][:8]} ({len(producto['fotos'])} fotos)"


# --------------------------------------------------------------------------
# Render de un grupo (fila de fotos + acciones)
# --------------------------------------------------------------------------
def _fotos_en_orden(fotos_grupo: list[dict]) -> list[dict]:
    """Orden sugerido dentro del grupo (nitidez, vía core.images). Si algo
    falla, se muestra en el orden que ya trae `fotos_grupo` (cronológico,
    tal y como lo entrega `store.cargar_lote`) — nunca se rompe el render
    de la pantalla por un fallo de una heurística de orden."""
    if len(fotos_grupo) <= 1:
        return fotos_grupo
    try:
        rutas = [Path(f["ruta"]) for f in fotos_grupo]
        orden = sugerir_orden(rutas)
        posicion_por_ruta = {str(o.ruta): o.posicion for o in orden}
        return sorted(fotos_grupo, key=lambda f: posicion_por_ruta.get(f["ruta"], 0))
    except Exception:
        logger.exception("No se pudo calcular el orden sugerido; se muestra cronológico.")
        return fotos_grupo


def _render_grupo(
    store: LoteStore,
    lote_id: str,
    producto: dict,
    confianza: str,
    motivo: str,
    fotos_por_id: dict[str, dict],
    otros_no_confirmados: list[dict],
) -> None:
    aviso = {"baja": st.error, "media": st.warning, "alta": st.success}[confianza]
    fotos_grupo = _fotos_en_orden([fotos_por_id[fid] for fid in producto["fotos"]])

    with st.container(border=True):
        aviso(f"**Confianza {confianza}** — {motivo}")
        st.caption(f"{len(fotos_grupo)} foto(s) · grupo `{producto['id'][:8]}`")

        columnas = st.columns(5)
        seleccionadas: list[str] = []
        for i, foto in enumerate(fotos_grupo):
            with columnas[i % 5]:
                st.image(
                    _miniatura_de(foto["ruta"], foto["hash"]),
                    use_container_width=True,
                )
                marcada = st.checkbox(
                    "Seleccionar", key=f"sel_{foto['id']}", label_visibility="collapsed"
                )
                if marcada:
                    seleccionadas.append(foto["id"])
                st.caption(Path(foto["ruta"]).name)

        col_confirmar, col_mover, col_fusionar = st.columns(3)

        with col_confirmar:
            # Sin `on_click`/`_limpiar_seleccion` a propósito: un grupo
            # confirmado se pinta con `_render_grupo_confirmado` (sin
            # checkboxes), así que sus keys `sel_*` quedan sin widget que
            # las lea — Streamlit las descarta como stale. Inocuo, no
            # necesita el patrón callback de mover/fusionar.
            if st.button("✅ Confirmar grupo", key=f"confirmar_{producto['id']}", type="primary"):
                store.confirmar_producto(producto["id"])
                st.rerun()

        with col_mover:
            if seleccionadas:
                opciones = ["➕ Nuevo grupo (partir)"] + [
                    _etiqueta_grupo(p) for p in otros_no_confirmados
                ]
                destino_label = st.selectbox(
                    "Mover seleccionadas a", opciones, key=f"destino_{producto['id']}"
                )
                destino_id = None
                if destino_label != opciones[0]:
                    idx = opciones.index(destino_label) - 1
                    destino_id = otros_no_confirmados[idx]["id"]
                st.button(
                    f"Mover {len(seleccionadas)} foto(s)",
                    key=f"mover_{producto['id']}",
                    on_click=_accion_mover,
                    args=(store, lote_id, [f["id"] for f in fotos_grupo], destino_id),
                )
            else:
                st.caption("Marca fotos para moverlas a otro grupo o partir éste.")

        with col_fusionar:
            if otros_no_confirmados:
                opciones_fusion = [_etiqueta_grupo(p) for p in otros_no_confirmados]
                fusion_label = st.selectbox(
                    "Fusionar este grupo con", opciones_fusion, key=f"fusion_{producto['id']}"
                )
                idx = opciones_fusion.index(fusion_label)
                destino_id = otros_no_confirmados[idx]["id"]
                st.button(
                    "🔗 Fusionar",
                    key=f"btn_fusion_{producto['id']}",
                    on_click=_accion_fusionar,
                    args=(store, lote_id, list(producto["fotos"]), destino_id),
                )
            else:
                st.caption("No hay otro grupo sin confirmar con el que fusionar.")


def _render_grupo_confirmado(producto: dict, fotos_por_id: dict[str, dict]) -> None:
    fotos_grupo = [fotos_por_id[fid] for fid in producto["fotos"]]
    with st.container(border=True):
        st.caption(f"🔒 Confirmado — {len(fotos_grupo)} foto(s) · grupo `{producto['id'][:8]}`")
        columnas = st.columns(6)
        for i, foto in enumerate(fotos_grupo):
            with columnas[i % 6]:
                st.image(_miniatura_de(foto["ruta"], foto["hash"]), use_container_width=True)


def _avisar_exif_del_lote(resumen: dict[str, int]) -> None:
    """Recordatorio de una línea: si este lote se agrupó sin (o con poca)
    señal temporal, el `motivo` de cada grupo no puede fingir una certeza
    que no tiene — `core/grouping.py` ya lo respeta (techo de confianza,
    nunca "alta" sin base), esta línea es sólo para que Diego lo tenga
    presente antes de revisar. El conteo viene ya calculado de
    `core.store.LoteStore.resumen_exif_lote` — esto sólo lo pinta."""
    total = resumen["total"]
    sin_exif = resumen["sin_exif"]
    if sin_exif == 0 or total == 0:
        return
    if resumen["con_exif"] == 0:
        st.warning(
            "Ninguna foto de este lote trae fecha de captura (EXIF): los grupos de abajo se "
            "propusieron sin señal temporal, sólo por parecido visual — revísalos con más "
            "cuidado de lo habitual."
        )
    else:
        st.warning(
            f"{sin_exif} de {total} foto(s) de este lote no traen fecha de captura (EXIF): "
            "los grupos que las incluyen se propusieron con la señal temporal incompleta — "
            "revísalos con más cuidado."
        )


# --------------------------------------------------------------------------
# Entrada de la pantalla
# --------------------------------------------------------------------------
def render(store: LoteStore, lote_id: str) -> None:
    estado = store.cargar_lote(lote_id)

    st.header("2. Confirmación de grupos")
    st.caption(
        f"Lote «{estado['lote']['nombre']}» — {len(estado['fotos'])} foto(s)."
    )

    if not estado["fotos"]:
        st.info("Este lote todavía no tiene fotos. Ve a «Ingesta» para añadirlas.")
        return

    _avisar_exif_del_lote(store.resumen_exif_lote(lote_id))

    # Propuesta automática para fotos recién llegadas sin grupo todavía.
    # Nunca re-agrupa fotos que ya están en un producto (confirmado o no):
    # sólo rellena el hueco de las huérfanas.
    _proponer_grupo_inicial(store, lote_id, estado)
    estado = store.cargar_lote(lote_id)

    productos = estado["productos"]
    fotos_por_id = {f["id"]: f for f in estado["fotos"]}
    no_confirmados = [p for p in productos if not p["confirmado"]]
    confirmados = [p for p in productos if p["confirmado"]]

    fotos_no_confirmadas = tuple(
        sorted(
            (f["id"], f["ruta"])
            for f in estado["fotos"]
            if f["producto_id"] is not None and not f["confirmada"]
        )
    )
    lookup = _lookup_confianza(fotos_no_confirmadas)

    grupos_anotados: list[tuple[dict, str, str]] = []
    for producto in no_confirmados:
        grupo_meta = lookup.get(frozenset(producto["fotos"]))
        if grupo_meta is not None:
            grupos_anotados.append((producto, grupo_meta.confianza, grupo_meta.motivo))
        else:
            # Diego ya tocó este grupo (movió/partió/fusionó fotos): la
            # propuesta original del algoritmo ya no describe lo que hay
            # aquí. Se marca así, explícitamente, en vez de mentir con la
            # confianza/motivo de una agrupación que ya no es ésta.
            grupos_anotados.append((producto, "media", _MOTIVO_AJUSTE_MANUAL))

    grupos_anotados.sort(key=lambda t: _ORDEN_CONFIANZA[t[1]])
    grupos_atencion = [g for g in grupos_anotados if g[1] in ("baja", "media")]
    grupos_alta = [g for g in grupos_anotados if g[1] == "alta"]

    st.caption(
        f"{len(no_confirmados)} grupo(s) pendiente(s) de confirmar "
        f"({len(grupos_atencion)} requieren atención) · "
        f"{len(confirmados)} confirmado(s)."
    )

    if grupos_atencion:
        st.subheader(f"⚠️ Requieren atención ({len(grupos_atencion)})")
        for producto, confianza, motivo in grupos_atencion:
            otros = [p for p in no_confirmados if p["id"] != producto["id"]]
            _render_grupo(store, lote_id, producto, confianza, motivo, fotos_por_id, otros)

    if grupos_alta:
        with st.expander(
            f"✅ Confianza alta — revisar en bloque ({len(grupos_alta)})", expanded=False
        ):
            # Tampoco limpia `sel_*` — igual de inocuo que "Confirmar
            # grupo" arriba: confirmar en bloque también quita los
            # checkboxes del render (pasan a `_render_grupo_confirmado`),
            # así que no queda ningún widget vivo que lea la key stale.
            if st.button(
                f"Confirmar los {len(grupos_alta)} grupos de confianza alta",
                key="confirmar_todos_alta",
            ):
                for producto, _, _ in grupos_alta:
                    store.confirmar_producto(producto["id"])
                st.rerun()
            for producto, confianza, motivo in grupos_alta:
                otros = [p for p in no_confirmados if p["id"] != producto["id"]]
                _render_grupo(store, lote_id, producto, confianza, motivo, fotos_por_id, otros)

    if confirmados:
        with st.expander(f"🔒 Confirmados ({len(confirmados)})", expanded=False):
            for producto in confirmados:
                _render_grupo_confirmado(producto, fotos_por_id)

    if not no_confirmados and not confirmados:
        st.info("No hay grupos todavía.")
