"""core/grouping.py — Costura de AGRUPACION (RESELLERMASTER).

Superficie SENSIBLE (`.claude/rules/truth-loop.md` SS B y SS E). Una foto
en la ficha del producto equivocado es el fallo mas caro y mas silencioso
del proyecto: se publica, y nadie lo caza. Este modulo PROPONE grupos;
Diego CONFIRMA (`core/store.py` bloquea cualquier re-agrupacion despues de
esa confirmacion, ver su docstring). Ninguna funcion de aqui cierra nada.

## [INC-002] -- por que este fichero se reescribio
La primera version usaba `mediana(huecos) * 3` como umbral de corte y
pHash como unica confirmacion visual. Los dos fallaban EXACTAMENTE con el
ritmo real de un lote (`.claude/incident-ledger.md`):
  - Una mediana no separa outliers -- el "cambio de producto" es un hueco
    que aparece en ~30% de los casos con el ritmo real de Diego (3 fotos
    por prenda), no un 5% raro. Con 8 prendas de 3 fotos a 4s/12s, la
    mediana de huecos es 4 y `4*3=12`, y `12 > 12` es FALSO: cero cortes,
    8 prendas en un solo grupo, `confianza="alta"`.
  - pHash promedia a luminancia: una camiseta roja y una azul con la
    MISMA forma tienen distancia de pHash 0. Es ciego al color, que es
    justo el discriminante mas comun en ropa.
Ambos fallos se combinaban: nada cortaba por tiempo, y nada lo cazaba por
imagen. Ver el resto de este docstring para el diseño que lo reemplaza.

## La tesis: el timestamp EXIF, no los pixeles
La senal PRIMARIA de agrupacion sigue siendo CUANDO se disparo cada foto
(`fecha_captura_exif`, de `core/images.py::leer_metadatos`): las fotos de
un mismo producto se disparan seguidas (segundos/minutos); entre un
producto y el siguiente hay un hueco. Es determinista, gratis, y NO puede
alucinar. El pHash + colorhash son CONFIRMACION dentro de un grupo ya
propuesto por tiempo -- nunca el criterio de corte.

## Como se deriva el corte temporal (`_umbral_corte`)
1. Se ordenan las fotos CON fecha EXIF cronologicamente y se calculan los
   huecos entre disparos consecutivos.
2. NO se usa una mediana (no separa outliers cuando el hueco "grande"
   ocurre en una fraccion no despreciable de los huecos -- ver arriba).
   Se busca el mayor SALTO RELATIVO entre huecos consecutivos ORDENADOS
   por magnitud: se ordenan los huecos de menor a mayor, y se mide, para
   cada frontera entre un hueco y el siguiente, `(alto - bajo) / (bajo +
   0.5)` (el `+0.5` evita que un hueco casi-cero infle el salto
   artificialmente). La frontera con mayor salto relativo es la mejor
   candidata a "aqui cambia de producto": separa el bulto de huecos
   INTRA-producto (ritmo de disparo) del bulto de huecos INTER-producto
   (tiempo de cambiar de prenda), sea cual sea la proporcion entre ambos
   bultos -- a diferencia de un metodo de outliers (IQR, z-score), este
   SI funciona cuando el bulto "grande" es ~30% de los datos.
3. Ese salto tiene que superar `UMBRAL_SALTO_RELATIVO_MINIMO` para
   considerarse una frontera real y no ruido de jitter humano (ver
   docstring de la constante para la simulacion que lo calibra). Si no
   la supera, o si todos los huecos son iguales (ritmo perfectamente
   constante -- no hay frontera que buscar), o si hay menos de 2 huecos,
   `_umbral_corte` devuelve `None`: **sin base estadistica para cortar
   por tiempo**.
4. El umbral final es el punto medio entre el hueco mas alto del bulto
   bajo y el mas bajo del bulto alto.

## `None` no es un fallo silencioso -- baja el TECHO de confianza [C2]
Cuando `_umbral_corte` devuelve `None`, NO se corta nada por tiempo (todas
las fotos CON fecha quedan en un unico grupo), y **la confianza de ese
grupo tiene un techo de `"media"`, nunca `"alta"`**: sin señal primaria
que lo respalde, no hay base para la maxima confianza aunque las fotos se
parezcan entre si. El motivo lo dice explicitamente ("no hay huecos
suficientes para separar productos por tiempo"). Esto es lo que arregla
los dos casos rotos de [INC-002]: dos productos con una foto cada uno
separados 10 minutos (1 solo hueco: sin base) y una rafaga con todos los
timestamps identicos (huecos todos en 0: sin base) ya NO pueden salir
`"alta"`.

## El techo tambien baja si el corte SI existe pero el margen es corto [C1]
Aunque `_umbral_corte` derive un umbral, un grupo concreto solo puede
llegar a `"alta"` si su hueco INTERNO mas grande queda CLARAMENTE por
debajo de ese umbral (`FACTOR_MARGEN_ALTA`, ver constante). Si el hueco
mayor del grupo esta cerca del umbral de corte -- el jitter humano pudo
haber estado a un pelo de fundir dos productos o partir uno -- el techo
baja a `"media"` y el motivo reporta el hueco real y el umbral usado, en
vez de fingir una certeza que no hay.

## La confirmacion visual (dentro de un grupo ya propuesto por tiempo)
Dentro de un grupo agrupado por tiempo se comprueban DOS senales
independientes, cualquiera de las dos puede bajar el grupo a
`confianza="baja"` [C3]:
  - **Forma** (pHash, `UMBRAL_PHASH_MUY_DISTINTA`): si una foto no se
    parece EN FORMA a ninguna otra del grupo.
  - **Color** (`imagehash.colorhash`, `UMBRAL_COLORHASH_MUY_DISTINTA`):
    si una foto no se parece EN COLOR a ninguna otra del grupo. Esto es
    lo que arregla que una camiseta roja y una azul, indistinguibles para
    pHash, ya no pasen desapercibidas.
La foto NO se expulsa del grupo -- eso seria decidir, no proponer. Es
justo el caso que existe para cazar: una foto de otro producto (por forma
O por color) colada por error en medio de una sesion de fotos.

## Grupos adyacentes casi identicos -- posible producto partido [A2]
Si el corte por tiempo separa dos grupos consecutivos cuyas fotos del
borde (la ultima del primero, la primera del segundo) son casi identicas
en FORMA Y COLOR, lo mas probable es que sea UN solo producto partido en
dos por un hueco de tiempo mas largo de lo normal (p. ej. Diego se paro a
reencuadrar). Ambos grupos bajan a `confianza="media"` (nunca a mas de lo
que ya tenian) y el motivo de los dos sugiere revisar la fusion. No se
fusionan solos -- eso lo decide Diego.

## Fotos SIN EXIF
Es el caso real (fotos reenviadas por WhatsApp, capturas de pantalla,
descargas de otro sitio). No hay timestamp que agrupar, y forzarlas en un
grupo por tiempo para que "cuadre" seria mentir sobre la procedencia. Se
intenta el UNICO criterio que queda sin inventar nada nuevo: si dos fotos
sin fecha son CASI-DUPLICADAS por pHash (el MISMO umbral que
`core.images.duplicados_similares` ya usa para deduplicacion) Y ADEMAS no
difieren en color mas de `UMBRAL_COLORHASH_MUY_DISTINTA` [C3], se
agrupan. Antes bastaba el pHash solo -- y cuatro camisetas de colores
distintos con la misma silueta pasaban el umbral de casi-duplicado y
salian como un solo producto ([INC-002]). Ahora hace falta forma Y color.
Lo que no conecta con nada es su propio grupo, `confianza="baja"`, con un
motivo honesto.

## Cero red, cero LLM, cero coste
Este modulo solo usa lo que `core/images.py` ya calcula (EXIF, sha256,
pHash) mas `imagehash.colorhash` (misma libreria, cero dependencia
nueva). No llama a ningun proveedor, no importa nada de `core/llm.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import imagehash

from core.images import (
    MetadatosImagen,
    RegistroHash,
    abrir_derecha,
    duplicados_similares,
    hashear_lote,
    leer_metadatos,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constantes -- derivadas y documentadas, ninguna puesta "porque suena bien"
# --------------------------------------------------------------------------

# Salto relativo minimo, entre dos huecos consecutivos ORDENADOS por
# magnitud, para considerarlo una frontera real "aqui cambia de producto"
# y no ruido de jitter humano (ver `_umbral_corte`). Calibrado por
# SIMULACION (no a ojo): con jitter humano fijo de +-1s (la convencion de
# `tests/test_grouping.py`), el salto relativo minimo observado en 200
# simulaciones de 8 productos x 3 fotos con ratio inter/intra = 2.0 (el
# caso MAS dificil que este modulo tiene que separar) fue 0.37; el salto
# relativo espurio mas alto observado en un ritmo de UN SOLO producto (sin
# cambio real) con el mismo jitter, barriendo de 3 a 30 fotos y ritmos de
# 1.5 a 20s, fue 1.97 (unicamente en el caso extremo de un ritmo de
# rafaga de 1.5s con jitter +-1s, es decir jitter comparable al propio
# ritmo -- ya en el limite de lo fisicamente disparable a mano). Un umbral
# de 0.30 deja margen holgado por debajo del caso real minimo (0.37) y
# confia en la red de seguridad de [A2] (grupos adyacentes casi
# identicos -> sugerir fusion) para el residuo de casos adversariales
# donde un ritmo anormalmente rapido con jitter extremo cortase de mas.
UMBRAL_SALTO_RELATIVO_MINIMO = 0.30

# Segundos que se suman al hueco "bajo" al medir el salto relativo, para
# que un hueco casi-cero (rafaga de disparos casi simultaneos) no infle
# el salto relativo a valores absurdos por division entre un numero
# minusculo.
_EPSILON_SALTO_RELATIVO = 0.5

# Fraccion del umbral de corte que el hueco INTERNO mas grande de un
# grupo tiene que respetar para que ese grupo pueda llegar a
# `confianza="alta"` [C1]. Si el hueco mayor del grupo es igual o mayor
# que este porcentaje del umbral, el jitter pudo haber estado a un paso
# de fundir dos productos (o de partir uno) -- no hay margen para la
# maxima confianza. 0.7 se eligio porque en el caso exacto de [INC-002]
# (8 prendas, 3 fotos/prenda, 4s/12s, SIN jitter) el hueco interno maximo
# (4s) es la mitad del umbral derivado (~8s) -- 0.5, con margen de sobra
# por debajo de 0.7 -- mientras que en el barrido de ratio 2.0 CON jitter
# +-1s el hueco interno puede acercarse a 5s frente a un umbral de ~6s
# (~0.83, por encima de 0.7): ese caso limite queda capado a "media" a
# proposito, en vez de fingir una certeza que el jitter pudo haber roto.
FACTOR_MARGEN_ALTA = 0.7

# Distancia de Hamming de pHash (64 bits, `imagehash.phash` por defecto) a
# partir de la cual dos fotos ya no comparten NADA de estructura visible.
# Calibrado EMPIRICAMENTE, no a ojo (medido sobre imagenes sinteticas
# comparables a las de `tests/test_images.py`):
#   - variaciones legitimas del MISMO objeto (recorte leve, cambio de
#     brillo) midieron <=18 de distancia;
#   - contenido sin ninguna relacion (color solido, patron de tablero,
#     ruido aleatorio) midio >=31.
# 24 deja margen holgado a ambos lados. Es DELIBERADAMENTE alto (mas cerca
# del "sin relacion" que del "casi identico"): pHash mide composicion de
# pixeles, no identidad del producto, y dos fotos legitimas del MISMO
# articulo desde angulos muy distintos pueden diferir bastante sin ser una
# foto cruzada. Un umbral mas bajo generaria demasiados falsos "dudoso" y
# ahogaria la senal; se prefiere que solo dispare ante un contraste
# brutal, que es la situacion real que este chequeo existe para cazar.
UMBRAL_PHASH_MUY_DISTINTA = 24

# Distancia de Hamming de `imagehash.colorhash` (binbits=3 por defecto, 42
# bits) a partir de la cual dos fotos tienen un color CLARAMENTE distinto.
# pHash es ciego al color (promedia a luminancia: una camiseta roja y una
# azul con la misma silueta miden distancia 0 -- ver [INC-002]);
# `colorhash` si distingue tono/saturacion. Calibrado EMPIRICAMENTE con
# las MISMAS variaciones "legitimas" que ya usa este fichero de test
# (`_variante_leve` con factor de brillo 0.9/1.1, `_recomprimir_jpeg` a
# calidad 60 -- no un barrido inventado aparte) sobre roja/azul/negra/
# blanca (la paleta del caso real de [INC-002]):
#   - variaciones legitimas del MISMO color midieron <=2 de distancia;
#   - pares de colores CLARAMENTE distintos (roja-azul=6, roja-negra=5,
#     roja-blanca=4, azul-negra=5, azul-blanca=4, negra-blanca=3)
#     midieron >=3.
# Limite honesto, documentado en vez de escondido: `colorhash` usa solo 6
# cubos de tono, asi que colores ADYACENTES en el circulo cromatico (rojo
# y naranja, o dos grises de luminancia parecida) pueden seguir midiendo
# poco. No es un clasificador de color universal -- es, como pHash, una
# confirmacion que dispara ante un contraste claro y se ABSTIENE (no baja
# a "baja") ante uno sutil, coherente con "ante la duda, no alucines".
UMBRAL_COLORHASH_MUY_DISTINTA = 2


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
    confianza: "alta" SOLO cuando la senal primaria (EXIF, huecos
        cortos, consistentes Y con margen claro por debajo del umbral de
        corte -- ver [C1]/[C2] en el docstring del modulo) Y la de
        confirmacion visual (pHash + colorhash, fotos parecidas en forma
        Y color) coinciden limpio. Cualquier duda -> "media" o "baja". Un
        grupo de una sola foto es SIEMPRE "baja" (`truth-loop.md` SS E:
        "un producto con una sola foto [...] marcar como grupo dudoso, no
        colarlo en silencio").
    motivo: en espanol, para que Diego entienda en un vistazo por que se
        agruparon o por que hay que revisarlas. Siempre reporta NUMEROS
        reales (hueco maximo, umbral usado) en vez de lenguaje de certeza
        ("timestamps seguidos") cuando la realidad es mas ambigua.
    """

    fotos: list[Path]
    confianza: Literal["alta", "media", "baja"]
    motivo: str


_ORDEN_CONFIANZA: dict[str, int] = {"baja": 0, "media": 1, "alta": 2}


def _min_confianza(a: str, b: str) -> Literal["alta", "media", "baja"]:
    """La mas baja de las dos -- nunca se sube la confianza, solo se capa."""
    return a if _ORDEN_CONFIANZA[a] <= _ORDEN_CONFIANZA[b] else b  # type: ignore[return-value]


def agrupar(fotos: list[Path]) -> list[Grupo]:
    """Propone una agrupacion por producto a partir de un lote de fotos
    mezcladas. Nunca decide -- Diego confirma. Cero red, cero LLM, cero
    coste: solo reusa lo que `core/images.py` ya calcula.

    Estrategia (detalle completo en el docstring del modulo):
      1. Fotos CON fecha EXIF: se ordenan cronologicamente y se cortan
         donde el hueco entre disparos consecutivos separa claramente el
         "bulto" de huecos intra-producto del de huecos inter-producto
         (`_umbral_corte`). Sin esa base, no se corta -- y el techo de
         confianza baja a "media" [C2].
      2. Cada segmento se confirma por pHash (forma) Y colorhash (color):
         si una foto no se parece a ninguna otra del segmento en
         cualquiera de los dos, el grupo baja a "baja" -- no se expulsa
         la foto, se marca [C3].
      3. Grupos temporales ADYACENTES cuyo borde es casi identico en forma
         y color bajan a "media" con una sugerencia de fusion -- podria
         ser un solo producto partido por el corte de tiempo [A2].
      4. Fotos SIN fecha EXIF nunca se mezclan con las anteriores. Se
         agrupan solo si son casi-duplicados por pHash Y por color; lo
         que no conecta con nada es su propio grupo, "baja".
    """
    if not fotos:
        return []

    metadatos: dict[Path, MetadatosImagen] = {foto: leer_metadatos(foto) for foto in fotos}
    registros_hash = hashear_lote(fotos)
    phash_por_ruta: dict[Path, str | None] = {r.ruta: r.phash for r in registros_hash}
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None] = {
        foto: _calcular_colorhash(foto) for foto in fotos
    }

    con_fecha = sorted(
        (foto for foto in fotos if metadatos[foto].fecha_captura_exif is not None),
        key=lambda foto: metadatos[foto].fecha_captura_exif,
    )
    sin_fecha = [foto for foto in fotos if metadatos[foto].fecha_captura_exif is None]

    grupos: list[Grupo] = []
    grupos.extend(_agrupar_por_tiempo(con_fecha, metadatos, phash_por_ruta, colorhash_por_ruta))
    grupos.extend(
        _agrupar_por_phash_sin_fecha(sin_fecha, metadatos, phash_por_ruta, colorhash_por_ruta)
    )
    return grupos


def _calcular_colorhash(ruta: Path) -> imagehash.ImageHash | None:
    """`imagehash.colorhash` sobre los pixeles ya orientados. Se guarda el
    objeto `ImageHash` tal cual -- NO se serializa via `str()` porque
    `colorhash` produce un array NO cuadrado (14 x binbits, p. ej. 14x3 =
    42 bits) y `imagehash.hex_to_hash` asume una forma cuadrada al
    reconstruir desde hex, lo que corrompe el hash en el viaje de ida y
    vuelta (medido: un hash de 42 bits se reconstruye como uno de 36).
    Misma frontera de errores que `core.images.hashear_lote`: un fichero
    corrupto/ilegible no aborta el lote, se registra con traceback
    completo y se devuelve `None` -- nunca un fallback silencioso a "sin
    color, no importa"."""
    try:
        img = abrir_derecha(ruta)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return imagehash.colorhash(img)
    except Exception:  # noqa: BLE001 — frontera "un fichero del lote", igual que core/images.py.
        logger.exception("No se pudo calcular colorhash de %s", ruta)
        return None


def _distancia(hex_a: str, hex_b: str) -> int:
    """Distancia de Hamming entre dos pHash serializados en hex (formato
    de `RegistroHash.phash`, siempre cuadrado -- aqui SI es seguro pasar
    por `hex_to_hash`)."""
    return imagehash.hex_to_hash(hex_a) - imagehash.hex_to_hash(hex_b)


def _distancia_color(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    """Distancia de Hamming entre dos `colorhash` ya calculados (objetos
    `ImageHash` en memoria, nunca pasados por `hex_to_hash` -- ver
    `_calcular_colorhash`)."""
    return a - b


# --------------------------------------------------------------------------
# Fotos CON fecha EXIF: corte por hueco temporal + confirmacion visual
# --------------------------------------------------------------------------
def _agrupar_por_tiempo(
    con_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None],
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
    umbral = _umbral_corte(huecos)

    segmentos: list[list[Path]] = [[con_fecha[0]]]
    for i, hueco in enumerate(huecos):
        if umbral is not None and hueco > umbral:
            segmentos.append([])
        segmentos[-1].append(con_fecha[i + 1])

    grupos = [
        _construir_grupo_temporal(seg, metadatos, phash_por_ruta, colorhash_por_ruta, umbral)
        for seg in segmentos
    ]
    return _sugerir_fusion_adyacentes(grupos, phash_por_ruta, colorhash_por_ruta)


def _umbral_corte(huecos: list[float]) -> float | None:
    """Deriva el umbral de "hueco grande" buscando la mayor separacion
    RELATIVA entre huecos consecutivos, una vez ORDENADOS por magnitud
    (ver docstring del modulo para el razonamiento completo y la
    simulacion que calibra `UMBRAL_SALTO_RELATIVO_MINIMO`). Nunca un
    numero de segundos fijo, y nunca una mediana (no separa outliers
    cuando el "bulto grande" no es una minoria residual -- justo el caso
    real de [INC-002]).

    `None` significa "sin base estadistica para cortar por tiempo":
      - con menos de 2 huecos (0 o 1 foto extra tras la primera con
        fecha) no hay distribucion de la que derivar una frontera;
      - si TODOS los huecos son 0 (rafaga con timestamps al segundo
        identico en todo el lote), tampoco hay base;
      - si todos los huecos son iguales y >0 (ritmo perfectamente
        constante, sin ninguna variacion), no hay frontera que buscar;
      - si la mejor frontera encontrada no supera
        `UMBRAL_SALTO_RELATIVO_MINIMO`, se descarta como ruido de jitter
        en vez de forzar un corte dudoso.
    Cualquier grupo que dependa de un umbral `None` tiene el TECHO de
    confianza capado a "media" -- ver `_construir_grupo_temporal` [C2].
    """
    if len(huecos) < 2:
        return None

    valores = sorted(huecos)
    if valores[-1] <= 0:
        return None  # rafaga perfecta: todos los huecos son 0

    mejor_salto_relativo = 0.0
    mejor_frontera: tuple[float, float] | None = None
    for i in range(len(valores) - 1):
        bajo, alto = valores[i], valores[i + 1]
        salto = alto - bajo
        if salto <= 0:
            continue
        salto_relativo = salto / (bajo + _EPSILON_SALTO_RELATIVO)
        if salto_relativo > mejor_salto_relativo:
            mejor_salto_relativo = salto_relativo
            mejor_frontera = (bajo, alto)

    if mejor_frontera is None or mejor_salto_relativo < UMBRAL_SALTO_RELATIVO_MINIMO:
        return None

    bajo, alto = mejor_frontera
    return (bajo + alto) / 2


def _construir_grupo_temporal(
    seg: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None],
    umbral: float | None,
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

    sin_hash = [
        foto.name
        for foto in seg
        if phash_por_ruta.get(foto) is None or colorhash_por_ruta.get(foto) is None
    ]
    if sin_hash:
        return Grupo(
            fotos=seg,
            confianza=_min_confianza(techo, "media"),
            motivo=(
                f"{motivo_tiempo} No se pudo calcular el hash visual (forma "
                f"y/o color) de {', '.join(sin_hash)} para confirmar que se "
                "parece al resto. Revisalo a mano."
            ),
        )

    distintas = _fotos_muy_distintas(seg, phash_por_ruta, colorhash_por_ruta)
    if distintas:
        detalle = ", ".join(f"{foto.name} ({razon})" for foto, razon in distintas)
        plural = "n" if len(distintas) > 1 else ""
        logger.info("Grupo temporal con foto(s) visualmente distinta(s): %s", detalle)
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                f"{motivo_tiempo} Pero {detalle} no se parece{plural} a "
                "ninguna otra del grupo -- posible foto cruzada de otro "
                "producto. Revisala antes de confirmar."
            ),
        )

    return Grupo(
        fotos=seg,
        confianza=techo,
        motivo=f"{motivo_tiempo} Visualmente parecidas entre si (forma y color).",
    )


def _fotos_muy_distintas(
    seg: list[Path],
    phash_por_ruta: dict[Path, str | None],
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None],
) -> list[tuple[Path, str]]:
    """Fotos de `seg` que no se parecen a NINGUNA otra foto del grupo, ni
    en FORMA (pHash) ni en COLOR (colorhash) [C3]. Devuelve, por cada
    foto senalada, la razon ("forma", "color" o "forma y color").
    Precondicion: ningun hash de `seg` es `None` (eso se trata antes,
    como confirmacion inconclusa, no como "distinta" -- ver
    `_construir_grupo_temporal`)."""
    hashes_forma = {foto: phash_por_ruta[foto] for foto in seg}
    hashes_color = {foto: colorhash_por_ruta[foto] for foto in seg}

    distintas: list[tuple[Path, str]] = []
    for foto in seg:
        d_forma = [_distancia(hashes_forma[foto], hashes_forma[otra]) for otra in seg if otra != foto]
        d_color = [_distancia_color(hashes_color[foto], hashes_color[otra]) for otra in seg if otra != foto]
        forma_distinta = min(d_forma) > UMBRAL_PHASH_MUY_DISTINTA
        color_distinta = min(d_color) > UMBRAL_COLORHASH_MUY_DISTINTA
        if not (forma_distinta or color_distinta):
            continue
        if forma_distinta and color_distinta:
            razon = "forma y color"
        elif forma_distinta:
            razon = "forma"
        else:
            razon = "color"
        distintas.append((foto, razon))
    return distintas


# --------------------------------------------------------------------------
# [A2] Grupos temporales adyacentes casi identicos -> posible producto partido
# --------------------------------------------------------------------------
_AVISO_FUSION = (
    " Aviso: la foto del borde con el grupo vecino es visualmente casi "
    "identica en forma y color -- podria ser UN solo producto partido en "
    "dos por el corte de tiempo. Revisa si hay que fusionarlos."
)


def _sugerir_fusion_adyacentes(
    grupos: list[Grupo],
    phash_por_ruta: dict[Path, str | None],
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None],
) -> list[Grupo]:
    """Si dos grupos temporales CONSECUTIVOS tienen las fotos de su borde
    (la ultima del primero, la primera del segundo) casi identicas en
    forma Y color, es mas probable que sea un solo producto partido por
    un hueco de tiempo mas largo de lo normal que dos productos
    distintos. Ambos grupos bajan a "media" como mucho (nunca se sube la
    confianza) y se avisa en el motivo -- Diego decide si fusiona."""
    if len(grupos) < 2:
        return grupos

    resultado = list(grupos)
    for i in range(len(resultado) - 1):
        a, b = resultado[i], resultado[i + 1]
        if not a.fotos or not b.fotos:
            continue
        ultima_a, primera_b = a.fotos[-1], b.fotos[0]
        ph_a, ph_b = phash_por_ruta.get(ultima_a), phash_por_ruta.get(primera_b)
        ch_a, ch_b = colorhash_por_ruta.get(ultima_a), colorhash_por_ruta.get(primera_b)
        if None in (ph_a, ph_b, ch_a, ch_b):
            continue  # sin hash de confirmacion en el borde: no se puede evaluar, no se sugiere nada

        parecen_el_mismo = (
            _distancia(ph_a, ph_b) <= UMBRAL_PHASH_MUY_DISTINTA
            and _distancia_color(ch_a, ch_b) <= UMBRAL_COLORHASH_MUY_DISTINTA
        )
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
# Fotos SIN fecha EXIF: solo pHash + colorhash, o cada una su propio grupo
# --------------------------------------------------------------------------
def _agrupar_por_phash_sin_fecha(
    sin_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
    colorhash_por_ruta: dict[Path, imagehash.ImageHash | None],
) -> list[Grupo]:
    if not sin_fecha:
        return []

    registros = [
        RegistroHash(ruta=foto, sha256=None, phash=phash_por_ruta.get(foto))
        for foto in sin_fecha
    ]
    # Mismo umbral que `core.images.duplicados_similares` usa para
    # deduplicacion por FORMA (casi-duplicado, no "mismo producto otro
    # angulo" -- no inventamos un umbral mas laxo aqui sin datos que lo
    # respalden). [C3/INC-002] Ya no basta: se exige TAMBIEN que el color
    # sea casi identico, si no, cuatro camisetas de colores distintos con
    # la misma silueta pasaban este filtro y salian como un solo producto.
    pares_por_forma = duplicados_similares(registros)
    pares = [
        (a, b, d)
        for (a, b, d) in pares_por_forma
        if colorhash_por_ruta.get(a) is not None
        and colorhash_por_ruta.get(b) is not None
        and _distancia_color(colorhash_por_ruta[a], colorhash_por_ruta[b]) <= UMBRAL_COLORHASH_MUY_DISTINTA
    ]
    componentes = _componentes_conexas(sin_fecha, pares)

    grupos: list[Grupo] = []
    for componente in componentes:
        ordenada = sorted(componente, key=lambda foto: _clave_orden_sin_fecha(foto, metadatos))
        if len(ordenada) == 1:
            foto = ordenada[0]
            if phash_por_ruta.get(foto) is None or colorhash_por_ruta.get(foto) is None:
                motivo = (
                    "Esta foto no tiene fecha EXIF y no se pudo analizar "
                    "visualmente (fichero ilegible o corrupto). Revisala a mano."
                )
            else:
                motivo = (
                    "Esta foto no tiene fecha y no se parece a ninguna otra del "
                    "lote (ni en forma ni en color). Queda sola: revisala a mano."
                )
            grupos.append(Grupo(fotos=[foto], confianza="baja", motivo=motivo))
        else:
            grupos.append(
                Grupo(
                    fotos=ordenada,
                    confianza="media",
                    motivo=(
                        f"{len(ordenada)} fotos sin fecha EXIF pero casi identicas "
                        "por forma (pHash) Y color (colorhash): probablemente el "
                        "mismo producto. Sin timestamp no se puede confirmar el "
                        "orden real de disparo (se ordenan por fecha del fichero, "
                        "no de camara)."
                    ),
                )
            )
    return grupos


def _componentes_conexas(
    nodos: list[Path], pares: list[tuple[Path, Path, int]]
) -> list[list[Path]]:
    """Union-find minimo: agrupa `nodos` conectados (directa o
    transitivamente) por algun par en `pares`. Un nodo sin ningun par es
    su propia componente de tamano 1."""
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

    for a, b, _distancia_par in pares:
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
