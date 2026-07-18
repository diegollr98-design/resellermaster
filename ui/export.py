"""ui/export.py — EL EXPORT: de "ficha que Diego confirmó" a algo que se
pega en Wallapop/Vinted en segundos. Cuarta pantalla (tras Ingesta, Curar
agrupación y Ficha).

ESTA PANTALLA ES EL PRODUCTO. El export mide ~66% del tiempo de Diego por
producto (~285 s de los ~375 s totales, panel del 2026-07-16) y hasta hoy lo
hacía 100% a mano. Cada clic que ahorra ES el valor de esta fase — optimiza
segundos-hasta-publicar, no elegancia de código.

Toda la lógica de negocio (traducciones a los literales de cada plataforma,
sanitizador de texto, bloqueos) vive en `core/export.py` (que a su vez usa
`core/schema.py` y `core/images.py`). Este módulo SÓLO renderiza —
`.claude/rules/file-organization.md`: "Nunca meter lógica de negocio en
app.py/ui/".

## La defensa con dientes de esta pantalla (`truth-loop.md` §A.2, `[INC-013]`)
Un producto cuya ficha NO está confirmada por Diego **no se exporta**. No
hay botón de "exportar igualmente": el giro null->mejor-intento sólo es
legítimo porque Diego revisó cada campo con el píxel delante antes de
publicar. Saltarse eso desde el export sería tirar la premisa entera.

## Persistencia (`decision-making.md` §13, `[INC-006]`)
El estado se relee de `store.cargar_lote()` en cada render, nunca de
`st.session_state` — igual que `ui/ficha.py` y `ui/curar.py`. Esta pantalla
es de SÓLO LECTURA salvo el botón "Preparar fotos", que copia ficheros a
disco vía `core.images.exportar_producto` (ya existente) y sólo recuerda en
`session_state` la última ruta preparada, para pintar el resultado —
perderlo en un rerun cuesta un vistazo a la carpeta, no trabajo de Diego.
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from core import export, images, pricing, schema
from core.store import LoteStore

logger = logging.getLogger(__name__)

_PLATAFORMAS: tuple[str, ...] = ("wallapop", "vinted")
_ETIQUETA_PLATAFORMA: dict[str, str] = {"wallapop": "Wallapop", "vinted": "Vinted"}


# --------------------------------------------------------------------------
# Estado de "extraído" / "confirmado" — mismas comprobaciones que
# `ui/ficha.py` (misma forma del dict `producto["campos"]`), duplicadas aquí
# a propósito: cada pantalla es un módulo desechable e independiente
# (`file-organization.md`); la fuente de verdad es el store, no la UI.
# --------------------------------------------------------------------------
def _esta_extraido(producto: dict) -> bool:
    return isinstance(producto.get("campos"), dict) and "campos" in producto["campos"]


def _ficha_confirmada(producto: dict) -> bool:
    return isinstance(producto.get("campos"), dict) and producto["campos"].get("confirmada") is True


# --------------------------------------------------------------------------
# Límite de longitud de un campo de texto para una plataforma — leído de las
# tablas declarativas de `core/schema.py` (Costura 3), nunca reinventado
# aquí. Sólo para pintar el contador; no valida nada (eso ya lo hizo
# `core.export.construir_payload` vía `schema.validar_texto`).
# --------------------------------------------------------------------------
def _limite_texto(plataforma: str, nombre_campo: str) -> "schema.LimiteTexto | None":
    tabla = schema.VINTED_CAMPOS if plataforma == "vinted" else schema.WALLAPOP_CAMPOS
    for campo_schema in tabla:
        if campo_schema.nombre == nombre_campo:
            return campo_schema.limite
    return None


def _contador(texto: str, limite: "schema.LimiteTexto | None") -> str:
    n = len(texto)
    if limite is None or limite.maximo is None:
        return f"{n} caracteres"
    marca = "✅" if n <= limite.maximo else "⚠️"
    return f"{marca} {n}/{limite.maximo} caracteres"


# --------------------------------------------------------------------------
# Categoría: HOJAS CANDIDATAS. La máquina PROPONE 2-3 hojas (de "navega 859
# a mano" a "1 clic"); Diego ELIGE. Nunca se auto-rellena una hoja
# (`decision-making.md` §18: una hoja mal = anuncio oculto = venta perdida
# silenciosa). Si ninguna encaja, el fallback es navegar a mano en la
# plataforma — exactamente lo de hoy, nunca peor.
# --------------------------------------------------------------------------
def _render_candidatas_categoria(payload: export.PayloadPlataforma) -> None:
    st.subheader("Categoría — elige UNA hoja")
    if payload.categoria_snapshot:
        st.caption(
            f"Árbol de {_ETIQUETA_PLATAFORMA[payload.plataforma]} · snapshot "
            f"{payload.categoria_snapshot} · refresca con "
            "`python -m core.categorias --refrescar`"
        )
    if not payload.candidatas_categoria:
        st.caption(
            "— ninguna hoja candidata encaja con este producto: navega el árbol "
            "a mano en la plataforma (como hasta ahora). —"
        )
        return
    st.caption(
        "Sugerencias ordenadas por parecido con el título/descripción. "
        "**No están elegidas** — elige tú la que corresponda (fíjate en el "
        "género y el subtipo); una hoja equivocada oculta el anuncio."
    )
    for cand in payload.candidatas_categoria:
        st.code(cand.hoja.ruta_completa, language=None)


# --------------------------------------------------------------------------
# Un campo estructurado ya traducido (o crudo con su nota).
# --------------------------------------------------------------------------
def _render_campo_exportado(campo: export.CampoExportado) -> None:
    st.markdown(f"**{campo.etiqueta}** _(campo interno: {campo.nombre})_")
    if campo.valor is None:
        st.caption("— elígelo en la plataforma —")
    else:
        st.code(campo.valor, language=None)
    st.caption("✅ traducido — pégalo tal cual" if campo.traducido else "⚠️ valor crudo — revísalo en la plataforma")
    if campo.nota:
        st.caption(campo.nota)


# --------------------------------------------------------------------------
# Una foto del payload, con su número de orden encima. Un fichero que ya no
# existe (borrado a mano tras confirmar) no puede tumbar la pantalla —
# mismo criterio que `ui/ficha.py::_render_recorte`.
# --------------------------------------------------------------------------
def _render_foto_export(ruta: Path, numero: int) -> None:
    st.caption(f"**{numero}**")
    if not ruta.exists():
        st.caption(f"⚠️ no encontrada: {ruta.name}")
        return
    try:
        st.image(str(ruta), use_container_width=True)
    except Exception as exc:  # noqa: BLE001 — frontera "una foto del producto".
        logger.exception("No se pudo pintar la foto de export %s", ruta)
        st.caption(f"⚠️ no se pudo abrir {ruta.name}: {exc}")


# --------------------------------------------------------------------------
# Botón "Preparar fotos" → copia a disco vía `core.images.exportar_producto`
# (ya existe, no se reescribe). Corre en el cuerpo del script (no en un
# on_click), igual que `ui/ficha.py::_accion_confirmar_ficha` — así
# `st.error`/`st.success` son válidos y el resultado se puede guardar en
# `session_state` para sobrevivir al rerun que dispara el propio botón.
# --------------------------------------------------------------------------
def _accion_preparar_fotos(
    store: LoteStore, lote_id: str, producto_id: str, plataforma: str, payload: export.PayloadPlataforma
) -> Path | None:
    if not payload.fotos:
        st.error("No hay fotos que preparar para esta plataforma.")
        return None
    directorio_destino = store.data_dir / "exports" / lote_id / plataforma
    try:
        resultado = images.exportar_producto(
            list(payload.fotos), directorio_destino, plataforma, producto_id[:8]
        )
    except (images.ImagenError, OSError) as exc:
        logger.exception(
            "No se pudieron preparar las fotos del producto %s para %s", producto_id, plataforma
        )
        st.error(f"No se pudieron preparar las fotos: {exc}")
        return None
    return resultado.directorio_producto


# --------------------------------------------------------------------------
# Una plataforma completa (una pestaña).
# --------------------------------------------------------------------------
def _render_plataforma(store: LoteStore, lote_id: str, producto: dict, fotos_por_id: dict, plataforma: str) -> None:
    try:
        payload = export.construir_payload(producto, fotos_por_id, plataforma)
    except export.ExportBloqueadoError as exc:
        st.error(
            f"No se puede exportar a {_ETIQUETA_PLATAFORMA[plataforma]} tal cual:\n\n"
            + "\n".join(f"- {v}" for v in exc.violaciones)
        )
        return

    for aviso in payload.avisos:
        st.warning(f"⚠️ {aviso}")

    st.subheader("Título + descripción")
    st.caption(_contador(payload.titulo, _limite_texto(plataforma, "title")))
    st.code(payload.titulo, language=None)
    st.caption(_contador(payload.descripcion, _limite_texto(plataforma, "description")))
    st.code(payload.descripcion, language=None)
    st.caption("Título + descripción juntos, para pegar de una vez:")
    st.code(f"{payload.titulo}\n\n{payload.descripcion}", language=None)

    st.divider()
    _render_candidatas_categoria(payload)

    st.divider()
    st.subheader("Campos")
    for campo in payload.campos:
        _render_campo_exportado(campo)
        st.divider()

    st.subheader("Fotos")
    if payload.fotos:
        columnas = st.columns(min(len(payload.fotos), 5))
        for i, ruta in enumerate(payload.fotos):
            with columnas[i % len(columnas)]:
                _render_foto_export(ruta, i + 1)
    else:
        st.caption("— sin fotos —")
    if payload.fotos_excluidas:
        st.caption(
            f"{len(payload.fotos_excluidas)} foto(s) no caben en el límite de "
            f"{_ETIQUETA_PLATAFORMA[plataforma]} — no se incluyen."
        )

    st.divider()
    key_boton = f"export_fotos_{producto['id']}_{plataforma}"
    key_ruta = f"export_ruta_{producto['id']}_{plataforma}"
    if st.button(
        f"📁 Preparar fotos para {_ETIQUETA_PLATAFORMA[plataforma]}",
        key=key_boton,
        use_container_width=True,
    ):
        ruta = _accion_preparar_fotos(store, lote_id, producto["id"], plataforma, payload)
        if ruta is not None:
            st.session_state[key_ruta] = str(ruta)

    ruta_preparada = st.session_state.get(key_ruta)
    if ruta_preparada:
        st.success(f"Fotos copiadas — ábrela y arrástralas a {_ETIQUETA_PLATAFORMA[plataforma]}:")
        st.code(ruta_preparada, language=None)


# --------------------------------------------------------------------------
# Precio: MEDIANA de comparables PARECIDOS (core/pricing.py tasar). Nunca "el
# precio de tu producto": Diego abre los enlaces y decide. Bajo demanda (un
# botón), no en cada render -- leer la búsqueda pública es una llamada de red.
# `Campo`/`Evidencia` se reconstruyen en `pricing.atributos_desde_campos`.
# --------------------------------------------------------------------------
# Un buscador por sesión (cachea por términos; no machaca el endpoint).
def _buscador() -> pricing.BuscadorWallapop:
    if "_buscador_precio" not in st.session_state:
        st.session_state["_buscador_precio"] = pricing.BuscadorWallapop()
    return st.session_state["_buscador_precio"]


def _render_precio(producto: dict) -> None:
    st.subheader("Precio — mediana de parecidos")
    campos = producto.get("campos", {}).get("campos", {})
    atributos = pricing.atributos_desde_campos(campos)

    key_tas = f"tasacion_{producto['id']}"
    if st.button("🔎 Buscar comparables en Wallapop", key=f"btn_precio_{producto['id']}"):
        with st.spinner("Leyendo la búsqueda pública de Wallapop…"):
            try:
                st.session_state[key_tas] = pricing.tasar(atributos, _buscador())
            except Exception as exc:  # noqa: BLE001 — la red nunca tumba la pantalla
                logger.exception("Fallo al tasar el producto %s", producto["id"])
                st.error(f"No se pudo leer la búsqueda: {exc}")

    tas: pricing.Tasacion | None = st.session_state.get(key_tas)
    if tas is None:
        st.caption(
            "El precio nunca sale de un modelo: sale de comparables reales que "
            "puedes abrir y comprobar. Pulsa para leer la búsqueda pública."
        )
        return

    if tas.terminos:
        st.caption(f"Búsqueda: «{tas.terminos}»")
    if tas.mediana is not None:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.metric("Mediana de parecidos", f"{tas.mediana:.0f} €")
        with c2:
            st.caption(f"Rango observado: {tas.minimo:.0f} – {tas.maximo:.0f} € · "
                       f"{tas.n} anuncios parecidos")
        st.caption(f"⚠️ {tas.motivo}")
    else:
        st.warning(f"Sin mediana: {tas.motivo}")

    if tas.url_busqueda:
        st.caption("Ábrela entera para verlos todos:")
        st.code(tas.url_busqueda, language=None)
    if tas.comparables:
        with st.expander(f"Ver los {tas.n} comparables usados (ábrelos y compruébalo)"):
            for comp in tas.comparables:
                st.markdown(f"- **{comp.precio:.0f} €** — [{comp.titulo or comp.url}]({comp.url})")


# --------------------------------------------------------------------------
# Entrada de la pantalla.
# --------------------------------------------------------------------------
def render(store: LoteStore, lote_id: str) -> None:
    estado = store.cargar_lote(lote_id)

    st.header("4. Export")
    st.caption(f"Lote «{estado['lote']['nombre']}».")
    st.caption(
        "Cada bloque tiene su botón de copiar (`st.code`). Título+descripción, "
        "cada campo estructurado y las fotos ya en el límite y el orden de "
        "cada plataforma — nada que teclear a mano."
    )

    confirmados_agrupacion = [p for p in estado["productos"] if p["confirmado"]]
    if not confirmados_agrupacion:
        st.info(
            "No hay ningún producto con la agrupación confirmada. Confírmala primero "
            "en «Curar agrupación»."
        )
        return

    fotos_por_id = {f["id"]: f for f in estado["fotos"]}

    etiquetas = {
        p["id"]: f"Producto `{p['id'][:8]}` — {len(p['fotos'])} foto(s)" for p in confirmados_agrupacion
    }
    ids = list(etiquetas.keys())
    pid = st.selectbox(
        "Producto",
        ids,
        format_func=lambda i: etiquetas[i],
        key="export_producto_id",
    )
    producto = next(p for p in confirmados_agrupacion if p["id"] == pid)

    if not _ficha_confirmada(producto):
        if not _esta_extraido(producto):
            motivo = "todavía no tiene los atributos extraídos"
        else:
            motivo = "tiene los atributos extraídos pero SIN CONFIRMAR"
        st.warning(
            f"Este producto {motivo}. Confirma la ficha en «3. Ficha» antes de "
            "exportar — el export nunca publica un valor que no hayas visto "
            "(truth-loop.md §A.2). No hay atajo para saltarse esto."
        )
        return

    _render_precio(producto)
    st.divider()

    tabs = st.tabs([_ETIQUETA_PLATAFORMA[p] for p in _PLATAFORMAS])
    for plataforma, tab in zip(_PLATAFORMAS, tabs):
        with tab:
            _render_plataforma(store, lote_id, producto, fotos_por_id, plataforma)
