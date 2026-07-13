"""app.py — entrypoint de RESELLERMASTER (Streamlit).

SOLO renderiza: decide qué pantalla de `ui/` pintar y qué `lote_id` le
pasa. Ninguna decisión de negocio vive aquí (agrupar, extraer atributos,
fijar precio) — eso vive detrás de las costuras en `core/`
(`.claude/rules/file-organization.md`).

Persistencia: `LoteStore` es la única fuente de verdad del lote.
`st.session_state` aquí sólo guarda qué pantalla/lote tiene Diego
seleccionado en la barra lateral — perder eso en un rerun cuesta un click
para reelegir el lote, no el trabajo de curado (que vive en disco).
"""

from __future__ import annotations

import streamlit as st

from core.store import DEFAULT_DATA_DIR, LoteStore
from ui import confirmacion, ingesta

st.set_page_config(page_title="RESELLERMASTER", layout="wide")

_PANTALLA_INGESTA = "1. Ingesta"
_PANTALLA_CONFIRMACION = "2. Confirmación de grupos"


@st.cache_resource
def _get_store() -> LoteStore:
    """Una única instancia de `LoteStore` por sesión de servidor — evita
    repetir la migración de esquema (una transacción SQLite) en cada
    rerun de Streamlit, que ocurre en cada click."""
    return LoteStore(data_dir=DEFAULT_DATA_DIR)


def _aplicar_navegacion_pendiente() -> None:
    """Si una pantalla acaba de pedir un cambio de pantalla/lote (p.ej.
    Ingesta tras crear un lote nuevo), lo aplica ANTES de instanciar los
    widgets de la barra lateral, para que el cambio se vea en el mismo
    rerun en vez de requerir un click extra de Diego."""
    pendiente = st.session_state.pop("_navegar_a", None)
    if pendiente:
        st.session_state["sb_pantalla"] = pendiente["pantalla"]
        st.session_state["sb_lote_id"] = pendiente["lote_id"]


def main() -> None:
    store = _get_store()
    _aplicar_navegacion_pendiente()

    st.sidebar.title("RESELLERMASTER")
    pantalla = st.sidebar.radio(
        "Pantalla", [_PANTALLA_INGESTA, _PANTALLA_CONFIRMACION], key="sb_pantalla"
    )

    st.sidebar.divider()
    st.sidebar.caption("Lote")
    lotes = store.listar_lotes()
    lote_id: str | None = None
    if lotes:
        etiquetas = {
            lote["id"]: (
                f"{lote['nombre']} — {lote['n_fotos']} fotos, "
                f"{lote['n_confirmados']}/{lote['n_productos']} confirmados"
            )
            for lote in lotes
        }
        ids = list(etiquetas.keys())
        if st.session_state.get("sb_lote_id") not in ids:
            st.session_state["sb_lote_id"] = ids[0]  # más reciente primero
        lote_id = st.sidebar.selectbox(
            "Lote actual",
            ids,
            format_func=lambda i: etiquetas[i],
            key="sb_lote_id",
        )
    else:
        st.sidebar.info("Todavía no hay ningún lote. Créalo en «Ingesta».")

    if pantalla == _PANTALLA_INGESTA:
        nuevo_lote_id = ingesta.render(store)
        if nuevo_lote_id is not None:
            st.session_state["_navegar_a"] = {
                "pantalla": _PANTALLA_CONFIRMACION,
                "lote_id": nuevo_lote_id,
            }
            st.rerun()
    else:
        if lote_id is None:
            st.warning("No hay ningún lote todavía. Ve a «Ingesta» para crear el primero.")
        else:
            confirmacion.render(store, lote_id)


if __name__ == "__main__":
    main()
