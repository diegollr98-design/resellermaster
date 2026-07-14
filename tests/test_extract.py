"""Tests de core/extract.py -- COSTURA 1 aplicada (`ExtractorEngine`).

TODOS corren SIN `ANTHROPIC_API_KEY` y SIN llamar a la red: el VLM se
sustituye por `_MotorFake` (duck-typing del contrato de `LLMEngine`:
`consultar(...)` + `costes_por_producto()`), nunca se toca `anthropic`. El
OCR SI es real (RapidOCR, local, gratis) donde el test lo necesita -- no
hace falta mockearlo, no cuesta nada ni llama a ningun proveedor.

Estructura:
  1. Helpers (fotos sinteticas minimas, `_MotorFake`, un extractor "ingenuo"
     para demostrar que los tests de trampas son ROJOS sin las reglas
     duras de `core/extract.py`).
  2. Unidad -- las reglas duras EN CODIGO (`_construir_campo_texto` y
     compania), con las 3 trampas reales del golden set + el descarte de
     fondo ajeno + la nota de Diego.
  3. Unidad -- heuristicas gratis (metro, atajo OCR limpio, fusion de
     regiones cercanas).
  4. Integracion -- `ExtractorEngine.extraer_producto` end-to-end contra
     fotos sinteticas + `_MotorFake`, cubriendo los 5 CASOS DE FALLO
     obligatorios (decision-making.md SS16): 0 detecciones OCR, VLM
     legible=false, VLM revienta, sin API key, foto corrupta.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from core.extract import (
    CandidatoConflicto,
    ExtractorEngine,
    ExtractorError,
    LecturaCrop,
    MAX_LLAMADAS_VLM_POR_PRODUCTO,
    RespuestaVLMInvalidaError,
    UMBRAL_SIMILITUD_CORROBORACION,
    VERSION_PROMPT_CROP,
    VERSION_PROMPT_ESTADO,
    VERSION_PROMPT_METRO,
    _campo_composicion,
    _color_dominante_rgb,
    _confianza_corroborada,
    _construir_campo_desperfectos,
    _construir_campo_texto,
    _es_bloque_de_texto_largo,
    _es_repeticion_de_un_campo_ya_resuelto,
    _es_ristra_metro,
    _intentar_atajo_ocr,
    _nombre_color_mas_cercano,
    _parsear_lectura_crop,
    _similitud_normalizada,
    _validar_campo_ean,
    _validar_checksum_ean,
    _UBICACIONES_VALIDAS_MARCA,
    _UBICACIONES_VALIDAS_MODELO,
    fusionar_regiones_cercanas,
    RegionOCR,
)
from core.llm import ApiKeyFaltanteError, LLMLlamadaFallidaError, ResultadoLLM
from core.schema import Campo, Evidencia


# ============================================================================
# 1. HELPERS
# ============================================================================


def _foto_sintetica(ruta: Path, tamano: tuple[int, int] = (600, 800)) -> Path:
    """Una foto REAL minima (no hace falta que contenga nada legible: en
    los tests de integracion el OCR se monkeypatchea, pero `abrir_derecha`
    necesita un fichero de imagen valido para poder recortar/leer la
    foto completa de verdad)."""
    Image.new("RGB", tamano, (170, 170, 170)).save(ruta, format="JPEG", quality=85)
    return ruta


def _foto_corrupta(ruta: Path) -> Path:
    ruta.write_bytes(b"esto no es un jpeg valido, son bytes cualquiera")
    return ruta


def _foto_de_color(ruta: Path, rgb: tuple[int, int, int], tamano: tuple[int, int] = (600, 800)) -> Path:
    """Una foto REAL de un solo color solido -- para probar el color por
    PIXELES (`_color_dominante_rgb`) sin depender de una foto de verdad."""
    Image.new("RGB", tamano, rgb).save(ruta, format="JPEG", quality=95)
    return ruta


def _lectura(
    fichero: str = "IMG_1.jpg",
    bbox: tuple[int, int, int, int] = (0, 0, 10, 10),
    legible: bool = True,
    pertenece_al_producto: bool = True,
    ubicacion: str = "etiqueta_interior",
    contenido_probable: str = "marca",
    texto: str | None = "Reebok",
    texto_ocr_crudo: str | None = None,
) -> LecturaCrop:
    return LecturaCrop(
        fichero=fichero,
        bbox=bbox,
        legible=legible,
        pertenece_al_producto=pertenece_al_producto,
        ubicacion=ubicacion,  # type: ignore[arg-type]
        contenido_probable=contenido_probable,  # type: ignore[arg-type]
        texto=texto,
        texto_ocr_crudo=texto_ocr_crudo,
    )


class _MotorFake:
    """Sustituye a `LLMEngine`: `consultar()` devuelve lo que se haya
    configurado para ese `version_prompt`, o lanza la excepcion
    configurada. Nunca toca la red ni `anthropic`. La respuesta de estado
    por defecto es "legible=false" para que un test que solo le interesa
    el crop de marca/talla no tenga que configurarla siempre. El color NO
    pasa por aqui -- sale de pixeles (`_color_dominante_rgb`), nunca del
    VLM (architecture.md Costura 1)."""

    def __init__(self) -> None:
        self.respuestas: dict[str, dict] = {
            VERSION_PROMPT_ESTADO: {"estimacion_legible": False, "descripcion": None},
        }
        self.excepciones: dict[str, Exception] = {}
        self.llamadas: list[tuple[str, str]] = []

    def consultar(self, imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
        fichero = imagenes[0].fichero
        self.llamadas.append((fichero, version_prompt))
        if version_prompt in self.excepciones:
            raise self.excepciones[version_prompt]
        datos = self.respuestas.get(version_prompt)
        if datos is None:
            raise AssertionError(
                f"_MotorFake: sin respuesta configurada para version_prompt={version_prompt!r} "
                f"(fichero={fichero!r})"
            )
        return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

    def costes_por_producto(self) -> dict:
        return {}


def _extractor_ingenuo_primero_legible(lecturas: list[LecturaCrop], contenido: str) -> str | None:
    """Otro extractor "ingenuo": el PRIMER texto legible que declare ese
    `contenido_probable`, sin mirar si hay mas de uno en conflicto ni si
    `ubicacion` es un estampado."""
    for lectura in lecturas:
        if lectura.legible and lectura.texto and lectura.contenido_probable == contenido:
            return lectura.texto
    return None


# ============================================================================
# 2. LAS REGLAS DURAS EN CODIGO -- las 3 trampas reales del golden set
# ============================================================================


class TestTrampaEstampadoNoEsMarca:
    """legibilidad.json producto 4: el estampado gigante dice 'ORIGINALS'
    (estampado_o_grafico); la marca real es 'JACK & JONES', en la etiqueta
    de cuello (etiqueta_interior).

    C1/INC-010 corrigio la regla original ("el estampado SIEMPRE se ignora
    si hay una etiqueta que diga otra cosa"): eso era exactamente el bug --
    `_candidatos_de_campo` filtraba por ubicacion ANTES de comprobar si
    habia conflicto, asi que un estampado que CONTRADICE la etiqueta se
    borraba en silencio y el pipeline "elegia" la etiqueta con
    `confianza='alta'` sin saber que habia una version distinta. El
    pipeline no puede saber a priori si 'ORIGINALS' es un print decorativo
    o si es la etiqueta de cuello la que miente (ver
    TestTrampaDosMarcasConflicto, la misma ambiguedad con una marca real
    de por medio) -- asi que ahora es un CONFLICTO real, no una eleccion
    silenciosa. Lo que SI se mantiene (invariante A de la regla dura #1):
    un estampado SOLO, sin ninguna etiqueta que lo contradiga, JAMAS
    publica marca por si mismo."""

    def _lecturas(self) -> list[LecturaCrop]:
        return [
            _lectura(
                fichero="IMG_estampado.jpg",
                ubicacion="estampado_o_grafico",
                contenido_probable="marca",
                texto="ORIGINALS",
            ),
            _lectura(
                fichero="IMG_etiqueta.jpg",
                ubicacion="etiqueta_interior",
                contenido_probable="marca",
                texto="JACK & JONES",
            ),
        ]

    def test_estampado_que_discrepa_de_la_etiqueta_es_conflicto(self):
        lecturas = self._lecturas()
        campo, conflictos = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert campo.valor is None
        assert campo.confianza == "baja"
        assert len(conflictos) == 2
        valores = {c.valor for c in conflictos}
        assert valores == {"ORIGINALS", "JACK & JONES"}

    def test_estampado_solo_sin_etiqueta_nunca_publica_marca(self):
        """Invariante A de la regla dura #1, SIN cambios: un estampado sin
        ninguna etiqueta_interior competidora NUNCA se convierte en el
        valor publicado de marca."""
        lecturas = [
            _lectura(
                fichero="IMG_estampado.jpg",
                ubicacion="estampado_o_grafico",
                contenido_probable="marca",
                texto="ORIGINALS",
            )
        ]
        campo, conflictos = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert campo.valor is None
        assert conflictos == ()

    def test_ROJO_contra_el_extractor_ingenuo(self):
        """Demuestra que el test de arriba SI prueba algo: un extractor
        ingenuo que no mire `ubicacion` (solo el primer texto legible
        etiquetado 'marca', que es como el estampado suele aparecer
        primero en el orden de fotos de un producto) cae en la trampa y
        publica el estampado como si fuera la marca."""
        lecturas = self._lecturas()
        marca_ingenua = _extractor_ingenuo_primero_legible(lecturas, "marca")
        assert marca_ingenua == "ORIGINALS"  # la trampa: esto seria FALSO en una ficha real
        assert marca_ingenua != "JACK & JONES"


class TestTrampaDosMarcasConflicto:
    """legibilidad.json producto 5: 'UMBRO' bordado (nitido, en el pecho --
    ubicacion='estampado_o_grafico') Y 'RAMI JALAB' en la etiqueta de
    cuello (tambien nitido, ubicacion='etiqueta_interior') -- prenda
    reetiquetada. Cualquier regla de precedencia acierta en una y miente
    en la otra: la unica salida correcta es null + ambas candidatas para
    Diego.

    C1/INC-010: la version anterior de este test etiquetaba AMBAS lecturas
    como 'etiqueta_interior' (un input imposible: el bordado del pecho NO
    es una etiqueta de cuello) -- con eso, el bug de `_candidatos_de_campo`
    (filtrar por ubicacion ANTES de contar conflictos) nunca se ejercitaba:
    las dos lecturas ya pasaban el filtro y el conflicto se detectaba "por
    accidente". Con la ubicacion REAL del golden set, el bug SI se
    manifestaba: el UMBRO del estampado se borraba antes del conteo y
    'RAMI JALAB' salia solo, con confianza='alta'."""

    def _lecturas(self) -> list[LecturaCrop]:
        return [
            _lectura(
                fichero="IMG_pecho.jpg",
                bbox=(10, 10, 50, 20),
                ubicacion="estampado_o_grafico",
                contenido_probable="marca",
                texto="UMBRO",
            ),
            _lectura(
                fichero="IMG_cuello.jpg",
                bbox=(20, 20, 60, 25),
                ubicacion="etiqueta_interior",
                contenido_probable="marca",
                texto="RAMI JALAB",
            ),
        ]

    def test_el_extractor_real_se_abstiene_y_expone_ambas(self):
        lecturas = self._lecturas()
        campo, conflictos = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert campo.valor is None
        assert campo.confianza == "baja"
        assert len(conflictos) == 2
        valores = {c.valor for c in conflictos}
        assert valores == {"UMBRO", "RAMI JALAB"}
        assert all(isinstance(c, CandidatoConflicto) for c in conflictos)

    def test_ROJO_contra_el_extractor_ingenuo(self):
        """El extractor ingenuo (primer texto legible de ese contenido) SI
        elige uno de los dos -- exactamente la mentira plausible que la
        regla dura evita."""
        lecturas = self._lecturas()
        elegido = _extractor_ingenuo_primero_legible(lecturas, "marca")
        assert elegido in ("UMBRO", "RAMI JALAB")  # eligio UNO, sin saber cual es el real


class TestTrampaTextoOcluidoNuncaPlausible:
    """legibilidad.json producto 2: el frontal dice '.Pocket ____' con la
    segunda palabra TAPADA POR UN CABLE. Un VLM ingenuo dira 'Pocket Life'
    con confianza alta porque es lo plausible -- NO ESTA EN EL PIXEL."""

    def test_legible_false_fuerza_texto_none_pase_lo_que_pase_en_el_json(self):
        """La red de seguridad de `_parsear_lectura_crop`: aunque el JSON
        crudo del modelo traiga un texto (el modelo ignoro la instruccion
        del prompt), `legible=False` lo anula en CODIGO."""
        datos_del_modelo_que_alucina = {
            "legible": False,
            "pertenece_al_producto": True,
            "ubicacion": "codigo_o_modelo_impreso",
            "contenido_probable": "modelo",
            "texto": "Pocket Life",  # el modelo lo puso pese a legible=False
        }
        lectura = _parsear_lectura_crop(datos_del_modelo_que_alucina, "IMG_frontal.jpg", (5, 5, 40, 15))

        assert lectura.legible is False
        assert lectura.texto is None  # nunca "Pocket Life"

    def test_el_extractor_real_produce_null_no_un_valor_plausible(self):
        lecturas = [
            _lectura(
                fichero="IMG_frontal.jpg",
                ubicacion="codigo_o_modelo_impreso",
                contenido_probable="modelo",
                legible=False,
                texto=None,
            )
        ]
        campo, conflictos = _construir_campo_texto(lecturas, "modelo", _UBICACIONES_VALIDAS_MODELO)

        assert campo.valor is None
        assert campo.confianza == "baja"
        # PRESENTE_ILEGIBLE: hubo un intento sobre esta foto -> fuente="foto"
        # (hay evidencia de que se miro, aunque no se pudiera leer), no
        # "inferido" (que se reserva para NO_FOTOGRAFIADO, sin evidencia).
        assert campo.fuente == "foto"
        assert campo.evidencia is not None
        assert campo.evidencia.fichero == "IMG_frontal.jpg"
        assert conflictos == ()

    def test_ROJO_contra_un_parseo_ingenuo_que_no_respeta_legible(self):
        """Un parseo ingenuo que confie en `texto` tal cual venga del JSON
        (sin la red de seguridad de `_parsear_lectura_crop`) SI cuela el
        valor alucinado."""
        datos_del_modelo_que_alucina = {
            "legible": False,
            "texto": "Pocket Life",
        }
        texto_ingenuo = datos_del_modelo_que_alucina["texto"]  # sin mirar "legible"
        assert texto_ingenuo == "Pocket Life"  # la trampa: esto NO deberia publicarse jamas


# ============================================================================
# Reglas duras adicionales: fondo ajeno (#7) y papel manuscrito (#6)
# ============================================================================


class TestFondoAjenoNuncaEsAtributo:
    """legibilidad.json producto 1: un PORTATIL AJENO en el encuadre. El
    OCR lee 'GeForce RTX', 'intel CORE'. `pertenece_al_producto=False`
    debe descartar la lectura ENTERA, pase lo que sea `ubicacion`."""

    def test_se_descarta_aunque_declare_ubicacion_de_etiqueta(self):
        lecturas = [
            _lectura(
                fichero="IMG_fondo.jpg",
                ubicacion="etiqueta_interior",
                contenido_probable="marca",
                texto="GeForce RTX",
                pertenece_al_producto=False,
            )
        ]
        campo, conflictos = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        # Ni siquiera cuenta como "intento": no hay evidencia de que ESTE
        # producto tuviera una etiqueta ahi -- NO_FOTOGRAFIADO, no PRESENTE_ILEGIBLE.
        assert campo.valor is None
        assert campo.fuente == "inferido"
        assert campo.confianza == "baja"
        assert conflictos == ()

    def test_ROJO_contra_un_extractor_que_ignora_pertenece_al_producto(self):
        lecturas = [
            _lectura(
                fichero="IMG_fondo.jpg",
                ubicacion="etiqueta_interior",
                contenido_probable="marca",
                texto="GeForce RTX",
                pertenece_al_producto=False,
            )
        ]
        # Un extractor ingenuo que no comprobara pertenece_al_producto
        # habria devuelto "GeForce RTX" como marca -- una contaminacion
        # con el texto de un objeto de fondo.
        ingenuo = _extractor_ingenuo_primero_legible(lecturas, "marca")
        assert ingenuo == "GeForce RTX"


class TestPapelManuscritoEsNotaDeDiego:
    """legibilidad.json producto 7: un PAPEL MANUSCRITO ('CREMALLERA
    ROTA') junto al producto.

    C7 corrigio `fuente`: el texto SI esta en el pixel (es una
    transcripcion, no algo que Diego tecleara en la app) -- `fuente="foto"`
    con su `evidencia`, NUNCA "diego" (esa es la unica fuente que la UI no
    audita; la nota debe mostrarse como "nota tuya, confirmala", no colarse
    como un hecho ya verificado). Ley de corroboracion: una sola foto de
    la nota -> techo "media"."""

    def test_va_a_desperfectos_con_fuente_foto_y_confianza_media(self):
        lecturas = [
            _lectura(
                fichero="IMG_papel.jpg",
                bbox=(3, 3, 30, 12),
                ubicacion="papel_manuscrito",
                contenido_probable="desperfecto",
                texto="CREMALLERA ROTA",
            )
        ]
        campo = _construir_campo_desperfectos(lecturas)

        assert campo.valor == "CREMALLERA ROTA"
        assert campo.fuente == "foto"  # C7: es una transcripcion, no un dato tecleado
        assert campo.confianza == "media"  # una sola foto de la nota
        assert campo.evidencia is not None
        assert campo.evidencia.fichero == "IMG_papel.jpg"

    def test_la_misma_nota_en_dos_fotos_distintas_sube_a_alta(self):
        lecturas = [
            _lectura(
                fichero="IMG_papel_a.jpg",
                ubicacion="papel_manuscrito",
                contenido_probable="desperfecto",
                texto="CREMALLERA ROTA",
            ),
            _lectura(
                fichero="IMG_papel_b.jpg",
                ubicacion="papel_manuscrito",
                contenido_probable="desperfecto",
                texto="CREMALLERA ROTA",
            ),
        ]
        campo = _construir_campo_desperfectos(lecturas)
        assert campo.confianza == "alta"

    def test_sin_papel_el_campo_es_null(self):
        campo = _construir_campo_desperfectos([_lectura(ubicacion="etiqueta_interior")])
        assert campo.valor is None


class TestComposicionSiempreNull:
    """Regla dura #4: decision explicita de Diego, ninguna foto del lote
    fotografia la etiqueta de composicion -- el campo es SIEMPRE null,
    sin excepcion, y la funcion no recibe argumentos (no puede depender
    de nada que la haga variar)."""

    def test_siempre_none(self):
        campo = _campo_composicion()
        assert campo.valor is None
        assert isinstance(campo, Campo)

    def test_no_acepta_argumentos(self):
        import inspect

        firma = inspect.signature(_campo_composicion)
        assert len(firma.parameters) == 0


# ============================================================================
# 3. HEURISTICAS GRATIS (OCR local, sin VLM)
# ============================================================================


class TestRistraDeMetro:
    def test_ristra_larga_de_digitos_se_detecta(self):
        # Ristra real medida en el golden set (producto 7, foto del metro).
        assert _es_ristra_metro("69899995999291909698925959556575") is True

    def test_ean_no_se_confunde_con_metro(self):
        assert _es_ristra_metro("EANCODE:*8445061029720*") is False

    def test_texto_corto_no_es_metro(self):
        assert _es_ristra_metro("XXL") is False
        assert _es_ristra_metro("Reebok") is False


class TestAtajoOcrLimpio:
    def test_ean_limpio_con_score_alto_se_acepta_directo(self):
        region = RegionOCR(
            fichero="IMG_ean.jpg", bbox=(0, 0, 10, 10), texto_ocr="EANCODE:*8445061029720", score=0.91
        )
        resultado = _intentar_atajo_ocr(region)
        assert resultado is not None
        nombre, campo = resultado
        assert nombre == "ean"
        assert campo.valor == "8445061029720"
        assert campo.fuente == "foto"
        assert campo.confianza == "alta"

    def test_modelo_limpio_con_score_alto_se_acepta_directo(self):
        region = RegionOCR(fichero="IMG_modelo.jpg", bbox=(0, 0, 10, 10), texto_ocr="Model:LLLT-200", score=0.88)
        nombre, campo = _intentar_atajo_ocr(region)
        assert nombre == "modelo"
        assert campo.valor == "LLLT-200"

    def test_score_bajo_no_se_acepta_aunque_el_patron_encaje(self):
        region = RegionOCR(fichero="IMG_x.jpg", bbox=(0, 0, 10, 10), texto_ocr="Model:LLLT-200", score=0.5)
        assert _intentar_atajo_ocr(region) is None

    def test_texto_garbled_de_marca_no_dispara_ningun_atajo(self):
        # 'Raabdk'/'Reabak' (lectura garbled real de Reebok) no matchea
        # ningun patron de EAN/Model -> nunca se cuela como atajo gratis.
        region = RegionOCR(fichero="IMG_marca.jpg", bbox=(0, 0, 10, 10), texto_ocr="Raabdk", score=0.75)
        assert _intentar_atajo_ocr(region) is None


class TestFusionDeRegionesCercanas:
    def test_dos_lineas_de_la_misma_etiqueta_se_fusionan(self):
        # Medido en el golden set: 'ORIGINAL' y 'MARINES' (producto 7).
        regiones = [
            RegionOCR(fichero="f.jpg", bbox=(1235, 1910, 506, 107), texto_ocr="ORIGINAL", score=0.84),
            RegionOCR(fichero="f.jpg", bbox=(1225, 2007, 520, 101), texto_ocr="MARINES", score=0.87),
        ]
        fusionadas = fusionar_regiones_cercanas(regiones)
        assert len(fusionadas) == 1
        assert "ORIGINAL" in fusionadas[0].texto_ocr and "MARINES" in fusionadas[0].texto_ocr

    def test_regiones_lejanas_no_se_fusionan(self):
        regiones = [
            RegionOCR(fichero="f.jpg", bbox=(0, 0, 50, 50), texto_ocr="Reebok", score=0.7),
            RegionOCR(fichero="f.jpg", bbox=(0, 3000, 50, 50), texto_ocr="XXL", score=0.7),
        ]
        fusionadas = fusionar_regiones_cercanas(regiones)
        assert len(fusionadas) == 2


class TestFiltrosDeCoste:
    """Los dos filtros pedidos por el coordinador tras re-derivar el gasto
    por ejecucion: ninguno relaja las 10 reglas duras, solo deciden que NO
    hace falta preguntarle nada al VLM."""

    def test_bloque_largo_se_descarta(self):
        # Medido: el parrafo de especificaciones del producto 1 real.
        texto = (
            "Product name: Masajeador LH Laser Rodilla Model:LLLT-200 Medio: "
            "Semiconductor y Diodo de emision de luz Peso neto del producto: 400g"
        )
        assert _es_bloque_de_texto_largo(texto) is True

    def test_candidato_corto_no_se_descarta(self):
        assert _es_bloque_de_texto_largo("Reebok") is False
        assert _es_bloque_de_texto_largo("ORIGINAL MARINES") is False
        assert _es_bloque_de_texto_largo("EST1590 Oriainadle") is False

    def test_repeticion_de_ean_ya_resuelto_se_descarta(self):
        campos_atajo = {
            "ean": Campo(
                valor="8445061029720", fuente="foto", confianza="alta", evidencia=Evidencia(fichero="f.jpg")
            ),
        }
        # Medido: el producto 1 repite el mismo EAN junto a una referencia interna.
        assert _es_repeticion_de_un_campo_ya_resuelto("THO8LASLHR_UDS *8445061029720", campos_atajo) is True

    def test_texto_sin_relacion_con_el_atajo_no_se_descarta(self):
        campos_atajo = {
            "ean": Campo(
                valor="8445061029720", fuente="foto", confianza="alta", evidencia=Evidencia(fichero="f.jpg")
            )
        }
        assert _es_repeticion_de_un_campo_ya_resuelto("Dlufthous", campos_atajo) is False

    def test_sin_atajos_resueltos_nunca_se_descarta_por_repeticion(self):
        assert _es_repeticion_de_un_campo_ya_resuelto("Reebok", {}) is False


class TestColorPorPixeles:
    """architecture.md Costura 1, tabla de proveedores: "color por
    pixeles" -- nunca VLM. Gratis, determinista, sin posibilidad de
    alucinar (el resultado es una cuenta de bytes)."""

    def test_color_dominante_de_una_foto_solida(self, tmp_path):
        from core.images import abrir_derecha

        foto = _foto_de_color(tmp_path / "IMG_gris.jpg", (130, 130, 130))
        imagen_pil = abrir_derecha(foto)
        rgb = _color_dominante_rgb(imagen_pil)
        assert _nombre_color_mas_cercano(rgb) == "gris"

    def test_paleta_es_cerrada_nunca_inventa_un_nombre(self):
        # Un RGB cualquiera (no en la paleta) siempre resuelve a UNO de los
        # nombres de la paleta -- nunca a una descripcion libre inventada.
        from core.extract import _PALETA_COLORES_REFERENCIA

        nombres_validos = {nombre for nombre, _ in _PALETA_COLORES_REFERENCIA}
        assert _nombre_color_mas_cercano((17, 240, 3)) in nombres_validos  # verde chillon, fuera de la paleta

    def test_recorte_central_ignora_el_borde(self, tmp_path):
        """El color dominante debe salir del CENTRO (la prenda), no del
        borde (fondo/pared/percha) -- pinta un marco gris y un centro
        rosa, y comprueba que domina el rosa."""
        from core.images import abrir_derecha

        ancho, alto = 400, 400
        imagen = Image.new("RGB", (ancho, alto), (130, 130, 130))  # borde/fondo gris
        centro = Image.new("RGB", (200, 200), (232, 160, 180))  # la prenda, rosa
        imagen.paste(centro, (100, 100))
        ruta = tmp_path / "IMG_centro.jpg"
        imagen.save(ruta, format="JPEG", quality=95)

        rgb = _color_dominante_rgb(abrir_derecha(ruta))
        assert _nombre_color_mas_cercano(rgb) == "rosa"


class TestParseoDefensivoDeLaRespuestaDelVLM:
    def test_falta_una_clave_obligatoria_lanza(self):
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop({"legible": True}, "IMG_1.jpg", (0, 0, 1, 1))

    def test_ubicacion_desconocida_lanza(self):
        datos = {
            "legible": True,
            "pertenece_al_producto": True,
            "ubicacion": "un_valor_que_no_existe",
            "contenido_probable": "marca",
            "texto": "Nike",
        }
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))

    def test_texto_vacio_se_normaliza_a_none(self):
        datos = {
            "legible": True,
            "pertenece_al_producto": True,
            "ubicacion": "etiqueta_interior",
            "contenido_probable": "marca",
            "texto": "   ",
        }
        lectura = _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))
        assert lectura.texto is None

    def test_texto_ocr_crudo_se_traslada_al_lectura_crop(self):
        """El OCR crudo de la region viaja al `LecturaCrop` -- es la mitad
        gratis de la ley de corroboracion (INC-010)."""
        datos = {
            "legible": True,
            "pertenece_al_producto": True,
            "ubicacion": "etiqueta_interior",
            "contenido_probable": "marca",
            "texto": "Reebok",
        }
        lectura = _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1), texto_ocr_crudo="Raabdk")
        assert lectura.texto_ocr_crudo == "Raabdk"


# ============================================================================
# LA LEY DE CORROBORACION (INC-010): ninguna afirmacion de texto sale con
# confianza="alta" sin corroboracion INDEPENDIENTE. Una sola senal, por
# nitida que parezca, tiene techo "media".
# ============================================================================


class TestLeyDeCorroboracion:
    def test_similitud_calibrada_con_los_pares_reales_del_golden_set(self):
        # Pares que DEBEN corroborar (garbled real del OCR vs lectura VLM correcta).
        assert _similitud_normalizada("Reebok", "Raabdk") >= UMBRAL_SIMILITUD_CORROBORACION
        assert _similitud_normalizada("Reebok", "Reabak") >= UMBRAL_SIMILITUD_CORROBORACION
        assert _similitud_normalizada("RAMI JALAB", "RAMI SALAB") >= UMBRAL_SIMILITUD_CORROBORACION
        assert _similitud_normalizada("ORIGINAL MARINES", "ORIGINAL MARINES") >= UMBRAL_SIMILITUD_CORROBORACION
        # Pares que NO deben corroborar (marcas realmente distintas, o
        # el VLM afirmando algo que el OCR ni sugiere).
        assert _similitud_normalizada("JACK & JONES", "ESTI550") < UMBRAL_SIMILITUD_CORROBORACION
        assert _similitud_normalizada("JACK & JONES", "Orioinae") < UMBRAL_SIMILITUD_CORROBORACION
        assert _similitud_normalizada("UMBRO", "RAMI JALAB") < UMBRAL_SIMILITUD_CORROBORACION

    def test_una_sola_lectura_sin_corroboracion_tiene_techo_media(self):
        lecturas = [_lectura(texto="Reebok", texto_ocr_crudo=None)]
        campo, conflictos = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert campo.valor == "Reebok"
        assert campo.confianza == "media"
        assert conflictos == ()

    def test_corroborado_por_ocr_crudo_de_la_misma_region_sube_a_alta(self):
        lecturas = [_lectura(texto="Reebok", texto_ocr_crudo="Raabdk")]
        campo, _ = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert campo.confianza == "alta"

    def test_ocr_crudo_que_no_se_parece_no_corrobora(self):
        # El VLM afirma "Nike" pero el OCR de esa misma region no sugeria
        # nada parecido -- el modelo esta inventando, no confirmando.
        lecturas = [_lectura(texto="Nike", texto_ocr_crudo="Raabdk")]
        campo, _ = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert campo.confianza == "media"

    def test_corroborado_por_dos_fotos_distintas_sube_a_alta(self):
        lecturas = [
            _lectura(fichero="IMG_a.jpg", texto="Reebok"),
            _lectura(fichero="IMG_b.jpg", texto="Reebok"),
        ]
        campo, _ = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert campo.confianza == "alta"

    def test_confianza_corroborada_directa(self):
        rep = _lectura(texto="Reebok", texto_ocr_crudo="Raabdk")
        assert _confianza_corroborada([rep], rep) == "alta"
        rep_sola = _lectura(texto="Reebok", texto_ocr_crudo=None)
        assert _confianza_corroborada([rep_sola], rep_sola) == "media"


# ============================================================================
# C5 -- EAN sin checksum GS1 valido no es un EAN
# ============================================================================


class TestChecksumEan:
    def test_ean_con_checksum_valido_pasa(self):
        assert _validar_checksum_ean("8445061029720") is True

    def test_ean_con_un_digito_mal_leido_no_valida(self):
        assert _validar_checksum_ean("8445061029721") is False

    def test_longitud_invalida_no_valida(self):
        assert _validar_checksum_ean("12345") is False

    def test_no_digitos_no_valida(self):
        assert _validar_checksum_ean("844506102972X") is False

    def test_atajo_no_acepta_ean_con_checksum_invalido(self):
        region = RegionOCR(
            fichero="IMG_ean.jpg", bbox=(0, 0, 10, 10), texto_ocr="EANCODE:*8445061029721", score=0.91
        )
        assert _intentar_atajo_ocr(region) is None

    def test_atajo_si_acepta_ean_con_checksum_valido(self):
        region = RegionOCR(
            fichero="IMG_ean.jpg", bbox=(0, 0, 10, 10), texto_ocr="EANCODE:*8445061029720", score=0.91
        )
        nombre, campo = _intentar_atajo_ocr(region)
        assert nombre == "ean"
        assert campo.valor == "8445061029720"
        assert campo.confianza == "alta"

    def test_campo_ean_con_checksum_invalido_se_anula(self):
        campo = Campo(
            valor="8445061029721", fuente="foto", confianza="alta", evidencia=Evidencia(fichero="f.jpg")
        )
        anulado = _validar_campo_ean(campo)
        assert anulado.valor is None
        assert anulado.confianza == "baja"
        assert anulado.evidencia is not None  # se preserva el rastro de que hubo un intento

    def test_campo_ean_sin_valor_no_se_toca(self):
        campo = Campo(valor=None, fuente="inferido", confianza="baja")
        assert _validar_campo_ean(campo) == campo

    def test_campo_ean_con_checksum_valido_se_preserva(self):
        campo = Campo(
            valor="8445061029720", fuente="foto", confianza="alta", evidencia=Evidencia(fichero="f.jpg")
        )
        assert _validar_campo_ean(campo) == campo


# ============================================================================
# 4. INTEGRACION -- ExtractorEngine.extraer_producto, LOS 5 CASOS DE FALLO
# ============================================================================


class TestCasosDeFallo:
    """decision-making.md SS16: para cada uno, el resultado correcto es
    `valor=None` + `confianza="baja"`, NUNCA un valor plausible -- y se
    EJECUTA cada caso, no se infiere del codigo."""

    def test_1_ocr_devuelve_cero_detecciones(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_1.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["marca"].confianza == "baja"
        assert resultado.campos["talla"].valor is None
        assert resultado.campos["ean"].valor is None
        assert resultado.campos["modelo"].valor is None
        # cero regiones -> ninguna llamada de tipo "crop" (el color ya no
        # llama al VLM en absoluto; solo queda la llamada de estado).
        assert all(vp != VERSION_PROMPT_CROP for _, vp in motor.llamadas)

    def test_2_vlm_devuelve_legible_false(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_2.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = {
            "legible": False,
            "pertenece_al_producto": True,
            "ubicacion": "etiqueta_interior",
            "contenido_probable": "marca",
            "texto": None,
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["marca"].confianza == "baja"
        assert resultado.campos["marca"].fuente == "foto"  # hubo un intento real
        assert resultado.fallos == ()

    def test_3_el_vlm_revienta(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_3.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.excepciones[VERSION_PROMPT_CROP] = LLMLlamadaFallidaError("limite de tasa excedido")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["marca"].confianza == "baja"
        # Un fallo TECNICO se ve en `fallos` -- no se disfraza de "no legible".
        assert any("vlm_crop" in f for f in resultado.fallos)

    def test_4_sin_api_key(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_4.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.excepciones[VERSION_PROMPT_CROP] = ApiKeyFaltanteError("Falta ANTHROPIC_API_KEY")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["marca"].confianza == "baja"
        assert any("vlm_crop" in f for f in resultado.fallos)

    def test_5_la_foto_esta_corrupta(self, tmp_path):
        foto = _foto_corrupta(tmp_path / "IMG_5.jpg")

        motor = _MotorFake()
        # La foto no se puede ni abrir -- color/estado tambien fallaran al
        # intentar abrirla, asi que el resultado entero debe ser honesto
        # (todo None) sin que NADA reviente hacia el llamador.
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["color"].valor is None
        assert resultado.campos["estado"].valor is None
        assert any("ocr:" in f for f in resultado.fallos)


class TestExtraerProductoFelizYAtajos:
    def test_marca_por_vlm_desde_una_etiqueta_limpia(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_ok.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = {
            "legible": True,
            "pertenece_al_producto": True,
            "ubicacion": "etiqueta_interior",
            "contenido_probable": "marca",
            "texto": "Reebok",
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.campos["marca"].fuente == "foto"
        assert resultado.campos["marca"].confianza == "alta"
        assert resultado.campos["composicion"].valor is None  # regla dura #4, siempre

    def test_ean_por_atajo_ocr_no_gasta_ninguna_llamada_vlm_de_crop(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_ean.jpg")
        region = RegionOCR(
            fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="EANCODE:*8445061029720", score=0.91
        )
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["ean"].valor == "8445061029720"
        assert resultado.campos["ean"].confianza == "alta"
        # el atajo es gratis: no debe haber ninguna llamada VERSION_PROMPT_CROP
        assert all(vp != VERSION_PROMPT_CROP for _, vp in motor.llamadas)

    def test_construir_solicitudes_no_llama_a_nada(self, tmp_path, monkeypatch):
        """`construir_solicitudes` es para ESTIMAR coste antes de gastar
        (decision-making.md SS15): no debe tocar `motor_llm.consultar` en
        absoluto."""
        foto = _foto_sintetica(tmp_path / "IMG_est.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        solicitudes = extractor.construir_solicitudes([foto])

        assert motor.llamadas == []  # nada se llamo de verdad
        assert len(solicitudes) >= 1
        # (imagenes, prompt, version_prompt) -- C6: el texto del prompt
        # viaja explicito porque el hash de cache ahora lo incluye.
        version_prompts = {vp for _, _, vp in solicitudes}
        assert VERSION_PROMPT_CROP in version_prompts
        assert VERSION_PROMPT_ESTADO in version_prompts
        # El color NUNCA genera una solicitud VLM (sale de pixeles).
        assert "extract-color-v1" not in version_prompts

    def test_metro_sin_las_dos_condiciones_no_produce_medida(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_metro.jpg")
        region_metro = RegionOCR(
            fichero=foto.name,
            bbox=(10, 10, 5, 500),
            texto_ocr="69899995999291909698925959556575",
            score=0.66,
        )
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region_metro])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_METRO] = {
            "cero_visible": False,
            "borde_prenda_visible": True,
            "medida_cm": None,
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["medidas"].valor is None
        assert resultado.campos["medidas"].fuente == "foto"  # hubo evidencia (la foto del metro)
        assert resultado.campos["medidas"].confianza == "baja"
        # la ristra de metro NUNCA se manda como crop de lectura de texto.
        assert all(vp != VERSION_PROMPT_CROP for _, vp in motor.llamadas)

    def test_metro_con_las_dos_condiciones_si_produce_medida(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_metro_ok.jpg")
        region_metro = RegionOCR(
            fichero=foto.name,
            bbox=(10, 10, 5, 500),
            texto_ocr="69899995999291909698925959556575",
            score=0.66,
        )
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region_metro])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_METRO] = {
            "cero_visible": True,
            "borde_prenda_visible": True,
            "medida_cm": 62.0,
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["medidas"].valor == 62.0
        assert resultado.campos["medidas"].fuente == "foto"

    def test_color_divergente_es_none_nunca_la_primera_lectura(self, tmp_path, monkeypatch):
        """C3(c): el color sale de PIXELES (no del VLM): 3 fotos, 2 tonos
        distintos (rosa vs rojo vino, el caso real del producto 6) ->
        `valor=None` -- ninguna de las dos lecturas es mas valida que la
        otra, asi que ya NO se publica "la primera que salga primero"."""
        rosa = (232, 160, 180)
        rojo_vino = (110, 20, 40)
        fotos = [
            _foto_de_color(tmp_path / "IMG_c1.jpg", rosa),
            _foto_de_color(tmp_path / "IMG_c2.jpg", rojo_vino),
            _foto_de_color(tmp_path / "IMG_c3.jpg", rosa),
        ]
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto(fotos)

        assert resultado.campos["color"].valor is None
        assert resultado.campos["color"].confianza == "baja"
        assert resultado.campos["color"].fuente == "inferido"
        # el color nunca gasta una llamada VLM -- lo unico que puede haber
        # llamado al motor es la evaluacion de estado (VERSION_PROMPT_ESTADO).
        assert all(vp == VERSION_PROMPT_ESTADO for _, vp in motor.llamadas)

    def test_color_convergente_sigue_con_techo_media_nunca_alta(self, tmp_path, monkeypatch):
        """C3(a): ni siquiera con las 3 fotos de acuerdo el color puede
        salir 'alta' -- el acuerdo puede ser el MISMO sesgo del sensor
        repetido (el caso real que motivo esta regla: una sudadera negra
        sale gris oscuro en las 3 fotos por la autoexposicion). Y
        `fuente='inferido'`: una cuenta de pixeles no es una lectura
        directa, es una inferencia sobre lo que el sensor capturo."""
        gris = (130, 130, 130)
        fotos = [
            _foto_de_color(tmp_path / "IMG_g1.jpg", gris),
            _foto_de_color(tmp_path / "IMG_g2.jpg", gris),
            _foto_de_color(tmp_path / "IMG_g3.jpg", gris),
        ]
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto(fotos)

        assert resultado.campos["color"].valor == "gris"
        assert resultado.campos["color"].confianza == "media"
        assert resultado.campos["color"].fuente == "inferido"

    def test_estampado_se_excluye_del_muestreo_de_color(self, tmp_path):
        """C3(d): si el VLM ya localizo un `estampado_o_grafico` en la
        MISMA foto que se muestrea para color, ese bbox se excluye del
        histograma -- el recorte central puede caer justo encima de un
        estampado grande (producto 4 real: un leon gris) y contaminar el
        color de la prenda."""
        ancho, alto = 400, 400
        imagen = Image.new("RGB", (ancho, alto), (130, 130, 130))  # borde gris
        centro = Image.new("RGB", (200, 200), (60, 140, 60))  # el "estampado", verde, domina el centro
        imagen.paste(centro, (100, 100))
        rosa = Image.new("RGB", (200, 40), (232, 160, 180))  # una franja rosa de la prenda real
        imagen.paste(rosa, (100, 100))
        ruta = tmp_path / "IMG_estampado_color.jpg"
        imagen.save(ruta, format="JPEG", quality=95)

        from core.images import abrir_derecha

        imagen_pil = abrir_derecha(ruta)
        bbox_estampado = (100, 140, 200, 160)  # coords ORIGINALES del bloque verde

        rgb_sin_excluir = _color_dominante_rgb(imagen_pil)
        assert _nombre_color_mas_cercano(rgb_sin_excluir) == "verde"

        rgb_excluido = _color_dominante_rgb(imagen_pil, bboxes_excluir=[bbox_estampado])
        assert _nombre_color_mas_cercano(rgb_excluido) == "rosa"

    def test_estado_siempre_fuente_inferido(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_estado.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_ESTADO] = {
            "estimacion_legible": True,
            "descripcion": "buen estado general, sin danos visibles",
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        # Regla dura #8: SIEMPRE "inferido", pase lo que declare el VLM.
        assert resultado.campos["estado"].fuente == "inferido"
        assert resultado.campos["estado"].valor == "buen estado general, sin danos visibles"

    def test_extraer_producto_sin_fotos_lanza(self):
        extractor = ExtractorEngine(_MotorFake())
        with pytest.raises(ValueError):
            extractor.extraer_producto([])


# ============================================================================
# C4 -- nombres de fichero duplicados fallan RUIDOSO, nunca aliasing silencioso
# ============================================================================


class TestNombresDuplicadosFallaRuidoso:
    """Dos fotos de RUTAS distintas (dos tarjetas SD, dos moviles) con el
    MISMO nombre base -- un `dict` clavado por nombre perderia una en
    silencio y el recorte saldria de la foto EQUIVOCADA (C4)."""

    def _dos_fotos_mismo_nombre(self, tmp_path) -> list[Path]:
        carpeta_a = tmp_path / "sd1"
        carpeta_b = tmp_path / "sd2"
        carpeta_a.mkdir()
        carpeta_b.mkdir()
        foto_a = _foto_sintetica(carpeta_a / "IMG_0001.jpg")
        foto_b = _foto_sintetica(carpeta_b / "IMG_0001.jpg")
        return [foto_a, foto_b]

    def test_extraer_producto_falla_si_hay_nombres_base_duplicados(self, tmp_path):
        fotos = self._dos_fotos_mismo_nombre(tmp_path)
        extractor = ExtractorEngine(_MotorFake())
        with pytest.raises(ExtractorError):
            extractor.extraer_producto(fotos)

    def test_construir_solicitudes_tambien_falla(self, tmp_path):
        fotos = self._dos_fotos_mismo_nombre(tmp_path)
        extractor = ExtractorEngine(_MotorFake())
        with pytest.raises(ExtractorError):
            extractor.construir_solicitudes(fotos)

    def test_nombres_unicos_no_falla(self, tmp_path):
        foto_a = _foto_sintetica(tmp_path / "IMG_0001.jpg")
        foto_b = _foto_sintetica(tmp_path / "IMG_0002.jpg")
        extractor = ExtractorEngine(_MotorFake())
        # No debe lanzar -- solo se comprueba que no reviente por esto.
        extractor.extraer_producto([foto_a, foto_b])


# ============================================================================
# C9 -- backstop de coste: MAX_LLAMADAS_VLM_POR_PRODUCTO
# ============================================================================


class TestLimiteLlamadasVlm:
    def test_se_corta_al_llegar_al_limite_y_se_anota_en_fallos(self, tmp_path, monkeypatch):
        n_fotos = MAX_LLAMADAS_VLM_POR_PRODUCTO + 5
        fotos = [_foto_sintetica(tmp_path / f"IMG_{i}.jpg") for i in range(n_fotos)]
        regiones = {
            fotos[i].name: [
                RegionOCR(fichero=fotos[i].name, bbox=(5, 5, 20, 10), texto_ocr=f"txt{i}xyz", score=0.5)
            ]
            for i in range(n_fotos)
        }
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = {
            "legible": False,
            "pertenece_al_producto": True,
            "ubicacion": "otro",
            "contenido_probable": "otro",
            "texto": None,
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto(fotos)

        llamadas_crop = [vp for _, vp in motor.llamadas if vp == VERSION_PROMPT_CROP]
        assert len(llamadas_crop) == MAX_LLAMADAS_VLM_POR_PRODUCTO
        assert any("limite_llamadas_vlm_alcanzado" in f for f in resultado.fallos)


# ============================================================================
# C2 -- LA FICHA FRANKENSTEIN: aviso de coherencia con dientes
# ============================================================================


class TestAvisoDeCoherencia:
    """INC-011: si los campos de identidad (marca/talla/modelo/ean)
    proceden de fotos DISJUNTAS -- ninguna foto liga dos campos entre si
    -- es la firma de una fusion de dos productos que Diego no caza al
    curar. El aviso tiene DIENTES: degrada la confianza, no es un pie de
    foto."""

    def test_marca_y_talla_de_fotos_disjuntas_dispara_el_aviso_y_degrada(self, tmp_path, monkeypatch):
        foto_marca = _foto_sintetica(tmp_path / "IMG_marca.jpg")
        foto_talla = _foto_sintetica(tmp_path / "IMG_talla.jpg")
        region_marca = RegionOCR(fichero=foto_marca.name, bbox=(1, 1, 5, 5), texto_ocr="Raabdk", score=0.7)
        region_talla = RegionOCR(fichero=foto_talla.name, bbox=(1, 1, 5, 5), texto_ocr="XXL", score=0.7)
        regiones = {foto_marca.name: [region_marca], foto_talla.name: [region_talla]}
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if version_prompt == VERSION_PROMPT_ESTADO:
                return ResultadoLLM(
                    datos={"estimacion_legible": False, "descripcion": None},
                    fuente="api",
                    coste_usd=0.0,
                    tokens_entrada=10,
                    tokens_salida=5,
                )
            if fichero == foto_marca.name:
                datos = {
                    "legible": True,
                    "pertenece_al_producto": True,
                    "ubicacion": "etiqueta_interior",
                    "contenido_probable": "marca",
                    "texto": "Reebok",
                }
            else:
                datos = {
                    "legible": True,
                    "pertenece_al_producto": True,
                    "ubicacion": "etiqueta_interior",
                    "contenido_probable": "talla",
                    "texto": "XXL",
                }
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto_marca, foto_talla])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.campos["talla"].valor == "XXL"
        assert resultado.aviso_coherencia is not None
        assert "DISJUNTAS" in resultado.aviso_coherencia
        # Ambos vendrian corroborados por OCR<->VLM ("Raabdk"~"Reebok" no,
        # pero "XXL" es identico a si mismo si hubiera repeticion) -- el
        # punto es que si CUALQUIERA hubiera salido "alta", el aviso lo baja
        # a "media": ningun campo de identidad puede quedar en "alta" aqui.
        assert resultado.campos["marca"].confianza != "alta"
        assert resultado.campos["talla"].confianza != "alta"

    def test_marca_y_talla_de_la_misma_foto_no_dispara_el_aviso(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_ambas.jpg", tamano=(600, 1000))
        region_marca = RegionOCR(fichero=foto.name, bbox=(1, 1, 5, 5), texto_ocr="Raabdk", score=0.7)
        # Lejos verticalmente (>150px) para que NO se fusione con region_marca
        # (fusionar_regiones_cercanas uniria las dos en un solo recorte).
        region_talla = RegionOCR(fichero=foto.name, bbox=(1, 500, 5, 5), texto_ocr="XXL", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region_marca, region_talla])

        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if version_prompt == VERSION_PROMPT_ESTADO:
                return ResultadoLLM(
                    datos={"estimacion_legible": False, "descripcion": None},
                    fuente="api",
                    coste_usd=0.0,
                    tokens_entrada=10,
                    tokens_salida=5,
                )
            # Ambas lecturas de crop en esta foto (misma imagen, distinto bbox);
            # devolvemos segun el contenido esperado alternando por orden de llamada.
            indice_crop = sum(1 for f, vp in motor.llamadas if vp == VERSION_PROMPT_CROP)
            if indice_crop <= 1:
                datos = {
                    "legible": True,
                    "pertenece_al_producto": True,
                    "ubicacion": "etiqueta_interior",
                    "contenido_probable": "marca",
                    "texto": "Reebok",
                }
            else:
                datos = {
                    "legible": True,
                    "pertenece_al_producto": True,
                    "ubicacion": "etiqueta_interior",
                    "contenido_probable": "talla",
                    "texto": "XXL",
                }
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.campos["talla"].valor == "XXL"
        assert resultado.aviso_coherencia is None

    def test_un_solo_campo_con_valor_no_dispara_el_aviso(self, tmp_path, monkeypatch):
        """0 o 1 campo de identidad con valor -- no hay nada que pueda "no
        ligar", el aviso no aplica."""
        foto = _foto_sintetica(tmp_path / "IMG_solo_marca.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(1, 1, 5, 5), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = {
            "legible": True,
            "pertenece_al_producto": True,
            "ubicacion": "etiqueta_interior",
            "contenido_probable": "marca",
            "texto": "Reebok",
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.aviso_coherencia is None
