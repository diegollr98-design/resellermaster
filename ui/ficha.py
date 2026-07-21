"""ui/ficha.py — LA FICHA: el extractor PROPONE, Diego CONFIRMA de un vistazo.

Tercera pantalla (tras Ingesta y Curar). Opera sobre los productos cuya
AGRUPACIÓN ya confirmó Diego en la Fase 1 (`productos.confirmado`) — nunca
sobre una agrupación a medias: extraer atributos de un grupo sin confirmar
es justo la ficha Frankenstein que `[INC-011]` existe para no crear.

## La ley de esta pantalla (`core/extract.py`, decisión de Diego tras `[INC-012]`)
    EL EXTRACTOR NO AFIRMA. Propone un valor y ENSEÑA EL PÍXEL del que lo
    sacó. Diego confirma de un vistazo. El fallo caro —publicar un dato
    que no está en ninguna foto— se vuelve inexpresable: cada valor va
    JUNTO A SU RECORTE, y si Diego no ve el dato en el recorte, no lo
    confirma.

Consecuencia medida en el Paso 1 (Haiku real sobre las 33 fotos): la marca
casi nunca llega a `campo.valor` (el OCR localiza antes el logo del pecho
—estampado, no publicable— que la etiqueta de cuello), pero SÍ se lee y
queda en `lecturas`. Por eso esta pantalla PROMUEVE las `lecturas` y las
`alternativas` a propuesta de primer nivel, confirmables en un click con su
recorte a la vista: sin eso, la ficha se vería vacía teniendo el dato.

## Coste (`decision-making.md` §15) — antes de gastar, se muestra y se autoriza
La extracción llama al VLM (dinero). NUNCA se lanza sin que Diego vea el
coste estimado y lo autorice. Se cablea `ExtractorEngine.construir_solicitudes`
→ `LLMEngine.estimar_coste_lote` (mira sólo la caché en disco, 0 red, 0 €):
un producto ya extraído antes sale a 0 (caché por hash de imagen).

## Persistencia y errores (`[INC-006]`, `decision-making.md` §13) — igual que `ui/curar.py`
El estado se relee de `store.cargar_lote()` en CADA render, nunca de
`st.session_state` (que aquí sólo guarda valores de widgets de edición y un
mensaje de error de callback). La extracción persiste vía
`store.guardar_extraccion`; la confirmación de Diego vía `store.confirmar_ficha`
(hecho append-only, `fuente="diego"`). Todo fallo del store o del VLM se
captura, se loguea ruidoso y se pinta con `st.error` — nunca un traceback a
la pantalla de Diego.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from core import images, pricing
from core.extract import (
    ExtractorEngine,
    ExtractorError,
    construir_solicitud_redaccion,
    deserializar_extraccion,
    redactar_desde_campos_confirmados,
    serializar_extraccion,
)
from core.llm import LLMEngine, LLMEngineError
from core.schema import Campo, validar_texto
from core.store import LoteStore, StoreError

logger = logging.getLogger(__name__)

# Orden de presentación de los campos. Los que no aparezcan aquí se pintan
# después, en el orden en que vengan. `categoria` va PRIMERO: es lo que
# decide qué campos estructurados pide cada plataforma (`core.schema.
# WALLAPOP_ATRIBUTOS_POR_CATEGORIA`), tiene sentido confirmarla antes que
# el resto. `estado` va al final: SIEMPRE lo pone Diego (`truth-loop.md`
# §A.4), no es una lectura del modelo. `categoria` puede faltar de
# `campos` (el modelo violó el enum) — el filtro de abajo ya lo tolera.
# "composicion" ELIMINADA de la ficha (Diego, 2026-07-17): sólo aplicaba
# a ropa y no aportaba valor a su flujo -- `core/extract.py` ya no la
# produce. Si una extracción VIEJA (persistida antes de este cambio)
# todavía trae la clave, el fallback `[c for c in campos if c not in
# _ORDEN_CAMPOS]` de `_render_producto` la sigue pintando al final --
# degrada, no se pierde.
#
# "tipo" (Fase 4, `docs/seeds/fase-4-tipo-producto.md`): qué ES el
# producto ("masajeador de rodilla", "sudadera") -- va justo después de
# "categoria" porque es lo primero que Diego valida ("¿de qué va esto?"
# antes que marca/talla). SIEMPRE `fuente="inferido"` (un juicio de la
# síntesis, nunca una lectura de píxel) y SIN recorte propio -- cae en el
# mismo camino GENÉRICO que cualquier campo de texto (`_render_campo`, la
# rama `else`): no exige recorte para pintarse ni para confirmarse.
_ORDEN_CAMPOS = (
    "categoria", "tipo", "marca", "modelo", "ean", "talla", "color",
    "medidas", "estado", "desperfectos", "titulo", "descripcion",
)

# Escala de estado (interna, friendly). El mapeo exacto a los literales de
# Wallapop/Vinted vive en `core/schema.py` y se aplica en el EXPORT, no aquí:
# esta pantalla sólo captura la decisión de Diego. "(sin elegir)" es un
# resultado válido (un null que Diego rellena luego), nunca un valor por
# defecto plausible.
_OPCIONES_ESTADO = (
    "(sin elegir)",
    "Nuevo",
    "Como nuevo",
    "Muy bueno",
    "Bueno",
    "Satisfactorio",
    "Para reparar",
)

# `categoria` (`core.schema.CategoriaTipo`/`CATEGORIAS`): SIEMPRE lo
# confirma Diego con un selectbox, igual que `estado` — es un juicio del
# modelo (`fuente="inferido"` siempre, nunca "foto"), no una lectura.
# "(sin elegir)" es un resultado válido, nunca un valor por defecto
# plausible (mismo criterio que `_OPCIONES_ESTADO`).
_OPCIONES_CATEGORIA = (
    "(sin elegir)",
    "moda",
    "electronica",
    "hogar",
    "libros",
    "otros",
)

# --------------------------------------------------------------------------
# CAMPOS OBLIGATORIOS -- defensa CON DIENTES (`decision-making.md` §12,
# pedido explícito de Diego 2026-07-17: "no deje confirmar ficha hasta que
# no se rellene"). `categoria`/`estado`/`titulo`/`descripcion` son
# obligatorios en AMBAS plataformas (`product.md`: Vinted exige
# `title`/`description`/`catalog_id`/`status_id`; Wallapop exige
# `title`/`description`/`categoria`/`estado`) y `core/export.py` YA
# bloquea sin categoría/texto limpio -- bloquear aquí, en la ficha, es
# más barato para Diego que descubrirlo al exportar.
#
# `marca` NO está en esta lista, A PROPÓSITO: en Vinted "Sin marca" es un
# valor VÁLIDO (`product.md` implicación #4) y en Wallapop es opcional --
# exigirla inventaría un requisito que la plataforma no tiene.
# --------------------------------------------------------------------------
_CAMPOS_OBLIGATORIOS: tuple[str, ...] = ("categoria", "estado", "titulo", "descripcion")
_ETIQUETA_OBLIGATORIO: dict[str, str] = {
    "categoria": "categoría",
    "estado": "estado",
    "titulo": "título",
    "descripcion": "descripción",
}


def _con_obligatorios(campos: dict) -> dict:
    """`campos` + un hueco vacío por cada obligatorio que la extracción NO
    produjo. Es el invariante que hace que el gate sea una defensa y no un
    callejón sin salida.

    Lo cazó Diego (2026-07-17) con una ficha real: su producto `eea6b292` se
    extrajo ANTES de que existiera `categoria`, así que su dict de campos no
    tiene esa clave. La pantalla sólo pintaba `[c for c in _ORDEN_CAMPOS if
    c in campos]` y la siembra sólo iteraba `campos.items()` → la categoría
    no se pintaba NI se sembraba, pero seguía siendo obligatoria → **la
    ficha quedaba imposible de confirmar, sin ningún campo donde arreglarlo**.
    Un gate que bloquea por un campo que no se puede rellenar no es una
    defensa con dientes: es una puerta cerrada con la llave dentro.

    La regla, y por eso vive aquí y no en un `if` del render: **todo campo
    obligatorio se pinta SIEMPRE**, lo haya producido la extracción o no. Se
    aplica en el render, en la siembra y en la confirmación (los tres parten
    de aquí), así que no pueden divergir (`change-loop.md` §C3).

    El hueco es `valor=None` + `fuente="inferido"` + `confianza="baja"`: NO
    se inventa nada (`decision-making.md` §13) — es un campo vacío que Diego
    rellena, que es exactamente lo que un obligatorio ausente ES.
    """
    completos = dict(campos)
    for campo in _CAMPOS_OBLIGATORIOS:
        if campo not in completos:
            completos[campo] = {
                "valor": None,
                "fuente": "inferido",
                "confianza": "baja",
                "evidencia": None,
                "propuesta": {
                    "campo": campo,
                    "valor": None,
                    "recorte": None,
                    "evidencia": None,
                    "lecturas": [],
                    "alternativas": [],
                    "motivo": (
                        "esta ficha se extrajo con una versión anterior de la app, "
                        "que todavía no tenía este campo — rellénalo a mano, o "
                        "re-extrae el producto para que lo proponga el modelo"
                    ),
                },
            }
    return completos

_KEY_ERROR = "_ficha_error"


# --------------------------------------------------------------------------
# Errores de callback (mismo patrón que ui/curar.py: st.error dentro de un
# on_click se descarta; se guarda en session_state y se pinta al principio).
# --------------------------------------------------------------------------
def _registrar_error(mensaje: str) -> None:
    st.session_state[_KEY_ERROR] = mensaje


def _mostrar_error_pendiente() -> None:
    mensaje = st.session_state.pop(_KEY_ERROR, None)
    if mensaje:
        st.error(mensaje)


# --------------------------------------------------------------------------
# Render de un recorte (el PÍXEL). Un recorte que falta o está corrupto NO
# tumba la pantalla: se avisa en su sitio y el resto sigue vivo (mismo
# criterio que `ui/curar.py::_render_imagen_segura`).
# --------------------------------------------------------------------------
def _render_recorte(recorte: Path | None, *, width: int = 260) -> None:
    if recorte is None:
        st.caption("— sin recorte —")
        return
    ruta = Path(recorte)
    if not ruta.exists():
        st.caption(f"⚠️ recorte no encontrado: {ruta.name}")
        return
    try:
        st.image(str(ruta), width=width)
    except Exception as exc:  # noqa: BLE001 — frontera "un recorte del producto".
        logger.exception("No se pudo pintar el recorte %s", ruta)
        st.caption(f"⚠️ no se pudo abrir el recorte {ruta.name}: {exc}")


def _render_recorte_miniatura(recorte: Path | None, *, pid: str, campo: str, sufijo: str = "") -> None:
    """El PÍXEL sigue AL LADO del campo -- LÍMITE DURO, nunca se quita
    (`truth-loop.md` §A: el recorte a la vista es lo que sostiene el
    default de "mejor intento"; sin él, confirmar sería afirmar a ciegas).
    Pedido de Diego (2026-07-17, "mínimo scroll posible"): deja de ocupar
    media pantalla -- se pinta como MINIATURA (~90px) y un click
    (`st.popover`, más simple que un `@st.dialog` para esto) lo enseña
    grande. `sufijo` desambigua la key del popover cuando hay varias
    candidatas del mismo campo (`_alt0`, `_alt1`, ...)."""
    if recorte is None:
        st.caption("— sin recorte —")
        return
    ruta = Path(recorte)
    if not ruta.exists():
        st.caption(f"⚠️ recorte no encontrado: {ruta.name}")
        return
    _render_recorte(ruta, width=90)
    with st.popover("🔍 ampliar", key=f"popover_{pid}_{campo}{sufijo}"):
        _render_recorte(ruta, width=420)


def _badge_confianza(confianza: str | None) -> str:
    return {"alta": "🟢 alta", "media": "🟡 media", "baja": "🔴 baja"}.get(
        confianza or "baja", "🔴 baja"
    )


def _badge_fuente(fuente: str | None) -> str:
    """De un vistazo: ¿el dato se LEYÓ en una foto o lo INFIRIÓ el modelo?
    Un inferido no puede verse igual de confirmable que un leído
    (`[INC-008]`, hallazgo del listing-audit: mostrar la fuente ES la
    defensa que hace visible qué revisar)."""
    return {
        "foto": "📷 leído en foto",
        "diego": "✍️ tuyo",
        "comparable": "🔗 comparable",
        "inferido": "🧠 inferido — verifícalo",
    }.get(fuente or "inferido", "🧠 inferido — verifícalo")


# --------------------------------------------------------------------------
# GALERÍA MINIMIZADA (pedido de Diego, 2026-07-17: "que todo ocupe mucho
# menos"). Sólo la foto PRINCIPAL (pequeña) se ve siempre -- para
# identificar el producto de un vistazo -- el RESTO vive dentro de un
# `st.expander` CERRADO por defecto ("escondido pero que se puedan ver
# todas"). Los recortes/evidencia junto a cada campo (`_render_recorte`
# dentro de `_render_campo`) NO SE TOCAN -- son el corazón del proyecto
# (`truth-loop.md` §A: el píxel al lado del campo); esto sólo minimiza la
# galería de fotos ORIGINALES del producto, nunca la evidencia de un dato.
# --------------------------------------------------------------------------
def _render_galeria_fotos(fotos: list[Path]) -> None:
    """La foto PRINCIPAL es la que `core.images.sugerir_orden` sugiere
    primero (nitidez descendente, o cronológico si la nitidez falla en
    alguna foto -- ver su docstring) -- barato, YA se calcula así en otros
    puntos del pipeline (`core/export.py`), no una heurística nueva sólo
    para esta pantalla."""
    if not fotos:
        return
    principal = images.sugerir_orden(fotos)[0].ruta
    _render_recorte(principal, width=160)

    resto = [f for f in fotos if f != principal]
    if resto:
        with st.expander(f"Ver las {len(fotos)} foto(s)"):
            columnas = st.columns(4)
            for i, foto in enumerate(resto):
                with columnas[i % 4]:
                    _render_recorte(foto, width=140)


# --------------------------------------------------------------------------
# Estado de "extraído" / "confirmado" derivado del store (nunca de memoria).
# --------------------------------------------------------------------------
def _esta_extraido(producto: dict) -> bool:
    """Un producto recién agrupado tiene `campos == {}` (INSERT de
    `guardar_agrupacion`). En cuanto se extrae, `serializar_extraccion`
    mete la clave `campos` dentro del JSON."""
    return isinstance(producto.get("campos"), dict) and "campos" in producto["campos"]


def _ficha_confirmada(producto: dict) -> bool:
    """Se deriva del CONTENIDO ACTUAL de `campos` (la marca `confirmada=True`
    que escribe `confirmar_ficha`), NUNCA de que exista una fila en
    `confirmaciones` — esa tabla es append-only y sobreviviría a un
    re-extract, haciendo MENTIR al badge (`[INC-008]`: mostrar ≠ defender).
    Si Diego re-extrae, `guardar_extraccion` sobreescribe `campos` sin esta
    marca → el badge deja de decir 'confirmada', que es la verdad.
    `confirmaciones` sigue siendo el log auditable, no la señal de la UI."""
    return isinstance(producto.get("campos"), dict) and producto["campos"].get("confirmada") is True


def _paths_producto(producto: dict, fotos_por_id: dict[str, dict]) -> list[Path]:
    return [
        Path(fotos_por_id[fid]["ruta"])
        for fid in producto["fotos"]
        if fid in fotos_por_id
    ]


# --------------------------------------------------------------------------
# EXTRACCIÓN CON GATE DE COSTE (§15). Un @st.dialog: al abrirse corre la
# planificación LOCAL (OCR, gratis) y enseña el coste estimado con la caché
# ya descontada; el gasto real sólo ocurre si Diego pulsa "Extraer". La
# mutación (llamada al VLM + guardar_extraccion) vive SÓLO aquí dentro
# (`[INC-006]`: nunca en un on_click del cuerpo del script).
# --------------------------------------------------------------------------
@st.dialog("Extraer atributos — coste antes de gastar", width="large")
def _dialog_extraer(
    store: LoteStore,
    lote_id: str,
    producto: dict,
    fotos: list[Path],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
    *,
    ya_confirmada: bool = False,
) -> None:
    carpeta_crops = store.lotes_dir / lote_id / "crops"
    extractor = crear_extractor(motor, carpeta_crops)

    if ya_confirmada:
        st.warning(
            "⚠️ Esta ficha YA la confirmaste. Re-extraer **descarta tus valores "
            "confirmados** y los sustituye por las propuestas crudas del modelo. "
            "Sólo sigue si de verdad quieres rehacerla desde cero."
        )

    st.caption(
        f"{len(fotos)} foto(s) de este producto. Se localiza el texto en local "
        "(gratis) y sólo los recortes con texto van al VLM."
    )

    try:
        with st.spinner("Planificando (OCR local)…"):
            solicitudes = extractor.construir_solicitudes(fotos)
            estimacion = motor.estimar_coste_lote(solicitudes)
    except Exception as exc:  # noqa: BLE001 — el OCR/planificación no puede tumbar la pantalla.
        logger.exception("No se pudo estimar el coste del producto %s", producto["id"])
        st.error(f"No se pudo preparar la extracción: {exc}")
        return

    coste_cts = estimacion.coste_usd_estimado * 100
    st.write(
        f"**{estimacion.n_llamadas_total} llamada(s) al VLM** · "
        f"{estimacion.n_en_cache} ya en caché (0 €) · "
        f"{estimacion.n_a_pagar} a pagar."
    )
    if estimacion.n_a_pagar == 0:
        st.success("Coste estimado: **0 €** — todo estaba en caché (ya se extrajo antes).")
    else:
        st.info(f"Coste estimado: **~{coste_cts:.2f} cts USD** (Haiku 4.5). Se paga una sola vez.")

    col_si, col_no = st.columns(2)
    with col_si:
        if st.button("💸 Extraer ahora", type="primary", use_container_width=True):
            try:
                with st.spinner("Leyendo etiquetas con el VLM…"):
                    resultado = extractor.extraer_producto(fotos, producto_id=producto["id"])
                    store.guardar_extraccion(producto["id"], serializar_extraccion(resultado))
            except (StoreError, LLMEngineError) as exc:
                logger.exception("Falló la extracción del producto %s", producto["id"])
                st.error(f"No se pudo extraer: {exc}")
                return
            st.rerun()
    with col_no:
        if st.button("cancelar", use_container_width=True):
            st.rerun()


# --------------------------------------------------------------------------
# EXTRACCIÓN DE TODO EL LOTE (Fase 3, 2026-07-17, pedido urgente de Diego):
# "no quiero rellenar las fichas manualmente" — antes había que abrir el
# diálogo POR PRODUCTO (2 clics + 2 esperas × 7 productos = 14 clics). Este
# botón hace la MISMA extracción de siempre (`ExtractorEngine.extraer_producto`,
# la costura no cambia) pero recorre TODOS los productos sin extraer detrás
# de UNA sola puerta de coste (`decision-making.md` §15: se sigue enseñando
# el coste ANTES de gastar, sólo que una vez para el lote entero, no 7).
#
# `_accion_extraer_lote` es PURA (nunca llama a `st.*`) — mismo criterio que
# `ui.curar._accion_dividir_grupo`/`_cerrar_costura`: así se puede probar
# directamente el caso de FALLO (`decision-making.md` §16) sin necesitar el
# runtime de Streamlit. Persiste CADA resultado según termina (nunca junta
# todo para guardarlo al final): si el producto 4 revienta, los 3 primeros
# YA están en disco — dinero ya gastado, no se pierde (`CLAUDE.md`). Un
# fallo en un producto se LOGUEA, se ANOTA con su id y el motivo real, y el
# bucle SIGUE con los demás — nunca un `except: pass`, nunca un producto que
# se salta en silencio (`decision-making.md` §13).
# --------------------------------------------------------------------------
def _productos_sin_extraer(productos: list[dict]) -> list[dict]:
    return [p for p in productos if not _esta_extraido(p)]


def _productos_pendientes_confirmar(productos: list[dict]) -> list[dict]:
    """Extraídos, con la ficha SIN confirmar todavía — el universo de
    entrada del botón de confirmación en bloque (`_dialog_confirmar_lote`).
    NO filtra por "listo" (obligatorios completos): eso lo hace
    `_productos_listos_y_saltados`, más abajo, sobre este mismo conjunto —
    aquí sólo se descarta lo que NUNCA tendría sentido ofrecer: lo que aún
    no se ha extraído, o lo que Diego ya confirmó."""
    return [p for p in productos if _esta_extraido(p) and not _ficha_confirmada(p)]


def _accion_extraer_lote(
    store: LoteStore,
    lote_id: str,
    productos: list[dict],
    fotos_por_id: dict[str, dict],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
    *,
    on_progreso: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Extrae TODOS los `productos` de una tirada. Devuelve
    `(ids_extraidos_ok, [(producto_id, motivo_del_fallo), ...])`. Ver el
    bloque de arriba para el porqué de cada decisión."""
    carpeta_crops = store.lotes_dir / lote_id / "crops"
    extractor = crear_extractor(motor, carpeta_crops)
    ok: list[str] = []
    fallos: list[tuple[str, str]] = []
    total = len(productos)
    for i, producto in enumerate(productos, start=1):
        pid = producto["id"]
        if on_progreso is not None:
            on_progreso(i, total, pid)
        try:
            fotos = _paths_producto(producto, fotos_por_id)
            resultado = extractor.extraer_producto(fotos, producto_id=pid)
            store.guardar_extraccion(pid, serializar_extraccion(resultado))
        except Exception as exc:  # noqa: BLE001 — frontera "un producto no puede tumbar el lote entero" (ver docstring).
            logger.exception("Falló la extracción masiva del producto %s", pid)
            fallos.append((pid, str(exc)))
            continue
        ok.append(pid)
    return ok, fallos


def _render_puerta_coste_extraccion(
    store: LoteStore,
    lote_id: str,
    productos: list[dict],
    fotos_por_id: dict[str, dict],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
) -> Any | None:
    """El CUERPO de la puerta de coste (§15): planifica en local (OCR,
    gratis), llama a `LLMEngine.estimar_coste_lote` (sólo caché en disco, 0
    red, 0 €) y PINTA el resumen. Devuelve la `EstimacionLote`, o `None` si
    falló (ya pintó el `st.error`). Compartido por `_dialog_extraer_lote` y
    `_dialog_reextraer_seleccionados` -- MISMO predicado, no dos que puedan
    divergir (`change-loop.md` §C3)."""
    n = len(productos)
    carpeta_crops = store.lotes_dir / lote_id / "crops"
    extractor = crear_extractor(motor, carpeta_crops)
    try:
        with st.spinner(f"Planificando (OCR local) los {n} producto(s)…"):
            solicitudes_totales: list = []
            for producto in productos:
                fotos = _paths_producto(producto, fotos_por_id)
                solicitudes_totales.extend(extractor.construir_solicitudes(fotos))
            estimacion = motor.estimar_coste_lote(solicitudes_totales)
    except Exception as exc:  # noqa: BLE001 — la planificación (OCR) no puede tumbar la pantalla.
        logger.exception("No se pudo estimar el coste de %s producto(s) del lote %s", n, lote_id)
        st.error(f"No se pudo preparar la extracción: {exc}")
        return None

    coste_cts = estimacion.coste_usd_estimado * 100
    st.write(
        f"**{n} producto(s)** · **{estimacion.n_llamadas_total} llamada(s) al VLM en total** · "
        f"{estimacion.n_en_cache} ya en caché (0 €) · {estimacion.n_a_pagar} a pagar."
    )
    if estimacion.n_a_pagar == 0:
        st.success("Coste estimado: **0 €** — todo estaba en caché (ya se extrajo antes).")
    else:
        st.info(f"Coste estimado: **~{coste_cts:.2f} cts USD** (Haiku 4.5) para los {n} producto(s).")
    return estimacion


@st.dialog("Extraer TODO el lote — coste antes de gastar", width="large")
def _dialog_extraer_lote(
    store: LoteStore,
    lote_id: str,
    productos: list[dict],
    fotos_por_id: dict[str, dict],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
) -> None:
    """UNA sola puerta de coste para todo el lote: suma la estimación
    (`LLMEngine.estimar_coste_lote`, sólo caché en disco, 0 red, 0 €) de
    CADA producto sin extraer antes de mostrar el botón de gasto real.
    `productos` viene ya filtrado a "sin extraer" (`render()`), así que
    nunca hay una ficha confirmada aquí dentro -- un producto no puede estar
    confirmado sin haberse extraído antes -- por eso este camino no lleva el
    gate de `_dialog_reextraer_seleccionados`."""
    n = len(productos)
    st.caption(
        f"{n} producto(s) sin extraer todavía. Se localiza el texto en local "
        "(gratis) y sólo los recortes con texto van al VLM — igual que la "
        "extracción de un producto suelto, pero de una vez."
    )

    estimacion = _render_puerta_coste_extraccion(
        store, lote_id, productos, fotos_por_id, motor, crear_extractor
    )
    if estimacion is None:
        return

    if st.button(f"💸 Extraer los {n} productos ahora", type="primary", use_container_width=True):
        progreso = st.progress(0.0, text=f"Extrayendo 0/{n}…")

        def _avisar(i: int, total: int, pid: str) -> None:
            progreso.progress(i / total, text=f"Producto {i}/{total} (`{pid[:8]}`)…")

        ok, fallos = _accion_extraer_lote(
            store, lote_id, productos, fotos_por_id, motor, crear_extractor, on_progreso=_avisar
        )
        if fallos:
            st.error(
                f"Fallaron {len(fallos)} de {n} (los demás SÍ quedaron guardados en disco):\n\n"
                + "\n".join(f"- producto `{pid[:8]}`: {motivo}" for pid, motivo in fallos)
            )
        if ok:
            st.success(f"{len(ok)} producto(s) extraído(s) y guardado(s).")
        if not fallos:
            st.rerun()


# --------------------------------------------------------------------------
# RE-EXTRAER LOS SELECCIONADOS (pedido de Diego, 2026-07-17: "un botón para
# poder reextraer las que quiera a la vez, como con un selector... por si un
# día hay alguno que quiere hacerse a mano o hay que reextraer X"). Reusa el
# MISMO camino de mutación que "Extraer TODO" (`_accion_extraer_lote`, sin
# cambios: ya era genérica sobre `productos`) y la MISMA puerta de coste
# (`_render_puerta_coste_extraccion`) -- lo único nuevo es que aquí la
# selección PUEDE incluir fichas que Diego ya CONFIRMÓ, y re-extraer
# descarta esos valores confirmados (`_dialog_extraer` ya avisa de esto por
# producto suelto). En bloque, ese aviso podría perder HORAS de curado de un
# solo clic (`CLAUDE.md` LO QUE NUNCA: "perder el trabajo de curado de un
# lote") -- así que el botón de gasto real queda DESHABILITADO hasta que
# Diego marca una casilla explícita ("descarta mis valores confirmados") que
# NOMBRA cada ficha afectada (`decision-making.md` §12: una defensa que
# sólo avisa no es defensa). Si NINGUNA de las seleccionadas está
# confirmada, no hay fricción extra: el botón sale habilitado directo.
# --------------------------------------------------------------------------
def _confirmadas_entre(productos: list[dict]) -> list[str]:
    """ids de los `productos` cuya ficha YA está confirmada -- lo que
    dispara el gate de arriba. Función PURA (no llama a `st.*`), así se
    puede probar sin runtime de Streamlit."""
    return [p["id"] for p in productos if _ficha_confirmada(p)]


def _etiqueta_producto_selector(producto: dict) -> str:
    """Etiqueta IDENTIFICABLE para el `st.multiselect` -- los productos no
    tienen nombre, sólo id: `id[:8]` + título/marca (o "sin extraer") + nº
    de fotos + un aviso si ya está confirmada. Diego tiene que saber cuál es
    cuál sin adivinar."""
    pid = producto["id"]
    fotos = producto.get("fotos", [])
    n_fotos = len(fotos)
    plural = "foto" if n_fotos == 1 else "fotos"
    if not _esta_extraido(producto):
        detalle = "sin extraer"
    else:
        campos = (producto.get("campos") or {}).get("campos", {})
        titulo = (campos.get("titulo") or {}).get("valor")
        marca = (campos.get("marca") or {}).get("valor")
        detalle = titulo or marca or "sin título/marca"
    sufijo = " · ✅ confirmada" if _ficha_confirmada(producto) else ""
    return f"{pid[:8]} — {detalle} ({n_fotos} {plural}){sufijo}"


@st.dialog("Re-extraer los seleccionados — coste antes de gastar", width="large")
def _dialog_reextraer_seleccionados(
    store: LoteStore,
    lote_id: str,
    productos: list[dict],
    fotos_por_id: dict[str, dict],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
) -> None:
    n = len(productos)
    confirmadas = _confirmadas_entre(productos)

    st.caption(
        f"{n} producto(s) seleccionado(s) para re-extraer. Se localiza el texto en "
        "local (gratis) y sólo los recortes con texto van al VLM."
    )

    acepto_descartar = True
    if confirmadas:
        st.warning(
            f"⚠️ {len(confirmadas)} de {n} ficha(s) seleccionada(s) YA están "
            "CONFIRMADAS y van a PERDER sus valores confirmados, sustituidos por "
            "las propuestas crudas del modelo: "
            + ", ".join(f"`{pid[:8]}`" for pid in confirmadas)
        )
        acepto_descartar = st.checkbox(
            f"sí, descarta mis valores confirmados de estas {len(confirmadas)} ficha(s)",
            key="ficha_reextraer_bloque_acepto_descartar",
        )

    estimacion = _render_puerta_coste_extraccion(
        store, lote_id, productos, fotos_por_id, motor, crear_extractor
    )
    if estimacion is None:
        return

    if st.button(
        f"💸 Re-extraer los {n} producto(s) ahora",
        key="btn_reextraer_gasto",
        type="primary",
        use_container_width=True,
        disabled=not acepto_descartar,
    ):
        progreso = st.progress(0.0, text=f"Extrayendo 0/{n}…")

        def _avisar(i: int, total: int, pid: str) -> None:
            progreso.progress(i / total, text=f"Producto {i}/{total} (`{pid[:8]}`)…")

        ok, fallos = _accion_extraer_lote(
            store, lote_id, productos, fotos_por_id, motor, crear_extractor, on_progreso=_avisar
        )
        if fallos:
            st.error(
                f"Fallaron {len(fallos)} de {n} (los demás SÍ quedaron guardados en disco):\n\n"
                + "\n".join(f"- producto `{pid[:8]}`: {motivo}" for pid, motivo in fallos)
            )
        if ok:
            st.success(f"{len(ok)} producto(s) re-extraído(s) y guardado(s).")
        if not fallos:
            st.rerun()


# --------------------------------------------------------------------------
# Un campo de la ficha: EL VALOR JUNTO A SU RECORTE. Promueve lecturas y
# alternativas a propuesta confirmable en un click.
# --------------------------------------------------------------------------
def _valor_por_defecto(campo: str, datos_campo: dict) -> str:
    """Lo que se pre-rellena en el input: el `valor` comprometido por la
    extracción (el mejor intento de la síntesis), o la primera lectura si aún
    no hay valor.

    DECISIÓN DE DIEGO (revierte el `[INC-008]`/§16 anterior): se pre-rellena
    SIEMPRE con el mejor intento, aunque el recorte no esté a la vista. En SU
    flujo la asimetría es la contraria: un campo vacío le cuesta teclearlo
    entero; un valor mal, 2 s corregirlo. La verificación es su ojo con el
    recorte al lado (cuando lo hay), no un hueco en blanco. Un conflicto ya
    NO deja el campo vacío: la síntesis eligió un valor y las otras candidatas
    quedan a un click en `alternativas`."""
    if datos_campo.get("valor"):
        return str(datos_campo["valor"])
    propuesta = datos_campo.get("propuesta") or {}
    for lec in propuesta.get("lecturas", []):
        if lec.get("texto"):
            return str(lec["texto"])
    return ""


def _valor_por_defecto_serial(campo: str, datos_campo_serial: dict) -> str:
    """MISMO criterio que `_valor_por_defecto`, pero sobre el dict SERIAL
    crudo (`producto['campos']['campos'][campo]`, la forma que guarda
    `serializar_extraccion` -- la que consume `_construir_confirmado`).
    Ahí `lecturas` es `[origen, texto]` -- una LISTA posicional
    (`core.extract._lecturas_a_lista`), no la lista de dicts que produce
    `deserializar_extraccion` y que `_valor_por_defecto` sabe leer.
    Confundir las dos formas es EXACTAMENTE el bug que reventaba el botón
    de confirmar en bloque (`'list' object has no attribute 'get'`,
    reproducido ejecutando `AppTest`, `decision-making.md` §4/§16) -- no se
    pasa por `deserializar_extraccion` completo aquí porque eso reconstruye
    `Path` de cada recorte, que este cálculo no necesita."""
    if datos_campo_serial.get("valor"):
        return str(datos_campo_serial["valor"])
    propuesta = datos_campo_serial.get("propuesta") or {}
    for lec in propuesta.get("lecturas", []):
        texto = lec[1] if isinstance(lec, (list, tuple)) and len(lec) > 1 else None
        if texto:
            return str(texto)
    return ""


def _rellenar_valor(key: str, valor: str) -> None:
    """Callback de un botón "usar «X»": escribe la key del text_input ANTES
    de que se instancie en el próximo rerun (patrón legal de Streamlit; lo
    contrario —escribir la key de un widget ya instanciado— lanza
    StreamlitAPIException, ver `app.py`)."""
    st.session_state[key] = valor


# --------------------------------------------------------------------------
# Defaults de "estado"/"categoria" — UN sitio (`change-loop.md` §C3): los
# usa la siembra inicial del widget (`_sembrar_valores_iniciales`) Y la
# detección de "¿lo tocó Diego?" del confirm en bloque (`_construir_confirmado`,
# `modo_bloque=True`, ver su docstring). Si algún día cambia CÓMO se deriva
# el default (p.ej. normalizar mayúsculas del modelo), cambia en un sitio y
# ambos caminos lo heredan.
# --------------------------------------------------------------------------
def _estado_default(datos_campo: dict) -> str:
    sugerido = datos_campo.get("valor")
    return sugerido if sugerido in _OPCIONES_ESTADO else _OPCIONES_ESTADO[0]


def _categoria_default(datos_campo: dict) -> str:
    sugerido = datos_campo.get("valor")
    return sugerido if sugerido in _OPCIONES_CATEGORIA else _OPCIONES_CATEGORIA[0]


# --------------------------------------------------------------------------
# "Valor actual, o el default que se habría sembrado" — para poder decidir
# si un campo está TOCADO sin depender de que `_render_producto` ya haya
# recorrido ese producto EN ESTE script run (el botón de confirmar en
# bloque vive ANTES del bucle que siembra `session_state`, ver `render()`).
# Si la key ya está sembrada (el caso normal, tras un render previo), se lee
# de ahí -- MISMO valor que leería un widget ya instanciado. `datos_campo`
# aquí es SIEMPRE la forma SERIAL (`_construir_confirmado` itera
# `serial.get("campos", {})`) -- por eso el fallback usa
# `_valor_por_defecto_serial`, no `_valor_por_defecto` (forma deserializada).
# --------------------------------------------------------------------------
def _valor_actual_o_defecto(pid: str, campo: str, datos_campo: dict) -> str:
    key = f"ficha_{pid}_{campo}_valor"
    if key in st.session_state:
        return st.session_state[key]
    return _valor_por_defecto_serial(campo, datos_campo)


def _estado_actual_o_defecto(pid: str, datos_campo: dict) -> str:
    key = f"ficha_{pid}_estado_estado"
    if key in st.session_state:
        return st.session_state[key]
    return _estado_default(datos_campo)


def _categoria_actual_o_defecto(pid: str, datos_campo: dict) -> str:
    key = f"ficha_{pid}_categoria_categoria"
    if key in st.session_state:
        return st.session_state[key]
    return _categoria_default(datos_campo)


def _sembrar_valores_iniciales(pid: str, campos: dict) -> None:
    """Pre-siembra en `session_state` el valor por defecto de cada campo, y
    RE-SIEMBRA cuando la extracción cambia (firma distinta).

    Por qué: Streamlit IGNORA el `value=`/`index=` de un widget cuya `key`
    ya está en `session_state`. Sin esto, un valor de una extracción ANTERIOR
    (o de antes de re-extraer, o de una versión vieja del código) se queda
    pegado y TAPA el nuevo — el bug que hacía ver `marca` vacía teniendo
    'Lufthous' persistido. Al sembrar nosotros y re-sembrar sólo cuando la
    propuesta cambia, re-extraer resetea los campos a lo nuevo y las
    ediciones de Diego dentro de una misma extracción se conservan."""
    firma = tuple(
        (campo, _valor_por_defecto(campo, dc)) for campo, dc in sorted(campos.items())
    )
    marcador = f"_ficha_firma_{pid}"
    if st.session_state.get(marcador) == firma:
        return
    for campo, dc in campos.items():
        if campo == "estado":
            st.session_state[f"ficha_{pid}_estado_estado"] = _estado_default(dc)
        elif campo == "categoria":
            st.session_state[f"ficha_{pid}_categoria_categoria"] = _categoria_default(dc)
        else:
            st.session_state[f"ficha_{pid}_{campo}_valor"] = _valor_por_defecto(campo, dc)
    st.session_state[marcador] = firma


def _render_campo(pid: str, campo: str, datos_campo: dict, *, confirmada: bool = False) -> None:
    propuesta = datos_campo.get("propuesta") or {}
    alternativas = propuesta.get("alternativas") or []
    key_valor = f"ficha_{pid}_{campo}_valor"
    badge_obligatorio = _badge_obligatorio(campo, pid)

    # Título y descripción -- pedido de Diego (2026-07-17): "quita la
    # trampa". Editarlos ANTES de confirmar desactivaba la regeneración que
    # los corrige con los campos que Diego acaba de dejar
    # (`_diego_edito_texto` los trataría como "ya tocados por Diego" y
    # `_regenerar_titulo_descripcion` nunca los tocaría) -- exactamente el
    # bug que describía una descripción vieja ("sin daños importantes")
    # sobre un `desperfectos` corregido. ANTES de confirmar: SÓLO LECTURA,
    # con el aviso de que se generan al confirmar (no hay `text_area` --
    # nada que "tocar" por accidente). DESPUÉS de confirmar
    # (`_ficha_confirmada`=True): editables, como antes -- si Diego los
    # retoca AHÍ, queda `fuente="diego"` al volver a confirmar (mismo
    # camino de siempre, `_diego_edito_texto`/`_regenerar_titulo_
    # descripcion` no se tocan).
    if campo in ("titulo", "descripcion"):
        if confirmada:
            st.markdown(
                f"**{campo}**{badge_obligatorio} · editable — se guarda tal cual al confirmar de nuevo"
            )
            st.text_area(  # sin value=: ya sembrado en session_state
                campo,
                key=key_valor,
                label_visibility="collapsed",
                height=70 if campo == "titulo" else 150,
            )
        else:
            st.markdown(f"**{campo}**{badge_obligatorio} · se genera al confirmar, a partir de tus campos")
            valor_actual = st.session_state.get(key_valor, "")
            if valor_actual:
                st.info(valor_actual)
            else:
                st.caption("— sin borrador todavía —")
        return

    col_pixel, col_dato = st.columns([1, 2])

    with col_pixel:
        _render_recorte_miniatura(propuesta.get("recorte"), pid=pid, campo=campo)
        # Conflicto: la síntesis eligió un valor; las OTRAS candidatas quedan
        # dentro de un expander CERRADO por defecto (pedido de Diego,
        # "mínimo scroll posible") -- nunca se pierde ninguna
        # (`truth-loop.md`: el pipeline no elige a ciegas), sólo dejan de
        # estar siempre desplegadas.
        if alternativas:
            with st.expander(f"Otras {len(alternativas)} candidata(s)"):
                for i, cand in enumerate(alternativas):
                    st.caption(f"otra: **{cand.get('valor')}**")
                    _render_recorte_miniatura(cand.get("recorte"), pid=pid, campo=campo, sufijo=f"_alt{i}")
                    st.button(
                        f"usar «{cand.get('valor')}»",
                        key=f"use_{pid}_{campo}_alt{i}",
                        on_click=_rellenar_valor,
                        args=(key_valor, str(cand.get("valor") or "")),
                        use_container_width=True,
                    )

    with col_dato:
        st.markdown(
            f"**{campo}**{badge_obligatorio} · {_badge_fuente(datos_campo.get('fuente'))} · "
            f"confianza {_badge_confianza(datos_campo.get('confianza'))}"
        )
        motivo = propuesta.get("motivo")
        if motivo:
            st.caption(motivo)

        if not datos_campo.get("valor"):
            for i, lec in enumerate(propuesta.get("lecturas", [])):
                texto, origen = lec.get("texto"), lec.get("origen")
                if texto:
                    st.button(
                        f"usar «{texto}» (leído por {origen})",
                        key=f"use_{pid}_{campo}_lec{i}",
                        on_click=_rellenar_valor,
                        args=(key_valor, str(texto)),
                    )

        if campo == "estado":
            # Lo cierra SIEMPRE Diego (`truth-loop.md` §A.4). Si el modelo dio
            # una estimación en prosa (el evaluador de estado la da), se
            # muestra como PISTA; el literal lo elige él. La key ya está
            # sembrada por `_sembrar_valores_iniciales` (canónico o "(sin
            # elegir)") — NO se pasa `value=`/`index=` para no chocar con la
            # key ya en session_state.
            sugerido = datos_campo.get("valor")
            if sugerido and sugerido not in _OPCIONES_ESTADO:
                st.caption(f"El modelo estimó: _{str(sugerido)[:140]}_ — elige el estado:")
            st.selectbox(
                "estado (lo confirmas tú)",
                _OPCIONES_ESTADO,
                key=f"ficha_{pid}_{campo}_estado",
                label_visibility="collapsed",
            )
        elif campo == "categoria":
            # Igual que `estado`: es un juicio del modelo (`fuente=
            # "inferido"` siempre, `[INC-013]`-style — nunca "foto", una
            # categoría no es texto legible en un píxel), lo cierra Diego.
            # La key ya está sembrada por `_sembrar_valores_iniciales`.
            sugerido = datos_campo.get("valor")
            if sugerido and sugerido not in _OPCIONES_CATEGORIA:
                st.caption(f"El modelo propuso: _{str(sugerido)[:140]}_ — elige la categoría:")
            st.selectbox(
                "categoría (la confirmas tú)",
                _OPCIONES_CATEGORIA,
                key=f"ficha_{pid}_{campo}_categoria",
                label_visibility="collapsed",
            )
        else:
            # Sin `value=`: el valor por defecto ya está sembrado en
            # session_state (ver `_sembrar_valores_iniciales`). Pasar `value=`
            # con la key ya sembrada haría que Streamlit ignore uno de los dos
            # — y era justo lo que dejaba pegado un valor de una extracción
            # vieja tapando el nuevo.
            st.text_input(
                campo,
                key=key_valor,
                label_visibility="collapsed",
                placeholder="vacío = null",
            )


# --------------------------------------------------------------------------
# Confirmación de la ficha: recoge lo que Diego dejó en cada campo.
#
# DOS MODOS, mismo código (`change-loop.md` §C3 — un predicado, no dos):
#   - `modo_bloque=False` (confirmar UN producto, "como hoy"): Diego abrió
#     ESTA ficha y la revisó campo a campo con el píxel al lado -- eso ES la
#     confirmación (`truth-loop.md` §A.2: "quien afirma es Diego, al
#     confirmar"). TODO campo pasa a `fuente="diego"`, lo haya tocado o no.
#   - `modo_bloque=True` (confirmar VARIAS de golpe, botón de lote): Diego
#     NO ha mirado cada píxel de cada ficha -- sólo aceptó el lote. Un campo
#     que él NO tocó (el valor actual es el mismo default que se sembró)
#     MANTIENE su `fuente`/`confianza` originales (`foto`/`inferido`); sólo
#     los que sí editó -- a mano, o con un botón "usar «X»" -- se marcan
#     `fuente="diego"`. Mentir sobre la procedencia es justo lo único que el
#     giro del 2026-07-16 NO relajó (`truth-loop.md` §A.2).
# --------------------------------------------------------------------------
def _construir_confirmado(pid: str, serial: dict, *, modo_bloque: bool = False) -> dict[str, Any]:
    """Se parte del dict SERIALIZADO (`producto['campos']`, rutas de recorte
    como `str`), NO del deserializado (rutas `Path`, que `json.dumps` de
    `confirmar_ficha` no sabe serializar). Se preserva el envoltorio entero
    (`coste_usd`, `fallos`, la propuesta con su recorte para re-revisar) y
    sólo se sobrescriben `valor` (siempre) y `fuente`/`confianza` (según el
    modo de arriba). Un campo que Diego dejó vacío es un null CONFIRMADO,
    no un fallo.

    Usa `_valor_actual_o_defecto`/`_estado_actual_o_defecto`/
    `_categoria_actual_o_defecto` (no `st.session_state` directo): así
    funciona igual si el widget de este producto YA está sembrado (el caso
    normal) o si todavía NO se ha renderizado en este script run (el botón
    de confirmar en bloque vive ANTES del bucle que siembra, ver `render()`)
    -- en ese caso cae al MISMO default que sembraría `_sembrar_valores_
    iniciales`, así que "tocado" se puede evaluar sin depender del orden."""
    # `[INC-011]` (ficha Frankenstein) CON DIENTES: si la extracción avisó de
    # incoherencia (campos de fotos disjuntas), un valor confirmado NUNCA sube
    # a `alta` — el aviso baja el techo, no es sólo un pie de foto (`§12`).
    hay_aviso_coherencia = bool(serial.get("aviso_coherencia"))

    campos_confirmados: dict[str, Any] = {}
    # Mismo invariante que el render (`_con_obligatorios`): si la extracción
    # no produjo un obligatorio, aquí existe igual como hueco, así que el
    # valor que Diego teclea en pantalla SE PERSISTE en vez de perderse.
    for campo, datos_campo in _con_obligatorios(serial.get("campos", {})).items():
        if campo == "estado":
            elegido = _estado_actual_o_defecto(pid, datos_campo)
            valor = None if elegido == _OPCIONES_ESTADO[0] else elegido
            tocado = elegido != _estado_default(datos_campo)
        elif campo == "categoria":
            elegido = _categoria_actual_o_defecto(pid, datos_campo)
            valor = None if elegido == _OPCIONES_CATEGORIA[0] else elegido
            tocado = elegido != _categoria_default(datos_campo)
        else:
            crudo = _valor_actual_o_defecto(pid, campo, datos_campo)
            valor = crudo.strip() or None
            tocado = crudo.strip() != (_valor_por_defecto_serial(campo, datos_campo) or "").strip()

        base = dict(datos_campo)  # json-safe: rutas ya son str
        base["valor"] = valor
        if modo_bloque and not tocado:
            # Sin tocar, en modo bloque: se PRESERVA fuente/confianza
            # originales (ya están en `base` vía `dict(datos_campo)`) —
            # Diego no lo revisó, así que no puede decir "esto lo confirmé
            # yo". `valor` sí se actualiza arriba porque, al no estar
            # tocado, coincide exactamente con el default (foto/inferido).
            pass
        else:
            base["fuente"] = "diego"
            if valor is None:
                base["confianza"] = "baja"
            elif hay_aviso_coherencia:
                base["confianza"] = "media"
            else:
                base["confianza"] = "alta"
        campos_confirmados[campo] = base

    nuevo = dict(serial)
    nuevo["campos"] = campos_confirmados
    # Marca que la lee `_ficha_confirmada` desde el CONTENIDO de campos: un
    # re-extract posterior la borra (sobreescribe campos) → el badge no miente.
    nuevo["confirmada"] = True
    return nuevo


# Título/descripción → nombre del campo en `core/schema.py`. Las violaciones
# de LONGITUD no bloquean la confirmación (el export las reajusta); las de
# CONTENIDO sí — una marca ajena, un email o un enlace en Vinted ocultan el
# anuncio (`product.md §7`, defensa con dientes §12).
_CAMPO_TEXTO_A_SCHEMA = {"titulo": "title", "descripcion": "description"}
_CODIGOS_LONGITUD = frozenset({"TOO_SHORT", "TOO_LONG"})


def _problemas_de_texto(confirmado: dict, marca: str | None) -> list[str]:
    """Corre el sanitizador (`schema.validar_texto`) sobre título y
    descripción para AMBAS plataformas. Devuelve los problemas de CONTENIDO
    (no de longitud) que impiden confirmar; vacío = limpio."""
    problemas: list[str] = []
    for campo, campo_schema in _CAMPO_TEXTO_A_SCHEMA.items():
        dc = confirmado.get("campos", {}).get(campo)
        texto = (dc or {}).get("valor")
        if not texto:
            continue
        for plataforma in ("wallapop", "vinted"):
            for viol in validar_texto(texto, plataforma, marca, campo_schema):  # type: ignore[arg-type]
                if viol.codigo not in _CODIGOS_LONGITUD:
                    problemas.append(f"{campo}: {viol.mensaje}")
    return sorted(set(problemas))


# --------------------------------------------------------------------------
# EL BUG DE DIEGO (2026-07-17, "la descripción no menciona la CREMALLERA
# ROTA"): `titulo`/`descripcion` se redactaban DURANTE LA EXTRACCIÓN
# (`core.extract._sintetizar_ficha`), antes de que Diego corrigiera nada —
# si luego cambiaba `marca` o rellenaba `desperfectos`, el texto se quedaba
# describiendo la versión VIEJA. Fix: se REGENERAN aquí, al confirmar, a
# partir de los campos que Diego acaba de dejar en pantalla — nunca de las
# fotos (`core.extract.redactar_desde_campos_confirmados`, la garantía
# anti-marca-ajena vive en que `LLMEngine.consultar_texto` ni siquiera
# ACEPTA imágenes).
#
# Excepción — RESPETAR SU EDICIÓN: si Diego escribió su propio título o
# descripción (a mano, o ya los confirmó así en una vuelta anterior), NUNCA
# se le pisa: su texto manda. `_diego_edito_texto` decide comparando contra
# el valor que la extracción tenía ANTES de este confirm (`serial`, el
# `producto["campos"]` tal y como estaba en disco al abrir la pantalla).
# --------------------------------------------------------------------------
def _diego_edito_texto(campo: str, serial: dict, confirmado: dict) -> bool:
    """True si el texto de `campo` ("titulo"/"descripcion") es de Diego y
    NO debe regenerarse: (a) ya estaba confirmado con `fuente="diego"`
    ANTES de este confirm (una vuelta anterior lo fijó a mano y este
    confirm no lo tocó), o (b) el valor que deja en el widget AHORA
    difiere del que tenía la extracción — lo acaba de teclear él mismo.

    Si NINGUNA de las dos se cumple, es el borrador del modelo tal cual
    (`fuente="inferido"`) sin tocar → hay que regenerarlo con los campos
    recién confirmados (el caso exacto del bug: Diego corrige `marca`/
    `desperfectos` y no toca la caja de texto)."""
    original = serial.get("campos", {}).get(campo) or {}
    valor_actual = (confirmado.get("campos", {}).get(campo) or {}).get("valor")
    return original.get("fuente") == "diego" or valor_actual != original.get("valor")


def _regenerar_titulo_descripcion(
    confirmado: dict, serial: dict, motor: LLMEngine, pid: str
) -> tuple[dict, str | None]:
    """Devuelve `(confirmado_actualizado, error_o_None)`. Si alguno de
    título/descripción NO lo tocó Diego, llama a la redacción con SOLO los
    campos ya confirmados (cero fotos) y sobreescribe SÓLO ese campo,
    `fuente="inferido"` (lo redactó el modelo, no Diego — mentir sobre la
    procedencia es justo lo único que el giro del 2026-07-16 NO relajó).

    Un fallo de la redacción se propaga como error explícito: NUNCA se
    confirma en silencio con el texto viejo (describiría el producto
    PRE-corrección)."""
    campos = confirmado.get("campos", {})
    if "titulo" not in campos and "descripcion" not in campos:
        return confirmado, None  # extracción sin estos campos (muy vieja); nada que regenerar

    titulo_tocado = _diego_edito_texto("titulo", serial, confirmado)
    descripcion_tocada = _diego_edito_texto("descripcion", serial, confirmado)
    if titulo_tocado and descripcion_tocada:
        return confirmado, None  # los dos son de Diego -- no se toca nada

    campos_para_redaccion = {
        campo: (dc or {}).get("valor")
        for campo, dc in campos.items()
        if campo not in ("titulo", "descripcion")
    }
    try:
        nuevo_titulo, nueva_descripcion = redactar_desde_campos_confirmados(
            campos_para_redaccion, motor, producto_id=pid
        )
    except (LLMEngineError, ExtractorError) as exc:
        logger.exception("No se pudo regenerar título/descripción del producto %s", pid)
        return confirmado, (
            f"No se pudo regenerar el título/descripción con los datos corregidos: {exc}. "
            "No se ha confirmado la ficha (para no dejar un texto desactualizado) — "
            "reintenta o edita el título/descripción a mano y confirma de nuevo."
        )

    if not titulo_tocado and "titulo" in campos:
        campos["titulo"] = {**campos["titulo"], "valor": nuevo_titulo, "fuente": "inferido", "confianza": "baja"}
    if not descripcion_tocada and "descripcion" in campos:
        campos["descripcion"] = {
            **campos["descripcion"], "valor": nueva_descripcion, "fuente": "inferido", "confianza": "baja",
        }
    confirmado["campos"] = campos
    return confirmado, None


def _obligatorios_faltantes_en_confirmado(confirmado: dict) -> list[str]:
    """Lee el dict YA CONSTRUIDO (post `_construir_confirmado` +
    `_regenerar_titulo_descripcion`) -- la fuente AUTORITATIVA de lo que
    de verdad se va a persistir; `titulo`/`descripcion` pueden haberse
    regenerado justo antes, así que este chequeo va DESPUÉS de eso, nunca
    antes. Nunca revienta si la clave no existe (extracción vieja sin ese
    campo) -- ausencia cuenta como obligatorio faltante, igual que un
    valor vacío (`decision-making.md` §12: una defensa que sólo avisa no
    es defensa -- ésta BLOQUEA, no sugiere)."""
    campos = confirmado.get("campos", {})
    return [
        _ETIQUETA_OBLIGATORIO[campo]
        for campo in _CAMPOS_OBLIGATORIOS
        if not (campos.get(campo) or {}).get("valor")
    ]


def _campo_esta_vacio_en_pantalla(pid: str, campo: str) -> bool:
    """UN sitio (`change-loop.md` §C3) que decide si un campo obligatorio
    está vacío AHORA MISMO en pantalla (valor LIVE de `session_state`, no
    el de la extracción). Lo usan `_obligatorios_faltantes_en_pantalla`
    (agrega sobre los 4 obligatorios) Y el badge que se pinta junto a cada
    campo (`_badge_obligatorio`, más abajo) -- el mismo predicado que
    decide el rojo del badge decide el botón deshabilitado; si divergieran,
    el badge mentiría (`decision-making.md` §12: mostrar ≠ defender)."""
    if campo == "estado":
        return st.session_state.get(f"ficha_{pid}_estado_estado", _OPCIONES_ESTADO[0]) == _OPCIONES_ESTADO[0]
    if campo == "categoria":
        return (
            st.session_state.get(f"ficha_{pid}_categoria_categoria", _OPCIONES_CATEGORIA[0])
            == _OPCIONES_CATEGORIA[0]
        )
    valor = st.session_state.get(f"ficha_{pid}_{campo}_valor", "")
    return not valor.strip()


def _obligatorios_faltantes_en_pantalla(pid: str) -> list[str]:
    """Mismo chequeo que `_obligatorios_faltantes_en_confirmado` pero
    leyendo DIRECTO de `session_state` -- para pintar "falta X" y
    deshabilitar el botón de Confirmar EN VIVO, antes de que Diego pulse
    nada. Es sólo UX: el bloqueo REAL, con dientes, vive dentro de
    `_accion_confirmar_ficha` (una defensa que sólo deshabilita un botón
    del cliente no es una defensa, `decision-making.md` §12). Nota:
    `titulo`/`descripcion` casi nunca aparecen aquí como "faltantes" en la
    práctica (la síntesis los rellena con su mejor intento), así que esta
    vista previa no necesita anticipar la regeneración al confirmar."""
    return [
        _ETIQUETA_OBLIGATORIO[campo]
        for campo in _CAMPOS_OBLIGATORIOS
        if _campo_esta_vacio_en_pantalla(pid, campo)
    ]


# --------------------------------------------------------------------------
# BADGE DE OBLIGATORIO -- pedido de Diego, 2026-07-17: "los que sean
# obligatorios que se distingan bien del resto" -- el `*(obligatorio)*` en
# cursiva se perdía dentro de la línea. Badge de color de Streamlit
# (`:color-badge[texto]`, funciona dentro de cualquier `st.markdown` y es
# visible para `AppTest` como texto plano dentro de `at.markdown`): NARANJA
# si ya tiene un valor, ROJO si está vacío -- el vacío es justo el que
# bloquea confirmar, así que tiene que cantar más (`decision-making.md`
# §16: la señal más peligrosa no puede verse igual que la inocua).
# --------------------------------------------------------------------------
def _badge_obligatorio(campo: str, pid: str) -> str:
    if campo not in _CAMPOS_OBLIGATORIOS:
        return ""
    if _campo_esta_vacio_en_pantalla(pid, campo):
        return " :red-badge[⚠️ OBLIGATORIO — falta]"
    return " :orange-badge[obligatorio]"


def _confirmar_uno(
    store: LoteStore, pid: str, serial: dict, motor: LLMEngine, *, modo_bloque: bool
) -> tuple[bool, str | None]:
    """EL ÚNICO camino que escribe una ficha confirmada -- lo llaman
    `_accion_confirmar_ficha` (botón individual) Y `_accion_confirmar_lote`
    (botón de bloque), con el MISMO orden de validaciones
    (`change-loop.md` §C3: un predicado, no dos que puedan divergir). Pura
    (nunca llama a `st.*`): devuelve `(ok, motivo_del_fallo_o_None)` para
    que cada llamador decida cómo mostrarlo (un `st.error` inmediato en el
    caso individual; una lista de `(pid, motivo)` acumulada en el de bloque,
    ver `decision-making.md` §13: nunca un fallo que se traga en silencio)."""
    confirmado = _construir_confirmado(pid, serial, modo_bloque=modo_bloque)

    confirmado, error_redaccion = _regenerar_titulo_descripcion(confirmado, serial, motor, pid)
    if error_redaccion:
        return False, error_redaccion

    faltan = _obligatorios_faltantes_en_confirmado(confirmado)
    if faltan:
        return False, "Faltan campos obligatorios antes de confirmar: " + ", ".join(faltan) + "."

    marca = (confirmado.get("campos", {}).get("marca") or {}).get("valor")
    problemas = _problemas_de_texto(confirmado, marca)
    if problemas:
        return False, (
            "Arregla el texto antes de confirmar (Wallapop/Vinted lo rechazan):\n\n"
            + "\n".join(f"- {p}" for p in problemas)
        )
    try:
        store.confirmar_ficha(pid, confirmado)
    except StoreError as exc:
        logger.exception("No se pudo confirmar la ficha del producto %s", pid)
        return False, f"No se pudo confirmar la ficha: {exc}"
    return True, None


def _accion_confirmar_ficha(store: LoteStore, pid: str, serial: dict, motor: LLMEngine) -> bool:
    """Corre en el mismo script run que el click del botón (no en un
    on_click), así que `st.session_state` ya tiene los valores de los
    widgets y `st.error` es válido — mismo criterio que
    `ui/curar.py::_accion_archivar_foto`. `modo_bloque=False`: Diego abrió
    ESTA ficha y la revisó -- "como hoy", todo pasa a `fuente="diego"`."""
    ok, motivo = _confirmar_uno(store, pid, serial, motor, modo_bloque=False)
    if motivo:
        st.error(motivo)
    return ok


# --------------------------------------------------------------------------
# CONFIRMACIÓN DE TODAS LAS FICHAS LISTAS DE GOLPE (Fase 3, pedido de
# Diego: "menos clics"). SIN MENTIR SOBRE LA PROCEDENCIA -- contrato exacto
# (decidido por Diego cuando se le preguntó): un campo que él NO tocó
# MANTIENE su `fuente`/`confianza` originales (`_construir_confirmado`,
# `modo_bloque=True`); sólo lo que sí editó pasa a `fuente="diego"`. La
# ficha queda `confirmada=True` igual (él ACEPTÓ los valores), pero el
# rastro de qué revisó de verdad sobrevive hasta el export
# (`truth-loop.md` §A.2: "un 'confirmar todo' a ciegas" es EXACTAMENTE el
# caso que tumbaría la premisa del giro si mintiera -- por eso no miente).
#
# La red de seguridad que Diego pidió explícitamente: un producto con
# obligatorios vacíos NUNCA se confirma en bloque -- se SALTA y se NOMBRA
# (nunca en silencio, `decision-making.md` §13).
# --------------------------------------------------------------------------
def _productos_listos_y_saltados(
    productos: list[dict],
) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    """`productos` debe venir YA FILTRADO a extraídos-sin-confirmar (mismo
    criterio que `_productos_sin_extraer` para la extracción en bloque, ver
    `render()`). Separa los que tienen TODOS los obligatorios listos
    (confirmables de golpe) de los que no (se saltan, nombrando QUÉ les
    falta). Reusa `_construir_confirmado` (`modo_bloque=True`, pura, sin
    llamar a la API ni a `st.error`) + `_obligatorios_faltantes_en_
    confirmado`, que YA EXISTEN -- el MISMO predicado que decide si un
    confirm individual pasa la puerta decide aquí si un producto entra en
    el lote (`change-loop.md` §C3)."""
    listos: list[dict] = []
    saltados: list[tuple[str, list[str]]] = []
    for producto in productos:
        pid = producto["id"]
        confirmado_preview = _construir_confirmado(pid, producto["campos"], modo_bloque=True)
        faltan = _obligatorios_faltantes_en_confirmado(confirmado_preview)
        if faltan:
            saltados.append((pid, faltan))
        else:
            listos.append(producto)
    return listos, saltados


def _solicitudes_redaccion_pendientes(productos: list[dict]) -> list[tuple[str, str]]:
    """`(prompt, version_prompt)` de la redacción que HARÍA FALTA para cada
    `producto` de `productos` si se confirmara en bloque AHORA MISMO -- para
    poder ENSEÑAR EL COSTE antes de gastar (§15) sin llamar a nadie. Sigue
    el MISMO criterio que `_regenerar_titulo_descripcion`/`_diego_edito_
    texto`: si Diego ya tocó título Y descripción, no hace falta redacción
    -- no se cuenta ni se paga."""
    solicitudes: list[tuple[str, str]] = []
    for producto in productos:
        pid = producto["id"]
        serial = producto["campos"]
        confirmado = _construir_confirmado(pid, serial, modo_bloque=True)
        campos = confirmado.get("campos", {})
        if "titulo" not in campos and "descripcion" not in campos:
            continue
        titulo_tocado = _diego_edito_texto("titulo", serial, confirmado)
        descripcion_tocada = _diego_edito_texto("descripcion", serial, confirmado)
        if titulo_tocado and descripcion_tocada:
            continue
        campos_para_redaccion = {
            c: (dc or {}).get("valor") for c, dc in campos.items() if c not in ("titulo", "descripcion")
        }
        solicitudes.append(construir_solicitud_redaccion(campos_para_redaccion))
    return solicitudes


def _accion_confirmar_lote(
    store: LoteStore,
    productos: list[dict],
    motor: LLMEngine,
    *,
    on_progreso: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Confirma TODOS los `productos` (ya filtrados a LISTOS por
    `_productos_listos_y_saltados`) de una tirada, por el MISMO camino que
    confirmar uno (`_confirmar_uno`, `modo_bloque=True`). Persiste CADA
    ficha según termina (dinero de la redacción ya gastado no se pierde si
    la N+1 revienta); un fallo de UNA no tumba las demás -- se loguea, se
    anota con su `producto_id` y el motivo real, y el bucle SIGUE (mismo
    criterio que `_accion_extraer_lote`, `decision-making.md` §13/§16)."""
    ok: list[str] = []
    fallos: list[tuple[str, str]] = []
    total = len(productos)
    for i, producto in enumerate(productos, start=1):
        pid = producto["id"]
        if on_progreso is not None:
            on_progreso(i, total, pid)
        try:
            exito, motivo = _confirmar_uno(store, pid, producto["campos"], motor, modo_bloque=True)
        except Exception as exc:  # noqa: BLE001 — un producto no puede tumbar el lote entero (ver docstring de `_accion_extraer_lote`).
            logger.exception("Fallo inesperado confirmando en bloque el producto %s", pid)
            fallos.append((pid, str(exc)))
            continue
        if exito:
            ok.append(pid)
        else:
            fallos.append((pid, motivo or "motivo desconocido"))
    return ok, fallos


@st.dialog("Confirmar todas las fichas listas — coste antes de gastar", width="large")
def _dialog_confirmar_lote(
    store: LoteStore,
    productos: list[dict],
    motor: LLMEngine,
) -> None:
    """UNA sola puerta de coste (§15) para las N redacciones que este
    confirm en bloque va a disparar — mismo patrón que
    `_dialog_extraer_lote`. `productos` viene YA FILTRADO a extraídos-sin-
    confirmar (ver `render()`); aquí se separan listos/saltados y se avisa
    de los saltados ANTES de mostrar el botón de gasto real."""
    listos, saltados = _productos_listos_y_saltados(productos)

    if saltados:
        with st.expander(f"⚠️ {len(saltados)} ficha(s) se van a SALTAR (les falta algo)"):
            for pid, faltan in saltados:
                st.caption(f"- producto `{pid[:8]}`: falta " + ", ".join(faltan))

    if not listos:
        st.warning(
            "Ninguna ficha tiene todos los obligatorios listos todavía — no hay nada "
            "que confirmar en bloque. Complétalas una a una primero."
        )
        if st.button("cerrar", use_container_width=True):
            st.rerun()
        return

    st.caption(
        f"{len(listos)} ficha(s) lista(s). Los campos que edites/uses individualmente "
        "quedan `fuente=diego`; los que se aceptan SIN TOCAR mantienen su procedencia "
        "original (📷 leído en foto / 🧠 inferido) — nunca se marcan como verificados "
        "por ti si no los miraste."
    )

    try:
        with st.spinner("Estimando el coste de la redacción…"):
            solicitudes = _solicitudes_redaccion_pendientes(listos)
            estimacion = motor.estimar_coste_texto_lote(solicitudes)
    except Exception as exc:  # noqa: BLE001 — la estimación no puede tumbar la pantalla.
        logger.exception("No se pudo estimar el coste de la confirmación en bloque")
        st.error(f"No se pudo preparar la confirmación en bloque: {exc}")
        return

    coste_cts = estimacion.coste_usd_estimado * 100
    st.write(
        f"**{estimacion.n_llamadas_total} redacción(es) de título/descripción** · "
        f"{estimacion.n_en_cache} ya en caché (0 €) · {estimacion.n_a_pagar} a pagar."
    )
    if estimacion.n_a_pagar == 0:
        st.success("Coste estimado: **0 €** — todo estaba en caché o ya escrito por ti.")
    else:
        st.info(f"Coste estimado: **~{coste_cts:.2f} cts USD** (Haiku 4.5) para las {len(listos)} fichas.")

    if st.button(f"✅ Confirmar las {len(listos)} ficha(s) ahora", type="primary", use_container_width=True):
        progreso = st.progress(0.0, text=f"Confirmando 0/{len(listos)}…")

        def _avisar(i: int, total: int, pid: str) -> None:
            progreso.progress(i / total, text=f"Ficha {i}/{total} (`{pid[:8]}`)…")

        ok, fallos = _accion_confirmar_lote(store, listos, motor, on_progreso=_avisar)
        if fallos:
            st.error(
                f"Fallaron {len(fallos)} de {len(listos)} (las demás SÍ quedaron confirmadas):\n\n"
                + "\n".join(f"- producto `{pid[:8]}`: {motivo}" for pid, motivo in fallos)
            )
        if ok:
            st.success(f"{len(ok)} ficha(s) confirmada(s).")
        if not fallos:
            st.rerun()


# --------------------------------------------------------------------------
# Comparables de precio — Costura 2 (`core/pricing.py`): NO tasa, abre la
# búsqueda del producto en Wallapop/Vinted para que Diego vea precios reales.
# --------------------------------------------------------------------------
def _render_comparables(datos: dict) -> None:
    campos = datos.get("campos", {})
    atributos: dict[str, Campo] = {}
    for campo in ("marca", "modelo", "ean", "talla"):
        dc = campos.get(campo)
        if dc and dc.get("valor"):
            # fuente="diego" para no exigir evidencia (Campo la pide sólo con
            # fuente="foto"); a `pricing.buscar` sólo le importa `.valor`.
            atributos[campo] = Campo(valor=str(dc["valor"]), fuente="diego", confianza="baja")

    consulta = pricing.buscar(atributos)
    if not consulta.urls_busqueda:
        st.caption("💰 Comparables: rellena marca/modelo o el código de barras y podrás buscarlos.")
        return

    match = (
        "código de barras — el MISMO producto"
        if consulta.tipo_match == "exacto"
        else "texto — productos parecidos, míralos"
    )
    st.caption(f"💰 Comparables por {match}: «{consulta.terminos}»")
    columnas = st.columns(len(consulta.urls_busqueda))
    for columna, (plataforma, url) in zip(columnas, consulta.urls_busqueda.items()):
        with columna:
            st.link_button(f"🔎 Ver en {plataforma.capitalize()}", url, use_container_width=True)


# --------------------------------------------------------------------------
# Tarjeta de un producto.
# --------------------------------------------------------------------------
def _render_producto(
    store: LoteStore,
    lote_id: str,
    producto: dict,
    fotos_por_id: dict[str, dict],
    motor: LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine],
) -> None:
    pid = producto["id"]
    with st.container(border=True):
        st.subheader(f"Producto `{pid[:8]}` — {len(producto['fotos'])} foto(s)")
        fotos = _paths_producto(producto, fotos_por_id)
        _render_galeria_fotos(fotos)

        if not _esta_extraido(producto):
            st.caption("Atributos sin extraer todavía.")
            if st.button("🔎 Extraer atributos…", key=f"extraer_{pid}"):
                _dialog_extraer(store, lote_id, producto, fotos, motor, crear_extractor)
            return

        datos = deserializar_extraccion(producto["campos"])
        confirmada = _ficha_confirmada(producto)

        if confirmada:
            st.success("✅ Ficha confirmada por Diego.")
        for aviso in (datos.get("aviso_coherencia"),):
            if aviso:
                st.warning(f"⚠️ {aviso}")
        for fallo in datos.get("fallos", []):
            st.caption(f"· fallo técnico durante la extracción: {fallo}")

        # `_con_obligatorios`: un obligatorio que la extracción no produjo
        # (ficha de una versión anterior) DEBE pintarse igual, o el gate se
        # vuelve un callejón sin salida. Ver su docstring.
        campos = _con_obligatorios(datos.get("campos", {}))
        _sembrar_valores_iniciales(pid, campos)
        orden = [c for c in _ORDEN_CAMPOS if c in campos] + [
            c for c in campos if c not in _ORDEN_CAMPOS
        ]
        for campo in orden:
            st.divider()
            _render_campo(pid, campo, campos[campo], confirmada=confirmada)

        st.divider()
        _render_comparables(datos)

        st.divider()
        col_conf, col_reextraer = st.columns([2, 1])
        with col_conf:
            etiqueta = "✅ Volver a confirmar" if confirmada else "✅ Confirmar ficha"
            faltan = _obligatorios_faltantes_en_pantalla(pid)
            if faltan:
                st.caption("⚠️ obligatorio, falta: " + ", ".join(faltan))
            if st.button(
                etiqueta, key=f"confirmar_{pid}", type="primary",
                use_container_width=True, disabled=bool(faltan),
            ):
                if _accion_confirmar_ficha(store, pid, producto["campos"], motor):
                    st.rerun()
        with col_reextraer:
            if st.button("🔁 Re-extraer", key=f"reextraer_{pid}", use_container_width=True):
                _dialog_extraer(
                    store, lote_id, producto, fotos, motor, crear_extractor,
                    ya_confirmada=confirmada,
                )


# --------------------------------------------------------------------------
# Entrada de la pantalla.
# --------------------------------------------------------------------------
def render(
    store: LoteStore,
    lote_id: str,
    *,
    crear_motor: Callable[[], LLMEngine] = LLMEngine,
    crear_extractor: Callable[[LLMEngine, Path], ExtractorEngine] | None = None,
) -> None:
    """`crear_motor`/`crear_extractor` son inyectables para los tests
    (`AppTest`): en producción usan `LLMEngine`/`ExtractorEngine` reales;
    un test pasa un motor falso para no llamar a la API ni depender de una
    clave."""
    if crear_extractor is None:
        def crear_extractor(motor: LLMEngine, crops: Path) -> ExtractorEngine:
            return ExtractorEngine(motor, carpeta_crops=crops)

    estado = store.cargar_lote(lote_id)

    st.header("3. Ficha (atributos)")
    st.caption(f"Lote «{estado['lote']['nombre']}».")
    st.caption(
        "El extractor **propone** cada dato y **enseña el píxel** del que lo sacó. "
        "Confírmalo de un vistazo; un campo vacío es correcto si no se ve en la foto."
    )

    _mostrar_error_pendiente()

    confirmados_agrupacion = [p for p in estado["productos"] if p["confirmado"]]
    if not confirmados_agrupacion:
        st.info(
            "No hay ningún producto con la agrupación confirmada. Confírmala primero "
            "en «Curar agrupación» — no se extraen atributos de un grupo sin cerrar."
        )
        return

    # El motor (y su caché/coste) es único para toda la pantalla. El cliente
    # de la API se crea PEREZOSO dentro de `LLMEngine` (no aquí): una pantalla
    # que sólo revisa fichas ya extraídas no llega a tocar la API ni su clave
    # (la caché sirve sin clave) — el engine en sí sí se instancia ya.
    motor = crear_motor()
    fotos_por_id = {f["id"]: f for f in estado["fotos"]}

    # BOTÓN DE LOTE ENTERO — arriba del todo, antes de la lista. Pedido
    # urgente de Diego (2026-07-17): "no quiero rellenar las fichas
    # manualmente" — de 2 clics × N productos a 1 clic + 1 confirmación de
    # coste para TODO el lote. Ver el bloque `_dialog_extraer_lote` arriba.
    #
    # `st.container()` ENVOLVIENDO cada bloque condicional, A PROPÓSITO
    # (`decision-making.md` §4/§16, bug reproducido ejecutando `AppTest`):
    # sin el contenedor, el NÚMERO de elementos que este bloque emite antes
    # del bucle de productos cambia entre el run que procesa un click (p.ej.
    # confirmar una ficha) y el run que dispara ese mismo click vía
    # `st.rerun()` -- eso DESPLAZA la posición del contenedor de cada
    # producto entre un run y el siguiente, y `AppTest` (a diferencia de la
    # app real: su cola de mensajes NUNCA se limpia entre un `st.rerun()`
    # interno y el run que lo originó) deja el producto DUPLICADO en el
    # árbol resultante -- un botón "Confirmar ficha" fantasma con la key
    # repetida que el siguiente click puede agarrar por error. Envolver en
    # un contenedor SIEMPRE presente fija la posición de todo lo que viene
    # después, pase lo que pase dentro del contenedor.
    with st.container():
        sin_extraer = _productos_sin_extraer(confirmados_agrupacion)
        if sin_extraer:
            if st.button(
                f"🔎 Extraer TODO el lote ({len(sin_extraer)} producto(s) sin extraer)",
                type="primary",
                use_container_width=True,
            ):
                _dialog_extraer_lote(store, lote_id, sin_extraer, fotos_por_id, motor, crear_extractor)
            st.divider()

    # SELECTOR "RE-EXTRAER LOS SELECCIONADOS" (pedido de Diego, 2026-07-17):
    # "un botón para elegir cuáles extraer... por si un día hay alguno que
    # quiere hacerse a mano o hay que reextraer X". Vacío por defecto --
    # re-extraer es DESTRUCTIVO (descarta lecturas/propuestas previas y,
    # si la ficha está confirmada, también los valores de Diego -- ver el
    # gate dentro de `_dialog_reextraer_seleccionados`) -- nunca se
    # pre-selecciona nada. MISMO motivo de arriba para el `st.container()`.
    with st.container():
        if confirmados_agrupacion:
            productos_por_id = {p["id"]: p for p in confirmados_agrupacion}
            seleccionados_pids = st.multiselect(
                "Elegir productos concretos a (re)extraer",
                options=list(productos_por_id.keys()),
                default=[],
                format_func=lambda pid: _etiqueta_producto_selector(productos_por_id[pid]),
                key="ficha_multiselect_reextraer",
                help=(
                    "Re-extraer DESCARTA las propuestas previas de esa ficha (y, si ya "
                    "la confirmaste, tus valores confirmados también -- se avisa antes "
                    "de gastar)."
                ),
            )
            if st.button(
                f"🔁 Re-extraer los seleccionados ({len(seleccionados_pids)})",
                key="btn_reextraer_seleccionados",
                use_container_width=True,
                disabled=not seleccionados_pids,
            ):
                seleccionados = [productos_por_id[pid] for pid in seleccionados_pids]
                _dialog_reextraer_seleccionados(
                    store, lote_id, seleccionados, fotos_por_id, motor, crear_extractor
                )
            st.divider()

    # SEMBRAR ANTES DE CONTAR — condición de CORRECCIÓN, no de estilo.
    #
    # Lo cazó Diego (2026-07-17): el botón decía "(1)" con las 7 fichas
    # completas en disco. El código estaba bien (una sesión limpia contra su
    # store real cuenta 7); lo que fallaba era el ORDEN. `_sembrar_valores_
    # iniciales` vive dentro de `_render_producto`, o sea DESPUÉS de este
    # bloque — así que el contador leía el `session_state` del render
    # ANTERIOR (con los valores de la extracción vieja) mientras la pantalla
    # de abajo ya pintaba los nuevos. El botón iba un render por detrás.
    #
    # `_construir_confirmado` cae al default cuando la key NO EXISTE, pero no
    # cuando existe y está RANCIA (`[INC-014]`): ésa es la diferencia entre un
    # test —que siempre arranca limpio y por eso nunca vio esto— y la sesión
    # viva de Diego. Sembrar aquí re-siembra por firma antes de contar, así
    # que el contador y la pantalla leen SIEMPRE lo mismo.
    # `deserializar_extraccion` + `_con_obligatorios`: EXACTAMENTE lo que hace
    # `_render_producto` antes de sembrar. Si aquí se sembrara desde el dict
    # SERIAL, `_valor_por_defecto` reventaría (`lecturas` es una lista
    # posicional ahí, no un dict) -- y sobre todo sembraría un default
    # distinto del de la pantalla, que es el bug que este bloque arregla.
    for producto in confirmados_agrupacion:
        if _esta_extraido(producto):
            datos = deserializar_extraccion(producto["campos"])
            _sembrar_valores_iniciales(
                producto["id"], _con_obligatorios(datos.get("campos", {}))
            )

    # BOTÓN "CONFIRMAR TODAS DE GOLPE" — junto al de arriba, pedido de
    # Diego ("menos clics"). N = listos AHORA MISMO (obligatorios completos);
    # se recalcula en el diálogo por si algo cambió entre medias. Si hay
    # pendientes pero NINGUNO está listo, el botón se enseña deshabilitado
    # (nunca escondido: Diego tiene que poder ver por qué no puede pulsarlo).
    # MISMO motivo de arriba para el `st.container()`.
    with st.container():
        pendientes_confirmar = _productos_pendientes_confirmar(confirmados_agrupacion)
        if pendientes_confirmar:
            listos_preview, saltados_preview = _productos_listos_y_saltados(pendientes_confirmar)
            if st.button(
                f"✅ Confirmar todas las fichas listas ({len(listos_preview)})",
                type="primary",
                use_container_width=True,
                disabled=not listos_preview,
            ):
                _dialog_confirmar_lote(store, pendientes_confirmar, motor)
            if saltados_preview:
                st.caption(
                    f"⚠️ {len(saltados_preview)} ficha(s) sin obligatorios completos se "
                    "saltarán (se detallan al abrir el diálogo)."
                )
            st.divider()

    for producto in confirmados_agrupacion:
        _render_producto(store, lote_id, producto, fotos_por_id, motor, crear_extractor)
