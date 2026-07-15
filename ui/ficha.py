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

from core import pricing
from core.extract import (
    ExtractorEngine,
    deserializar_extraccion,
    serializar_extraccion,
)
from core.llm import LLMEngine, LLMEngineError
from core.schema import Campo, validar_texto
from core.store import LoteStore, StoreError

logger = logging.getLogger(__name__)

# Orden de presentación de los campos. Los que no aparezcan aquí se pintan
# después, en el orden en que vengan. `estado` va al final: SIEMPRE lo pone
# Diego (`truth-loop.md` §A.4), no es una lectura del modelo.
_ORDEN_CAMPOS = (
    "marca", "modelo", "ean", "talla", "color", "composicion", "medidas",
    "estado", "desperfectos", "titulo", "descripcion",
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


def _rellenar_valor(key: str, valor: str) -> None:
    """Callback de un botón "usar «X»": escribe la key del text_input ANTES
    de que se instancie en el próximo rerun (patrón legal de Streamlit; lo
    contrario —escribir la key de un widget ya instanciado— lanza
    StreamlitAPIException, ver `app.py`)."""
    st.session_state[key] = valor


def _render_campo(pid: str, campo: str, datos_campo: dict) -> None:
    propuesta = datos_campo.get("propuesta") or {}
    alternativas = propuesta.get("alternativas") or []
    key_valor = f"ficha_{pid}_{campo}_valor"

    # Título y descripción: borradores del modelo, sin recorte, a ancho
    # completo y en caja multilínea editable.
    if campo in ("titulo", "descripcion"):
        st.markdown(f"**{campo}** · borrador del modelo, edítalo")
        st.text_area(
            campo,
            value=_valor_por_defecto(campo, datos_campo),
            key=key_valor,
            label_visibility="collapsed",
            height=70 if campo == "titulo" else 150,
        )
        return

    col_pixel, col_dato = st.columns([1, 2])

    with col_pixel:
        _render_recorte(propuesta.get("recorte"))
        # Conflicto: la síntesis eligió un valor; las OTRAS candidatas quedan
        # aquí, cada una con su recorte, para cambiar con un click. Nunca se
        # pierde ninguna (`truth-loop.md`: el pipeline no elige a ciegas).
        for i, cand in enumerate(alternativas):
            st.caption(f"otra: **{cand.get('valor')}**")
            _render_recorte(cand.get("recorte"), width=180)
            st.button(
                f"usar «{cand.get('valor')}»",
                key=f"use_{pid}_{campo}_alt{i}",
                on_click=_rellenar_valor,
                args=(key_valor, str(cand.get("valor") or "")),
                use_container_width=True,
            )

    with col_dato:
        st.markdown(
            f"**{campo}** · {_badge_fuente(datos_campo.get('fuente'))} · "
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
            # un literal canónico, se pre-selecciona; si dio una estimación en
            # prosa (el evaluador de estado la da), se muestra como PISTA y
            # Diego elige el literal — un click guiado, no teclear. Pre-sembrar
            # la key antes de instanciar el widget es el patrón legal (no
            # `index=` con la key ya en session_state, que Streamlit rechaza).
            sugerido = datos_campo.get("valor")
            if sugerido and sugerido not in _OPCIONES_ESTADO:
                st.caption(f"El modelo estimó: _{str(sugerido)[:140]}_ — elige el estado:")
            key_est = f"ficha_{pid}_{campo}_estado"
            if key_est not in st.session_state:
                st.session_state[key_est] = (
                    sugerido if sugerido in _OPCIONES_ESTADO else _OPCIONES_ESTADO[0]
                )
            st.selectbox(
                "estado (lo confirmas tú)",
                _OPCIONES_ESTADO,
                key=key_est,
                label_visibility="collapsed",
            )
        else:
            st.text_input(
                campo,
                value=_valor_por_defecto(campo, datos_campo),
                key=key_valor,
                label_visibility="collapsed",
                placeholder="vacío = null",
            )


# --------------------------------------------------------------------------
# Confirmación de la ficha: recoge lo que Diego dejó en cada campo y lo
# persiste con fuente="diego" (su palabra), preservando el recorte/lecturas
# para poder re-revisar. Hecho append-only en `confirmaciones` (tipo='ficha').
# --------------------------------------------------------------------------
def _construir_confirmado(pid: str, serial: dict) -> dict[str, Any]:
    """Se parte del dict SERIALIZADO (`producto['campos']`, rutas de recorte
    como `str`), NO del deserializado (rutas `Path`, que `json.dumps` de
    `confirmar_ficha` no sabe serializar). Se preserva el envoltorio entero
    (`coste_usd`, `fallos`, la propuesta con su recorte para re-revisar) y
    sólo se sobrescriben `valor`/`fuente`/`confianza` con lo que Diego dejó.
    Un campo que Diego dejó vacío es un null CONFIRMADO, no un fallo."""
    # `[INC-011]` (ficha Frankenstein) CON DIENTES: si la extracción avisó de
    # incoherencia (campos de fotos disjuntas), un valor confirmado NUNCA sube
    # a `alta` — el aviso baja el techo, no es sólo un pie de foto (`§12`).
    hay_aviso_coherencia = bool(serial.get("aviso_coherencia"))

    campos_confirmados: dict[str, Any] = {}
    for campo, datos_campo in serial.get("campos", {}).items():
        if campo == "estado":
            elegido = st.session_state.get(f"ficha_{pid}_{campo}_estado", _OPCIONES_ESTADO[0])
            valor = None if elegido == _OPCIONES_ESTADO[0] else elegido
        else:
            crudo = st.session_state.get(f"ficha_{pid}_{campo}_valor", "")
            valor = crudo.strip() or None

        base = dict(datos_campo)  # json-safe: rutas ya son str
        base["valor"] = valor
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


def _accion_confirmar_ficha(store: LoteStore, pid: str, serial: dict) -> bool:
    """Corre en el mismo script run que el click del botón (no en un
    on_click), así que `st.session_state` ya tiene los valores de los
    widgets y `st.error` es válido — mismo criterio que
    `ui/curar.py::_accion_archivar_foto`."""
    confirmado = _construir_confirmado(pid, serial)
    marca = (confirmado.get("campos", {}).get("marca") or {}).get("valor")
    problemas = _problemas_de_texto(confirmado, marca)
    if problemas:
        st.error(
            "Arregla el texto antes de confirmar (Wallapop/Vinted lo rechazan):\n\n"
            + "\n".join(f"- {p}" for p in problemas)
        )
        return False
    try:
        store.confirmar_ficha(pid, confirmado)
    except StoreError as exc:
        logger.exception("No se pudo confirmar la ficha del producto %s", pid)
        st.error(f"No se pudo confirmar la ficha: {exc}")
        return False
    return True


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

        if not _esta_extraido(producto):
            st.caption("Atributos sin extraer todavía.")
            if st.button("🔎 Extraer atributos…", key=f"extraer_{pid}"):
                fotos = _paths_producto(producto, fotos_por_id)
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

        campos = datos.get("campos", {})
        orden = [c for c in _ORDEN_CAMPOS if c in campos] + [
            c for c in campos if c not in _ORDEN_CAMPOS
        ]
        for campo in orden:
            st.divider()
            _render_campo(pid, campo, campos[campo])

        st.divider()
        _render_comparables(datos)

        st.divider()
        col_conf, col_reextraer = st.columns([2, 1])
        with col_conf:
            etiqueta = "✅ Volver a confirmar" if confirmada else "✅ Confirmar ficha"
            if st.button(etiqueta, key=f"confirmar_{pid}", type="primary", use_container_width=True):
                if _accion_confirmar_ficha(store, pid, producto["campos"]):
                    st.rerun()
        with col_reextraer:
            if st.button("🔁 Re-extraer", key=f"reextraer_{pid}", use_container_width=True):
                fotos = _paths_producto(producto, fotos_por_id)
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
    for producto in confirmados_agrupacion:
        _render_producto(store, lote_id, producto, fotos_por_id, motor, crear_extractor)
