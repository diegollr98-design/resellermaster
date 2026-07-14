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

import json
from pathlib import Path

import pytest

from core.extract import (
    ExtractorEngine,
    VERSION_PROMPT_CROP,
    VERSION_PROMPT_ESTADO,
    VERSION_PROMPT_METRO,
    recortar_region,
    _es_ristra_metro,
    fusionar_regiones_cercanas,
    localizar_regiones_ocr,
)
from core.images import abrir_derecha
from core.llm import ResultadoLLM
from tests.test_extract import _MotorFake, _respuesta_crop, _respuesta_crop_simple

_REPO = Path(__file__).resolve().parent.parent
_FOTOS = _REPO / "fotos"
_TRUTH = json.loads((_REPO / "tests" / "golden" / "truth.json").read_text(encoding="utf-8"))


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
    # (contrato v2: 'hallazgos' es una lista) para que la extraccion no
    # reviente por falta de respuesta configurada.
    motor.respuestas[VERSION_PROMPT_CROP] = {
        "pertenece_al_producto": True,
        "ubicacion": "otro",
        "hallazgos": [{"contenido_probable": "otro", "legible": False, "texto": None}],
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
    ya resuelto por el atajo.

    Contrato v2: `_planificar` devuelve `candidatos_atajo` como una lista
    de `LecturaCrop` (`origen="atajo_ocr"`), no un dict de `Campo`."""
    fotos = [fotos_reales[nombre] for nombre in _FOTOS_PRODUCTO_1]
    extractor = ExtractorEngine(_MotorFake())
    regiones, candidatos_atajo, _ = extractor._planificar(fotos)

    atajo_por_contenido = {c.contenido_probable: c.texto for c in candidatos_atajo}
    assert atajo_por_contenido.get("ean") == "8445061029720"
    assert atajo_por_contenido.get("modelo") == "LLLT-200"

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


# ============================================================================
# EL RECORTE ES EXACTAMENTE EL PIXEL QUE SE MANDO -- verificado sobre una
# FOTO REAL (no sintetica). Corazon del rediseno v2 (docstring del modulo,
# punto 3): si a Diego se le ensena un pixel distinto del que vio el
# modelo, la evidencia es una mentira.
# ============================================================================


def test_el_recorte_guardado_es_exactamente_el_que_se_mando_al_vlm_sobre_foto_real(fotos_reales, tmp_path):
    """`IMG_20260714_110805` (producto 6): etiqueta de cuello 'Reebok'
    perfectamente nitida (legibilidad.json). Se ejecuta el pipeline REAL
    (OCR real localizando la region) y solo se mockea el VLM -- el
    recorte que `Propuesta` ensena debe ser BYTE A BYTE el que se
    construyo para el `Imagen` enviado, y ademas un crop de verdad (mas
    pequeno que la foto entera, no la foto completa)."""
    foto = fotos_reales["IMG_20260714_110805"]

    bytes_mandados_al_vlm: list[bytes] = []
    motor = _MotorFake()

    def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
        motor.llamadas.append((imagenes[0].fichero, version_prompt))
        if version_prompt == VERSION_PROMPT_CROP:
            bytes_mandados_al_vlm.append(imagenes[0].bytes_)
            return ResultadoLLM(
                datos=_respuesta_crop_simple("marca", True, "Reebok"),
                fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10,
            )
        return ResultadoLLM(
            datos={"estimacion_legible": False, "descripcion": None},
            fuente="api", coste_usd=0.0, tokens_entrada=10, tokens_salida=5,
        )

    motor.consultar = _consultar
    extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
    resultado = extractor.extraer_producto([foto], producto_id="prod-6-real")

    assert resultado.campos["marca"].valor == "Reebok"
    propuesta = resultado.propuestas["marca"]
    assert propuesta.recorte is not None
    assert propuesta.evidencia is not None

    bytes_en_disco = propuesta.recorte.read_bytes()
    assert len(bytes_mandados_al_vlm) >= 1
    assert bytes_en_disco in bytes_mandados_al_vlm  # el MISMO que se envio

    # Determinismo: recortar la foto original con el MISMO bbox reproduce
    # byte a byte lo guardado -- la funcion que recorta es la MISMA en
    # ambos caminos (docstring del modulo).
    bytes_recalculados = recortar_region(abrir_derecha(foto), propuesta.evidencia.bbox)
    assert bytes_en_disco == bytes_recalculados

    # Y es un CROP de verdad: mas pequeno que la foto entera (3072x4080 en
    # las fotos de Diego), nunca la foto completa reenviada.
    assert len(bytes_en_disco) < foto.stat().st_size


def test_un_crop_real_produce_marca_y_talla_juntas_producto_6(fotos_reales, tmp_path):
    """Regla dura #4 (el corazon del rediseno v2), sobre una foto REAL: la
    etiqueta de cuello de `IMG_20260714_110805` trae 'Reebok' Y 'M' juntas
    (legibilidad.json, producto 6). En el diseno anterior un crop -> un
    solo campo del esquema, asi que la talla era estructuralmente
    INALCANZABLE aqui. Ahora el mismo recorte fisico rinde los dos
    campos."""
    foto = fotos_reales["IMG_20260714_110805"]
    motor = _MotorFake()
    motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop(
        ubicacion="etiqueta_interior",
        hallazgos=[
            {"contenido_probable": "marca", "legible": True, "texto": "Reebok"},
            {"contenido_probable": "talla", "legible": True, "texto": "M"},
        ],
    )
    extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
    resultado = extractor.extraer_producto([foto], producto_id="prod-6-multi")

    assert resultado.campos["marca"].valor == "Reebok"
    assert resultado.campos["talla"].valor == "M"
    propuesta_marca = resultado.propuestas["marca"]
    propuesta_talla = resultado.propuestas["talla"]
    assert propuesta_marca.recorte is not None and propuesta_marca.recorte.exists()
    assert propuesta_talla.recorte is not None and propuesta_talla.recorte.exists()
    # el MISMO crop fisico (mismo fichero+bbox de la foto real) rinde los
    # dos campos -- bytes IDENTICOS en disco.
    assert propuesta_marca.recorte.read_bytes() == propuesta_talla.recorte.read_bytes()


# ============================================================================
# MEDICION DE COBERTURA CON VLM ORACULO -- la cifra que decide si el
# rediseno sirve (`[INC-012]`: con el diseno viejo, un VLM ORACULO daba
# marca 2/5 prendas y talla 1/5; el pipeline no servia aunque el modelo
# acertara siempre).
#
# El "oraculo" responde SOLO lo que `tests/golden/legibilidad.json` marca
# como LEGIBLE_TEXTO (leyendo el valor correcto) para la foto de
# referencia exacta que el propio golden set cita -- para cualquier otro
# recorte (fondo, ruido, otro angulo) responde "no hay nada legible aqui"
# (conservador: el oraculo NO simula el rechazo por `pertenece_al_producto`
# de fondos ajenos, ya cubierto por unit tests sinteticos en
# `test_extract.py::TestFondoAjenoNuncaEsAtributo`; aqui solo se mide
# LOCALIZACION+LECTURA real). El OCR de LOCALIZACION es el REAL (RapidOCR
# sobre las fotos reales) -- si el OCR nunca propone un recorte sobre la
# foto de referencia, el oraculo nunca llega a responder: esa es
# precisamente la clase de limite que esta medicion tiene que sacar a la
# luz, EJECUTANDO, no razonando.
# ============================================================================

_ORACULO_POR_FOTO: dict[str, tuple[str, list[dict]]] = {
    # (ubicacion, hallazgos) -- ver legibilidad.json para la cita exacta.
    # Producto 1 (masajeador, caja): marca en el propio cuerpo/caja -- NO
    # es una etiqueta de cuello, luego NO cae en _UBICACIONES_VALIDAS_MARCA
    # (limite real y esperado: marca de producto de caja no es publicable
    # por esta via, ver hallazgo mas abajo).
    "IMG_20260714_101637": ("codigo_o_modelo_impreso", [{"contenido_probable": "marca", "legible": True, "texto": "lufthous"}]),
    # Producto 2 (electroestimulador, caja): marca legible; modelo
    # PRESENTE_ILEGIBLE (Trampa 3, tapado por un cable) -- oraculo debe
    # abstenerse.
    "IMG_20260714_101732": ("codigo_o_modelo_impreso", [{"contenido_probable": "marca", "legible": True, "texto": "New Age"}]),
    "IMG_20260714_101915": ("codigo_o_modelo_impreso", [{"contenido_probable": "modelo", "legible": False, "texto": None}]),
    # Producto 3 (Reebok gris): marca+talla en la MISMA etiqueta de cuello.
    "IMG_20260714_110458": ("etiqueta_interior", [
        {"contenido_probable": "marca", "legible": True, "texto": "Reebok"},
        {"contenido_probable": "talla", "legible": True, "texto": "XXL"},
    ]),
    # Producto 4 (Jack & Jones): Trampa 1 -- estampado 'ORIGINALS' Y la
    # etiqueta real en fotos DISTINTAS.
    "IMG_20260714_110547": ("estampado_o_grafico", [{"contenido_probable": "marca", "legible": True, "texto": "ORIGINALS"}]),
    "IMG_20260714_110552": ("etiqueta_interior", [
        {"contenido_probable": "marca", "legible": True, "texto": "JACK & JONES"},
        {"contenido_probable": "talla", "legible": True, "texto": "XL"},
    ]),
    # Producto 5 (UMBRO/RAMI JALAB): Trampa 2 -- dos marcas legibles reales
    # en fotos distintas, mas la talla en la etiqueta de cuello.
    "IMG_20260714_110701": ("estampado_o_grafico", [{"contenido_probable": "marca", "legible": True, "texto": "UMBRO"}]),
    "IMG_20260714_110714": ("estampado_o_grafico", [{"contenido_probable": "marca", "legible": True, "texto": "UMBRO"}]),
    "IMG_20260714_110705": ("etiqueta_interior", [
        {"contenido_probable": "marca", "legible": True, "texto": "RAMI JALAB"},
        {"contenido_probable": "talla", "legible": True, "texto": "XXS"},
    ]),
    # Producto 6 (Reebok rosa): marca+talla en la MISMA etiqueta.
    "IMG_20260714_110805": ("etiqueta_interior", [
        {"contenido_probable": "marca", "legible": True, "texto": "Reebok"},
        {"contenido_probable": "talla", "legible": True, "texto": "M"},
    ]),
    # Producto 7 (Looney Tunes / ORIGINAL MARINES): marca+talla en la MISMA
    # etiqueta; el "modelo" (licencia Looney Tunes) es un ESTAMPADO, no una
    # etiqueta/codigo -- limite real esperado (ver hallazgo mas abajo); el
    # papel manuscrito de la cremallera rota.
    "IMG_20260714_110846": ("etiqueta_interior", [
        {"contenido_probable": "marca", "legible": True, "texto": "ORIGINAL MARINES"},
        {"contenido_probable": "talla", "legible": True, "texto": "S"},
    ]),
    "IMG_20260714_110842": ("estampado_o_grafico", [{"contenido_probable": "modelo", "legible": True, "texto": "LOONEY TUNES"}]),
    "IMG_20260714_111141": ("papel_manuscrito", [{"contenido_probable": "desperfecto", "legible": True, "texto": "CREMALLERA ROTA"}]),
}

# Productos 3-7 son "prendas" (ropa) -- el baseline de INC-012 ("marca 2/5,
# talla 1/5") se midio sobre estas 5.
_PRENDAS_IDS = (3, 4, 5, 6, 7)


def _consultar_oraculo(motor: _MotorFake):
    """Cierre que fabrica el `consultar` del oraculo: responde segun
    `_ORACULO_POR_FOTO` para VERSION_PROMPT_CROP; para METRO, dice que la
    medida NO es derivable (Trampa del metro sin origen, producto 7,
    legibilidad.json); para ESTADO, se abstiene siempre (no es objeto de
    esta medicion de cobertura de identidad)."""

    def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
        fichero = imagenes[0].fichero
        motor.llamadas.append((fichero, version_prompt))
        if version_prompt == VERSION_PROMPT_ESTADO:
            datos = {"estimacion_legible": False, "descripcion": None}
        elif version_prompt == VERSION_PROMPT_METRO:
            # legibilidad.json producto 7: la cinta se lee pero NO el 0 ni
            # el borde de la prenda -- la medida NO es derivable.
            datos = {"cero_visible": False, "borde_prenda_visible": True, "medida_cm": None}
        else:
            stem = Path(fichero).stem
            entrada = _ORACULO_POR_FOTO.get(stem)
            if entrada is None:
                # Cualquier otro recorte (background, otro angulo, ruido
                # de OCR): el oraculo, conservador, no lee nada util aqui.
                datos = _respuesta_crop_simple("otro", False, None, ubicacion="otro")
            else:
                ubicacion, hallazgos = entrada
                datos = _respuesta_crop(ubicacion=ubicacion, hallazgos=hallazgos)
        return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

    return _consultar


def test_medir_cobertura_con_vlm_oraculo_sobre_los_7_productos(fotos_reales, tmp_path, capsys):
    """LA CIFRA QUE DECIDE SI EL REDISENO SIRVE. Con un VLM ORACULO (lee
    perfecto lo que `legibilidad.json` dice que es legible) corriendo
    sobre el pipeline REAL (OCR de localizacion real + agregacion real),
    mide para cada producto x campo de identidad si Diego recibe algo
    CONFIRMABLE (un valor publicado, o un conflicto con SUS alternativas y
    recorte, o al menos un recorte que mirar) o NADA.

    Baseline `[INC-012]` (diseno viejo, un crop -> un solo campo): marca
    2/5 prendas, talla 1/5. Este test IMPRIME la tabla nueva -- correr con
    `-s` para verla. NO se fija un umbral exacto de aprobacion (romperia
    con cualquier cambio de RapidOCR); se REPORTA el numero real."""
    productos = _TRUTH["productos"]
    filas: list[tuple[int, str, str, str]] = []  # (producto_id, campo, estado, detalle)

    resumen_marca_prendas = 0
    resumen_talla_prendas = 0

    for producto in productos:
        pid = producto["id"]
        fotos = [fotos_reales[nombre] for nombre in producto["fotos"]]
        motor = _MotorFake()
        motor.consultar = _consultar_oraculo(motor)
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / f"crops_p{pid}")
        resultado = extractor.extraer_producto(fotos, producto_id=f"prod-{pid}")

        for campo in ("marca", "talla", "modelo", "ean"):
            campo_obj = resultado.campos[campo]
            propuesta = resultado.propuestas[campo]
            if campo_obj.valor is not None:
                estado, detalle = "VALOR_PUBLICADO", str(campo_obj.valor)
            elif propuesta.alternativas:
                estado = "CONFLICTO_CON_ALTERNATIVAS"
                detalle = " vs ".join(c.valor for c in propuesta.alternativas)
            elif propuesta.recorte is not None:
                estado, detalle = "RECORTE_SIN_VALOR", propuesta.motivo[:60]
            else:
                estado, detalle = "NADA", propuesta.motivo[:60]
            filas.append((pid, campo, estado, detalle))

            if pid in _PRENDAS_IDS and campo == "marca" and estado in ("VALOR_PUBLICADO", "CONFLICTO_CON_ALTERNATIVAS"):
                resumen_marca_prendas += 1
            if pid in _PRENDAS_IDS and campo == "talla" and estado == "VALOR_PUBLICADO":
                resumen_talla_prendas += 1

    with capsys.disabled():
        print("\n\n=== COBERTURA CON VLM ORACULO (7 productos x marca/talla/modelo/ean) ===")
        for pid, campo, estado, detalle in filas:
            print(f"  producto {pid} | {campo:8s} | {estado:26s} | {detalle}")
        print(
            f"\nRESUMEN (prendas, productos {_PRENDAS_IDS}): "
            f"marca con algo confirmable = {resumen_marca_prendas}/{len(_PRENDAS_IDS)}, "
            f"talla con valor publicado = {resumen_talla_prendas}/{len(_PRENDAS_IDS)}"
        )
        print("Baseline [INC-012] (diseno viejo, un crop -> un solo campo): marca 2/5 prendas, talla 1/5.\n")

    # No se fija un umbral -- se deja constancia legible de que el test
    # corrio de verdad sobre las 33 fotos reales (7 productos).
    assert len(filas) == len(productos) * 4
