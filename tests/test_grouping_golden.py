"""EL GATE de `core/grouping.py` — contra las 33 fotos REALES de Diego.

`pytest` en verde no significa nada aqui: **las cuatro versiones anteriores de
`core/grouping.py` tenian todos sus tests en verde** (`[INC-002]`, `[INC-003]`).
Este fichero corre `agrupar()` sobre las fotos que Diego hizo de verdad
(`fotos/IMG_20260714_*.jpg`) y compara contra la verdad que fijo el
(`tests/golden/truth.json`).

**EL GATE, y es el UNICO assert duro sobre el golden set:**

    FUSIONES (dos productos distintos en un mismo grupo) == 0.

Los cortes de mas **NO fallan el test**: se REPORTAN. Es deliberado y es una
correccion de la version anterior de este fichero, que tenia
`assert cortes_de_mas <= 5` y con eso **cerraba por construccion la unica
reparacion que el modulo declara siempre correcta**: bajar el umbral. Quien
reaccionara a una fusion real bajando la constante —lo correcto— se habria
encontrado un test en rojo, y habria sido empujado a SUBIRLA, que es el unico
movimiento que produce el error caro. Una defensa que muerde a quien la
obedece no es una defensa (`decision-making.md` §12).

El eje que mata NO es el que cortaba la version anterior. Aquella solo probaba
el margen **hacia arriba** (subir el umbral fusiona). El fallo real vive **hacia
abajo**: un cambio de producto MAS RAPIDO que el umbral. Eso lo cubre
`test_cambio_rapido_de_producto_*`, que es donde de verdad se juega el modulo
— exactamente la leccion de `[INC-003]`: "hay que barrer el eje donde puede
vivir el fallo, no el comodo".

Si estos tests no encuentran las fotos, se SALTAN con un motivo explicito
(`fotos/` esta gitignored: son fotos de Diego). Un skip es visible; un test que
"pasa" sin datos seria una mentira.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import core.grouping as grouping
from core.grouping import UMBRAL_HUECO_SEGUNDOS, agrupar
from core.images import MetadatosImagen

_REPO = Path(__file__).resolve().parent.parent
_TRUTH = _REPO / "tests" / "golden" / "truth.json"
_FOTOS = _REPO / "fotos"

# Cortes de mas MEDIDOS hoy con UMBRAL_HUECO_SEGUNDOS = 15 (6/6 fronteras, 0
# fusiones, 6 cortes de mas). NO es un assert: es la cifra que se reporta para
# que un cambio de umbral sea visible en la salida del test. Bajar el umbral
# SUBE este numero y eso esta BIEN.
CORTES_DE_MAS_REFERENCIA = 6


@pytest.fixture(scope="module")
def golden() -> tuple[list[Path], dict[str, int]]:
    """(fotos del golden set, {nombre_sin_extension: id_de_producto})."""
    if not _TRUTH.exists():
        pytest.skip(f"No existe el golden set ({_TRUTH})")

    truth = json.loads(_TRUTH.read_text(encoding="utf-8"))
    esperado = {
        nombre: producto["id"]
        for producto in truth["productos"]
        for nombre in producto["fotos"]
    }

    fotos = sorted(_FOTOS.glob("IMG_20260714_*.jpg"))
    if len(fotos) != truth["n_fotos"]:
        pytest.skip(
            f"Las fotos reales de Diego no estan disponibles en {_FOTOS} "
            f"(encontradas {len(fotos)}, esperadas {truth['n_fotos']}). La carpeta "
            "esta gitignored: son sus fotos. Sin ellas este gate NO se puede "
            "evaluar — y sin el gate, un cambio en la agrupacion NO se cierra."
        )
    return fotos, esperado


def _fusiones(grupos, esperado) -> list[str]:
    """Grupos que mezclan mas de un producto real. Es LA metrica."""
    return [
        f"  productos {sorted({esperado[f.stem] for f in g.fotos})} "
        f"(confianza={g.confianza}): {[f.name for f in g.fotos]}"
        for g in grupos
        if len({esperado[f.stem] for f in g.fotos}) > 1
    ]


def _cortes_de_mas(grupos, esperado) -> int:
    """Cuantas veces un producto real quedo repartido en mas de un grupo.

    NO se calcula como `len(grupos) - n_productos`: ese proxy es CIEGO a las
    fusiones (una fusion RESTA un grupo, asi que el numero mejora cuanto peor
    va la agrupacion). Se cuenta producto a producto."""
    grupos_por_producto: dict[int, int] = defaultdict(int)
    for grupo in grupos:
        for producto in {esperado[f.stem] for f in grupo.fotos}:
            grupos_por_producto[producto] += 1
    return sum(n - 1 for n in grupos_por_producto.values())


# --------------------------------------------------------------------------
# EL GATE
# --------------------------------------------------------------------------
def test_el_gate_cero_fusiones(golden):
    """Ningun grupo propuesto puede contener fotos de dos productos distintos.
    Una sola fusion = una foto de otra prenda en el anuncio = venta perdida."""
    fotos, esperado = golden
    fusiones = _fusiones(agrupar(fotos), esperado)

    assert not fusiones, (
        f"FUSION DETECTADA — {len(fusiones)} grupo(s) mezclan productos distintos.\n"
        f"Accion correcta: BAJAR UMBRAL_HUECO_SEGUNDOS (ahora "
        f"{UMBRAL_HUECO_SEGUNDOS:.0f} s), nunca subirlo.\n" + "\n".join(fusiones)
    )


def test_ningun_grupo_sale_con_confianza_alta(golden):
    """Con señal SOLO temporal, `alta` no es derivable: una fusion la causa un
    hueco PEQUEÑO, y un hueco maximo pequeño era justo lo que ganaba `alta` en
    v4 — la confianza estaba anti-correlacionada con el riesgo. Y `alta` es la
    que `ui/confirmacion.py` deja confirmar en bloque SIN MIRAR."""
    fotos, _ = golden
    altas = [g for g in agrupar(fotos) if g.confianza == "alta"]
    assert not altas, (
        f"{len(altas)} grupo(s) salieron con confianza='alta'. El reloj puede "
        "PARTIR, pero no puede CONFIRMAR. `alta` solo podra volver cuando exista "
        "una señal independiente del tiempo (clasificador de tipo de foto)."
    )


def test_todos_los_productos_aparecen(golden):
    fotos, esperado = golden
    grupos = agrupar(fotos)
    vistos = {esperado[f.stem] for g in grupos for f in g.fotos}
    assert vistos == set(esperado.values()), "Falta algun producto en la propuesta"


def test_reporta_cortes_de_mas(golden, capsys):
    """Los cortes de mas son el coste ACEPTADO del diseño (Diego fusiona en ~5 s).
    Se REPORTAN, no se asertan: un assert aqui impediria bajar el umbral, que es
    la reparacion correcta ante una fusion. Ver docstring del modulo."""
    fotos, esperado = golden
    grupos = agrupar(fotos)
    cortes = _cortes_de_mas(grupos, esperado)

    with capsys.disabled():
        print(f"\n--- AGRUPACION sobre las {len(fotos)} fotos reales de Diego ---")
        print(
            f"umbral={UMBRAL_HUECO_SEGUNDOS:.0f}s  productos_reales="
            f"{len(set(esperado.values()))}  grupos={len(grupos)}  "
            f"cortes_de_mas={cortes} (referencia medida: {CORTES_DE_MAS_REFERENCIA})  "
            f"FUSIONES={len(_fusiones(grupos, esperado))}"
        )
        for i, g in enumerate(grupos, 1):
            pid = sorted({esperado[f.stem] for f in g.fotos})
            print(
                f"  [{i:2d}] prod={pid} conf={g.confianza:5s} n={len(g.fotos)}  "
                f"{[f.name[13:-4] for f in g.fotos]}"
            )

    assert cortes >= 0  # el reporte es el objetivo; no hay umbral que superar


def test_barrido_de_umbrales(golden, capsys):
    """La tabla que justifica la constante, RE-DERIVADA en cada ejecucion — no
    citada de memoria en un comentario que puede pudrirse. Demuestra las dos
    cosas que fijan el valor: (1) hay un acantilado hacia ARRIBA, (2) no hay
    ninguno hacia ABAJO."""
    fotos, esperado = golden
    filas = []
    original = grouping.UMBRAL_HUECO_SEGUNDOS
    try:
        for u in range(5, 31):
            grouping.UMBRAL_HUECO_SEGUNDOS = float(u)
            grupos = agrupar(fotos)
            filas.append((u, len(_fusiones(grupos, esperado)), _cortes_de_mas(grupos, esperado)))
    finally:
        grouping.UMBRAL_HUECO_SEGUNDOS = original

    with capsys.disabled():
        print("\n--- BARRIDO DE UMBRALES (golden set real) ---")
        print("umbral  FUSIONES  cortes_de_mas")
        for u, fus, cortes in filas:
            marca = "  <== EN USO" if u == int(original) else ""
            marca += "  *** FUSIONA ***" if fus else ""
            print(f"{u:5d}  {fus:8d}  {cortes:13d}{marca}")

    seguros = [u for u, fus, _ in filas if fus == 0]
    con_fusion = [u for u, fus, _ in filas if fus > 0]

    assert original in seguros, (
        f"El umbral EN USO ({original:.0f} s) fusiona productos en el golden set."
    )
    assert con_fusion, "El barrido no encontro ningun umbral que fusione: revisa el golden set."
    assert max(seguros) < min(con_fusion), (
        "La zona segura y la de fusion no son contiguas: la premisa 'bajar nunca "
        "fusiona' no se sostiene. Vuelve a medir antes de tocar la constante."
    )
    # El colchon hacia el acantilado es la magnitud que de verdad importa.
    colchon = min(con_fusion) - original
    assert colchon >= 2 * 2.4, (
        f"Solo hay {colchon:.0f} s de colchon entre el umbral en uso "
        f"({original:.0f} s) y el primero que fusiona ({min(con_fusion)} s). El "
        "jitter medido de Diego es ±2.4 s: eso es menos de dos jitters. BAJA el "
        "umbral."
    )


# --------------------------------------------------------------------------
# EL EJE QUE MATA — un cambio de producto MAS RAPIDO que el umbral.
# Es la direccion que la version anterior de este fichero NO probaba, y es
# donde vivia el fallo. Se construye con los timestamps REALES de Diego,
# acortando SOLO la frontera entre dos productos.
# --------------------------------------------------------------------------
def _metadatos_falsos(mapa: dict[Path, datetime]):
    def _leer(ruta: Path) -> MetadatosImagen:
        return MetadatosImagen(
            ruta=ruta,
            legible=True,
            formato="JPEG",
            ancho=1,
            alto=1,
            orientacion_exif=1,
            fecha_captura_exif=mapa[ruta],
            mtime_fichero=None,
            error=None,
        )

    return _leer


@pytest.mark.parametrize("segundos_de_cambio", [1, 5, 9, 12, 14])
def test_cambio_rapido_de_producto_no_se_puede_cazar_pero_NO_sale_confiado(
    segundos_de_cambio: int,
):
    """LA VERDAD INCOMODA, fijada como test para que nadie la olvide.

    Si Diego cambia de producto MAS RAPIDO que el umbral, el reloj **no puede
    verlo** — y este modulo los fusionara. Eso es un limite de la señal, no un
    bug arreglable con constantes: sus huecos INTRA-producto llegan a 19 s, asi
    que ningun umbral puede separar un cambio de 9 s sin triturar cada producto.

    Lo que este test SI exige, y es lo que v4 incumplia: que cuando eso pase,
    el grupo contaminado **NO salga con `confianza='alta'`**, porque `alta` es
    la que la UI confirma en bloque sin mirar. Una fusion inevitable que Diego
    puede ver es recuperable. Una fusion inevitable escondida detras de un
    "confianza alta, confirmar en bloque" es la venta perdida.

    La reparacion real de esto NO es temporal: es el clasificador de tipo de
    foto (`truth-loop.md` §E) — un plano general empezando producto.
    """
    base = datetime(2026, 7, 14, 11, 0, 0)
    offsets = [(0, "A"), (4, "A"), (8, "A")]
    t = 8 + segundos_de_cambio
    for i, paso in enumerate([0, 5, 4]):
        t += paso
        offsets.append((t, "B"))
        del i

    fotos = [Path(f"{p}_{i}.jpg") for i, (_, p) in enumerate(offsets)]
    mapa = {
        Path(f"{p}_{i}.jpg"): base + timedelta(seconds=o)
        for i, (o, p) in enumerate(offsets)
    }

    with patch.object(grouping, "leer_metadatos", _metadatos_falsos(mapa)):
        grupos = agrupar(fotos)

    contaminados = [g for g in grupos if len({f.name[0] for f in g.fotos}) > 1]
    for grupo in contaminados:
        assert grupo.confianza != "alta", (
            f"Un cambio de producto de {segundos_de_cambio} s produjo un grupo "
            f"FUSIONADO con confianza='alta' — la UI lo confirmaria en bloque sin "
            "mirarlo. El reloj no puede cazar esta fusion, pero JAMAS puede "
            "firmarla."
        )
        assert "no hubo pausa" in grupo.motivo or "reloj no los" in grupo.motivo, (
            "El motivo de un grupo asi debe advertir que la ausencia de pausa NO "
            f"prueba que sea un solo producto. Motivo real: {grupo.motivo}"
        )


def test_exif_degenerado_no_produce_un_grupo_gigante_confiado():
    """12 fotos, 2 productos, TODAS con el mismo timestamp. v4 devolvia un solo
    grupo con los 2 productos y `confianza='alta'`. Un hueco de 0 s no significa
    "el mismo producto": significa "no se"."""
    t = datetime(2026, 7, 14, 10, 0, 0)
    fotos = [Path(f"A_{i}.jpg") for i in range(6)] + [Path(f"B_{i}.jpg") for i in range(6)]
    mapa = dict.fromkeys(fotos, t)

    with patch.object(grouping, "leer_metadatos", _metadatos_falsos(mapa)):
        grupos = agrupar(fotos)

    fusionados = [g for g in grupos if len({f.name[0] for f in g.fotos}) > 1]
    assert not fusionados, (
        "Con EXIF degenerado (todos los timestamps identicos) el modulo metio "
        "productos distintos en un mismo grupo. El reloj no dio ninguna señal: "
        "las fotos deben ir al cajon de INCIERTAS, sueltas."
    )
    assert all(g.confianza == "baja" for g in grupos)
    assert all("mismo timestamp" in g.motivo for g in grupos)
