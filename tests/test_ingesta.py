"""Tests de `ui/ingesta.py` — HALLAZGO 3 (`listing-audit`, 2026-07-14).

Medido: primer render de "Curar agrupación" con la caché de miniaturas
FRÍA cuesta 8,4 s con 33 fotos y 17,9 s con 26 (el rerun caliente sí es
0,14 s — el docstring viejo de `ui/curar.py` sólo cubría ese caso).
`show_spinner=False` deja la pantalla EN BLANCO durante esos segundos justo
al llegar al curado. El fix mueve la generación de la miniatura a la
ingesta (`_ingerir`, que ya recorre cada fichero para EXIF/hash — el
momento natural, y donde Diego ya está mirando una barra de progreso), así
que el curado llega con la caché ya caliente.

Este fichero comprueba el contrato observable: tras `_ingerir()`, la
miniatura cacheada de CADA foto legible del lote ya existe en disco, en el
mismo directorio que lee `ui/curar.py::_miniatura_de` — sin depender de
ningún render de la pantalla de curado.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.images import TAMANO_MINIATURA_DEFECTO, es_soportada, nombre_miniatura
from core.store import LoteStore
from ui.ingesta import _DIR_CACHE_MINIATURAS


def _crear_foto_real(ruta: Path, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (64, 64), color)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, format="JPEG")


def _script(data_dir: str, carpeta_origen: str, nombre_lote: str) -> None:
    from pathlib import Path as _Path

    from core.images import es_soportada as _es_soportada
    from core.store import LoteStore as _LoteStore
    from ui.ingesta import _ingerir as _ingerir_real

    _store = _LoteStore(data_dir=_Path(data_dir))
    _carpeta = _Path(carpeta_origen)
    _rutas = sorted(p for p in _carpeta.iterdir() if _es_soportada(p))
    _ingerir_real(_store, nombre_lote, carpeta_origen, _rutas)


def test_ingesta_pregenera_miniaturas_en_cache_de_todas_las_fotos_legibles(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    for i in range(4):
        _crear_foto_real(origen / f"foto_{i}.jpg", (10 * i, 20 * i, 30 * i))

    data_dir = tmp_path / "data"
    at = AppTest.from_function(
        _script, args=(str(data_dir), str(origen), "Lote miniaturas H3")
    ).run()
    assert not at.exception, f"la ingesta con pre-generación lanzó: {at.exception}"

    store = LoteStore(data_dir=data_dir)
    lotes = store.listar_lotes()
    assert len(lotes) == 1
    estado = store.cargar_lote(lotes[0]["id"])
    assert len(estado["fotos"]) == 4

    for foto in estado["fotos"]:
        assert foto["legible"]
        ruta_miniatura = _DIR_CACHE_MINIATURAS / nombre_miniatura(
            foto["hash"], TAMANO_MINIATURA_DEFECTO
        )
        assert ruta_miniatura.exists(), (
            f"la miniatura de {foto['ruta']} (hash {foto['hash']}) no se pre-generó "
            "durante la ingesta — HALLAZGO 3 sigue abierto"
        )


def test_ingesta_con_fichero_ilegible_no_aborta_y_no_intenta_pregenerar_su_miniatura(tmp_path):
    """Un fichero corrupto no tiene píxeles que miniaturizar: la ingesta
    debe seguir sin excepción y sin marcar un segundo error extra por la
    miniatura (el `n_con_error` ya lo cuenta `leer_metadatos`)."""
    origen = tmp_path / "origen"
    origen.mkdir()
    _crear_foto_real(origen / "buena.jpg", (50, 50, 50))
    (origen / "mala.jpg").write_bytes(b"no soy una imagen de verdad")

    data_dir = tmp_path / "data"
    at = AppTest.from_function(
        _script, args=(str(data_dir), str(origen), "Lote con ilegible H3")
    ).run()
    assert not at.exception, f"la ingesta con un fichero ilegible lanzó: {at.exception}"

    store = LoteStore(data_dir=data_dir)
    lotes = store.listar_lotes()
    estado = store.cargar_lote(lotes[0]["id"])
    assert len(estado["fotos"]) == 2

    buena = next(f for f in estado["fotos"] if "buena" in f["ruta"])
    mala = next(f for f in estado["fotos"] if "mala" in f["ruta"])
    # `store.cargar_lote` no convierte `fotos.legible` a `bool` (a
    # diferencia de `productos.confirmado`): SQLite lo devuelve como
    # INTEGER 0/1 — se compara por verdad, no por identidad con `True`.
    assert bool(buena["legible"]) is True
    assert bool(mala["legible"]) is False

    ruta_miniatura_buena = _DIR_CACHE_MINIATURAS / nombre_miniatura(
        buena["hash"], TAMANO_MINIATURA_DEFECTO
    )
    assert ruta_miniatura_buena.exists()

    ruta_miniatura_mala = _DIR_CACHE_MINIATURAS / nombre_miniatura(
        mala["hash"], TAMANO_MINIATURA_DEFECTO
    )
    assert not ruta_miniatura_mala.exists()


def test_es_soportada_helper_disponible_para_el_script_de_test():
    # Guard trivial: si el import de arriba se rompiera, este test lo cazaría
    # antes que un fallo confuso en `_script`.
    assert es_soportada(Path("x.jpg"))
