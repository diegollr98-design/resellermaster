"""core/extract.py -- COSTURA 1 aplicada: `ExtractorEngine`.

`.claude/rules/architecture.md` (Costura 1) + `.claude/rules/truth-loop.md` SS A
+ `.claude/rules/product.md` + `docs/seeds/fase-2.md` (decisiones D1/D2/D3).

Esta es LA pieza donde vive el modo de fallo del proyecto entero: que el
pipeline afirme algo falso sobre el producto con total fluidez y Diego lo
publique sin mirar. Todo lo que sigue esta escrito para que la respuesta
por defecto, ante cualquier ambiguedad, sea abstenerse (`valor=None,
confianza="baja"`) -- nunca un valor plausible.

EL DISENO: "el OCR LOCALIZA. El VLM LEE. Diego CIERRA."
------------------------------------------------------------------------
Medido sobre las 33 fotos reales de Diego (`tests/golden/legibilidad.json`):
un humano lee marca 7/7 y talla 5/5; el OCR local (RapidOCR) solo lee marca
3/7 y talla 2/5, y subir la resolucion de entrada NO lo arregla (tipografia
estilizada, bajo contraste). Pero el OCR SI localiza que hay texto y DONDE
esta (su bbox), aunque lo lea mal -- y Haiku 4.5 reescala toda imagen
enviada a 1568px de lado largo, asi que mandarle la foto ENTERA reproduce
el mismo cuello de botella que mata al OCR. Por eso:

    1. RapidOCR (local, gratis) localiza regiones de texto en cada foto.
    2. Regiones cercanas de la MISMA foto se fusionan (una marca partida en
       dos lineas -- "ORIGINAL" + "MARINES" -- no son dos marcas: es una).
    3. Una ristra larga de digitos (>= `UMBRAL_DIGITOS_METRO`) delata el
       METRO -- se descarta, nunca produce un atributo (regla gratis,
       medida en el golden set).
    4. Si el OCR YA lee limpio y sin ambiguedad un patron reconocible (EAN,
       "Model:XXX") con score alto, se acepta DIRECTO -- sin gastar en el
       VLM: ya se tiene el dato, gratis (D2).
    5. Lo que queda se RECORTA (con margen, a resolucion NATIVA, nunca la
       foto entera) y SOLO el recorte se manda al VLM (`core/llm.py`), que
       LEE lo que el OCR no pudo.
    6. Diego CIERRA: un conflicto (dos marcas legibles) no lo resuelve el
       pipeline -- sale `marca=None` + las dos candidatas expuestas
       (`ResultadoExtraccion.conflictos`) para que el decida.

LA LEY DE CORROBORACION (INC-010/INC-011, corregida 2026-07-14)
------------------------------------------------------------------------
Dos `listing-audit` independientes convergieron en la misma causa raiz:
`confianza="alta"` salia con UNA SOLA SENAL -- un candidato unico, o
varias fotos que coinciden PORQUE COMPARTEN EL MISMO SESGO (la camara, no
el producto). Cuanto menos sabia el pipeline, mas confiado sonaba.

    EL VLM PUEDE PROPONER, PERO NO PUEDE CONFIRMAR EL SOLO.

Es la misma ley que ya rige `core/grouping.py` ("el reloj puede PARTIR,
pero no puede CONFIRMAR"), aplicada a los atributos: ninguna afirmacion de
texto (marca/talla/modelo/ean) sale con `confianza="alta"` sin
CORROBORACION INDEPENDIENTE (`_confianza_corroborada`). Corroboracion
valida, ambas gratis:
  1. El OCR crudo de la MISMA region se parece (`_similitud_normalizada`,
     calibrada sobre pares reales del golden set) a lo que leyo el VLM.
  2. El MISMO valor aparece en >=2 fotos DISTINTAS del grupo.
Sin ninguna de las dos, techo `"media"`. El color (histograma de pixeles)
es la UNICA excepcion que queda SIEMPRE en `"media"` incluso con varias
fotos de acuerdo -- porque el acuerdo entre fotos puede ser el mismo sesgo
del sensor repetido (ver `_extraer_color`), no corroboracion real.

REGLAS DURAS QUE ESTE MODULO ENCIERRA EN CODIGO, NO EN EL PROMPT
------------------------------------------------------------------------
Un prompt es una peticion; un `if` es una garantia. Las trampas reales del
golden set (`tests/golden/legibilidad.json`) se enfrentan ASI:

  1. Estampado != marca, EN LA PUBLICACION: un candidato de
     "estampado_o_grafico" NUNCA es, EL SOLO, lo que se publica en
     `marca`/`talla` -- la exclusion en la publicacion es estructural
     (`_UBICACIONES_VALIDAS_MARCA`). Pero (corregido, INC-010: "la regla #1
     se comia a la regla #2") la ubicacion NUNCA borra evidencia de un
     posible conflicto: si el estampado dice algo DISTINTO de la etiqueta,
     eso es un conflicto real (regla #2), no una senal que se descarta en
     silencio -- el pipeline no puede saber a priori si el estampado es un
     print decorativo o si es la etiqueta la que miente (prenda
     reetiquetada).
  2. Mas de una marca/talla legible (candidatos que NO normalizan igual,
     vengan de la ubicacion que vengan) -> el campo sale `None` +
     `confianza="baja"`, y AMBAS candidatas se exponen en
     `ResultadoExtraccion.conflictos`. El pipeline NUNCA elige.
  3. `legible=False` fuerza `texto=None` EN CODIGO (`_parsear_lectura_crop`),
     pase lo que pase en el JSON del modelo -- un VLM que ignore la
     instruccion y devuelva un texto plausible de todos modos no puede
     colarlo: es una red de seguridad barata, no una excusa para no seguir
     instruyendo bien el prompt.
  4. `composicion`/`material` -> SIEMPRE `None` (`_campo_composicion`).
     Ninguna foto del golden set fotografia esa etiqueta -- no es una
     limitacion del modelo, el dato no existe en ningun pixel. Es
     estructuralmente imposible que esta funcion devuelva otra cosa.
  5. Una foto de metro NUNCA produce una medida por su digitos: la ristra
     se descarta antes de llegar al VLM. Si ademas la foto se marca como
     candidata a metro, se hace UNA llamada VLM sobre la foto COMPLETA
     preguntando explicitamente si el 0 Y el borde de la prenda son
     visibles -- si cualquiera de los dos falta, `medidas=None`.
  6. Un papel manuscrito en el grupo (`ubicacion="papel_manuscrito"`) nunca
     entra en ningun campo del producto: va a `desperfectos` con
     `fuente="diego"`, nunca `fuente="foto"` (es una nota de Diego, no una
     etiqueta del producto).
  7. `pertenece_al_producto=False` descarta la lectura ENTERA, pase lo que
     sea `ubicacion`/`contenido_probable` -- el texto de fondo (un
     portatil ajeno en el encuadre) no es un atributo del producto.
  8. `estado` SIEMPRE sale con `fuente="inferido"` -- nunca "foto", pase lo
     que declare el VLM. Lo confirma Diego (`truth-loop.md` SS A.4).
  9. `color` se muestrea en varias fotos; si divergen, `confianza="baja"`
     (nunca se saca de una sola foto: es un sorteo).
  10. Un fallo del VLM (rate limit, sin API key, error de red, respuesta no
      valida) en un recorte NUNCA rellena ese campo con un valor plausible:
      se registra en `ResultadoExtraccion.fallos` (log + marca), y el resto
      del producto sigue procesandose -- un fallo tecnico no puede tirar
      todo el producto ni disfrazarse de "no legible" (son cosas distintas:
      `fuente="foto"+valor=None+confianza=baja` es "lo mire y no se lee";
      un fallo tecnico es "no lo pude mirar", y eso se ve en `fallos`, no
      se enmascara).

COSTE -- dos correcciones (no optimizaciones opinables) sobre la primera
version de este modulo, pedidas tras re-derivar el gasto por ejecucion:
  - EL COLOR NUNCA PASA POR EL VLM. `architecture.md` Costura 1 (tabla de
    proveedores) es literal: "color por pixeles". Un histograma de pixeles
    (`_color_dominante_rgb`) lo resuelve gratis y sin posibilidad de
    alucinar -- pagarle a un modelo por esto era gastar dinero en una
    pregunta que el proveedor `local` ya tenia asignada por diseno.
  - EL PRESUPUESTO DEL VLM VA DONDE EL OCR FALLA (la ropa), NO DONDE SOBRA
    TEXTO (las cajas). Medido: los productos "de caja" (especificaciones
    tecnicas en varios idiomas) se llevaban el 45% del coste del lote real
    mandando al VLM parrafos de 30-130 palabras que NUNCA iban a ser
    marca/talla/modelo/ean/desperfecto, y repitiendo un EAN/modelo que el
    atajo YA habia resuelto. `_es_bloque_de_texto_largo` y
    `_es_repeticion_de_un_campo_ya_resuelto` descartan esas dos clases de
    region ANTES de llamar al VLM -- ninguna de las 10 reglas duras de
    arriba cambia: siguen aplicando igual sobre lo que SI se manda.

Sin dependencias de red directas: la unica llamada a un proveedor pasa por
`core/llm.py` (`LLMEngine.consultar`), inyectado -- este modulo NUNCA
importa `anthropic`.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Sequence

import numpy as np
from PIL import Image, ImageDraw

from core.images import abrir_derecha
from core.llm import (
    ApiKeyFaltanteError,
    Imagen,
    LLMEngine,
    LLMLlamadaFallidaError,
)
from core.schema import Campo, CategoriaTipo, Evidencia

logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    """Base de los errores propios de core/extract.py."""


class RespuestaVLMInvalidaError(ExtractorError):
    """El VLM devolvio un JSON que no cumple el contrato minimo esperado
    (falta una clave obligatoria, un enum con un valor desconocido...).

    Esto NUNCA se traga en silencio ni se completa con un valor plausible
    (decision-making.md SS 13): se propaga para que quien orquesta la
    extraccion decida -- en `ExtractorEngine` esto se captura por-recorte y
    se anota en `fallos`, nunca se inventa el campo que faltaba.
    """


# ============================================================================
# CONSTANTES -- umbrales y version_prompt (cada TIPO de llamada VLM lleva su
# propio version_prompt: `LLMEngine._clave_cache` hashea bytes+modelo+
# version_prompt+EL TEXTO DEL PROMPT (C6: antes NO incluia el texto, asi que
# endurecer un prompt sin subir version_prompt respondia con el prompt VIEJO
# en silencio), asi que dos llamadas de distinto tipo con bytes identicos
# (p.ej. la MISMA foto completa usada para color Y para estado) colisionarian
# en cache si compartieran version_prompt Y prompt.
# ============================================================================

VERSION_PROMPT_CROP = "extract-crop-v1"
VERSION_PROMPT_METRO = "extract-metro-v1"
VERSION_PROMPT_ESTADO = "extract-estado-v1"
# NO existe VERSION_PROMPT_COLOR: el color sale de pixeles
# (`architecture.md` Costura 1, tabla de proveedores: "color por pixeles"),
# nunca del VLM -- ver `_color_dominante_rgb` mas abajo.

# Ristra de digitos que delata el METRO (golden set: 33 digitos reales,
# '69899995999291909698925959556575'). El EAN mas largo verificado es de 14
# digitos -- 16 deja margen de sobra sin arriesgar confundir un EAN limpio
# con la cinta del metro.
UMBRAL_DIGITOS_METRO = 16

# Fusion de regiones OCR cercanas de la MISMA foto (una marca partida en dos
# lineas, p.ej. "ORIGINAL" + "MARINES", NO son dos marcas). Medido sobre las
# fotos reales: lineas de una misma etiqueta quedan a menos de 150px de
# separacion vertical con solape horizontal amplio.
MARGEN_FUSION_VERTICAL_PX = 150
MARGEN_FUSION_HORIZONTAL_MINIMO_PX = -400  # solape horizontal minimo exigido

# Atajo "el OCR YA lee limpio" (D2): solo se acepta sin pasar por el VLM si
# el score de RapidOCR es alto Y el patron es inequivoco (EAN, "Model:").
UMBRAL_SCORE_OCR_LIMPIO = 0.85

# LEY DE CORROBORACION (INC-010): similitud minima entre el OCR crudo de una
# region y lo que el VLM dijo haber leido para que cuente como corroboracion
# independiente (ver `_confianza_corroborada`). Calibrado con
# `difflib.SequenceMatcher` sobre los pares REALES del golden set (no a ojo):
#   DEBEN corroborar     Reebok/Raabdk=0.500  Reebok/Reabak=0.667
#                        RAMI JALAB/RAMI SALAB=0.900  MARINES/MARINES=1.000
#   NO deben corroborar  JACK & JONES/ESTI550=0.211  JACK & JONES/Orioinae=0.200
#                        UMBRO/RAMI JALAB=0.267 (marcas REALMENTE distintas)
# El hueco entre el "debe" mas bajo (0.500) y el "no debe" mas alto (0.267)
# es de 0.233 -- 0.4 deja margen simetrico razonable a ambos lados (0.1 por
# debajo del primero, 0.133 por encima del segundo).
UMBRAL_SIMILITUD_CORROBORACION = 0.4

# NO es lo mismo "sin testigo" (el OCR no leyo nada en esa region -- no hay
# contradiccion, solo falta corroboracion) que "testigo QUE CONTRADICE" (el
# OCR leyo algo SUSTANCIAL que no se parece a lo que el VLM afirma -- eso es
# evidencia ACTIVA de que el modelo esta inventando o leyendo otra cosa).
# `UMBRAL_MIN_CHARS_TESTIGO_OCR` es el minimo de caracteres alfanumericos en
# `texto_ocr_crudo` para que cuente como "testigo" en vez de ruido -- por
# debajo, se trata como "sin testigo" (media, nunca contradiccion).
# Calibrado sobre los pares REALES, no a ojo:
#   Reebok/Raabdk (6 alnum), Reebok/Reabak (6), RAMI JALAB/RAMI SALAB
#   (9 alnum) y ORIGINAL MARINES/ORIGINAL MARINES (15 alnum) DEBEN poder
#   corroborar -- sus testigos tienen 6-15 caracteres.
#   Nike/Raabdk (6 alnum, similitud 0.200) y Adidas/XXL (3 alnum, similitud
#   0.000) DEBEN poder contradecir -- 'XXL' (el testigo mas corto de los
#   casos reales que SI debe contar) tiene exactamente 3 caracteres, asi
#   que 3 es el maximo umbral que no lo excluye. Por debajo de 3 (1-2
#   caracteres) es indistinguible de ruido de OCR (un trazo suelto, un
#   caracter mal segmentado) y se trata como "sin testigo".
UMBRAL_MIN_CHARS_TESTIGO_OCR = 3

# EAN/UPC: longitudes con checksum GS1 valido (C5). El algoritmo (modulo 10,
# pesos 3/1 alternando desde la DERECHA) es el mismo para las cuatro.
_LONGITUDES_EAN_VALIDAS: frozenset[int] = frozenset({8, 12, 13, 14})

# C9: backstop de coste, NO la defensa primaria (esa son
# `_es_bloque_de_texto_largo`/`_es_repeticion_de_un_campo_ya_resuelto`, que ya
# filtran ANTES de llegar aqui). Medido sobre el lote real: 19.5 cts totales,
# 6.8 cts solo en el producto 1 -- ~6 llamadas se fueron en el portatil de
# fondo que el VLM luego descarta via `pertenece_al_producto=False` (no se
# puede filtrar ANTES: solo el VLM juzga pertenencia). Este limite solo debe
# dispararse en escenas patologicamente ruidosas; 20 da margen de sobra sobre
# el numero de candidatos reales de un producto normal (marca+talla+modelo+
# ean+desperfectos, incluso repartidos en varias fotos).
MAX_LLAMADAS_VLM_POR_PRODUCTO: int = 20

_EAN_OCR_RE = re.compile(r"EAN\w*\W*\*?(\d{8,14})\*?", re.IGNORECASE)
_MODELO_OCR_RE = re.compile(r"\bmodel\w*\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,20})\b", re.IGNORECASE)

# EL PRESUPUESTO DEL VLM VA DONDE EL OCR FALLA (la ropa), NO DONDE SOBRA
# TEXTO (las cajas). Medido sobre el golden set: los productos "de caja"
# (masajeador, electroestimulador) traen especificaciones tecnicas en
# varios idiomas -- parrafos de 30-130 palabras -- que NUNCA van a ser
# marca/talla/modelo/ean/desperfecto (esos son SIEMPRE tokens cortos:
# "Reebok", "XXL", "JACK & JONES", incluso fusionados "ORIGINAL MARINES").
# Filtrar esto ANTES de llamar al VLM ahorra una llamada que nunca iba a
# producir un campo de la ficha -- no es una relajacion de ninguna regla
# dura, es no preguntarle al modelo algo cuya respuesta no se va a usar.
UMBRAL_MAX_PALABRAS_CANDIDATO_TEXTO = 6
UMBRAL_MAX_CHARS_CANDIDATO_TEXTO = 40

# Recorte: margen alrededor del bbox detectado por el OCR, para dar contexto
# a la etiqueta entera (no solo la palabra que el OCR ancló) sin diluir la
# resolucion nativa.
MARGEN_RECORTE_RATIO = 0.6
MARGEN_RECORTE_MINIMO_PX = 50

# Cuantas fotos se muestrean para el color POR PIXELES (regla dura #9:
# nunca de una sola foto -- medido en el golden set, producto 6: rosa en
# una foto, rojo vino en otra bajo luz distinta).
N_FOTOS_MUESTRA_COLOR = 3

CALIDAD_JPEG_RECORTE = 92


# ============================================================================
# ESTRUCTURAS DE DATOS
# ============================================================================

Ubicacion = Literal[
    "etiqueta_interior",  # etiqueta de cuello/costura interior -- fuente de marca/talla/modelo validos
    "estampado_o_grafico",  # texto grande impreso/bordado en el frontal/espalda -- NUNCA marca
    "papel_manuscrito",  # nota escrita a mano por Diego -- va a desperfectos, fuente=diego
    "codigo_o_modelo_impreso",  # texto plano impreso (caja, EAN, modelo) -- no es etiqueta de prenda
    "otro",
]

ContenidoProbable = Literal["marca", "talla", "modelo", "ean", "desperfecto", "otro"]

_UBICACIONES_VALIDAS: frozenset[str] = frozenset(
    {"etiqueta_interior", "estampado_o_grafico", "papel_manuscrito", "codigo_o_modelo_impreso", "otro"}
)
_CONTENIDOS_VALIDOS: frozenset[str] = frozenset(
    {"marca", "talla", "modelo", "ean", "desperfecto", "otro"}
)

# Que UBICACIONES puede tener un candidato valido para cada campo -- esta es
# la aplicacion en codigo de la regla dura #1 (estampado != marca).
_UBICACIONES_VALIDAS_MARCA: frozenset[str] = frozenset({"etiqueta_interior"})
_UBICACIONES_VALIDAS_TALLA: frozenset[str] = frozenset({"etiqueta_interior"})
_UBICACIONES_VALIDAS_MODELO: frozenset[str] = frozenset(
    {"etiqueta_interior", "codigo_o_modelo_impreso"}
)
_UBICACIONES_VALIDAS_EAN: frozenset[str] = frozenset({"codigo_o_modelo_impreso"})


@dataclass(frozen=True)
class RegionOCR:
    """Una region de texto localizada por el OCR local en UNA foto.

    `texto_ocr` es el texto tal y como lo leyo RapidOCR -- puede estar mal
    (es garbled con frecuencia, medido en el golden set). Este texto NUNCA
    se usa para rellenar un campo estructurado del producto directamente
    salvo que pase el atajo de "OCR limpio" (`_intentar_atajo_ocr`, patron
    inequivoco + score alto). En cualquier otro caso solo sirve para
    LOCALIZAR: decidir si hay una ristra de metro (se descarta) o generar
    un recorte para que el VLM lo lea de verdad.
    """

    fichero: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h) en la imagen YA orientada
    texto_ocr: str
    score: float


@dataclass(frozen=True)
class LecturaCrop:
    """Lo que el VLM devolvio al leer UN recorte (o `None` si no se llego a
    consultar -- ver `ExtractorEngine._leer_crop`, que puede devolver
    `None` tras un fallo tecnico registrado en `fallos`).

    `texto_ocr_crudo`: lo que el OCR local leyo en ESA MISMA region ANTES
    de mandarla al VLM (puede ser garbled -- por eso se manda al VLM). Es
    la mitad GRATIS de la ley de corroboracion (INC-010,
    `_confianza_corroborada`): si el OCR ya sugeria algo parecido a lo que
    el VLM afirma, eso es una segunda lectura INDEPENDIENTE del mismo
    pixel. `None` cuando no hubo region OCR de origen (p.ej. en tests
    sinteticos) -- entonces esta via de corroboracion simplemente no
    aplica, no se inventa una similitud.
    """

    fichero: str
    bbox: tuple[int, int, int, int]
    legible: bool
    pertenece_al_producto: bool
    ubicacion: Ubicacion
    contenido_probable: ContenidoProbable
    texto: str | None
    texto_ocr_crudo: str | None = None


@dataclass(frozen=True)
class CandidatoConflicto:
    """Un candidato de un campo con MAS DE UN valor legible y contradictorio
    (regla dura #2). El pipeline no elige: ambos se exponen para Diego."""

    valor: str
    evidencia: Evidencia


@dataclass(frozen=True)
class ResultadoExtraccion:
    """Lo que `ExtractorEngine.extraer_producto` devuelve para UN producto
    (un grupo de fotos ya confirmado por Diego en la Fase 1).

    `campos`: nombre_campo -> `Campo` (la estructura de procedencia de
        `core/schema.py`). Siempre incluye las mismas claves
        (`CAMPOS_PRODUCIDOS`), nunca faltan ni sobran.
    `conflictos`: solo para los campos donde hubo mas de un valor legible y
        contradictorio -- vacio si no hubo ninguno.
    `fallos`: fallos TECNICOS (VLM, OCR, foto corrupta) durante la
        extraccion -- distinto de "no legible" (eso es un `Campo` normal
        con `valor=None`). Un fallo tecnico se ve aqui, nunca se disfraza.
    `coste_usd`: lo que costo esta extraccion en llamadas reales al VLM
        (0.0 si todo vino de cache o de atajos gratis).
    `aviso_coherencia`: C2/INC-011 ("LA FICHA FRANKENSTEIN") -- `None` si
        no hay senal de riesgo. Si los campos de IDENTIDAD del producto
        (marca/talla/modelo/ean) proceden de fotos DISJUNTAS (ninguna foto
        liga dos campos entre si), esto lleva el aviso CON DIENTES: la UI
        ESTA OBLIGADA a mostrarlo (no es un pie de foto,
        `decision-making.md` SS12), y `campos` ya viene degradado (techo
        `confianza="media"` en los campos de identidad) -- ver
        `_detectar_fotos_disjuntas`.
    """

    campos: dict[str, Campo]
    conflictos: dict[str, tuple[CandidatoConflicto, ...]] = field(default_factory=dict)
    fallos: tuple[str, ...] = ()
    coste_usd: float = 0.0
    aviso_coherencia: str | None = None


CAMPOS_PRODUCIDOS: tuple[str, ...] = (
    "marca",
    "talla",
    "modelo",
    "ean",
    "composicion",
    "medidas",
    "color",
    "estado",
    "desperfectos",
)


# ============================================================================
# ETAPA 1 -- OCR LOCALIZA (local, gratis, cero llamadas a proveedor)
# ============================================================================

_motor_ocr = None  # instancia perezosa; RapidOCR tarda ~1-2s en cargar pesos


def _obtener_motor_ocr():
    """Instancia perezosa de RapidOCR -- se crea UNA vez por proceso (cargar
    los pesos es lento, ~1-2s), y se reutiliza en todas las fotos del lote."""
    global _motor_ocr
    if _motor_ocr is None:
        from rapidocr_onnxruntime import RapidOCR  # import perezoso: no todo test necesita OCR real

        _motor_ocr = RapidOCR()
    return _motor_ocr


def _bbox_desde_poligono(poligono: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    xs = [p[0] for p in poligono]
    ys = [p[1] for p in poligono]
    x0, y0 = min(xs), min(ys)
    return (int(x0), int(y0), int(max(xs) - x0), int(max(ys) - y0))


def localizar_regiones_ocr(foto: Path) -> list[RegionOCR]:
    """Corre RapidOCR sobre `foto` YA ORIENTADA (via `core.images.abrir_derecha`,
    para que el bbox devuelto coincida con los pixeles que luego se recortan
    -- si se corriera sobre el fichero crudo y se recortase de la version
    orientada, un EXIF de rotacion desalinearia el bbox).

    Nunca lanza por una foto que RapidOCR no pueda procesar internamente
    (el propio motor ya es robusto); lo que SI puede fallar y se propaga
    hacia el llamador es abrir el fichero (`abrir_derecha`) -- eso lo
    captura `ExtractorEngine.extraer_producto`, foto a foto, sin tirar el
    producto entero (mismo patron que `core/images.py`).
    """
    imagen = abrir_derecha(foto)
    if imagen.mode != "RGB":
        imagen = imagen.convert("RGB")
    arreglo = np.array(imagen)

    resultado, _ = _obtener_motor_ocr()(arreglo)
    if not resultado:
        return []

    regiones = []
    for poligono, texto, score in resultado:
        regiones.append(
            RegionOCR(
                fichero=foto.name,
                bbox=_bbox_desde_poligono(poligono),
                texto_ocr=texto,
                score=float(score),
            )
        )
    return regiones


def _es_ristra_metro(texto: str) -> bool:
    """El metro se delata solo (legibilidad.json regla #5): una ristra larga
    y casi puramente numerica. Un EAN limpio ("EANCODE:*844...*") tiene
    letras alrededor y no cae aqui (lo captura `_intentar_atajo_ocr`)."""
    digitos = sum(1 for c in texto if c.isdigit())
    if digitos < UMBRAL_DIGITOS_METRO:
        return False
    return digitos / max(len(texto), 1) > 0.9


def _cerca(a: RegionOCR, b: RegionOCR) -> bool:
    ax, ay, aw, ah = a.bbox
    bx, by, bw, bh = b.bbox
    hueco_vertical = max(ay, by) - min(ay + ah, by + bh)
    solape_horizontal = min(ax + aw, bx + bw) - max(ax, bx)
    return hueco_vertical <= MARGEN_FUSION_VERTICAL_PX and solape_horizontal > MARGEN_FUSION_HORIZONTAL_MINIMO_PX


def _unir_regiones(grupo: list[RegionOCR]) -> RegionOCR:
    x0 = min(r.bbox[0] for r in grupo)
    y0 = min(r.bbox[1] for r in grupo)
    x1 = max(r.bbox[0] + r.bbox[2] for r in grupo)
    y1 = max(r.bbox[1] + r.bbox[3] for r in grupo)
    texto = " ".join(r.texto_ocr for r in sorted(grupo, key=lambda r: r.bbox[1]))
    return RegionOCR(
        fichero=grupo[0].fichero,
        bbox=(x0, y0, x1 - x0, y1 - y0),
        texto_ocr=texto,
        score=min(r.score for r in grupo),
    )


def fusionar_regiones_cercanas(regiones: Sequence[RegionOCR]) -> list[RegionOCR]:
    """Une regiones de texto cercanas de la MISMA foto en una sola.

    Sin esto, una marca escrita en dos lineas (p.ej. "ORIGINAL" en una linea
    y "MARINES" en la siguiente, medido en el golden set) generaria DOS
    recortes -> el VLM leeria dos fragmentos -> la agregacion los veria
    como DOS marcas distintas y dispararia un conflicto FALSO (la regla
    dura #2 es para dos marcas REALES, no para un nombre partido)."""
    pendientes = list(regiones)
    usados = [False] * len(pendientes)
    fusionadas: list[RegionOCR] = []

    for i, r in enumerate(pendientes):
        if usados[i]:
            continue
        grupo = [r]
        usados[i] = True
        cambiado = True
        while cambiado:
            cambiado = False
            for j, r2 in enumerate(pendientes):
                if usados[j]:
                    continue
                if any(_cerca(g, r2) for g in grupo):
                    grupo.append(r2)
                    usados[j] = True
                    cambiado = True
        fusionadas.append(_unir_regiones(grupo) if len(grupo) > 1 else grupo[0])
    return fusionadas


# ============================================================================
# ETAPA 2 -- ATAJO "EL OCR YA LEE LIMPIO" (D2, gratis, sin VLM)
# ============================================================================


def _validar_checksum_ean(digitos: str) -> bool:
    """C5: checksum GS1 (EAN-8, UPC-12, EAN-13, GTIN-14) -- mismo algoritmo
    modulo-10 para las cuatro longitudes, contando pesos 3/1 alternando
    desde la DERECHA. Un EAN con un solo digito mal leido (el fallo nativo
    del OCR) NUNCA debe pasar como "identidad garantizada": `pricing.py` lo
    usa tal cual como termino de busqueda de comparables, y un digito
    equivocado trae el precio de OTRO producto. Si no valida, no es un EAN
    -- no hay excepcion."""
    if not digitos.isdigit() or len(digitos) not in _LONGITUDES_EAN_VALIDAS:
        return False
    cuerpo, check_esperado = digitos[:-1], int(digitos[-1])
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(cuerpo)))
    return (10 - total % 10) % 10 == check_esperado


def _validar_campo_ean(campo: Campo) -> Campo:
    """Anula (a `valor=None`) un `Campo` de `ean` cuyo checksum no valida,
    venga de donde venga (atajo OCR o lectura VLM). Preserva `fuente` y
    `evidencia` si las habia -- hubo un intento real (PRESENTE_ILEGIBLE),
    solo que el resultado no es un EAN fiable; degrada a
    `confianza="baja"`."""
    if campo.valor is None:
        return campo
    digitos = re.sub(r"\D", "", str(campo.valor))
    if digitos and _validar_checksum_ean(digitos):
        return campo
    return Campo(valor=None, fuente=campo.fuente, confianza="baja", evidencia=campo.evidencia)


def _intentar_atajo_ocr(region: RegionOCR) -> tuple[str, Campo] | None:
    """Si el texto detectado por el OCR es limpio e inequivoco (EAN, o
    "Model:XXX" con score alto), se acepta DIRECTO sin gastar en el VLM --
    ya se tiene el dato (D2, medido: 'EANCODE:*8445061029720*' score=0.91,
    'Model:LLLT-200' score=0.88). Devuelve `(nombre_campo, Campo)` o `None`
    si no aplica ningun atajo -- en ese caso el recorte SIGUE su camino
    normal hacia el VLM.

    EAN (C5): el atajo SOLO se acepta si el checksum GS1 valida. Un EAN
    con checksum invalido no es "el OCR ya lee limpio" -- es un digito mal
    leido, y ese caso NO se cuela gratis: cae al camino normal (VLM), que
    en el peor caso tambien fallara el checksum en `_validar_campo_ean` y
    el campo saldra `None`, nunca un codigo erroneo con `confianza=alta`.
    """
    if region.score < UMBRAL_SCORE_OCR_LIMPIO:
        return None

    m = _EAN_OCR_RE.search(region.texto_ocr)
    if m and _validar_checksum_ean(m.group(1)):
        return "ean", Campo(
            valor=m.group(1),
            fuente="foto",
            confianza="alta",
            evidencia=Evidencia(fichero=region.fichero, bbox=region.bbox),
        )

    m = _MODELO_OCR_RE.search(region.texto_ocr)
    if m:
        return "modelo", Campo(
            valor=m.group(1),
            fuente="foto",
            confianza="alta",
            evidencia=Evidencia(fichero=region.fichero, bbox=region.bbox),
        )

    return None


def _es_bloque_de_texto_largo(texto: str) -> bool:
    """Un bloque de texto MUY largo (especificaciones tecnicas,
    instrucciones, descripciones multilingues) no es un candidato
    razonable de marca/talla/modelo/ean/desperfecto -- esos son SIEMPRE
    tokens cortos (medido en el golden set: 'Reebok', 'XXL', 'UMBRO',
    'JACK & JONES'; incluso fusionados en dos lineas, 'ORIGINAL MARINES'
    o 'EST1590 Oriainadle' se quedan en pocas palabras). Filtrar esto
    ANTES de llamar al VLM ahorra una llamada que nunca iba a producir un
    campo de la ficha -- medido: los parrafos de especificaciones del
    producto 1 (masajeador) llegan a 35 y 132 palabras. Esto NO relaja
    ninguna regla dura: un candidato real jamas cae en este filtro."""
    return (
        len(texto.split()) > UMBRAL_MAX_PALABRAS_CANDIDATO_TEXTO
        or len(texto) > UMBRAL_MAX_CHARS_CANDIDATO_TEXTO
    )


def _es_repeticion_de_un_campo_ya_resuelto(texto: str, campos_atajo: dict[str, Campo]) -> bool:
    """Si este texto contiene, como substring, el valor de un `ean`/`modelo`
    que el atajo YA resolvio (D2), es una repeticion del mismo dato en
    otra parte de la caja (medido: el producto 1 imprime el EAN dos veces,
    una limpia ('EANCODE:*8445061029720*', capturada por el atajo) y otra
    junto a una referencia interna ('THO8LASLHR_UDS *8445061029720')). No
    aporta nada nuevo -- no hace falta gastar una llamada al VLM para
    confirmar un dato que ya se tiene con `confianza='alta'`."""
    for nombre_campo in ("ean", "modelo"):
        campo = campos_atajo.get(nombre_campo)
        if campo is not None and campo.valor and str(campo.valor) in texto:
            return True
    return False


# ============================================================================
# ETAPA 3 -- RECORTE a resolucion nativa
# ============================================================================


def recortar_region(imagen_pil: Image.Image, bbox: tuple[int, int, int, int]) -> bytes:
    """Recorta `bbox` de `imagen_pil` con un margen de contexto, a
    resolucion NATIVA (nunca se redimensiona), y devuelve los bytes JPEG
    listos para `core.llm.Imagen`.

    El margen existe para que el VLM vea la etiqueta completa, no solo el
    trozo exacto que el OCR ancló (una etiqueta suele tener mas lineas de
    texto alrededor de la que el OCR detecto)."""
    x, y, w, h = bbox
    margen = max(int(max(w, h) * MARGEN_RECORTE_RATIO), MARGEN_RECORTE_MINIMO_PX)
    x0 = max(x - margen, 0)
    y0 = max(y - margen, 0)
    x1 = min(x + w + margen, imagen_pil.width)
    y1 = min(y + h + margen, imagen_pil.height)

    recorte = imagen_pil.crop((x0, y0, x1, y1))
    if recorte.mode != "RGB":
        recorte = recorte.convert("RGB")

    import io

    buffer = io.BytesIO()
    recorte.save(buffer, format="JPEG", quality=CALIDAD_JPEG_RECORTE)
    return buffer.getvalue()


def foto_completa_a_bytes(imagen_pil: Image.Image) -> bytes:
    """Foto COMPLETA (no un recorte) a bytes JPEG -- solo se usa para
    estado y la comprobacion de composicion del metro (llamadas al VLM
    donde lo que importa es el ENCUADRE completo, no leer texto pequeno).
    El color NO pasa por aqui: sale de pixeles, nunca del VLM (ver
    `_color_dominante_rgb` mas abajo)."""
    if imagen_pil.mode != "RGB":
        imagen_pil = imagen_pil.convert("RGB")
    import io

    buffer = io.BytesIO()
    imagen_pil.save(buffer, format="JPEG", quality=CALIDAD_JPEG_RECORTE)
    return buffer.getvalue()


# ============================================================================
# ETAPA 3B -- COLOR POR PIXELES (`architecture.md` Costura 1, tabla de
# proveedores: "color por pixeles" -- NUNCA VLM. Cero llamadas a proveedor,
# cero posibilidad de alucinar: el resultado sale directo de los bytes de
# la foto, nunca de un modelo).
# ============================================================================

# Recorte central: en las fotos de producto de Diego el objeto/prenda
# llena el centro del encuadre; los bordes suelen ser fondo/pared/percha.
FRACCION_RECORTE_CENTRAL_COLOR = 0.5
# Miniatura antes de contar: reduce ruido de compresion JPEG/antialiasing
# sin cambiar el color dominante real.
LADO_MINIATURA_COLOR = 60
# Bins de cuantizacion (agrupa tonos parecidos antes de contar el mas
# frecuente -- sin esto, el ruido de compresion dispersaria el histograma
# en cientos de valores RGB casi-iguales y ninguno ganaria por frecuencia).
_PASO_CUANTIZACION_COLOR = 32

# Paleta de referencia CERRADA (nombres en espanol + su RGB de referencia)
# -- igual que los enums de `core/schema.py`: el resultado SIEMPRE es uno
# de estos nombres, nunca una descripcion libre inventada por un modelo.
_PALETA_COLORES_REFERENCIA: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("negro", (25, 25, 25)),
    ("blanco", (240, 240, 240)),
    ("gris", (130, 130, 130)),
    ("gris claro", (195, 195, 195)),
    ("gris oscuro", (75, 75, 75)),
    ("beige", (222, 202, 173)),
    ("marron", (101, 67, 33)),
    ("rojo", (200, 30, 30)),
    ("rojo vino", (110, 20, 40)),
    ("rosa", (232, 160, 180)),
    ("naranja", (230, 120, 30)),
    ("amarillo", (230, 210, 60)),
    ("verde", (60, 140, 60)),
    ("azul", (40, 90, 180)),
    ("azul marino", (20, 30, 70)),
    ("morado", (120, 60, 150)),
)


def _bbox_recorte_central(
    tamano: tuple[int, int], fraccion: float = FRACCION_RECORTE_CENTRAL_COLOR
) -> tuple[int, int, int, int]:
    """El bbox `(x0, y0, x1, y1)`, en coordenadas de la imagen ORIGINAL, del
    `fraccion` central (por defecto, la mitad en cada eje) -- separado de
    `_recorte_central` para que `_color_dominante_rgb` pueda traducir
    `bboxes_excluir` (en coords originales, p.ej. el bbox de un estampado
    ya localizado por el OCR) a coordenadas del recorte (C3d)."""
    ancho, alto = tamano
    mitad_x = max(int(ancho * fraccion / 2), 1)
    mitad_y = max(int(alto * fraccion / 2), 1)
    cx, cy = ancho // 2, alto // 2
    return (cx - mitad_x, cy - mitad_y, cx + mitad_x, cy + mitad_y)


def _recorte_central(imagen_pil: Image.Image, fraccion: float = FRACCION_RECORTE_CENTRAL_COLOR) -> Image.Image:
    """El `fraccion` central de la foto (por defecto, la mitad en cada
    eje) -- evita que el color dominante salga de la pared o la percha en
    vez de la prenda."""
    x0, y0, x1, y1 = _bbox_recorte_central(imagen_pil.size, fraccion)
    return imagen_pil.crop((x0, y0, x1, y1))


def _color_dominante_rgb(
    imagen_pil: Image.Image,
    bboxes_excluir: Sequence[tuple[int, int, int, int]] = (),
) -> tuple[int, int, int]:
    """Color dominante del recorte central via un histograma de pixeles
    cuantizados a bins gruesos. Gratis, determinista, y estructuralmente
    incapaz de alucinar: el resultado es una cuenta de bytes, no la
    opinion de un modelo.

    `bboxes_excluir` (C3d, en coordenadas de la imagen ORIGINAL, formato
    `(x, y, w, h)`): regiones a EXCLUIR del histograma antes de contar --
    p.ej. el bbox de un `estampado_o_grafico` que el OCR ya localizo en
    esta misma foto (medido: producto 4, el recorte central al 50% cae
    justo encima del leon gris del estampado, contaminando el color de la
    prenda real). Si la mascara excluye TODOS los pixeles del recorte (el
    bbox lo cubre entero), se usa el recorte SIN enmascarar -- un color
    degradado es mejor que ningun candidato."""
    x0, y0, x1, y1 = _bbox_recorte_central(imagen_pil.size)
    recorte = imagen_pil.crop((x0, y0, x1, y1))
    if recorte.mode != "RGB":
        recorte = recorte.convert("RGB")

    mascara = Image.new("L", recorte.size, 255)
    if bboxes_excluir:
        dibujo = ImageDraw.Draw(mascara)
        for bx, by, bw, bh in bboxes_excluir:
            rx0, ry0 = bx - x0, by - y0
            rx1, ry1 = rx0 + bw, ry0 + bh
            dibujo.rectangle((rx0, ry0, rx1, ry1), fill=0)

    pequena = recorte.resize((LADO_MINIATURA_COLOR, LADO_MINIATURA_COLOR))
    mascara_pequena = mascara.resize((LADO_MINIATURA_COLOR, LADO_MINIATURA_COLOR), Image.NEAREST)

    arreglo = np.asarray(pequena).reshape(-1, 3).astype(np.int32)
    mascara_arr = np.asarray(mascara_pequena).reshape(-1) > 127
    if mascara_arr.any():
        arreglo = arreglo[mascara_arr]

    cuantizado = (arreglo // _PASO_CUANTIZACION_COLOR) * _PASO_CUANTIZACION_COLOR + _PASO_CUANTIZACION_COLOR // 2
    valores, conteos = np.unique(cuantizado, axis=0, return_counts=True)
    dominante = valores[int(np.argmax(conteos))]
    return (int(dominante[0]), int(dominante[1]), int(dominante[2]))


def _linearizar_srgb(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb_a_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (D65) -> CIE Lab, formula estandar. C3b: Lab separa luminosidad
    (L) de crominancia (a, b) -- a diferencia de la distancia euclidiana en
    RGB, un tono desaturado con hue real (rosa bajo poca saturacion) no
    colapsa sobre el gris neutro solo por tener los tres canales RGB
    parecidos entre si; medido: con RGB puro, un rosa (232,160,180) podia
    resolver mas cerca de 'gris' que de 'rosa'."""
    r, g, b = (_linearizar_srgb(v / 255.0) for v in rgb)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    x, y, z = x / 0.95047, y / 1.0, z / 1.08883

    def _f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = _f(x), _f(y), _f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


_PALETA_COLORES_LAB: tuple[tuple[str, tuple[float, float, float]], ...] = tuple(
    (nombre, _rgb_a_lab(rgb)) for nombre, rgb in _PALETA_COLORES_REFERENCIA
)


def _nombre_color_mas_cercano(rgb: tuple[int, int, int]) -> str:
    """El nombre de `_PALETA_COLORES_REFERENCIA` mas cercano en distancia
    euclidiana en espacio Lab (C3b, no RGB) -- SIEMPRE uno de esa lista
    cerrada, nunca una descripcion libre inventada.

    Limite honesto que esto NO arregla (C3a): un negro sobreexpuesto por
    la autoexposicion del movil a RGB~(80,80,80) sigue siendo, en CUALQUIER
    metrica razonable, mas cercano a 'gris oscuro' (75,75,75) que a 'negro'
    (25,25,25) -- no es un fallo de distancia, es que el sensor realmente
    capturo un gris. Por eso ese caso no se corrige aqui: se corrige en
    `_extraer_color` marcando el resultado como `fuente="inferido"` (una
    inferencia sobre lo que el sensor vio, no una lectura directa) con
    techo `confianza="media"`, nunca "alta"."""
    lab = _rgb_a_lab(rgb)

    def _distancia_cuadrada(referencia: tuple[float, float, float]) -> float:
        return sum((a - b) ** 2 for a, b in zip(lab, referencia))

    return min(_PALETA_COLORES_LAB, key=lambda par: _distancia_cuadrada(par[1]))[0]


# ============================================================================
# ETAPA 4 -- PROMPTS Y ESQUEMAS DEL VLM
# ============================================================================

PROMPT_LECTURA_CROP = """Estas viendo el RECORTE de una foto de un producto de segunda mano.
Tu trabajo es describir que hay en este recorte, no adivinar lo que "deberia" decir.

Responde:
- legible: true solo si puedes leer el texto con seguridad. false es una
  respuesta CORRECTA y ESPERADA si el texto esta borroso, ocluido, en
  angulo imposible, o tapado por algo (un cable, un dedo, un pliegue). NO
  completes lo plausible: si no se lee con seguridad, legible=false y
  texto=null, aunque creas adivinar que dice.
- pertenece_al_producto: false si este texto es de un objeto de FONDO
  ajeno al producto (por ejemplo, las specs de un portatil que sale detras
  en el encuadre). true si es del propio producto o de una nota/papel que
  el vendedor puso junto al producto.
- ubicacion: elige UNA:
  - "etiqueta_interior": una etiqueta cosida de cuello o costura interior
    (la etiqueta OFICIAL de fabricante, con marca/talla/composicion).
  - "estampado_o_grafico": un texto o grafico grande IMPRESO/BORDADO en el
    frontal, espalda o superficie visible de la prenda (un logo de diseno,
    un estampado decorativo). Esto NUNCA es la etiqueta de marca oficial,
    aunque el texto se parezca a un nombre de marca.
  - "papel_manuscrito": un papel o nota escrita A MANO junto al producto.
  - "codigo_o_modelo_impreso": texto plano impreso en una caja o adhesivo
    (codigo de barras, numero de modelo, referencia).
  - "otro": cualquier otra cosa.
- contenido_probable: tu mejor estimacion de que TIPO de informacion es
  esto, incluso si legible=false (por el contexto visual: posicion, estilo,
  tamano de letra). Elige UNA: "marca", "talla", "modelo", "ean",
  "desperfecto" (una nota describiendo un dano/defecto), "otro".
- texto: la transcripcion EXACTA si legible=true; null si legible=false.

Responde SOLO el JSON pedido."""

ESQUEMA_LECTURA_CROP: dict = {
    "type": "object",
    "properties": {
        "legible": {"type": "boolean"},
        "pertenece_al_producto": {"type": "boolean"},
        "ubicacion": {"type": "string", "enum": sorted(_UBICACIONES_VALIDAS)},
        "contenido_probable": {"type": "string", "enum": sorted(_CONTENIDOS_VALIDOS)},
        "texto": {"type": ["string", "null"]},
    },
    "required": ["legible", "pertenece_al_producto", "ubicacion", "contenido_probable", "texto"],
    "additionalProperties": False,
}

PROMPT_MEDIDA_METRO = """Esta foto muestra (o deberia mostrar) un metro/cinta
metrica midiendo una prenda. Para que la medida sea valida hacen falta DOS
cosas visibles a la vez: el punto CERO de la cinta Y el borde de la prenda
que se esta midiendo. Si la cinta esta en el aire, sujeta con la mano, sin
que se vea donde empieza a medir, o si no se ve el borde de la prenda, la
medida NO es derivable -- no inventes un origen que no se ve.

Responde:
- cero_visible: true solo si el punto 0 de la cinta se ve con claridad.
- borde_prenda_visible: true solo si el borde de la prenda que se mide se
  ve con claridad, alineado con la cinta.
- medida_cm: el numero en centimetros que marca el borde de la prenda en la
  cinta, SOLO si cero_visible Y borde_prenda_visible son true. En cualquier
  otro caso, null -- null es la respuesta correcta si falta cualquiera de
  las dos condiciones.

Responde SOLO el JSON pedido."""

ESQUEMA_MEDIDA_METRO: dict = {
    "type": "object",
    "properties": {
        "cero_visible": {"type": "boolean"},
        "borde_prenda_visible": {"type": "boolean"},
        "medida_cm": {"type": ["number", "null"]},
    },
    "required": ["cero_visible", "borde_prenda_visible", "medida_cm"],
    "additionalProperties": False,
}

PROMPT_ESTADO = """Mira esta foto de un producto de segunda mano y evalua su
estado de conservacion visible (manchas, roturas, desgaste, decoloracion).

Responde:
- estimacion_legible: true solo si puedes distinguir con razonable
  seguridad si hay danos visibles o no. false si es ambiguo (por ejemplo,
  no puedes distinguir una mancha real de una sombra o un reflejo de luz).
- descripcion: una frase corta describiendo lo que ves, SOLO si
  estimacion_legible=true. null en caso contrario.

Este campo lo confirmara siempre una persona despues -- tu respuesta es
solo una sugerencia inicial, no una decision final.

Responde SOLO el JSON pedido."""

ESQUEMA_ESTADO: dict = {
    "type": "object",
    "properties": {
        "estimacion_legible": {"type": "boolean"},
        "descripcion": {"type": ["string", "null"]},
    },
    "required": ["estimacion_legible", "descripcion"],
    "additionalProperties": False,
}


# ============================================================================
# ETAPA 5 -- PARSEO DEFENSIVO de la respuesta del VLM
# ============================================================================


def _parsear_lectura_crop(
    datos: dict,
    fichero: str,
    bbox: tuple[int, int, int, int],
    texto_ocr_crudo: str | None = None,
) -> LecturaCrop:
    """Convierte el dict crudo del VLM en un `LecturaCrop`, validando el
    contrato minimo y aplicando la red de seguridad de la regla dura #3:
    si `legible` es false, `texto` se fuerza a `None` EN CODIGO sin
    importar lo que el modelo haya puesto ahi -- un VLM que ignore la
    instruccion del prompt y devuelva un texto plausible de todos modos NO
    puede colarlo en una ficha.

    `texto_ocr_crudo`: lo que el OCR local leyo en esta MISMA region antes
    de mandarla al VLM -- se traslada tal cual al `LecturaCrop` para que
    `_confianza_corroborada` (INC-010) pueda comparar las dos lecturas."""
    for clave in ("legible", "pertenece_al_producto", "ubicacion", "contenido_probable", "texto"):
        if clave not in datos:
            raise RespuestaVLMInvalidaError(f"falta la clave {clave!r} en la respuesta del VLM: {datos!r}")

    if datos["ubicacion"] not in _UBICACIONES_VALIDAS:
        raise RespuestaVLMInvalidaError(f"ubicacion desconocida: {datos['ubicacion']!r}")
    if datos["contenido_probable"] not in _CONTENIDOS_VALIDOS:
        raise RespuestaVLMInvalidaError(f"contenido_probable desconocido: {datos['contenido_probable']!r}")

    legible = bool(datos["legible"])
    texto = datos["texto"] if legible else None
    if texto is not None and not str(texto).strip():
        texto = None

    return LecturaCrop(
        fichero=fichero,
        bbox=bbox,
        legible=legible,
        pertenece_al_producto=bool(datos["pertenece_al_producto"]),
        ubicacion=datos["ubicacion"],
        contenido_probable=datos["contenido_probable"],
        texto=str(texto).strip() if texto is not None else None,
        texto_ocr_crudo=texto_ocr_crudo,
    )


# ============================================================================
# ETAPA 6 -- AGREGACION: de lecturas de recorte a Campo (aqui viven las
# reglas duras #1 y #2, EN CODIGO)
# ============================================================================


def _candidatos_legibles(lecturas: Sequence[LecturaCrop], contenido: ContenidoProbable) -> list[LecturaCrop]:
    """TODAS las lecturas legibles de este `contenido` -- SIN filtrar por
    `ubicacion` (C1/INC-010: la ubicacion prioriza y explica que se
    PUBLICA, pero nunca debe borrar evidencia antes de comprobar si hay un
    conflicto real). Exige `pertenece_al_producto`, `legible`, `texto` no
    vacio y `contenido_probable == contenido`."""
    return [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto and lectura.legible and lectura.texto and lectura.contenido_probable == contenido
    ]


def _candidatos_de_campo(
    lecturas: Sequence[LecturaCrop],
    contenido: ContenidoProbable,
    ubicaciones_validas: frozenset[str],
) -> list[LecturaCrop]:
    """Subconjunto de `_candidatos_legibles` cuya `ubicacion` esta en
    `ubicaciones_validas` -- los unicos PUBLICABLES como valor final de
    `contenido` (regla dura #1: un "estampado_o_grafico" jamas publica
    marca/talla el solo, sin importar que `contenido_probable` diga el
    VLM)."""
    return [lectura for lectura in _candidatos_legibles(lecturas, contenido) if lectura.ubicacion in ubicaciones_validas]


def _intentos_de_campo(
    lecturas: Sequence[LecturaCrop],
    contenido: ContenidoProbable,
    ubicaciones_validas: frozenset[str],
) -> list[LecturaCrop]:
    """Recortes que el VLM clasifico como sobre `contenido` (en una
    ubicacion valida) AUNQUE no fueran legibles -- distingue
    PRESENTE_ILEGIBLE (hubo una foto de esto, no se pudo leer) de
    NO_FOTOGRAFIADO (no hubo ninguna foto de esto)."""
    return [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto
        and lectura.contenido_probable == contenido
        and lectura.ubicacion in ubicaciones_validas
    ]


def _similitud_normalizada(a: str, b: str) -> float:
    """Similitud de cadenas normalizada (minusculas, sin espacios en los
    extremos) via `difflib.SequenceMatcher.ratio()` -- 1.0 identico, 0.0
    sin nada en comun. Calibracion y pares reales en
    `UMBRAL_SIMILITUD_CORROBORACION`."""
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _es_testigo_valido(texto_ocr_crudo: str | None) -> bool:
    """Hay "testigo" si el OCR leyo algo con SENAL suficiente (>=
    `UMBRAL_MIN_CHARS_TESTIGO_OCR` caracteres alfanumericos) en esa region
    -- por debajo de eso, un caracter suelto o un trazo mal segmentado es
    ruido, no una lectura. Ver la calibracion junto a la constante."""
    if not texto_ocr_crudo:
        return False
    alfanumericos = sum(1 for c in texto_ocr_crudo if c.isalnum())
    return alfanumericos >= UMBRAL_MIN_CHARS_TESTIGO_OCR


def _confianza_corroborada(
    miembros: Sequence[LecturaCrop], representante: LecturaCrop
) -> Literal["alta", "media", "contradicho"]:
    """LA LEY DE CORROBORACION (INC-010, matiz anadido tras validar el
    rediseno): el VLM puede PROPONER, pero no puede CONFIRMAR el solo.
    TRES estados, no dos -- "sin testigo" y "testigo que CONTRADICE" NO son
    lo mismo:

      - "alta": corroboracion INDEPENDIENTE real. O bien (1) multi-foto --
        el mismo valor (ya normalizado -- `miembros` es precisamente el
        grupo que comparte la clave normalizada) aparece en >=2 fotos
        DISTINTAS; o bien (2) OCR<->VLM -- el OCR crudo de la MISMA region
        tiene senal suficiente (`_es_testigo_valido`) Y se parece a lo que
        el VLM afirmo leer (>= `UMBRAL_SIMILITUD_CORROBORACION`).
      - "media": SIN TESTIGO -- el OCR no leyo nada sustancial en esa
        region (vacio, o por debajo del umbral de senal). No hay
        contradiccion: solo falta corroboracion. Es el caso legitimo de
        una etiqueta de bajo contraste que el OCR simplemente no lee.
      - "contradicho": TESTIGO EN CONTRA -- el OCR leyo algo SUSTANCIAL en
        esa MISMA region y NO se parece a lo que el VLM afirma. Eso no es
        ausencia de evidencia: es evidencia ACTIVA de que el VLM esta
        inventando (o leyendo una region distinta). Tratar esto igual que
        "sin testigo" vaciaria la ley de corroboracion de contenido --
        quien llama (`_construir_campo_texto`) debe exponer AMBAS lecturas
        como conflicto, nunca publicar ninguna con `confianza` intermedia.
    """
    if len({m.fichero for m in miembros}) >= 2:
        return "alta"

    testigo = representante.texto_ocr_crudo
    if representante.texto and _es_testigo_valido(testigo):
        if _similitud_normalizada(testigo, representante.texto) >= UMBRAL_SIMILITUD_CORROBORACION:  # type: ignore[arg-type]
            return "alta"
        return "contradicho"
    return "media"


def _construir_campo_texto(
    lecturas: Sequence[LecturaCrop],
    contenido: ContenidoProbable,
    ubicaciones_validas: frozenset[str],
) -> tuple[Campo, tuple[CandidatoConflicto, ...]]:
    """El nucleo de las reglas duras #1 y #2, mas la ley de corroboracion
    (INC-010). Ver docstring del modulo.

    Paso 1 (C1): agrupa TODOS los candidatos legibles de `contenido`, SIN
    filtrar por ubicacion -- un conflicto real (dos marcas legibles) tiene
    que verse aunque una de ellas venga de un estampado. Si hay >1 valor
    normalizado distinto, es un conflicto: `None` + `baja` + AMBAS
    candidatas expuestas, la ubicacion NO decide esto.

    Paso 2: si sobrevive UN solo valor, la ubicacion decide si es
    PUBLICABLE (regla dura #1) -- si ninguno de los miembros del grupo
    tiene una `ubicacion` valida (p.ej. solo estampado, sin ninguna
    etiqueta), no se publica: cae al mismo tratamiento que "sin
    candidatos" de abajo.

    Paso 3: si es publicable, la confianza sale de `_confianza_corroborada`
    -- nunca "alta" por defecto."""
    grupos: dict[str, list[LecturaCrop]] = {}
    for lectura in _candidatos_legibles(lecturas, contenido):
        clave = lectura.texto.strip().lower()  # type: ignore[union-attr]
        grupos.setdefault(clave, []).append(lectura)

    if len(grupos) > 1:
        # Regla dura #2 (ampliada, C1): mas de un valor distinto, VENGA DE
        # DONDE VENGA -> None + baja, y AMBAS candidatas expuestas. El
        # pipeline NUNCA elige por Diego.
        candidatas = tuple(
            CandidatoConflicto(
                valor=miembros[0].texto,  # type: ignore[arg-type]
                evidencia=Evidencia(fichero=miembros[0].fichero, bbox=miembros[0].bbox),
            )
            for miembros in grupos.values()
        )
        campo = Campo(valor=None, fuente="inferido", confianza="baja")
        return campo, candidatas

    if len(grupos) == 1:
        (miembros,) = grupos.values()
        candidatos_validos = [m for m in miembros if m.ubicacion in ubicaciones_validas]
        if candidatos_validos:
            representante = candidatos_validos[0]
            estado = _confianza_corroborada(miembros, representante)
            if estado == "contradicho":
                # TESTIGO EN CONTRA (matiz post-validacion): el OCR crudo
                # de esta MISMA region leyo algo sustancial que NO se
                # parece a lo que el VLM afirma -- no es "sin evidencia",
                # es evidencia ACTIVA de que el VLM inventa o lee otra
                # cosa. Mismo tratamiento que dos marcas legibles: None +
                # baja + AMBAS lecturas expuestas. El pipeline NO elige.
                candidatas = (
                    CandidatoConflicto(
                        valor=representante.texto,  # type: ignore[arg-type]
                        evidencia=Evidencia(fichero=representante.fichero, bbox=representante.bbox),
                    ),
                    CandidatoConflicto(
                        valor=representante.texto_ocr_crudo,  # type: ignore[arg-type]
                        evidencia=Evidencia(fichero=representante.fichero, bbox=representante.bbox),
                    ),
                )
                return Campo(valor=None, fuente="inferido", confianza="baja"), candidatas
            campo = Campo(
                valor=representante.texto,
                fuente="foto",
                confianza=estado,
                evidencia=Evidencia(fichero=representante.fichero, bbox=representante.bbox),
            )
            return campo, ()
        # Regla dura #1: el UNICO valor legible viene solo de una ubicacion
        # no valida (p.ej. solo estampado, sin ninguna etiqueta_interior) --
        # un estampado NUNCA publica marca/talla por si solo. Cae al mismo
        # tratamiento que "sin candidatos" de abajo.

    # Sin candidatos PUBLICABLES: distinguir PRESENTE_ILEGIBLE de NO_FOTOGRAFIADO.
    intentos = _intentos_de_campo(lecturas, contenido, ubicaciones_validas)
    if intentos:
        primero = intentos[0]
        campo = Campo(
            valor=None,
            fuente="foto",
            confianza="baja",
            evidencia=Evidencia(fichero=primero.fichero, bbox=primero.bbox),
        )
        return campo, ()

    return Campo(valor=None, fuente="inferido", confianza="baja"), ()


def _construir_campo_desperfectos(lecturas: Sequence[LecturaCrop]) -> Campo:
    """Regla dura #6 (corregida, C7): un papel manuscrito en el grupo es
    una NOTA que Diego puso junto al producto -- pero el texto SI esta en
    el pixel (es una transcripcion, no algo que Diego tecleara en la app),
    asi que `fuente="foto"` con su `evidencia`, NUNCA "diego" ("diego" es
    la unica fuente que la UI no audita -- la nota debe mostrarse como
    "nota tuya, confirmala", no colarse como un hecho ya verificado).

    Ley de corroboracion: una sola foto de la nota -> techo "media"; si la
    MISMA nota aparece en >=2 fotos distintas -> "alta"."""
    candidatas = [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto
        and lectura.ubicacion == "papel_manuscrito"
        and lectura.legible
        and lectura.texto
    ]
    if not candidatas:
        return Campo(valor=None, fuente="inferido", confianza="baja")

    notas_unicas = list(dict.fromkeys(lectura.texto for lectura in candidatas))  # dedup preservando orden
    ficheros_distintos = {lectura.fichero for lectura in candidatas}
    confianza: Literal["alta", "media"] = (
        "alta" if len(notas_unicas) == 1 and len(ficheros_distintos) >= 2 else "media"
    )

    valor = "; ".join(notas_unicas)
    primera = candidatas[0]
    return Campo(valor=valor, fuente="foto", confianza=confianza, evidencia=Evidencia(fichero=primera.fichero, bbox=primera.bbox))


def _campo_composicion() -> Campo:
    """Regla dura #4: SIEMPRE None. Decision explicita de Diego
    (2026-07-14): ninguna foto del lote fotografia la etiqueta de
    composicion -- el dato no existe en ningun pixel, en ningun producto.
    Esta funcion no recibe ningun argumento a proposito: es
    estructuralmente imposible que devuelva otra cosa."""
    return Campo(valor=None, fuente="inferido", confianza="baja")


# ============================================================================
# C2 -- LA FICHA FRANKENSTEIN: coherencia entre campos de IDENTIDAD
# ============================================================================

_CAMPOS_IDENTIDAD_PRODUCTO: tuple[str, ...] = ("marca", "talla", "modelo", "ean")


def _detectar_fotos_disjuntas(campos: dict[str, Campo]) -> str | None:
    """INC-011: si un grupo trae fotos de DOS productos (una fusion que
    Diego no caza al curar), la marca puede salir de una prenda y la talla
    de otra -- las dos con evidencia REAL y LEGIBLE, asi que las capas 1 y
    2 del truth-loop (legibilidad + `listing-audit`) las dejan pasar
    LIMPIAS: cada dato es cierto, solo que no son del mismo producto.

    Senal (barata, sin VLM): si NINGUNA foto aporta evidencia de >=2 de
    los campos de identidad (marca/talla/modelo/ean) a la vez, es que esos
    campos nunca se vieron JUNTOS en la misma foto -- la firma de una
    fusion. Devuelve el aviso (con dientes: quien llama degrada la
    confianza) o `None` si no hay senal de riesgo (0 o 1 campo con valor,
    o al menos una foto liga 2+ campos)."""
    por_fichero: dict[str, set[str]] = {}
    for nombre in _CAMPOS_IDENTIDAD_PRODUCTO:
        campo = campos[nombre]
        if campo.valor is not None and campo.evidencia is not None:
            por_fichero.setdefault(campo.evidencia.fichero, set()).add(nombre)

    n_campos_con_valor = sum(len(vistos) for vistos in por_fichero.values())
    if n_campos_con_valor < 2:
        return None  # no hay nada que pueda "no ligar" -- no aplica

    if any(len(vistos) >= 2 for vistos in por_fichero.values()):
        return None  # al menos una foto liga 2+ campos -- coherente

    fotos_implicadas = ", ".join(sorted(por_fichero))
    return (
        "COHERENCIA: los campos estructurados de este producto proceden de "
        f"fotos DISJUNTAS ({fotos_implicadas}) -- ninguna foto liga dos campos "
        "entre si. Puede ser una fusion de dos productos distintos en el "
        "mismo grupo (revisa las fotos antes de publicar -- INC-011)."
    )


def verificar_misma_prenda_vlm(
    motor_llm: LLMEngine,
    imagen_a: Imagen,
    imagen_b: Imagen,
    producto_id: str | None = None,
) -> bool:
    """HOOK sin implementar (C2, deliberado -- no una feature a medias).

    Cuando `_detectar_fotos_disjuntas` marca un aviso, este es el UNICO
    punto del pipeline entero donde ~0.2 cts (Haiku 4.5) compran la venta
    entera: preguntarle al VLM si `imagen_a` e `imagen_b` son la MISMA
    prenda, para poder CONFIRMAR (no solo avisar) la coherencia. No se
    llama desde ningun sitio todavia. Cuando se implemente: pasa por
    `LLMEngine.consultar` (Costura 1, nunca directo), con su propio
    `version_prompt` dedicado, y el resultado se usa para promover el
    aviso a bloqueante (si dice que NO son la misma prenda) o para
    levantarlo (si confirma que SI lo son) -- nunca en silencio.
    """
    raise NotImplementedError(
        "verificar_misma_prenda_vlm: hook de C2 pendiente de implementar, ver docstring"
    )


# ============================================================================
# C4 -- nombres de fichero duplicados fallan RUIDOSO, nunca aliasing en silencio
# ============================================================================


def _verificar_nombres_unicos(fotos: Sequence[Path]) -> None:
    """El pipeline identifica cada foto por su NOMBRE BASE en varios sitios
    (`RegionOCR.fichero`, `Evidencia.fichero`) -- si el grupo trae dos
    ficheros de RUTAS distintas con el MISMO nombre base (dos tarjetas SD,
    dos moviles), un `dict` clavado por nombre pierde uno EN SILENCIO y el
    recorte resultante sale de la foto EQUIVOCADA (verificado: el crop
    mostraba la percha, no la etiqueta) -- y ni siquiera es auditable a
    posteriori, porque la `Evidencia` guarda un nombre que apunta a dos
    ficheros. Falla RUIDOSO en vez de alias silencioso (C4)."""
    vistas: dict[str, Path] = {}
    duplicados: dict[str, list[Path]] = {}
    for foto in fotos:
        if foto.name in vistas:
            duplicados.setdefault(foto.name, [vistas[foto.name]]).append(foto)
        else:
            vistas[foto.name] = foto
    if duplicados:
        detalle = "; ".join(
            f"{nombre} -> {[str(ruta) for ruta in rutas]}" for nombre, rutas in duplicados.items()
        )
        raise ExtractorError(
            f"Nombres de fichero duplicados en el grupo (rutas distintas, mismo "
            f"nombre base): {detalle}. Un recorte saldria de la foto EQUIVOCADA "
            "(C4) -- renombra o deduplica las fotos antes de extraer."
        )


# ============================================================================
# EL MOTOR -- ExtractorEngine
# ============================================================================


def _abrir_foto_o_none(foto: Path, fallos: list[str], etiqueta: str) -> Image.Image | None:
    """Abre `foto` ya orientada (`core.images.abrir_derecha`), o `None` si
    el fichero esta corrupto/ilegible -- nunca deja que UNA foto mala tire
    la extraccion del producto entero (mismo patron que `core/images.py`:
    se registra el traceback completo con `logger.exception` y se marca en
    `fallos`, nunca se enmascara ni se disfraza de "no legible": esto es
    un fallo TECNICO de apertura de fichero, no un `legible=false` del
    VLM)."""
    try:
        return abrir_derecha(foto)
    except Exception:  # noqa: BLE001 -- frontera "una foto del lote", documentada en el modulo.
        logger.exception("No se pudo abrir %s (%s); se marca y se sigue con el resto", foto, etiqueta)
        fallos.append(f"foto_ilegible:{etiqueta}:{foto.name}")
        return None


class ExtractorEngine:
    """COSTURA 1 aplicada a atributos de producto: `extraer_producto` es el
    punto de entrada. Envuelve un `LLMEngine` inyectado -- este motor NUNCA
    llama a un proveedor por su cuenta.
    """

    def __init__(self, motor_llm: LLMEngine) -> None:
        self.motor_llm = motor_llm

    # -- planificacion (compartida entre estimar coste y ejecutar) ----------

    def _planificar(self, fotos: Sequence[Path]) -> tuple[list[RegionOCR], dict[str, Campo], list[str]]:
        """Corre OCR (gratis) sobre todas las fotos, descarta ristras de
        metro y resuelve el atajo de OCR limpio SOBRE LAS REGIONES CRUDAS
        (antes de fusionar) -- el metro y el atajo son propiedades de UNA
        linea de texto individual tal y como el OCR la detecto (medido:
        "Model:LLLT-200" y "EANCODE:*8445061029720*" son cada uno su
        propia caja). Fusionar ANTES de decidir el atajo mezclaria esas
        lineas limpias con el resto de la etiqueta (mucho mas larga y con
        lineas de score mas bajo), arrastrando el score minimo del grupo
        por debajo del umbral y perdiendo el atajo gratis sin ganar nada a
        cambio -- medido sobre el producto 1 real del golden set. Solo lo
        que SOBRA tras metro+atajo se fusiona antes de generar recortes
        para el VLM (eso si necesita fusion: una marca partida en dos
        lineas, "ORIGINAL" + "MARINES").

        Tras la fusion, se descartan dos clases de region que NUNCA
        aportan un campo de la ficha -- el presupuesto del VLM va donde el
        OCR falla (la ropa), no donde sobra texto (las cajas):
          - Bloques de texto largos (`_es_bloque_de_texto_largo`):
            especificaciones tecnicas, instrucciones, descripciones
            multilingues -- medido en el golden set, producto 1: 35 y 132
            palabras. Un candidato real de marca/talla/modelo/ean/
            desperfecto es SIEMPRE corto.
          - Repeticiones de un campo YA resuelto por el atajo
            (`_es_repeticion_de_un_campo_ya_resuelto`): el mismo EAN/modelo
            impreso otra vez en la caja no aporta un dato nuevo.
        Ninguno de los dos filtros toca las 10 reglas duras de agregacion
        (siguen aplicando exactamente igual sobre lo que SI se manda al
        VLM) -- solo deciden que NO hace falta preguntarle nada al modelo.

        Devuelve (regiones_para_vlm, campos_resueltos_por_atajo,
        fallos_de_ocr)."""
        regiones_para_vlm: list[RegionOCR] = []
        campos_atajo: dict[str, Campo] = {}
        fallos: list[str] = []
        self._fotos_con_metro: list[Path] = []  # usado luego por _evaluar_medidas

        for foto in fotos:
            try:
                regiones = localizar_regiones_ocr(foto)
            except Exception:  # noqa: BLE001 -- frontera "una foto del lote", documentada en el modulo.
                logger.exception("OCR fallo en %s; se sigue con el resto del producto", foto)
                fallos.append(f"ocr:{foto.name}")
                continue

            restantes: list[RegionOCR] = []
            for region in regiones:
                if _es_ristra_metro(region.texto_ocr):
                    self._fotos_con_metro.append(foto)
                    continue

                atajo = _intentar_atajo_ocr(region)
                if atajo is not None:
                    nombre, campo = atajo
                    campos_atajo.setdefault(nombre, campo)
                    continue

                restantes.append(region)

            for region in fusionar_regiones_cercanas(restantes):
                if _es_repeticion_de_un_campo_ya_resuelto(region.texto_ocr, campos_atajo):
                    continue
                if _es_bloque_de_texto_largo(region.texto_ocr):
                    continue
                regiones_para_vlm.append(region)

        return regiones_para_vlm, campos_atajo, fallos

    def construir_solicitudes(self, fotos: Sequence[Path]) -> list[tuple[Sequence[Imagen], str, str]]:
        """Lo que `extraer_producto` MANDARIA al VLM para este producto, sin
        llamar a nada -- para pasarselo a `LLMEngine.estimar_coste_lote`
        ANTES de gastar un euro (decision-making.md SS15,
        `.claude/rules/architecture.md` Costura 1). Corre el mismo OCR y
        las mismas reglas de descarte que la extraccion real, asi que el
        coste estimado coincide con el coste real (misma fuente de
        verdad -- ver `_planificar`).

        Cada solicitud es `(imagenes, prompt, version_prompt)` -- C6: el
        texto del prompt viaja explicito porque `LLMEngine._clave_cache`
        ahora lo incluye en el hash (antes solo hasheaba version_prompt, y
        endurecer un prompt sin subir la version respondia con el prompt
        VIEJO en silencio)."""
        fotos = list(fotos)
        _verificar_nombres_unicos(fotos)  # C4: falla ruidoso antes de mapear por nombre
        regiones_para_vlm, campos_atajo, _ = self._planificar(fotos)
        fallos_descartados: list[str] = []  # una foto ilegible aqui solo reduce el estimado, no crashea
        rutas_por_nombre = {f.name: f for f in fotos}  # seguro tras _verificar_nombres_unicos

        solicitudes: list[tuple[Sequence[Imagen], str, str]] = []

        # C9: mismo backstop que `extraer_producto` -- si no se replicara
        # aqui, la estimacion de coste podria prometer MAS llamadas de las
        # que el motor real llega a hacer, rompiendo la promesa de este
        # docstring ("el coste estimado coincide con el coste real").
        for region in regiones_para_vlm[:MAX_LLAMADAS_VLM_POR_PRODUCTO]:
            if region.fichero in campos_atajo:  # pragma: no cover -- defensivo, no aplica hoy
                continue
            ruta = rutas_por_nombre[region.fichero]
            imagen_pil = _abrir_foto_o_none(ruta, fallos_descartados, "crop")
            if imagen_pil is None:
                continue
            recorte = recortar_region(imagen_pil, region.bbox)
            solicitudes.append(
                ([Imagen(bytes_=recorte, fichero=region.fichero)], PROMPT_LECTURA_CROP, VERSION_PROMPT_CROP)
            )

        for foto in dict.fromkeys(self._fotos_con_metro):  # dedup preservando orden
            imagen_pil = _abrir_foto_o_none(foto, fallos_descartados, "metro")
            if imagen_pil is None:
                continue
            foto_bytes = foto_completa_a_bytes(imagen_pil)
            solicitudes.append(
                ([Imagen(bytes_=foto_bytes, fichero=foto.name)], PROMPT_MEDIDA_METRO, VERSION_PROMPT_METRO)
            )

        # El color NO genera ninguna solicitud VLM: sale de pixeles
        # (`_color_dominante_rgb`), gratis -- no hay nada que estimar aqui.

        if fotos:
            imagen_pil = _abrir_foto_o_none(fotos[0], fallos_descartados, "estado")
            if imagen_pil is not None:
                foto_bytes = foto_completa_a_bytes(imagen_pil)
                solicitudes.append(
                    ([Imagen(bytes_=foto_bytes, fichero=fotos[0].name)], PROMPT_ESTADO, VERSION_PROMPT_ESTADO)
                )

        return solicitudes

    # -- ejecucion real -------------------------------------------------------

    def extraer_producto(
        self,
        fotos: Sequence[Path],
        categoria: CategoriaTipo = "moda",
        producto_id: str | None = None,
    ) -> ResultadoExtraccion:
        """Extrae los atributos de UN producto (fotos ya agrupadas y
        confirmadas por Diego en la Fase 1). `categoria` no cambia el
        comportamiento hoy (reservado para cuando existan reglas
        especificas por categoria); se recibe para que la firma sea
        estable cuando se necesite.
        """
        del categoria  # reservado; ver docstring
        fotos = list(fotos)
        if not fotos:
            raise ValueError("extraer_producto requiere al menos una foto")
        _verificar_nombres_unicos(fotos)  # C4: falla ruidoso antes de mapear por nombre

        regiones_para_vlm, campos_atajo, fallos = self._planificar(fotos)
        rutas_por_nombre = {f.name: f for f in fotos}  # seguro tras _verificar_nombres_unicos

        lecturas: list[LecturaCrop] = []
        limite_vlm_avisado = False
        for indice, region in enumerate(regiones_para_vlm):
            if indice >= MAX_LLAMADAS_VLM_POR_PRODUCTO:
                # C9: backstop de coste -- una escena con mucho fondo ajeno
                # (regiones que el VLM luego descartaria via
                # pertenece_al_producto=False, pero eso solo se sabe DESPUES
                # de llamar) no puede facturar sin techo. Ver docstring de
                # `MAX_LLAMADAS_VLM_POR_PRODUCTO`.
                if not limite_vlm_avisado:
                    limite_vlm_avisado = True
                    logger.warning(
                        "limite de %d llamadas VLM de recorte alcanzado (%d regiones detectadas) "
                        "-- se descartan las restantes para este producto (C9)",
                        MAX_LLAMADAS_VLM_POR_PRODUCTO,
                        len(regiones_para_vlm),
                    )
                    fallos.append(
                        f"limite_llamadas_vlm_alcanzado:{MAX_LLAMADAS_VLM_POR_PRODUCTO}:"
                        f"{len(regiones_para_vlm)}_regiones_detectadas"
                    )
                break
            ruta = rutas_por_nombre[region.fichero]
            imagen_pil = _abrir_foto_o_none(ruta, fallos, "crop")
            if imagen_pil is None:
                continue
            try:
                recorte = recortar_region(imagen_pil, region.bbox)
                imagen = Imagen(bytes_=recorte, fichero=region.fichero)
                resultado = self.motor_llm.consultar(
                    [imagen],
                    PROMPT_LECTURA_CROP,
                    ESQUEMA_LECTURA_CROP,
                    version_prompt=VERSION_PROMPT_CROP,
                    producto_id=producto_id,
                )
                lectura = _parsear_lectura_crop(
                    resultado.datos, region.fichero, region.bbox, texto_ocr_crudo=region.texto_ocr
                )
            except (LLMLlamadaFallidaError, ApiKeyFaltanteError, RespuestaVLMInvalidaError) as exc:
                logger.error("Fallo VLM en recorte %s %s: %s", region.fichero, region.bbox, exc)
                fallos.append(f"vlm_crop:{region.fichero}:{region.bbox}: {exc}")
                continue
            lecturas.append(lectura)

        campo_marca, conflictos_marca = _construir_campo_texto(lecturas, "marca", _UBICACIONES_VALIDAS_MARCA)
        campo_talla, conflictos_talla = _construir_campo_texto(lecturas, "talla", _UBICACIONES_VALIDAS_TALLA)
        campo_modelo, conflictos_modelo = _construir_campo_texto(lecturas, "modelo", _UBICACIONES_VALIDAS_MODELO)
        campo_ean, conflictos_ean = _construir_campo_texto(lecturas, "ean", _UBICACIONES_VALIDAS_EAN)
        campo_desperfectos = _construir_campo_desperfectos(lecturas)

        # Los atajos de OCR limpio (D2) tienen prioridad: son gratis y ya
        # estan verificados por patron -- si tambien hubo un recorte VLM
        # para el mismo campo (no deberia, pero por si acaso) el atajo manda.
        if "ean" in campos_atajo:
            campo_ean = campos_atajo["ean"]
            conflictos_ean = ()
        if "modelo" in campos_atajo:
            campo_modelo = campos_atajo["modelo"]
            conflictos_modelo = ()

        # C5: el checksum GS1 es la ultima palabra sobre `ean`, venga de
        # donde venga (atajo o VLM) -- un digito mal leido no es un EAN.
        campo_ean = _validar_campo_ean(campo_ean)

        campo_medidas, fallos_medidas = self._evaluar_medidas(producto_id)
        # C3d: el color excluye el bbox de un estampado ya localizado en la
        # MISMA foto (si lo hay) -- pasa las lecturas VLM, no llama al VLM.
        campo_color, fallos_color = self._extraer_color(fotos, lecturas)
        campo_estado, fallos_estado = self._extraer_estado(fotos, producto_id)
        fallos.extend(fallos_medidas)
        fallos.extend(fallos_color)
        fallos.extend(fallos_estado)

        campos = {
            "marca": campo_marca,
            "talla": campo_talla,
            "modelo": campo_modelo,
            "ean": campo_ean,
            "composicion": _campo_composicion(),
            "medidas": campo_medidas,
            "color": campo_color,
            "estado": campo_estado,
            "desperfectos": campo_desperfectos,
        }
        conflictos = {
            nombre: candidatas
            for nombre, candidatas in (
                ("marca", conflictos_marca),
                ("talla", conflictos_talla),
                ("modelo", conflictos_modelo),
                ("ean", conflictos_ean),
            )
            if candidatas
        }

        # C2/INC-011: LA FICHA FRANKENSTEIN -- si los campos de identidad
        # vienen de fotos disjuntas, degradar (techo "media") y avisar CON
        # DIENTES (la UI esta obligada a mostrarlo, no es un pie de foto).
        aviso_coherencia = _detectar_fotos_disjuntas(campos)
        if aviso_coherencia is not None:
            for nombre in _CAMPOS_IDENTIDAD_PRODUCTO:
                campo_afectado = campos[nombre]
                if campo_afectado.confianza == "alta":
                    campos[nombre] = replace(campo_afectado, confianza="media")

        coste = (
            self.motor_llm.costes_por_producto()[producto_id].coste_usd
            if producto_id is not None and producto_id in self.motor_llm.costes_por_producto()
            else 0.0
        )

        return ResultadoExtraccion(
            campos=campos,
            conflictos=conflictos,
            fallos=tuple(fallos),
            coste_usd=coste,
            aviso_coherencia=aviso_coherencia,
        )

    # -- medidas (metro): regla dura #5 --------------------------------------

    def _evaluar_medidas(self, producto_id: str | None) -> tuple[Campo, list[str]]:
        fotos_metro = list(dict.fromkeys(getattr(self, "_fotos_con_metro", [])))
        fallos: list[str] = []
        if not fotos_metro:
            # NO_FOTOGRAFIADO: ninguna foto de este producto tenia ni
            # siquiera una ristra de metro -- no hay evidencia que citar.
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos

        for foto in fotos_metro:
            imagen_pil = _abrir_foto_o_none(foto, fallos, "metro")
            if imagen_pil is None:
                continue
            try:
                foto_bytes = foto_completa_a_bytes(imagen_pil)
                imagen = Imagen(bytes_=foto_bytes, fichero=foto.name)
                resultado = self.motor_llm.consultar(
                    [imagen],
                    PROMPT_MEDIDA_METRO,
                    ESQUEMA_MEDIDA_METRO,
                    version_prompt=VERSION_PROMPT_METRO,
                    producto_id=producto_id,
                )
                datos = resultado.datos
                if "cero_visible" not in datos or "borde_prenda_visible" not in datos or "medida_cm" not in datos:
                    raise RespuestaVLMInvalidaError(f"respuesta de medida incompleta: {datos!r}")
            except (LLMLlamadaFallidaError, ApiKeyFaltanteError, RespuestaVLMInvalidaError) as exc:
                logger.error("Fallo VLM evaluando metro en %s: %s", foto.name, exc)
                fallos.append(f"vlm_metro:{foto.name}: {exc}")
                continue

            # Regla dura #5: hacen falta las DOS condiciones a la vez.
            if datos["cero_visible"] and datos["borde_prenda_visible"] and datos["medida_cm"] is not None:
                campo = Campo(
                    valor=float(datos["medida_cm"]),
                    fuente="foto",
                    confianza="media",
                    evidencia=Evidencia(fichero=foto.name),
                )
                return campo, fallos

        # Hubo foto(s) de metro pero ninguna con las dos condiciones -- es
        # PRESENTE_ILEGIBLE (hay evidencia, no es derivable), no NO_FOTOGRAFIADO.
        return (
            Campo(
                valor=None,
                fuente="foto",
                confianza="baja",
                evidencia=Evidencia(fichero=fotos_metro[0].name),
            ),
            fallos,
        )

    # -- color: regla dura #9 -------------------------------------------------

    def _extraer_color(
        self, fotos: Sequence[Path], lecturas_vlm: Sequence[LecturaCrop] = ()
    ) -> tuple[Campo, list[str]]:
        """Color por PIXELES (`architecture.md` Costura 1: "color por
        pixeles"), NUNCA VLM -- gratis y estructuralmente incapaz de
        alucinar en el sentido de "opinar": pero un histograma SI puede
        estar sesgado por el sensor, y C3 (INC-010) corrige tres cosas
        sobre la version anterior:

          (a) el color de un histograma NUNCA es `confianza="alta"` --
              NI SIQUIERA con varias fotos de acuerdo, porque el acuerdo
              puede ser el MISMO sesgo del sensor repetido (medido: una
              sudadera NEGRA sale RGB~(80,80,80) en las 3 fotos por la
              autoexposicion del movil -- eso es el sesgo, no
              corroboracion). Y `fuente="inferido"`, no "foto": una cuenta
              de pixeles no es una lectura directa del color real, es una
              inferencia sobre lo que el sensor capturo.
          (b) `_nombre_color_mas_cercano` distingue en espacio Lab, no RGB
              (ver esa funcion).
          (c) si las fotos muestreadas DIVERGEN en el nombre de color,
              `valor=None` -- nunca "la primera lectura que salga primero"
              (medido en el golden set, producto 6: rosa en una foto, rojo
              vino en otra bajo luz distinta -- ninguna de las dos es mas
              valida que la otra).
          (d) `lecturas_vlm` (las lecturas YA clasificadas por el VLM para
              este producto, si las hay) se usan para EXCLUIR del
              histograma el bbox de cualquier `estampado_o_grafico`
              localizado en la MISMA foto muestreada -- el recorte central
              puede caer justo encima de un estampado (producto 4: un
              leon gris) y contaminar el color real de la prenda.
        """
        fallos: list[str] = []
        lecturas_color: list[tuple[str, Path]] = []
        for foto in _muestrear_fotos(fotos, N_FOTOS_MUESTRA_COLOR):
            imagen_pil = _abrir_foto_o_none(foto, fallos, "color")
            if imagen_pil is None:
                continue
            bboxes_estampado = [
                lectura.bbox
                for lectura in lecturas_vlm
                if lectura.fichero == foto.name and lectura.ubicacion == "estampado_o_grafico"
            ]
            rgb = _color_dominante_rgb(imagen_pil, bboxes_excluir=bboxes_estampado)
            lecturas_color.append((_nombre_color_mas_cercano(rgb), foto))

        if not lecturas_color:
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos

        normalizados = {nombre for nombre, _ in lecturas_color}
        if len(normalizados) > 1:
            # (c) diverge -> None, nunca la primera lectura.
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos

        valor, foto = lecturas_color[0]
        # (a) techo "media", SIEMPRE -- ver docstring de este metodo.
        campo = Campo(
            valor=valor,
            fuente="inferido",
            confianza="media",
            evidencia=Evidencia(fichero=foto.name),
        )
        return campo, fallos

    # -- estado: regla dura #8, SIEMPRE fuente=inferido ------------------------

    def _extraer_estado(self, fotos: Sequence[Path], producto_id: str | None) -> tuple[Campo, list[str]]:
        fallos: list[str] = []
        if not fotos:
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos

        foto = fotos[0]
        imagen_pil = _abrir_foto_o_none(foto, fallos, "estado")
        if imagen_pil is None:
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos
        try:
            foto_bytes = foto_completa_a_bytes(imagen_pil)
            imagen = Imagen(bytes_=foto_bytes, fichero=foto.name)
            resultado = self.motor_llm.consultar(
                [imagen],
                PROMPT_ESTADO,
                ESQUEMA_ESTADO,
                version_prompt=VERSION_PROMPT_ESTADO,
                producto_id=producto_id,
            )
            datos = resultado.datos
            if "estimacion_legible" not in datos or "descripcion" not in datos:
                raise RespuestaVLMInvalidaError(f"respuesta de estado incompleta: {datos!r}")
        except (LLMLlamadaFallidaError, ApiKeyFaltanteError, RespuestaVLMInvalidaError) as exc:
            logger.error("Fallo VLM evaluando estado en %s: %s", foto.name, exc)
            fallos.append(f"vlm_estado:{foto.name}: {exc}")
            return Campo(valor=None, fuente="inferido", confianza="baja"), fallos

        legible = bool(datos["estimacion_legible"])
        descripcion = datos["descripcion"] if legible else None
        # Regla dura #8: SIEMPRE "inferido", nunca "foto" -- lo confirma
        # Diego siempre, pase lo que declare el VLM.
        campo = Campo(
            valor=str(descripcion).strip() if descripcion else None,
            fuente="inferido",
            confianza="media" if legible else "baja",
        )
        return campo, fallos


def _muestrear_fotos(fotos: Sequence[Path], k: int) -> list[Path]:
    """`k` fotos repartidas a lo largo de la secuencia (no las k primeras
    consecutivas, que podrian ser el mismo angulo) -- para que el muestreo
    de color (regla dura #9) de verdad cubra variacion, no repita la misma
    toma."""
    fotos = list(fotos)
    n = len(fotos)
    if n == 0:
        return []
    if n <= k:
        return fotos
    indices = {round(i * (n - 1) / (k - 1)) for i in range(k)} if k > 1 else {0}
    return [fotos[i] for i in sorted(indices)]
