"""core/extract.py -- COSTURA 1 aplicada: `ExtractorEngine`.

`.claude/rules/architecture.md` (Costura 1) + `.claude/rules/truth-loop.md` SS A
+ `.claude/rules/product.md` + `docs/seeds/fase-2.md`.

REDISENO ESTRUCTURAL (2026-07-15, decision de Diego, tras dos `listing-audit`
BLOQUEANTE seguidos sobre la version anterior de este modulo).
------------------------------------------------------------------------
La version anterior intentaba que el pipeline DECIDIERA LA VERDAD: leia una
etiqueta, la corroboraba con una segunda senal (OCR crudo de la misma region,
o la misma foto repetida) y, si corroboraba, publicaba `confianza="alta"`.
Dos auditorias independientes tumbaron esa premisa por la misma causa raiz:
la "corroboracion" NO era independiente (los dos lectores miran el MISMO
crop; si el crop esta mal, se equivocan igual y se dan la razon), la
similitud de cadenas no separa "XL" de "XXL" (0.80, y esas son las tallas
reales del lote), y con un modelo PERFECTO la cobertura util (cuantos campos
llegan a Diego con algo que confirmar) era marca 2/5 prendas y talla 1/5 --
aunque el modelo acertara siempre, el diseno no servia.

La causa raiz de fondo: este proyecto YA aprendio (`core/grouping.py`,
"el reloj puede PARTIR, pero no puede CONFIRMAR") que un algoritmo que
intenta decidir la verdad por su cuenta es la premisa equivocada -- lo que
funciona es que la maquina PROPONGA y el humano CONFIRME, con el fallo caro
hecho INEXPRESABLE, no meramente avisado. `core/pricing.py` ya vive de la
misma ley: no tasa, ensena donde mirar.

LA LEY NUEVA DE ESTE MODULO
------------------------------------------------------------------------
    EL EXTRACTOR NO AFIRMA. PROPONE un valor y ENSENA EL PIXEL del que lo
    saco. Diego confirma de un vistazo.

En la practica:
  1. `confianza="alta"` PRACTICAMENTE DESAPARECE de este modulo. Solo puede
     salir para un dato DETERMINISTA y VERIFICABLE POR UNA REGLA MATEMATICA,
     no por un modelo: un EAN cuyo checksum GS1 valida (`_validar_checksum_ean`).
     Nada mas -- ni la corroboracion multi-foto, ni el atajo de "modelo" por
     regex, ni el papel manuscrito repetido en dos fotos. Que emitir "alta"
     desde una lectura de VLM/OCR sea IMPOSIBLE de escribir, no desaconsejado:
     no existe ningun camino en este archivo que construya un `Campo` con
     `confianza="alta"` salvo `_agregar_campo_texto` sobre candidatos de
     `ean` ya filtrados por checksum (ver `_filtrar_ean_checksum_valido`).
  2. Cada campo trae una `Propuesta` (ademas del `Campo` de exportacion):
     el VALOR propuesto, el RECORTE guardado en disco (el pixel, ampliado,
     listo para pintar en pantalla), TODAS las lecturas (lo que dijo el VLM
     Y lo que leyo el OCR crudo -- nunca se descarta una a favor de la otra),
     las ALTERNATIVAS en conflicto (cada una con su propio recorte: nunca se
     elige entre "UMBRO" y "RAMI JALAB", las dos llegan a Diego) y el MOTIVO
     de por que se propone esto o por que no hay valor.
  3. El recorte que se ENSENA es EXACTAMENTE el que se mando al modelo: se
     capturan los MISMOS bytes en el momento de construir el `Imagen` que se
     envia a `core/llm.py`, y esos mismos bytes son los que se escriben a
     disco (`data/lotes/<lote>/crops/`, o la carpeta que indique quien llama).
     Nunca se re-genera el recorte para "ensenarlo" -- eso podria mostrar un
     pixel distinto del que vio el modelo, y la evidencia seria una mentira.
  4. UN CROP PUEDE PRODUCIR VARIOS CAMPOS. La version anterior solo permitia
     un `texto`/`contenido_probable` por recorte -- un crop real del golden
     set trae la marca Y la talla juntas (misma etiqueta de cuello) y la
     segunda era estructuralmente INALCANZABLE. Ahora el VLM devuelve una
     LISTA de `hallazgos` por recorte (`_parsear_lectura_crop` devuelve una
     lista de `LecturaCrop`, una por hallazgo), y esto sube la cobertura
     util sin gastar una llamada extra.
  5. El atajo "el OCR ya lee limpio" (EAN/modelo por patron, D2) YA NO ES
     UNA CARRETERA PARALELA que bypasea la deteccion de conflictos: sus
     candidatos entran al MISMO pool de lecturas que las del VLM y pasan
     por la MISMA agregacion (`_agregar_campo_texto`) -- dos EAN validos y
     distintos (uno por atajo, otro leido por el VLM en otra foto) generan
     un conflicto real, nunca "el primero que se encontro" en silencio.
  6. Conflictos: SIEMPRE se muestran TODOS, nunca se elige uno.
  7. La coherencia entre campos de identidad (C2/INC-011, "LA FICHA
     FRANKENSTEIN") ya no se apaga con que UNA foto ligue dos campos --
     ahora exige que TODOS los campos de identidad con valor esten en la
     MISMA componente conexa del grafo "campos que comparten una foto"
     (`_detectar_fotos_disjuntas`, union-find). Antes, una foto que ligara
     marca+talla apagaba la defensa entera aunque el modelo viniera de una
     foto disjunta de las otras dos -- quedaba sin auditar.

EL DISENO DE LOCALIZACION (sin cambios: "el OCR LOCALIZA. El VLM LEE.")
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
       "Model:XXX") con score alto, se acepta como candidato SIN gastar en
       el VLM -- entra al mismo pool que las lecturas de VLM (punto 5 de
       arriba), gratis.
    5. Lo que queda se RECORTA (con margen, a resolucion NATIVA, nunca la
       foto entera) y SOLO el recorte se manda al VLM (`core/llm.py`), que
       LEE lo que el OCR no pudo -- y puede devolver VARIOS hallazgos.
    6. Diego CIERRA: un conflicto (dos marcas legibles, o mas de un valor
       en general) no lo resuelve el pipeline -- las candidatas quedan en
       `Propuesta.alternativas`, cada una con su recorte, para que el decida.

REGLAS DURAS QUE ESTE MODULO ENCIERRA EN CODIGO, NO EN EL PROMPT
------------------------------------------------------------------------
Un prompt es una peticion; un `if` es una garantia. Las trampas reales del
golden set (`tests/golden/legibilidad.json`) se enfrentan ASI:

  1. Estampado != marca, EN LA PUBLICACION: un candidato de
     "estampado_o_grafico" NUNCA es, EL SOLO, lo que se PUBLICA en
     `Campo.valor` de marca/talla -- la exclusion en la publicacion es
     estructural (`_UBICACIONES_VALIDAS_MARCA`). Pero la ubicacion NUNCA
     borra evidencia de un posible conflicto: si el estampado dice algo
     DISTINTO de la etiqueta, eso es un conflicto real, no una senal que se
     descarta en silencio -- el pipeline no puede saber a priori si el
     estampado es un print decorativo o si es la etiqueta la que miente
     (prenda reetiquetada). Y aunque el estampado NO compita con ninguna
     etiqueta, su recorte SI se ensena (como `representante` de una
     Propuesta cuyo `Campo.valor` sigue siendo `None`) -- "el extractor no
     afirma, propone y ensena el pixel", incluso cuando lo que propone no
     es publicable.
  2. Mas de una marca/talla/modelo/ean legible (candidatos que NO
     normalizan igual, vengan de la ubicacion que vengan) -> `Campo.valor`
     sale `None` + `confianza="baja"`, y TODAS las candidatas se exponen en
     `Propuesta.alternativas`, cada una con su propio recorte. El pipeline
     NUNCA elige.
  3. `legible=False` fuerza `texto=None` EN CODIGO (`_parsear_lectura_crop`),
     pase lo que pase en el JSON del modelo -- un VLM que ignore la
     instruccion y devuelva un texto plausible de todos modos no puede
     colarlo: es una red de seguridad barata, no una excusa para no seguir
     instruyendo bien el prompt.
  4. `composicion`/`material` -- CAMPO ELIMINADO DE LA FICHA (Diego,
     2026-07-17): solo aplicaba a ropa y "no es realmente importante" para
     su flujo. Ademas de que ninguna foto del golden set fotografia la
     etiqueta de composicion, el campo YA NO se pide en la sintesis
     (`_CAMPOS_SINTESIS` ya no incluye "material") ni aparece en
     `CAMPOS_PRODUCIDOS`/`ui/ficha.py`. `core/export.py::_campo_composicion`
     sigue existiendo para Vinted (su enum de materiales SI es un campo
     valido alli) pero ya nunca recibe un valor del pipeline -- degrada a
     `valor=None`, Diego lo rellena a mano si quiere.
  5. Una foto de metro NUNCA produce una medida por su digitos: la ristra
     se descarta antes de llegar al VLM. Si ademas la foto se marca como
     candidata a metro, se hace UNA llamada VLM sobre la foto COMPLETA
     preguntando explicitamente si el 0 Y el borde de la prenda son
     visibles -- si cualquiera de los dos falta, `medidas=None`.
  6. Un papel manuscrito en el grupo (`ubicacion="papel_manuscrito"`) nunca
     entra en ningun campo del producto: va a `desperfectos` con
     `fuente="foto"` (es una transcripcion de un pixel real, no un dato
     tecleado por Diego), techo `confianza="media"` SIEMPRE (la version
     anterior subia a "alta" si la misma nota aparecia en 2+ fotos -- esa
     via murio con el resto de la maquinaria de corroboracion, regla 1).
  7. `pertenece_al_producto=False` descarta la lectura ENTERA, pase lo que
     sea `ubicacion`/`contenido_probable` -- el texto de fondo (un
     portatil ajeno en el encuadre) no es un atributo del producto.
  8. `estado` SIEMPRE sale con `fuente="inferido"` -- nunca "foto", pase lo
     que declare el VLM. Lo confirma Diego (`truth-loop.md` SS A.4).
  9. `color` se muestrea en varias fotos; si divergen, `confianza="baja"`.
     NUNCA "alta", ni siquiera si TODAS las fotos coinciden -- el acuerdo
     puede ser el mismo sesgo del sensor repetido (ver `_extraer_color`).
  10. Un fallo del VLM (rate limit, sin API key, error de red, respuesta no
      valida) en un recorte NUNCA rellena ese campo con un valor plausible:
      se registra en `ResultadoExtraccion.fallos` (log + marca), y el resto
      del producto sigue procesandose.

LA SIMILITUD OCR<->VLM: DE "CORROBORA" A "SOLO ORDENA/AVISA"
------------------------------------------------------------------------
La version anterior usaba la similitud entre el OCR crudo de una region y
lo que el VLM dijo haber leido para CONCEDER `confianza="alta"`
(`_confianza_corroborada`, ya eliminada de este archivo). Esa funcion, el
umbral que la alimentaba, y la via "misma foto x2 -> alta" murieron con la
ley nueva. Lo que SI se conserva -- `_similitud_normalizada` y
`UMBRAL_SIMILITUD_DUDOSO` -- vive ahora SOLO como senal de aviso dentro del
`motivo` de la Propuesta ("el OCR leyo algo que no se parece a lo que dice
el VLM: revisa este recorte con mas cuidado"), nunca para tocar
`confianza`. Es informativo, no decisorio.

COSTE -- sin cambios respecto a la version anterior (medido, no opinable):
  - EL COLOR NUNCA PASA POR EL VLM COMO FUENTE PRIMARIA (`architecture.md`
    Costura 1: "color por pixeles"). `_color_dominante_rgb` lo resuelve
    gratis; el VLM solo entra como GAP-FILLER de la sintesis (ver abajo)
    cuando los pixeles ya se abstuvieron.
  - EL PRESUPUESTO DEL VLM VA DONDE EL OCR FALLA (la ropa), NO DONDE SOBRA
    TEXTO (las cajas): `_es_bloque_de_texto_largo` y
    `_es_repeticion_de_un_campo_ya_resuelto` descartan bloques largos y
    repeticiones de un dato ya resuelto por el atajo, ANTES de llamar al VLM.

LA SINTESIS COMPROMETIDA (2026-07-15, pivote de producto decidido por Diego)
------------------------------------------------------------------------
Hasta aqui, el diseno de este modulo optimizaba ABSTENERSE: ante conflicto
o falta de senal, `valor=None`. Diego decidio INVERTIR ese default para la
etapa final: EL revisa cada campo con su recorte delante antes de publicar,
asi que en SU flujo un campo vacio le cuesta TECLEARLO y un valor
pre-rellenado que falle le cuesta 2 SEGUNDOS corregirlo -- el coste ya no
es simetrico, y el diseno tiene que seguir a esa asimetria nueva.

Por eso, tras construir los candidatos/lecturas de siempre (el pool
determinista de arriba, SIN cambios), `ExtractorEngine._sintetizar_ficha`
hace UNA llamada VLM mas que ve el producto ENTERO (hasta
`N_FOTOS_MUESTRA_SINTESIS` fotos GENERALES, nunca crops -- no hay
clasificador de tipo de foto en este repo, `truth-loop.md` SS E, asi que se
reparten con `_muestrear_fotos` igual que el muestreo de color, no se
"eligen las de mas resolucion" de verdad) MAS la lista en texto de todo lo
que el OCR/VLM YA detecto en los crops, y PROPONE un valor COMPROMETIDO
para `marca`/`modelo`/`talla`/`color`/`estado`/`medidas`, ademas
de redactar `titulo`/`descripcion` (campos NUEVOS que no existian antes de
este paso).

GAP-FILLER, NUNCA reemplazo ciego: la sintesis solo toca un campo si la
agregacion determinista (o el histograma de pixeles) YA lo dejo en
`valor=None` -- una lectura de una etiqueta a resolucion NATIVA, o un
histograma de pixeles, sigue siendo mas fiable que una opinion sobre la
foto general reescalada a 1568px, asi que la sintesis NUNCA pisa un valor
que la agregacion ya resolvio con solidez (ni siquiera para "corroborar":
esa via ya murio una vez, `[INC-010]`/`[INC-012]`, no se reintenta aqui).
`ean` (unico camino a `confianza="alta"`, via checksum matematico),
`desperfectos` (regla dura #6, transcripcion literal de una nota
manuscrita) y `composicion`/`material` (ELIMINADO de la ficha entera,
Diego 2026-07-17: solo aplicaba a ropa y no aportaba valor a su flujo)
quedan ESTRUCTURALMENTE fuera de la sintesis -- no estan en su
`json_schema`, no hay ningun camino en este archivo por el que la sintesis
pueda tocarlos.

La ley de procedencia (`truth-loop.md` SS A) sigue intacta: `visible_en_foto`
decide `fuente` -- "foto" SOLO si hay un candidato REAL que lo respalde
(`de_texto_detectado` casa con una lectura legible del pool) Y su ubicacion
es publicable para ese campo (regla dura #1 se aplica TAMBIEN aqui: un
"visible_en_foto=true" sobre un candidato de `estampado_o_grafico` para
`marca` se DEGRADA a "inferido" en codigo, la misma disciplina de
"un prompt es una peticion, un if es una garantia" del resto del modulo).
"inferido" en cualquier otro caso. `confianza="alta"` sigue siendo
estructuralmente IMPOSIBLE desde este camino -- el `json_schema` de la
sintesis solo admite "media"/"baja" (nunca "alta"), y el camino "inferido"
la fuerza a "baja" en codigo, pase lo que diga el modelo.

Lo que SI cambia respecto a `truth-loop.md` SS A.3 ("inferido nunca se pega
tal cual en un campo estructurado"): un valor "inferido" AHORA SI se
publica en `marca`/`talla`/`medidas`/`color`/`estado` -- ese es
el pivote explicito de Diego para ESTE camino, y SOLO para el hueco que la
agregacion determinista dejo vacio. Se publica siempre marcado
`fuente="inferido"`, con su recorte de CONTEXTO si lo hay (nunca "el
recorte que se mando", si no hay un candidato real que lo respalde) y
techo `confianza="baja"` -- la UI (`ui/ficha.py`, pendiente) es quien debe
distinguir visualmente "foto" de "inferido" para que Diego sepa, de un
vistazo, cual confirma y cual corrige.

Sin dependencias de red directas: la unica llamada a un proveedor pasa por
`core/llm.py` (`LLMEngine.consultar`), inyectado -- este modulo NUNCA
importa `anthropic`.

`categoria` (2026-07-17, Fase 3 -- decide que campos estructurados pide
cada plataforma, `core.schema.WALLAPOP_ATRIBUTOS_POR_CATEGORIA`) se anadio
a la MISMA llamada de sintesis (0 llamadas extra, 0 coste extra): es un
ENUM CERRADO (`core.schema.CATEGORIAS`), no un texto legible, asi que NO
tiene camino a `fuente="foto"` -- sale SIEMPRE "inferido"/"baja", igual
que `estado`. Regla dura nueva: si el modelo devuelve algo fuera del enum,
la clave "categoria" NO se anade a `campos` (nunca "otros" como comodin
silencioso, decision-making.md SS13) -- unica excepcion a que `campos`
siempre traiga las mismas claves de `CAMPOS_PRODUCIDOS`.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw

from core.images import abrir_derecha
from core.llm import (
    ApiKeyFaltanteError,
    Imagen,
    LLMEngine,
    LLMLlamadaFallidaError,
)
from core.schema import CATEGORIAS, Campo, CategoriaTipo, Evidencia, es_categoria_valida

logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    """Base de los errores propios de core/extract.py."""


class RespuestaVLMInvalidaError(ExtractorError):
    """El VLM devolvio un JSON que no cumple el contrato minimo esperado
    (falta una clave obligatoria, un enum con un valor desconocido, la
    lista de `hallazgos` viene vacia...).

    Esto NUNCA se traga en silencio ni se completa con un valor plausible
    (decision-making.md SS 13): se propaga para que quien orquesta la
    extraccion decida -- en `ExtractorEngine` esto se captura por-recorte y
    se anota en `fallos`, nunca se inventa el campo que faltaba.
    """


# ============================================================================
# CONSTANTES -- umbrales y version_prompt (cada TIPO de llamada VLM lleva su
# propio version_prompt: `LLMEngine._clave_cache` hashea bytes+modelo+
# version_prompt+EL TEXTO DEL PROMPT (antes NO incluia el texto, asi que
# endurecer un prompt sin subir version_prompt respondia con el prompt VIEJO
# en silencio), asi que dos llamadas de distinto tipo con bytes identicos
# (p.ej. la MISMA foto completa usada para color Y para estado) colisionarian
# en cache si compartieran version_prompt Y prompt.
# ============================================================================

VERSION_PROMPT_CROP = "extract-crop-v2"  # v2: hallazgos como LISTA (regla 4)
VERSION_PROMPT_METRO = "extract-metro-v1"
VERSION_PROMPT_ESTADO = "extract-estado-v1"
VERSION_PROMPT_SINTESIS = "extract-sintesis-v3"  # v3: + categoria (enum cerrado, SIEMPRE inferido)
# NO existe VERSION_PROMPT_COLOR: el color sale de pixeles
# (`architecture.md` Costura 1, tabla de proveedores: "color por pixeles"),
# nunca del VLM -- ver `_color_dominante_rgb` mas abajo. La sintesis SI
# puede rellenar `color` (ver mas abajo), pero solo como GAP-FILLER cuando
# el histograma de pixeles ya se abstuvo (`valor=None`) -- los pixeles
# siguen siendo la fuente PRIMARIA, nunca se le pregunta al VLM primero.

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
# Sus candidatos entran al MISMO pool que los del VLM (regla 5 del docstring
# del modulo) -- no conceden "alta" por si solos salvo EAN con checksum
# valido (unico camino a "alta" de todo este archivo).
UMBRAL_SCORE_OCR_LIMPIO = 0.85

# Similitud MINIMA (calibrada sobre pares REALES del golden set, no a ojo)
# entre el OCR crudo de una region y lo que el VLM dijo haber leido, para
# considerar que NO hay senal de aviso ("dudoso"). YA NO concede confianza
# -- ver "LA SIMILITUD OCR<->VLM" en el docstring del modulo. Calibracion:
#   DEBEN parecerse    Reebok/Raabdk=0.500  Reebok/Reabak=0.667
#                      RAMI JALAB/RAMI SALAB=0.900  MARINES/MARINES=1.000
#   NO deben parecerse JACK & JONES/ESTI550=0.211  JACK & JONES/Orioinae=0.200
#                      UMBRO/RAMI JALAB=0.267 (marcas REALMENTE distintas)
UMBRAL_SIMILITUD_DUDOSO = 0.4

# Minimo de caracteres alfanumericos en `texto_ocr_crudo` para que cuente
# como "testigo" real (en vez de ruido de OCR: un trazo suelto, un caracter
# mal segmentado) al construir el aviso de "dudoso" en el motivo de una
# Propuesta. Calibrado sobre los pares reales (ver constante de arriba):
# 'XXL' (3 alnum) es el testigo mas corto que SI debe contar.
UMBRAL_MIN_CHARS_TESTIGO_OCR = 3

# EAN/UPC: longitudes con checksum GS1 valido (C5). El algoritmo (modulo 10,
# pesos 3/1 alternando desde la DERECHA) es el mismo para las cuatro.
_LONGITUDES_EAN_VALIDAS: frozenset[int] = frozenset({8, 12, 13, 14})

# Backstop de coste, NO la defensa primaria (esa son
# `_es_bloque_de_texto_largo`/`_es_repeticion_de_un_campo_ya_resuelto`, que ya
# filtran ANTES de llegar aqui). Medido sobre el lote real: 19.5 cts totales,
# 6.8 cts solo en el producto 1 -- ~6 llamadas se fueron en el portatil de
# fondo que el VLM luego descarta via `pertenece_al_producto=False` (no se
# puede filtrar ANTES: solo el VLM juzga pertenencia). Este limite solo debe
# dispararse en escenas patologicamente ruidosas.
MAX_LLAMADAS_VLM_POR_PRODUCTO: int = 20

_EAN_OCR_RE = re.compile(r"EAN\w*\W*\*?(\d{8,14})\*?", re.IGNORECASE)
_MODELO_OCR_RE = re.compile(r"\bmodel\w*\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,20})\b", re.IGNORECASE)

# EL PRESUPUESTO DEL VLM VA DONDE EL OCR FALLA (la ropa), NO DONDE SOBRA
# TEXTO (las cajas). Medido sobre el golden set: los productos "de caja"
# (masajeador, electroestimulador) traen especificaciones tecnicas en
# varios idiomas -- parrafos de 30-130 palabras -- que NUNCA van a ser
# marca/talla/modelo/ean/desperfecto (esos son SIEMPRE tokens cortos).
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

# Cuantas fotos GENERALES (planteadas como "producto entero", nunca crops)
# ve la sintesis comprometida. Mismo valor que el color a proposito -- NO
# es una seleccion real por resolucion/generalidad (no hay clasificador de
# tipo de foto en este repo, `truth-loop.md` SS E, ya se midio y se
# descarto CLIP/OCR para eso), asi que se reparten con `_muestrear_fotos`
# igual que el muestreo de color: cobertura de la secuencia, no un
# "elegir las mejores fotos" que este codigo no puede hacer honestamente.
N_FOTOS_MUESTRA_SINTESIS = 3

CALIDAD_JPEG_RECORTE = 92

# Nombre de la subcarpeta de recortes dentro de la carpeta de un lote
# (`data/lotes/<lote_id>/crops/`) cuando quien llama no especifica una
# carpeta explicita -- ver `ExtractorEngine`.
NOMBRE_CARPETA_CROPS = "crops"


# ============================================================================
# ESTRUCTURAS DE DATOS
# ============================================================================

Ubicacion = Literal[
    "etiqueta_interior",  # etiqueta de cuello/costura interior -- fuente de marca/talla/modelo validos
    "estampado_o_grafico",  # texto grande impreso/bordado en el frontal/espalda -- NUNCA marca
    "papel_manuscrito",  # nota escrita a mano por Diego -- va a desperfectos, fuente=foto
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
    """UN hallazgo dentro de un recorte -- un mismo recorte puede producir
    VARIOS `LecturaCrop` (regla dura #4 del docstring del modulo: una
    etiqueta de cuello real trae la marca Y la talla juntas).

    `origen` distingue si este hallazgo vino de una lectura real del VLM
    ("vlm") o del atajo de OCR limpio sin pasar por el VLM ("atajo_ocr",
    D2) -- lo necesita `Propuesta` para etiquetar correctamente sus
    `Lectura` (nunca se le atribuye al VLM algo que nunca vio).

    `texto_ocr_crudo`: lo que el OCR local leyo en ESA MISMA region ANTES
    de mandarla al VLM (puede ser garbled). Ya NO concede confianza (esa
    maquinaria murio, ver docstring del modulo) -- ahora solo alimenta el
    aviso "dudoso" informativo en `Propuesta.motivo`. `None` cuando no hubo
    region OCR de origen (p.ej. en tests sinteticos).
    """

    fichero: str
    bbox: tuple[int, int, int, int]
    legible: bool
    pertenece_al_producto: bool
    ubicacion: Ubicacion
    contenido_probable: ContenidoProbable
    texto: str | None
    texto_ocr_crudo: str | None = None
    origen: Literal["vlm", "atajo_ocr"] = "vlm"


@dataclass(frozen=True)
class Lectura:
    """Una lectura CRUDA sobre un candidato -- lo que dijo el VLM, o lo que
    leyo el OCR local en la misma region. Nunca se elige entre lecturas:
    TODAS viven juntas en `Propuesta.lecturas` para que Diego las compare."""

    origen: Literal["vlm", "ocr"]
    texto: str | None


@dataclass(frozen=True)
class Candidato:
    """Un valor candidato para un campo, EN CONFLICTO con otro(s) -- cada
    uno con su PROPIO recorte y evidencia (nunca se elige entre ellos).

    Nota de diseno: el contrato original de este rediseno pedia
    `alternativas: tuple[str, ...]` (solo el valor). Se amplia aqui a un
    `Candidato` completo porque un string solo no basta para que Diego
    pueda MIRAR el pixel de CADA candidata por separado -- y esa es la ley
    de este modulo entero ("UMBRO y RAMI JALAB salen las dos como
    alternativas CON SUS RECORTES", verificacion pedida explicitamente).
    """

    valor: str
    recorte: Path | None
    evidencia: Evidencia
    lecturas: tuple[Lectura, ...] = ()


@dataclass(frozen=True)
class Propuesta:
    """La entrega real de este modulo: NO una afirmacion, una PROPUESTA con
    el pixel del que sale, para que Diego confirme en segundos.

    `campo`: nombre del campo ("marca", "talla", "modelo", "ean", "color",
        "medidas", "estado", "desperfectos"). ("composicion" ELIMINADA de
        la ficha, Diego 2026-07-17 -- ya no es una clave posible aqui.)
    `valor`: lo que se PROPONE -- `None` es un resultado correcto, nunca un
        fallo (puede haber conflicto, o simplemente no haber nada legible).
    `recorte`: EL PIXEL, guardado en disco, EXACTAMENTE el mismo que se
        mando al modelo (o, si no hubo VLM -- atajo OCR o un valor por
        pixeles/nota manuscrita ilegible-pero-vista --, el recorte de la
        region que originó la propuesta). `None` si no hay ningun recorte
        que mostrar.
    `evidencia`: foto + bbox, mismo sistema de coordenadas que `recorte`.
    `lecturas`: TODAS las lecturas crudas disponibles sobre el valor
        propuesto (la del VLM y la del OCR, si las hay) -- nunca se
        descarta una a favor de la otra.
    `alternativas`: candidatas EN CONFLICTO con el valor propuesto (vacio
        si no hubo conflicto). Cada una es un `Candidato` completo, con su
        propio recorte -- el pipeline nunca elige entre ellas.
    `motivo`: por que se propone este valor (o por que no hay ninguno) --
        texto corto y legible para Diego, no un codigo interno.
    """

    campo: str
    valor: str | None
    recorte: Path | None
    evidencia: Evidencia | None
    lecturas: tuple[Lectura, ...] = ()
    alternativas: tuple[Candidato, ...] = ()
    motivo: str = ""


@dataclass(frozen=True)
class ResultadoExtraccion:
    """Lo que `ExtractorEngine.extraer_producto` devuelve para UN producto
    (un grupo de fotos ya confirmado por Diego en la Fase 1).

    `campos`: nombre_campo -> `Campo` (la estructura de procedencia de
        `core/schema.py`) -- LA COSTURA para exportar a Wallapop/Vinted.
        Siempre incluye las mismas claves (`CAMPOS_PRODUCIDOS`), CON UNA
        EXCEPCION: "categoria" falta si el modelo violo su enum cerrado
        (nunca se rellena con un comodin silencioso) -- quien consuma
        `campos` debe usar `.get("categoria")`, nunca asumir que esta.
    `propuestas`: nombre_campo -> `Propuesta` -- LA ENTREGA para la UI de
        revision de Diego: el recorte, todas las lecturas, las alternativas
        en conflicto (cada una con su recorte) y el motivo. Mismas claves
        que `campos`.
    `fallos`: fallos TECNICOS (VLM, OCR, foto corrupta) durante la
        extraccion -- distinto de "no legible" (eso es un `Campo` normal
        con `valor=None`). Un fallo tecnico se ve aqui, nunca se disfraza.
    `coste_usd`: lo que costo esta extraccion en llamadas reales al VLM
        (0.0 si todo vino de cache o de atajos gratis).
    `aviso_coherencia`: "LA FICHA FRANKENSTEIN" -- `None` si no hay senal
        de riesgo. Si los campos de IDENTIDAD del producto (marca/talla/
        modelo/ean) no estan todos en la MISMA componente conexa de "fotos
        que comparten campos", esto lleva el aviso CON DIENTES: la UI ESTA
        OBLIGADA a mostrarlo, y `campos` ya viene degradado (techo
        `confianza="media"` en los campos de identidad) -- ver
        `_detectar_fotos_disjuntas`.
    """

    campos: dict[str, Campo]
    propuestas: dict[str, Propuesta] = field(default_factory=dict)
    fallos: tuple[str, ...] = ()
    coste_usd: float = 0.0
    aviso_coherencia: str | None = None


CAMPOS_PRODUCIDOS: tuple[str, ...] = (
    "marca",
    "talla",
    "modelo",
    "ean",
    # "composicion" ELIMINADO de la ficha (Diego, 2026-07-17): solo
    # aplicaba a ropa y no aportaba valor a su flujo -- ver la regla
    # dura #4 del docstring del modulo. Ya no se pide en la sintesis
    # ni se produce nunca.
    "medidas",
    "color",
    "estado",
    "desperfectos",
    "categoria",  # NUEVO (LA SINTESIS COMPROMETIDA): clasificacion moda/electronica/
    # hogar/libros/otros, fuente="inferido" SIEMPRE -- NUNCA "foto" (no es texto
    # legible en un pixel, es un juicio). UNICA excepcion a "siempre las mismas
    # claves" de abajo: si el modelo viola el enum cerrado, la clave "categoria"
    # NO se anade a `campos` (nunca un comodin tipo "otros" en silencio) -- el
    # fallo queda en `ResultadoExtraccion.fallos`.
    "titulo",  # NUEVO (LA SINTESIS COMPROMETIDA): borrador redactado, fuente=inferido SIEMPRE
    "descripcion",  # NUEVO, idem
)

_CAMPOS_IDENTIDAD_PRODUCTO: tuple[str, ...] = ("marca", "talla", "modelo", "ean")


# ============================================================================
# SERIALIZACION -- persistencia de la extraccion (core/store.py, Fase 2 Paso 2)
# ============================================================================
# `ResultadoExtraccion` vive en memoria durante una sesion; `core/store.py`
# lo guarda como JSON en texto plano (`productos.campos`, columna que YA
# existe desde la Fase 1) para que sobreviva a un rerun de Streamlit, un
# cierre de la app o una sesion distinta que abra la ficha para revision
# (`truth-loop.md` SS A: la UI necesita el `valor`, la `fuente`, la
# `confianza` Y el pixel exacto -- `Propuesta` entera, no solo `Campo` --
# para que Diego pueda confirmar sin volver a mirar las fotos originales).
#
# `deserializar_extraccion` NO reconstruye los dataclasses frozen
# (`Campo`/`Propuesta`/`Candidato`/`Lectura`/`Evidencia`): devuelve dicts
# anidados, que es lo que la UI necesita leer. Las rutas de `recorte` SI se
# devuelven como `Path` (son las que la UI abre con `Image.open`, no texto).
#
# Round-trip: `deserializar_extraccion(json.loads(json.dumps(serializar_extraccion(r))))`
# preserva valores, rutas de recorte, evidencias (fichero+bbox), lecturas,
# alternativas y motivos -- verificado en tests/test_extract.py.


def _evidencia_a_dict(evidencia: Evidencia | None) -> dict[str, Any] | None:
    if evidencia is None:
        return None
    return {
        "fichero": evidencia.fichero,
        "bbox": list(evidencia.bbox) if evidencia.bbox is not None else None,
    }


def _evidencia_desde_dict(datos: dict[str, Any] | None) -> dict[str, Any] | None:
    if datos is None:
        return None
    bbox = datos.get("bbox")
    return {
        "fichero": datos["fichero"],
        "bbox": tuple(bbox) if bbox is not None else None,
    }


def _lecturas_a_lista(lecturas: Sequence[Lectura]) -> list[list[str | None]]:
    return [[lectura.origen, lectura.texto] for lectura in lecturas]


def _lecturas_desde_lista(datos: Sequence[Sequence[str | None]]) -> list[dict[str, str | None]]:
    return [{"origen": origen, "texto": texto} for origen, texto in datos]


def _candidato_a_dict(candidato: Candidato) -> dict[str, Any]:
    return {
        "valor": candidato.valor,
        "recorte": str(candidato.recorte) if candidato.recorte is not None else None,
        "evidencia": _evidencia_a_dict(candidato.evidencia),
        "lecturas": _lecturas_a_lista(candidato.lecturas),
    }


def _candidato_desde_dict(datos: dict[str, Any]) -> dict[str, Any]:
    recorte = datos.get("recorte")
    return {
        "valor": datos.get("valor"),
        "recorte": Path(recorte) if recorte is not None else None,
        "evidencia": _evidencia_desde_dict(datos.get("evidencia")),
        "lecturas": _lecturas_desde_lista(datos.get("lecturas", [])),
    }


def _propuesta_a_dict(propuesta: Propuesta) -> dict[str, Any]:
    return {
        "campo": propuesta.campo,
        "valor": propuesta.valor,
        "recorte": str(propuesta.recorte) if propuesta.recorte is not None else None,
        "evidencia": _evidencia_a_dict(propuesta.evidencia),
        "lecturas": _lecturas_a_lista(propuesta.lecturas),
        "alternativas": [_candidato_a_dict(candidato) for candidato in propuesta.alternativas],
        "motivo": propuesta.motivo,
    }


def _propuesta_desde_dict(datos: dict[str, Any]) -> dict[str, Any]:
    recorte = datos.get("recorte")
    return {
        "campo": datos.get("campo"),
        "valor": datos.get("valor"),
        "recorte": Path(recorte) if recorte is not None else None,
        "evidencia": _evidencia_desde_dict(datos.get("evidencia")),
        "lecturas": _lecturas_desde_lista(datos.get("lecturas", [])),
        "alternativas": [_candidato_desde_dict(alt) for alt in datos.get("alternativas", [])],
        "motivo": datos.get("motivo", ""),
    }


def serializar_extraccion(resultado: ResultadoExtraccion) -> dict[str, Any]:
    """`ResultadoExtraccion` -> dict JSON-serializable (`json.dumps` SIN
    `default` custom -- todo lo que sale de aqui ya es `str`/`int`/`float`/
    `bool`/`None`/`list`/`dict`, nunca un `Path` ni un dataclass crudo).

    Por cada campo de `resultado.campos` (mismas claves que
    `CAMPOS_PRODUCIDOS`) preserva TODO lo que `ui/ficha.py` necesita para
    pintar el campo junto a su recorte: `valor`, `fuente`, `confianza`,
    `evidencia` (fichero + bbox como lista) Y la `Propuesta` completa
    (`valor`, `recorte`, `evidencia`, `lecturas`, `alternativas` -- cada
    una con su propio recorte -- y `motivo`). Si `resultado.propuestas` no
    trae ese campo (no deberia pasar en uso normal -- `ExtractorEngine`
    siempre entrega las mismas claves en `campos` y `propuestas`, pero esta
    funcion no confia en ese invariante sin comprobarlo), `propuesta` sale
    `None` en vez de reventar: quien serializa nunca debe perder el
    `Campo` (la parte exportable) porque falte la parte de revision.

    A nivel raiz: `coste_usd`, `fallos` (lista) y `aviso_coherencia`.
    """
    campos_serializados: dict[str, Any] = {}
    for nombre, campo in resultado.campos.items():
        propuesta = resultado.propuestas.get(nombre)
        campos_serializados[nombre] = {
            "valor": campo.valor,
            "fuente": campo.fuente,
            "confianza": campo.confianza,
            "evidencia": _evidencia_a_dict(campo.evidencia),
            "propuesta": _propuesta_a_dict(propuesta) if propuesta is not None else None,
        }
    return {
        "campos": campos_serializados,
        "coste_usd": resultado.coste_usd,
        "fallos": list(resultado.fallos),
        "aviso_coherencia": resultado.aviso_coherencia,
    }


def deserializar_extraccion(datos: dict[str, Any]) -> dict[str, Any]:
    """Inversa de `serializar_extraccion` -- NO reconstruye los dataclasses
    frozen (`Campo`/`Propuesta`/`Candidato`/`Lectura`/`Evidencia`): devuelve
    dicts anidados, comodos de consumir por la UI. Las rutas de `recorte`
    SI vuelven como `Path` (la UI las abre con `Image.open`, no las trata
    como texto).

    Robusta a claves ausentes (`.get(...)` con default) para que un JSON
    guardado por una version anterior del serializador no reviente al
    leerlo -- degrada a `None`/lista vacia, nunca lanza por una clave que
    falte."""
    campos: dict[str, Any] = {}
    for nombre, campo_datos in datos.get("campos", {}).items():
        propuesta_datos = campo_datos.get("propuesta")
        campos[nombre] = {
            "valor": campo_datos.get("valor"),
            "fuente": campo_datos.get("fuente"),
            "confianza": campo_datos.get("confianza"),
            "evidencia": _evidencia_desde_dict(campo_datos.get("evidencia")),
            "propuesta": _propuesta_desde_dict(propuesta_datos) if propuesta_datos is not None else None,
        }
    return {
        "campos": campos,
        "coste_usd": datos.get("coste_usd", 0.0),
        "fallos": list(datos.get("fallos", [])),
        "aviso_coherencia": datos.get("aviso_coherencia"),
    }


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
    """Checksum GS1 (EAN-8, UPC-12, EAN-13, GTIN-14) -- mismo algoritmo
    modulo-10 para las cuatro longitudes, contando pesos 3/1 alternando
    desde la DERECHA. Un EAN con un solo digito mal leido (el fallo nativo
    del OCR) NUNCA debe pasar como "identidad garantizada": `pricing.py` lo
    usa tal cual como termino de busqueda de comparables, y un digito
    equivocado trae el precio de OTRO producto. Si no valida, no es un EAN
    -- no hay excepcion. Es el UNICO camino de este modulo entero hacia
    `confianza="alta"` (ver docstring del modulo)."""
    if not digitos.isdigit() or len(digitos) not in _LONGITUDES_EAN_VALIDAS:
        return False
    cuerpo, check_esperado = digitos[:-1], int(digitos[-1])
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(cuerpo)))
    return (10 - total % 10) % 10 == check_esperado


def _validar_campo_ean(campo: Campo) -> Campo:
    """Defensa en profundidad: anula (a `valor=None`) un `Campo` de `ean`
    cuyo checksum no valida, venga de donde venga. Se aplica DESPUES de
    `_agregar_campo_texto` (que ya trabaja sobre candidatos pre-filtrados
    por `_filtrar_ean_checksum_valido`) como red de seguridad barata, no
    como el filtro primario. Preserva `fuente`/`evidencia` si las habia."""
    if campo.valor is None:
        return campo
    digitos = re.sub(r"\D", "", str(campo.valor))
    if digitos and _validar_checksum_ean(digitos):
        return campo
    return Campo(valor=None, fuente=campo.fuente, confianza="baja", evidencia=campo.evidencia)


def _intentar_atajo_ocr(region: RegionOCR) -> LecturaCrop | None:
    """Si el texto detectado por el OCR es limpio e inequivoco (EAN, o
    "Model:XXX" con score alto), se genera un candidato SIN gastar en el
    VLM -- ya se tiene el dato (D2, medido: 'EANCODE:*8445061029720*'
    score=0.91, 'Model:LLLT-200' score=0.88). Devuelve un `LecturaCrop`
    (origen="atajo_ocr") que entra al MISMO pool de candidatos que las
    lecturas del VLM -- `None` si no aplica ningun atajo (el recorte sigue
    su camino normal hacia el VLM).

    EAN: el atajo SOLO se acepta si el checksum GS1 valida -- un EAN con
    checksum invalido no es "el OCR ya lee limpio", es un digito mal leido,
    y no se cuela ni siquiera como candidato (no compite ni como conflicto).

    Modelo: YA NO produce `confianza="alta"` (esa via murio, docstring del
    modulo) -- es un candidato mas, capado a "media" cuando se agregue.
    """
    if region.score < UMBRAL_SCORE_OCR_LIMPIO:
        return None

    m = _EAN_OCR_RE.search(region.texto_ocr)
    if m and _validar_checksum_ean(m.group(1)):
        return LecturaCrop(
            fichero=region.fichero,
            bbox=region.bbox,
            legible=True,
            pertenece_al_producto=True,
            ubicacion="codigo_o_modelo_impreso",
            contenido_probable="ean",
            texto=m.group(1),
            texto_ocr_crudo=region.texto_ocr,
            origen="atajo_ocr",
        )

    m = _MODELO_OCR_RE.search(region.texto_ocr)
    if m:
        return LecturaCrop(
            fichero=region.fichero,
            bbox=region.bbox,
            legible=True,
            pertenece_al_producto=True,
            ubicacion="codigo_o_modelo_impreso",
            contenido_probable="modelo",
            texto=m.group(1),
            texto_ocr_crudo=region.texto_ocr,
            origen="atajo_ocr",
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


def _es_repeticion_de_un_campo_ya_resuelto(
    texto: str, candidatos_atajo: Sequence[LecturaCrop]
) -> bool:
    """Si este texto contiene, como substring, el valor de un `ean`/`modelo`
    que el atajo YA resolvio (D2), es una repeticion del mismo dato en
    otra parte de la caja (medido: el producto 1 imprime el EAN dos veces,
    una limpia ('EANCODE:*8445061029720*', capturada por el atajo) y otra
    junto a una referencia interna ('THO8LASLHR_UDS *8445061029720')). No
    aporta nada nuevo -- no hace falta gastar una llamada al VLM para
    confirmar un dato que ya se tiene como candidato."""
    for candidato in candidatos_atajo:
        if candidato.contenido_probable in ("ean", "modelo") and candidato.texto and candidato.texto in texto:
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


def _nombre_recorte(fichero: str, bbox: tuple[int, int, int, int]) -> str:
    """Nombre trazable del recorte guardado en disco: de que foto sale y
    que region exacta -- se puede reconstruir a mano el origen sin abrir
    ningun indice."""
    x, y, w, h = bbox
    return f"{Path(fichero).stem}_{x}-{y}-{w}-{h}.jpg"


def _guardar_recorte(
    bytes_: bytes, carpeta: Path, fichero: str, bbox: tuple[int, int, int, int]
) -> Path:
    """Escribe `bytes_` (los MISMOS bytes que se mandaron o se mandarian al
    VLM -- nunca un recorte re-generado aparte, ver docstring del modulo,
    punto 3) a `carpeta/<nombre trazable>.jpg`. Determinista: el mismo
    fichero+bbox siempre produce el mismo nombre, asi que volver a extraer
    el mismo producto sobreescribe el mismo fichero en vez de acumular
    basura."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / _nombre_recorte(fichero, bbox)
    ruta.write_bytes(bytes_)
    return ruta


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
    ya localizado por el OCR) a coordenadas del recorte."""
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

    `bboxes_excluir` (en coordenadas de la imagen ORIGINAL, formato
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
    """sRGB (D65) -> CIE Lab, formula estandar. Lab separa luminosidad (L)
    de crominancia (a, b) -- a diferencia de la distancia euclidiana en
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
    euclidiana en espacio Lab, no RGB -- SIEMPRE uno de esa lista cerrada,
    nunca una descripcion libre inventada.

    Limite honesto que esto NO arregla: un negro sobreexpuesto por la
    autoexposicion del movil a RGB~(80,80,80) sigue siendo, en CUALQUIER
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

Puede haber MAS DE UN dato distinto en este mismo recorte (por ejemplo, una
etiqueta de cuello que trae la marca Y la talla juntas, o una caja que trae
el modelo Y el codigo de barras). Reporta TODOS los que veas por separado en
la lista `hallazgos` -- no elijas solo el primero.

Responde:
- pertenece_al_producto: false si este recorte es de un objeto de FONDO
  ajeno al producto (por ejemplo, las specs de un portatil que sale detras
  en el encuadre). true si es del propio producto o de una nota/papel que
  el vendedor puso junto al producto. Aplica a TODO el recorte.
- ubicacion: elige UNA para todo el recorte (es una unica superficie fisica):
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
- hallazgos: una lista, MINIMO un elemento. Cada elemento:
  - contenido_probable: tu mejor estimacion de que TIPO de informacion es
    este hallazgo, incluso si legible=false (por el contexto visual:
    posicion, estilo, tamano de letra). Elige UNA: "marca", "talla",
    "modelo", "ean", "desperfecto" (una nota describiendo un dano/defecto),
    "otro".
  - legible: true solo si puedes leer el texto de ESTE hallazgo con
    seguridad. false es una respuesta CORRECTA y ESPERADA si el texto esta
    borroso, ocluido, en angulo imposible, o tapado por algo (un cable, un
    dedo, un pliegue). NO completes lo plausible: si no se lee con
    seguridad, legible=false y texto=null, aunque creas adivinar que dice.
  - texto: la transcripcion EXACTA de ESTE hallazgo si legible=true; null
    si legible=false. Si hay dos hallazgos distintos (marca y talla), cada
    uno lleva su propio texto, no los mezcles en uno.

Si no hay nada reconocible en el recorte, devuelve un unico hallazgo con
contenido_probable="otro", legible=false, texto=null.

Responde SOLO el JSON pedido."""

ESQUEMA_LECTURA_CROP: dict = {
    "type": "object",
    "properties": {
        "pertenece_al_producto": {"type": "boolean"},
        "ubicacion": {"type": "string", "enum": sorted(_UBICACIONES_VALIDAS)},
        "hallazgos": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "contenido_probable": {"type": "string", "enum": sorted(_CONTENIDOS_VALIDOS)},
                    "legible": {"type": "boolean"},
                    "texto": {"type": ["string", "null"]},
                },
                "required": ["contenido_probable", "legible", "texto"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pertenece_al_producto", "ubicacion", "hallazgos"],
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
# ETAPA 4B -- LA SINTESIS COMPROMETIDA (ver docstring del modulo): UNA
# llamada mas, sobre el producto ENTERO + los candidatos ya detectados, que
# PROPONE un valor comprometido por campo y redacta titulo/descripcion.
# EAN y desperfectos quedan fuera a proposito -- no estan en este esquema.
# ============================================================================

# Los 6 campos de identidad/atributo que la sintesis puede rellenar.
# "material" (-> "composicion") ELIMINADO de este esquema (Diego,
# 2026-07-17): solo aplicaba a ropa y no aportaba valor a su flujo. Un
# campo menos que pedir al modelo, pero el numero de LLAMADAS no cambia
# (sigue siendo UNA sintesis) -- `LLMEngine.estimar_coste_lote` usa un
# coste FIJO por imagen/llamada, no por tamano del json_schema, asi que
# el coste ESTIMADO que ve Diego antes de gastar no varia; lo que baja es
# el coste REAL (prompt mas corto, un objeto menos en la respuesta JSON),
# demasiado pequeno para verse en la cifra redondeada que muestra la UI.
_CAMPOS_SINTESIS: tuple[str, ...] = (
    "marca", "modelo", "talla", "color", "estado", "medidas"
)

PROMPT_SINTESIS_FICHA = """Estas viendo UN producto de segunda mano (varias fotos
GENERALES del mismo articulo, no recortes). Tu trabajo es PROPONER la mejor
ficha posible para que un humano (Diego) la revise y corrija en segundos --
NO te abstengas: para cada campo da tu MEJOR estimacion aunque no estes
100% seguro. Un valor con confianza baja que Diego corrige en 2 segundos es
mejor que un hueco vacio que Diego tiene que teclear desde cero.

IGNORA cualquier texto u objeto de FONDO ajeno a este producto (otro
articulo, un portatil, una caja de otro objeto en el encuadre) -- solo te
interesa ESTE producto.

Ademas de las fotos, aqui tienes los textos que YA se detectaron en
recortes de etiquetas/estampados/codigos de este mismo producto (leidos por
un paso de OCR/vision anterior -- no los inventes ni los cambies si los
usas, cita el texto EXACTO):

{texto_candidatos}

Para cada uno de estos 6 campos -- marca, modelo, talla, color, estado,
medidas -- responde un objeto con:
  - valor: tu mejor estimacion en texto corto, o null SOLO si no tienes
    ninguna base razonable ni siquiera para inferir (esto debe ser RARO:
    se prefiere un valor con confianza=baja a un null, porque Diego lo
    revisa de todas formas antes de publicar).
  - visible_en_foto: true UNICAMENTE si el valor que propones se LEE
    LITERALMENTE en un texto/etiqueta del producto (de la lista de arriba,
    o algo que tu mismo leas con seguridad en la foto). false si es una
    INFERENCIA tuya (a ojo, por estilo, por contexto, por como cae la
    prenda) -- por ejemplo "parece algodon" o "parece talla M por como
    cae" son SIEMPRE false. NUNCA pongas true si no puedes senalar el
    texto exacto que lo demuestra.
  - de_texto_detectado: si visible_en_foto=true y tu valor viene de la
    lista de textos detectados de arriba, copia EXACTAMENTE ese texto aqui
    (para poder ensenarle a Diego el recorte del que salio). null en
    cualquier otro caso (incluido si lo leiste tu mismo en la foto general,
    fuera de la lista).
  - confianza: "media" si tienes bases razonables, "baja" si es una
    estimacion mas debil. NUNCA "alta" -- eso solo lo decide una regla
    matematica verificable (un codigo de barras), nunca tu.

NUNCA propongas un valor que CONTRADIGA algo visible (si un texto
detectado dice claramente "Nike", no propongas "Adidas").

DOS campos con reglas ESPECIALES (no des una frase libre en ellos):
  - estado: usa EXACTAMENTE uno de estos seis valores, nada mas: "Nuevo",
    "Como nuevo", "Muy bueno", "Bueno", "Satisfactorio", "Para reparar".
    Ante la duda elige el MAS BAJO (mas conservador). Para estado,
    visible_en_foto es SIEMPRE false (es un juicio, no un texto que se lea).
  - medidas: SOLO una dimension real en cm que puedas ver medida con un
    metro/regla en la foto (p.ej. "largo 70 cm"). Si no hay ninguna medida
    tomada a la vista, pon null -- NO metas aqui una descripcion del
    producto ni una medida inventada.

Ademas, clasifica el TIPO de producto (decide que campos estructurados le
va a pedir la plataforma -- Moda pide talla, Electronica pide capacidad,
etc.):
  - categoria: EXACTAMENTE uno de estos cinco valores, ninguno mas:
    "moda" (ropa, calzado, complementos), "electronica" (dispositivos,
    accesorios electronicos), "hogar" (muebles, decoracion, menaje,
    utensilios), "libros", "otros" (cualquier cosa que no encaje
    claramente en las anteriores -- deportes, juguetes, herramientas...).
    Esto es SIEMPRE un juicio tuyo, nunca un texto que se lea en una
    etiqueta -- no hay "visible_en_foto" para este campo.

Ademas, redacta:
  - titulo: un titulo de venta corto y honesto para Wallapop/Vinted (maximo
    100 caracteres), sin mayusculas excesivas, sin emojis en ristra, sin
    mencionar ninguna marca distinta de la que propusiste.
  - descripcion: una descripcion de venta corta (maximo 600 caracteres),
    en espanol, sin emails, sin enlaces, sin mayusculas excesivas, sin
    ristras de simbolos/emojis, mencionando solo la marca que propusiste
    (si alguna) -- honesta sobre lo que se ve, sin inventar caracteristicas
    que no puedas justificar con lo que ves.

Responde SOLO el JSON pedido."""


def _construir_prompt_sintesis(texto_candidatos: str) -> str:
    """Rellena `PROMPT_SINTESIS_FICHA` con la lista de candidatos ya
    detectados -- funcion separada porque `LLMEngine._clave_cache` hashea
    el TEXTO REAL del prompt (C6), asi que `construir_solicitudes` y
    `extraer_producto` tienen que construir el prompt exactamente igual
    para que la estimacion de coste y la llamada real usen la misma
    clave de cache cuando el texto de candidatos coincide."""
    return PROMPT_SINTESIS_FICHA.format(texto_candidatos=texto_candidatos)


def _esquema_campo_sintesis() -> dict:
    return {
        "type": "object",
        "properties": {
            "valor": {"type": ["string", "null"]},
            "visible_en_foto": {"type": "boolean"},
            "de_texto_detectado": {"type": ["string", "null"]},
            "confianza": {"type": "string", "enum": ["media", "baja"]},  # NUNCA "alta"
        },
        "required": ["valor", "visible_en_foto", "de_texto_detectado", "confianza"],
        "additionalProperties": False,
    }


ESQUEMA_SINTESIS_FICHA: dict = {
    "type": "object",
    "properties": {
        **{campo: _esquema_campo_sintesis() for campo in _CAMPOS_SINTESIS},
        # "categoria" NO usa `_esquema_campo_sintesis()` -- no es un texto
        # legible con visible_en_foto/de_texto_detectado, es un ENUM CERRADO
        # de clasificacion (`core.schema.CATEGORIAS`). El enum en el
        # json_schema ya restringe la respuesta a nivel de API; el codigo
        # (`_construir_campo_categoria_desde_sintesis`) NUNCA confia solo en
        # eso -- vuelve a validar con `es_categoria_valida`.
        "categoria": {"type": "string", "enum": list(CATEGORIAS)},
        "titulo": {"type": "string"},
        "descripcion": {"type": "string"},
    },
    "required": [*_CAMPOS_SINTESIS, "categoria", "titulo", "descripcion"],
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
) -> list[LecturaCrop]:
    """Convierte el dict crudo del VLM en una LISTA de `LecturaCrop` -- una
    por cada hallazgo (regla dura #4: un mismo recorte puede traer varios
    datos, p.ej. marca Y talla en la misma etiqueta de cuello).

    Aplica el contrato minimo y la red de seguridad de la regla dura #3:
    si `legible` de un hallazgo es false, su `texto` se fuerza a `None` EN
    CODIGO sin importar lo que el modelo haya puesto ahi.

    `texto_ocr_crudo`: lo que el OCR local leyo en esta MISMA region antes
    de mandarla al VLM -- se traslada a CADA `LecturaCrop` resultante (ya
    no concede confianza, solo alimenta el aviso "dudoso" informativo, ver
    docstring del modulo)."""
    for clave in ("pertenece_al_producto", "ubicacion", "hallazgos"):
        if clave not in datos:
            raise RespuestaVLMInvalidaError(f"falta la clave {clave!r} en la respuesta del VLM: {datos!r}")

    if datos["ubicacion"] not in _UBICACIONES_VALIDAS:
        raise RespuestaVLMInvalidaError(f"ubicacion desconocida: {datos['ubicacion']!r}")

    hallazgos = datos["hallazgos"]
    if not isinstance(hallazgos, list) or not hallazgos:
        raise RespuestaVLMInvalidaError(f"'hallazgos' debe ser una lista no vacia: {datos!r}")

    pertenece = bool(datos["pertenece_al_producto"])
    ubicacion = datos["ubicacion"]
    resultado: list[LecturaCrop] = []

    for hallazgo in hallazgos:
        for clave in ("contenido_probable", "legible", "texto"):
            if clave not in hallazgo:
                raise RespuestaVLMInvalidaError(f"falta la clave {clave!r} en un hallazgo: {hallazgo!r}")
        if hallazgo["contenido_probable"] not in _CONTENIDOS_VALIDOS:
            raise RespuestaVLMInvalidaError(
                f"contenido_probable desconocido: {hallazgo['contenido_probable']!r}"
            )

        legible = bool(hallazgo["legible"])
        texto = hallazgo["texto"] if legible else None
        if texto is not None and not str(texto).strip():
            texto = None

        resultado.append(
            LecturaCrop(
                fichero=fichero,
                bbox=bbox,
                legible=legible,
                pertenece_al_producto=pertenece,
                ubicacion=ubicacion,
                contenido_probable=hallazgo["contenido_probable"],
                texto=str(texto).strip() if texto is not None else None,
                texto_ocr_crudo=texto_ocr_crudo,
                origen="vlm",
            )
        )

    return resultado


def _parsear_respuesta_sintesis(datos: dict) -> dict[str, Any]:
    """Convierte el dict crudo de la sintesis en un dict normalizado,
    aplicando el mismo contrato minimo defensivo que `_parsear_lectura_crop`
    (claves obligatorias, enum de `confianza` cerrado a "media"/"baja" --
    "alta" NUNCA sale de aqui, ni del json_schema ni de esta funcion).

    Normaliza `valor`/`de_texto_detectado` vacios o solo-espacios a `None`
    (mismo patron que `_parsear_lectura_crop` con `texto`).

    `categoria` se exige PRESENTE (estructura minima), pero su ENUM se
    valida mas abajo, en `_construir_campo_categoria_desde_sintesis` --
    no aqui. Si se validara aqui y se lanzara, un valor de categoria fuera
    de enum tumbaria la sintesis ENTERA (marca/talla/color/... y titulo/
    descripcion con ella) por culpa de un campo que tiene su propio camino
    de fallo no-fatal (decision-making.md SS13: la clave simplemente no se
    anade a `campos`, nunca aborta el resto)."""
    claves_requeridas = (*_CAMPOS_SINTESIS, "categoria", "titulo", "descripcion")
    for clave in claves_requeridas:
        if clave not in datos:
            raise RespuestaVLMInvalidaError(
                f"falta la clave {clave!r} en la respuesta de sintesis: {datos!r}"
            )

    resultado: dict[str, Any] = {}
    for campo in _CAMPOS_SINTESIS:
        bloque = datos[campo]
        if not isinstance(bloque, dict):
            raise RespuestaVLMInvalidaError(f"el campo {campo!r} de la sintesis no es un objeto: {bloque!r}")
        for clave in ("valor", "visible_en_foto", "de_texto_detectado", "confianza"):
            if clave not in bloque:
                raise RespuestaVLMInvalidaError(
                    f"falta la clave {clave!r} en el campo {campo!r} de la sintesis: {bloque!r}"
                )
        if bloque["confianza"] not in ("media", "baja"):
            raise RespuestaVLMInvalidaError(
                f"confianza invalida en el campo {campo!r} de la sintesis: {bloque['confianza']!r}"
            )

        valor = bloque["valor"]
        valor = str(valor).strip() if valor is not None and str(valor).strip() else None
        de_texto = bloque["de_texto_detectado"]
        de_texto = str(de_texto).strip() if de_texto is not None and str(de_texto).strip() else None

        resultado[campo] = {
            "valor": valor,
            "visible_en_foto": bool(bloque["visible_en_foto"]),
            "de_texto_detectado": de_texto,
            "confianza": bloque["confianza"],
        }

    # Se pasa CRUDO (sin normalizar/validar enum): ver docstring de arriba.
    resultado["categoria"] = datos["categoria"]
    resultado["titulo"] = str(datos["titulo"]).strip()
    resultado["descripcion"] = str(datos["descripcion"]).strip()
    return resultado


# ============================================================================
# ETAPA 6 -- AGREGACION: de lecturas de recorte a `_GrupoCampo` (Campo +
# lo necesario para construir la Propuesta). Aqui viven las reglas duras
# #1 y #2, EN CODIGO -- y la ley nueva: confianza NUNCA "alta" salvo EAN.
# ============================================================================


def _candidatos_legibles(lecturas: Sequence[LecturaCrop], contenido: ContenidoProbable) -> list[LecturaCrop]:
    """TODAS las lecturas legibles de este `contenido` -- SIN filtrar por
    `ubicacion` (la ubicacion prioriza y explica que se PUBLICA, pero nunca
    debe borrar evidencia antes de comprobar si hay un conflicto real).
    Exige `pertenece_al_producto`, `legible`, `texto` no vacio y
    `contenido_probable == contenido`."""
    return [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto and lectura.legible and lectura.texto and lectura.contenido_probable == contenido
    ]


def _intentos_de_campo(
    lecturas: Sequence[LecturaCrop],
    contenido: ContenidoProbable,
    ubicaciones_validas: frozenset[str],
) -> list[LecturaCrop]:
    """Recortes que el VLM (o el atajo OCR) clasifico como sobre `contenido`
    (en una ubicacion valida) AUNQUE no fueran legibles -- distingue
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
    sin nada en comun. YA NO concede confianza (ver docstring del modulo);
    solo alimenta el aviso "dudoso" informativo en el motivo de una
    Propuesta. Calibracion y pares reales junto a `UMBRAL_SIMILITUD_DUDOSO`."""
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


@dataclass(frozen=True)
class _GrupoCampo:
    """Resultado PURO (sin tocar disco ni abrir imagenes) de agregar los
    candidatos de un campo de texto -- el `Campo` ya listo para exportar,
    mas lo que hace falta para construir la `Propuesta` una vez que quien
    llama tenga acceso a las imagenes abiertas (`ExtractorEngine`).

    `representante`: el `LecturaCrop` cuyo recorte se convierte en
        `Propuesta.recorte` -- puede ser un candidato NO publicable (p.ej.
        un estampado sin etiqueta que lo respalde): se ensena igual, solo
        que `campo.valor` sigue siendo `None` (regla dura #1). `None` si no
        hay ningun recorte razonable que mostrar.
    `alternativas`: un `LecturaCrop` por cada candidato en CONFLICTO
        (>1 valor normalizado distinto) -- vacio si no hubo conflicto.
    `motivo`: explicacion corta y legible para Diego.
    """

    campo: Campo
    representante: LecturaCrop | None
    alternativas: tuple[LecturaCrop, ...] = ()
    motivo: str = ""


def _agregar_campo_texto(
    lecturas: Sequence[LecturaCrop],
    contenido: ContenidoProbable,
    ubicaciones_validas: frozenset[str],
) -> _GrupoCampo:
    """El nucleo de las reglas duras #1 y #2, mas la ley nueva de confianza
    (techo "media" -- nunca "alta" desde una lectura de modelo/OCR).

    Paso 1: agrupa TODOS los candidatos legibles de `contenido`, SIN
    filtrar por ubicacion -- un conflicto real (dos marcas legibles) tiene
    que verse aunque una de ellas venga de un estampado. Si hay >1 valor
    normalizado distinto, es un conflicto: `None` + `baja` + TODAS las
    candidatas en `alternativas` (cada una se convierte en su propio
    `Candidato` con recorte en `ExtractorEngine._propuesta_desde_grupo`).

    Paso 2: si sobrevive UN solo valor, la ubicacion decide si es
    PUBLICABLE (regla dura #1). Si NO lo es (solo estampado, sin ninguna
    etiqueta), `campo.valor` queda `None` pero el recorte SI se ensena via
    `representante` -- "el extractor no afirma, propone y ensena el pixel".

    Paso 3: si es publicable, `confianza="media"` SIEMPRE -- nunca "alta".
    La similitud OCR<->VLM, si hay testigo y no cuadra, solo anade un aviso
    informativo al `motivo` (nunca toca `confianza`)."""
    grupos: dict[str, list[LecturaCrop]] = {}
    for lectura in _candidatos_legibles(lecturas, contenido):
        clave = lectura.texto.strip().lower()  # type: ignore[union-attr]
        grupos.setdefault(clave, []).append(lectura)

    if len(grupos) > 1:
        representantes = tuple(miembros[0] for miembros in grupos.values())
        motivo = (
            f"conflicto: {len(grupos)} valores distintos legibles para "
            f"'{contenido}' -- el pipeline NUNCA elige, cada uno con su "
            "propio recorte para que Diego decida"
        )
        campo = Campo(valor=None, fuente="inferido", confianza="baja")
        return _GrupoCampo(campo=campo, representante=None, alternativas=representantes, motivo=motivo)

    if len(grupos) == 1:
        (miembros,) = grupos.values()
        candidatos_validos = [m for m in miembros if m.ubicacion in ubicaciones_validas]
        if candidatos_validos:
            representante = candidatos_validos[0]
            motivo = f"unico valor legible en ubicacion publicable ({representante.ubicacion})"
            if representante.origen == "vlm" and _es_testigo_valido(representante.texto_ocr_crudo):
                similitud = _similitud_normalizada(
                    representante.texto_ocr_crudo, representante.texto  # type: ignore[arg-type]
                )
                if similitud < UMBRAL_SIMILITUD_DUDOSO:
                    motivo += (
                        f"; OJO -- dudoso: el OCR crudo de esta region leyo "
                        f"{representante.texto_ocr_crudo!r}, que no se parece a la "
                        f"lectura del VLM ({representante.texto!r}). Revisa el recorte "
                        "con cuidado antes de confirmar (senal de orden, no de confianza)"
                    )
            campo = Campo(
                valor=representante.texto,
                fuente="foto",
                confianza="media",
                evidencia=Evidencia(fichero=representante.fichero, bbox=representante.bbox),
            )
            return _GrupoCampo(campo=campo, representante=representante, alternativas=(), motivo=motivo)

        # Regla dura #1: el UNICO valor legible viene solo de una ubicacion
        # NO valida (p.ej. solo estampado, sin ninguna etiqueta_interior) --
        # no se publica, pero SI se ensena su recorte para que Diego juzgue.
        no_publicable = miembros[0]
        intentos = _intentos_de_campo(lecturas, contenido, ubicaciones_validas)
        if intentos:
            primero = intentos[0]
            campo = Campo(
                valor=None, fuente="foto", confianza="baja",
                evidencia=Evidencia(fichero=primero.fichero, bbox=primero.bbox),
            )
        else:
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
        motivo = (
            f"unico valor legible ({no_publicable.texto!r}) pero SOLO en ubicacion "
            f"no publicable ({no_publicable.ubicacion}) -- un estampado/grafico nunca "
            "publica esto por si solo (regla dura #1); se ensena igual para que Diego lo mire"
        )
        return _GrupoCampo(campo=campo, representante=no_publicable, alternativas=(), motivo=motivo)

    # Sin candidatos PUBLICABLES: distinguir PRESENTE_ILEGIBLE de NO_FOTOGRAFIADO.
    intentos = _intentos_de_campo(lecturas, contenido, ubicaciones_validas)
    if intentos:
        primero = intentos[0]
        campo = Campo(
            valor=None, fuente="foto", confianza="baja",
            evidencia=Evidencia(fichero=primero.fichero, bbox=primero.bbox),
        )
        return _GrupoCampo(
            campo=campo, representante=primero, alternativas=(),
            motivo="presente en foto pero no se pudo leer con seguridad (PRESENTE_ILEGIBLE)",
        )

    return _GrupoCampo(
        campo=Campo(valor=None, fuente="inferido", confianza="baja"),
        representante=None, alternativas=(),
        motivo="ninguna foto de este grupo trae este dato (NO_FOTOGRAFIADO)",
    )


def _filtrar_ean_checksum_valido(lecturas: Sequence[LecturaCrop]) -> list[LecturaCrop]:
    """Deja pasar cualquier lectura que NO sea de `ean` tal cual; para las
    que SI son de `ean`, exige que el checksum GS1 valide (extrayendo solo
    los digitos) -- si no valida, la lectura se DESCARTA ENTERA: un EAN con
    checksum invalido no es un EAN real (C5), no debe competir ni siquiera
    como candidata de un conflicto. Es el filtro que unifica el atajo OCR y
    las lecturas del VLM en el MISMO camino (ya no hay carretera paralela
    que bypasee la deteccion de conflictos, ver docstring del modulo)."""
    resultado: list[LecturaCrop] = []
    for lectura in lecturas:
        if lectura.contenido_probable != "ean" or not lectura.legible or not lectura.texto:
            resultado.append(lectura)
            continue
        digitos = re.sub(r"\D", "", lectura.texto)
        if digitos and _validar_checksum_ean(digitos):
            resultado.append(replace(lectura, texto=digitos))
        # si no valida, se descarta -- no es un EAN real, no compite.
    return resultado


def _agregar_campo_desperfectos(lecturas: Sequence[LecturaCrop]) -> _GrupoCampo:
    """Regla dura #6: un papel manuscrito en el grupo es una NOTA que Diego
    puso junto al producto -- pero el texto SI esta en el pixel (es una
    transcripcion, no algo que Diego tecleara en la app), asi que
    `fuente="foto"` con su `evidencia`.

    Confianza: SIEMPRE "media", incluso si la MISMA nota aparece en varias
    fotos distintas -- la via "multi-foto -> alta" murio con el resto de
    la maquinaria de corroboracion (ver docstring del modulo, ley 1: nada
    que no sea EAN con checksum llega a "alta"). Varias notas DISTINTAS se
    concatenan (defectos reales pueden coexistir), no compiten como
    conflicto."""
    candidatas = [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto
        and lectura.ubicacion == "papel_manuscrito"
        and lectura.legible
        and lectura.texto
    ]
    if not candidatas:
        return _GrupoCampo(
            campo=Campo(valor=None, fuente="inferido", confianza="baja"),
            representante=None, alternativas=(),
            motivo="sin papel manuscrito en el grupo",
        )

    notas_unicas = list(dict.fromkeys(lectura.texto for lectura in candidatas))  # dedup preservando orden
    valor = "; ".join(notas_unicas)
    primera = candidatas[0]
    campo = Campo(
        valor=valor, fuente="foto", confianza="media",
        evidencia=Evidencia(fichero=primera.fichero, bbox=primera.bbox),
    )
    motivo = (
        "nota manuscrita transcrita de una foto -- es la transcripcion de un "
        "pixel real, no un dato tecleado; confirmala siempre"
    )
    return _GrupoCampo(campo=campo, representante=primera, alternativas=(), motivo=motivo)


# `_campo_composicion()` ELIMINADA (Diego, 2026-07-17): el campo
# "composicion" entero salio de la ficha -- ver la regla dura #4 del
# docstring del modulo. `core/export.py::_campo_composicion` (funcion
# DISTINTA, del modulo de export) sigue viva y degrada sola a `valor=None`
# porque la clave "composicion" ya nunca esta en `campos`.


# ============================================================================
# ETAPA 6B -- LA SINTESIS COMPROMETIDA: helpers puros (formatear candidatos
# para el prompt, indexarlos para poder ligar `de_texto_detectado` a su
# recorte, y traducir UNA decision de sintesis a un `Campo`. La llamada
# real y el gap-filler viven en `ExtractorEngine._sintetizar_ficha`.
# ============================================================================


def _formatear_candidatos_para_sintesis(lecturas: Sequence[LecturaCrop]) -> str:
    """Lista en texto de TODO lo que el OCR/VLM ya detecto en los crops de
    este producto, para que la sintesis vea CANDIDATOS reales, no tenga
    que inventarselos -- etiquetados con su `ubicacion` para que el propio
    prompt pueda razonar sobre cual es publicable (aunque la regla dura #1
    se aplica EN CODIGO despues, no solo se le pide amablemente al modelo).

    Solo entran lecturas LEGIBLES, con texto, que pertenecen al producto
    (regla dura #7 -- el fondo ajeno no debe llegar ni como candidato de
    contexto). Deduplica preservando orden -- una region fusionada y su
    atajo pueden repetir el mismo texto."""
    candidatos = [
        lectura
        for lectura in lecturas
        if lectura.pertenece_al_producto and lectura.legible and lectura.texto
    ]
    if not candidatos:
        return "(el paso de OCR/vision anterior no detecto ningun texto candidato en las fotos de este producto)"

    lineas = [f"- [{lectura.ubicacion}] {lectura.contenido_probable}: {lectura.texto!r}" for lectura in candidatos]
    return "\n".join(dict.fromkeys(lineas))  # dedup preservando orden


def _indexar_candidatos_por_texto(lecturas: Sequence[LecturaCrop]) -> dict[str, LecturaCrop]:
    """`texto normalizado -> LecturaCrop` para ligar `de_texto_detectado`
    (lo que la sintesis dice haber usado) a SU recorte real -- el mismo
    filtro que `_formatear_candidatos_para_sintesis` (legible, con texto,
    pertenece al producto), asi que todo lo que la sintesis puede CITAR es
    exactamente lo que se le enseno en el prompt. La primera ocurrencia
    gana (determinista) si dos lecturas comparten texto normalizado."""
    indice: dict[str, LecturaCrop] = {}
    for lectura in lecturas:
        if lectura.pertenece_al_producto and lectura.legible and lectura.texto:
            clave = lectura.texto.strip().lower()
            if clave not in indice:
                indice[clave] = lectura
    return indice


def _construir_campo_desde_sintesis(
    decision: dict[str, Any],
    indice_candidatos: dict[str, LecturaCrop],
    ubicaciones_validas: frozenset[str] | None,
) -> tuple[Campo, LecturaCrop | None]:
    """Traduce UNA decision de sintesis (para UN campo) a un `Campo`, mas
    el `LecturaCrop` que la respalda (si lo hay) para poder ensenar su
    recorte -- devuelve `(Campo(valor=None,...), None)` si la sintesis
    tampoco tuvo opinion (valor=None): quien llama debe entonces dejar el
    campo previo intacto (gap-filler, no lo sustituye por un None nuevo).

    Regla dura #1 (estampado != marca EN LA PUBLICACION) se aplica TAMBIEN
    aqui: si la sintesis dice `visible_en_foto=true` pero el candidato que
    cita viene de una `ubicacion` NO publicable para este campo (p.ej. un
    `estampado_o_grafico` para `marca`), NUNCA se publica como
    `fuente="foto"` -- se degrada a "inferido", la misma disciplina de
    "un prompt es una peticion, un if es una garantia" del resto del
    modulo. Lo mismo si `de_texto_detectado` no casa con NINGUN candidato
    real (una cita que no existe no es evidencia, aunque el modelo diga
    `visible_en_foto=true`): sin candidato, no hay recorte que ensenar, y
    "foto" sin evidencia real es exactamente el bug que `Campo` bloquea en
    su `__post_init__` -- aqui se evita ANTES de construirlo."""
    valor = decision["valor"]
    if valor is None:
        return Campo(valor=None, fuente="inferido", confianza="baja"), None

    candidato: LecturaCrop | None = None
    if decision["de_texto_detectado"]:
        candidato = indice_candidatos.get(decision["de_texto_detectado"].strip().lower())

    ubicacion_publicable = candidato is not None and (
        ubicaciones_validas is None or candidato.ubicacion in ubicaciones_validas
    )

    # GARANTIA DE PROCEDENCIA (truth-loop.md SS A.1; hallazgo BLOQUEANTE del
    # listing-audit). `fuente="foto"` exige que el VALOR propuesto este
    # CONTENIDO en el texto LEGIBLE del candidato citado -- no basta con que
    # la cita exista. Si el modelo EXTIENDE una lectura real ("Reebok" ->
    # "Reebok Classic 100% algodon") o la sustituye ("Nike" citando un crop
    # de "Reebok"), la parte que no esta en el pixel NO es legible: se
    # degrada a "inferido" (el crop pasa a ser CONTEXTO, no evidencia). Antes
    # el codigo ligaba el recorte a la cita pero nunca comprobaba el valor:
    # "un if es una garantia", el docstring lo prometia y no lo forzaba.
    valor_en_el_pixel = (
        candidato is not None
        and candidato.texto is not None
        and valor.strip().lower() in candidato.texto.strip().lower()
    )

    if decision["visible_en_foto"] and ubicacion_publicable and valor_en_el_pixel:
        assert candidato is not None  # ubicacion_publicable ya lo exige
        campo = Campo(
            valor=valor,
            fuente="foto",
            confianza=decision["confianza"],  # ya viene acotado a "media"/"baja" por el json_schema
            evidencia=Evidencia(fichero=candidato.fichero, bbox=candidato.bbox),
        )
        return campo, candidato

    # Inferido: sin candidato real que lo respalde, la ubicacion no es
    # publicable para este campo, o la propia sintesis lo marco como su
    # inferencia -- techo "baja" SIEMPRE (nunca lo que diga el modelo).
    campo = Campo(valor=valor, fuente="inferido", confianza="baja")
    return campo, candidato  # candidato puede servir de recorte de CONTEXTO


def _construir_campo_categoria_desde_sintesis(valor: Any) -> Campo | None:
    """Traduce el `categoria` crudo de la sintesis a un `Campo`, o `None`
    si `valor` no es uno de los `CategoriaTipo` conocidos
    (`core.schema.es_categoria_valida`).

    A diferencia de `_construir_campo_desde_sintesis`, aqui NO hay camino
    a `fuente="foto"`: una categoria es una CLASIFICACION del producto
    entero, nunca un texto legible en un pixel concreto -- no tiene
    `visible_en_foto`/`de_texto_detectado` en el json_schema (ver
    `ESQUEMA_SINTESIS_FICHA`). Por eso sale SIEMPRE `fuente="inferido"`,
    techo `confianza="baja"` -- es un juicio del modelo, Diego lo confirma
    en la UI con un selectbox, igual que `estado`.

    `None` cuando el valor no valida (el modelo violo el enum cerrado del
    json_schema, o un mock de test manda basura): quien llama NO debe
    inventar un valor de relleno (p.ej. "otros" como comodin) -- eso seria
    exactamente el fallback silencioso que `decision-making.md` SS13
    prohibe. Debe anotar el fallo en `ResultadoExtraccion.fallos` y dejar
    la clave "categoria" AUSENTE de `campos` (unica excepcion documentada
    a "CAMPOS_PRODUCIDOS siempre presente")."""
    if not es_categoria_valida(valor):
        return None
    return Campo(valor=valor, fuente="inferido", confianza="baja")


# nombre_del_campo_en_el_json_schema -> (nombre_canonico_del_modulo, ubicaciones_validas)
# EAN, desperfectos y "material" (composicion, ELIMINADA 2026-07-17) quedan
# fuera A PROPOSITO -- no estan en `_CAMPOS_SINTESIS`.
_CAMPOS_SINTESIS_GAP: dict[str, tuple[str, frozenset[str] | None]] = {
    "marca": ("marca", _UBICACIONES_VALIDAS_MARCA),
    "modelo": ("modelo", _UBICACIONES_VALIDAS_MODELO),
    "talla": ("talla", _UBICACIONES_VALIDAS_TALLA),
    "color": ("color", None),  # sin restriccion de ubicacion -- el color no viene de un texto
    "estado": ("estado", None),
    "medidas": ("medidas", None),
}


# ============================================================================
# COHERENCIA entre campos de IDENTIDAD -- "LA FICHA FRANKENSTEIN"
# ============================================================================


def _detectar_fotos_disjuntas(campos: dict[str, Campo]) -> str | None:
    """Si un grupo trae fotos de DOS productos (una fusion que Diego no
    caza al curar), la marca puede salir de una prenda y la talla de otra
    -- las dos con evidencia REAL y LEGIBLE, asi que la capa de
    legibilidad las deja pasar LIMPIAS: cada dato es cierto, solo que no
    son del mismo producto.

    Senal (barata, sin VLM): construye un grafo cuyos NODOS son los campos
    de identidad (marca/talla/modelo/ean) que tienen valor, y cuyas ARISTAS
    unen dos campos cuando ALGUNA foto aporta evidencia de ambos a la vez.
    Si TODOS los campos con valor caen en la MISMA componente conexa, no
    hay senal de riesgo. Si hay mas de una componente, es la firma de una
    fusion.

    Corregido (antes bastaba con `any(len(vistos) >= 2)`: que UNA foto
    ligara dos campos apagaba la defensa ENTERA, dejando sin auditar un
    tercer campo que viniera de una foto totalmente disjunta de las otras
    dos -- p.ej. marca+talla en la foto A, modelo en la foto B sin ninguna
    relacion con A, el chequeo viejo decia "coherente" con solo mirar A).
    Con union-find, ese tercer campo queda en su propia componente y el
    aviso SI se dispara.

    Devuelve el aviso (con dientes: quien llama degrada la confianza) o
    `None` si no hay senal de riesgo (0 o 1 campo con valor, o todos en la
    misma componente)."""
    por_fichero: dict[str, set[str]] = {}
    campos_con_valor: set[str] = set()
    for nombre in _CAMPOS_IDENTIDAD_PRODUCTO:
        campo = campos[nombre]
        if campo.valor is not None and campo.evidencia is not None:
            campos_con_valor.add(nombre)
            por_fichero.setdefault(campo.evidencia.fichero, set()).add(nombre)

    if len(campos_con_valor) < 2:
        return None  # no hay nada que pueda "no ligar" -- no aplica

    padre: dict[str, str] = {nombre: nombre for nombre in campos_con_valor}

    def _raiz(x: str) -> str:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def _unir(a: str, b: str) -> None:
        ra, rb = _raiz(a), _raiz(b)
        if ra != rb:
            padre[ra] = rb

    for vistos in por_fichero.values():
        vistos_lista = sorted(vistos)
        for otro in vistos_lista[1:]:
            _unir(vistos_lista[0], otro)

    raices = {_raiz(nombre) for nombre in campos_con_valor}
    if len(raices) == 1:
        return None  # todos los campos de identidad estan conectados

    fotos_implicadas = ", ".join(sorted(por_fichero))
    return (
        "COHERENCIA: los campos estructurados de este producto proceden de "
        f"fotos DISJUNTAS ({fotos_implicadas}) -- no todos los campos de "
        "identidad estan ligados por una foto en comun. Puede ser una fusion "
        "de dos productos distintos en el mismo grupo (revisa las fotos "
        "antes de publicar -- INC-011)."
    )


def verificar_misma_prenda_vlm(
    motor_llm: LLMEngine,
    imagen_a: Imagen,
    imagen_b: Imagen,
    producto_id: str | None = None,
) -> bool:
    """HOOK sin implementar (deliberado -- no una feature a medias).

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
        "verificar_misma_prenda_vlm: hook pendiente de implementar, ver docstring"
    )


# ============================================================================
# Nombres de fichero duplicados fallan RUIDOSO, nunca aliasing en silencio
# ============================================================================


def _verificar_nombres_unicos(fotos: Sequence[Path]) -> None:
    """El pipeline identifica cada foto por su NOMBRE BASE en varios sitios
    (`RegionOCR.fichero`, `Evidencia.fichero`) -- si el grupo trae dos
    ficheros de RUTAS distintas con el MISMO nombre base (dos tarjetas SD,
    dos moviles), un `dict` clavado por nombre pierde uno EN SILENCIO y el
    recorte resultante sale de la foto EQUIVOCADA -- y ni siquiera es
    auditable a posteriori, porque la `Evidencia` guarda un nombre que
    apunta a dos ficheros. Falla RUIDOSO en vez de alias silencioso."""
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
            f"nombre base): {detalle}. Un recorte saldria de la foto EQUIVOCADA -- "
            "renombra o deduplica las fotos antes de extraer."
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

    `carpeta_crops` (constructor): donde se guardan los recortes por
    defecto si `extraer_producto` no recibe una carpeta explicita. En uso
    real sera `data/lotes/<lote_id>/crops/` (quien orquesta el lote la
    pasa); si no se especifica ninguna, se usa `<carpeta de la primera
    foto>/crops/` -- razonable para tests y para uso suelto, pero quien
    integre esto con `core/store.py` debe pasar la carpeta del lote real.
    """

    def __init__(self, motor_llm: LLMEngine, carpeta_crops: Path | str | None = None) -> None:
        self.motor_llm = motor_llm
        self._carpeta_crops_default = Path(carpeta_crops) if carpeta_crops is not None else None
        self._bytes_recorte_por_clave: dict[tuple[str, tuple[int, int, int, int]], bytes] = {}

    # -- planificacion (compartida entre estimar coste y ejecutar) ----------

    def _planificar(
        self, fotos: Sequence[Path]
    ) -> tuple[list[RegionOCR], list[LecturaCrop], list[str]]:
        """Corre OCR (gratis) sobre todas las fotos, descarta ristras de
        metro y resuelve el atajo de OCR limpio SOBRE LAS REGIONES CRUDAS
        (antes de fusionar) -- el metro y el atajo son propiedades de UNA
        linea de texto individual tal y como el OCR la detecto. Fusionar
        ANTES de decidir el atajo mezclaria esas lineas limpias con el
        resto de la etiqueta (mucho mas larga y con lineas de score mas
        bajo), arrastrando el score minimo del grupo por debajo del umbral
        y perdiendo el atajo gratis sin ganar nada a cambio -- medido sobre
        el producto 1 real del golden set. Solo lo que SOBRA tras
        metro+atajo se fusiona antes de generar recortes para el VLM.

        Tras la fusion, se descartan dos clases de region que NUNCA
        aportan un campo de la ficha -- el presupuesto del VLM va donde el
        OCR falla (la ropa), no donde sobra texto (las cajas): bloques de
        texto largos (`_es_bloque_de_texto_largo`) y repeticiones de un
        campo YA resuelto por el atajo (`_es_repeticion_de_un_campo_ya_resuelto`).
        Ninguno de los dos filtros toca las reglas duras de agregacion
        (siguen aplicando igual sobre lo que SI se manda al VLM) -- solo
        deciden que NO hace falta preguntarle nada al modelo.

        Devuelve (regiones_para_vlm, candidatos_atajo, fallos_de_ocr).
        `candidatos_atajo` son `LecturaCrop` (origen="atajo_ocr") que
        entran al MISMO pool de candidatos que las lecturas del VLM -- ya
        no hay carretera paralela (ver docstring del modulo)."""
        regiones_para_vlm: list[RegionOCR] = []
        candidatos_atajo: list[LecturaCrop] = []
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
                    candidatos_atajo.append(atajo)
                    continue

                restantes.append(region)

            for region in fusionar_regiones_cercanas(restantes):
                if _es_repeticion_de_un_campo_ya_resuelto(region.texto_ocr, candidatos_atajo):
                    continue
                if _es_bloque_de_texto_largo(region.texto_ocr):
                    continue
                regiones_para_vlm.append(region)

        return regiones_para_vlm, candidatos_atajo, fallos

    def construir_solicitudes(self, fotos: Sequence[Path]) -> list[tuple[Sequence[Imagen], str, str]]:
        """Lo que `extraer_producto` MANDARIA al VLM para este producto, sin
        llamar a nada -- para pasarselo a `LLMEngine.estimar_coste_lote`
        ANTES de gastar un euro (decision-making.md SS15,
        `.claude/rules/architecture.md` Costura 1). Corre el mismo OCR y
        las mismas reglas de descarte que la extraccion real, asi que el
        coste estimado coincide con el coste real (misma fuente de
        verdad -- ver `_planificar`).

        Cada solicitud es `(imagenes, prompt, version_prompt)` -- el texto
        del prompt viaja explicito porque `LLMEngine._clave_cache` lo
        incluye en el hash (endurecer un prompt sin subir version_prompt
        respondia con el prompt VIEJO en silencio)."""
        fotos = list(fotos)
        _verificar_nombres_unicos(fotos)  # falla ruidoso antes de mapear por nombre
        regiones_para_vlm, _candidatos_atajo, _ = self._planificar(fotos)
        fallos_descartados: list[str] = []  # una foto ilegible aqui solo reduce el estimado, no crashea
        rutas_por_nombre = {f.name: f for f in fotos}  # seguro tras _verificar_nombres_unicos

        solicitudes: list[tuple[Sequence[Imagen], str, str]] = []

        # Mismo backstop que `extraer_producto` -- si no se replicara aqui,
        # la estimacion de coste podria prometer MAS llamadas de las que el
        # motor real llega a hacer.
        for region in regiones_para_vlm[:MAX_LLAMADAS_VLM_POR_PRODUCTO]:
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

        # El color NO genera ninguna solicitud VLM PRIMARIA: sale de pixeles
        # (`_color_dominante_rgb`), gratis -- solo puede entrar via la
        # sintesis de abajo, como gap-filler.

        if fotos:
            imagen_pil = _abrir_foto_o_none(fotos[0], fallos_descartados, "estado")
            if imagen_pil is not None:
                foto_bytes = foto_completa_a_bytes(imagen_pil)
                solicitudes.append(
                    ([Imagen(bytes_=foto_bytes, fichero=fotos[0].name)], PROMPT_ESTADO, VERSION_PROMPT_ESTADO)
                )

        # LA SINTESIS COMPROMETIDA: UNA llamada mas, SIEMPRE (produce
        # titulo/descripcion ademas de rellenar huecos) -- si no se contara
        # aqui, el coste estimado quedaria por debajo del real
        # (`change-loop.md` SS C5). El texto de candidatos en esta fase de
        # ESTIMACION solo puede usar `_candidatos_atajo` (gratis, sin VLM):
        # los hallazgos de los crops de arriba todavia no se conocen (esa
        # es la llamada que se esta ESTIMANDO, no ejecutando) -- es un
        # subconjunto conservador del texto real, nunca de mas.
        imagenes_sintesis: list[Imagen] = []
        for foto in _muestrear_fotos(fotos, N_FOTOS_MUESTRA_SINTESIS):
            imagen_pil = _abrir_foto_o_none(foto, fallos_descartados, "sintesis")
            if imagen_pil is None:
                continue
            imagenes_sintesis.append(Imagen(bytes_=foto_completa_a_bytes(imagen_pil), fichero=foto.name))
        if imagenes_sintesis:
            texto_candidatos = _formatear_candidatos_para_sintesis(_candidatos_atajo)
            solicitudes.append(
                (imagenes_sintesis, _construir_prompt_sintesis(texto_candidatos), VERSION_PROMPT_SINTESIS)
            )

        return solicitudes

    # -- construccion de Propuesta (recorte a disco, lecturas, alternativas) --

    def _recorte_y_lecturas(
        self,
        lectura: LecturaCrop | None,
        rutas_por_nombre: dict[str, Path],
        carpeta_crops: Path,
        fallos: list[str],
    ) -> tuple[Path | None, Evidencia | None, tuple[Lectura, ...]]:
        """El PIXEL que se ensena es EXACTAMENTE el que se mando (o se
        mandaria) al modelo -- reutiliza los bytes capturados en el momento
        de construir el `Imagen` (`self._bytes_recorte_por_clave`) si esta
        lectura vino de una llamada VLM real; si no (atajo OCR, o no se
        llego a mandar por algun motivo), recorta la foto original de
        nuevo con la MISMA funcion (`recortar_region`) -- determinista."""
        if lectura is None:
            return None, None, ()

        if lectura.origen == "vlm":
            lecturas = (Lectura(origen="vlm", texto=lectura.texto),)
            if lectura.texto_ocr_crudo:
                lecturas = lecturas + (Lectura(origen="ocr", texto=lectura.texto_ocr_crudo),)
        else:  # "atajo_ocr" -- ningun VLM lo miro, la lectura es puramente OCR
            lecturas = (Lectura(origen="ocr", texto=lectura.texto),)

        evidencia = Evidencia(fichero=lectura.fichero, bbox=lectura.bbox)

        clave = (lectura.fichero, lectura.bbox)
        bytes_recorte = self._bytes_recorte_por_clave.get(clave)
        if bytes_recorte is None:
            ruta_foto = rutas_por_nombre.get(lectura.fichero)
            if ruta_foto is None:
                return None, evidencia, lecturas
            imagen_pil = _abrir_foto_o_none(ruta_foto, fallos, "recorte")
            if imagen_pil is None:
                return None, evidencia, lecturas
            bytes_recorte = recortar_region(imagen_pil, lectura.bbox)
            self._bytes_recorte_por_clave[clave] = bytes_recorte

        ruta_recorte = _guardar_recorte(bytes_recorte, carpeta_crops, lectura.fichero, lectura.bbox)
        return ruta_recorte, evidencia, lecturas

    def _candidato_desde_lectura(
        self,
        lectura: LecturaCrop,
        rutas_por_nombre: dict[str, Path],
        carpeta_crops: Path,
        fallos: list[str],
    ) -> Candidato:
        recorte, evidencia, lecturas = self._recorte_y_lecturas(lectura, rutas_por_nombre, carpeta_crops, fallos)
        assert evidencia is not None  # `lectura` no es None aqui
        return Candidato(valor=lectura.texto, recorte=recorte, evidencia=evidencia, lecturas=lecturas)  # type: ignore[arg-type]

    def _propuesta_desde_grupo(
        self,
        grupo: _GrupoCampo,
        nombre_campo: str,
        rutas_por_nombre: dict[str, Path],
        carpeta_crops: Path,
        fallos: list[str],
    ) -> Propuesta:
        recorte, evidencia, lecturas = self._recorte_y_lecturas(
            grupo.representante, rutas_por_nombre, carpeta_crops, fallos
        )
        alternativas = tuple(
            self._candidato_desde_lectura(alt, rutas_por_nombre, carpeta_crops, fallos)
            for alt in grupo.alternativas
        )
        return Propuesta(
            campo=nombre_campo,
            valor=grupo.campo.valor,
            recorte=recorte,
            evidencia=evidencia,
            lecturas=lecturas,
            alternativas=alternativas,
            motivo=grupo.motivo,
        )

    # -- LA SINTESIS COMPROMETIDA (gap-filler, ver docstring del modulo) ------

    def _sintetizar_ficha(
        self,
        fotos: Sequence[Path],
        lecturas_totales: Sequence[LecturaCrop],
        campos: dict[str, Campo],
        propuestas: dict[str, Propuesta],
        rutas_por_nombre: dict[str, Path],
        carpeta_crops: Path,
        fallos: list[str],
        producto_id: str | None,
    ) -> None:
        """UNA llamada VLM mas (Diego, 2026-07-15: "null -> mejor-intento
        comprometido") que ve el producto ENTERO (hasta
        `N_FOTOS_MUESTRA_SINTESIS` fotos GENERALES, nunca crops) + la lista
        de textos ya detectados, y PROPONE un valor comprometido por campo,
        ademas de redactar `titulo`/`descripcion`. Muta `campos` y
        `propuestas` EN SITIO (mismo patron que el resto del pipeline, que
        ya construyo esos dicts antes de llamar aqui).

        GAP-FILLER: para marca/modelo/talla/color/estado/medidas, solo
        sobreescribe si `campos[nombre].valor` YA es `None` -- una lectura
        de una etiqueta a resolucion NATIVA (o un histograma de pixeles)
        es mas fiable que una opinion sobre la foto general, asi que nunca
        se pisa un valor solido. `ean`, `desperfectos` y "composicion"
        (ELIMINADA de la ficha, Diego 2026-07-17) quedan fuera a proposito
        -- no estan en `_CAMPOS_SINTESIS_GAP`. `titulo`/`descripcion` SON campos nuevos
        (no habia nada previo que preservar): se toman de la sintesis
        siempre, sin gap-filler.

        Fallo tecnico (rate limit, sin API key, JSON invalido): se loguea +
        se anota en `fallos` (nunca fallback silencioso) y NINGUN campo
        cambia -- el producto se queda con lo que ya tenia antes de este
        paso, nunca peor."""
        imagenes: list[Imagen] = []
        for foto in _muestrear_fotos(fotos, N_FOTOS_MUESTRA_SINTESIS):
            imagen_pil = _abrir_foto_o_none(foto, fallos, "sintesis")
            if imagen_pil is None:
                continue
            imagenes.append(Imagen(bytes_=foto_completa_a_bytes(imagen_pil), fichero=foto.name))

        if not imagenes:
            fallos.append("sintesis:sin_fotos_abribles")
            return

        texto_candidatos = _formatear_candidatos_para_sintesis(lecturas_totales)
        prompt = _construir_prompt_sintesis(texto_candidatos)

        try:
            resultado = self.motor_llm.consultar(
                imagenes,
                prompt,
                ESQUEMA_SINTESIS_FICHA,
                version_prompt=VERSION_PROMPT_SINTESIS,
                producto_id=producto_id,
            )
            decisiones = _parsear_respuesta_sintesis(resultado.datos)
        except (LLMLlamadaFallidaError, ApiKeyFaltanteError, RespuestaVLMInvalidaError) as exc:
            logger.error("Fallo VLM en la sintesis de ficha: %s", exc)
            fallos.append(f"vlm_sintesis: {exc}")
            return

        indice_candidatos = _indexar_candidatos_por_texto(lecturas_totales)

        for campo_sintesis, (nombre_campo, ubicaciones_validas) in _CAMPOS_SINTESIS_GAP.items():
            if campos[nombre_campo].valor is not None:
                continue  # ya solido -- la sintesis NUNCA pisa (gap-filler)

            campo_nuevo, candidato = _construir_campo_desde_sintesis(
                decisiones[campo_sintesis], indice_candidatos, ubicaciones_validas
            )
            if campo_nuevo.valor is None:
                continue  # la sintesis tampoco tuvo opinion -- se deja el previo intacto

            recorte, evidencia_recorte, lecturas_crudas = self._recorte_y_lecturas(
                candidato, rutas_por_nombre, carpeta_crops, fallos
            )
            propuesta_previa = propuestas[nombre_campo]
            alternativas_restantes = tuple(
                alt
                for alt in propuesta_previa.alternativas
                if alt.valor is None or alt.valor.strip().lower() != campo_nuevo.valor.strip().lower()
            )

            campos[nombre_campo] = campo_nuevo
            propuestas[nombre_campo] = Propuesta(
                campo=nombre_campo,
                valor=campo_nuevo.valor,
                recorte=recorte if recorte is not None else propuesta_previa.recorte,
                evidencia=evidencia_recorte if evidencia_recorte is not None else propuesta_previa.evidencia,
                lecturas=lecturas_crudas if lecturas_crudas else propuesta_previa.lecturas,
                alternativas=alternativas_restantes,
                motivo=(
                    "propuesto por la sintesis (mejor intento comprometido, Diego revisa): "
                    + ("visible en foto" if campo_nuevo.fuente == "foto" else "inferido, sin etiqueta que lo respalde")
                ),
            )

        # "categoria" es un campo NUEVO (como titulo/descripcion: no existia
        # antes de la sintesis, asi que no hay gap-filler que respetar) pero
        # con su PROPIO camino de fallo no-fatal: si el modelo violo el enum
        # cerrado, la clave se queda AUSENTE de `campos` -- nunca un
        # comodin silencioso (decision-making.md SS13). Ver
        # `_construir_campo_categoria_desde_sintesis`.
        campo_categoria = _construir_campo_categoria_desde_sintesis(decisiones["categoria"])
        if campo_categoria is not None:
            campos["categoria"] = campo_categoria
            propuestas["categoria"] = Propuesta(
                campo="categoria",
                valor=campo_categoria.valor,
                recorte=None,
                evidencia=None,
                motivo="clasificacion del modelo, confirmala",
            )
        else:
            fallos.append(f"sintesis_categoria_fuera_de_enum: {decisiones['categoria']!r}")

        for campo_texto in ("titulo", "descripcion"):
            texto = decisiones[campo_texto]
            campos[campo_texto] = Campo(valor=texto, fuente="inferido", confianza="baja")
            propuestas[campo_texto] = Propuesta(
                campo=campo_texto,
                valor=texto,
                recorte=None,
                evidencia=None,
                motivo="borrador del modelo, editalo",
            )

    # -- ejecucion real -------------------------------------------------------

    def extraer_producto(
        self,
        fotos: Sequence[Path],
        categoria: CategoriaTipo = "moda",
        producto_id: str | None = None,
        carpeta_crops: Path | str | None = None,
    ) -> ResultadoExtraccion:
        """Extrae los atributos de UN producto (fotos ya agrupadas y
        confirmadas por Diego en la Fase 1). `categoria` no cambia el
        comportamiento hoy (reservado para cuando existan reglas
        especificas por categoria); se recibe para que la firma sea
        estable cuando se necesite.

        `carpeta_crops`: donde se guardan los recortes de este producto.
        Si no se especifica, se usa la del constructor, y si tampoco esa
        existe, `<carpeta de fotos[0]>/crops/`.
        """
        del categoria  # reservado; ver docstring
        fotos = list(fotos)
        if not fotos:
            raise ValueError("extraer_producto requiere al menos una foto")
        _verificar_nombres_unicos(fotos)  # falla ruidoso antes de mapear por nombre

        self._bytes_recorte_por_clave = {}  # limpio por llamada -- no se filtra entre productos
        if carpeta_crops is not None:
            carpeta_crops_efectiva = Path(carpeta_crops)
        elif self._carpeta_crops_default is not None:
            carpeta_crops_efectiva = self._carpeta_crops_default
        else:
            carpeta_crops_efectiva = fotos[0].parent / NOMBRE_CARPETA_CROPS

        regiones_para_vlm, candidatos_atajo, fallos = self._planificar(fotos)
        rutas_por_nombre = {f.name: f for f in fotos}  # seguro tras _verificar_nombres_unicos

        lecturas: list[LecturaCrop] = []
        limite_vlm_avisado = False
        for indice, region in enumerate(regiones_para_vlm):
            if indice >= MAX_LLAMADAS_VLM_POR_PRODUCTO:
                # Backstop de coste -- una escena con mucho fondo ajeno
                # (regiones que el VLM luego descartaria via
                # pertenece_al_producto=False, pero eso solo se sabe DESPUES
                # de llamar) no puede facturar sin techo.
                if not limite_vlm_avisado:
                    limite_vlm_avisado = True
                    logger.warning(
                        "limite de %d llamadas VLM de recorte alcanzado (%d regiones detectadas) "
                        "-- se descartan las restantes para este producto",
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
                recorte_bytes = recortar_region(imagen_pil, region.bbox)
                # Se capturan los bytes AQUI, antes de mandarlos -- son los
                # mismos que luego se escriben a disco (docstring, punto 3).
                self._bytes_recorte_por_clave[(region.fichero, region.bbox)] = recorte_bytes
                imagen = Imagen(bytes_=recorte_bytes, fichero=region.fichero)
                resultado = self.motor_llm.consultar(
                    [imagen],
                    PROMPT_LECTURA_CROP,
                    ESQUEMA_LECTURA_CROP,
                    version_prompt=VERSION_PROMPT_CROP,
                    producto_id=producto_id,
                )
                nuevas_lecturas = _parsear_lectura_crop(
                    resultado.datos, region.fichero, region.bbox, texto_ocr_crudo=region.texto_ocr
                )
            except (LLMLlamadaFallidaError, ApiKeyFaltanteError, RespuestaVLMInvalidaError) as exc:
                logger.error("Fallo VLM en recorte %s %s: %s", region.fichero, region.bbox, exc)
                fallos.append(f"vlm_crop:{region.fichero}:{region.bbox}: {exc}")
                continue
            lecturas.extend(nuevas_lecturas)

        lecturas_totales: list[LecturaCrop] = lecturas + candidatos_atajo

        grupo_marca = _agregar_campo_texto(lecturas_totales, "marca", _UBICACIONES_VALIDAS_MARCA)
        grupo_talla = _agregar_campo_texto(lecturas_totales, "talla", _UBICACIONES_VALIDAS_TALLA)
        grupo_modelo = _agregar_campo_texto(lecturas_totales, "modelo", _UBICACIONES_VALIDAS_MODELO)

        # EAN: se pre-filtra por checksum ANTES de agregar (unifica atajo y
        # VLM en el mismo camino -- dos EAN validos y distintos SI generan
        # un conflicto real, nunca "el primero que se encontro" en silencio).
        lecturas_ean_validas = _filtrar_ean_checksum_valido(lecturas_totales)
        grupo_ean = _agregar_campo_texto(lecturas_ean_validas, "ean", _UBICACIONES_VALIDAS_EAN)
        if grupo_ean.campo.valor is not None:
            # UNICO camino de este modulo a confianza="alta": un EAN cuyo
            # checksum GS1 valida (verificable por una regla matematica,
            # no por un modelo).
            grupo_ean = replace(grupo_ean, campo=replace(grupo_ean.campo, confianza="alta"))
        # Defensa en profundidad (C5): re-valida el Campo final aunque ya
        # se haya pre-filtrado.
        grupo_ean = replace(grupo_ean, campo=_validar_campo_ean(grupo_ean.campo))

        grupo_desperfectos = _agregar_campo_desperfectos(lecturas_totales)
        # "composicion" ELIMINADA de la ficha entera (Diego, 2026-07-17):
        # ya no se construye ningun `_GrupoCampo`/`Propuesta` para ella.

        propuesta_marca = self._propuesta_desde_grupo(grupo_marca, "marca", rutas_por_nombre, carpeta_crops_efectiva, fallos)
        propuesta_talla = self._propuesta_desde_grupo(grupo_talla, "talla", rutas_por_nombre, carpeta_crops_efectiva, fallos)
        propuesta_modelo = self._propuesta_desde_grupo(grupo_modelo, "modelo", rutas_por_nombre, carpeta_crops_efectiva, fallos)
        propuesta_ean = self._propuesta_desde_grupo(grupo_ean, "ean", rutas_por_nombre, carpeta_crops_efectiva, fallos)
        propuesta_desperfectos = self._propuesta_desde_grupo(
            grupo_desperfectos, "desperfectos", rutas_por_nombre, carpeta_crops_efectiva, fallos
        )

        campo_medidas, propuesta_medidas, fallos_medidas = self._evaluar_medidas(producto_id)
        # El color excluye el bbox de un estampado ya localizado en la MISMA
        # foto (si lo hay) -- pasa las lecturas VLM, no llama al VLM.
        campo_color, propuesta_color, fallos_color = self._extraer_color(fotos, lecturas_totales)
        campo_estado, propuesta_estado, fallos_estado = self._extraer_estado(fotos, producto_id)
        fallos.extend(fallos_medidas)
        fallos.extend(fallos_color)
        fallos.extend(fallos_estado)

        campos = {
            "marca": grupo_marca.campo,
            "talla": grupo_talla.campo,
            "modelo": grupo_modelo.campo,
            "ean": grupo_ean.campo,
            "medidas": campo_medidas,
            "color": campo_color,
            "estado": campo_estado,
            "desperfectos": grupo_desperfectos.campo,
        }
        propuestas = {
            "marca": propuesta_marca,
            "talla": propuesta_talla,
            "modelo": propuesta_modelo,
            "ean": propuesta_ean,
            "medidas": propuesta_medidas,
            "color": propuesta_color,
            "estado": propuesta_estado,
            "desperfectos": propuesta_desperfectos,
        }

        # LA SINTESIS COMPROMETIDA (ver docstring del modulo): UNA llamada
        # mas, GAP-FILLER -- solo rellena lo que arriba quedo en None, mas
        # titulo/descripcion (campos nuevos, siempre). Muta `campos` y
        # `propuestas` en sitio; nunca pisa un valor ya solido.
        self._sintetizar_ficha(
            fotos,
            lecturas_totales,
            campos,
            propuestas,
            rutas_por_nombre,
            carpeta_crops_efectiva,
            fallos,
            producto_id,
        )

        # "LA FICHA FRANKENSTEIN": si los campos de identidad no estan todos
        # en la misma componente conexa, degradar (techo "media") y avisar
        # CON DIENTES (la UI esta obligada a mostrarlo, no es un pie de foto).
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
            propuestas=propuestas,
            fallos=tuple(fallos),
            coste_usd=coste,
            aviso_coherencia=aviso_coherencia,
        )

    # -- medidas (metro): regla dura #5 --------------------------------------

    def _evaluar_medidas(self, producto_id: str | None) -> tuple[Campo, Propuesta, list[str]]:
        fotos_metro = list(dict.fromkeys(getattr(self, "_fotos_con_metro", [])))
        fallos: list[str] = []
        if not fotos_metro:
            # NO_FOTOGRAFIADO: ninguna foto de este producto tenia ni
            # siquiera una ristra de metro -- no hay evidencia que citar.
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            propuesta = Propuesta(
                campo="medidas", valor=None, recorte=None, evidencia=None,
                motivo="ninguna foto de este producto tenia ni siquiera una ristra de metro (NO_FOTOGRAFIADO)",
            )
            return campo, propuesta, fallos

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
                propuesta = Propuesta(
                    campo="medidas",
                    valor=str(datos["medida_cm"]),
                    recorte=None,
                    evidencia=Evidencia(fichero=foto.name),
                    lecturas=(Lectura(origen="vlm", texto=str(datos["medida_cm"])),),
                    motivo="el 0 de la cinta Y el borde de la prenda son visibles en esta foto",
                )
                return campo, propuesta, fallos

        # Hubo foto(s) de metro pero ninguna con las dos condiciones -- es
        # PRESENTE_ILEGIBLE (hay evidencia, no es derivable), no NO_FOTOGRAFIADO.
        campo = Campo(
            valor=None, fuente="foto", confianza="baja",
            evidencia=Evidencia(fichero=fotos_metro[0].name),
        )
        propuesta = Propuesta(
            campo="medidas", valor=None, recorte=None,
            evidencia=Evidencia(fichero=fotos_metro[0].name),
            motivo="hubo foto(s) de metro pero ninguna con el 0 Y el borde de la prenda visibles a la vez",
        )
        return campo, propuesta, fallos

    # -- color: regla dura #9 -------------------------------------------------

    def _extraer_color(
        self, fotos: Sequence[Path], lecturas_vlm: Sequence[LecturaCrop] = ()
    ) -> tuple[Campo, Propuesta, list[str]]:
        """Color por PIXELES (`architecture.md` Costura 1: "color por
        pixeles"), NUNCA VLM -- gratis y estructuralmente incapaz de
        alucinar en el sentido de "opinar": pero un histograma SI puede
        estar sesgado por el sensor.

          (a) el color de un histograma NUNCA es `confianza="alta"` --
              NI SIQUIERA con varias fotos de acuerdo, porque el acuerdo
              puede ser el MISMO sesgo del sensor repetido (medido: una
              sudadera NEGRA sale RGB~(80,80,80) en las 3 fotos por la
              autoexposicion del movil). Y `fuente="inferido"`, no "foto":
              una cuenta de pixeles no es una lectura directa del color
              real, es una inferencia sobre lo que el sensor capturo.
          (b) `_nombre_color_mas_cercano` distingue en espacio Lab, no RGB.
          (c) si las fotos muestreadas DIVERGEN en el nombre de color,
              `valor=None` -- nunca "la primera lectura que salga primero".
          (d) `lecturas_vlm` se usa para EXCLUIR del histograma el bbox de
              cualquier `estampado_o_grafico` localizado en la MISMA foto
              muestreada.
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
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            propuesta = Propuesta(
                campo="color", valor=None, recorte=None, evidencia=None,
                motivo="ninguna foto se pudo abrir para muestrear el color",
            )
            return campo, propuesta, fallos

        normalizados = {nombre for nombre, _ in lecturas_color}
        if len(normalizados) > 1:
            # (c) diverge -> None, nunca la primera lectura.
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            detalle = ", ".join(f"{foto.name}={nombre}" for nombre, foto in lecturas_color)
            propuesta = Propuesta(
                campo="color", valor=None, recorte=None, evidencia=None,
                motivo=f"las fotos muestreadas DIVERGEN en color ({detalle}) -- ninguna es mas valida que otra",
            )
            return campo, propuesta, fallos

        valor, foto = lecturas_color[0]
        # (a) techo "media", SIEMPRE -- ver docstring de este metodo.
        campo = Campo(
            valor=valor,
            fuente="inferido",
            confianza="media",
            evidencia=Evidencia(fichero=foto.name),
        )
        propuesta = Propuesta(
            campo="color", valor=valor, recorte=None, evidencia=Evidencia(fichero=foto.name),
            motivo=(
                "color dominante por histograma de pixeles (nunca VLM); techo "
                "confianza=media SIEMPRE, incluso si todas las fotos coinciden "
                "(puede ser el mismo sesgo del sensor repetido)"
            ),
        )
        return campo, propuesta, fallos

    # -- estado: regla dura #8, SIEMPRE fuente=inferido ------------------------

    def _extraer_estado(
        self, fotos: Sequence[Path], producto_id: str | None
    ) -> tuple[Campo, Propuesta, list[str]]:
        fallos: list[str] = []
        if not fotos:
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            propuesta = Propuesta(campo="estado", valor=None, recorte=None, evidencia=None, motivo="sin fotos")
            return campo, propuesta, fallos

        foto = fotos[0]
        imagen_pil = _abrir_foto_o_none(foto, fallos, "estado")
        if imagen_pil is None:
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            propuesta = Propuesta(
                campo="estado", valor=None, recorte=None, evidencia=None,
                motivo=f"no se pudo abrir {foto.name}",
            )
            return campo, propuesta, fallos
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
            campo = Campo(valor=None, fuente="inferido", confianza="baja")
            propuesta = Propuesta(
                campo="estado", valor=None, recorte=None, evidencia=Evidencia(fichero=foto.name),
                motivo=f"fallo tecnico consultando el VLM: {exc}",
            )
            return campo, propuesta, fallos

        legible = bool(datos["estimacion_legible"])
        descripcion = datos["descripcion"] if legible else None
        valor = str(descripcion).strip() if descripcion else None
        # Regla dura #8: SIEMPRE "inferido", nunca "foto" -- lo confirma
        # Diego siempre, pase lo que declare el VLM.
        campo = Campo(valor=valor, fuente="inferido", confianza="media" if legible else "baja")
        propuesta = Propuesta(
            campo="estado", valor=valor, recorte=None, evidencia=Evidencia(fichero=foto.name),
            lecturas=(Lectura(origen="vlm", texto=valor),) if valor else (),
            motivo="estimacion visual del VLM -- SIEMPRE la confirma Diego, nunca se afirma sola (regla dura #8)",
        )
        return campo, propuesta, fallos


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


# ============================================================================
# ETAPA 6 -- REDACCION DESDE CAMPOS CONFIRMADOS (2026-07-17, fix del bug de
# Diego: "la descripcion no menciona la CREMALLERA ROTA")
# ============================================================================
# CAUSA RAIZ del bug: `_sintetizar_ficha` redacta `titulo`/`descripcion`
# DURANTE LA EXTRACCION, a partir de lo que el modelo CREE ver en las fotos
# -- ANTES de que Diego corrija nada. Si Diego luego cambia `marca`
# ("Umbro" -> "Reebok") o rellena `desperfectos` ("CREMALLERA ROTA", leido
# de un papel manuscrito, `fuente="foto"`), el texto de venta NUNCA se
# actualizaba: `ui/ficha.py::_accion_confirmar_ficha` sobreescribia todos
# los DEMAS campos con lo que Diego dejo en pantalla, pero titulo y
# descripcion se colaban tal cual, describiendo el producto PRE-correccion.
#
# LA LEY DE ESTE MODULO ("EL EXTRACTOR NO AFIRMA, PROPONE") no se rompe con
# este fix -- se REFUERZA: `redactar_desde_campos_confirmados` es una
# llamada de puro TEXTO (via `LLMEngine.consultar_texto`, que ni siquiera
# TIENE un parametro `imagenes` en su firma -- no hay donde colar una foto)
# cuyo UNICO input son los VALORES YA CONFIRMADOS (marca, talla, color,
# estado, desperfectos, categoria...), nunca las fotos ni las propuestas
# crudas del modelo. Es la garantia estructural, no una promesa de prompt,
# de que el texto de venta JAMAS puede mencionar una marca que Diego no
# confirmo (`product.md` SS"Reglas de contenido": `MENTIONS_OTHER_BRAND`
# OCULTA el anuncio en Vinted) ni inventar un dato que el no tecleo -- si
# el dato no esta en el input, no existe para el modelo, punto.
#
# Quien orquesta esta llamada (`ui/ficha.py::_accion_confirmar_ficha`) debe
# invocarla EN EL MOMENTO DE CONFIRMAR, con los campos que Diego acaba de
# dejar en pantalla -- nunca durante la extraccion. Un titulo/descripcion
# que Diego edito A MANO (difiere de lo que la extraccion propuso) NUNCA se
# pisa: eso lo decide quien llama, comparando contra el valor previo, este
# modulo solo redacta cuando se le pide.
#
# `desperfectos` es OBLIGATORIO en la descripcion cuando tiene valor -- es
# el campo que evita la devolucion (el motivo entero de este fix), y el
# prompt lo instruye con dureza, sin margen de "suavizarlo hasta que no se
# note".

VERSION_PROMPT_REDACCION = "extract-redaccion-v1"

# Orden de presentacion de los campos en el prompt -- SOLO campos de
# atributo (nunca titulo/descripcion, que es lo que este paso REDACTA, ni
# `ean`, que no aporta nada a un texto de venta legible). "composicion" NO
# esta aqui: salio ENTERA de la ficha (Diego, 2026-07-17, ver
# `CAMPOS_PRODUCIDOS`) -- si algun dia vuelve, se anade aqui tambien.
# `desperfectos` va el ultimo A PROPOSITO: es el campo con la instruccion
# mas dura del prompt, y quedarse el ultimo en la lista reduce que se
# pierda entre los demas.
_CAMPOS_REDACCION_ORDEN: tuple[str, ...] = (
    "categoria", "marca", "modelo", "talla", "color", "estado",
    "medidas", "desperfectos",
)

_NOMBRE_CAMPO_LEGIBLE: dict[str, str] = {
    "categoria": "categoria",
    "marca": "marca",
    "modelo": "modelo/referencia",
    "talla": "talla",
    "color": "color",
    "estado": "estado de conservacion",
    "medidas": "medidas",
    "desperfectos": "DESPERFECTOS (nota manuscrita del vendedor)",
}

PROMPT_REDACCION_FICHA = """Vas a redactar el TITULO y la DESCRIPCION de venta
de UN producto de segunda mano, para publicarlos en Wallapop y Vinted.

NO has visto ninguna foto de este producto. Tu UNICA fuente de informacion
son estos datos, ya confirmados por el vendedor (Diego):

{campos_texto}

REGLAS DURAS -- son invariantes, no sugerencias:
  1. NO menciones NINGUNA marca que no sea EXACTAMENTE la del campo "marca"
     de la lista de arriba. Si "marca" no aparece en la lista, NO
     menciones ninguna marca (ni la insinues, ni digas "de marca
     reconocida"). Mencionar una marca ajena OCULTA el anuncio en Vinted
     -- es la regla mas importante de este prompt.
  2. NO inventes NINGUN dato (talla, color, material, medida, modelo,
     estado, defecto) que no este LITERALMENTE en la lista de arriba. Si
     un campo no aparece en la lista, ese dato NO EXISTE para ti -- no lo
     menciones, no lo insinues, no lo "completes a ojo".
  3. Si en la lista aparece "DESPERFECTOS", DEBES mencionarlo EXPLICITA y
     CLARAMENTE en la descripcion, con esas mismas palabras o muy
     parecidas -- es la informacion que evita una devolucion. Prohibido
     omitirlo, minimizarlo o redactarlo de forma que quede irreconocible.
  4. Nada de emails, enlaces, MAYUSCULAS excesivas, ristras de simbolos o
     emojis repetidos. Español, tono honesto de segunda mano (ni de venta
     agresiva ni alarmista).

FORMATO:
  - titulo: maximo 100 caracteres, corto y claro (incluye marca+tipo de
    producto si los tienes; nunca vacio).
  - descripcion: maximo 600 caracteres, honesta sobre lo que hay en la
    lista (incluido cualquier desperfecto, regla 3).

Responde SOLO el JSON pedido."""


def _construir_prompt_redaccion(texto_campos: str) -> str:
    """Funcion separada por el mismo motivo que `_construir_prompt_sintesis`
    (C6, `LLMEngine._clave_cache` hashea el TEXTO REAL del prompt): quien
    estime coste y quien llame de verdad deben construir el prompt
    IDENTICO para compartir clave de cache."""
    return PROMPT_REDACCION_FICHA.format(campos_texto=texto_campos)


ESQUEMA_REDACCION_FICHA: dict = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "descripcion": {"type": "string"},
    },
    "required": ["titulo", "descripcion"],
    "additionalProperties": False,
}


def _formatear_campos_para_redaccion(campos_confirmados: Mapping[str, Any]) -> str:
    """`campos_confirmados` es un dict PLANO `nombre_campo -> valor` (texto
    o `None`) -- NO la estructura `Campo`/`Propuesta` completa: quien llama
    (`ui/ficha.py`) ya extrajo `.valor` de cada campo confirmado antes de
    pasarlo aqui, precisamente para que este modulo no tenga ni la
    TENTACION de leer una `evidencia` o una `propuesta` que apunte a una
    foto. Los campos ausentes o vacios se OMITEN de la lista -- para el
    modelo, "ausente en la lista" y "no existe" son la misma cosa (regla
    dura #2 del prompt)."""
    lineas: list[str] = []
    for campo in _CAMPOS_REDACCION_ORDEN:
        valor = campos_confirmados.get(campo)
        if valor is None:
            continue
        texto = str(valor).strip()
        if not texto:
            continue
        lineas.append(f"- {_NOMBRE_CAMPO_LEGIBLE.get(campo, campo)}: {texto}")
    if not lineas:
        return "(el vendedor aun no ha confirmado ningun dato de este producto)"
    return "\n".join(lineas)


def redactar_desde_campos_confirmados(
    campos_confirmados: Mapping[str, Any],
    motor: LLMEngine,
    producto_id: str | None = None,
) -> tuple[str, str]:
    """Redacta `(titulo, descripcion)` a partir de los campos YA
    CONFIRMADOS por Diego -- CERO fotos, CERO propuestas del modelo, cero
    lecturas de OCR/VLM. Es LA LEY de este modulo aplicada a la redaccion:
    el extractor no afirma nada que no pueda respaldar, y aqui lo que se
    "afirma" en prosa es exactamente lo que Diego ya afirmo en los campos
    estructurados -- ni una palabra mas.

    `campos_confirmados`: dict PLANO `nombre_campo -> valor` (ver
    `_formatear_campos_para_redaccion`). NUNCA se le pasan imagenes: la
    llamada usa `LLMEngine.consultar_texto`, que estructuralmente no
    acepta ninguna (no tiene parametro `imagenes`) -- la garantia
    anti-marca-ajena no depende de que quien llame "se acuerde" de no
    mandar fotos, esta en la firma del metodo que se usa.

    Fallo tecnico (red, rate limit, sin API key, JSON invalido): se
    PROPAGA (`LLMLlamadaFallidaError`/`ApiKeyFaltanteError`/
    `RespuestaVLMInvalidaError`) -- NUNCA devuelve un texto plausible de
    repuesto. Quien llama (`ui/ficha.py`) debe capturarlo y avisar a
    Diego, nunca confirmar en silencio con el texto viejo (describiria el
    producto PRE-correccion, que es exactamente el bug que esto arregla).
    """
    texto_campos = _formatear_campos_para_redaccion(campos_confirmados)
    prompt = _construir_prompt_redaccion(texto_campos)
    resultado = motor.consultar_texto(
        prompt,
        ESQUEMA_REDACCION_FICHA,
        version_prompt=VERSION_PROMPT_REDACCION,
        producto_id=producto_id,
    )
    datos = resultado.datos
    if "titulo" not in datos or "descripcion" not in datos:
        raise RespuestaVLMInvalidaError(f"redaccion: faltan claves en la respuesta: {datos!r}")

    titulo = str(datos["titulo"]).strip()
    descripcion = str(datos["descripcion"]).strip()
    if not titulo or not descripcion:
        raise RespuestaVLMInvalidaError(
            f"redaccion: titulo o descripcion vacios (titulo={titulo!r}, descripcion={descripcion!r})"
        )
    return titulo, descripcion


def construir_solicitud_redaccion(
    campos_confirmados: Mapping[str, Any],
) -> tuple[str, str]:
    """`(prompt, version_prompt)` de la llamada de redaccion, SIN llamar a
    nadie -- para que quien quiera estimar coste ANTES de confirmar (si
    algun dia hace falta un gate explicito aqui, `decision-making.md` §15)
    pueda construir la MISMA solicitud que `redactar_desde_campos_confirmados`
    sin gastar. No hay imagenes que reportar: siempre es una llamada de
    texto puro."""
    return _construir_prompt_redaccion(_formatear_campos_para_redaccion(campos_confirmados)), VERSION_PROMPT_REDACCION
