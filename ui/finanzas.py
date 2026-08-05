"""ui/finanzas.py — LA 5ª PANTALLA: dashboard de ventas + export a Excel.

Superficie sensible de PERSISTENCIA (`truth-loop.md` §B: los importes son
dinero). Toda la lógica (agregados, el `.xlsx`) vive en `core/finanzas.py`;
este módulo SÓLO renderiza y traduce euros<->céntimos en el borde de la UI
(`.claude/rules/file-organization.md`: "Nunca meter lógica de negocio en
app.py/ui/"). El store (`core/store.py`) ya hace el trabajo de negocio real
(snapshots congelados, idempotencia, el ledger CROSS-LOTE) — aquí no se
reimplementa nada de eso.

## CROSS-LOTE, a propósito
`store.cargar_ventas()` no toma `lote_id`: el ledger de finanzas abarca
TODOS los lotes (un producto vendido puede ser de un lote de hace semanas).
Por eso esta pantalla, a diferencia de Ficha/Export/Curar, no depende del
selector de lote de la barra lateral.

## Persistencia y errores (mismo patrón que `ui/export.py`, `[INC-006]`,
`decision-making.md` §13)
El estado se relee de `store.cargar_ventas()` en CADA render, nunca de
`st.session_state` (que aquí sólo guarda los valores de los widgets de
precio/plataforma en edición, y la última ruta del Excel generado — perder
eso en un rerun cuesta un vistazo a `data/exports/`, no dinero). Todo fallo
del store se captura, se loguea ruidoso y se pinta con `st.error` — nunca un
traceback a la pantalla de Diego. Las acciones ("Vendido"/"Deshacer
venta"/"Devolución"/"Exportar") corren DENTRO del cuerpo de un
`if st.button(...):`, nunca vía `on_click=`, así que `st.error` se pinta en
el mismo render sin necesitar el indirect de `session_state` que sí hace
falta para un callback `on_click` de verdad (ver `ui/ficha.py::_rellenar_valor`).

## B2 — recordatorio de retirar del OTRO sitio
Al marcar vendido en la plataforma X, si el producto tiene una publicación
en la OTRA plataforma, se pinta un aviso persistente ("recuerda RETIRAR el
anuncio de <otra>"). Es un CHECKLIST local derivado de los datos ya
guardados (`fila["publicaciones"]` vs `venta["plataforma_venta"]`) — CERO
llamadas a ninguna plataforma, nunca automatiza nada (`CLAUDE.md`: "Nunca
automatizar la publicación").
"""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from core import finanzas
from core.store import LoteStore, ProductoNoEncontradoError, StoreError

logger = logging.getLogger(__name__)

_ETIQUETA_PLATAFORMA: dict[str, str] = {
    "wallapop": "Wallapop",
    "vinted": "Vinted",
    "otro": "otro",
}

_OPCIONES_PLATAFORMA_BASE: tuple[str, ...] = ("wallapop", "vinted", "otro")

_FILTRO_TODO = "Todo"
_FILTRO_NO_VENDIDO = "No vendido"
_FILTRO_VENDIDO = "Vendido"
_FILTRO_DEVUELTO = "Devuelto"
_OPCIONES_FILTRO_ESTADO = (_FILTRO_TODO, _FILTRO_NO_VENDIDO, _FILTRO_VENDIDO, _FILTRO_DEVUELTO)

_KEY_RUTA_EXCEL = "_finanzas_ruta_excel"


# --------------------------------------------------------------------------
# Filtro (estado + buscador de texto). Función PURA -- testeable sin runtime
# de Streamlit (`decision-making.md` §16: el caso "sin resultados" también se
# ejecuta, no sólo se lee).
# --------------------------------------------------------------------------
def _filtrar(
    filas: list[dict[str, Any]], filtro_estado: str, texto_busqueda: str
) -> list[dict[str, Any]]:
    resultado = filas
    if filtro_estado == _FILTRO_NO_VENDIDO:
        resultado = [f for f in resultado if f.get("venta") is None]
    elif filtro_estado == _FILTRO_VENDIDO:
        resultado = [
            f for f in resultado if (f.get("venta") or {}).get("estado") == "vendida"
        ]
    elif filtro_estado == _FILTRO_DEVUELTO:
        resultado = [
            f for f in resultado if (f.get("venta") or {}).get("estado") == "devuelta"
        ]

    texto = (texto_busqueda or "").strip().lower()
    if texto:
        def _coincide(fila: dict[str, Any]) -> bool:
            referencia = str(fila.get("referencia") or "")
            titulo = (fila.get("titulo") or "").lower()
            return texto in referencia or texto in titulo

        resultado = [f for f in resultado if _coincide(f)]
    return resultado


# --------------------------------------------------------------------------
# Auto-relleno del precio/plataforma al abrir el formulario "Vendido":
# la publicación MÁS RECIENTE con un precio elegido (Diego lo fijó en
# «4. Export» al pulsar Subido). Si no hay ninguna publicación con precio
# (nunca subió, o subió sin pasar por el precio de Export), el campo nace
# vacío -- Diego lo teclea, nunca se inventa un número.
# --------------------------------------------------------------------------
def _publicacion_mas_reciente_con_precio(fila: dict[str, Any]) -> dict[str, Any] | None:
    con_precio = [
        p for p in (fila.get("publicaciones") or []) if p.get("precio_elegido_cents") is not None
    ]
    if not con_precio:
        return None
    return max(con_precio, key=lambda p: p.get("subido_en") or "")


def _precio_auto_euros(fila: dict[str, Any]) -> str:
    pub = _publicacion_mas_reciente_con_precio(fila)
    if pub is None:
        return ""
    return finanzas.cents_a_euros(pub["precio_elegido_cents"])


def _plataforma_auto(fila: dict[str, Any]) -> str | None:
    pub = _publicacion_mas_reciente_con_precio(fila)
    if pub is not None:
        return pub.get("plataforma")
    publicaciones = fila.get("publicaciones") or []
    if publicaciones:
        return max(publicaciones, key=lambda p: p.get("subido_en") or "").get("plataforma")
    return None


def _opciones_plataforma(fila: dict[str, Any]) -> tuple[str, ...]:
    """Las plataformas ya vistas en `publicaciones` primero (lo más probable
    que Diego quiera elegir), seguidas de las que falten de la lista base --
    nunca se pierde una opción por no estar en `_OPCIONES_PLATAFORMA_BASE`
    (un valor libre que llegara de otra fuente futura seguiría eligible)."""
    vistas = sorted({p["plataforma"] for p in (fila.get("publicaciones") or [])})
    resto = [p for p in _OPCIONES_PLATAFORMA_BASE if p not in vistas]
    return tuple(vistas) + tuple(resto)


# --------------------------------------------------------------------------
# Acciones -- mutan el store, capturan sus errores tipados (nunca un
# traceback a Diego), nunca un `except Exception: pass` silencioso
# (`decision-making.md` §13). Se llaman DENTRO del cuerpo de un `if
# st.button(...):` (nunca vía `on_click=`), así que `st.error` aquí SÍ se
# pinta en el mismo render -- mismo patrón que
# `ui/export.py::_accion_preparar_fotos` (a diferencia de un callback
# `on_click`, donde `st.error` se descartaría antes del rerun).
# --------------------------------------------------------------------------
def _accion_marcar_vendido(
    store: LoteStore, producto_id: str, precio_euros_texto: str, plataforma: str
) -> bool:
    try:
        precio_cents = finanzas.euros_texto_a_cents(precio_euros_texto)
    except ValueError as exc:
        st.error(f"Precio de venta inválido: {exc}. Escribe un número, p.ej. 19.99.")
        return False
    try:
        store.marcar_vendido(producto_id, precio_cents, plataforma)
    except (StoreError, ProductoNoEncontradoError) as exc:
        logger.exception("No se pudo marcar como vendido el producto %s", producto_id)
        st.error(f"No se pudo marcar como vendido: {exc}")
        return False
    return True


def _accion_deshacer_venta(store: LoteStore, producto_id: str) -> bool:
    try:
        store.deshacer_venta(producto_id)
    except (StoreError, ProductoNoEncontradoError) as exc:
        logger.exception("No se pudo deshacer la venta del producto %s", producto_id)
        st.error(f"No se pudo deshacer la venta: {exc}")
        return False
    return True


def _accion_marcar_devuelta(store: LoteStore, producto_id: str) -> bool:
    try:
        store.marcar_devuelta(producto_id)
    except (StoreError, ProductoNoEncontradoError) as exc:
        logger.exception("No se pudo marcar la devolución del producto %s", producto_id)
        st.error(f"No se pudo marcar la devolución: {exc}")
        return False
    return True


def _accion_exportar_excel(store: LoteStore, filas: list[dict[str, Any]]) -> None:
    destino = store.data_dir / "exports" / finanzas.nombre_export_por_defecto()
    try:
        ruta = finanzas.exportar_excel(filas, destino)
    except OSError as exc:
        logger.exception("No se pudo exportar el Excel de finanzas")
        st.error(f"No se pudo generar el Excel: {exc}")
        return
    st.session_state[_KEY_RUTA_EXCEL] = str(ruta)


# --------------------------------------------------------------------------
# Render de una fila (un producto del ledger).
# --------------------------------------------------------------------------
def _render_marcar_vendido(store: LoteStore, pid: str, fila: dict[str, Any]) -> None:
    key_precio = f"finanzas_{pid}_precio"
    key_plataforma = f"finanzas_{pid}_plataforma"
    opciones = _opciones_plataforma(fila)

    # Aviso (NO bloqueo) si se va a vender con el coste sin informar: al marcar
    # Vendido se CONGELA `coste_snap_cents` con el coste vivo, y si es 0 el
    # beneficio queda fijado como el precio íntegro, sobreestimado y sin vía de
    # corrección salvo Deshacer+revender. No se bloquea porque a veces el coste
    # real ES 0; pero la sobreestimación no debe ser silenciosa. [audit 2026-08-05]
    if not fila.get("coste_cents"):
        st.caption(
            "⚠️ Coste sin informar (0 €): al vender, el beneficio se congelará "
            "como el precio íntegro. Si conoces el coste, ponlo en «3. Ficha» "
            "antes de marcar Vendido."
        )

    if key_precio not in st.session_state:
        st.session_state[key_precio] = _precio_auto_euros(fila)
    if key_plataforma not in st.session_state:
        st.session_state[key_plataforma] = _plataforma_auto(fila) or opciones[0]

    col_precio, col_plataforma, col_boton = st.columns([1, 1, 1])
    with col_precio:
        st.text_input("Precio de venta (€)", key=key_precio)
    with col_plataforma:
        st.selectbox(
            "Plataforma",
            opciones,
            key=key_plataforma,
            format_func=lambda p: _ETIQUETA_PLATAFORMA.get(p, p),
        )
    with col_boton:
        st.write("")  # alinea el botón con los inputs
        if st.button("✅ Vendido", key=f"finanzas_{pid}_btn_vendido", use_container_width=True):
            ok = _accion_marcar_vendido(
                store, pid, st.session_state[key_precio], st.session_state[key_plataforma]
            )
            if ok:
                st.rerun()


def _render_fila(store: LoteStore, fila: dict[str, Any]) -> None:
    pid = fila["producto_id"]
    referencia = fila.get("referencia")
    etiqueta_ref = f"Ref. {referencia}" if referencia is not None else "Ref. —"
    titulo = fila.get("titulo") or "(sin título)"

    st.subheader(f"{etiqueta_ref} — {titulo}")
    lote_id = fila.get("lote_id")
    st.caption(
        f"Producto `{pid[:8]}` · lote `{lote_id[:8] if lote_id else '— (producto borrado)'}` · "
        f"coste {finanzas.cents_a_euros(fila.get('coste_cents', 0)) or '0.00'} €"
    )

    publicaciones = fila.get("publicaciones") or []
    if publicaciones:
        resumen = ", ".join(
            f"{_ETIQUETA_PLATAFORMA.get(p['plataforma'], p['plataforma'])} "
            f"({(p.get('subido_en') or '')[:10]})"
            for p in publicaciones
        )
        st.caption(f"Subido a: {resumen}")
    else:
        st.caption("Todavía no subido a ninguna plataforma.")

    venta = fila.get("venta")
    if venta is None:
        _render_marcar_vendido(store, pid, fila)
    else:
        estado = venta.get("estado")
        precio_txt = finanzas.cents_a_euros(venta.get("precio_final_cents"))
        plataforma_venta = venta.get("plataforma_venta") or "—"
        etiqueta_plataforma_venta = _ETIQUETA_PLATAFORMA.get(plataforma_venta, plataforma_venta)

        if estado == "devuelta":
            st.warning(f"🔴 DEVUELTA — se vendió por {precio_txt} € en {etiqueta_plataforma_venta}")
        else:
            st.success(f"✅ Vendido por {precio_txt} € en {etiqueta_plataforma_venta}")
            beneficio = fila.get("beneficio_bruto_cents")
            if beneficio is not None:
                st.caption(f"Beneficio bruto: {finanzas.cents_a_euros(beneficio)} €")

            # B2 — recordatorio de retirar del OTRO sitio.
            otras_plataformas = sorted(
                {
                    p["plataforma"]
                    for p in publicaciones
                    if p["plataforma"] != plataforma_venta
                }
            )
            for otra in otras_plataformas:
                etiqueta_otra = _ETIQUETA_PLATAFORMA.get(otra, otra)
                st.warning(f"🔴 Recuerda RETIRAR el anuncio de {etiqueta_otra}")

        col_deshacer, col_devolucion = st.columns(2)
        with col_deshacer:
            if st.button(
                "↩️ Deshacer venta",
                key=f"finanzas_{pid}_btn_deshacer",
                use_container_width=True,
            ):
                ok = _accion_deshacer_venta(store, pid)
                if ok:
                    st.rerun()
        with col_devolucion:
            if estado != "devuelta":
                if st.button(
                    "🔴 Devolución",
                    key=f"finanzas_{pid}_btn_devolucion",
                    use_container_width=True,
                ):
                    ok = _accion_marcar_devuelta(store, pid)
                    if ok:
                        st.rerun()

    st.divider()


# --------------------------------------------------------------------------
# Entrada de la pantalla. CROSS-LOTE: no recibe `lote_id`.
# --------------------------------------------------------------------------
def render(store: LoteStore) -> None:
    st.header("5. Finanzas")
    st.caption(
        "Ledger de ventas de TODOS los lotes. Marca «Vendido» cuando se venda un "
        "producto, deshazlo si te equivocaste, o márcalo «Devolución» si vuelve."
    )

    filas = store.cargar_ventas()

    total_vendido = finanzas.total_vendido_cents(filas)
    beneficio_total = finanzas.beneficio_total_cents(filas)

    col_total, col_beneficio = st.columns(2)
    with col_total:
        st.metric("Total vendido", f"{finanzas.cents_a_euros(total_vendido)} €")
    with col_beneficio:
        st.metric("Beneficio bruto total", f"{finanzas.cents_a_euros(beneficio_total)} €")
    st.caption(
        "⚠️ Beneficio bruto (sin comisiones ni envío; coste 0 si no lo informaste)."
    )

    st.divider()

    col_filtro, col_buscar = st.columns([1, 2])
    with col_filtro:
        filtro_estado = st.selectbox(
            "Estado", _OPCIONES_FILTRO_ESTADO, key="finanzas_filtro_estado"
        )
    with col_buscar:
        texto_busqueda = st.text_input(
            "Buscar por referencia o título", key="finanzas_buscar"
        )

    if st.button("📊 Exportar a Excel", key="finanzas_btn_exportar"):
        _accion_exportar_excel(store, filas)

    ruta_excel = st.session_state.get(_KEY_RUTA_EXCEL)
    if ruta_excel:
        st.success("Excel generado (SQLite sigue siendo la verdad; esto es una foto de hoy):")
        st.code(ruta_excel, language=None)

    st.divider()

    filas_filtradas = _filtrar(filas, filtro_estado, texto_busqueda)
    if not filas_filtradas:
        st.info("No hay ningún producto que mostrar con este filtro.")
        return

    for fila in filas_filtradas:
        _render_fila(store, fila)
