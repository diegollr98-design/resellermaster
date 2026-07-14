"""core/grouping.py — Costura de AGRUPACION (RESELLERMASTER). v5.

Superficie SENSIBLE (`.claude/rules/truth-loop.md` §B y §E). Este modulo
PROPONE grupos; Diego CONFIRMA. Nada de aqui cierra nada.

## La regla madre: la asimetria (`truth-loop.md` §E)
- Partir un producto de mas  -> Diego fusiona en ~5 segundos.  BARATO.
- Fusionar dos productos     -> una foto de otra prenda en el anuncio.
                                Nadie lo caza. UNA VENTA PERDIDA.

Este modulo **no optimiza acierto: optimiza NO-FUSIONAR**. Un corte de mas es
el resultado *correcto* cuando no hay certeza, no un fallo que haya que afinar.

## LO QUE ESTE MODULO SABE, Y LO QUE NO (leelo antes de tocar nada)

**Sabe una sola cosa: cuanto tiempo paso entre dos disparos.** Nada mas. No
mira un solo pixel. De ahi se deduce **todo** lo demas que hay aqui:

> **El reloj puede PARTIR, pero no puede CONFIRMAR.**

Una pausa larga es evidencia razonable de que Diego cambio de producto. Pero
la ausencia de pausa **no es evidencia de nada**: si fotografia dos productos
seguidos sin pararse, el reloj no ve absolutamente ninguna diferencia. Por eso:

> **NINGUN grupo puede salir con `confianza="alta"`.** No es prudencia: es que
> con esta señal la certeza **no es derivable**. Ver §Confianza.

## Por que v5 (y por que v1-v4 no valian)
Las tres primeras versiones se calibraron contra imagenes SINTETICAS, sin mirar
una sola foto de Diego (`[INC-002]`, `[INC-003]`, `[INC-004]`). La v4 ya se
midio sobre sus 33 fotos reales, pero **dos `listing-audit` independientes la
declararon BLOQUEANTE** y tenian razon — reproducido ejecutando:

  - Emitia `confianza="alta"` cuando el hueco interno maximo del grupo era
    pequeño. Pero **una fusion la CAUSA un hueco pequeño**: si Diego cambia de
    producto en 9 s, los dos productos quedan juntos y el grupo, al no tener
    ninguna pausa interna, se lleva la confianza MAS ALTA. **La confianza
    estaba anti-correlacionada con el riesgo** — y `alta` es justo la que la UI
    esta diseñada para confirmar en bloque sin mirar (`ui/confirmacion.py`).
    Medido: `productos=[A,B] conf=ALTA n=6` con un cambio de producto de 9 s.
  - Con EXIF degenerado (12 fotos, 2 productos, timestamps IDENTICOS) devolvia
    **un solo grupo con los 2 productos, confianza ALTA**, y el motivo decia
    "12 fotos seguidas en 0 s, sin ninguna pausa dentro". El peor output
    posible con la maxima confianza.

## Lo que este modulo NO hace, deliberadamente

1. **No deriva el umbral de la distribucion de huecos del lote.** Es la clase
   de error de `[INC-003]`: un umbral sacado de un extremo de la distribucion
   se deja mover por un solo hueco anomalo. Y hay una razon mas dura para no
   intentarlo: **en las fotos reales de Diego no existe la separacion bimodal
   que ese enfoque necesita.** Medido, sobre sus 33 fotos:

       huecos INTRA-producto: 1, 4, 4, 4, 4, 4, 4, 4, 5, 5, 7, 7, 8, 8, 9,
                              10, 12, 14, 14, 14, 19, 28, 28, 35, 71, 94
       huecos INTER-producto: 23, 29, 32, 32, 36, 2735

   **Se solapan.** No hay ningun "valle" que encontrar. (Los intra de 28-94 s
   son la foto del metro, la del papel y las de detalle: Diego se para a
   colocarlas. Son exactamente los cortes de mas que este modulo produce.)

2. **No mira los pixeles.** Ni pHash (ciego al color, `[INC-002]`), ni CLIP
   (medido dos veces sobre fotos reales: dice "prenda entera 100%" ante un
   primer plano de etiqueta, y su similitud consecutiva esta INVERTIDA — dos
   prendas distintas colgadas dan 0.90; el plano general y la etiqueta del
   MISMO producto dan 0.61, `[INC-004]`). Una señal peor que nada, porque
   llega con aire de confirmacion.

3. **No sugiere fusiones por tiempo.** Medido: los huecos de los cortes de mas
   (28, 28, 35, 71, 94 s) y los de las fronteras reales (23, 29, 32, 32, 36 s)
   **se solapan por completo**. El tiempo ya dio todo lo que tenia al cortar.

## El hueco que queda abierto, dicho en voz alta
La reparacion correcta de los cortes de mas es clasificar el TIPO de foto y
aplicar la regla dura de `truth-loop.md` §E: **una foto de metro / etiqueta /
papel nunca puede EMPEZAR un producto; solo un plano general puede.** Eso exige
un modelo de visión de verdad (CLIP no sirve, medido), va detras de la costura
`ExtractorEngine` y **no esta en esta version**.

Ese clasificador es tambien lo unico que permitiria volver a emitir
`confianza="alta"`: seria una señal INDEPENDIENTE del reloj. Hasta entonces,
`alta` no existe aqui, y el boton de "confirmar en bloque sin mirar" de la UI
se queda —correctamente— sin nada que confirmar.

**Degradacion honesta:** sin ese clasificador la app SIGUE FUNCIONANDO — solo
hay mas cortes de mas y mas revision de Diego. Nunca una ficha contaminada. El
suelo determinista no depende de ningun modelo, ninguna red y ningun euro.

## Confianza: que significa aqui (y que NO)
La confianza **no dice** "estas fotos son del mismo producto" — eso el reloj no
lo puede saber. Dice **cuanta señal hubo para el corte**:

  - "media" -> el grupo quedo delimitado por pausas >= el umbral a los lados y
               no hay nada raro dentro. Es el techo. Diego lo revisa igual.
  - "baja"  -> hay una razon concreta para mirarlo primero: es una foto sola
               (casi siempre un corte de mas: ningun producto real de Diego
               tiene una sola foto), o el reloj no dio ninguna señal utilizable
               (sin EXIF, EXIF degenerado), o el fichero no se pudo leer.

Cero red, cero LLM, cero coste. Sin dependencias mas alla de `core.images`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.images import MetadatosImagen, leer_metadatos

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# LA constante. Medida sobre las 33 fotos reales de Diego, no elegida a ojo.
# --------------------------------------------------------------------------
# Un hueco >= este valor entre dos disparos consecutivos se lee como "aqui
# empieza otro producto".
#
# BARRIDO COMPLETO sobre el golden set (re-derivado ejecutando, reproducible
# con `tests/test_grouping_golden.py::test_barrido_de_umbrales`):
#
#     umbral   fusiones   cortes de mas
#      1..14      0          9..18
#      15..19     0            6         <- ELEGIDO: 15
#      20..23     0            5
#      24..28     1  ***FUSIONA prod 1 y 2***
#      30         2  ***
#
# **Por que 15 y no 20** (que es el numero que citaba `truth-loop.md` §E, antes
# de que existiera este barrido): la meseta segura llega hasta 23. Elegir 20
# deja **4 s de colchon** hasta el acantilado, con un jitter medido de ±2.4 s
# — menos de dos jitters. Elegir 15 deja **9 s** (~3.7 jitters) y cuesta
# exactamente UN corte de mas (6 en vez de 5): ~5 segundos de Diego, una vez,
# en todo el lote. Comprar el doble de colchon contra el fallo caro por 5
# segundos del barato es precisamente lo que manda la asimetria de §E.
#
# El colchon NO es simetrico, y esto es lo que hace facil la decision:
#   - hacia ARRIBA hay un acantilado (24 s ya fusiona dos productos reales).
#   - hacia ABAJO no hay ninguno: bajar JAMAS fusiona, solo corta de mas.
#
# ==> ANTE LA DUDA, ESTE NUMERO SE BAJA. NUNCA SE SUBE. <==
#
# Si algun dia un lote real fusiona dos productos, la accion correcta es BAJAR
# esta constante. `tests/test_grouping_golden.py` esta escrito para PERMITIRLO
# (el unico assert duro es FUSIONES == 0; los cortes de mas solo se reportan).
UMBRAL_HUECO_SEGUNDOS = 15.0

# Numero minimo de huecos DISTINTOS que debe tener un lote para que el reloj
# cuente como señal utilizable. Un lote donde todos los disparos caen en el
# mismo segundo (o donde el EXIF viene con una constante fabricada) no tiene
# ninguna informacion temporal: sus "huecos" de 0 s no significan "ratatata,
# el mismo producto", significan "no se". Sin este suelo, v4 devolvia el lote
# entero en un grupo, con la maxima confianza. Ver docstring, §Por que v5.
_MINIMO_HUECOS_DISTINTOS = 2


@dataclass(frozen=True)
class Grupo:
    """Una PROPUESTA de agrupacion de fotos por producto.

    fotos: en orden cronologico (EXIF) dentro del grupo.
    confianza: cuanta SEÑAL hubo para el corte — nunca certeza sobre el
        producto, que el reloj no puede dar. **Nunca "alta"**: ver §Confianza
        en el docstring del modulo. El literal "alta" sigue en el tipo porque
        `core/store.py` y `ui/` lo comparten con el resto del pipeline, y
        porque volvera a ser emitible cuando exista una señal independiente
        del reloj (el clasificador de tipo de foto).
    motivo: en español, con NUMEROS reales. Nunca afirma que las fotos sean
        del mismo producto — dice que el reloj no vio ninguna pausa, que es
        una cosa distinta y es la unica que se midio.
    """

    fotos: list[Path]
    confianza: Literal["alta", "media", "baja"]
    motivo: str


def agrupar(fotos: list[Path]) -> list[Grupo]:
    """Propone una agrupacion por producto. Nunca decide — Diego confirma.

    1. Las fotos ILEGIBLES (fichero corrupto, formato no soportado) salen
       aparte, cada una sola, con su error: no se pueden agrupar ni mirar.
    2. Si el reloj del lote no da señal utilizable (sin EXIF, o EXIF
       degenerado: todos los disparos en el mismo instante), NO se agrupa por
       tiempo. Cada foto va al cajon de INCIERTAS (`truth-loop.md` §E: "lo que
       el modelo no pueda casar va a un cajon de INCIERTAS, nunca al grupo que
       mejor cuadre"). Feo a proposito: es la forma honesta de decir "no lo se".
    3. Con señal utilizable: se ordenan cronologicamente y se corta en cada
       hueco >= `UMBRAL_HUECO_SEGUNDOS`. Sesgo permanente a cortar de mas.
    """
    if not fotos:
        return []

    metadatos = {foto: leer_metadatos(foto) for foto in fotos}

    ilegibles = [f for f in fotos if not metadatos[f].legible]
    legibles = [f for f in fotos if metadatos[f].legible]

    con_fecha = sorted(
        (f for f in legibles if metadatos[f].fecha_captura_exif is not None),
        key=lambda f: (metadatos[f].fecha_captura_exif, f.name),
    )
    sin_fecha = [f for f in legibles if metadatos[f].fecha_captura_exif is None]

    if sin_fecha:
        logger.warning(
            "%d de %d fotos del lote no tienen fecha EXIF: no hay señal de "
            "agrupacion para ellas (cada una queda sola, para que Diego las "
            "agrupe a mano). Causa habitual: llegaron por WhatsApp, que borra "
            "el EXIF entero (medido: 0/59 conservaron la fecha). Pasalas por "
            "cable.",
            len(sin_fecha),
            len(fotos),
        )

    grupos: list[Grupo] = []
    grupos.extend(_agrupar_por_tiempo(con_fecha, metadatos))
    grupos.extend(_cajon_de_inciertas(sin_fecha, _MOTIVO_SIN_EXIF))
    grupos.extend(_grupos_ilegibles(ilegibles, metadatos))
    return grupos


# --------------------------------------------------------------------------
# Paso 1 — corte por hueco temporal. La unica señal que las fotos reales de
# Diego tienen de verdad... cuando la tienen.
# --------------------------------------------------------------------------
def _agrupar_por_tiempo(
    con_fecha: list[Path], metadatos: dict[Path, MetadatosImagen]
) -> list[Grupo]:
    if not con_fecha:
        return []

    if len(con_fecha) == 1:
        return [
            Grupo(
                fotos=list(con_fecha),
                confianza="baja",
                motivo=(
                    "Unica foto con fecha del lote: no hay ninguna otra con la que "
                    "comparar el reloj. Revisala a mano."
                ),
            )
        ]

    huecos = [
        (metadatos[b].fecha_captura_exif - metadatos[a].fecha_captura_exif).total_seconds()
        for a, b in zip(con_fecha, con_fecha[1:])
    ]

    # EXIF DEGENERADO. Si todos los disparos caen en el mismo instante (o el
    # EXIF trae una constante fabricada), los huecos de 0 s NO dicen "el mismo
    # producto": no dicen NADA. v4 devolvia aqui el lote entero en un grupo con
    # confianza alta — el peor output posible. Ahora: INCIERTAS.
    if len(set(huecos)) < _MINIMO_HUECOS_DISTINTOS and max(huecos) == 0:
        logger.warning(
            "EXIF degenerado: las %d fotos con fecha del lote tienen todas el "
            "MISMO timestamp. El reloj no aporta ninguna señal de agrupacion "
            "(un hueco de 0 s no significa 'mismo producto', significa 'no se'). "
            "Las fotos van al cajon de INCIERTAS en vez de a un grupo unico.",
            len(con_fecha),
        )
        return _cajon_de_inciertas(con_fecha, _MOTIVO_EXIF_DEGENERADO)

    segmentos: list[list[Path]] = [[con_fecha[0]]]
    for (anterior, actual), hueco in zip(zip(con_fecha, con_fecha[1:]), huecos):
        del anterior
        if hueco >= UMBRAL_HUECO_SEGUNDOS:
            segmentos.append([])
        segmentos[-1].append(actual)

    return [_construir_grupo(seg, metadatos) for seg in segmentos]


def _construir_grupo(seg: list[Path], metadatos: dict[Path, MetadatosImagen]) -> Grupo:
    if len(seg) == 1:
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                "Una sola foto entre dos pausas largas. En tus fotos reales NINGUN "
                "producto tiene una sola foto: casi siempre esto es un detalle, la "
                "etiqueta, el metro o un papel — y pertenece al producto de antes o "
                "al de despues. Miralo y fusionala si es asi."
            ),
        )

    huecos = [
        (metadatos[b].fecha_captura_exif - metadatos[a].fecha_captura_exif).total_seconds()
        for a, b in zip(seg, seg[1:])
    ]
    hueco_maximo = max(huecos)
    duracion = (
        metadatos[seg[-1]].fecha_captura_exif - metadatos[seg[0]].fecha_captura_exif
    ).total_seconds()

    # NUNCA "alta". El reloj puede partir, pero no puede confirmar: si Diego
    # fotografio dos productos seguidos sin pausa, esto los tendria juntos y no
    # habria ninguna forma de saberlo desde aqui. El motivo lo dice, en vez de
    # afirmar "es un solo producto" — que es lo que decia v4 y era falso.
    return Grupo(
        fotos=seg,
        confianza="media",
        motivo=(
            f"{len(seg)} fotos en {duracion:.0f} s, sin ninguna pausa de "
            f"{UMBRAL_HUECO_SEGUNDOS:.0f} s o mas dentro (la mayor es de "
            f"{hueco_maximo:.0f} s). Ojo: eso solo dice que NO hubo pausa — si "
            "fotografiaste dos productos seguidos sin pararte, el reloj no los "
            "distingue y estarian aqui juntos. Comprueba que todas son del mismo."
        ),
    )


# --------------------------------------------------------------------------
# Cajon de INCIERTAS — `truth-loop.md` §E: "lo que el modelo no pueda casar va
# a un cajon de INCIERTAS, nunca al grupo que mejor cuadre".
# --------------------------------------------------------------------------
_MOTIVO_SIN_EXIF = (
    "Esta foto no tiene fecha EXIF, asi que no hay NINGUNA señal fiable para "
    "agruparla (el reloj es la unica que funciona en este lote). Se deja sola en "
    "vez de adivinar: meterla en el grupo equivocado pondria una foto de otro "
    "producto en tu anuncio. Agrupala a mano. Si el lote entero esta asi, "
    "probablemente venga de WhatsApp (borra el EXIF): pasalo por cable."
)

_MOTIVO_EXIF_DEGENERADO = (
    "Todas las fotos con fecha de este lote tienen EXACTAMENTE el mismo "
    "timestamp, asi que el reloj no aporta ninguna señal: un hueco de 0 s no "
    "significa 'el mismo producto', significa 'no se'. Se dejan sueltas en vez "
    "de meterlas todas en un grupo. Agrupalas a mano."
)


def _cajon_de_inciertas(fotos: list[Path], motivo: str) -> list[Grupo]:
    """Cada foto sola, "baja". Es feo a proposito: es infinitamente mas barato
    que meter la foto del producto A en la ficha del B."""
    return [
        Grupo(fotos=[foto], confianza="baja", motivo=motivo)
        for foto in sorted(fotos, key=lambda f: f.name)
    ]


def _grupos_ilegibles(
    ilegibles: list[Path], metadatos: dict[Path, MetadatosImagen]
) -> list[Grupo]:
    """Un fichero corrupto NO es una foto sin EXIF. v4 los confundia y le decia
    a Diego "agrupala a mano" sobre un fichero que no se puede ni abrir. El
    error real de `core.images` se propaga tal cual — nunca se silencia
    (`decision-making.md` §13)."""
    grupos: list[Grupo] = []
    for foto in sorted(ilegibles, key=lambda f: f.name):
        error = metadatos[foto].error or "motivo no registrado"
        logger.warning("Foto ilegible en el lote, no se puede agrupar: %s (%s)", foto, error)
        grupos.append(
            Grupo(
                fotos=[foto],
                confianza="baja",
                motivo=(
                    f"Este fichero NO se puede abrir ({error}). No es que le falte "
                    "la fecha: esta corrupto o no es una imagen soportada. No se "
                    "puede agrupar ni usar en una ficha — revisalo o quitalo del lote."
                ),
            )
        )
    return grupos
