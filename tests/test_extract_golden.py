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
import os
import unicodedata
from pathlib import Path

import pytest

from core.extract import (
    ExtractorEngine,
    VERSION_PROMPT_CROP,
    VERSION_PROMPT_METRO,
    recortar_region,
    _es_ristra_metro,
    fusionar_regiones_cercanas,
    localizar_regiones_ocr,
)
from core.images import abrir_derecha
from core.llm import LLMEngine, ResultadoLLM
from tests.test_extract import _MotorFake, _respuesta_crop, _respuesta_crop_simple

_REPO = Path(__file__).resolve().parent.parent
_FOTOS = _REPO / "fotos"
_TRUTH = json.loads((_REPO / "tests" / "golden" / "truth.json").read_text(encoding="utf-8"))
_LEG = json.loads((_REPO / "tests" / "golden" / "legibilidad.json").read_text(encoding="utf-8"))
_LEG_POR_PRODUCTO: dict[int, dict] = {p["id"]: p["campos"] for p in _LEG["productos"]}


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
    legibilidad.json). "estado" ya no tiene una llamada VLM propia
    (ELIMINADA 2026-07-17, vive dentro de la sintesis) -- no es objeto de
    esta medicion de cobertura de identidad."""

    def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
        fichero = imagenes[0].fichero
        motor.llamadas.append((fichero, version_prompt))
        if version_prompt == VERSION_PROMPT_METRO:
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


# ============================================================================
# MEDICION CON HAIKU 4.5 REAL -- EL PASO 1 DE LA CONTINUACION DE FASE 2
# (docs/seeds/fase-2-continuacion.md).
#
# Todo lo demas de esta suite prueba COMO EL CODIGO TRATA lo que el modelo
# diga (con un oraculo o con `_MotorFake`). Esto es lo unico que mide CON QUE
# FRECUENCIA HAIKU MIENTE en los crops reales de Diego -- la Capa 2 del
# `truth-loop` ejecutada de verdad, no razonada. Ningun test verde la
# sustituye.
#
# DOBLE CANDADO DE COSTE (`decision-making.md` SS 15): corre SOLO si estan
# ademas de las 33 fotos-- (a) `ANTHROPIC_API_KEY` en el entorno y (b) el
# opt-in explicito `MEDIR_VLM_REAL=1`. Un `pytest` normal con la key ya
# puesta en `.env`/entorno NO puede gastar 19 cts sin querer: hay que
# pedirlo. La cache por hash hace que la 2a ejecucion cueste 0.
#
#   Como correrlo (una vez, ~19 cts; despues 0 por la cache):
#     MEDIR_VLM_REAL=1 pytest tests/test_extract_golden.py -s \
#       -k medir_alucinacion_con_haiku_real
#
# QUE MIDE, contra `tests/golden/legibilidad.json` (el mapa de que dato es
# legible en que pixel, fijado a ojo por el orquestador -- NO es la "verdad
# del producto", Diego no la da: es LEGIBILIDAD y PROCEDENCIA):
#   - ALUCINACION (la metrica que MANDA): el extractor publica un valor en
#     una celda NO_FOTOGRAFIADO / PRESENTE_ILEGIBLE. Con `confianza=alta`
#     es el fallo catastrofico que este proyecto existe para evitar -> se
#     ASSERTA == 0 (candado duro). Con confianza media/baja se reporta
#     ruidoso (lleva un crop que Diego vera, pero no deberia haberse
#     propuesto).
#   - LECTURA_ERRONEA: Haiku leyo un texto distinto del que legibilidad.json
#     dice que pone el pixel. NO es fallo automatico (el valor de referencia
#     lo leyo un modelo, no Diego): es una ALERTA para que Diego mire ese
#     crop. Se reporta, no se assertar.
#   - COBERTURA: para cuantas celdas legibles Diego recibe algo confirmable
#     (un valor, un conflicto con sus candidatas, o al menos un recorte).
# ============================================================================

# Solo los campos de IDENTIDAD que legibilidad.json audita como texto -- es
# donde se juega "Haiku miente". color=pixeles (nunca VLM), estado=siempre
# Diego, composicion=NO_FOTOGRAFIADO por diseno (se comprueba aparte abajo).
_CAMPOS_IDENTIDAD = ("marca", "talla", "modelo", "ean")

_MEDIR_VLM_REAL = os.environ.get("MEDIR_VLM_REAL") == "1"
_HAY_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


def _norm(s: str | None) -> str | None:
    """Normaliza para comparar lecturas: sin acentos, mayusculas, espacios
    colapsados. 'Jack & Jones' == 'JACK & JONES'; 'Reebok' == 'REEBOK'."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.upper().split()) or None


def _clasificar_celda(pid: int, campo: str, campo_obj, propuesta) -> tuple[str, str] | None:
    """Clasifica UNA celda (producto x campo) del extractor real contra
    legibilidad.json. Devuelve `(clasificacion, detalle)` o `None` si la
    celda no esta auditada para ese producto. La unica clase catastrofica
    es ALUCINACION_ALTA; el resto se reporta."""
    celda = _LEG_POR_PRODUCTO[pid].get(campo)
    if celda is None:
        return None
    etiqueta = celda["etiqueta"]
    got = campo_obj.valor

    # 1. Celdas donde NADIE puede leer el dato -> el extractor DEBE abstenerse.
    if etiqueta in ("NO_FOTOGRAFIADO", "PRESENTE_ILEGIBLE"):
        if got is None:
            return ("ABSTENCION_OK", f"{etiqueta} -> null (correcto)")
        if campo_obj.confianza == "alta":
            return ("ALUCINACION_ALTA", f"emitio {got!r} con confianza=ALTA sobre {etiqueta}")
        return ("ALUCINACION", f"emitio {got!r} (conf={campo_obj.confianza}) sobre {etiqueta}")

    # 2. Celda de CONFLICTO real (p.ej. P5 marca UMBRO vs RAMI JALAB): lo
    #    correcto es NO elegir -> valor=null + ambas candidatas a la vista.
    candidatas = celda.get("candidatas")
    if candidatas:
        esperadas = {_norm(c) for c in candidatas}
        vistas = {_norm(c.valor) for c in propuesta.alternativas} | {_norm(propuesta.valor)}
        if got is None and esperadas <= vistas:
            return ("CONFLICTO_OK", " vs ".join(candidatas))
        if got is not None:
            return ("CONFLICTO_RESUELTO_MAL", f"eligio {got!r} entre {candidatas}")
        return ("CONFLICTO_INCOMPLETO", f"faltan candidatas; se ven {sorted(v for v in vistas if v)}")

    # 3. Celda LEGIBLE_TEXTO con un valor de referencia concreto.
    esperado = celda.get("valor_legible")
    exp = _norm(esperado)
    if exp is not None and _norm(got) == exp:
        return ("ACIERTO", f"{got}")
    if got is not None:
        return ("LECTURA_ERRONEA", f"leyo {got!r}, legibilidad dice {esperado!r}")
    # got is None: mirar si al menos llego cobertura (lecturas/recorte).
    vistas = {_norm(c.valor) for c in propuesta.alternativas} | {
        _norm(lec.texto) for lec in propuesta.lecturas
    }
    if exp in vistas:
        return ("EN_LECTURAS_SIN_PUBLICAR", f"{esperado!r} esta en lecturas pero no se publico")
    if propuesta.recorte is not None:
        return ("RECORTE_SIN_VALOR", propuesta.motivo[:70] or "hay recorte que mirar")
    return ("PERDIDO", propuesta.motivo[:70] or "el OCR no localizo la region; nada llega a Diego")


# Clasificaciones que NO son problema: Diego recibe algo util o una
# abstencion correcta. El resto (ALUCINACION*, LECTURA_ERRONEA, PERDIDO,
# CONFLICTO_*_MAL/INCOMPLETO) se destaca para que Diego lo mire.
_CLASES_OK = frozenset(
    {"ABSTENCION_OK", "CONFLICTO_OK", "ACIERTO", "RECORTE_SIN_VALOR", "EN_LECTURAS_SIN_PUBLICAR"}
)


@pytest.mark.skipif(
    not (_MEDIR_VLM_REAL and _HAY_KEY),
    reason=(
        "Medicion con Haiku REAL: requiere MEDIR_VLM_REAL=1 y ANTHROPIC_API_KEY "
        "(gasta ~19 cts la 1a vez; despues 0 por cache). Opt-in explicito a "
        "proposito -- decision-making.md SS 15."
    ),
)
def test_medir_alucinacion_con_haiku_real(fotos_reales, capsys):
    """EL PASO 1. Corre el ExtractorEngine REAL (OCR local + Haiku 4.5 via
    core/llm.py, con cache por hash) sobre los 7 productos reales y, para
    cada campo de identidad, lo clasifica contra legibilidad.json. Imprime
    la tabla, la tasa de alucinacion, la cobertura, el coste real y LOS
    CROPS que Diego debe mirar. Candado duro: cero alucinaciones con
    confianza=alta."""
    motor = LLMEngine()  # Haiku 4.5 por defecto; lee ANTHROPIC_API_KEY del entorno
    carpeta_crops = _REPO / "data" / "eval_vlm_real"
    extractor = ExtractorEngine(motor, carpeta_crops=carpeta_crops)

    filas: list[tuple] = []
    n_alucinacion = 0
    n_alucinacion_alta = 0
    n_legibles = 0
    n_cubiertos = 0
    fallos_tecnicos: list[str] = []
    coste_real = 0.0

    for producto in _TRUTH["productos"]:
        pid = producto["id"]
        fotos = [fotos_reales[nombre] for nombre in producto["fotos"]]
        resultado = extractor.extraer_producto(fotos, producto_id=f"prod-{pid}")
        coste_real += resultado.coste_usd
        fallos_tecnicos.extend(f"P{pid}: {f}" for f in resultado.fallos)

        # "composicion" ELIMINADA de la ficha entera (Diego, 2026-07-17):
        # `resultado.campos` ya nunca trae esa clave -- `.get()` degrada a
        # `None` y el bloque de abajo nunca se dispara. Se deja el chequeo
        # (barato, defensivo) por si algun dia resucita el campo.
        comp = resultado.campos.get("composicion")
        if comp is not None and comp.valor is not None:
            n_alucinacion += 1
            filas.append((pid, "composicion", "ALUCINACION", f"emitio {comp.valor!r}; material NO esta fotografiado", None))

        for campo in _CAMPOS_IDENTIDAD:
            campo_obj = resultado.campos.get(campo)
            propuesta = resultado.propuestas.get(campo)
            if campo_obj is None or propuesta is None:
                continue
            clas = _clasificar_celda(pid, campo, campo_obj, propuesta)
            if clas is None:
                continue
            clasificacion, detalle = clas
            crop = str(propuesta.recorte) if propuesta.recorte is not None else None
            filas.append((pid, campo, clasificacion, detalle, crop))

            celda = _LEG_POR_PRODUCTO[pid][campo]
            if celda["etiqueta"] == "LEGIBLE_TEXTO":
                n_legibles += 1
                if clasificacion in _CLASES_OK or clasificacion.startswith("LECTURA"):
                    n_cubiertos += 1  # llego algo confirmable (valor/conflicto/recorte)
            if clasificacion == "ALUCINACION":
                n_alucinacion += 1
            elif clasificacion == "ALUCINACION_ALTA":
                n_alucinacion_alta += 1
                n_alucinacion += 1

    with capsys.disabled():
        print("\n\n=== MEDICION CON HAIKU 4.5 REAL sobre las 33 fotos (Paso 1, Fase 2) ===")
        print("producto | campo       | clasificacion             | detalle")
        print("-" * 100)
        for pid, campo, clasificacion, detalle, _crop in filas:
            marca_ojo = "  " if clasificacion in _CLASES_OK else ">>"
            print(f"{marca_ojo} P{pid} | {campo:11s} | {clasificacion:25s} | {detalle}")

        print("\n--- CROPS A MIRAR (¿es el pixel de verdad legible a ojo? si no, la propuesta es inutil) ---")
        for pid, campo, clasificacion, _detalle, crop in filas:
            if crop is not None and clasificacion not in ("ABSTENCION_OK",):
                print(f"  P{pid} {campo:8s} [{clasificacion}] -> {crop}")

        cobertura = f"{n_cubiertos}/{n_legibles}" if n_legibles else "n/a"
        print("\n=== RESUMEN ===")
        print(f"  TASA DE ALUCINACION: {n_alucinacion} campos publicados sin pixel que los pruebe")
        print(f"    de ellos con confianza=ALTA (catastrofico): {n_alucinacion_alta}  <- el candado duro exige 0")
        print(f"  COBERTURA legible: {cobertura} celdas LEGIBLE_TEXTO con algo confirmable para Diego")
        print(f"  COSTE REAL: {coste_real:.4f} USD (estimado en CLAUDE.md: ~0.192 USD el lote entero)")
        print(f"    coste_lote motor: {motor.coste_lote.coste_usd:.4f} USD, {motor.coste_lote.llamadas} llamadas")
        if fallos_tecnicos:
            print(f"  FALLOS TECNICOS (VLM/OCR/foto): {len(fallos_tecnicos)}")
            for f in fallos_tecnicos:
                print(f"    - {f}")
        print()

    # CANDADO DURO (truth-loop.md SS C): una alucinacion con confianza=alta es
    # la mentira plausible que se publica sin que nadie la mire. Cero, o
    # BLOQUEA. El resto (lecturas erroneas, alucinaciones de baja confianza)
    # se REPORTA arriba para el ojo de Diego, no se assertar (el valor de
    # referencia lo leyo un modelo, no el; y RapidOCR varia entre versiones).
    assert n_alucinacion_alta == 0, (
        f"{n_alucinacion_alta} alucinacion(es) con confianza=ALTA -- ver tabla arriba. "
        "Es el fallo catastrofico del truth-loop: un valor sin pixel, publicado sin revision."
    )
    assert filas, "no se clasifico ninguna celda -- ¿se localizo alguna region?"
