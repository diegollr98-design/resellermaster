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

    # ORDEN = EL DEL FORMULARIO DE WALLAPOP, verbatim de Diego (2026-07-21),
    # para rellenar de arriba abajo en paralelo con la web sin scrollear:
    #   Fotos -> Categoría -> Título -> Descripción -> (Marca/Talla) -> Estado
    #   -> PRECIO -> Medidas(desperfectos) -> Tamaño(envío).
    # (El "Resumen del producto" de arriba de Wallapop es el mismo título.)
    # El orden de Vinted lo afinará Diego; por ahora usa el mismo.
    campos_por_nombre = {c.nombre: c for c in payload.campos}

    def _campo(nombre: str) -> None:
        campo = campos_por_nombre.get(nombre)
        if campo is not None:
            _render_campo_exportado(campo)
            st.divider()

    # 0. RESUMEN DEL PRODUCTO -- Wallapop lo pide ARRIBA DEL TODO (máx 50
    # chars); es el mismo título, se repite aquí para pegarlo primero sin
    # scrollear (Diego, 2026-07-21). Vinted no tiene "Resumen".
    if plataforma == "wallapop":
        st.subheader("Resumen del producto")
        st.caption(_contador(payload.titulo, schema.LimiteTexto(maximo=50)))
        st.code(payload.titulo, language=None)
        st.divider()

    # 1. FOTOS (+ preparar) -- van arriba en el formulario de Wallapop.
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

    # 2. CATEGORÍA
    st.divider()
    _render_candidatas_categoria(payload)

    # 3-4. TÍTULO + DESCRIPCIÓN (el título sirve también para el "Resumen").
    st.divider()
    st.subheader("Título + descripción")
    st.caption(_contador(payload.titulo, _limite_texto(plataforma, "title")))
    st.code(payload.titulo, language=None)
    st.caption(_contador(payload.descripcion, _limite_texto(plataforma, "description")))
    st.code(payload.descripcion, language=None)
    st.caption("Título + descripción juntos, para pegar de una vez:")
    st.code(f"{payload.titulo}\n\n{payload.descripcion}", language=None)

    # 5. (MARCA/TALLA, atributos de moda), 6. ESTADO
    st.divider()
    for nombre in ("marca", "talla", "estado"):
        _campo(nombre)

    # 7. PRECIO
    _render_precio(producto, plataforma)

    # 8. MEDIDAS (desperfectos), 9. TAMAÑO (envío)
    st.divider()
    for nombre in ("desperfectos", "composicion", "tramo_peso_kg", "package_size"):
        _campo(nombre)


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


def _render_una_tasacion(tas: pricing.Tasacion) -> None:
    """Un bloque de resultado para UNA combinación de palabras clave."""
    st.markdown(f"**«{tas.terminos}»**")
    if tas.mediana is not None:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.metric("Mediana", f"{tas.mediana:.0f} €", label_visibility="collapsed")
        with c2:
            st.caption(f"**{tas.mediana:.0f} €** · rango {tas.minimo:.0f}–{tas.maximo:.0f} € · "
                       f"{tas.n} parecidos")
    else:
        st.caption(f"— {tas.motivo}")
    if tas.url_busqueda:
        st.link_button("🔗 Abrir la búsqueda en Wallapop", tas.url_busqueda)
    if tas.comparables:
        with st.expander(f"Ver los {tas.n} comparables (ábrelos y compruébalo)"):
            for comp in tas.comparables:
                st.markdown(f"- **{comp.precio:.0f} €** — [{comp.titulo or comp.url}]({comp.url})")


def _render_precio(producto: dict, plataforma: str) -> None:
    st.subheader("Precio — mediana de parecidos")
    campos = producto.get("campos", {}).get("campos", {})

    st.caption(
        "Una línea por combinación de palabras clave. **Edítalas**: añade las "
        "que veas en el producto (marca, modelo, tipo…) y borra las que no "
        "encajen. Al buscar, sale la mediana de parecidos de CADA línea, para "
        "que compares y te quedes con la que de verdad casa (abre sus "
        "comparables para verificar). El precio nunca sale de un modelo — sale "
        "de anuncios reales que puedes abrir."
    )

    # Semilla editable: sugerencias generosas (incluye la marca aunque sea
    # inferida; con título como respaldo si no hay marca/modelo).
    sugeridas = pricing.sugerir_terminos(campos)
    key_txt = f"precio_terminos_{producto['id']}_{plataforma}"
    texto = st.text_area(
        "Palabras clave (una combinación por línea)",
        value="\n".join(sugeridas),
        key=key_txt,
        height=120,
    )
    terminos = [ln for ln in texto.splitlines() if ln.strip()]

    key_tas = f"tasaciones_{producto['id']}_{plataforma}"

    def _buscar() -> None:
        with st.spinner(f"Leyendo {len(terminos)} búsquedas públicas de Wallapop…"):
            try:
                st.session_state[key_tas] = pricing.tasar_terminos(terminos, _buscador())
            except Exception as exc:  # noqa: BLE001 — la red nunca tumba la pantalla
                logger.exception("Fallo al tasar el producto %s", producto["id"])
                st.error(f"No se pudo leer la búsqueda: {exc}")

    # AUTO-BUSCAR una vez al abrir el producto (idea de Diego: menos clics --
    # que salga ya al entrar en «4. Export», sin darle en cada producto). Sólo
    # la primera vez (gate por `session_state`); no se repite en cada rerun ni
    # machaca el endpoint. Tras editar las palabras, el botón re-busca.
    if key_tas not in st.session_state and terminos:
        _buscar()

    if st.button(
        "🔎 Buscar de nuevo (con las palabras de arriba)",
        key=f"btn_precio_{producto['id']}_{plataforma}",
    ):
        if not terminos:
            st.warning("Escribe al menos una combinación de palabras clave.")
        else:
            _buscar()

    tasaciones: list[pricing.Tasacion] | None = st.session_state.get(key_tas)
    if not tasaciones:
        return

    st.caption(f"⚠️ {pricing.NOTA_PRECIO_PEDIDO}")
    for tas in tasaciones:
        st.divider()
        _render_una_tasacion(tas)


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

    tabs = st.tabs([_ETIQUETA_PLATAFORMA[p] for p in _PLATAFORMAS])
    for plataforma, tab in zip(_PLATAFORMAS, tabs):
        with tab:
            _render_plataforma(store, lote_id, producto, fotos_por_id, plataforma)
