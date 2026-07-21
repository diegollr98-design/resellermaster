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

## Persistencia (`decision-making.md` §13/§19, `[INC-006]`, Fase 5 FINANZAS)
El estado se relee de `store.cargar_lote()`/`store.cargar_ventas()` en cada
render, nunca sólo de `st.session_state` — igual que `ui/ficha.py` y
`ui/curar.py`. Esta pantalla ESCRIBE en tres sitios, todos vía `core/store.py`
(ya implementado, esta pantalla sólo lo consume) y ninguno vive sólo en
`session_state`:
- **"Preparar fotos"**: copia ficheros a disco vía `core.images.exportar_producto`
  y abre la carpeta (`os.startfile`, Windows-only, guardado con `hasattr`) —
  sólo recuerda en `session_state` la última ruta, para pintar el resultado;
  perderlo en un rerun cuesta un vistazo a la carpeta, no trabajo de Diego.
- **Referencia** (`store.asignar_referencia`, idempotente): se garantiza al
  entrar en esta pantalla y se inyecta como "Ref. N" en la descripción de
  AMBAS plataformas (`core/export.py::_inyectar_referencia`) — la llave que
  Diego imprime para localizar la venta.
- **"Subido"** (`store.registrar_subido`, idempotente por plataforma):
  congela el precio elegido + la tasación (mediana/comparables) vistos en
  pantalla al pulsar. El estado "¿ya está subido?" se relee del DISCO en
  cada render (`_estado_publicacion`), nunca sólo de `session_state` — un
  rerun no puede hacer parecer que se perdió una publicación real.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import streamlit as st

from core import export, images, pricing, schema
from core.store import LoteStore, StoreError

logger = logging.getLogger(__name__)

_PLATAFORMAS: tuple[str, ...] = ("wallapop", "vinted")
_ETIQUETA_PLATAFORMA: dict[str, str] = {"wallapop": "Wallapop", "vinted": "Vinted"}

# ORDEN de los bloques = el del FORMULARIO de cada plataforma (verbatim de
# Diego, 2026-07-21), para rellenar de arriba abajo en paralelo con la web sin
# scrollear. Los tokens especiales: "resumen"/"fotos"/"categoria"/"titulo_desc"/
# "precio"; el resto son nombres de `CampoExportado` (envío = "tramo_peso_kg" en
# Wallapop, "package_size" en Vinted; "desperfectos" ≈ el campo "Medidas" del
# formulario, que no producimos como dimensiones). Un campo ausente no pinta nada.
_ORDEN_BLOQUES: dict[str, tuple[str, ...]] = {
    # Resumen -> Fotos -> Categoría -> Título -> Descripción -> Marca/Talla ->
    # Estado -> Precio -> Medidas -> Tamaño.
    "wallapop": (
        "resumen", "fotos", "categoria", "titulo_desc",
        "marca", "talla", "estado", "precio", "desperfectos", "tramo_peso_kg",
    ),
    # Fotos -> Título -> Descripción -> Categoría -> Marca -> Talla -> Medidas ->
    # Estado -> Color -> Material -> Precio -> Envío.
    "vinted": (
        "fotos", "titulo_desc", "categoria",
        "marca", "talla", "desperfectos", "estado", "color", "composicion",
        "precio", "package_size",
    ),
}


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
    # Abrir la carpeta: ahorra el "buscarla a mano" (Diego pidió esto explícitamente,
    # Fase 5 FINANZAS). SOLO abre una carpeta LOCAL -- no toca ninguna plataforma,
    # no roza la prohibición de automatizar publicación (`architecture.md`).
    # `os.startfile` es Windows-only (no existe en Linux/Mac): el `hasattr` evita
    # un `AttributeError` en cualquier otro SO, incluido el que corre `pytest`.
    # Un fallo al abrir la carpeta (permiso, ruta rara) NUNCA debe tumbar la
    # pantalla -- las fotos YA están copiadas, lo único que se pierde es el
    # atajo visual, así que se loguea y se sigue (`decision-making.md` §13:
    # esto SÍ está marcado -- no es un fallback silencioso, es una comodidad
    # best-effort sobre un resultado que ya se logró).
    if hasattr(os, "startfile"):
        try:
            os.startfile(str(resultado.directorio_producto))  # type: ignore[attr-defined]
        except OSError as exc:
            logger.warning(
                "No se pudo abrir la carpeta %s (las fotos sí se copiaron): %s",
                resultado.directorio_producto,
                exc,
            )
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

    campos_por_nombre = {c.nombre: c for c in payload.campos}

    def _resumen() -> None:
        # Wallapop pide el "Resumen del producto" arriba del todo (máx 50 chars);
        # es el mismo título, repetido para pegarlo primero sin scrollear.
        st.subheader("Resumen del producto")
        st.caption(_contador(payload.titulo, schema.LimiteTexto(maximo=50)))
        st.code(payload.titulo, language=None)
        st.divider()

    def _fotos() -> None:
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
        st.divider()

    def _titulo_desc() -> None:
        st.subheader("Título + descripción")
        st.caption(_contador(payload.titulo, _limite_texto(plataforma, "title")))
        st.code(payload.titulo, language=None)
        st.caption(_contador(payload.descripcion, _limite_texto(plataforma, "description")))
        st.code(payload.descripcion, language=None)
        st.caption("Título + descripción juntos, para pegar de una vez:")
        st.code(f"{payload.titulo}\n\n{payload.descripcion}", language=None)
        st.divider()

    def _paso(token: str) -> None:
        if token == "resumen":
            _resumen()
        elif token == "fotos":
            _fotos()
        elif token == "categoria":
            _render_candidatas_categoria(payload)
            st.divider()
        elif token == "titulo_desc":
            _titulo_desc()
        elif token == "precio":
            _render_precio(producto, plataforma)
            st.divider()
            _render_subida(store, producto, plataforma)
            st.divider()
        else:  # un CampoExportado por nombre (marca/talla/estado/color/…/envío)
            campo = campos_por_nombre.get(token)
            if campo is not None:
                _render_campo_exportado(campo)
                st.divider()

    for token in _ORDEN_BLOQUES[plataforma]:
        _paso(token)


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

    # -- Precio FINAL a publicar --------------------------------------------
    # Diego CONFIRMA/EDITA el número final (`truth-loop.md` §D: el precio
    # nunca lo fija la máquina sola). Se sugiere -- nunca se impone -- la
    # mediana de la PRIMERA combinación con resultado como punto de partida.
    # Este valor (en céntimos) + la tasación elegida (snapshot, para
    # `registrar_subido`) se guardan en `session_state` SIEMPRE, incluso sin
    # tasaciones todavía, para que "Subido" (renderizado después, fuera de
    # esta función) siempre pueda leerlos.
    elegida = next((t for t in (tasaciones or []) if t.mediana is not None), None)
    key_precio_final = f"precio_final_{producto['id']}_{plataforma}"
    key_precio_final_cents = f"precio_final_cents_{producto['id']}_{plataforma}"
    key_tasacion_elegida = f"tasacion_elegida_{producto['id']}_{plataforma}"
    # Sólo se siembra si la key FALTA -- misma regla que `ui/ficha.py::
    # _sembrar_coste`: si ya está, es la edición viva de Diego (o la de un
    # rerun anterior) y pisarla perdería tecleo en curso sin necesidad.
    if key_precio_final not in st.session_state and elegida is not None:
        st.session_state[key_precio_final] = round(float(elegida.mediana), 2)
    st.divider()
    st.markdown("**Precio final a publicar**")
    st.caption(
        "Edítalo si quieres — es el número que se congela (junto con esta "
        "tasación) al pulsar «Subido», más abajo."
    )
    precio_final_euros = st.number_input(
        "Precio final (€)",
        min_value=0.0,
        step=1.0,
        format="%.2f",
        key=key_precio_final,
        label_visibility="collapsed",
    )
    st.session_state[key_precio_final_cents] = (
        round(precio_final_euros * 100) if precio_final_euros and precio_final_euros > 0 else None
    )
    st.session_state[key_tasacion_elegida] = asdict(elegida) if elegida is not None else None

    if not tasaciones:
        return

    st.caption(f"⚠️ {pricing.NOTA_PRECIO_PEDIDO}")
    for tas in tasaciones:
        st.divider()
        _render_una_tasacion(tas)


# --------------------------------------------------------------------------
# "Subido": registra la publicación + congela el snapshot de precio/tasación
# (superficie `persistencia`, `core/store.py::registrar_subido`, YA
# implementada -- este módulo SÓLO la consume). Se relee del DISCO en cada
# render (`store.cargar_ventas()`), nunca sólo de `session_state`: un rerun
# de Streamlit no puede hacer PARECER que se perdió una publicación que sí
# está en la BD (`decision-making.md` §19, `[INC-029]`/`[INC-030]`) -- ni al
# revés, que el botón parezca funcionar sin haber escrito nada.
# --------------------------------------------------------------------------
def _estado_publicacion(store: LoteStore, producto_id: str, plataforma: str) -> dict[str, Any] | None:
    """`None` si el producto nunca se marcó "Subido" a `plataforma`; si no,
    el dict de la publicación (`plataforma`, `subido_en`,
    `precio_elegido_cents`, `tasacion`).

    `store.cargar_ventas()` es CROSS-LOTE (la única vía pública que expone
    `publicaciones` por producto; `cargar_lote` no las trae) -- filtrar aquí
    es correcto y barato para el volumen de este proyecto (~7
    productos/lote); no justifica tocar `store.py` para esta tarea."""
    for fila in store.cargar_ventas():
        if fila["producto_id"] != producto_id:
            continue
        for pub in fila["publicaciones"]:
            if pub["plataforma"] == plataforma:
                return pub
    return None


def _accion_subido(store: LoteStore, producto_id: str, plataforma: str) -> None:
    """El precio final y la tasación elegida los deja `_render_precio` en
    `session_state` (misma fila, misma pestaña) -- se leen aquí, nunca se
    recalculan: es el snapshot que Diego vio en pantalla al pulsar."""
    precio_cents = st.session_state.get(f"precio_final_cents_{producto_id}_{plataforma}")
    tasacion = st.session_state.get(f"tasacion_elegida_{producto_id}_{plataforma}")
    try:
        store.registrar_subido(
            producto_id, plataforma, precio_elegido_cents=precio_cents, tasacion=tasacion
        )
    except StoreError as exc:
        logger.exception(
            "No se pudo registrar 'Subido' del producto %s en %s", producto_id, plataforma
        )
        st.error(f"No se pudo registrar la subida: {exc}")


def _render_subida(store: LoteStore, producto: dict, plataforma: str) -> None:
    st.subheader("Publicación")
    key_boton = f"export_subido_{producto['id']}_{plataforma}"
    if st.button(
        f"✅ Subido a {_ETIQUETA_PLATAFORMA[plataforma]}",
        key=key_boton,
        use_container_width=True,
    ):
        _accion_subido(store, producto["id"], plataforma)
    publicacion = _estado_publicacion(store, producto["id"], plataforma)
    if publicacion is not None:
        st.success(
            f"✔ Subido a {_ETIQUETA_PLATAFORMA[plataforma]} el {publicacion['subido_en']}"
        )
    else:
        st.caption(f"Aún no marcado como subido a {_ETIQUETA_PLATAFORMA[plataforma]}.")


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

    # Cinturón y tirantes: `ui/ficha.py` YA asigna la referencia al confirmar
    # la ficha, pero el export la GARANTIZA aquí antes de inyectarla en la
    # descripción (idempotente -- `store.asignar_referencia`) -- así un lote
    # de antes de esta feature, o cualquier hueco, nunca deja la ficha sin
    # "Ref. N". `estado["productos"]` se cargó ANTES de este punto en este
    # mismo render, así que se refresca con el valor devuelto en vez de
    # confiar en el dict potencialmente stale.
    try:
        referencia = store.asignar_referencia(producto["id"])
    except StoreError as exc:
        logger.exception("No se pudo asignar la referencia del producto %s", producto["id"])
        st.error(
            f"No se pudo asignar/leer el número de referencia: {exc} "
            "(la descripción se exportará SIN «Ref. N»)."
        )
    else:
        producto = {**producto, "referencia": referencia}
        st.caption(f"🏷️ **Ref. {referencia}** — se imprime en la descripción de ambas plataformas.")

    tabs = st.tabs([_ETIQUETA_PLATAFORMA[p] for p in _PLATAFORMAS])
    for plataforma, tab in zip(_PLATAFORMAS, tabs):
        with tab:
            _render_plataforma(store, lote_id, producto, fotos_por_id, plataforma)
