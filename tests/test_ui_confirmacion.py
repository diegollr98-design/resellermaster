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

from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.store import Foto, LoteStore


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
# 1. Mover con una foto marcada: no debe lanzar StreamlitAPIException.
# --------------------------------------------------------------------------
def test_mover_foto_marcada_no_lanza_excepcion_y_el_store_refleja_el_movimiento(tmp_path):
    lote_id, (producto_a, producto_b), foto_ids = _preparar_lote_dos_grupos(tmp_path)
    foto_a1 = foto_ids[0]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    at.checkbox(key=f"sel_{foto_a1}").check().run()
    assert not at.exception

    # Único otro grupo sin confirmar: índice 1 de la selectbox de destino
    # (índice 0 es "Nuevo grupo (partir)").
    at.selectbox(key=f"destino_{producto_a}").select_index(1).run()
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
    at.selectbox(key=f"destino_{producto_a}").select_index(1).run()
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

    # Índice 0 de la selectbox de destino = "➕ Nuevo grupo (partir)" (es
    # el valor por defecto; se selecciona explícitamente para no depender
    # de qué trae por defecto el widget).
    at.selectbox(key=f"destino_{producto_a}").select_index(0).run()
    at.button(key=f"mover_{producto_a}").click().run()

    assert not at.exception, f"'Partir' (Mover a Nuevo grupo) lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    # Antes: A(2 fotos), B(2 fotos). Después de partir 1 foto de A:
    # A(1 foto), B(2 fotos), nuevo(1 foto) = 3 grupos.
    assert len(no_confirmados) == 3
    assert at.checkbox(key=f"sel_{foto_a1}").value is False
