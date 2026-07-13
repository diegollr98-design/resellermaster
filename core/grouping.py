"""core/grouping.py — Costura de AGRUPACION (RESELLERMASTER).

Superficie SENSIBLE (`.claude/rules/truth-loop.md` SS B y SS E). Una foto
en la ficha del producto equivocado es el fallo mas caro y mas silencioso
del proyecto: se publica, y nadie lo caza. Este modulo PROPONE grupos;
Diego CONFIRMA (`core/store.py` bloquea cualquier re-agrupacion despues de
esa confirmacion, ver su docstring). Ninguna funcion de aqui cierra nada.

## [INC-002] v2 -- por que este fichero se reescribio DOS VECES
La v1 (mediana*3 + pHash) fallaba con 8 prendas en un grupo "alta". El
primer arreglo (salto relativo + colorhash) lo cerro un `listing-audit`,
pero un SEGUNDO `listing-audit`, independiente, lo declaro BLOQUEANTE otra
vez: "cosmetico respecto a la clase de fallo". Dos hallazgos, ejecutados y
re-derivados por el orquestador:

1. **Una unica frontera "la de mayor salto" se deja aplastar.** Con huecos
   `[4,4,12, 4,4,12, 4,4,120, 4,4,12, ...]` (ritmo normal, y UNA vez Diego
   tarda 2 min en cambiar de prenda -- se enredo, sono el telefono), el
   salto 12->120 es tan grande que "gana" sobre el salto real 4->12, el
   umbral sale ~66s, y NINGUN hueco de 12s corta: 3 prendas en un grupo,
   marcado "alta". Cuanto mas larga la pausa anomala, mas confiado se
   declara el grupo -- perverso.
2. **pHash es luminancia; colorhash tambien falla, sistematicamente.**
   pHash da distancia 0 entre dos camisetas del mismo corte y color
   distinto (promedia a gris). colorhash mejora eso, pero usa solo 6
   cubos de tono: rojo vs naranja mide 2, dentro de su propio umbral. Y
   ninguno de los dos ve BIMODALIDAD: 3 fotos rojas + 3 azules en el mismo
   segmento no tiene "una foto rara" (cada una tiene 2 hermanas del mismo
   color) -- `_fotos_muy_distintas` (v1) devolvia `[]` con una senal de
   color clarisima (distancia cruzada 7, muy por encima del umbral 2) que
   el algoritmo tiraba por preguntar la pregunta equivocada.

Diego aprobo el rediseno completo. Los 4 cambios, en orden:

### Cambio 1 -- el corte temporal ya NO elige "la mejor frontera"
`_umbral_corte` ya no busca el salto relativo MAXIMO entre huecos
ordenados. Busca TODAS las fronteras que superan
`UMBRAL_SALTO_RELATIVO_MINIMO` y usa la MAS BAJA que califica -- nunca la
de mayor salto. Como cualquier hueco por encima de esa frontera baja
tambien esta por encima de las fronteras mas altas que pudieran existir,
"cortar por la mas baja que califica" ya corta, de facto, por TODAS las
que califican: no hace falta una logica de "multiples umbrales", un solo
numero (el mas bajo) basta. Esto arregla el caso exacto de arriba: con
huecos {4,12,120}, la frontera (4,12) YA califica (salto relativo
(12-4)/4.5=1.78, por encima de 0.30) y se usa esa, sin esperar a que
aparezca la (12,120) y la eclipse.

**Y la regla que cierra la clase, no solo el caso:** si hay 2 o mas
fronteras que califican (aqui: (4,12) Y (12,120)), hay AMBIGUEDAD REAL
sobre donde cambia el producto -- el modulo no tiene base para afirmar
"aqui, con certeza, y no alli tambien". `ambiguo=True` capa el techo local
de ese grupo a "media" con un motivo que lo dice explicitamente, ademas
del techo global (cambio 4).

**Calibracion, MEDIDA otra vez** (la seleccion cambio de "la mejor" a "la
mas baja que califica", asi que la calibracion de v1 no aplicaba sin
volver a medir -- `decision-making.md` SS4, verificar con datos reales, no
reusar un numero viejo sin comprobarlo): simulacion de 1000 lotes x 7
ratios inter/intra (2.0 a 8.0, jitter humano +-1s, el rango que este
modulo tiene que separar) con `UMBRAL_SALTO_RELATIVO_MINIMO=0.30`:
**cero** casos de dos productos reales fusionados en un mismo segmento
(across 8000 simulaciones); 36 casos (0.45%) de un producto partido de
mas por jitter interno (el fallo mas caro NUNCA aparecio; el mas barato,
casi nunca). Por separado, el escenario EXACTO de [INC-002] (ritmo normal
+ UN cambio anomalamente lento de 30-180s, barrido de 5/6/8 prendas, con y
sin jitter, 7680 combinaciones): **cero** fusiones. Subir el umbral
relativo (probado hasta 1.2) EMPEORA las cosas: un umbral mas alto hace
que mas lotes devuelvan `None` (sin base para cortar) y entonces NINGUN
hueco corta, ni siquiera el outlier de 120s -- la fusion total es el peor
resultado posible. 0.30 se queda.

### Cambio 2 -- CLIP en vez de pHash/colorhash
pHash y colorhash miden PIXELES (luminancia y tono). Un embedding CLIP
(`open_clip`, modelo `ViT-B-32-quickgelu` pre-entrenado `openai`, CPU,
local) mide SIGNIFICADO: entiende "camiseta" como concepto y compara
contenido visual completo (forma + color + textura + contexto), no un
histograma. Es la herramienta correcta para "esta foto es del mismo
producto que esa otra", que es literalmente lo que este modulo necesita
decidir -- no una mejora incremental de pHash, una metrica distinta.

**Descarga unica, luego offline:** la PRIMERA vez que se ejecuta en una
maquina, `open_clip` descarga los pesos (~350-600 MB segun el formato que
cachee `huggingface_hub`) a `~/.cache/huggingface/hub/`. Las llamadas
siguientes -- mismo proceso o un proceso nuevo -- reutilizan esa cache en
disco sin tocar la red. Cero llamadas de red POR PRODUCTO, cero coste. El
modelo corre en CPU (`device="cpu"` fijado explicitamente, nunca CUDA:
`requirements.txt` instala `torch` desde el indice CPU-only de PyTorch,
que pesa cientos de MB menos que el build con CUDA).

**Cache por sha256** (mismo patron que `core.images.obtener_o_crear_miniatura`:
escritura atomica fichero-temporal + `replace`, indexado por el hash del
CONTENIDO, no de la ruta): el embedding de una foto no se recalcula si ya
esta en `data/cache/embeddings_clip/<sha256>.npy`. Calcular un embedding
dos veces en un lote de cientos de fotos es tiempo tirado.

**Calibracion, MEDIDA, no a ojo -- y el limite HONESTO documentado.**
Midiendo con elipses solidas sobre fondo gris (el estilo de fixture que
`test_images.py`/v1 de este fichero ya usaban) CLIP casi no discrimina:
"roja" vs "naranja" dio 0.965 de similitud coseno, PRACTICAMENTE IGUAL que
"roja" vs una variante de brillo de si misma (0.93-0.97) -- una elipse
solida no tiene suficiente contenido semantico para que un modelo
entrenado con fotos reales saque nada de una foto sintetica sin textura.
Con un fixture mas realista (silueta de prenda + ruido de tela + fondo
neutro, ver `tests/test_grouping.py::_imagen_prenda`), la separacion
aparece: sobre 8 colores x (3 disparos + 2 variantes de brillo 0.9/1.1)
por color, la similitud INTRA-producto (mismo color, "otro disparo" o
brillo distinto) midio [0.985, 0.999]; la similitud INTER-color (misma
silueta, color distinto) midio [0.909, 0.973]. El margen es corto (0.012)
y UN par extremo (negro vs. gris muy claro) cayo justo en el borde
(0.973) -- documentado como miss conocido, no escondido. El caso
especifico que este cambio existe para cazar, rojo vs NARANJA (colores
ADYACENTES, el que colorhash media 2 y dejaba pasar), midio 0.924: bien
por debajo de cualquier variante legitima medida.
`UMBRAL_CLIP_SIMILITUD_MINIMA = 0.97` se eligio DENTRO de ese margen medido
(por debajo del minimo intra 0.985, por encima del caso que hay que
cazar 0.924) -- conservador hacia el lado de "marcar como distinta si hay
duda", coherente con `truth-loop.md`: perder una foto rara sin marcar
cuesta una devolucion; marcar de mas una foto legitima como "revisar"
cuesta un click de Diego. **Esta calibracion es sobre imagenes SINTETICAS,
no sobre las fotos reales de Diego -- se dice explicitamente, no se
esconde (`decision-making.md` SS6: no calibrar a ciegas). Por eso el
Cambio 4 existe.**

**Degradacion honesta si CLIP no carga** (modelo no descargado, `torch`/
`open_clip` no instalados, o cualquier fallo al cargar): NUNCA se cae en
silencio a pHash. Se registra con `logger.exception` (traceback completo)
UNA vez, y `agrupar()` fuerza confianza="baja" en TODOS los grupos del
lote, con un motivo que dice explicitamente que la confirmacion visual no
esta disponible (`_aplicar_degradacion_clip_no_disponible`, unico punto de
salida para este caso -- `decision-making.md` SS13, nunca un fallback
silencioso).

### Cambio 3 -- BIMODALIDAD, no "el intruso solitario"
v1 preguntaba, foto por foto. "?esta foto no se parece a NINGUNA otra del
grupo?". Con 3 rojas + 3 azules, cada foto tiene 2 hermanas del mismo
color -- la pregunta nunca dispara, aunque la senal (roja vs azul) sea
clarisima. La pregunta correcta es GLOBAL: "?este grupo se separa en mas
de un bloque visualmente consistente?".

Se responde con **componentes conexas** del grafo de similitud CLIP del
segmento (mismo algoritmo de union-find que ya se usaba para las fotos SIN
fecha EXIF, `_componentes_conexas` -- reutilizado, no reinventado): dos
fotos son aristas del grafo si su similitud CLIP >= `UMBRAL_CLIP_
SIMILITUD_MINIMA`. Si el grafo tiene 1 sola componente, el grupo es
visualmente consistente. Si tiene 2 o mas, el grupo es bimodal (o
multimodal) -- podria ser 3+3 (particion equitativa) o 7+1 (el intruso
solitario de v1): EL MISMO mecanismo cubre ambos casos, no hace falta
codigo especial para "el caso de una sola foto rara". `confianza="baja"` y
el motivo lista las sub-agrupaciones sugeridas para que Diego vea
exactamente por donde partir.

### Cambio 4 -- TECHO GLOBAL DE CONFIANZA: "media", nunca "alta" (todavia)
Decision de Diego, y la que cierra la clase de fallo entera: **si nada se
declara "alta", no puede haber nada "alta" y equivocado.** Los umbrales de
arriba (salto temporal, similitud CLIP) estan calibrados con SIMULACION e
imagenes SINTETICAS -- sobre las fotos reales de Diego seran otros
numeros, y calibrar a ciegas es simular con datos malos
(`decision-making.md` SS6, SS7 challenge-the-premise). Hasta que exista el
golden set (Fase 0, Diego fotografiandolo) y `/eval` recalibre estos
umbrales sobre fotos reales, NINGUN grupo puede salir "alta" -- ni
siquiera uno donde el temporal Y el visual coincidan limpio.

`TECHO_CONFIANZA` es una constante, aplicada en UN unico punto de salida
(`_aplicar_techo_confianza`, ultimo paso de `agrupar()`): trivial de ver
que esta puesto, trivial de quitar cuando `/eval` demuestre que revisar
grupos "media" le cuesta a Diego demasiado tiempo. La logica interna
(`_construir_grupo_temporal` etc.) SIGUE razonando en terminos de "alta"
candidata -- para que el motivo pueda explicar honestamente que perderia
el grupo si se levantara el techo -- pero el campo `confianza` que sale de
`agrupar()` nunca supera el techo. No se reparte "media" a mano por el
resto del modulo: un solo sitio, una sola constante.

## Como se deriva el corte temporal (`_umbral_corte`) -- resto sin cambios
1. Se ordenan las fotos CON fecha EXIF cronologicamente y se calculan los
   huecos entre disparos consecutivos.
2. Se buscan TODAS las fronteras (entre huecos ORDENADOS por magnitud) con
   salto relativo `(alto-bajo)/(bajo+0.5) >= UMBRAL_SALTO_RELATIVO_MINIMO`
   y se usa la MAS BAJA que califica (Cambio 1, arriba).
3. Si hay 0 fronteras que califican (o <2 huecos, o todos los huecos
   iguales/cero): `_umbral_corte` devuelve `(None, False)` -- sin base
   estadistica para cortar por tiempo. Ningun grupo que dependa de eso
   puede pasar de "media" [C2].
4. Si hay >=2 fronteras que califican: `ambiguo=True` -- hay mas de un
   "salto grande" candidato, no hay forma de afirmar cual es el real.
   Tambien capa a "media" [C1-ambiguo], ademas del margen de siempre
   (`FACTOR_MARGEN_ALTA`) [C1].

## La confirmacion visual (dentro de un grupo ya propuesto por tiempo)
Dentro de un grupo agrupado por tiempo se comprueba bimodalidad CLIP
(Cambio 3). >1 componente -> `confianza="baja"` con la sugerencia de
particion. Ninguna foto se expulsa del grupo -- eso seria decidir, no
proponer.

## Grupos adyacentes casi identicos -- posible producto partido [A2]
Sin cambios de diseno: si el borde de dos grupos temporales consecutivos
es visualmente casi identico por CLIP (en vez de pHash+colorhash), ambos
bajan a "media" como mucho y se sugiere revisar la fusion.

## Fotos SIN EXIF
Sin fecha que agrupar: se conectan por similitud CLIP (>= `UMBRAL_CLIP_
SIMILITUD_MINIMA`) via el mismo `_componentes_conexas`. Lo que no conecta
con nada es su propio grupo, "baja".

## Cero red, cero LLM, cero coste POR PRODUCTO
Este modulo no llama a ningun proveedor y no importa nada de `core/llm.py`
-- el modelo CLIP es local, corre en CPU, y solo toca red la PRIMERA vez
que se ejecuta en una maquina (descarga unica de los pesos). `torch` y
`open_clip` se importan de forma perezosa (dentro de `_cargar_modelo_clip`)
para que importar este modulo, o correr los tests que no dependen de CLIP,
no pague ese coste si no hace falta.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np

from core.images import MetadatosImagen, abrir_derecha, leer_metadatos, sha256_de_fichero

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constantes -- derivadas y documentadas, ninguna puesta "porque suena bien"
# --------------------------------------------------------------------------

# Salto relativo minimo, entre dos huecos consecutivos ORDENADOS por
# magnitud, para considerar esa frontera una candidata real "aqui cambia
# de producto" y no ruido de jitter humano. Ver docstring del modulo,
# Cambio 1, para la simulacion completa que lo calibra (1000 lotes x 7
# ratios inter/intra de 2.0 a 8.0, jitter +-1s: cero fusiones de productos
# reales; y el escenario exacto de [INC-002] -- ritmo normal + un cambio
# anomalamente lento de 30-180s -- barrido de 5/6/8 prendas, 7680
# combinaciones: cero fusiones). Subir este valor EMPEORA el resultado
# (mas lotes sin base para cortar en absoluto); 0.30 es el numero medido,
# no un valor redondo elegido a ojo.
UMBRAL_SALTO_RELATIVO_MINIMO = 0.30

# Segundos que se suman al hueco "bajo" al medir el salto relativo, para
# que un hueco casi-cero (rafaga de disparos casi simultaneos) no infle
# el salto relativo a valores absurdos por division entre un numero
# minusculo.
_EPSILON_SALTO_RELATIVO = 0.5

# Fraccion del umbral de corte que el hueco INTERNO mas grande de un grupo
# tiene que respetar para que ese grupo sea "alta" CANDIDATA [C1] (antes
# de que el techo global de Cambio 4 lo capa de todas formas). Heredado de
# v1 sin recalibrar: con el umbral ahora derivado de la frontera MAS BAJA
# que califica (Cambio 1), en vez de la de mayor salto, el numero que sale
# de `_umbral_corte` tiende a ser mas bajo/mas cercano al ritmo intra-
# producto que antes -- recalibrar este factor especificamente requiere
# fotos reales (golden set), no simulacion sintetica adicional. Diferido a
# `/eval`: mientras `TECHO_CONFIANZA="media"` este activo, este factor solo
# afecta el TEXTO del motivo (que candidatura interna tenia el grupo), no
# el resultado publicado -- ver Cambio 4.
FACTOR_MARGEN_ALTA = 0.7

# Modelo CLIP local (open_clip). "openai" son los pesos originales de
# OpenAI republicados via HuggingFace; "-quickgelu" es la variante de
# arquitectura que coincide con como se entrenaron esos pesos (usar
# "ViT-B-32" a secas con pretrained="openai" carga con la activacion
# equivocada -- open_clip avisa de ese "mismatch" en tiempo de carga).
_MODELO_CLIP_NOMBRE = "ViT-B-32-quickgelu"
_MODELO_CLIP_PRETRAINED = "openai"

# Similitud coseno minima (embeddings ya normalizados, asi que el
# producto punto ES la similitud coseno) para considerar dos fotos "la
# misma identidad visual". Ver docstring del modulo, Cambio 2, para la
# medicion completa. Elegido DENTRO del margen medido (minimo intra-
# producto 0.985, maximo inter-color 0.973, caso a cazar rojo/naranja
# 0.924): conservador hacia "marcar como distinta si hay duda" -- perder
# una foto cruzada sin marcar cuesta una devolucion; marcar de mas una
# variante legitima cuesta un click de revision.
UMBRAL_CLIP_SIMILITUD_MINIMA = 0.97

# --------------------------------------------------------------------------
# TECHO GLOBAL DE CONFIANZA -- Cambio 4. Decision de Diego, 2026-07-13, tras
# el SEGUNDO listing-audit BLOQUEANTE sobre este modulo ([INC-002] v2). Ver
# docstring del modulo para el razonamiento completo. Aplicado en UN unico
# punto de salida (`_aplicar_techo_confianza`, ultimo paso real de
# `agrupar()` antes de devolver) -- nunca repartido "a mano" por el resto
# del modulo, para que sea trivial de ver que esta puesto y trivial de
# quitar cuando `/eval` (sobre el golden set, fotos reales) demuestre que
# revisar grupos "media" le cuesta a Diego mas tiempo del que ahorra.
# --------------------------------------------------------------------------
TECHO_CONFIANZA: Literal["alta", "media", "baja"] = "media"

# Ubicacion por defecto de la cache de embeddings CLIP en disco (mismo
# patron que `core.store.DEFAULT_DATA_DIR`: raiz del repo / data / ...).
# `agrupar()` acepta `cache_dir` explicito para que los tests no escriban
# en la cache real del proyecto (`.claude/rules/decision-making.md` SS15:
# la cache no se borra nunca sin permiso -- por eso tampoco se ensucia con
# datos de test).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_EMBEDDINGS_DIR = _REPO_ROOT / "data" / "cache" / "embeddings_clip"


# --------------------------------------------------------------------------
# El contrato
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Grupo:
    """Una PROPUESTA de agrupacion de fotos por producto.

    `core/grouping.py` nunca cierra un grupo -- Diego confirma
    (`core/store.py` bloquea cualquier re-agrupacion despues de esa
    confirmacion). Ver `.claude/rules/truth-loop.md` SS E.

    fotos: en orden cronologico si el grupo tiene fecha EXIF. Si NINGUNA
        foto del grupo tiene EXIF, el mejor proxy disponible es el mtime
        del fichero -- documentado como tal en el motivo, nunca disfrazado
        de fecha real de disparo (ver `core/images.py::MetadatosImagen`).
    confianza: mientras `TECHO_CONFIANZA="media"` este activo (Cambio 4),
        NUNCA es "alta" -- ver docstring del modulo. "media" cuando la
        senal primaria (tiempo) o la de confirmacion (CLIP) no son
        concluyentes; "baja" cuando hay evidencia positiva de un
        problema (bimodalidad visual, sin base temporal Y sin visual,
        etc.) o un producto de una sola foto.
    motivo: en espanol, para que Diego entienda en un vistazo por que se
        agruparon o por que hay que revisarlas. Siempre reporta NUMEROS
        reales (hueco maximo, umbral usado, similitud CLIP) en vez de
        lenguaje de certeza cuando la realidad es mas ambigua.
    """

    fotos: list[Path]
    confianza: Literal["alta", "media", "baja"]
    motivo: str


_ORDEN_CONFIANZA: dict[str, int] = {"baja": 0, "media": 1, "alta": 2}


def _min_confianza(a: str, b: str) -> Literal["alta", "media", "baja"]:
    """La mas baja de las dos -- nunca se sube la confianza, solo se capa."""
    return a if _ORDEN_CONFIANZA[a] <= _ORDEN_CONFIANZA[b] else b  # type: ignore[return-value]


def agrupar(fotos: list[Path], cache_dir: Path | None = None) -> list[Grupo]:
    """Propone una agrupacion por producto a partir de un lote de fotos
    mezcladas. Nunca decide -- Diego confirma. Cero red, cero LLM, cero
    coste por producto (la descarga del modelo CLIP es unica por maquina,
    ver docstring del modulo).

    `cache_dir`: donde viven los embeddings CLIP cacheados por sha256.
    Por defecto `data/cache/embeddings_clip/` bajo la raiz del repo; los
    tests pasan un directorio temporal explicito para no ensuciar la
    cache real del proyecto.

    Estrategia (detalle completo en el docstring del modulo):
      1. Se cargan (o se intenta cargar, una vez) el modelo CLIP y se
         calcula el embedding de CADA foto del lote, cacheado por sha256.
         Si el modelo no carga, se seguira sin el (todo el lote sale
         "baja" al final, Cambio 2) -- nunca en silencio a pHash.
      2. Fotos CON fecha EXIF: se ordenan cronologicamente y se cortan por
         la frontera de hueco temporal MAS BAJA que califica como salto
         real (Cambio 1). Sin esa base, o si hay ambiguedad entre varias
         fronteras candidatas, el grupo no puede pasar de "media".
      3. Cada segmento se confirma por BIMODALIDAD del grafo de similitud
         CLIP (Cambio 3): si se separa en mas de una componente conexa,
         "baja" con la particion sugerida.
      4. Grupos temporales ADYACENTES cuyo borde es casi identico por CLIP
         bajan a "media" con una sugerencia de fusion -- podria ser un
         solo producto partido por el corte de tiempo [A2].
      5. Fotos SIN fecha EXIF nunca se mezclan con las anteriores. Se
         agrupan por componentes conexas de similitud CLIP; lo que no
         conecta con nada es su propio grupo, "baja".
      6. Techo global de confianza (Cambio 4): ningun grupo sale "alta"
         mientras `TECHO_CONFIANZA="media"` este activo.
    """
    if not fotos:
        return []

    cache_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_EMBEDDINGS_DIR

    clip_disponible = _cargar_modelo_clip()[0] is not None
    metadatos: dict[Path, MetadatosImagen] = {foto: leer_metadatos(foto) for foto in fotos}
    embeddings: dict[Path, np.ndarray | None] = {
        foto: _calcular_embedding_clip(foto, cache_dir) for foto in fotos
    }

    con_fecha = sorted(
        (foto for foto in fotos if metadatos[foto].fecha_captura_exif is not None),
        key=lambda foto: metadatos[foto].fecha_captura_exif,
    )
    sin_fecha = [foto for foto in fotos if metadatos[foto].fecha_captura_exif is None]

    grupos: list[Grupo] = []
    grupos.extend(_agrupar_por_tiempo(con_fecha, metadatos, embeddings))
    grupos.extend(_agrupar_por_similitud_sin_fecha(sin_fecha, metadatos, embeddings))

    grupos = _aplicar_degradacion_clip_no_disponible(grupos, clip_disponible)
    grupos = _aplicar_techo_confianza(grupos)
    return grupos


# --------------------------------------------------------------------------
# CLIP: carga perezosa del modelo, embeddings cacheados por sha256,
# similitud coseno. Cambio 2.
# --------------------------------------------------------------------------
_estado_modelo_clip: dict[str, object] = {}


def _cargar_modelo_clip() -> tuple[object | None, object | None]:
    """Carga (una vez por proceso, cacheado en memoria) el modelo CLIP
    local usado como confirmacion visual. Devuelve `(modelo, preprocess)`
    o `(None, None)` si no se pudo cargar -- NUNCA lanza, y quien llama
    (`agrupar`) DEBE forzar confianza="baja" en todo el lote si esto
    devuelve `None` (`_aplicar_degradacion_clip_no_disponible`). Nunca cae
    en silencio a una alternativa mas debil como pHash
    (`decision-making.md` SS13).

    La PRIMERA vez que se llama en una maquina nueva, `open_clip` descarga
    los pesos de `ViT-B-32` entrenados por OpenAI (~350-600 MB segun el
    formato que cachee `huggingface_hub`) a `~/.cache/huggingface/hub/`.
    Es una descarga UNICA: las llamadas siguientes (mismo proceso o
    procesos futuros en la misma maquina) reutilizan la cache en disco sin
    tocar la red -- cero llamadas de red por producto, cero coste.
    """
    if "cargado" in _estado_modelo_clip:
        return _estado_modelo_clip["modelo"], _estado_modelo_clip["preprocess"]  # type: ignore[return-value]

    try:
        import open_clip  # import perezoso -- ver docstring del modulo
        import torch

        torch.set_grad_enabled(False)
        modelo, _, preprocess = open_clip.create_model_and_transforms(
            _MODELO_CLIP_NOMBRE, pretrained=_MODELO_CLIP_PRETRAINED, device="cpu"
        )
        modelo.eval()
    except Exception:  # noqa: BLE001 — degradacion honesta, nunca silenciosa; ver docstring.
        logger.exception(
            "No se pudo cargar el modelo CLIP local (%s/%s). La confirmacion "
            "visual de agrupacion NO esta disponible en esta maquina: TODOS "
            "los grupos de este lote se marcaran confianza='baja'. Revisa "
            "que 'open-clip-torch' este instalado (requirements.txt) y que "
            "la descarga inicial del modelo haya podido completarse (hace "
            "falta red la PRIMERA vez que se usa en esta maquina).",
            _MODELO_CLIP_NOMBRE,
            _MODELO_CLIP_PRETRAINED,
        )
        _estado_modelo_clip.update(cargado=True, modelo=None, preprocess=None)
        return None, None

    _estado_modelo_clip.update(cargado=True, modelo=modelo, preprocess=preprocess)
    return modelo, preprocess


def _sha256_seguro(ruta: Path) -> str | None:
    """`sha256_de_fichero` envuelto para no abortar el lote entero por un
    fichero corrupto/ilegible -- mismo patron de frontera de errores que
    el resto del proyecto: se registra con traceback completo y se
    devuelve `None`, nunca una excepcion sin capturar."""
    try:
        return sha256_de_fichero(ruta)
    except Exception:  # noqa: BLE001 — frontera "un fichero del lote", documentada en core/images.py.
        logger.exception("No se pudo calcular sha256 de %s", ruta)
        return None


def _calcular_embedding_clip(ruta: Path, cache_dir: Path) -> np.ndarray | None:
    """Embedding CLIP normalizado (norma 1) de una foto, cacheado en disco
    por sha256 del CONTENIDO del fichero (mismo patron de escritura
    atomica -- fichero temporal + `replace` -- que
    `core.images.obtener_o_crear_miniatura`). Devuelve `None` si el modelo
    no esta disponible, si el fichero no se puede leer, o si el calculo
    falla por cualquier otra razon -- siempre registrado, nunca en
    silencio."""
    modelo, preprocess = _cargar_modelo_clip()
    if modelo is None:
        return None

    sha = _sha256_seguro(ruta)
    if sha is None:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    ruta_cache = cache_dir / f"{sha}.npy"
    if ruta_cache.exists():
        try:
            with ruta_cache.open("rb") as fh:
                return np.load(fh)
        except Exception:  # noqa: BLE001 — cache corrupta: se recalcula, no se aborta el lote.
            logger.exception(
                "Cache de embedding CLIP corrupta para %s (%s); se recalcula", ruta, ruta_cache
            )

    try:
        import torch

        img = abrir_derecha(ruta)
        if img.mode != "RGB":
            img = img.convert("RGB")
        with torch.no_grad():
            tensor = preprocess(img).unsqueeze(0)  # type: ignore[operator]
            vector = modelo.encode_image(tensor)  # type: ignore[attr-defined]
            vector = vector / vector.norm(dim=-1, keepdim=True)
        embedding = vector.squeeze(0).cpu().numpy().astype(np.float32)
    except Exception:  # noqa: BLE001 — frontera "un fichero del lote", documentada en core/images.py.
        logger.exception("No se pudo calcular el embedding CLIP de %s", ruta)
        return None

    ruta_tmp = ruta_cache.with_name(ruta_cache.name + ".tmp")
    try:
        with ruta_tmp.open("wb") as fh:
            np.save(fh, embedding)
        ruta_tmp.replace(ruta_cache)
    except OSError:
        logger.exception("No se pudo escribir la cache de embedding CLIP de %s", ruta)

    return embedding


def _similitud_clip(a: np.ndarray, b: np.ndarray) -> float:
    """Similitud coseno entre dos embeddings YA normalizados -- el
    producto punto es directamente la similitud coseno."""
    return float(np.dot(a, b))


# --------------------------------------------------------------------------
# Fotos CON fecha EXIF: corte por hueco temporal + confirmacion visual
# --------------------------------------------------------------------------
def _agrupar_por_tiempo(
    con_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    embeddings: dict[Path, np.ndarray | None],
) -> list[Grupo]:
    if not con_fecha:
        return []

    if len(con_fecha) == 1:
        foto = con_fecha[0]
        return [
            Grupo(
                fotos=[foto],
                confianza="baja",
                motivo=(
                    "Una sola foto con fecha EXIF en todo el lote: no hay "
                    "ninguna otra con la que comparar el timestamp ni la "
                    "imagen. Revisa si faltan mas fotos de este producto."
                ),
            )
        ]

    fechas = [metadatos[foto].fecha_captura_exif for foto in con_fecha]
    huecos = [(fechas[i + 1] - fechas[i]).total_seconds() for i in range(len(fechas) - 1)]
    umbral, ambiguo = _umbral_corte(huecos)

    segmentos: list[list[Path]] = [[con_fecha[0]]]
    for i, hueco in enumerate(huecos):
        if umbral is not None and hueco > umbral:
            segmentos.append([])
        segmentos[-1].append(con_fecha[i + 1])

    grupos = [
        _construir_grupo_temporal(seg, metadatos, embeddings, umbral, ambiguo) for seg in segmentos
    ]
    return _sugerir_fusion_adyacentes(grupos, embeddings)


def _umbral_corte(huecos: list[float]) -> tuple[float | None, bool]:
    """Deriva el umbral de "hueco grande" -- Cambio 1: busca TODAS las
    fronteras (entre huecos ORDENADOS por magnitud) cuyo salto relativo
    `(alto-bajo)/(bajo+0.5)` supera `UMBRAL_SALTO_RELATIVO_MINIMO`, y usa
    la MAS BAJA que califica (nunca la de mayor salto -- ver docstring del
    modulo para por que "la mejor" se deja aplastar por un outlier
    temporal). Devuelve `(umbral, ambiguo)`:

      - `umbral=None` si no hay ninguna frontera candidata: menos de 2
        huecos, todos los huecos en 0 (rafaga con timestamps identicos), o
        ninguna frontera supera el umbral relativo minimo. Sin base
        estadistica para cortar por tiempo -- el techo de confianza de
        cualquier grupo que dependa de esto queda capado a "media" [C2].
      - `ambiguo=True` si hay 2 O MAS fronteras candidatas: hay mas de un
        "salto grande" en la distribucion y no hay forma de afirmar cual
        es el cambio de producto real -- tambien capa el techo a "media"
        [C1-ambiguo], independientemente del techo global (Cambio 4).
    """
    if len(huecos) < 2:
        return None, False

    valores = sorted(huecos)
    if valores[-1] <= 0:
        return None, False  # rafaga perfecta: todos los huecos son 0

    candidatas: list[tuple[float, float, float]] = []
    for i in range(len(valores) - 1):
        bajo, alto = valores[i], valores[i + 1]
        salto = alto - bajo
        if salto <= 0:
            continue
        salto_relativo = salto / (bajo + _EPSILON_SALTO_RELATIVO)
        if salto_relativo >= UMBRAL_SALTO_RELATIVO_MINIMO:
            candidatas.append((bajo, alto, salto_relativo))

    if not candidatas:
        return None, False

    bajo, alto, _salto = candidatas[0]  # la MAS BAJA que califica -- Cambio 1
    umbral = (bajo + alto) / 2
    ambiguo = len(candidatas) >= 2
    return umbral, ambiguo


def _construir_grupo_temporal(
    seg: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    embeddings: dict[Path, np.ndarray | None],
    umbral: float | None,
    ambiguo: bool,
) -> Grupo:
    if len(seg) == 1:
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                "Una sola foto en este tramo de tiempo (hueco grande a los "
                "lados, o extremo del lote): no hay ninguna otra con la que "
                "confirmarla visualmente. Revisa si falta alguna foto de "
                "este producto."
            ),
        )

    n = len(seg)
    duracion_total = (
        metadatos[seg[-1]].fecha_captura_exif - metadatos[seg[0]].fecha_captura_exif
    ).total_seconds()
    huecos_seg = [
        (metadatos[seg[i + 1]].fecha_captura_exif - metadatos[seg[i]].fecha_captura_exif).total_seconds()
        for i in range(n - 1)
    ]
    hueco_maximo = max(huecos_seg)

    # [C2] Sin umbral derivable para el lote, ningun grupo puede llegar a
    # "alta": no hay señal primaria que lo respalde, por muy parecidas
    # que se vean las fotos entre si.
    if umbral is None:
        techo: Literal["alta", "media"] = "media"
        motivo_tiempo = (
            f"{n} fotos en {duracion_total:.0f} s (hueco maximo interno "
            f"{hueco_maximo:.0f} s). El lote no tiene huecos suficientes "
            "para derivar un umbral de corte por tiempo, asi que no hay "
            "señal primaria que confirme donde empieza y acaba cada "
            "producto -- techo de confianza: media."
        )
    elif ambiguo:
        # [C1-ambiguo] Cambio 1: hay 2+ fronteras candidatas en el lote --
        # ambiguedad real sobre donde cambia el producto, aunque ESTE
        # segmento en concreto haya quedado bien cortado.
        techo = "media"
        motivo_tiempo = (
            f"{n} fotos en {duracion_total:.0f} s (hueco maximo interno "
            f"{hueco_maximo:.0f} s, umbral de corte del lote {umbral:.0f} s). "
            "El lote tiene MAS DE UNA frontera de tiempo candidata a "
            "'aqui cambia el producto' -- hay ambiguedad real sobre donde "
            "esta el limite, asi que el techo de confianza es media aunque "
            "este segmento en concreto parezca bien cortado."
        )
    else:
        margen = hueco_maximo / umbral if umbral > 0 else 1.0
        if margen > FACTOR_MARGEN_ALTA:
            techo = "media"
            motivo_tiempo = (
                f"{n} fotos en {duracion_total:.0f} s. El hueco mas grande "
                f"dentro de este grupo ({hueco_maximo:.0f} s) no queda "
                f"claramente por debajo del umbral de corte de este lote "
                f"({umbral:.0f} s) -- margen insuficiente para confianza "
                "alta."
            )
        else:
            techo = "alta"
            motivo_tiempo = (
                f"{n} fotos en {duracion_total:.0f} s, timestamps EXIF "
                f"seguidos (hueco maximo interno {hueco_maximo:.0f} s, "
                f"claramente por debajo del umbral de corte de este lote "
                f"de {umbral:.0f} s)."
            )

    sin_embedding = [foto.name for foto in seg if embeddings.get(foto) is None]
    if sin_embedding:
        return Grupo(
            fotos=seg,
            confianza=_min_confianza(techo, "media"),
            motivo=(
                f"{motivo_tiempo} No se pudo calcular la confirmacion visual "
                f"(CLIP) de {', '.join(sin_embedding)}. Revisalo a mano."
            ),
        )

    componentes = _componentes_conexas(
        seg, _pares_por_similitud(seg, embeddings, UMBRAL_CLIP_SIMILITUD_MINIMA)
    )
    if len(componentes) > 1:
        detalle = _describir_componentes(componentes)
        logger.info("Grupo temporal bimodal (CLIP): %s", detalle)
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                f"{motivo_tiempo} Pero visualmente el grupo se separa en "
                f"{len(componentes)} sub-grupos distintos ({detalle}) -- "
                "posible mezcla de productos. Revisa antes de confirmar."
            ),
        )

    return Grupo(
        fotos=seg,
        confianza=techo,
        motivo=f"{motivo_tiempo} Visualmente consistentes (confirmado por CLIP).",
    )


def _pares_por_similitud(
    fotos: list[Path], embeddings: dict[Path, np.ndarray | None], umbral: float
) -> list[tuple[Path, Path, float]]:
    """Pares de `fotos` cuya similitud CLIP >= `umbral` -- las ARISTAS del
    grafo de similitud que alimenta `_componentes_conexas`. Tolera
    embeddings `None` (los salta, esa foto simplemente no gana ninguna
    arista): en los segmentos temporales ya se filtraron antes como
    confirmacion inconclusa (`_construir_grupo_temporal`), pero las fotos
    sin fecha EXIF no pasan por ese filtro previo -- aqui una foto sin
    embedding termina, correctamente, como su propia componente de una
    foto en vez de romper la funcion."""
    pares: list[tuple[Path, Path, float]] = []
    for i in range(len(fotos)):
        for j in range(i + 1, len(fotos)):
            a, b = fotos[i], fotos[j]
            ea, eb = embeddings.get(a), embeddings.get(b)
            if ea is None or eb is None:
                continue
            sim = _similitud_clip(ea, eb)
            if sim >= umbral:
                pares.append((a, b, sim))
    return pares


def _describir_componentes(componentes: list[list[Path]]) -> str:
    ordenados = sorted(componentes, key=len, reverse=True)
    return "; ".join("{" + ", ".join(f.name for f in c) + "}" for c in ordenados)


# --------------------------------------------------------------------------
# [A2] Grupos temporales adyacentes casi identicos -> posible producto partido
# --------------------------------------------------------------------------
_AVISO_FUSION = (
    " Aviso: la foto del borde con el grupo vecino es visualmente casi "
    "identica (confirmado por CLIP) -- podria ser UN solo producto partido "
    "en dos por el corte de tiempo. Revisa si hay que fusionarlos."
)


def _sugerir_fusion_adyacentes(
    grupos: list[Grupo], embeddings: dict[Path, np.ndarray | None]
) -> list[Grupo]:
    """Si dos grupos temporales CONSECUTIVOS tienen las fotos de su borde
    (la ultima del primero, la primera del segundo) casi identicas por
    CLIP, es mas probable que sea un solo producto partido por un hueco de
    tiempo mas largo de lo normal que dos productos distintos. Ambos
    grupos bajan a "media" como mucho (nunca se sube la confianza) y se
    avisa en el motivo -- Diego decide si fusiona."""
    if len(grupos) < 2:
        return grupos

    resultado = list(grupos)
    for i in range(len(resultado) - 1):
        a, b = resultado[i], resultado[i + 1]
        if not a.fotos or not b.fotos:
            continue
        ultima_a, primera_b = a.fotos[-1], b.fotos[0]
        e_a, e_b = embeddings.get(ultima_a), embeddings.get(primera_b)
        if e_a is None or e_b is None:
            continue  # sin embedding de confirmacion en el borde: no se puede evaluar, no se sugiere nada

        parecen_el_mismo = _similitud_clip(e_a, e_b) >= UMBRAL_CLIP_SIMILITUD_MINIMA
        if not parecen_el_mismo:
            continue

        for idx in (i, i + 1):
            nueva_confianza = _min_confianza(resultado[idx].confianza, "media")
            nuevo_motivo = resultado[idx].motivo
            if _AVISO_FUSION.strip() not in nuevo_motivo:
                nuevo_motivo = nuevo_motivo + _AVISO_FUSION
            resultado[idx] = replace(resultado[idx], confianza=nueva_confianza, motivo=nuevo_motivo)
    return resultado


# --------------------------------------------------------------------------
# Fotos SIN fecha EXIF: solo similitud CLIP, o cada una su propio grupo
# --------------------------------------------------------------------------
def _agrupar_por_similitud_sin_fecha(
    sin_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    embeddings: dict[Path, np.ndarray | None],
) -> list[Grupo]:
    if not sin_fecha:
        return []

    pares = _pares_por_similitud(sin_fecha, embeddings, UMBRAL_CLIP_SIMILITUD_MINIMA)
    componentes = _componentes_conexas(sin_fecha, pares)

    grupos: list[Grupo] = []
    for componente in componentes:
        ordenada = sorted(componente, key=lambda foto: _clave_orden_sin_fecha(foto, metadatos))
        if len(ordenada) == 1:
            foto = ordenada[0]
            if embeddings.get(foto) is None:
                motivo = (
                    "Esta foto no tiene fecha EXIF y no se pudo analizar "
                    "visualmente (CLIP: fichero ilegible/corrupto, o el modelo "
                    "no esta disponible en esta maquina). Revisala a mano."
                )
            else:
                motivo = (
                    "Esta foto no tiene fecha y no se parece visualmente "
                    "(CLIP) a ninguna otra del lote. Queda sola: revisala a "
                    "mano."
                )
            grupos.append(Grupo(fotos=[foto], confianza="baja", motivo=motivo))
        else:
            grupos.append(
                Grupo(
                    fotos=ordenada,
                    confianza="media",
                    motivo=(
                        f"{len(ordenada)} fotos sin fecha EXIF pero casi "
                        "identicas visualmente (confirmado por CLIP): "
                        "probablemente el mismo producto. Sin timestamp no se "
                        "puede confirmar el orden real de disparo (se ordenan "
                        "por fecha del fichero, no de camara)."
                    ),
                )
            )
    return grupos


def _componentes_conexas(
    nodos: list[Path], pares: list[tuple[Path, Path, float]]
) -> list[list[Path]]:
    """Union-find minimo: agrupa `nodos` conectados (directa o
    transitivamente) por algun par en `pares`. Un nodo sin ningun par es
    su propia componente de tamano 1. Cambio 3: este es el mecanismo de
    deteccion de BIMODALIDAD -- 1 componente = grupo visualmente
    consistente, 2+ componentes = el grupo se separa en bloques distintos
    (particion equitativa 3+3, o el intruso solitario de v1, ambos
    cubiertos por el mismo algoritmo)."""
    padre: dict[Path, Path] = {nodo: nodo for nodo in nodos}

    def encontrar(x: Path) -> Path:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a: Path, b: Path) -> None:
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[ra] = rb

    for a, b, _peso in pares:
        unir(a, b)

    componentes: dict[Path, list[Path]] = {}
    for nodo in nodos:
        raiz = encontrar(nodo)
        componentes.setdefault(raiz, []).append(nodo)
    return list(componentes.values())


def _clave_orden_sin_fecha(
    foto: Path, metadatos: dict[Path, MetadatosImagen]
) -> tuple[int, object]:
    """Sin EXIF no hay cronologia real que ordenar. El mejor proxy
    disponible, honesto y documentado, es el mtime del fichero; si tampoco
    existe, el nombre. Nunca se disfraza de fecha de disparo real (ver
    `core/images.py::MetadatosImagen`)."""
    meta = metadatos[foto]
    if meta.mtime_fichero is not None:
        return (0, meta.mtime_fichero)
    return (1, foto.name)


# --------------------------------------------------------------------------
# Cambio 2 -- degradacion honesta si CLIP no esta disponible; Cambio 4 --
# techo global de confianza. Dos puntos de salida distintos y explicitos:
# ambos son "nunca alta" cuando estan activos, pero por razones distintas
# (uno es permanente hasta que Diego instale/descargue el modelo; el otro
# es temporal hasta que exista el golden set). Se aplican en este orden al
# final de `agrupar()` -- nunca repartidos por el resto del modulo.
# --------------------------------------------------------------------------
_MOTIVO_CLIP_NO_DISPONIBLE = (
    " [CLIP no disponible: el modelo de confirmacion visual no se pudo "
    "cargar en esta maquina (ver logs de la aplicacion) -- no hay ninguna "
    "senal visual que confirme que las fotos de este grupo son del mismo "
    "producto, solo el timestamp (si lo hay). Confianza forzada a 'baja'. "
    "Revisa este grupo a mano.]"
)


def _aplicar_degradacion_clip_no_disponible(grupos: list[Grupo], disponible: bool) -> list[Grupo]:
    """Si el modelo CLIP no se pudo cargar, TODO el lote sale
    confianza='baja' -- no solo capada a 'media' como el techo global, una
    degradacion mas fuerte porque aqui no hay NINGUNA senal visual, ni
    siquiera una imperfecta. Nunca en silencio (`decision-making.md`
    SS13): el motivo lo dice explicitamente en cada grupo."""
    if disponible:
        return grupos
    return [replace(g, confianza="baja", motivo=g.motivo + _MOTIVO_CLIP_NO_DISPONIBLE) for g in grupos]


def _aplicar_techo_confianza(grupos: list[Grupo]) -> list[Grupo]:
    """Cambio 4 -- unico punto de salida de `TECHO_CONFIANZA`. Ver
    docstring del modulo y el comentario junto a la constante."""
    resultado: list[Grupo] = []
    for g in grupos:
        capada = _min_confianza(g.confianza, TECHO_CONFIANZA)
        if capada != g.confianza:
            motivo = g.motivo + (
                f" [Techo de confianza activo: este grupo calificaria para "
                f"'{g.confianza}' pero TECHO_CONFIANZA='{TECHO_CONFIANZA}' lo "
                "limita hasta que el golden set (fotos reales) recalibre los "
                "umbrales de este modulo -- ver docstring de core/grouping.py.]"
            )
        else:
            motivo = g.motivo
        resultado.append(replace(g, confianza=capada, motivo=motivo))
    return resultado
