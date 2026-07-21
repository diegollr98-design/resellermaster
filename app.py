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

import os
from pathlib import Path

import streamlit as st

from core.store import DEFAULT_DATA_DIR, LoteStore
from ui import curar, export, ficha, finanzas, ingesta


def _cargar_env() -> None:
    """Carga `.env` (raíz del repo) en `os.environ` si existe, sin machacar
    una variable ya puesta en el entorno. La extracción de atributos
    (`ui/ficha.py` → `core/llm.py`) necesita `ANTHROPIC_API_KEY`; sin este
    cargador Diego tendría que exportarla a mano cada vez que arranca la
    app. Parser mínimo (KEY=VALUE, ignora comentarios y líneas en blanco) —
    no se añade una dependencia por esto. `.env` está en `.gitignore`; la
    clave NUNCA se loguea ni se commitea."""
    ruta = Path(__file__).resolve().parent / ".env"
    if not ruta.exists():
        return
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave and clave not in os.environ:
                os.environ[clave] = valor
    except OSError:
        # Un .env ilegible no puede tumbar el arranque: la app sigue
        # (la extracción avisará luego con ApiKeyFaltanteError si hace falta).
        pass


_cargar_env()

st.set_page_config(page_title="RESELLERMASTER", layout="wide")

_PANTALLA_INGESTA = "1. Ingesta"
_PANTALLA_CONFIRMACION = "2. Curar agrupación"
_PANTALLA_FICHA = "3. Ficha"
_PANTALLA_EXPORT = "4. Export"
_PANTALLA_FINANZAS = "5. Finanzas"


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
        # Esto SOLO es legal porque `main()` llama a esta función ANTES
        # de instanciar `st.sidebar.radio(key="sb_pantalla")` y
        # `st.sidebar.selectbox(key="sb_lote_id")` más abajo — igual que
        # el bug de `ui/confirmacion.py::_limpiar_seleccion` (escribir la
        # key de un widget DESPUÉS de instanciarlo en el mismo rerun
        # lanza `StreamlitAPIException`). El ORDEN de las líneas de
        # `main()` es una condición de CORRECCIÓN, no un detalle de
        # estilo: si algún día se instancian esos widgets antes de llamar
        # a `_aplicar_navegacion_pendiente()`, esto revienta igual que
        # aquel bug. No reordenar sin mover esto también.
        st.session_state["sb_pantalla"] = pendiente["pantalla"]
        st.session_state["sb_lote_id"] = pendiente["lote_id"]


def main() -> None:
    store = _get_store()
    _aplicar_navegacion_pendiente()

    st.sidebar.title("RESELLERMASTER")
    pantalla = st.sidebar.radio(
        "Pantalla",
        [
            _PANTALLA_INGESTA,
            _PANTALLA_CONFIRMACION,
            _PANTALLA_FICHA,
            _PANTALLA_EXPORT,
            _PANTALLA_FINANZAS,
        ],
        key="sb_pantalla",
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
        # Misma condición de CORRECCIÓN que en `_aplicar_navegacion_pendiente`:
        # legal SOLO porque esto corre antes de la línea de abajo que
        # instancia `st.sidebar.selectbox(key="sb_lote_id", ...)`.
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
    elif pantalla == _PANTALLA_CONFIRMACION:
        if lote_id is None:
            st.warning("No hay ningún lote todavía. Ve a «Ingesta» para crear el primero.")
        else:
            curar.render(store, lote_id)
    elif pantalla == _PANTALLA_FICHA:
        if lote_id is None:
            st.warning("No hay ningún lote todavía. Ve a «Ingesta» para crear el primero.")
        else:
            ficha.render(store, lote_id)
    elif pantalla == _PANTALLA_EXPORT:
        if lote_id is None:
            st.warning("No hay ningún lote todavía. Ve a «Ingesta» para crear el primero.")
        else:
            export.render(store, lote_id)
    else:
        # Finanzas es CROSS-LOTE (`ui/finanzas.py`): el ledger de ventas
        # abarca todos los lotes, así que — a diferencia de las otras 4
        # pantallas — NO depende de `lote_id` ni se bloquea si no hay
        # ningún lote seleccionado en la barra lateral.
        finanzas.render(store)


if __name__ == "__main__":
    main()
