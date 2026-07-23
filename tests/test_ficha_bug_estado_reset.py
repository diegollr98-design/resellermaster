"""REPRODUCTOR del bug: al confirmar el producto A, el selectbox `estado` de
OTRO producto B (sin confirmar, en el mismo run) se resetea a "(sin elegir)".
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.extract import (
    Campo,
    Propuesta,
    ResultadoExtraccion,
    Lectura,
    serializar_extraccion,
)
from core.store import Foto, LoteStore


def _crear_img(ruta: Path, color=(120, 60, 60)) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(ruta, format="JPEG")


def _extraccion_lista(crops: Path) -> ResultadoExtraccion:
    """Ficha con categoria/titulo/descripcion propuestos, `estado` pendiente
    (SIEMPRE lo elige Diego)."""
    campos = {
        "marca": Campo(valor="Reebok", fuente="inferido", confianza="baja"),
        "categoria": Campo(valor="moda", fuente="inferido", confianza="baja"),
        "estado": Campo(valor=None, fuente="inferido", confianza="baja"),
        "titulo": Campo(valor="Sudadera Reebok M", fuente="inferido", confianza="baja"),
        "descripcion": Campo(valor="Sudadera en buen estado.", fuente="inferido", confianza="baja"),
    }
    propuestas = {
        "marca": Propuesta(campo="marca", valor="Reebok", recorte=None, evidencia=None,
                           lecturas=(Lectura(origen="vlm", texto="Reebok"),), motivo="mejor intento"),
        "categoria": Propuesta(campo="categoria", valor="moda", recorte=None, evidencia=None, motivo="clasif"),
        "estado": Propuesta(campo="estado", valor=None, recorte=None, evidencia=None, motivo="lo pones tu"),
        "titulo": Propuesta(campo="titulo", valor="Sudadera Reebok M", recorte=None, evidencia=None, motivo="borrador"),
        "descripcion": Propuesta(campo="descripcion", valor="Sudadera en buen estado.", recorte=None, evidencia=None, motivo="borrador"),
    }
    return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.02)


def _preparar_dos(tmp_path: Path) -> tuple[str, str, str]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote 2 productos", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    r1, r2 = carpeta / "IMG_1.jpg", carpeta / "IMG_2.jpg"
    _crear_img(r1)
    _crear_img(r2, (60, 120, 60))
    fids = store.añadir_fotos(lote_id, [Foto(ruta=str(r1), hash="h1"), Foto(ruta=str(r2), hash="h2")])
    pids = store.guardar_agrupacion(lote_id, [[fids[0]], [fids[1]]])
    for pid in pids:
        store.confirmar_producto(pid)
        store.guardar_extraccion(pid, serializar_extraccion(_extraccion_lista(tmp_path / "crops_fake")))
    return lote_id, pids[0], pids[1]


def _script(data_dir: str, lote_id: str) -> None:
    from pathlib import Path as _Path
    from core.llm import ResultadoLLM as _ResultadoLLM
    from core.store import LoteStore as _LoteStore
    from ui import ficha as _ficha

    class _MotorTextoFake:
        def consultar_texto(self, prompt, json_schema, version_prompt=None, producto_id=None):
            return _ResultadoLLM(
                datos={"titulo": "Titulo IA", "descripcion": "Descripcion IA generada."},
                fuente="api", coste_usd=0.00005, tokens_entrada=120, tokens_salida=60,
            )

    _ficha.render(_LoteStore(data_dir=_Path(data_dir)), lote_id, crear_motor=lambda: _MotorTextoFake())


def _estado_sel(at, pid):
    return next(s for s in at.selectbox if s.key == f"ficha_{pid}_estado_estado")


def test_confirmar_A_no_resetea_estado_de_B(tmp_path):
    lote_id, pid_a, pid_b = _preparar_dos(tmp_path)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # Diego elige estado en AMBOS productos (aun sin confirmar B).
    _estado_sel(at, pid_a).set_value("Bueno").run()
    _estado_sel(at, pid_b).set_value("Muy bueno").run()

    # sanity: B tiene su estado elegido antes de confirmar A
    assert _estado_sel(at, pid_b).value == "Muy bueno"

    # Diego confirma A.
    at.button(key=f"confirmar_{pid_a}").click().run()
    assert not at.exception

    # BUG: el estado de B (sin confirmar) debe seguir siendo "Muy bueno".
    assert _estado_sel(at, pid_b).value == "Muy bueno", (
        f"REGRESION: estado de B se reseteo a {_estado_sel(at, pid_b).value!r}"
    )


def test_confirmar_B_no_resetea_estado_de_A_rendido_antes(tmp_path):
    """Confirma el SEGUNDO producto (B). A se renderiza ANTES en el bucle,
    asi que ya esta instanciado cuando el st.rerun() de B se dispara -> A NO
    deberia resetearse. Aisla que la causa es el ORDEN de render (GC de
    widgets no instanciados en el run que corta con st.rerun())."""
    lote_id, pid_a, pid_b = _preparar_dos(tmp_path)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    _estado_sel(at, pid_a).set_value("Bueno").run()
    _estado_sel(at, pid_b).set_value("Muy bueno").run()
    at.button(key=f"confirmar_{pid_b}").click().run()
    assert not at.exception
    print("estado A tras confirmar B:", _estado_sel(at, pid_a).value)
    print("estado B (confirmado):", _estado_sel(at, pid_b).value)


def test_confirmar_A_tambien_pierde_categoria_y_texto_editado_de_B(tmp_path):
    """No sólo `estado`: el bug-hunter verificó que `categoria` (selectbox)
    y `marca` (text_input) de B también se perdían -- el fix se aplica a
    TODOS los campos sembrados en `_sembrar_valores_iniciales`, no sólo a
    `estado`."""
    lote_id, pid_a, pid_b = _preparar_dos(tmp_path)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    # B: cambia categoria y edita marca (text_input) sin confirmar
    next(s for s in at.selectbox if s.key == f"ficha_{pid_b}_categoria_categoria").set_value("electronica").run()
    next(t for t in at.text_input if t.key == f"ficha_{pid_b}_marca_valor").set_value("EDITADO_POR_DIEGO").run()
    # A: dejar listo y confirmar
    _estado_sel(at, pid_a).set_value("Bueno").run()
    at.button(key=f"confirmar_{pid_a}").click().run()
    assert not at.exception
    cat_b = next(s for s in at.selectbox if s.key == f"ficha_{pid_b}_categoria_categoria").value
    marca_b = next(t for t in at.text_input if t.key == f"ficha_{pid_b}_marca_valor").value
    assert cat_b == "electronica", f"REGRESION: categoria de B se reseteo a {cat_b!r}"
    assert marca_b == "EDITADO_POR_DIEGO", f"REGRESION: marca de B se reseteo a {marca_b!r}"
