"""Tests de core/extract.py -- COSTURA 1 aplicada (`ExtractorEngine`), v2.

REDISENO (2026-07-15, `[INC-012]`): el extractor NO AFIRMA. PROPONE un
valor y ENSENA EL PIXEL del que lo saco -- Diego confirma de un vistazo.
`confianza="alta"` PRACTICAMENTE DESAPARECE de este modulo: solo es
alcanzable por un EAN cuyo checksum GS1 valida. Ningun texto leido por un
VLM/OCR, por nitido o "corroborado" que parezca, puede llegar a "alta" --
la maquinaria de corroboracion de `[INC-010]` (que SI lo permitia, y fue
la causa de `[INC-012]`) fue ELIMINADA de `core/extract.py`. Estos tests
se reescriben contra ESE contrato -- ver el docstring de `core/extract.py`
para el detalle completo del rediseno.

TODOS corren SIN `ANTHROPIC_API_KEY` y SIN llamar a la red: el VLM se
sustituye por `_MotorFake` (duck-typing del contrato de `LLMEngine`:
`consultar(...)` + `costes_por_producto()`), nunca se toca `anthropic`. El
OCR SI es real (RapidOCR, local, gratis) donde el test lo necesita -- no
hace falta mockearlo, no cuesta nada ni llama a ningun proveedor.

Estructura:
  1. Helpers (fotos sinteticas minimas, constructores de respuesta VLM
     v2 con `hallazgos` como LISTA, `_MotorFake`, un extractor "ingenuo"
     para demostrar que los tests de trampas son ROJOS sin las reglas
     duras de `core/extract.py`).
  2. Unidad -- las reglas duras EN CODIGO (`_agregar_campo_texto` y
     compania), con las 3 trampas reales del golden set + el descarte de
     fondo ajeno + la nota de Diego.
  3. Unidad -- heuristicas gratis (metro, atajo OCR limpio, fusion de
     regiones cercanas).
  4. LEY NUEVA DE CONFIANZA: `confianza="alta"` es INALCANZABLE salvo por
     un EAN con checksum valido -- se demuestra INTENTANDOLO con la senal
     mas favorable posible, no solo leyendo el docstring.
  5. Integracion -- `ExtractorEngine.extraer_producto` end-to-end contra
     fotos sinteticas + `_MotorFake`, cubriendo los 5 CASOS DE FALLO
     obligatorios (decision-making.md SS16) y la entrega de `Propuesta`
     (recorte + alternativas con SU PROPIO recorte, nunca solo un string).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from core.extract import (
    ESQUEMA_SINTESIS_FICHA,
    MARCADOR_DESPERFECTOS,
    MAX_LLAMADAS_VLM_POR_PRODUCTO,
    UMBRAL_SIMILITUD_DUDOSO,
    VERSION_PROMPT_CROP,
    VERSION_PROMPT_METRO,
    VERSION_PROMPT_REDACCION,
    VERSION_PROMPT_SINTESIS,
    Candidato,
    ExtractorEngine,
    ExtractorError,
    Lectura,
    LecturaCrop,
    RegionOCR,
    RespuestaVLMInvalidaError,
    _agregar_campo_desde_marcador,
    _agregar_campo_desperfectos,
    _agregar_campo_texto,
    _asegurar_titulo_lidera_con_tipo,
    _color_dominante_rgb,
    _construir_campo_categoria_desde_sintesis,
    _construir_campo_estado_desde_sintesis,
    _construir_campo_genero_desde_sintesis,
    _construir_campo_tipo_desde_sintesis,
    _construir_prompt_redaccion,
    _es_bloque_de_texto_largo,
    _es_repeticion_de_un_campo_ya_resuelto,
    _es_ristra_metro,
    _es_testigo_valido,
    _extraer_valor_tras_marcador,
    _formatear_campos_para_redaccion,
    _intentar_atajo_ocr,
    _nombre_color_mas_cercano,
    _parsear_lectura_crop,
    _similitud_normalizada,
    _UBICACIONES_VALIDAS_MARCA,
    _UBICACIONES_VALIDAS_MODELO,
    _UBICACIONES_VALIDAS_TALLA,
    _validar_campo_ean,
    _validar_checksum_ean,
    construir_solicitud_redaccion,
    deserializar_extraccion,
    fusionar_regiones_cercanas,
    redactar_desde_campos_confirmados,
    serializar_extraccion,
)
from core.llm import ApiKeyFaltanteError, LLMEngine, LLMLlamadaFallidaError, ResultadoLLM
from core.schema import ESTADO_UI_A_CANONICO, Campo, Evidencia, parsear_estado


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
    origen: str = "vlm",
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
        origen=origen,  # type: ignore[arg-type]
    )


def _respuesta_crop(
    *,
    pertenece_al_producto: bool = True,
    ubicacion: str = "etiqueta_interior",
    hallazgos: list[dict] | None = None,
) -> dict:
    """Construye el dict crudo que el VLM devolveria para UN recorte,
    contrato v2: `hallazgos` es SIEMPRE una lista (regla dura #4 -- un
    mismo recorte puede traer varios datos)."""
    if hallazgos is None:
        hallazgos = [{"contenido_probable": "otro", "legible": False, "texto": None}]
    return {
        "pertenece_al_producto": pertenece_al_producto,
        "ubicacion": ubicacion,
        "hallazgos": hallazgos,
    }


def _respuesta_crop_simple(
    contenido_probable: str,
    legible: bool,
    texto: str | None,
    ubicacion: str = "etiqueta_interior",
    pertenece_al_producto: bool = True,
) -> dict:
    """Atajo para el caso comun: un solo hallazgo en el recorte."""
    return _respuesta_crop(
        pertenece_al_producto=pertenece_al_producto,
        ubicacion=ubicacion,
        hallazgos=[{"contenido_probable": contenido_probable, "legible": legible, "texto": texto}],
    )


def _respuesta_sintesis(
    *,
    marca: dict | None = None,
    modelo: dict | None = None,
    talla: dict | None = None,
    color: dict | None = None,
    estado: dict | None = None,
    material: dict | None = None,
    medidas: dict | None = None,
    categoria: str = "otros",
    genero: str | None = None,
    tipo: dict | None = None,
    titulo: str = "",
    descripcion: str = "",
) -> dict:
    """Construye una respuesta de sintesis completa (contrato de
    `ESQUEMA_SINTESIS_FICHA`). Cada campo de TEXTO LIBRE, si no se pasa,
    usa el default "SIN OPINION" (valor=None, visible_en_foto=False,
    de_texto_detectado=None, confianza="baja") -- asi `_MotorFake` puede
    usarlo como respuesta por defecto (la sintesis no opina -> gap-filler
    no pisa nada) y los tests de este archivo que NO les interesa la
    sintesis siguen viendo el comportamiento de la agregacion vieja intacto.

    `categoria` default "otros" (valido, dentro del enum) -- para los
    tests que no les interesa este campo, la sintesis "opina" pero con un
    valor SIEMPRE aceptable, y no revientan la clave requerida por
    `_parsear_respuesta_sintesis`.

    `estado` tiene su PROPIA estructura (valor ENUM + motivo + confianza,
    NO visible_en_foto/de_texto_detectado -- ver `_esquema_estado_
    sintesis` en `core/extract.py`); el default es "Bueno" (valido, dentro
    del enum), mismo criterio que `categoria`: siempre aceptable, para que
    los tests que no les interesa `estado` no revienten la clave
    requerida ni disparen `sintesis_estado_fuera_de_enum` sin querer.

    `tipo` (2026-07-21) tiene su PROPIA estructura, MAS simple que
    `estado` (solo valor + confianza, sin motivo, sin enum -- texto libre
    -- ver `_esquema_tipo_sintesis` en `core/extract.py`); el default es
    "SIN OPINION" (`valor=None`), igual que marca/modelo/talla/color: los
    tests que no les interesa `tipo` no ven la clave "tipo" en
    `resultado.campos` (gap-filler-like: ausente, no comodin).

    `genero` (2026-07-21, fast-follow de `tipo`) es un valor CRUDO (como
    `categoria`, no un objeto anidado) -- ESPEJO de `categoria` pero
    NULLABLE: el default es `None` (valido dentro del enum: "no aplica"),
    para que los tests que no les interesa este campo no vean la clave
    "genero" en `resultado.campos` ni disparen `sintesis_genero_fuera_de_
    enum` sin querer."""

    def _campo(override: dict | None) -> dict:
        base = {"valor": None, "visible_en_foto": False, "de_texto_detectado": None, "confianza": "baja"}
        if override:
            base.update(override)
        return base

    def _campo_estado(override: dict | None) -> dict:
        base = {"valor": "Bueno", "motivo": "", "confianza": "baja"}
        if override:
            base.update(override)
        return base

    def _campo_tipo(override: dict | None) -> dict:
        base = {"valor": None, "confianza": "baja"}
        if override:
            base.update(override)
        return base

    return {
        "marca": _campo(marca),
        "modelo": _campo(modelo),
        "talla": _campo(talla),
        "color": _campo(color),
        "estado": _campo_estado(estado),
        "material": _campo(material),
        "medidas": _campo(medidas),
        "categoria": categoria,
        "genero": genero,
        "tipo": _campo_tipo(tipo),
        "titulo": titulo,
        "descripcion": descripcion,
    }


class _MotorFake:
    """Sustituye a `LLMEngine`: `consultar()` devuelve lo que se haya
    configurado para ese `version_prompt`, o lanza la excepcion
    configurada. Nunca toca la red ni `anthropic`. El color NO pasa por
    aqui -- sale de pixeles (`_color_dominante_rgb`), nunca del VLM
    (architecture.md Costura 1). "estado" TAMPOCO tiene una llamada VLM
    propia (ELIMINADA 2026-07-17, ver `core/extract.py`): vive dentro de
    la respuesta de sintesis, como cualquier otro campo. La respuesta de
    sintesis por defecto "no opina en nada" (`_respuesta_sintesis()`) --
    gap-filler, ver core/extract.py: si la sintesis no propone nada, no
    pisa nada, y los tests que no configuran su propia respuesta de
    sintesis siguen viendo el comportamiento de la agregacion vieja."""

    def __init__(self) -> None:
        self.respuestas: dict[str, dict] = {
            VERSION_PROMPT_SINTESIS: _respuesta_sintesis(),
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
    """legibilidad.json producto 4 (Trampa 1): el estampado gigante dice
    'ORIGINALS' (estampado_o_grafico); la marca real es 'JACK & JONES', en
    la etiqueta de cuello (etiqueta_interior). La etiqueta de cuello REAL
    tambien trae 'Originals' en cursiva (es la LINEA, no la marca) -- eso
    NO se modela como texto legible aparte aqui (viviria en la MISMA
    etiqueta que ya dice JACK & JONES), pero la regla que importa es la
    misma: 'ORIGINALS'/'Originals' JAMAS es, por si solo, lo que se
    PUBLICA como marca (`_UBICACIONES_VALIDAS_MARCA` excluye
    `estampado_o_grafico`).

    C1/INC-010 corrigio la regla original ("el estampado SIEMPRE se ignora
    si hay una etiqueta que diga otra cosa"): eso era exactamente el bug --
    `_candidatos_de_campo` filtraba por ubicacion ANTES de comprobar si
    habia conflicto, asi que un estampado que CONTRADICE la etiqueta se
    borraba en silencio. Ahora es un CONFLICTO real, no una eleccion
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
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert grupo.campo.valor is None
        assert grupo.campo.confianza == "baja"
        assert len(grupo.alternativas) == 2
        valores = {c.texto for c in grupo.alternativas}
        assert valores == {"ORIGINALS", "JACK & JONES"}

    def test_estampado_solo_sin_etiqueta_nunca_publica_marca(self):
        """Invariante A de la regla dura #1, SIN cambios: un estampado sin
        ninguna etiqueta_interior competidora NUNCA se convierte en el
        valor publicado de marca -- pero SI se ensena su recorte
        (`representante`), nunca en silencio absoluto."""
        lecturas = [
            _lectura(
                fichero="IMG_estampado.jpg",
                ubicacion="estampado_o_grafico",
                contenido_probable="marca",
                texto="ORIGINALS",
            )
        ]
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert grupo.campo.valor is None
        assert grupo.alternativas == ()
        assert grupo.representante is not None  # se ensena el pixel igual
        assert grupo.representante.texto == "ORIGINALS"

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
    """legibilidad.json producto 5 (Trampa 2): 'UMBRO' bordado (nitido, en
    el pecho -- ubicacion='estampado_o_grafico') Y 'RAMI JALAB' en la
    etiqueta de cuello (tambien nitido, ubicacion='etiqueta_interior') --
    prenda reetiquetada. Cualquier regla de precedencia acierta en una y
    miente en la otra: la unica salida correcta es null + AMBAS
    candidatas para Diego, cada una con su propio recorte."""

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
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        assert grupo.campo.valor is None
        assert grupo.campo.confianza == "baja"
        assert len(grupo.alternativas) == 2
        valores = {c.texto for c in grupo.alternativas}
        assert valores == {"UMBRO", "RAMI JALAB"}
        assert all(isinstance(c, LecturaCrop) for c in grupo.alternativas)

    def test_ROJO_contra_el_extractor_ingenuo(self):
        """El extractor ingenuo (primer texto legible de ese contenido) SI
        elige uno de los dos -- exactamente la mentira plausible que la
        regla dura evita."""
        lecturas = self._lecturas()
        elegido = _extractor_ingenuo_primero_legible(lecturas, "marca")
        assert elegido in ("UMBRO", "RAMI JALAB")  # eligio UNO, sin saber cual es el real


class TestTrampaTextoOcluidoNuncaPlausible:
    """legibilidad.json producto 2 (Trampa 3): el frontal dice
    '.Pocket ____' con la segunda palabra TAPADA POR UN CABLE. Un VLM
    ingenuo dira 'Pocket Life' con confianza alta porque es lo plausible
    -- NO ESTA EN EL PIXEL."""

    def test_legible_false_fuerza_texto_none_pase_lo_que_pase_en_el_json(self):
        """La red de seguridad de `_parsear_lectura_crop`: aunque el JSON
        crudo del modelo traiga un texto (el modelo ignoro la instruccion
        del prompt), `legible=False` lo anula en CODIGO. Contrato v2:
        `hallazgos` es una lista -- se anula CADA hallazgo, no solo uno."""
        datos_del_modelo_que_alucina = _respuesta_crop(
            ubicacion="codigo_o_modelo_impreso",
            hallazgos=[{"contenido_probable": "modelo", "legible": False, "texto": "Pocket Life"}],
        )
        lecturas = _parsear_lectura_crop(datos_del_modelo_que_alucina, "IMG_frontal.jpg", (5, 5, 40, 15))

        assert len(lecturas) == 1
        assert lecturas[0].legible is False
        assert lecturas[0].texto is None  # nunca "Pocket Life"

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
        grupo = _agregar_campo_texto(lecturas, "modelo", _UBICACIONES_VALIDAS_MODELO)

        assert grupo.campo.valor is None
        assert grupo.campo.confianza == "baja"
        # PRESENTE_ILEGIBLE: hubo un intento sobre esta foto -> fuente="foto"
        # (hay evidencia de que se miro, aunque no se pudiera leer), no
        # "inferido" (que se reserva para NO_FOTOGRAFIADO, sin evidencia).
        assert grupo.campo.fuente == "foto"
        assert grupo.campo.evidencia is not None
        assert grupo.campo.evidencia.fichero == "IMG_frontal.jpg"
        assert grupo.alternativas == ()
        # Y el recorte SI se ensena a Diego (con el motivo), aunque el
        # valor sea null -- "el extractor no afirma, propone y ensena el pixel".
        assert grupo.representante is not None

    def test_ROJO_contra_un_parseo_ingenuo_que_no_respeta_legible(self):
        """Un parseo ingenuo que confie en `texto` tal cual venga del JSON
        (sin la red de seguridad de `_parsear_lectura_crop`) SI cuela el
        valor alucinado."""
        hallazgo_del_modelo_que_alucina = {"legible": False, "texto": "Pocket Life"}
        texto_ingenuo = hallazgo_del_modelo_que_alucina["texto"]  # sin mirar "legible"
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
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)

        # Ni siquiera cuenta como "intento": no hay evidencia de que ESTE
        # producto tuviera una etiqueta ahi -- NO_FOTOGRAFIADO, no PRESENTE_ILEGIBLE.
        assert grupo.campo.valor is None
        assert grupo.campo.fuente == "inferido"
        assert grupo.campo.confianza == "baja"
        assert grupo.alternativas == ()

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


class TestMarcadorDesperfectosCazaElBugRealDeDiego:
    """Decision de Diego (2026-07-17), medida sobre 3 fotos REALES de un
    lote: el pipeline NO PUEDE juzgar si un texto "es" un desperfecto sin
    alucinar. La version anterior (`ubicacion == "papel_manuscrito"` +
    `contenido_probable == "desperfecto"` como unica senal) publico:
      - el texto IMPRESO en la caja de un masajeador ("masajeador LH;
        Laser Rodilla", foto IMG_20260714_101637) como si fuera un
        desperfecto -- el VLM clasifico mal su `ubicacion` como
        "papel_manuscrito".
      - un NUMERO DE LOTE ("2250028", foto IMG_20260714_101750) escrito a
        mano de VERDAD en una pegatina -- SI era manuscrito, pero NO era
        un desperfecto.
    Ahora `_agregar_campo_desperfectos` (alias de
    `_agregar_campo_desde_marcador`) no juzga nada: busca la palabra clave
    "DESPERFECTOS:" que Diego escribe, en CUALQUIER ubicacion. `[INC-012]`
    sigue vigente para el TECHO de confianza: nunca "alta" salvo EAN con
    checksum."""

    def test_con_marcador_publica_fuente_foto_y_confianza_media(self):
        lecturas = [
            _lectura(
                fichero="IMG_papel.jpg",
                bbox=(3, 3, 30, 12),
                ubicacion="papel_manuscrito",
                contenido_probable="desperfecto",
                texto="DESPERFECTOS: CREMALLERA ROTA",
            )
        ]
        grupo = _agregar_campo_desperfectos(lecturas)

        assert grupo.campo.valor == "CREMALLERA ROTA"  # el ruido de OCR se conserva verbatim
        assert grupo.campo.fuente == "foto"  # es una transcripcion, no un dato tecleado
        assert grupo.campo.confianza == "media"  # nunca "alta" (salvo EAN con checksum)
        assert grupo.campo.evidencia is not None
        assert grupo.campo.evidencia.fichero == "IMG_papel.jpg"

    def test_EL_BUG_texto_impreso_de_caja_sin_marcador_ya_no_cuela(self):
        """EL TEST QUE IMPORTA: el nombre del producto, impreso en la
        caja, NO es un desperfecto -- sin marcador, `valor=None`, pase lo
        que declare `ubicacion`/`contenido_probable` (la senal vieja)."""
        grupo = _agregar_campo_desperfectos(
            [
                _lectura(
                    fichero="IMG_20260714_101637_5.jpg",
                    ubicacion="papel_manuscrito",  # mal clasificado por el VLM, como en el bug real
                    contenido_probable="otro",
                    texto="masajeador LH; Laser Rodilla",
                )
            ]
        )
        assert grupo.campo.valor is None
        assert grupo.campo.fuente == "inferido"

    def test_numero_de_lote_manuscrito_de_verdad_sin_marcador_tampoco_cuela(self):
        """El numero de lote SI estaba escrito a mano (a diferencia del
        caso anterior) pero NO es un desperfecto -- ser manuscrito nunca
        fue la senal correcta, el marcador si."""
        grupo = _agregar_campo_desperfectos(
            [
                _lectura(
                    fichero="IMG_20260714_101750_5.jpg",
                    ubicacion="papel_manuscrito",
                    contenido_probable="desperfecto",  # el VLM SI lo etiqueto como desperfecto
                    texto="2250028",
                )
            ]
        )
        assert grupo.campo.valor is None

    def test_marcador_leido_correctamente_en_nota_real_de_diego(self):
        """El unico papel manuscrito REAL de este lote SI trae el
        marcador -- caso feliz."""
        grupo = _agregar_campo_desperfectos(
            [_lectura(ubicacion="papel_manuscrito", texto="DESPERFECTOS: CKENAUERA ROTA")]
        )
        assert grupo.campo.valor == "CKENAUERA ROTA"  # el ruido de OCR ("CKENAUERA") NO se corrige
        assert grupo.campo.fuente == "foto"

    def test_marcador_funciona_en_cualquier_ubicacion_no_solo_papel_manuscrito(self):
        """La `ubicacion` ya NO filtra -- una pegatina de la caja con el
        marcador escrito a boli tambien cuenta."""
        grupo = _agregar_campo_desperfectos(
            [_lectura(ubicacion="codigo_o_modelo_impreso", texto="DESPERFECTOS: mancha en la base")]
        )
        assert grupo.campo.valor == "mancha en la base"
        assert grupo.campo.fuente == "foto"

    @pytest.mark.parametrize("variante", ["DESPERFECT0S:", "DESPERFECTQS:", "DESPERFECT05:", "Desperfect0s:"])
    def test_marcador_con_ruido_de_ocr_se_caza_fuzzy(self, variante):
        grupo = _agregar_campo_desperfectos(
            [_lectura(ubicacion="papel_manuscrito", texto=f"{variante} cremallera rota")]
        )
        assert grupo.campo.valor == "cremallera rota", variante

    def test_pertenece_al_producto_false_no_cuela_aunque_tenga_marcador(self):
        grupo = _agregar_campo_desperfectos(
            [_lectura(pertenece_al_producto=False, texto="DESPERFECTOS: mancha de fondo ajena")]
        )
        assert grupo.campo.valor is None

    def test_marcador_sin_nada_detras_no_es_un_desperfecto(self):
        grupo = _agregar_campo_desperfectos([_lectura(texto="DESPERFECTOS:")])
        assert grupo.campo.valor is None

    def test_sin_ningun_texto_el_campo_es_null_sin_reventar(self):
        grupo = _agregar_campo_desperfectos([])
        assert grupo.campo.valor is None
        assert "DESPERFECTOS" in grupo.motivo  # motivo explicito: escribelo en el papel

    def test_sin_papel_el_campo_es_null(self):
        grupo = _agregar_campo_desperfectos([_lectura(ubicacion="etiqueta_interior", texto="Reebok")])
        assert grupo.campo.valor is None


class TestMedidasYaNoUsaElMarcador:
    """`[listing-audit] SERIO, 2026-07-17 (2a ronda)`: `medidas` PROBO un
    camino por marcador ("MEDIDAS:") y se QUITO -- "MEDIDA:" (singular)
    seguia colisionando a distancia 1, y una caja imprimiendo SUS PROPIAS
    dimensiones de embalaje bajo "Medidas:" no se podia distinguir de
    forma fiable de la nota de Diego (no hay senal estructural). Decision
    de Diego: `medidas` vuelve a salir UNICAMENTE del metro fotografiado
    (regla dura #5, `_evaluar_medidas`) -- por la regla anti-loop
    (`decision-making.md` SS6: 2 rondas del mismo campo sin converger ->
    replantear, no un 3er parche). El bug de la caja es ahora
    INEXPRESABLE: no hay NINGUN camino de codigo por el que un texto
    leido (con o sin marcador) pueda llegar a `campos["medidas"]`."""

    def test_una_nota_con_MEDIDAS_no_rellena_medidas_sin_foto_de_metro(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_nota.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(3, 3, 30, 12), texto_ocr="MEDIDAS: 80x60", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple(
            "otro", True, "MEDIDAS: 80x60", ubicacion="papel_manuscrito"
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        # sin foto de metro, "medidas" se queda NO_FOTOGRAFIADO -- el
        # marcador ya no se mira EN ABSOLUTO para este campo.
        assert resultado.campos["medidas"].valor is None

    def test_medida_de_caja_ya_no_puede_contaminar_medidas_del_producto(self, tmp_path, monkeypatch):
        """EL BUG REAL del audit ('MEDIDAS: 30 x 20 x 10 cm' de una caja)
        es ahora INEXPRESABLE, no solo mitigado: no hay ningun camino de
        codigo por el que este texto pueda llegar a `campos['medidas']`."""
        foto = _foto_sintetica(tmp_path / "IMG_caja.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(3, 3, 30, 12), texto_ocr="MEDIDA: 30 x 20 x 10 cm", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple(
            "otro", True, "MEDIDA: 30 x 20 x 10 cm", ubicacion="codigo_o_modelo_impreso"
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["medidas"].valor is None

    def test_el_metro_sigue_siendo_la_unica_fuente_real_de_medidas(self, tmp_path, monkeypatch):
        """Confirmacion positiva: CON foto de metro valida, `medidas` SI
        se rellena -- el camino del metro no se toco, solo se quito el
        atajo del marcador."""
        foto = _foto_sintetica(tmp_path / "IMG_metro.jpg")
        region_metro = RegionOCR(
            fichero=foto.name, bbox=(10, 10, 5, 500),
            texto_ocr="69899995999291909698925959556575", score=0.66,
        )
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region_metro])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_METRO] = {
            "cero_visible": True, "borde_prenda_visible": True, "medida_cm": 45.0,
        }
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["medidas"].valor == 45.0
        assert resultado.campos["medidas"].fuente == "foto"

    def test_medidas_ya_no_esta_en_los_marcadores_de_nota(self):
        """El propio mapa `_MARCADORES_NOTA` ya no conoce "medidas" --
        pedirselo a `_agregar_campo_desde_marcador` debe reventar (KeyError),
        no devolver `None` en silencio (eso seria un fallback disfrazado)."""
        from core.extract import _MARCADORES_NOTA

        assert "medidas" not in _MARCADORES_NOTA
        assert "desperfectos" in _MARCADORES_NOTA
        with pytest.raises(KeyError):
            _agregar_campo_desde_marcador([_lectura(texto="MEDIDAS: 80x60")], "medidas")


class TestMedidasYDesperfectosFueraDeLaSintesis:
    """Regla dura #6 (Diego, 2026-07-17): el modelo NO PUEDE rellenar
    `desperfectos`/`medidas` con su "mejor intento" -- se aserta contra el
    `json_schema`/gap-filler REAL, no contra el docstring."""

    def test_medidas_no_esta_en_los_campos_de_sintesis(self):
        from core.extract import _CAMPOS_SINTESIS, _CAMPOS_SINTESIS_GAP

        assert "medidas" not in _CAMPOS_SINTESIS
        assert "medidas" not in _CAMPOS_SINTESIS_GAP

    def test_desperfectos_no_esta_en_los_campos_de_sintesis(self):
        from core.extract import _CAMPOS_SINTESIS, _CAMPOS_SINTESIS_GAP

        assert "desperfectos" not in _CAMPOS_SINTESIS
        assert "desperfectos" not in _CAMPOS_SINTESIS_GAP

    def test_medidas_no_esta_en_el_json_schema_de_la_sintesis(self):
        assert "medidas" not in ESQUEMA_SINTESIS_FICHA["properties"]
        assert "medidas" not in ESQUEMA_SINTESIS_FICHA["required"]

    def test_esquema_sintesis_sin_type_array_con_enum(self):
        """Guard de la CLASE del 400 que Diego vio al re-extraer: structured
        outputs de Anthropic RECHAZA con status 400 cualquier campo que declare
        `type` como ARRAY (p.ej. `["string","null"]`) *y* un `enum` a la vez
        ("Enum value 'hombre' does not match declared type ['string','null']").
        Es determinista (se dispara en TODA sintesis) y ningun test de estructura
        lo cazaba porque `_MotorFake` ignora el json_schema y nunca lo manda a la
        API real. Nullable+enum se expresa con `anyOf`, no con la union en `type`.

        Recorre TODO el esquema (los sub-esquemas de estado/tipo/campos tambien)
        para que un campo enum+nullable futuro no reintroduzca el 400 en silencio."""

        def _recorre(nodo, ruta="raiz"):
            if not isinstance(nodo, dict):
                return
            tipo = nodo.get("type")
            if isinstance(tipo, list) and "enum" in nodo:
                raise AssertionError(
                    f"{ruta}: `type` array {tipo} + `enum` -> 400 de structured "
                    f"outputs. Usa anyOf: [{{type:string, enum:[...]}}, {{type:null}}]."
                )
            for clave in ("properties", "$defs", "definitions"):
                for sub_nombre, sub in (nodo.get(clave) or {}).items():
                    _recorre(sub, f"{ruta}.{clave}.{sub_nombre}")
            for clave in ("anyOf", "allOf", "oneOf"):
                for i, sub in enumerate(nodo.get(clave) or []):
                    _recorre(sub, f"{ruta}.{clave}[{i}]")
            if isinstance(nodo.get("items"), dict):
                _recorre(nodo["items"], f"{ruta}.items")

        _recorre(ESQUEMA_SINTESIS_FICHA)


# Palabras REALES de packaging espanol contra las que se MIDIO el umbral
# fuzzy del marcador (no elegido a ojo) -- ver el comentario de
# `_UMBRALES_DISTANCIA_MARCADOR` en `core/extract.py`. Solo `desperfectos`
# usa marcador hoy (`medidas` se quito, `TestMedidasYaNoUsaElMarcador`).
_PALABRAS_PACKAGING_DESPERFECTOS = ("PERFECTOS", "IMPERFECTOS", "DEFECTOS")


class TestUmbralMarcadorMedidoContraPalabrasReales:
    """`[listing-audit] BLOQUEANTE, 2026-07-17`, REPRODUCIDO EJECUTANDO
    (no de oidas): la primera version de este match usaba un umbral fuzzy
    GLOBAL (distancia <=3), calibrado a ojo, que dejaba entrar "acabados
    PERFECTOS" como si fuera el marcador.

    EL TEST QUE IMPORTA: NINGUNA palabra de esta lista, en una frase SIN
    el separador pegado (como aparece de verdad en un texto de caja),
    debe matchear el marcador."""

    @pytest.mark.parametrize("palabra", _PALABRAS_PACKAGING_DESPERFECTOS)
    def test_ninguna_palabra_de_packaging_matchea_desperfectos(self, palabra):
        texto = f"acabados {palabra} garantizados de fabrica"
        assert _extraer_valor_tras_marcador(texto, MARCADOR_DESPERFECTOS) is None, palabra

    def test_acabados_perfectos_no_es_desperfecto(self):
        assert _extraer_valor_tras_marcador("acabados PERFECTOS", MARCADOR_DESPERFECTOS) is None

    def test_productos_imperfectos_no_es_desperfecto(self):
        valor = _extraer_valor_tras_marcador("PRODUCTOS IMPERFECTOS", MARCADOR_DESPERFECTOS)
        assert valor is None

    def test_desperfectos_no_se_trunca_por_una_palabra_parecida_a_otro_marcador(self):
        """FIX 5 (mecanismo generico, se conserva aunque hoy solo haya un
        marcador real): 'media' (dentro de 'media manga') no lleva ningun
        separador pegado -- no debe truncar la frase."""
        valor = _extraer_valor_tras_marcador("DESPERFECTOS: mancha en media manga", MARCADOR_DESPERFECTOS)
        assert valor == "mancha en media manga"

    def test_separador_no_pegado_no_matchea(self):
        """FIX 2: un espacio entre el token y el separador NO cuenta como
        'pegado'."""
        assert _extraer_valor_tras_marcador("DESPERFECTOS : algo", MARCADOR_DESPERFECTOS) is None

    @pytest.mark.parametrize("variante", ["DESPERFECT0S:", "DESPERFECTQS:", "DESPERFECT05:"])
    def test_ruido_ocr_real_de_desperfectos_sigue_cazandose(self, variante):
        valor = _extraer_valor_tras_marcador(f"{variante} cremallera rota", MARCADOR_DESPERFECTOS)
        assert valor == "cremallera rota", variante


class TestFix7EvidenciaIntegraNuncaConcatenaASiegas:
    """`[listing-audit] BLOQUEANTE, 2026-07-17`, FIX 7 (`[INC-011]` en otro
    disfraz): concatenar el valor de dos lecturas DISTINTAS en un solo
    `Campo.valor` pero ensenar la `evidencia`/`recorte` de UNA SOLA de
    ellas publica un dato con `fuente="foto"` sin que el pixel que lo
    prueba este a la vista. Ahora solo la PRIMERA se publica como valor;
    el resto viaja en `alternativas`, cada una con su PROPIO recorte
    (mismo mecanismo que ya usan marca/talla en conflicto)."""

    def test_dos_notas_en_fotos_distintas_no_se_concatenan_con_evidencia_de_una_sola(self):
        grupo = _agregar_campo_desde_marcador(
            [
                _lectura(fichero="a.jpg", bbox=(1, 1, 5, 5), texto="DESPERFECTOS: cremallera rota"),
                _lectura(fichero="b.jpg", bbox=(2, 2, 6, 6), texto="DESPERFECTOS: mancha lejia"),
            ],
            "desperfectos",
        )
        assert grupo.campo.valor == "cremallera rota"
        assert grupo.campo.evidencia.fichero == "a.jpg"
        assert len(grupo.alternativas) == 1
        assert grupo.alternativas[0].texto == "mancha lejia"
        assert grupo.alternativas[0].fichero == "b.jpg"

    def test_una_sola_nota_no_genera_alternativas(self):
        grupo = _agregar_campo_desde_marcador(
            [_lectura(fichero="a.jpg", texto="DESPERFECTOS: cremallera rota")], "desperfectos"
        )
        assert grupo.campo.valor == "cremallera rota"
        assert grupo.alternativas == ()


class TestComposicionEliminadaDeLaFicha:
    """Decision de Diego (2026-07-17): "composicion" salio ENTERA de la
    ficha -- solo aplicaba a ropa y no le aportaba valor. `_campo_composicion`
    (la funcion PURA de este modulo) ya no existe; `extraer_producto` ya no
    produce esa clave en absoluto (ni siquiera con `valor=None`)."""

    def test_no_esta_en_campos_producidos(self):
        from core.extract import CAMPOS_PRODUCIDOS

        assert "composicion" not in CAMPOS_PRODUCIDOS

    def test_no_esta_en_campos_de_sintesis(self):
        from core.extract import _CAMPOS_SINTESIS, _CAMPOS_SINTESIS_GAP

        assert "material" not in _CAMPOS_SINTESIS
        assert "material" not in _CAMPOS_SINTESIS_GAP


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
    """Contrato v2: `_intentar_atajo_ocr` devuelve un `LecturaCrop`
    (`origen="atajo_ocr"`) directamente, NO un `(nombre, Campo)` -- entra
    al MISMO pool de candidatos que las lecturas del VLM (ya no hay
    carretera paralela, ver docstring del modulo)."""

    def test_ean_limpio_con_score_alto_se_acepta_directo(self):
        region = RegionOCR(
            fichero="IMG_ean.jpg", bbox=(0, 0, 10, 10), texto_ocr="EANCODE:*8445061029720", score=0.91
        )
        resultado = _intentar_atajo_ocr(region)
        assert resultado is not None
        assert isinstance(resultado, LecturaCrop)
        assert resultado.contenido_probable == "ean"
        assert resultado.texto == "8445061029720"
        assert resultado.legible is True
        assert resultado.pertenece_al_producto is True
        assert resultado.origen == "atajo_ocr"

    def test_modelo_limpio_con_score_alto_se_acepta_directo(self):
        region = RegionOCR(fichero="IMG_modelo.jpg", bbox=(0, 0, 10, 10), texto_ocr="Model:LLLT-200", score=0.88)
        resultado = _intentar_atajo_ocr(region)
        assert resultado is not None
        assert resultado.contenido_probable == "modelo"
        assert resultado.texto == "LLLT-200"
        assert resultado.origen == "atajo_ocr"

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
    """Contrato v2: `_es_repeticion_de_un_campo_ya_resuelto` recibe una
    SECUENCIA de `LecturaCrop` (los candidatos de atajo), no un dict de
    `Campo` -- unifica atajo y VLM en el mismo pool (docstring del modulo,
    punto 5)."""

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
        candidatos_atajo = [
            _lectura(contenido_probable="ean", texto="8445061029720", origen="atajo_ocr"),
        ]
        # Medido: el producto 1 repite el mismo EAN junto a una referencia interna.
        assert _es_repeticion_de_un_campo_ya_resuelto("THO8LASLHR_UDS *8445061029720", candidatos_atajo) is True

    def test_texto_sin_relacion_con_el_atajo_no_se_descarta(self):
        candidatos_atajo = [
            _lectura(contenido_probable="ean", texto="8445061029720", origen="atajo_ocr"),
        ]
        assert _es_repeticion_de_un_campo_ya_resuelto("Dlufthous", candidatos_atajo) is False

    def test_sin_atajos_resueltos_nunca_se_descarta_por_repeticion(self):
        assert _es_repeticion_de_un_campo_ya_resuelto("Reebok", []) is False


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
    """Contrato v2: `_parsear_lectura_crop` devuelve una LISTA de
    `LecturaCrop` (regla dura #4 -- un mismo recorte puede traer varios
    hallazgos, p.ej. marca Y talla en la misma etiqueta de cuello)."""

    def test_falta_una_clave_obligatoria_lanza(self):
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop({"legible": True}, "IMG_1.jpg", (0, 0, 1, 1))

    def test_ubicacion_desconocida_lanza(self):
        datos = _respuesta_crop(
            ubicacion="un_valor_que_no_existe",
            hallazgos=[{"contenido_probable": "marca", "legible": True, "texto": "Nike"}],
        )
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))

    def test_contenido_probable_desconocido_lanza(self):
        datos = _respuesta_crop(
            hallazgos=[{"contenido_probable": "un_contenido_que_no_existe", "legible": True, "texto": "Nike"}]
        )
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))

    def test_hallazgos_vacio_lanza(self):
        datos = _respuesta_crop(hallazgos=[])
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))

    def test_hallazgos_que_no_es_lista_lanza(self):
        datos = {"pertenece_al_producto": True, "ubicacion": "etiqueta_interior", "hallazgos": "Nike"}
        with pytest.raises(RespuestaVLMInvalidaError):
            _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))

    def test_texto_vacio_se_normaliza_a_none(self):
        datos = _respuesta_crop_simple("marca", True, "   ")
        lecturas = _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1))
        assert len(lecturas) == 1
        assert lecturas[0].texto is None

    def test_texto_ocr_crudo_se_traslada_a_TODOS_los_lectura_crop(self):
        """El OCR crudo de la region viaja a CADA `LecturaCrop` resultante
        -- ya no concede confianza (`[INC-012]`), solo alimenta el aviso
        "dudoso" informativo en el `motivo`."""
        datos = _respuesta_crop(
            hallazgos=[
                {"contenido_probable": "marca", "legible": True, "texto": "Reebok"},
                {"contenido_probable": "talla", "legible": True, "texto": "M"},
            ]
        )
        lecturas = _parsear_lectura_crop(datos, "IMG_1.jpg", (0, 0, 1, 1), texto_ocr_crudo="Raabdk")
        assert len(lecturas) == 2
        assert all(lec.texto_ocr_crudo == "Raabdk" for lec in lecturas)

    def test_un_solo_recorte_produce_dos_hallazgos_marca_y_talla(self):
        """Regla dura #4, el corazon del rediseno v2: una etiqueta de
        cuello real trae la marca Y la talla juntas -- en el diseno
        anterior esto era estructuralmente INALCANZABLE (un crop -> un
        solo campo). Ahora `hallazgos` es una lista y produce las DOS."""
        datos = _respuesta_crop(
            hallazgos=[
                {"contenido_probable": "marca", "legible": True, "texto": "Reebok"},
                {"contenido_probable": "talla", "legible": True, "texto": "M"},
            ]
        )
        lecturas = _parsear_lectura_crop(datos, "IMG_20260714_110805.jpg", (0, 0, 10, 10))
        assert len(lecturas) == 2
        por_contenido = {lec.contenido_probable: lec.texto for lec in lecturas}
        assert por_contenido == {"marca": "Reebok", "talla": "M"}
        # las dos comparten fichero+bbox -- son el MISMO recorte fisico.
        assert lecturas[0].fichero == lecturas[1].fichero == "IMG_20260714_110805.jpg"
        assert lecturas[0].bbox == lecturas[1].bbox == (0, 0, 10, 10)


# ============================================================================
# 4. LEY NUEVA DE CONFIANZA (`[INC-012]`): "alta" es INALCANZABLE salvo EAN
# ============================================================================


class TestLaConfianzaNuncaLlegaAAltaSalvoEan:
    """La maquinaria de corroboracion de `[INC-010]` (que SI permitia
    "alta" desde una lectura de texto corroborada) fue la causa raiz de
    `[INC-012]` (dos senales que miran el MISMO crop no son independientes,
    y `XL`/`XXL` "corroboraban" con 0.80). Fue ELIMINADA. Estos tests
    demuestran, INTENTANDOLO con la senal MAS favorable posible, que el
    techo sigue siendo "media" -- no basta con leer el docstring."""

    def test_testigo_identico_al_vlm_no_sube_a_alta(self):
        """El escenario MAS favorable para "alta" bajo la ley VIEJA: el
        OCR crudo de la MISMA region coincide EXACTAMENTE con lo que dice
        el VLM. Bajo la ley NUEVA esto sigue en 'media'."""
        lecturas = [_lectura(texto="Reebok", texto_ocr_crudo="Reebok")]
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert grupo.campo.valor == "Reebok"
        assert grupo.campo.confianza == "media"  # NUNCA "alta"

    def test_dos_fotos_independientes_de_acuerdo_no_suben_a_alta(self):
        lecturas = [
            _lectura(fichero="IMG_a.jpg", texto="Reebok"),
            _lectura(fichero="IMG_b.jpg", texto="Reebok"),
        ]
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert grupo.campo.confianza == "media"

    def test_ningun_campo_de_texto_alcanza_alta_marca_talla_o_modelo(self):
        casos = (
            ("marca", _UBICACIONES_VALIDAS_MARCA),
            ("talla", _UBICACIONES_VALIDAS_TALLA),
            ("modelo", _UBICACIONES_VALIDAS_MODELO),
        )
        for contenido, ubicaciones_validas in casos:
            lecturas = [_lectura(contenido_probable=contenido, texto="XXL", texto_ocr_crudo="XXL")]
            grupo = _agregar_campo_texto(lecturas, contenido, ubicaciones_validas)
            assert grupo.campo.confianza != "alta", f"'{contenido}' alcanzo 'alta' sin ser un EAN"

    def test_similitud_dudosa_solo_toca_el_motivo_nunca_la_confianza(self):
        """El OCR leyo algo que NO se parece al VLM. Bajo la ley VIEJA
        (`[INC-010]`) esto era "contradiccion" y bajaba a `baja` -- ahora
        el pipeline no decide que el VLM miente solo porque el OCR local
        (que ya sabemos que falla, `truth-loop.md` SS E) leyo otra cosa:
        el valor SIGUE publicandose, con techo `media`, y solo se anade un
        AVISO informativo al motivo."""
        lecturas = [_lectura(texto="Nike", texto_ocr_crudo="Raabdk")]
        grupo = _agregar_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        assert grupo.campo.valor == "Nike"
        assert grupo.campo.confianza == "media"
        assert "dudoso" in grupo.motivo.lower()

    def test_similitud_normalizada_y_testigo_valido_siguen_calibrados(self):
        """Las funciones de calibracion sobreviven (alimentan el aviso
        informativo), aunque ya no toquen `confianza`. Pares reales del
        golden set (ver constante `UMBRAL_SIMILITUD_DUDOSO`): 'Reebok'/
        'Raabdk' SI deben parecerse (garbled real del mismo logo); 'JACK &
        JONES'/'ESTI550' NO (marcas realmente distintas)."""
        assert _similitud_normalizada("Reebok", "Raabdk") >= UMBRAL_SIMILITUD_DUDOSO
        assert _similitud_normalizada("JACK & JONES", "ESTI550") < UMBRAL_SIMILITUD_DUDOSO
        assert _similitud_normalizada("ORIGINAL MARINES", "ORIGINAL MARINES") >= UMBRAL_SIMILITUD_DUDOSO
        assert _es_testigo_valido("XXL") is True
        assert _es_testigo_valido("") is False
        assert _es_testigo_valido(None) is False


# ============================================================================
# C5 -- EAN sin checksum GS1 valido no es un EAN (UNICO camino a "alta")
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
        resultado = _intentar_atajo_ocr(region)
        assert resultado is not None
        assert resultado.contenido_probable == "ean"
        assert resultado.texto == "8445061029720"

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
# 5. INTEGRACION -- ExtractorEngine.extraer_producto, LOS 5 CASOS DE FALLO
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
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("marca", False, None)
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor is None
        assert resultado.campos["marca"].confianza == "baja"
        assert resultado.campos["marca"].fuente == "foto"  # hubo un intento real
        assert resultado.fallos == ()
        # el recorte SI se ensena a Diego aunque no haya valor.
        assert resultado.propuestas["marca"].recorte is not None

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
    def test_marca_por_vlm_desde_una_etiqueta_limpia_techo_media(self, tmp_path, monkeypatch):
        """`[INC-012]`: ninguna lectura de VLM llega a "alta", ni siquiera
        en el caso feliz (etiqueta limpia, sin ambiguedad)."""
        foto = _foto_sintetica(tmp_path / "IMG_ok.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("marca", True, "Reebok")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.campos["marca"].fuente == "foto"
        assert resultado.campos["marca"].confianza == "media"  # NUNCA "alta"
        assert "composicion" not in resultado.campos  # ELIMINADA de la ficha (2026-07-17)

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
        assert resultado.campos["ean"].confianza == "alta"  # UNICO camino a "alta"
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
        assert VERSION_PROMPT_SINTESIS in version_prompts
        # "estado" YA NO genera una solicitud VLM propia (ELIMINADA
        # 2026-07-17) -- vive dentro de VERSION_PROMPT_SINTESIS.
        assert "extract-estado-v1" not in version_prompts
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
        # el color nunca gasta una llamada VLM DIRECTA -- lo unico que puede
        # haber llamado al motor es la sintesis (SIEMPRE se llama, pero con
        # el `_MotorFake` por defecto "no opina" -- gap-filler, no pisa el
        # None de arriba).
        assert all(vp != VERSION_PROMPT_CROP for _, vp in motor.llamadas)
        assert {vp for _, vp in motor.llamadas} <= {VERSION_PROMPT_SINTESIS}

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
        """`estado` (2026-07-17) vive DENTRO de la sintesis, como ENUM
        CERRADO -- SIEMPRE `fuente="inferido"`, pase lo que declare el
        modelo (regla dura #8, `truth-loop.md` SS A.4)."""
        foto = _foto_sintetica(tmp_path / "IMG_estado.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            estado={"valor": "Bueno", "motivo": "sin danos visibles", "confianza": "media"},
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["estado"].fuente == "inferido"
        assert resultado.campos["estado"].valor == "Bueno"
        assert resultado.campos["estado"].confianza == "media"
        # el motivo (la razon en texto libre) se conserva como caption.
        assert "sin danos visibles" in resultado.propuestas["estado"].motivo

    def test_estado_fuera_de_enum_no_inventa_un_valor(self, tmp_path, monkeypatch):
        """El bug real de Diego (2026-07-17): el evaluador ANTERIOR de
        estado devolvia PROSA libre ("El producto muestra buen estado de
        conservacion...") que ninguna UI podia reconocer contra el enum de
        6 literales -> "(sin elegir)" en 7/7 productos de un lote real.
        Ahora el json_schema fuerza un ENUM CERRADO a nivel de API, pero
        este test verifica la defensa EN CODIGO (decision-making.md SS13):
        si de todas formas llega algo fuera del enum (un mock, un bug del
        proveedor), el pipeline NUNCA inventa un valor de relleno tipo
        "Bueno" como comodin -- el campo queda en su placeholder
        `valor=None` y el fallo se anota, nunca en silencio."""
        foto = _foto_sintetica(tmp_path / "IMG_estado_prosa.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            estado={
                "valor": (
                    "El producto muestra buen estado de conservacion. La caja "
                    "presenta ligeros signos de uso con posibles marcas menores."
                ),
                "motivo": "",
                "confianza": "media",
            },
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["estado"].valor is None
        assert resultado.campos["estado"].fuente == "inferido"
        assert any("sintesis_estado_fuera_de_enum" in f for f in resultado.fallos)

    def test_estado_nunca_confianza_alta_aunque_el_modelo_la_proponga(self, tmp_path, monkeypatch):
        """El json_schema de la sintesis restringe `confianza` de "estado"
        a "media"/"baja" (nunca "alta") -- verificado aqui contra el
        contrato real de `ESQUEMA_SINTESIS_FICHA`, no solo leyendo el
        docstring (decision-making.md SS4)."""
        enum_confianza = ESQUEMA_SINTESIS_FICHA["properties"]["estado"]["properties"]["confianza"]["enum"]
        assert set(enum_confianza) == {"media", "baja"}
        assert "alta" not in enum_confianza

    def test_estado_enum_del_json_schema_coincide_con_schema_estado_ui_a_canonico(self):
        """El enum que ve el MODELO tiene que ser EXACTAMENTE el mismo que
        reconoce la UI (`ui/ficha.py::_OPCIONES_ESTADO`) -- derivado de
        `core.schema.ESTADO_UI_A_CANONICO`, nunca una lista hardcodeada
        aparte que pueda divergir (el bug que se esta corrigiendo)."""
        enum_valor = ESQUEMA_SINTESIS_FICHA["properties"]["estado"]["properties"]["valor"]["enum"]
        assert set(enum_valor) == set(ESTADO_UI_A_CANONICO)
        assert len(enum_valor) == 6

    def test_barre_el_enum_de_estado_entero_contra_construir_campo_y_parsear_estado(self):
        """(decision-making.md SS4/SS16, `[INC-018]`): no un representante
        comodo -- los 6 literales, uno a uno, por LOS DOS caminos que
        importan: `_construir_campo_estado_desde_sintesis` (extraccion) y
        `schema.parsear_estado` (export). Ninguno cae a `None`; ese
        round-trip es lo que hace que el export publique el literal
        correcto."""
        assert len(ESTADO_UI_A_CANONICO) == 6
        for literal in ESTADO_UI_A_CANONICO:
            decision = {"valor": literal, "motivo": "test", "confianza": "media"}
            campo = _construir_campo_estado_desde_sintesis(decision)
            assert campo is not None, f"{literal!r} no construyo un Campo"
            assert campo.valor == literal
            assert campo.fuente == "inferido"
            assert campo.confianza in ("media", "baja")

            canonico = parsear_estado(literal)
            assert canonico is not None, f"{literal!r} no parseo a EstadoCanonico"

    def test_extraer_producto_sin_fotos_lanza(self):
        extractor = ExtractorEngine(_MotorFake())
        with pytest.raises(ValueError):
            extractor.extraer_producto([])


# ============================================================================
# Propuesta: recorte EXACTAMENTE el que se mando, alternativas CON su recorte
# ============================================================================


class TestPropuestaExponeAlternativasConRecorte:
    """Trampa 2 (UMBRO/RAMI JALAB), al nivel de PIPELINE COMPLETO -- no
    solo la funcion pura de agregacion: verifica que `Propuesta.alternativas`
    llegan como `Candidato`, cada uno con SU PROPIO recorte YA ESCRITO A
    DISCO. 'Las dos con sus recortes', pedido explicitamente en la
    verificacion de este rescate."""

    def test_dos_marcas_en_conflicto_llegan_como_candidatos_con_recorte_propio(self, tmp_path, monkeypatch):
        foto_pecho = _foto_sintetica(tmp_path / "IMG_pecho.jpg")
        foto_cuello = _foto_sintetica(tmp_path / "IMG_cuello.jpg")
        region_pecho = RegionOCR(fichero=foto_pecho.name, bbox=(10, 10, 50, 20), texto_ocr="UMBRO", score=0.8)
        region_cuello = RegionOCR(fichero=foto_cuello.name, bbox=(20, 20, 60, 25), texto_ocr="RAMI SALAB", score=0.7)
        regiones = {foto_pecho.name: [region_pecho], foto_cuello.name: [region_cuello]}
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if fichero == foto_pecho.name:
                datos = _respuesta_crop_simple("marca", True, "UMBRO", ubicacion="estampado_o_grafico")
            else:
                datos = _respuesta_crop_simple("marca", True, "RAMI JALAB", ubicacion="etiqueta_interior")
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto_pecho, foto_cuello])

        propuesta = resultado.propuestas["marca"]
        assert propuesta.valor is None
        assert len(propuesta.alternativas) == 2
        valores = {c.valor for c in propuesta.alternativas}
        assert valores == {"UMBRO", "RAMI JALAB"}
        for candidato in propuesta.alternativas:
            assert isinstance(candidato, Candidato)
            assert candidato.recorte is not None
            assert candidato.recorte.exists()
            assert candidato.recorte.read_bytes()  # el fichero no esta vacio
            assert len(candidato.lecturas) >= 1
            assert any(isinstance(lec, Lectura) for lec in candidato.lecturas)

    def test_el_recorte_de_la_propuesta_es_exactamente_el_que_se_mando_al_vlm(self, tmp_path, monkeypatch):
        """El corazon del rediseno (docstring del modulo, punto 3): el
        recorte que se ENSENA es EXACTAMENTE el que se mando al modelo --
        se capturan los MISMOS bytes en el momento de construir la
        `Imagen`, y esos mismos bytes son los que se escriben a disco."""
        foto = _foto_sintetica(tmp_path / "IMG_marca.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(15, 15, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        bytes_mandados_al_vlm: list[bytes] = []
        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            motor.llamadas.append((imagenes[0].fichero, version_prompt))
            if version_prompt == VERSION_PROMPT_CROP:
                bytes_mandados_al_vlm.append(imagenes[0].bytes_)
                datos = _respuesta_crop_simple("marca", True, "Reebok")
            else:
                datos = {"estimacion_legible": False, "descripcion": None}
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto])

        assert len(bytes_mandados_al_vlm) == 1
        propuesta = resultado.propuestas["marca"]
        assert propuesta.recorte is not None
        assert propuesta.recorte.read_bytes() == bytes_mandados_al_vlm[0]


# ============================================================================
# C4 -- nombres de fichero duplicados fallan RUIDOSO, nunca aliasing en silencio
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
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("otro", False, None, ubicacion="otro")
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

    def test_marca_y_talla_de_fotos_disjuntas_dispara_el_aviso(self, tmp_path, monkeypatch):
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
            if fichero == foto_marca.name:
                datos = _respuesta_crop_simple("marca", True, "Reebok")
            else:
                datos = _respuesta_crop_simple("talla", True, "XXL")
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto_marca, foto_talla])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.campos["talla"].valor == "XXL"
        assert resultado.aviso_coherencia is not None
        assert "DISJUNTAS" in resultado.aviso_coherencia
        # Ninguno de los dos puede quedar en "alta" -- ya no llegaban a
        # "alta" de todas formas (ley nueva, `[INC-012]`), pero el aviso
        # tambien lo garantizaria si algo llegara.
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
            # Ambas lecturas de crop en esta foto (misma imagen, distinto bbox);
            # devolvemos segun el contenido esperado alternando por orden de llamada.
            indice_crop = sum(1 for f, vp in motor.llamadas if vp == VERSION_PROMPT_CROP)
            if indice_crop <= 1:
                datos = _respuesta_crop_simple("marca", True, "Reebok")
            else:
                datos = _respuesta_crop_simple("talla", True, "XXL")
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
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("marca", True, "Reebok")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"
        assert resultado.aviso_coherencia is None

    def test_ean_alta_se_degrada_a_media_si_es_disjunto_de_otro_campo_identidad(self, tmp_path, monkeypatch):
        """El UNICO camino de este modulo a `confianza="alta"` (un EAN con
        checksum valido) tambien pasa por la defensa de coherencia: si ese
        EAN queda disjunto de otro campo de identidad con valor, se
        degrada a "media" igual que cualquier otro -- el aviso tiene
        dientes de verdad, no solo para los campos que ya estaban en
        "media"."""
        foto_ean = _foto_sintetica(tmp_path / "IMG_ean.jpg")
        foto_talla = _foto_sintetica(tmp_path / "IMG_talla.jpg")
        region_ean = RegionOCR(
            fichero=foto_ean.name, bbox=(1, 1, 5, 5), texto_ocr="EANCODE:*8445061029720", score=0.91
        )
        region_talla = RegionOCR(fichero=foto_talla.name, bbox=(1, 1, 5, 5), texto_ocr="XL_TALLA_XYZ", score=0.5)
        regiones = {foto_ean.name: [region_ean], foto_talla.name: [region_talla]}
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("talla", True, "XL")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto_ean, foto_talla])

        assert resultado.campos["ean"].valor == "8445061029720"
        assert resultado.campos["talla"].valor == "XL"
        assert resultado.aviso_coherencia is not None
        assert resultado.campos["ean"].confianza == "media"  # degradado desde "alta"


# ============================================================================
# Fase 2 Paso 2 -- serializar_extraccion / deserializar_extraccion:
# round-trip fiel por JSON (lo que core/store.py escribe en productos.campos)
# ============================================================================


class TestSerializacionRoundTrip:
    """`serializar_extraccion` -> `json.dumps` -> `json.loads` ->
    `deserializar_extraccion` tiene que devolver, para `ui/ficha.py`
    (todavia sin construir), exactamente lo que el pipeline propuso: el
    conflicto CON sus dos recortes (Trampa 2, UMBRO/RAMI JALAB) y el caso
    `recorte=None`/`evidencia=None` (talla, que este producto no
    fotografia)."""

    def _resultado_con_conflicto(self, tmp_path, monkeypatch):
        foto_pecho = _foto_sintetica(tmp_path / "IMG_pecho.jpg")
        foto_cuello = _foto_sintetica(tmp_path / "IMG_cuello.jpg")
        region_pecho = RegionOCR(fichero=foto_pecho.name, bbox=(10, 10, 50, 20), texto_ocr="UMBRO", score=0.8)
        region_cuello = RegionOCR(fichero=foto_cuello.name, bbox=(20, 20, 60, 25), texto_ocr="RAMI SALAB", score=0.7)
        regiones = {foto_pecho.name: [region_pecho], foto_cuello.name: [region_cuello]}
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if fichero == foto_pecho.name:
                datos = _respuesta_crop_simple("marca", True, "UMBRO", ubicacion="estampado_o_grafico")
            else:
                datos = _respuesta_crop_simple("marca", True, "RAMI JALAB", ubicacion="etiqueta_interior")
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        return extractor.extraer_producto([foto_pecho, foto_cuello])

    def test_conflicto_sobrevive_el_round_trip_con_sus_dos_recortes(self, tmp_path, monkeypatch):
        resultado = self._resultado_con_conflicto(tmp_path, monkeypatch)

        serializado = serializar_extraccion(resultado)
        tras_json = json.loads(json.dumps(serializado))  # simula el TEXT de productos.campos
        recuperado = deserializar_extraccion(tras_json)

        marca = recuperado["campos"]["marca"]
        assert marca["valor"] is None
        assert marca["fuente"] == "inferido"
        assert marca["confianza"] == "baja"
        assert marca["evidencia"] is None

        propuesta_marca = marca["propuesta"]
        assert propuesta_marca["valor"] is None
        assert propuesta_marca["recorte"] is None
        assert len(propuesta_marca["alternativas"]) == 2

        originales_por_valor = {c.valor: c for c in resultado.propuestas["marca"].alternativas}
        for candidato in propuesta_marca["alternativas"]:
            assert candidato["valor"] in originales_por_valor
            original = originales_por_valor[candidato["valor"]]
            assert isinstance(candidato["recorte"], Path)
            assert candidato["recorte"] == original.recorte
            assert candidato["recorte"].exists()
            assert candidato["evidencia"]["fichero"] == original.evidencia.fichero
            assert candidato["evidencia"]["bbox"] == original.evidencia.bbox
            assert len(candidato["lecturas"]) >= 1
            assert candidato["lecturas"][0]["origen"] in ("vlm", "ocr")

        assert recuperado["coste_usd"] == resultado.coste_usd
        assert recuperado["fallos"] == list(resultado.fallos)
        assert recuperado["aviso_coherencia"] == resultado.aviso_coherencia

    def test_recorte_none_evidencia_none_y_tuplas_vacias_sobreviven(self, tmp_path, monkeypatch):
        resultado = self._resultado_con_conflicto(tmp_path, monkeypatch)

        serializado = serializar_extraccion(resultado)
        recuperado = deserializar_extraccion(json.loads(json.dumps(serializado)))

        # talla: ninguna foto de este producto trae talla -- NO_FOTOGRAFIADO,
        # mismo patron (recorte=None, evidencia=None, tuplas vacias).
        talla = recuperado["campos"]["talla"]
        assert talla["valor"] is None
        assert talla["evidencia"] is None
        propuesta_talla = talla["propuesta"]
        assert propuesta_talla["recorte"] is None
        assert propuesta_talla["alternativas"] == []
        assert propuesta_talla["lecturas"] == []


# ============================================================================
# LA SINTESIS COMPROMETIDA (2026-07-15, pivote de producto decidido por
# Diego): "null -> mejor-intento comprometido". `ExtractorEngine.
# _sintetizar_ficha` es GAP-FILLER -- solo pisa un campo si la agregacion
# vieja lo dejo en `None` (conflicto/ilegible/sin foto/divergencia de
# color); nunca sustituye una lectura ya solida de una etiqueta a
# resolucion nativa. Estos tests EJECUTAN ese camino end-to-end via
# `extraer_producto` con `_MotorFake` -- nunca tocan la red ni `anthropic`.
# ============================================================================


class TestSintesisComprometida:
    def test_sintesis_elige_entre_cuatro_candidatos_en_conflicto(self, tmp_path, monkeypatch):
        """La sintesis elige 'lufthous' entre 4 candidatos de marca en
        conflicto (lufthous / Laser Rodilla / Intel Core / LLLT-200): el
        campo se publica con ese valor, las OTRAS tres candidatas se
        conservan en `alternativas` (con su propio recorte, NO se pierden),
        y `fuente`/`evidencia` son correctos porque `visible_en_foto=true`
        y `de_texto_detectado` casa con un candidato en `etiqueta_interior`
        (ubicacion publicable para marca)."""
        textos = ["lufthous", "Laser Rodilla", "Intel Core", "LLLT-200"]
        fotos = [_foto_sintetica(tmp_path / f"IMG_{i}.jpg") for i in range(len(textos))]
        textos_por_foto = {foto.name: texto for foto, texto in zip(fotos, textos)}
        regiones = {
            foto.name: [RegionOCR(fichero=foto.name, bbox=(5, 5, 20, 10), texto_ocr=texto, score=0.5)]
            for foto, texto in zip(fotos, textos)
        }
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        respuesta_sintesis = _respuesta_sintesis(
            marca={
                "valor": "lufthous",
                "visible_en_foto": True,
                "de_texto_detectado": "lufthous",
                "confianza": "media",
            },
        )
        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if version_prompt == VERSION_PROMPT_SINTESIS:
                return ResultadoLLM(
                    datos=respuesta_sintesis, fuente="api", coste_usd=0.002, tokens_entrada=200, tokens_salida=80,
                )
            # VERSION_PROMPT_CROP: una marca distinta por fichero.
            datos = _respuesta_crop_simple("marca", True, textos_por_foto[fichero])
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto(fotos)

        assert resultado.campos["marca"].valor == "lufthous"
        assert resultado.campos["marca"].fuente == "foto"  # visible_en_foto=True + candidato en etiqueta_interior
        assert resultado.campos["marca"].confianza == "media"
        assert resultado.campos["marca"].evidencia is not None

        propuesta = resultado.propuestas["marca"]
        assert propuesta.valor == "lufthous"
        alternativas = {c.valor for c in propuesta.alternativas}
        assert alternativas == {"Laser Rodilla", "Intel Core", "LLLT-200"}
        assert "lufthous" not in alternativas  # el elegido no se repite como alternativa
        for candidato in propuesta.alternativas:
            assert candidato.recorte is not None
            assert candidato.recorte.exists()

    def test_visible_en_foto_false_da_fuente_inferido_sin_evidencia_y_confianza_nunca_alta(
        self, tmp_path, monkeypatch
    ):
        """GAP: color diverge entre 3 fotos (rosa/rojo vino/rosa, el caso
        real del producto 6) -> `campos["color"].valor` queda en `None`
        (ver `test_color_divergente_es_none_nunca_la_primera_lectura`). La
        sintesis rellena ese hueco con `visible_en_foto=false` (una
        inferencia, no una lectura) -> `fuente="inferido"`, SIN evidencia,
        y `confianza` NUNCA "alta" (se fuerza a "baja" aunque el modelo
        proponga "media")."""
        rosa = (232, 160, 180)
        rojo_vino = (110, 20, 40)
        fotos = [
            _foto_de_color(tmp_path / "IMG_c1.jpg", rosa),
            _foto_de_color(tmp_path / "IMG_c2.jpg", rojo_vino),
            _foto_de_color(tmp_path / "IMG_c3.jpg", rosa),
        ]
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            color={
                "valor": "granate",
                "visible_en_foto": False,
                "de_texto_detectado": None,
                "confianza": "media",  # el modelo propone "media"...
            },
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto(fotos)

        assert resultado.campos["color"].valor == "granate"
        assert resultado.campos["color"].fuente == "inferido"
        assert resultado.campos["color"].evidencia is None
        assert resultado.campos["color"].confianza == "baja"  # ... pero se FUERZA a "baja"
        assert resultado.campos["color"].confianza != "alta"

    def test_titulo_y_descripcion_se_publican_siempre_como_campos_fuente_inferido(self, tmp_path, monkeypatch):
        """`titulo`/`descripcion` son campos NUEVOS (no existian antes de
        la sintesis): siempre se toman de ella, sin gap-filler (no hay
        nada previo que preservar), y viajan como cualquier otro `Campo`/
        `Propuesta` de la ficha."""
        foto = _foto_sintetica(tmp_path / "IMG_td.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            titulo="Sudadera gris talla M, buen estado",
            descripcion="Sudadera gris de segunda mano, sin manchas visibles. Revisar antes de publicar.",
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["titulo"].valor == "Sudadera gris talla M, buen estado"
        assert resultado.campos["titulo"].fuente == "inferido"
        assert resultado.campos["titulo"].confianza == "baja"
        assert resultado.campos["descripcion"].valor.startswith("Sudadera gris de segunda mano")
        assert resultado.campos["descripcion"].fuente == "inferido"
        assert resultado.propuestas["titulo"].motivo == "borrador del modelo, editalo"
        assert resultado.propuestas["titulo"].recorte is None
        assert resultado.propuestas["descripcion"].recorte is None

    def test_umbro_rami_jalab_la_sintesis_elige_una_la_otra_sigue_en_alternativas(self, tmp_path, monkeypatch):
        """El caso real del golden set (Trampa 2, `[INC-012]`): 'UMBRO'
        bordado (estampado_o_grafico) y 'RAMI JALAB' en la etiqueta de
        cuello (etiqueta_interior) -- ANTES de la sintesis esto era
        SIEMPRE `None` + ambas en alternativas (la unica salida "correcta"
        de la ley vieja). Ahora la sintesis elige una (aqui, la de la
        etiqueta -- la ubicacion publicable) y la OTRA se conserva en
        `alternativas` CON SU RECORTE: no se pierde."""
        foto_pecho = _foto_sintetica(tmp_path / "IMG_pecho.jpg")
        foto_cuello = _foto_sintetica(tmp_path / "IMG_cuello.jpg")
        region_pecho = RegionOCR(fichero=foto_pecho.name, bbox=(10, 10, 50, 20), texto_ocr="UMBRO", score=0.8)
        region_cuello = RegionOCR(fichero=foto_cuello.name, bbox=(20, 20, 60, 25), texto_ocr="RAMI SALAB", score=0.7)
        regiones = {foto_pecho.name: [region_pecho], foto_cuello.name: [region_cuello]}
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: regiones[ruta.name])

        respuesta_sintesis = _respuesta_sintesis(
            marca={
                "valor": "RAMI JALAB",
                "visible_en_foto": True,
                "de_texto_detectado": "RAMI JALAB",
                "confianza": "baja",
            },
        )
        motor = _MotorFake()

        def _consultar(imagenes, prompt, json_schema, version_prompt="v1", producto_id=None):
            fichero = imagenes[0].fichero
            motor.llamadas.append((fichero, version_prompt))
            if version_prompt == VERSION_PROMPT_SINTESIS:
                return ResultadoLLM(
                    datos=respuesta_sintesis, fuente="api", coste_usd=0.002, tokens_entrada=200, tokens_salida=80,
                )
            if fichero == foto_pecho.name:
                datos = _respuesta_crop_simple("marca", True, "UMBRO", ubicacion="estampado_o_grafico")
            else:
                datos = _respuesta_crop_simple("marca", True, "RAMI JALAB", ubicacion="etiqueta_interior")
            return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.001, tokens_entrada=100, tokens_salida=10)

        motor.consultar = _consultar
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto_pecho, foto_cuello])

        assert resultado.campos["marca"].valor == "RAMI JALAB"
        assert resultado.campos["marca"].fuente == "foto"
        propuesta = resultado.propuestas["marca"]
        assert len(propuesta.alternativas) == 1
        assert propuesta.alternativas[0].valor == "UMBRO"
        assert propuesta.alternativas[0].recorte is not None
        assert propuesta.alternativas[0].recorte.exists()

    def test_visible_en_foto_true_pero_ubicacion_no_publicable_se_degrada_a_inferido(self, tmp_path, monkeypatch):
        """Regla dura #1 (estampado != marca EN LA PUBLICACION) se aplica
        TAMBIEN al gap-filler de la sintesis: si `de_texto_detectado` casa
        con un candidato de `estampado_o_grafico`, NUNCA se publica como
        `fuente="foto"` para 'marca' aunque la sintesis diga
        `visible_en_foto=true` -- se degrada a 'inferido', pero el recorte
        SI se ensena (de contexto)."""
        foto = _foto_sintetica(tmp_path / "IMG_estampado.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="ORIGINALS", score=0.8)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple(
            "marca", True, "ORIGINALS", ubicacion="estampado_o_grafico"
        )
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            marca={
                "valor": "ORIGINALS",
                "visible_en_foto": True,  # el modelo AFIRMA verlo -- no basta
                "de_texto_detectado": "ORIGINALS",
                "confianza": "media",
            },
        )
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "ORIGINALS"
        assert resultado.campos["marca"].fuente == "inferido"  # degradado -- regla dura #1
        assert resultado.campos["marca"].evidencia is None
        assert resultado.campos["marca"].confianza == "baja"
        assert resultado.propuestas["marca"].recorte is not None  # recorte de CONTEXTO, se ensena igual

    def test_gap_filler_no_pisa_un_valor_ya_solido(self, tmp_path, monkeypatch):
        """Si la agregacion vieja YA resolvio 'marca' de forma solida (un
        unico valor legible en etiqueta_interior), la sintesis NUNCA la
        pisa aunque proponga otra cosa distinta -- gap-filler, no
        reemplazo ciego."""
        foto = _foto_sintetica(tmp_path / "IMG_solida.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("marca", True, "Reebok")
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            marca={
                "valor": "Otra Marca Cualquiera",
                "visible_en_foto": True,
                "de_texto_detectado": "Reebok",
                "confianza": "media",
            },
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["marca"].valor == "Reebok"  # la sintesis NO la piso

    def test_fallo_de_la_sintesis_no_toca_ningun_campo(self, tmp_path, monkeypatch):
        """`decision-making.md` SS13: nunca fallback silencioso. Si la
        llamada de sintesis revienta, se loguea + se anota en `fallos` y
        NINGUN campo cambia -- el producto se queda exactamente con lo que
        la agregacion vieja ya tenia."""
        foto = _foto_sintetica(tmp_path / "IMG_falla.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("marca", True, "Reebok")
        motor.excepciones[VERSION_PROMPT_SINTESIS] = LLMLlamadaFallidaError("limite de tasa excedido")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        # la agregacion vieja ya resolvio 'marca' solidamente -- el fallo de
        # la sintesis no la toca (nunca fallback silencioso: el fallo
        # tecnico queda escrito en `fallos`, nunca disfrazado de "no legible").
        assert resultado.campos["marca"].valor == "Reebok"
        assert any("vlm_sintesis" in f for f in resultado.fallos)
        # la excepcion corta ANTES del bucle de titulo/descripcion -- ni
        # siquiera se anaden esas claves (nunca un valor a medias).
        assert "titulo" not in resultado.campos

    def test_construir_solicitudes_incluye_la_solicitud_de_sintesis(self, tmp_path, monkeypatch):
        """`change-loop.md` SS C5: si esta llamada no se contara en
        `construir_solicitudes`, el coste estimado quedaria por debajo del
        real."""
        foto = _foto_sintetica(tmp_path / "IMG_est.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="Raabdk", score=0.7)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        extractor = ExtractorEngine(motor)
        solicitudes = extractor.construir_solicitudes([foto])

        assert motor.llamadas == []  # construir_solicitudes NUNCA llama de verdad
        version_prompts = [vp for _, _, vp in solicitudes]
        assert version_prompts.count(VERSION_PROMPT_SINTESIS) == 1

        imagenes_sintesis, prompt_sintesis, _ = next(s for s in solicitudes if s[2] == VERSION_PROMPT_SINTESIS)
        assert len(imagenes_sintesis) == 1  # una sola foto en este producto
        assert imagenes_sintesis[0].fichero == foto.name
        assert isinstance(prompt_sintesis, str) and len(prompt_sintesis) > 0


# ============================================================================
# `categoria` (Fase 3, 2026-07-17): ENUM CERRADO, SIEMPRE fuente="inferido"
# -- NUNCA "foto" (una categoria no es texto legible en un pixel). Misma
# llamada de sintesis, 0 llamadas extra. Regla dura: fuera del enum ->
# la clave "categoria" NO se anade a `campos` (nunca "otros" como comodin
# silencioso, decision-making.md SS13).
# ============================================================================


class TestSintesisCategoria:
    def test_categoria_valida_se_publica_inferido_baja(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_cat.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(categoria="moda")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["categoria"].valor == "moda"
        assert resultado.campos["categoria"].fuente == "inferido"
        assert resultado.campos["categoria"].confianza == "baja"
        assert resultado.campos["categoria"].evidencia is None
        assert resultado.propuestas["categoria"].motivo == "clasificacion del modelo, confirmala"
        assert resultado.propuestas["categoria"].recorte is None

    def test_categoria_fuera_del_enum_no_se_anade_a_campos(self, tmp_path, monkeypatch):
        """Ninguna categoria plausible-pero-inventada ("vehiculos", que ni
        siquiera existe en `CATEGORIAS`) puede colarse como si fuera un
        dato real -- la clave se queda AUSENTE, y el fallo TECNICO queda
        anotado (nunca se disfraza de "no legible")."""
        foto = _foto_sintetica(tmp_path / "IMG_cat_mal.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(categoria="vehiculos")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "categoria" not in resultado.campos
        assert "categoria" not in resultado.propuestas
        assert any("categoria" in f for f in resultado.fallos)

    def test_categoria_fuera_del_enum_no_toca_otros_campos(self, tmp_path, monkeypatch):
        """Un `categoria` invalido NO puede abortar el resto de la
        sintesis (marca/talla/titulo/descripcion siguen publicandose):
        eso seria justo el fallo que exige NO validar el enum dentro de
        `_parsear_respuesta_sintesis` (ver su docstring)."""
        foto = _foto_sintetica(tmp_path / "IMG_cat_mal2.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            categoria="no_es_una_categoria_real",
            titulo="Titulo de prueba",
            descripcion="Descripcion de prueba",
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "categoria" not in resultado.campos
        assert resultado.campos["titulo"].valor == "Titulo de prueba"
        assert resultado.campos["descripcion"].valor == "Descripcion de prueba"


class TestConstruirCampoCategoriaDesdeSintesis:
    """Unidad directa de `_construir_campo_categoria_desde_sintesis` --
    sin pasar por una llamada VLM ni por `extraer_producto`."""

    @pytest.mark.parametrize("valor", ["moda", "electronica", "hogar", "libros", "otros"])
    def test_las_cinco_categorias_validas_construyen_campo_inferido_baja(self, valor):
        campo = _construir_campo_categoria_desde_sintesis(valor)
        assert campo is not None
        assert campo.valor == valor
        assert campo.fuente == "inferido"
        assert campo.confianza == "baja"
        assert campo.evidencia is None

    @pytest.mark.parametrize("valor", [None, "", "Moda", "ropa", "vehiculos", 42, ["moda"]])
    def test_valores_invalidos_devuelven_none_nunca_un_campo_inventado(self, valor):
        assert _construir_campo_categoria_desde_sintesis(valor) is None


# ============================================================================
# `tipo` (2026-07-21, decision de Diego): que ES el producto ("masajeador
# de rodilla", "sudadera") -- TEXTO LIBRE (sin enum, a diferencia de
# categoria/estado), SIEMPRE fuente="inferido" (juicio sobre el producto
# entero, JAMAS un texto legible en un pixel -- sin visible_en_foto/
# de_texto_detectado en el json_schema, asi que estructuralmente NO HAY
# camino a fuente="foto" para este campo). Misma llamada de sintesis, 0
# llamadas extra. Regla dura: sin opinion (valor=None/""/solo-espacios) ->
# la clave "tipo" NO se anade a `campos` (ausente, nunca un comodin) -- y
# a diferencia de "categoria", esto NO es un fallo tecnico (no hay enum
# que violar), asi que NO se anota en `fallos`.
# ============================================================================


class TestSintesisTipo:
    def test_tipo_valido_se_publica_inferido_con_su_confianza(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_tipo.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            tipo={"valor": "masajeador de rodilla", "confianza": "media"},
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["tipo"].valor == "masajeador de rodilla"
        assert resultado.campos["tipo"].fuente == "inferido"
        assert resultado.campos["tipo"].confianza == "media"
        assert resultado.campos["tipo"].evidencia is None
        assert resultado.propuestas["tipo"].motivo == "que es el producto (inferido por la sintesis, confirmalo)"
        assert resultado.propuestas["tipo"].recorte is None

    @pytest.mark.parametrize("valor_crudo", [None, "", "   "])
    def test_sin_opinion_la_clave_tipo_queda_ausente_y_no_es_un_fallo(self, tmp_path, monkeypatch, valor_crudo):
        """A diferencia de `categoria` (donde violar el enum SI anota un
        fallo), aqui "sin opinion" no es un fallo tecnico -- no hay enum
        que violar, la sintesis simplemente no tuvo nada que proponer."""
        foto = _foto_sintetica(tmp_path / "IMG_tipo_vacio.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            tipo={"valor": valor_crudo, "confianza": "baja"},
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "tipo" not in resultado.campos
        assert "tipo" not in resultado.propuestas
        assert not any("tipo" in f for f in resultado.fallos)

    def test_tipo_fuente_nunca_es_foto_pase_lo_que_pase(self, tmp_path, monkeypatch):
        """Estructuralmente imposible: el json_schema de `tipo` no tiene
        `visible_en_foto`/`de_texto_detectado`, asi que no hay ningun
        camino en el codigo que pueda construir un `Campo` de `tipo` con
        `fuente="foto"` -- se demuestra con la senal mas favorable posible
        (confianza "media", un texto que hasta podria parecer una cita)."""
        foto = _foto_sintetica(tmp_path / "IMG_tipo_foto.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="sudadera", score=0.9)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("otro", True, "sudadera")
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            tipo={"valor": "sudadera", "confianza": "media"},
        )
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["tipo"].fuente == "inferido"
        assert resultado.campos["tipo"].fuente != "foto"
        assert resultado.campos["tipo"].confianza != "alta"

    def test_tipo_no_toca_otros_campos_ni_bloquea_titulo_descripcion(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_tipo_resto.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            tipo={"valor": None, "confianza": "baja"},
            titulo="Titulo de prueba",
            descripcion="Descripcion de prueba",
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "tipo" not in resultado.campos
        assert resultado.campos["titulo"].valor == "Titulo de prueba"
        assert resultado.campos["descripcion"].valor == "Descripcion de prueba"

    def test_tipo_sobrevive_el_round_trip_de_serializacion(self, tmp_path, monkeypatch):
        """Round-trip (`core/store.py` guarda `campos` como JSON): `tipo`
        se serializa/deserializa como cualquier otro `Campo` normal --
        `serializar_extraccion`/`deserializar_extraccion` operan sobre
        `dict[str, Campo]` generico, sin caso especial por nombre de
        campo, asi que un campo NUEVO no necesita tocar esas funciones."""
        foto = _foto_sintetica(tmp_path / "IMG_tipo_rt.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            tipo={"valor": "jersey", "confianza": "media"},
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        serializado = serializar_extraccion(resultado)
        recuperado = deserializar_extraccion(json.loads(json.dumps(serializado)))

        tipo = recuperado["campos"]["tipo"]
        assert tipo["valor"] == "jersey"
        assert tipo["fuente"] == "inferido"
        assert tipo["confianza"] == "media"
        assert tipo["evidencia"] is None


class TestConstruirCampoTipoDesdeSintesis:
    """Unidad directa de `_construir_campo_tipo_desde_sintesis` -- sin
    pasar por una llamada VLM ni por `extraer_producto`."""

    @pytest.mark.parametrize("confianza", ["media", "baja"])
    def test_valor_no_vacio_construye_campo_inferido(self, confianza):
        campo = _construir_campo_tipo_desde_sintesis({"valor": "camiseta", "confianza": confianza})
        assert campo is not None
        assert campo.valor == "camiseta"
        assert campo.fuente == "inferido"
        assert campo.confianza == confianza
        assert campo.confianza != "alta"
        assert campo.evidencia is None

    @pytest.mark.parametrize("valor", [None, "", "   "])
    def test_valores_vacios_devuelven_none_nunca_un_comodin(self, valor):
        assert _construir_campo_tipo_desde_sintesis({"valor": valor, "confianza": "baja"}) is None

    def test_valor_con_espacios_se_recorta(self):
        campo = _construir_campo_tipo_desde_sintesis({"valor": "  electroestimulador  ", "confianza": "baja"})
        assert campo is not None
        assert campo.valor == "electroestimulador"

    @pytest.mark.parametrize("confianza_invalida", ["alta", "altisima", None, "", "media "])
    def test_confianza_fuera_de_media_baja_se_acota_a_baja_en_el_constructor(self, confianza_invalida):
        """§17 / defensa en profundidad (hallazgo del listing-audit 2026-07-21):
        la garantia "nunca alta" la fuerza un `if` EN ESTA funcion, no solo el
        parseo aguas arriba. Aunque a este constructor le llegue un dict que NO
        paso por `_parsear_respuesta_sintesis` (con confianza="alta"), el `Campo`
        sale acotado a "baja" -- "alta" es estructuralmente inalcanzable para
        `tipo` desde CUALQUIER camino, no solo el del flujo real."""
        campo = _construir_campo_tipo_desde_sintesis({"valor": "sudadera Nike", "confianza": confianza_invalida})
        assert campo is not None
        assert campo.confianza == "baja"
        assert campo.confianza != "alta"


# ============================================================================
# `genero` (2026-07-21, fast-follow del `tipo`): ESPEJO EXACTO de `categoria`
# -- ENUM CERRADO, SIEMPRE fuente="inferido", NUNCA "foto". UNICA diferencia:
# su enum es NULLABLE ("no aplica" para no-ropa) -- por eso tiene DOS caminos
# distintos hacia la MISMA clave AUSENTE de `campos`: `valor=None` (no
# aplica, NUNCA un fallo) vs un string fuera de `GENEROS` (violacion de
# enum, SI un fallo, igual que `categoria`).
# ============================================================================


class TestSintesisGenero:
    def test_genero_valido_se_publica_inferido_baja(self, tmp_path, monkeypatch):
        foto = _foto_sintetica(tmp_path / "IMG_genero.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(genero="mujer")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["genero"].valor == "mujer"
        assert resultado.campos["genero"].fuente == "inferido"
        assert resultado.campos["genero"].confianza == "baja"
        assert resultado.campos["genero"].evidencia is None
        assert resultado.propuestas["genero"].motivo == "genero del producto (inferido), confirmalo"
        assert resultado.propuestas["genero"].recorte is None

    def test_genero_null_no_aplica_clave_ausente_y_no_es_un_fallo(self, tmp_path, monkeypatch):
        """`valor=None` es un caso NORMAL (el producto no es ropa/moda,
        p.ej. un masajeador) -- a diferencia de una violacion de enum,
        NUNCA se anota en `fallos`."""
        foto = _foto_sintetica(tmp_path / "IMG_genero_null.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(genero=None)
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "genero" not in resultado.campos
        assert "genero" not in resultado.propuestas
        assert not any("genero" in f for f in resultado.fallos)

    def test_genero_fuera_del_enum_no_se_anade_a_campos_y_anota_fallo(self, tmp_path, monkeypatch):
        """Un genero plausible-pero-inventado ("masculino"/"otro", que ni
        siquiera existe en `GENEROS`) no puede colarse como si fuera un
        dato real -- la clave se queda AUSENTE, y el fallo TECNICO queda
        anotado (mismo criterio que `categoria` fuera de enum)."""
        foto = _foto_sintetica(tmp_path / "IMG_genero_mal.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(genero="masculino")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "genero" not in resultado.campos
        assert "genero" not in resultado.propuestas
        assert any("sintesis_genero_fuera_de_enum" in f for f in resultado.fallos)

    def test_genero_fuera_del_enum_no_toca_otros_campos(self, tmp_path, monkeypatch):
        """Un `genero` invalido NO puede abortar el resto de la sintesis
        (categoria/titulo/descripcion siguen publicandose)."""
        foto = _foto_sintetica(tmp_path / "IMG_genero_mal2.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(
            genero="otro",
            categoria="moda",
            titulo="Titulo de prueba",
            descripcion="Descripcion de prueba",
        )
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        assert "genero" not in resultado.campos
        assert resultado.campos["categoria"].valor == "moda"
        assert resultado.campos["titulo"].valor == "Titulo de prueba"
        assert resultado.campos["descripcion"].valor == "Descripcion de prueba"

    def test_genero_fuente_nunca_es_foto_pase_lo_que_pase(self, tmp_path, monkeypatch):
        """Estructuralmente imposible: el json_schema de `genero` no tiene
        `visible_en_foto`/`de_texto_detectado`, asi que no hay ningun
        camino en el codigo que pueda construir un `Campo` de `genero` con
        `fuente="foto"` -- se demuestra con la senal mas favorable posible
        (un texto detectado que hasta podria parecer una cita: "MUJER")."""
        foto = _foto_sintetica(tmp_path / "IMG_genero_foto.jpg")
        region = RegionOCR(fichero=foto.name, bbox=(10, 10, 40, 20), texto_ocr="MUJER", score=0.9)
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [region])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_CROP] = _respuesta_crop_simple("otro", True, "MUJER")
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(genero="mujer")
        extractor = ExtractorEngine(motor, carpeta_crops=tmp_path / "crops")
        resultado = extractor.extraer_producto([foto])

        assert resultado.campos["genero"].fuente == "inferido"
        assert resultado.campos["genero"].fuente != "foto"
        assert resultado.campos["genero"].confianza != "alta"

    def test_genero_sobrevive_el_round_trip_de_serializacion(self, tmp_path, monkeypatch):
        """Round-trip (`core/store.py` guarda `campos` como JSON): `genero`
        se serializa/deserializa como cualquier otro `Campo` normal --
        `serializar_extraccion`/`deserializar_extraccion` operan sobre
        `dict[str, Campo]` generico, sin caso especial por nombre de
        campo, asi que un campo NUEVO no necesita tocar esas funciones."""
        foto = _foto_sintetica(tmp_path / "IMG_genero_rt.jpg")
        monkeypatch.setattr("core.extract.localizar_regiones_ocr", lambda ruta: [])

        motor = _MotorFake()
        motor.respuestas[VERSION_PROMPT_SINTESIS] = _respuesta_sintesis(genero="niño")
        extractor = ExtractorEngine(motor)
        resultado = extractor.extraer_producto([foto])

        serializado = serializar_extraccion(resultado)
        recuperado = deserializar_extraccion(json.loads(json.dumps(serializado)))

        genero = recuperado["campos"]["genero"]
        assert genero["valor"] == "niño"
        assert genero["fuente"] == "inferido"
        assert genero["confianza"] == "baja"
        assert genero["evidencia"] is None


class TestConstruirCampoGeneroDesdeSintesis:
    """Unidad directa de `_construir_campo_genero_desde_sintesis` -- sin
    pasar por una llamada VLM ni por `extraer_producto`."""

    @pytest.mark.parametrize("valor", ["hombre", "mujer", "niño", "niña", "unisex"])
    def test_los_cinco_generos_validos_construyen_campo_inferido_baja(self, valor):
        campo = _construir_campo_genero_desde_sintesis(valor)
        assert campo is not None
        assert campo.valor == valor
        assert campo.fuente == "inferido"
        assert campo.confianza == "baja"
        assert campo.evidencia is None

    def test_none_devuelve_none_no_aplica_no_es_un_valor_invalido(self):
        """`None` es el caso "no aplica" (no-ropa) -- mismo `None` de
        retorno que un valor invalido, pero el MOTIVO es distinto y quien
        llama (`_sintetizar_ficha`) es quien distingue los dos para
        decidir si anota `fallos` (ver el cableado en `core/extract.py`)."""
        assert _construir_campo_genero_desde_sintesis(None) is None

    @pytest.mark.parametrize("valor", ["", "Mujer", "hombre ", "masculino", "femenino", "otro", 42, ["mujer"]])
    def test_valores_invalidos_devuelven_none_nunca_un_campo_inventado(self, valor):
        assert _construir_campo_genero_desde_sintesis(valor) is None


def test_sintesis_fuente_foto_exige_que_el_valor_este_en_el_pixel():
    """Hallazgo BLOQUEANTE del listing-audit: `fuente="foto"` solo si el
    VALOR propuesto esta CONTENIDO en el texto legible del candidato citado.
    Extender una lectura real ("Reebok" -> "Reebok Classic 100% algodon") o
    sustituirla ("Nike" citando un crop de "Reebok") NO es legible en ese
    pixel -> se degrada a `inferido`. Antes el codigo ligaba el recorte a la
    cita pero no comprobaba el valor (truth-loop.md SS A.1)."""
    from core.extract import (
        _UBICACIONES_VALIDAS_MARCA,
        LecturaCrop,
        _construir_campo_desde_sintesis,
    )

    cand = LecturaCrop(
        fichero="IMG.jpg", bbox=(1, 2, 3, 4), legible=True, pertenece_al_producto=True,
        ubicacion="etiqueta_interior", contenido_probable="marca", texto="Reebok",
    )
    indice = {"reebok": cand}

    def decision(valor):
        return {"valor": valor, "visible_en_foto": True, "de_texto_detectado": "Reebok", "confianza": "media"}

    # valor == texto legible -> foto (caso legitimo)
    campo, _ = _construir_campo_desde_sintesis(decision("Reebok"), indice, _UBICACIONES_VALIDAS_MARCA)
    assert campo.fuente == "foto" and campo.valor == "Reebok"

    # valor EXTIENDE la lectura -> inferido (la parte extra no es legible)
    campo, _ = _construir_campo_desde_sintesis(
        decision("Reebok Classic 100% algodon"), indice, _UBICACIONES_VALIDAS_MARCA
    )
    assert campo.fuente == "inferido", "un valor extendido no puede salir como fuente=foto"

    # valor SUSTITUYE la lectura -> inferido (el crop dice Reebok, el valor Nike)
    campo, _ = _construir_campo_desde_sintesis(decision("Nike"), indice, _UBICACIONES_VALIDAS_MARCA)
    assert campo.fuente == "inferido", "un valor que no esta en el crop no es fuente=foto"


# ============================================================================
# REDACCION DESDE CAMPOS CONFIRMADOS (2026-07-17, fix del bug de Diego:
# "la descripcion no menciona la CREMALLERA ROTA" -- ver el docstring de la
# seccion en core/extract.py). CERO fotos, CERO propuestas del modelo: el
# UNICO input son los valores YA CONFIRMADOS. Estos tests corren SIN
# ANTHROPIC_API_KEY y SIN tocar la red -- `_MotorTextoFake` sustituye a
# `LLMEngine` (duck-typing de `consultar_texto`, igual que `_MotorFake`
# sustituye a `consultar` mas arriba).
# ============================================================================
class _MotorTextoFake:
    """Solo implementa `consultar_texto` -- el contrato exacto que necesita
    `redactar_desde_campos_confirmados`. Guarda cada prompt real recibido
    para poder comprobar QUE se le mando (verificacion anti-marca-ajena:
    que "Umbro" no aparezca cuando Diego confirmo "Reebok")."""

    def __init__(self, titulo: str = "Titulo generado", descripcion: str = "Descripcion generada") -> None:
        self.titulo = titulo
        self.descripcion = descripcion
        self.llamadas: list[dict] = []
        self.excepcion: Exception | None = None
        self.respuesta_cruda: dict | None = None  # si se fija, se devuelve TAL CUAL (para probar respuestas incompletas)

    def consultar_texto(self, prompt, json_schema, version_prompt="v1", producto_id=None):
        self.llamadas.append(
            {"prompt": prompt, "json_schema": json_schema, "version_prompt": version_prompt, "producto_id": producto_id}
        )
        if self.excepcion is not None:
            raise self.excepcion
        datos = self.respuesta_cruda if self.respuesta_cruda is not None else {
            "titulo": self.titulo, "descripcion": self.descripcion,
        }
        return ResultadoLLM(datos=datos, fuente="api", coste_usd=0.00005, tokens_entrada=120, tokens_salida=60)


class TestConsultarTextoNuncaAceptaImagenes:
    """La garantia anti-marca-ajena NO es una convencion de llamada ("pasa
    imagenes=[]"): es que `LLMEngine.consultar_texto` NO TIENE parametro
    `imagenes` en su firma. Se comprueba contra la clase REAL, no contra
    el fake -- si algun dia alguien le anade el parametro, este test debe
    reventar."""

    def test_firma_no_tiene_parametro_imagenes(self):
        import inspect

        firma = inspect.signature(LLMEngine.consultar_texto)
        assert "imagenes" not in firma.parameters

    def test_consultar_con_imagenes_sigue_exigiendo_al_menos_una(self, tmp_path):
        """El invariante VIEJO de `consultar()` (>=1 imagen) no se relaja
        por la existencia de `consultar_texto` -- son metodos DISTINTOS."""
        motor = LLMEngine(cache_dir=tmp_path / "cache")
        with pytest.raises(ValueError):
            motor.consultar([], "prompt", {"type": "object"})


class TestFormatearCamposParaRedaccion:
    def test_incluye_desperfectos_con_etiqueta_dura(self):
        texto = _formatear_campos_para_redaccion({"marca": "Reebok", "desperfectos": "CREMALLERA ROTA"})
        assert "CREMALLERA ROTA" in texto
        assert "DESPERFECTOS" in texto  # el nombre legible avisa que es obligatorio mencionarlo

    def test_omite_campos_ausentes_o_vacios_nunca_los_inventa(self):
        texto = _formatear_campos_para_redaccion({"marca": "Reebok", "talla": None, "color": "  "})
        assert "Reebok" in texto
        assert "talla" not in texto.lower()
        assert "color" not in texto.lower()

    def test_sin_ningun_campo_confirmado_lo_dice_explicitamente(self):
        texto = _formatear_campos_para_redaccion({})
        assert "aun no ha confirmado" in texto


class TestConstruirPromptRedaccion:
    def test_prompt_contiene_marca_confirmada_y_no_marcas_ajenas(self):
        texto_campos = _formatear_campos_para_redaccion({"marca": "Reebok"})
        prompt = _construir_prompt_redaccion(texto_campos)
        assert "Reebok" in prompt
        assert "Umbro" not in prompt

    def test_prompt_prohibe_inventar_y_exige_desperfectos(self):
        prompt = _construir_prompt_redaccion(_formatear_campos_para_redaccion({}))
        assert "NO inventes" in prompt
        assert "DESPERFECTOS" in prompt


class TestRedactarDesdeCamposConfirmados:
    def test_devuelve_titulo_y_descripcion(self):
        motor = _MotorTextoFake(titulo="Bici azul BH", descripcion="Bici de paseo, buen estado.")
        titulo, descripcion = redactar_desde_campos_confirmados({"marca": "BH"}, motor)
        assert titulo == "Bici azul BH"
        assert descripcion == "Bici de paseo, buen estado."

    def test_input_es_texto_puro_ninguna_imagen_ni_propuesta(self):
        """Verificacion directa (no solo por firma): la llamada real que
        sale de aqui usa `consultar_texto`, que no tiene donde poner una
        imagen -- `_MotorTextoFake` ni siquiera define `consultar()`, asi
        que si este codigo intentara mandar una imagen, reventaria con
        `AttributeError`, no con un fallo silencioso."""
        motor = _MotorTextoFake()
        assert not hasattr(motor, "consultar")  # solo consultar_texto -- por diseno del fake
        redactar_desde_campos_confirmados({"marca": "BH"}, motor)
        assert len(motor.llamadas) == 1

    def test_prompt_real_lleva_marca_corregida_y_desperfectos_nunca_la_marca_vieja(self):
        """Diego corrigio 'Umbro' -> 'Reebok' y confirmo el desperfecto: el
        dict que se le pasa a la funcion YA NO tiene 'Umbro' en absoluto
        (lo sobreescribio quien llama, `ui/ficha.py`) -- se comprueba que
        el prompt REAL enviado al motor reflexa exactamente eso."""
        motor = _MotorTextoFake()
        redactar_desde_campos_confirmados(
            {"marca": "Reebok", "desperfectos": "CREMALLERA ROTA"}, motor, producto_id="p1"
        )
        prompt = motor.llamadas[0]["prompt"]
        assert "Reebok" in prompt
        assert "Umbro" not in prompt
        assert "CREMALLERA ROTA" in prompt
        assert motor.llamadas[0]["producto_id"] == "p1"
        assert motor.llamadas[0]["version_prompt"] == VERSION_PROMPT_REDACCION

    def test_fallo_de_red_se_propaga_nunca_texto_de_repuesto(self):
        motor = _MotorTextoFake()
        motor.excepcion = LLMLlamadaFallidaError("boom")
        with pytest.raises(LLMLlamadaFallidaError):
            redactar_desde_campos_confirmados({"marca": "BH"}, motor)

    def test_respuesta_sin_descripcion_lanza_respuestavlminvalida(self):
        motor = _MotorTextoFake()
        motor.respuesta_cruda = {"titulo": "solo hay titulo"}
        with pytest.raises(RespuestaVLMInvalidaError):
            redactar_desde_campos_confirmados({"marca": "BH"}, motor)

    def test_respuesta_con_titulo_vacio_lanza_respuestavlminvalida(self):
        motor = _MotorTextoFake()
        motor.respuesta_cruda = {"titulo": "   ", "descripcion": "algo"}
        with pytest.raises(RespuestaVLMInvalidaError):
            redactar_desde_campos_confirmados({"marca": "BH"}, motor)

    # ------------------------------------------------------------------
    # `[listing-audit] ALTA, 2026-07-17` (`[INC-010]`, "pedir no es
    # verificar"): la regla 3 del prompt EXIGE mencionar el desperfecto,
    # pero nada comprobaba que el modelo obedeciera. Verificacion
    # DETERMINISTA (solapamiento de palabras), nunca otra llamada al LLM.
    # ------------------------------------------------------------------

    def test_desperfecto_no_mencionado_se_anexa_y_avisa_ruidoso(self, caplog):
        motor = _MotorTextoFake(
            titulo="Sudadera Reebok",
            descripcion="Sudadera en buen estado general, apenas usada.",
        )
        with caplog.at_level(logging.WARNING):
            _, descripcion = redactar_desde_campos_confirmados(
                {"marca": "Reebok", "desperfectos": "CREMALLERA ROTA en la zona delantera"},
                motor,
            )
        # Nunca se persiste una descripcion que omite el desperfecto confirmado.
        assert "CREMALLERA ROTA" in descripcion
        assert any("no mencionaba el desperfecto" in r.message.lower() for r in caplog.records)

    def test_desperfecto_SI_mencionado_pasa_limpio_sin_ruido(self, caplog):
        motor = _MotorTextoFake(
            titulo="Sudadera Reebok",
            descripcion="Sudadera con la cremallera rota en la zona delantera, resto en buen estado.",
        )
        with caplog.at_level(logging.WARNING):
            _, descripcion = redactar_desde_campos_confirmados(
                {"marca": "Reebok", "desperfectos": "CREMALLERA ROTA en la zona delantera"},
                motor,
            )
        assert descripcion == (
            "Sudadera con la cremallera rota en la zona delantera, resto en buen estado."
        )
        assert not any("no mencionaba el desperfecto" in r.message for r in caplog.records)

    def test_sin_desperfecto_confirmado_no_anade_nada(self):
        motor = _MotorTextoFake(titulo="T", descripcion="Descripcion normal sin defectos.")
        _, descripcion = redactar_desde_campos_confirmados({"marca": "BH"}, motor)
        assert descripcion == "Descripcion normal sin defectos."

    # ------------------------------------------------------------------
    # `tipo` lidera el titulo (2026-07-21, Diego midio 1/7 titulos
    # liderando con el tipo confirmado y 2 contradiciendolo). El prompt
    # instruye, el backstop `_asegurar_titulo_lidera_con_tipo` GARANTIZA.
    # ------------------------------------------------------------------

    def test_tipo_confirmado_aparece_en_el_prompt_real(self):
        """`tipo` es un campo mas de `_CAMPOS_REDACCION_ORDEN` -- debe
        llegar al texto real que se manda al motor, igual que `marca` o
        `desperfectos`."""
        motor = _MotorTextoFake()
        redactar_desde_campos_confirmados(
            {"tipo": "Masajeador de rodilla", "marca": "Lufthous"}, motor
        )
        prompt = motor.llamadas[0]["prompt"]
        assert "Masajeador de rodilla" in prompt
        assert "tipo de producto" in prompt.lower()

    def test_titulo_sin_tipo_liderando_se_corrige_con_el_backstop(self):
        """El modelo devuelve un titulo que NO empieza por el tipo -- el
        backstop lo antepone antes de que la funcion lo devuelva."""
        motor = _MotorTextoFake(
            titulo="Lufthous LLLT-200 blanco y gris",
            descripcion="En muy buen estado, apenas usado.",
        )
        titulo, _ = redactar_desde_campos_confirmados(
            {"tipo": "Masajeador de rodilla", "marca": "Lufthous"}, motor
        )
        assert titulo.startswith("Masajeador de rodilla")

    def test_sin_tipo_confirmado_titulo_del_modelo_no_se_toca(self):
        motor = _MotorTextoFake(titulo="Bici azul BH", descripcion="Bici de paseo, buen estado.")
        titulo, _ = redactar_desde_campos_confirmados({"marca": "BH"}, motor)
        assert titulo == "Bici azul BH"


class TestAseguraTituloLideraConTipo:
    """`_asegurar_titulo_lidera_con_tipo` -- el BACKSTOP determinista
    (misma disciplina que `_asegurar_desperfecto_en_descripcion`): nunca
    depende de que el modelo obedezca la regla de FORMATO del prompt."""

    def test_titulo_que_omite_el_tipo_se_prepone_y_lidera(self):
        resultado = _asegurar_titulo_lidera_con_tipo(
            "Lufthous LLLT-200 blanco", "Masajeador de rodilla"
        )
        assert resultado.startswith("Masajeador de rodilla")
        assert "Lufthous LLLT-200 blanco" in resultado

    def test_titulo_que_ya_lidera_con_el_tipo_no_cambia(self):
        titulo = "Sudadera Reebok gris XXL"
        resultado = _asegurar_titulo_lidera_con_tipo(titulo, "Sudadera")
        assert resultado == titulo
        assert resultado.count("Sudadera") == 1  # nunca duplica

    def test_titulo_que_contiene_el_tipo_no_al_principio_no_duplica(self):
        titulo = "Reebok Sudadera gris"
        resultado = _asegurar_titulo_lidera_con_tipo(titulo, "Sudadera")
        assert resultado == titulo
        assert resultado.count("Sudadera") == 1

    def test_tipo_none_o_vacio_no_toca_el_titulo(self):
        titulo = "Sudadera Reebok gris XXL"
        assert _asegurar_titulo_lidera_con_tipo(titulo, None) == titulo
        assert _asegurar_titulo_lidera_con_tipo(titulo, "") == titulo
        assert _asegurar_titulo_lidera_con_tipo(titulo, "   ") == titulo

    def test_prepend_que_excede_el_limite_se_capa_por_palabra_sin_partir(self):
        # Titulo SIN el nucleo del tipo ("masajeador") -> antepone y, al exceder
        # 100, capa por palabra completa.
        titulo_largo = (
            "Lufthous LLLT-200 aparato termico y por vibracion para "
            "rodilla, hombro y espalda, edicion blanco y gris, casi nuevo"
        )
        resultado = _asegurar_titulo_lidera_con_tipo(
            titulo_largo, "Masajeador de rodilla con tecnologia laser", limite=100
        )
        assert len(resultado) <= 100
        assert resultado.startswith("Masajeador de rodilla con tecnologia laser")
        # nunca corta a mitad de palabra: cada palabra del resultado debe
        # aparecer completa en el texto fuente (tipo + titulo original)
        fuente = "Masajeador de rodilla con tecnologia laser " + titulo_largo
        for palabra in resultado.split(" "):
            assert palabra in fuente.split(" ")

    def test_nucleo_del_tipo_enterrado_no_se_duplica_aunque_no_lidere(self):
        # #5: si el nucleo del tipo ("masajeador") ya esta en el titulo aunque no
        # al frente, NO se antepone (duplicaria) -- se deja tal cual. Reordenar
        # palabras es fragil y este modulo no lo hace; el prompt v2 hace que el
        # modelo lidere de entrada, asi que este caso enterrado es raro.
        titulo = "Lufthous LLLT-200 masajeador termico blanco y gris"
        resultado = _asegurar_titulo_lidera_con_tipo(
            titulo, "Masajeador de rodilla con tecnologia laser"
        )
        assert resultado == titulo
        assert resultado.lower().count("masajeador") == 1

    def test_deteccion_insensible_a_acentos_y_mayusculas_no_duplica(self):
        resultado = _asegurar_titulo_lidera_con_tipo("sudadera reebok", "Sudadera")
        assert resultado == "sudadera reebok"
        assert resultado.lower().count("sudadera") == 1

    def test_modelo_abrevia_un_tipo_largo_no_se_duplica(self):
        # listing-audit #5: el modelo lidera con "Masajeador" (nucleo del tipo)
        # abreviando el tipo largo confirmado -> el backstop NO debe anteponer
        # el tipo entero otra vez.
        resultado = _asegurar_titulo_lidera_con_tipo(
            "Masajeador Lufthous LLLT-200 blanco", "Masajeador de rodilla con tecnología láser"
        )
        assert resultado == "Masajeador Lufthous LLLT-200 blanco"
        assert resultado.lower().count("masajeador") == 1

    def test_nucleo_del_tipo_presente_no_duplica_parcialmente(self):
        # listing-audit #5: "Jersey" (nucleo) ya esta -> no "Jersey de cuello vuelto Jersey azul".
        resultado = _asegurar_titulo_lidera_con_tipo("Jersey azul de lana", "Jersey de cuello vuelto")
        assert resultado == "Jersey azul de lana"
        assert resultado.lower().count("jersey") == 1

    def test_plural_del_tipo_sigue_deduplicando(self):
        # El tipo entero como substring cubre el plural: "Sudadera" en "Sudaderas".
        resultado = _asegurar_titulo_lidera_con_tipo("Sudaderas Reebok rosas", "Sudadera")
        assert resultado == "Sudaderas Reebok rosas"


def test_construir_solicitud_redaccion_no_llama_a_nadie():
    """`(prompt, version_prompt)` sin gastar -- para estimar coste sin
    tocar la red, mismo contrato que `construir_solicitudes` para crops."""
    prompt, version_prompt = construir_solicitud_redaccion({"marca": "Reebok", "desperfectos": "CREMALLERA ROTA"})
    assert "Reebok" in prompt
    assert "CREMALLERA ROTA" in prompt
    assert version_prompt == VERSION_PROMPT_REDACCION


# `test_el_papel_manda_sobre_el_metro_en_medidas` / `test_metro_gana_si_
# no_hay_marcador_valido` -- ELIMINADOS (Diego, 2026-07-17, 2a ronda de
# listing-audit): probaban el override "el papel manda sobre el metro"
# para `medidas`, que se QUITO por completo (ver `_evaluar_medidas` en
# `core/extract.py` y `TestMedidasYaNoUsaElMarcador` en este archivo).
# `medidas` vuelve a salir UNICAMENTE del metro -- los tests de ese
# camino en solitario son `TestCasosDeFallo`/los que ejercitan
# `_evaluar_medidas` directamente, mas arriba en este archivo.
