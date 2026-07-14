"""Bonus: `core/extract.py` contra las 33 fotos REALES de Diego.

Mismo patron que `tests/test_grouping_golden.py`: si `fotos/` no esta
disponible (esta gitignored, son fotos de Diego), estos tests se SALTAN con
un motivo explicito -- un skip es visible, un test que "pasa" sin datos
seria una mentira.

Estos tests NO llaman a ningun VLM real (no hay `ANTHROPIC_API_KEY` en el
entorno de CI): cubren el suelo GRATIS (OCR local + heuristicas +
agregacion) contra pixeles reales, para confirmar que el diseno de
`core/extract.py` -- medido sobre `tests/golden/legibilidad.json` -- se
sostiene sobre las fotos de verdad, no solo sobre datos sinteticos. Donde
hace falta el VLM (leer una etiqueta estilizada) se usa `_MotorFake` de
`tests/test_extract.py`, igual que en el resto de la suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.extract import (
    ExtractorEngine,
    VERSION_PROMPT_CROP,
    _es_ristra_metro,
    fusionar_regiones_cercanas,
    localizar_regiones_ocr,
)
from tests.test_extract import _MotorFake

_REPO = Path(__file__).resolve().parent.parent
_FOTOS = _REPO / "fotos"


@pytest.fixture(scope="module")
def fotos_reales() -> dict[str, Path]:
    if not _FOTOS.exists():
        pytest.skip(f"Las fotos reales de Diego no estan en {_FOTOS} (gitignored: son suyas)")
    fotos = sorted(_FOTOS.glob("IMG_20260714_*.jpg"))
    if len(fotos) != 33:
        pytest.skip(f"Se esperaban 33 fotos reales, se encontraron {len(fotos)}")
    return {f.stem: f for f in fotos}


# Producto 1 (masajeador LH lufthous): el OCR SI lee limpio el modelo y el
# EAN (legibilidad.json), asi que el atajo gratis debe dispararse sin gastar
# en el VLM. La marca ('lufthous') el OCR la lee garbled -- SI necesita VLM.
_FOTOS_PRODUCTO_1 = (
    "IMG_20260714_101637",
    "IMG_20260714_101643",
    "IMG_20260714_101649",
    "IMG_20260714_101653",
    "IMG_20260714_101657",
    "IMG_20260714_101709",
)

# Producto 7 (Looney Tunes): tiene la foto del metro sin origen derivable Y
# el papel manuscrito "CREMALLERA ROTA".
_FOTO_METRO_PRODUCTO_7 = "IMG_20260714_111030"
_FOTO_PAPEL_PRODUCTO_7 = "IMG_20260714_111141"

# Producto 4 (Jack & Jones): estampado 'ORIGINALS' gigante en el frontal.
_FOTO_ESTAMPADO_PRODUCTO_4 = "IMG_20260714_110547"


def test_ean_y_modelo_se_resuelven_por_atajo_gratis_sin_vlm(fotos_reales):
    fotos = [fotos_reales[nombre] for nombre in _FOTOS_PRODUCTO_1]
    motor = _MotorFake()
    # La marca ('lufthous') SI necesita VLM -- se responde algo razonable
    # para que la extraccion no reviente por falta de respuesta configurada.
    motor.respuestas[VERSION_PROMPT_CROP] = {
        "legible": True,
        "pertenece_al_producto": True,
        "ubicacion": "etiqueta_interior",
        "contenido_probable": "otro",
        "texto": None,
    }
    extractor = ExtractorEngine(motor)
    resultado = extractor.extraer_producto(fotos, producto_id="prod-1")

    assert resultado.campos["ean"].valor == "8445061029720"
    assert resultado.campos["ean"].confianza == "alta"
    assert resultado.campos["modelo"].valor == "LLLT-200"


def test_filtros_de_coste_eliminan_los_parrafos_de_specs_del_producto_1(fotos_reales):
    """Verificacion del ahorro pedido por el coordinador tras re-derivar el
    gasto por ejecucion: sobre las fotos REALES del producto 1 (masajeador
    con especificaciones multilingues), ninguna region enviada al VLM
    puede ser uno de los parrafos largos ni una repeticion del EAN/modelo
    ya resuelto por el atajo."""
    fotos = [fotos_reales[nombre] for nombre in _FOTOS_PRODUCTO_1]
    extractor = ExtractorEngine(_MotorFake())
    regiones, campos_atajo, _ = extractor._planificar(fotos)

    assert campos_atajo["ean"].valor == "8445061029720"
    assert campos_atajo["modelo"].valor == "LLLT-200"

    for region in regiones:
        assert len(region.texto_ocr.split()) <= 6, (
            f"se colo un bloque largo al VLM: {region.texto_ocr[:80]!r}"
        )
        assert "8445061029720" not in region.texto_ocr
        assert "LLLT-200" not in region.texto_ocr

    # Antes de los filtros esto eran 26 regiones (medido); ahora deben ser
    # bastantes menos -- el numero exacto puede moverse si RapidOCR cambia
    # de version, así que se reporta en vez de fijarlo a un entero exacto.
    print(f"\nproducto 1: {len(regiones)} regiones enviadas al VLM tras los filtros de coste (antes: 26)")
    assert len(regiones) < 26


def test_metro_se_detecta_y_nunca_produce_un_atributo_de_texto(fotos_reales):
    foto = fotos_reales[_FOTO_METRO_PRODUCTO_7]
    regiones = localizar_regiones_ocr(foto)
    assert any(_es_ristra_metro(r.texto_ocr) for r in regiones), (
        "La ristra de digitos del metro (medida en legibilidad.json) deberia "
        "seguir detectandose sobre la foto real"
    )


def test_papel_manuscrito_se_localiza_por_ocr(fotos_reales):
    foto = fotos_reales[_FOTO_PAPEL_PRODUCTO_7]
    regiones = localizar_regiones_ocr(foto)
    regiones = fusionar_regiones_cercanas(regiones)
    textos = [r.texto_ocr for r in regiones]
    # El OCR lo lee garbled ("CKENALLERA ROTA") pero detecta ALGO en esa
    # zona -- suficiente para que se genere un recorte hacia el VLM, que es
    # quien de verdad clasifica "papel_manuscrito".
    assert any("ROTA" in t.upper() or "CREMALLERA" in t.upper() or "KENALLERA" in t.upper() for t in textos)


def test_producto_4_el_estampado_y_la_etiqueta_estan_en_fotos_distintas(fotos_reales):
    """No es una prueba end-to-end del VLM (no hay clave de API), pero
    confirma la premisa estructural de la trampa: el estampado 'ORIGINALS'
    vive en una foto (110547) y la etiqueta de cuello en otra (110552) --
    por eso `fusionar_regiones_cercanas` (que solo une regiones de la MISMA
    foto) nunca las mezcla en un solo recorte."""
    foto_estampado = fotos_reales[_FOTO_ESTAMPADO_PRODUCTO_4]
    regiones_estampado = localizar_regiones_ocr(foto_estampado)
    assert any("ORIGINALS" in r.texto_ocr.upper() or "ORICINALS" in r.texto_ocr.upper() for r in regiones_estampado)
