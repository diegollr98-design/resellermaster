"""core/grouping.py — Costura de AGRUPACION (RESELLERMASTER). v4.

Superficie SENSIBLE (`.claude/rules/truth-loop.md` §B y §E). Este modulo
PROPONE grupos; Diego CONFIRMA. Nada de aqui cierra nada.

## La regla madre: la asimetria (`truth-loop.md` §E)
- Partir un producto de mas  -> Diego fusiona en ~5 segundos.  BARATO.
- Fusionar dos productos     -> una foto de otra prenda en el anuncio.
                                Nadie lo caza. UNA VENTA PERDIDA.

Por tanto este modulo **no optimiza acierto: optimiza NO-FUSIONAR**. Ante
cualquier duda, corta. Un corte de mas es el resultado *correcto* cuando no
hay certeza, no un fallo que haya que "afinar".

## Por que v4 es asi (y por que las v1-v3 no lo eran)
Las tres versiones anteriores se calibraron contra imagenes SINTETICAS, sin
mirar una sola foto de Diego (`[INC-002]`, `[INC-003]`, `[INC-004]`). Las
señales sobre las que se construyeron (umbral derivado de la distribucion de
huecos; pHash; colorhash; CLIP) o no existian en sus fotos reales o estaban
invertidas. Esta version se deriva de sus 33 fotos reales
(`tests/golden/truth.json`, 7 productos, verdad fijada por el).

MEDIDO sobre ese golden set (re-derivado ejecutando, no citado):

    umbral         fronteras   fusiones   cortes de mas
    hueco >= 20 s     6/6         0            5          <- el elegido
    hueco >= 25 s     5/6         1            3          <- FUSIONA prod 1 y 2

## Lo que este modulo NO hace, deliberadamente

1. **No deriva el umbral de la distribucion de huecos del lote.** Es lo que
   hicieron v1 y v2, y es la clase de error de `[INC-003]`: un umbral sacado
   de un extremo de la distribucion (el mayor salto, o el menor que califica)
   se deja mover por un solo hueco anomalo. Y hay una razon mas dura para no
   intentarlo siquiera: **en las fotos reales de Diego no existe la separacion
   bimodal que ese enfoque necesita.** Medido: huecos intra-producto hasta
   19 s; huecos inter-producto desde 23 s. **Se solapan con el jitter (±2.4 s).**
   No hay ningun "valle" que encontrar. El umbral es una CONSTANTE medida
   sobre sus fotos, y punto.

2. **No mira los pixeles.** Ni pHash (ciego al color, `[INC-002]`), ni CLIP
   (medido dos veces sobre fotos reales: dice "prenda entera 100%" ante un
   primer plano de etiqueta, y su similitud consecutiva esta INVERTIDA — dos
   prendas distintas colgadas dan 0.90, mientras el plano general y la
   etiqueta del MISMO producto dan 0.61, `[INC-004]`). Una señal peor que
   nada, porque llega con aire de confirmacion. Este modulo no la usa.

3. **No sugiere fusiones por tiempo.** Es tentador ("este grupo empieza solo
   28 s despues del anterior, quiza sea el mismo") y es FALSO: medido, los
   huecos de los cortes de mas (35, 28, 28, 94, 71 s) y los de las fronteras
   reales (23, 36, 32, 32, 29 s) **se solapan por completo**. El tiempo ya
   dio todo lo que tenia en el paso 1. Sugerir una fusion con esa señal seria
   inventarse una certeza — exactamente el fallo que este proyecto existe
   para evitar. Lo que SI se hace es señalar donde mirar (ver §confianza).

## El hueco que queda abierto, dicho en voz alta
Los 5 cortes de mas son PREDECIBLES: son la foto del metro (+94 s), la del
papel con el desperfecto (+71 s) y fotos de detalle — Diego se para a
colocarlas. La reparacion correcta es clasificar el TIPO de foto y aplicar
la regla dura de `truth-loop.md` §E: **una foto de metro / etiqueta / papel
nunca puede EMPEZAR un producto; solo un plano general puede.** Eso exige un
modelo de visión de verdad (CLIP no sirve, medido). Va detras de la costura
`ExtractorEngine` y **no esta en esta version**: Fase 1b es el suelo
determinista.

**Degradacion honesta, en el sentido correcto:** sin ese clasificador la app
FUNCIONA — solo hay mas cortes de mas, que es el error barato. El suelo
determinista no depende de ningun modelo, ninguna red y ningun euro. Cuando
el clasificador llegue, solo puede FUSIONAR cortes sobrantes; nunca podra
partir lo que este modulo unio, porque este modulo une lo minimo.

## Confianza: que significa aqui
La confianza NO dice "estoy seguro de que estas fotos son el mismo producto"
— eso no lo puede saber nadie sin mirar. Dice **cuanto margen tuvo el corte**,
que es lo unico verificable:

  - "alta"  -> el grupo tiene >=2 fotos y su hueco interno mas grande esta
               MUY por debajo del umbral (< 50%): el ritmo es claramente de
               rafaga, sin ninguna pausa sospechosa dentro.
  - "media" -> el grupo tiene >=2 fotos pero contiene algun hueco interno que
               se acerca al umbral: el corte pudo quedarse corto ahi. Miralo.
  - "baja"  -> el grupo tiene UNA sola foto. Es el caso mas informativo del
               modulo: en el golden set, NINGUN producto real de Diego tiene
               una sola foto, y 4 de los 5 cortes de mas produjeron
               exactamente esto. Un grupo de una foto es, casi siempre, una
               foto de detalle/etiqueta/metro que pertenece al producto de al
               lado. La UI los saca arriba y fusionarlos cuesta segundos.

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
# empieza otro producto". Sobre `tests/golden/truth.json`: 6/6 fronteras
# reales, CERO fusiones, 5 cortes de mas.
#
# EL MARGEN ES ESTRECHO Y HAY QUE SABERLO: el hueco inter-producto mas
# PEQUEÑO del golden set es 23 s (frontera producto 1 -> 2) y el hueco
# intra-producto mas GRANDE es 19 s (dentro del producto 2). El colchon real
# a cada lado es de ~1-3 s, con un jitter medido de ±2.4 s.
#
# ==> ANTE LA DUDA, ESTE NUMERO SE BAJA. NUNCA SE SUBE. <==
#
# Subirlo a 25 ya fusiona los productos 1 y 2 (medido). Bajarlo solo produce
# mas cortes de mas, que es el error barato: Diego fusiona en 5 segundos.
# Si algun dia un lote real fusiona dos productos, la accion correcta es
# BAJAR esta constante y volver a correr `tests/test_grouping_golden.py` —
# no "afinarla" buscando un optimo que los datos dicen que no existe.
UMBRAL_HUECO_SEGUNDOS = 20.0

# Fraccion del umbral por debajo de la cual el hueco interno mas grande de un
# grupo se considera "ritmo de rafaga limpio" -> confianza "alta". No decide
# ninguna agrupacion: solo ordena la revision de Diego en la UI. Cambiarlo no
# puede fusionar ni partir nada.
FRACCION_MARGEN_ALTA = 0.5


@dataclass(frozen=True)
class Grupo:
    """Una PROPUESTA de agrupacion de fotos por producto.

    fotos: en orden cronologico (EXIF) dentro del grupo.
    confianza: margen que tuvo el corte, no certeza sobre el producto.
        Ver §confianza en el docstring del modulo.
    motivo: en español, con NUMEROS reales, para que Diego decida de un
        vistazo. Nunca lenguaje de certeza cuando la realidad es ambigua.
    """

    fotos: list[Path]
    confianza: Literal["alta", "media", "baja"]
    motivo: str


def agrupar(fotos: list[Path]) -> list[Grupo]:
    """Propone una agrupacion por producto. Nunca decide — Diego confirma.

    1. Las fotos CON fecha EXIF se ordenan cronologicamente y se cortan en
       cada hueco >= `UMBRAL_HUECO_SEGUNDOS`. Sesgo permanente a cortar de
       mas (ver docstring del modulo).
    2. Las fotos SIN fecha EXIF no tienen señal de agrupacion: van al cajon
       de INCIERTAS, cada una en su propio grupo "baja". **Nunca se meten en
       el grupo que mejor cuadre** (`truth-loop.md` §E) — eso seria adivinar,
       y adivinar aqui es contaminar una ficha.
    """
    if not fotos:
        return []

    metadatos = {foto: leer_metadatos(foto) for foto in fotos}

    con_fecha = sorted(
        (f for f in fotos if metadatos[f].fecha_captura_exif is not None),
        key=lambda f: (metadatos[f].fecha_captura_exif, f.name),
    )
    sin_fecha = [f for f in fotos if metadatos[f].fecha_captura_exif is None]

    if sin_fecha:
        logger.warning(
            "%d de %d fotos del lote no tienen fecha EXIF: no hay señal de "
            "agrupacion para ellas (cada una queda en su propio grupo, para "
            "que Diego las agrupe a mano). Causa habitual: llegaron por "
            "WhatsApp, que borra el EXIF entero (medido: 0/59 conservaron la "
            "fecha). Pasalas por cable.",
            len(sin_fecha),
            len(fotos),
        )

    grupos = _agrupar_por_tiempo(con_fecha, metadatos)
    grupos.extend(_cajon_de_inciertas(sin_fecha))
    return grupos


# --------------------------------------------------------------------------
# Paso 1 — corte por hueco temporal. La unica señal que las fotos reales de
# Diego tienen de verdad.
# --------------------------------------------------------------------------
def _agrupar_por_tiempo(
    con_fecha: list[Path], metadatos: dict[Path, MetadatosImagen]
) -> list[Grupo]:
    if not con_fecha:
        return []

    segmentos: list[list[Path]] = [[con_fecha[0]]]
    for anterior, actual in zip(con_fecha, con_fecha[1:]):
        hueco = (
            metadatos[actual].fecha_captura_exif - metadatos[anterior].fecha_captura_exif
        ).total_seconds()
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
                "Una sola foto entre dos pausas largas. En las fotos reales de "
                "Diego NINGUN producto tiene una sola foto: casi siempre esto es "
                "una foto de detalle, de la etiqueta, del metro o de un papel — "
                "y pertenece al producto de antes o al de despues. Miralo y "
                "fusionala si es asi."
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

    if hueco_maximo < UMBRAL_HUECO_SEGUNDOS * FRACCION_MARGEN_ALTA:
        return Grupo(
            fotos=seg,
            confianza="alta",
            motivo=(
                f"{len(seg)} fotos seguidas en {duracion:.0f} s, sin ninguna pausa "
                f"dentro (hueco interno maximo {hueco_maximo:.0f} s, muy por debajo "
                f"del corte de {UMBRAL_HUECO_SEGUNDOS:.0f} s). Ritmo de rafaga: "
                "encaja con fotografiar un solo producto del tiron."
            ),
        )

    return Grupo(
        fotos=seg,
        confianza="media",
        motivo=(
            f"{len(seg)} fotos en {duracion:.0f} s, pero hay una pausa de "
            f"{hueco_maximo:.0f} s dentro del grupo — cerca del corte de "
            f"{UMBRAL_HUECO_SEGUNDOS:.0f} s. Podrian ser dos productos que se "
            "quedaron juntos. Comprueba que todas las fotos son del mismo."
        ),
    )


# --------------------------------------------------------------------------
# Cajon de INCIERTAS — fotos sin EXIF. `truth-loop.md` §E: "lo que el modelo
# no pueda casar va a un cajon de INCIERTAS, nunca al grupo que mejor cuadre".
# --------------------------------------------------------------------------
def _cajon_de_inciertas(sin_fecha: list[Path]) -> list[Grupo]:
    """Sin fecha EXIF no hay ninguna señal de agrupacion: ni la temporal (no
    existe) ni la visual (medida y descartada, `[INC-004]`). Cada foto queda
    sola, "baja". Es feo a proposito: es la forma honesta de decir "no lo se",
    y es infinitamente mas barato que meter la foto del producto A en la ficha
    del B."""
    return [
        Grupo(
            fotos=[foto],
            confianza="baja",
            motivo=(
                "Esta foto no tiene fecha EXIF, asi que no hay NINGUNA señal "
                "fiable para agruparla (el reloj es la unica que funciona en "
                "este lote). Se deja sola en vez de adivinar: meterla en el "
                "grupo equivocado pondria una foto de otro producto en tu "
                "anuncio. Agrupala a mano."
            ),
        )
        for foto in sorted(sin_fecha, key=lambda f: f.name)
    ]
