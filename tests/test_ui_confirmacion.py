"""Tests de `ui/confirmacion.py` — la pantalla de agrupación (superficie
`agrupacion`, `truth-loop.md` §E y §B).

Regresión del bug BLOQUEANTE diagnosticado por el orquestador:
  1. `_limpiar_seleccion` escribía `st.session_state[f"sel_{fid}"]` desde
     el cuerpo del script DESPUÉS de que los checkboxes ya se habían
     instanciado en el mismo rerun -> `StreamlitAPIException` cada vez
     que Diego pulsaba "Mover" con una foto marcada.
  2. El bug HERMANO, silencioso: "Fusionar" nunca llamaba a
     `_limpiar_seleccion`, así que las fotos fusionadas podían aterrizar
     en el grupo destino con su checkbox todavía marcado, sin que Diego
     lo hubiera tocado ahí — el fallo más caro y más silencioso de
     `truth-loop.md` §E (una foto que parece seleccionada/fuera de sitio
     sin que nadie la haya puesto así).

Se usa `streamlit.testing.v1.AppTest` para ejecutar `ui.confirmacion.render`
de verdad (widgets reales, callbacks reales) contra un `LoteStore` real en
`tmp_path` — nada de esto vive sólo en `st.session_state`
(`.claude/rules/architecture.md`, persistencia).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from core import grouping
from core.images import MetadatosImagen
from core.store import Foto, FotoIlegibleError, LoteStore
from ui.confirmacion import _SENTINEL_NUEVO_GRUPO


def _crear_foto_real(ruta: Path, color: tuple[int, int, int]) -> None:
    """Un JPEG de verdad (no bytes falsos): `ui/confirmacion.py` llama a
    `core.images.obtener_o_crear_miniatura`, que decodifica la imagen de
    verdad con Pillow para generar la miniatura — un fichero corrupto
    rompería el render antes de llegar a la parte que este test cubre."""
    img = Image.new("RGB", (64, 64), color)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, format="JPEG")


def _preparar_lote_dos_grupos(tmp_path: Path) -> tuple[str, list[str], list[str]]:
    """Lote con 2 productos SIN confirmar, 2 fotos cada uno, guardado ya
    en disco vía el store real (`guardar_agrupacion`) — la propuesta
    inicial de `core.grouping.agrupar` no es lo que este test cubre (ver
    `tests/test_grouping*.py`), así que se fija la agrupación a mano para
    tener un punto de partida determinista.

    Devuelve `(lote_id, [producto_a_id, producto_b_id], foto_ids)`.
    """
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote UI", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    colores = [(200, 0, 0), (200, 50, 50), (0, 0, 200), (50, 50, 200)]
    fotos = []
    for i, color in enumerate(colores):
        ruta = carpeta / f"IMG_{i:04d}.jpg"
        _crear_foto_real(ruta, color)
        fotos.append(Foto(ruta=str(ruta), hash=f"hash{i}"))
    foto_ids = store.añadir_fotos(lote_id, fotos)

    grupo_a, grupo_b = foto_ids[:2], foto_ids[2:]
    producto_ids = store.guardar_agrupacion(lote_id, [grupo_a, grupo_b])
    return lote_id, producto_ids, foto_ids


def _script(data_dir: str, lote_id: str) -> None:
    """Cuerpo de la "app" bajo test: sólo `ui.confirmacion.render` contra
    un `LoteStore` que RELEE `data_dir` desde disco — igual que Diego
    cerrando y reabriendo la app de verdad, nunca un objeto en memoria
    compartido por la magia de un closure."""
    from pathlib import Path as _Path

    from core.store import LoteStore as _LoteStore
    from ui import confirmacion as _confirmacion

    _store = _LoteStore(data_dir=_Path(data_dir))
    _confirmacion.render(_store, lote_id)


# --------------------------------------------------------------------------
# Helpers para el rediseño de dos columnas (izquierda = "sueltas"
# `confianza="baja"`, derecha = "grupos" `confianza="media"/"alta"`).
#
# A diferencia de `_preparar_lote_dos_grupos` (que escribe la agrupación a
# mano vía `guardar_agrupacion`, sin EXIF), estos tests necesitan que
# `core/grouping.agrupar` produzca de verdad grupos "baja" (fotos sueltas)
# junto a grupos "media" — así que se fija el reloj con `monkeypatch` y se
# deja que `ui.confirmacion._proponer_grupo_inicial` (dentro de `render()`)
# haga la propuesta real. El resultado es el mismo patrón que ya usa
# `tests/test_grouping_golden.py::test_cambio_rapido_de_producto_*`
# (`monkeypatch.setattr(grouping, "leer_metadatos", ...)`), no una nueva
# convención inventada aquí.
# --------------------------------------------------------------------------
def _metadatos_falsos_por_ruta(mapa: dict[Path, datetime]):
    def _leer(ruta: Path) -> MetadatosImagen:
        return MetadatosImagen(
            ruta=ruta,
            legible=True,
            formato="JPEG",
            ancho=64,
            alto=64,
            orientacion_exif=1,
            fecha_captura_exif=mapa[ruta],
            mtime_fichero=None,
            error=None,
        )

    return _leer


def _preparar_lote_con_sueltas(tmp_path: Path, monkeypatch) -> tuple[str, list[str]]:
    """Lote con UN grupo `media` de 2 fotos (gap corto, 5 s) y DOS fotos
    `baja` sueltas y aisladas (gap largo, >= `UMBRAL_HUECO_SEGUNDOS`, a
    ambos lados) — exactamente la forma real que motiva el rediseño
    (`core/grouping.py` docstring, §Confianza: casi todo corte de más de
    Diego es una foto SOLA).

    Devuelve `(lote_id, [A0, A1, S0, S1])` (ids de foto, en ese orden).
    """
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote UI sueltas", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres_colores = [
        ("A0", (200, 0, 0)),
        ("A1", (200, 50, 50)),
        ("S0", (0, 150, 0)),
        ("S1", (0, 0, 200)),
    ]
    fotos: list[Foto] = []
    rutas: list[Path] = []
    for nombre, color in nombres_colores:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_real(ruta, color)
        rutas.append(ruta)
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}"))
    foto_ids = store.añadir_fotos(lote_id, fotos)

    base = datetime(2026, 7, 1, 12, 0, 0)
    # A0-A1: 5 s de separación (mismo segmento). A1->S0: 95 s (corte). S0->S1:
    # 200 s (corte). S0 y S1 quedan cada una encajonada entre dos pausas
    # largas (o al final de la secuencia) -> singleton "baja".
    segundos = [0, 5, 100, 300]
    mapa = {ruta: base + timedelta(seconds=s) for ruta, s in zip(rutas, segundos)}
    monkeypatch.setattr(grouping, "leer_metadatos", _metadatos_falsos_por_ruta(mapa))

    return lote_id, foto_ids


def _preparar_lote_general(tmp_path: Path, monkeypatch) -> tuple[str, list[str]]:
    """Lote con DOS grupos `media` (A y B, 2 fotos cada uno) y UNA foto
    `baja` suelta (S0) — para el barrido que ejercita las cinco acciones
    (marca, mueve, fusiona, parte, confirma) en un único lote realista.

    Devuelve `(lote_id, [A0, A1, B0, B1, S0])`.
    """
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote UI barrido", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres_colores = [
        ("A0", (200, 0, 0)),
        ("A1", (200, 50, 50)),
        ("B0", (0, 150, 0)),
        ("B1", (0, 180, 0)),
        ("S0", (0, 0, 200)),
    ]
    fotos: list[Foto] = []
    rutas: list[Path] = []
    for nombre, color in nombres_colores:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_real(ruta, color)
        rutas.append(ruta)
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}"))
    foto_ids = store.añadir_fotos(lote_id, fotos)

    base = datetime(2026, 7, 1, 12, 0, 0)
    # A0-A1 (gap 5s) | corte (45s) | B0-B1 (gap 5s) | corte (145s) | S0 sola.
    segundos = [0, 5, 50, 55, 200]
    mapa = {ruta: base + timedelta(seconds=s) for ruta, s in zip(rutas, segundos)}
    monkeypatch.setattr(grouping, "leer_metadatos", _metadatos_falsos_por_ruta(mapa))

    return lote_id, foto_ids


# --------------------------------------------------------------------------
# 1. Mover con una foto marcada: no debe lanzar StreamlitAPIException.
# --------------------------------------------------------------------------
def test_mover_foto_marcada_no_lanza_excepcion_y_el_store_refleja_el_movimiento(tmp_path):
    lote_id, (producto_a, producto_b), foto_ids = _preparar_lote_dos_grupos(tmp_path)
    foto_a1 = foto_ids[0]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    at.checkbox(key=f"sel_{foto_a1}").check().run()
    assert not at.exception

    # Único otro grupo sin confirmar: se selecciona por su ID real (la
    # selectbox resuelve por id + `format_func`, no por índice de un label
    # de texto — MENOR/baja del fix; `AppTest.select_index()` no es
    # compatible con `format_func`, hay que seleccionar por valor).
    at.selectbox(key=f"destino_{producto_a}").select(producto_b).run()
    assert not at.exception

    at.button(key=f"mover_{producto_a}").click().run()

    assert not at.exception, f"'Mover' con foto marcada lanzó: {at.exception}"

    # `guardar_agrupacion` REEMPLAZA todos los productos no confirmados
    # del lote en cada llamada (contrato de `core/store.py`): los ids
    # `producto_a`/`producto_b` de antes del movimiento ya no existen, así
    # que se localiza el grupo resultante por su CONTENIDO (qué fotos
    # tiene), no por un id que el propio movimiento ha regenerado.
    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_con_a1 = next(p for p in no_confirmados if foto_a1 in p["fotos"])
    assert set(grupo_con_a1["fotos"]) == {foto_a1, foto_ids[2], foto_ids[3]}
    otro_grupo = next(p for p in no_confirmados if p["id"] != grupo_con_a1["id"])
    assert otro_grupo["fotos"] == [foto_ids[1]]


# --------------------------------------------------------------------------
# 2. Tras mover, el checkbox de la foto movida queda DESMARCADO.
# --------------------------------------------------------------------------
def test_mover_desmarca_el_checkbox_de_la_foto_movida(tmp_path):
    lote_id, (producto_a, producto_b), foto_ids = _preparar_lote_dos_grupos(tmp_path)
    foto_a1 = foto_ids[0]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.checkbox(key=f"sel_{foto_a1}").check().run()
    at.selectbox(key=f"destino_{producto_a}").select(producto_b).run()
    at.button(key=f"mover_{producto_a}").click().run()

    assert not at.exception
    assert at.checkbox(key=f"sel_{foto_a1}").value is False


# --------------------------------------------------------------------------
# 3. Fusionar: sin excepción, el store fusiona, Y los checkboxes de las
#    fotos fusionadas quedan DESMARCADOS. Este es el bug HERMANO
#    silencioso — antes del fix, `_accion_fusionar` no existía y
#    "Fusionar" nunca limpiaba `sel_*`.
# --------------------------------------------------------------------------
def test_fusionar_no_lanza_excepcion_fusiona_y_desmarca_checkboxes(tmp_path):
    lote_id, (producto_a, producto_b), foto_ids = _preparar_lote_dos_grupos(tmp_path)
    foto_a1, foto_a2 = foto_ids[0], foto_ids[1]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # Diego marca una foto del grupo A (p.ej. para considerar moverla
    # suelta) y luego se decide por fusionar el grupo A entero con el B.
    at.checkbox(key=f"sel_{foto_a1}").check().run()
    assert not at.exception

    at.button(key=f"btn_fusion_{producto_a}").click().run()

    assert not at.exception, f"'Fusionar' lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    assert len(no_confirmados) == 1
    assert set(no_confirmados[0]["fotos"]) == set(foto_ids)

    # El fallo silencioso: SIN el fix, esta key sigue en `True` porque
    # "Fusionar" nunca limpiaba la selección de las fotos que movía.
    assert at.checkbox(key=f"sel_{foto_a1}").value is False
    assert at.checkbox(key=f"sel_{foto_a2}").value is False


# --------------------------------------------------------------------------
# 4. Partir un grupo ("Nuevo grupo (partir)"): sin excepción, un grupo
#    sin confirmar más en el store.
# --------------------------------------------------------------------------
def test_partir_grupo_no_lanza_excepcion_y_crea_un_grupo_mas(tmp_path):
    lote_id, (producto_a, producto_b), foto_ids = _preparar_lote_dos_grupos(tmp_path)
    foto_a1 = foto_ids[0]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.checkbox(key=f"sel_{foto_a1}").check().run()
    assert not at.exception

    # Sentinel `_SENTINEL_NUEVO_GRUPO` = "➕ Nuevo grupo (partir)" (es el
    # valor por defecto; se selecciona explícitamente para no depender de
    # qué trae por defecto el widget). Se selecciona por VALOR, no por
    # índice: la selectbox resuelve por id + `format_func` (fix BAJA), y
    # `AppTest.select_index()` no es compatible con `format_func`.
    at.selectbox(key=f"destino_{producto_a}").select(_SENTINEL_NUEVO_GRUPO).run()
    at.button(key=f"mover_{producto_a}").click().run()

    assert not at.exception, f"'Partir' (Mover a Nuevo grupo) lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    # Antes: A(2 fotos), B(2 fotos). Después de partir 1 foto de A:
    # A(1 foto), B(2 fotos), nuevo(1 foto) = 3 grupos.
    assert len(no_confirmados) == 3
    assert at.checkbox(key=f"sel_{foto_a1}").value is False


# --------------------------------------------------------------------------
# 5. LA OPERACIÓN ESTRELLA: marcar UNA foto suelta (columna izquierda) y
#    pulsar "⬅️ Añadir aquí" en un grupo (columna derecha) — sin excepción,
#    el store la mueve a ESE grupo concreto, y el checkbox queda
#    DESMARCADO (mismo bug hermano de `[INC-006]` que test 2/3, pero para
#    el botón NUEVO de este rediseño).
# --------------------------------------------------------------------------
def test_anadir_suelta_a_grupo_sin_excepcion_mueve_y_desmarca(tmp_path, monkeypatch):
    lote_id, foto_ids = _preparar_lote_con_sueltas(tmp_path, monkeypatch)
    foto_a0, foto_a1, foto_s0, _foto_s1 = foto_ids

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    grupo_media = next(
        p for p in estado["productos"] if not p["confirmado"] and len(p["fotos"]) > 1
    )
    assert set(grupo_media["fotos"]) == {foto_a0, foto_a1}

    # Sin nada marcado, el botón "Añadir aquí" no existe todavía. `at.button`
    # sin `key=` devuelve la lista completa de botones del render actual;
    # `at.button(key=...)` (usado más abajo) lanza `KeyError` si no
    # encuentra ninguno, así que aquí se recorre la lista en vez de indexar.
    assert not any(
        b.key == f"anadir_sueltas_{grupo_media['id']}" for b in at.button
    )

    at.checkbox(key=f"sel_{foto_s0}").check().run()
    assert not at.exception

    at.button(key=f"anadir_sueltas_{grupo_media['id']}").click().run()
    assert not at.exception, f"'Añadir aquí' lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_destino = next(p for p in no_confirmados if foto_s0 in p["fotos"])
    assert set(grupo_destino["fotos"]) == {foto_a0, foto_a1, foto_s0}

    assert at.checkbox(key=f"sel_{foto_s0}").value is False


# --------------------------------------------------------------------------
# 6. Marcar VARIAS sueltas a la vez: una sola pulsación de "Añadir aquí"
#    las mueve TODAS al mismo grupo, y desmarca las DOS.
# --------------------------------------------------------------------------
def test_anadir_varias_sueltas_una_pulsacion_mueve_todas(tmp_path, monkeypatch):
    lote_id, foto_ids = _preparar_lote_con_sueltas(tmp_path, monkeypatch)
    foto_a0, foto_a1, foto_s0, foto_s1 = foto_ids

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    grupo_media = next(
        p for p in estado["productos"] if not p["confirmado"] and len(p["fotos"]) > 1
    )

    at.checkbox(key=f"sel_{foto_s0}").check().run()
    at.checkbox(key=f"sel_{foto_s1}").check().run()
    assert not at.exception

    at.button(key=f"anadir_sueltas_{grupo_media['id']}").click().run()
    assert not at.exception, f"'Añadir aquí' (varias) lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    assert len(no_confirmados) == 1
    assert set(no_confirmados[0]["fotos"]) == {foto_a0, foto_a1, foto_s0, foto_s1}

    assert at.checkbox(key=f"sel_{foto_s0}").value is False
    assert at.checkbox(key=f"sel_{foto_s1}").value is False


# --------------------------------------------------------------------------
# 7. BARRIDO: marca, mueve (añadir-aquí), fusiona, parte, confirma — en
#    un único lote realista (2 grupos + 1 suelta) — ningún paso lanza
#    `StreamlitAPIException`. Es el barrido pedido explícitamente por la
#    tarea, no un único camino feliz.
# --------------------------------------------------------------------------
def test_barrido_de_acciones_no_lanza_streamlit_api_exception(tmp_path, monkeypatch):
    lote_id, foto_ids = _preparar_lote_general(tmp_path, monkeypatch)
    foto_a0, foto_a1, foto_b0, foto_b1, foto_s0 = foto_ids

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_a = next(p for p in no_confirmados if foto_a0 in p["fotos"])
    grupo_b = next(p for p in no_confirmados if foto_b0 in p["fotos"])
    assert set(grupo_a["fotos"]) == {foto_a0, foto_a1}
    assert set(grupo_b["fotos"]) == {foto_b0, foto_b1}

    # --- MARCA: la foto suelta S0, para añadirla más tarde. ---
    at.checkbox(key=f"sel_{foto_s0}").check().run()
    assert not at.exception

    # --- FUSIONA: el grupo A con el grupo B (la selección de S0, de otro
    #     grupo, no debe verse afectada por esto). ---
    at.button(key=f"btn_fusion_{grupo_a['id']}").click().run()
    assert not at.exception, f"'Fusionar' lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_ab = next(p for p in no_confirmados if foto_a0 in p["fotos"])
    assert set(grupo_ab["fotos"]) == {foto_a0, foto_a1, foto_b0, foto_b1}
    # S0 seguía marcada tras fusionar A con B (grupos independientes).
    assert at.checkbox(key=f"sel_{foto_s0}").value is True

    # --- MUEVE (añadir-aquí): S0 se incorpora al grupo AB fusionado. ---
    at.button(key=f"anadir_sueltas_{grupo_ab['id']}").click().run()
    assert not at.exception, f"'Añadir aquí' lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_abs = next(p for p in no_confirmados if foto_s0 in p["fotos"])
    assert set(grupo_abs["fotos"]) == {foto_a0, foto_a1, foto_b0, foto_b1, foto_s0}
    assert len(no_confirmados) == 1

    # --- PARTE: se marca B1 dentro del grupo fusionado y se manda a un
    #     grupo nuevo. ---
    at.checkbox(key=f"sel_{foto_b1}").check().run()
    assert not at.exception
    # Selección por VALOR (sentinel), no por índice: ver nota en el test 4.
    at.selectbox(key=f"destino_{grupo_abs['id']}").select(_SENTINEL_NUEVO_GRUPO).run()
    assert not at.exception
    at.button(key=f"mover_{grupo_abs['id']}").click().run()
    assert not at.exception, f"'Partir' lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    assert len(no_confirmados) == 2
    grupo_nuevo = next(p for p in no_confirmados if p["fotos"] == [foto_b1])

    # --- CONFIRMA: el grupo recién partido, sin excepción, y aparece
    #     como confirmado en el store. ---
    at.button(key=f"confirmar_{grupo_nuevo['id']}").click().run()
    assert not at.exception, f"'Confirmar grupo' lanzó: {at.exception}"

    estado = store.cargar_lote(lote_id)
    confirmados = [p for p in estado["productos"] if p["confirmado"]]
    assert any(p["id"] == grupo_nuevo["id"] for p in confirmados)


# --------------------------------------------------------------------------
# Regresión de los 4 hallazgos CRÍTICOS de `listing-audit` sobre este mismo
# módulo (`.claude/incident-ledger.md` [INC-006] es el bug hermano de más
# arriba; estos son cuatro incidentes NUEVOS, superficie `agrupacion`).
# --------------------------------------------------------------------------
def _metadatos_sin_exif(rutas: list[Path]):
    """Todas las fotos LEGIBLES pero SIN fecha EXIF — el caso más frecuente
    de Diego (WhatsApp la borra, medido 0/59). `core.grouping.agrupar` manda
    todo al cajón de INCIERTAS: todos los grupos "baja", ninguno "media"."""

    def _leer(ruta: Path) -> MetadatosImagen:
        return MetadatosImagen(
            ruta=ruta,
            legible=True,
            formato="JPEG",
            ancho=64,
            alto=64,
            orientacion_exif=1,
            fecha_captura_exif=None,
            mtime_fichero=None,
            error=None,
        )

    return _leer


def _preparar_lote_sin_exif_en_absoluto(tmp_path: Path, monkeypatch) -> tuple[str, list[str]]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote sin EXIF", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres_colores = [("F0", (200, 0, 0)), ("F1", (0, 150, 0)), ("F2", (0, 0, 200))]
    fotos: list[Foto] = []
    rutas: list[Path] = []
    for nombre, color in nombres_colores:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_real(ruta, color)
        rutas.append(ruta)
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}"))
    foto_ids = store.añadir_fotos(lote_id, fotos)

    monkeypatch.setattr(grouping, "leer_metadatos", _metadatos_sin_exif(rutas))
    return lote_id, foto_ids


# --------------------------------------------------------------------------
# CRÍTICO 1: un lote 100% sin EXIF deja `grupos_derecha` vacía -> antes,
# NINGÚN botón se pintaba en toda la pantalla (todos los botones viven en
# `_render_grupo`, que sólo se llama para `grupos_derecha`). El botón "➕
# Crear grupo con las N marcadas" vive en la cabecera de la columna
# izquierda, siempre visible, y resuelve el callejón sin salida.
# --------------------------------------------------------------------------
def test_lote_sin_exif_tiene_boton_para_crear_grupo_con_marcadas(tmp_path, monkeypatch):
    lote_id, foto_ids = _preparar_lote_sin_exif_en_absoluto(tmp_path, monkeypatch)

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton_crear = next((b for b in at.button if b.key == "crear_grupo_sueltas"), None)
    assert boton_crear is not None, (
        "falta el botón «➕ Crear grupo con las marcadas»: en un lote sin EXIF, "
        "ANTES no había NINGÚN botón en toda la pantalla (callejón sin salida)."
    )
    # Sin nada marcado todavía, está deshabilitado — no un gatillo listo.
    assert boton_crear.disabled is True

    at.checkbox(key=f"sel_{foto_ids[0]}").check().run()
    at.checkbox(key=f"sel_{foto_ids[1]}").check().run()
    assert not at.exception

    at.button(key="crear_grupo_sueltas").click().run()
    assert not at.exception, f"'Crear grupo' lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    nuevo = next(
        (p for p in no_confirmados if set(p["fotos"]) == {foto_ids[0], foto_ids[1]}), None
    )
    assert nuevo is not None, "las 2 fotos marcadas no formaron un grupo nuevo"
    # La tercera foto (no marcada) sigue suelta, intacta.
    assert any(p["fotos"] == [foto_ids[2]] for p in no_confirmados)


# --------------------------------------------------------------------------
# CRÍTICO 2: marcar sueltas de DOS productos reales distintos debe mostrar
# sus NOMBRES antes de mover — nunca un gatillo ciego. Se comprueba en la
# barra fija de la columna derecha (fuera del scroll) Y en el caption del
# botón "⬅️ Añadir aquí" dentro de un grupo.
# --------------------------------------------------------------------------
def test_nombres_de_sueltas_marcadas_se_muestran_antes_de_mover(tmp_path, monkeypatch):
    lote_id, foto_ids = _preparar_lote_con_sueltas(tmp_path, monkeypatch)
    _foto_a0, _foto_a1, foto_s0, foto_s1 = foto_ids

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # Nada marcado todavía: ninguna barra de nombres debe aparecer.
    textos_antes = " ".join(i.value for i in at.info)
    assert "S0.jpg" not in textos_antes and "S1.jpg" not in textos_antes

    at.checkbox(key=f"sel_{foto_s0}").check().run()
    at.checkbox(key=f"sel_{foto_s1}").check().run()
    assert not at.exception

    texto_render = " ".join(
        [i.value for i in at.info] + [c.value for c in at.caption] + [w.value for w in at.warning]
    )
    assert "S0.jpg" in texto_render, "el nombre de la foto suelta marcada no se muestra en ningún sitio"
    assert "S1.jpg" in texto_render, "el nombre de la segunda foto suelta marcada no se muestra"


# --------------------------------------------------------------------------
# CRÍTICO 3: un fichero ILEGIBLE (persistido con `legible=False` desde la
# ingesta) NO puede añadirse a un grupo desde la UI (sin checkbox) y
# `store.guardar_agrupacion` lo RECHAZA con dientes si algo intenta
# mezclarlo con otras fotos por debajo.
# --------------------------------------------------------------------------
def _preparar_lote_con_ilegible(tmp_path: Path) -> tuple[str, list[str]]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote con ilegible", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    carpeta.mkdir(parents=True, exist_ok=True)

    ruta_a0 = carpeta / "A0.jpg"
    ruta_a1 = carpeta / "A1.jpg"
    _crear_foto_real(ruta_a0, (200, 0, 0))
    _crear_foto_real(ruta_a1, (200, 50, 50))
    ruta_bad = carpeta / "BAD.jpg"
    ruta_bad.write_bytes(b"no soy una imagen de verdad")

    fotos = [
        Foto(ruta=str(ruta_a0), hash="hash_a0"),
        Foto(ruta=str(ruta_a1), hash="hash_a1"),
        Foto(
            ruta=str(ruta_bad),
            hash="hash_bad",
            legible=False,
            error_lectura="cannot identify image file",
        ),
    ]
    foto_ids = store.añadir_fotos(lote_id, fotos)
    # Igual que lo propondría `core.grouping._grupos_ilegibles`: la
    # ilegible queda SOLA, en su propio grupo de 1.
    store.guardar_agrupacion(lote_id, [foto_ids[:2], [foto_ids[2]]])
    return lote_id, foto_ids


def test_foto_ilegible_no_tiene_checkbox_y_store_rechaza_mezclarla(tmp_path):
    lote_id, foto_ids = _preparar_lote_con_ilegible(tmp_path)
    foto_a0, foto_a1, foto_bad = foto_ids

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    assert not any(cb.key == f"sel_{foto_bad}" for cb in at.checkbox), (
        "la foto ILEGIBLE tiene checkbox: se podría marcar y meter en un grupo"
    )
    boton_descartar = next((b for b in at.button if b.key == f"descartar_{foto_bad}"), None)
    assert boton_descartar is not None, "falta el botón «🗑️ Descartar del lote» para la ilegible"

    # La guardia real: `guardar_agrupacion` rechaza CON EXCEPCIÓN un grupo
    # que mezcle la ilegible con fotos legibles — no la traga en silencio.
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(FotoIlegibleError):
        store.guardar_agrupacion(lote_id, [[foto_a0, foto_a1, foto_bad]])

    # El botón de descarte SÍ funciona y la quita del lote definitivamente.
    at.button(key=f"descartar_{foto_bad}").click().run()
    assert not at.exception, f"'Descartar del lote' lanzó: {at.exception}"
    estado = store.cargar_lote(lote_id)
    assert all(f["id"] != foto_bad for f in estado["fotos"])


# --------------------------------------------------------------------------
# CRÍTICO 4: un fichero corrupto (cabecera intacta, píxeles truncados —
# `leer_metadatos` lo marca `legible=True` porque sólo lee la cabecera) NO
# debe tumbar el render del lote entero. Las fotos buenas siguen curables.
# --------------------------------------------------------------------------
def _crear_foto_truncada(ruta: Path, color: tuple[int, int, int]) -> None:
    """JPEG con cabecera válida (`Image.open` la abre, `leer_metadatos` la
    marca legible) pero datos de píxel truncados: decodificarla de verdad
    (`img.load()`, lo que hace `core.images.abrir_derecha` para la
    miniatura) revienta con `OSError` — reproduce el bug real de
    `core/images.py::obtener_o_crear_miniatura`, independiente de
    `legible` (medido: al 70% de los bytes de un JPEG 300x300, `Image.open`
    sigue abriendo pero `.load()` falla)."""
    img = Image.new("RGB", (300, 300), color)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, format="JPEG")
    datos = ruta.read_bytes()
    ruta.write_bytes(datos[: int(len(datos) * 0.7)])


def test_foto_corrupta_no_tumba_la_pantalla_y_las_buenas_siguen_curables(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote con corrupta", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    ruta_buena = carpeta / "BUENA.jpg"
    _crear_foto_real(ruta_buena, (200, 0, 0))
    ruta_buena2 = carpeta / "BUENA2.jpg"
    _crear_foto_real(ruta_buena2, (0, 200, 0))
    ruta_corrupta = carpeta / "CORRUPTA.jpg"
    _crear_foto_truncada(ruta_corrupta, (0, 0, 200))

    fotos = [
        Foto(ruta=str(ruta_buena), hash="hash_buena"),
        Foto(ruta=str(ruta_buena2), hash="hash_buena2"),
        # `leer_metadatos` marca esta ilegible=False (cabecera intacta) —
        # este test NO depende de la guardia del CRÍTICO 3, es el bug
        # DISTINTO de la miniatura reventando en el render.
        Foto(ruta=str(ruta_corrupta), hash="hash_corrupta"),
    ]
    foto_ids = store.añadir_fotos(lote_id, fotos)
    store.guardar_agrupacion(lote_id, [foto_ids])  # un solo grupo con las 3

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception, f"una foto corrupta tumbó la pantalla entera: {at.exception}"

    # Las fotos buenas se pueden seguir marcando con normalidad: el fallo
    # de una miniatura no dejó el resto de la tarjeta/checkboxes muertos.
    at.checkbox(key=f"sel_{foto_ids[0]}").check().run()
    assert not at.exception
    assert at.checkbox(key=f"sel_{foto_ids[0]}").value is True
