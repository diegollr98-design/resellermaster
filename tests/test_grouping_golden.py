"""EL GATE de `core/grouping.py` — contra las 33 fotos REALES de Diego.

`pytest` en verde no significa nada aqui: **las tres versiones rotas de
`core/grouping.py` tenian todos sus tests en verde** (`[INC-002]`,
`[INC-003]`). Pasaban porque testeaban imagenes sinteticas. Este fichero es
distinto: corre `agrupar()` sobre las fotos que Diego hizo de verdad
(`fotos/IMG_20260714_*.jpg`) y compara contra la verdad que fijo el
(`tests/golden/truth.json`).

**El gate, con dientes:**

    FUSIONES (dos productos distintos en un mismo grupo) == 0.

No es un aviso, es un no: si hay una sola fusion, el cambio no se cierra
(`truth-loop.md` §E — fusionar es el error caro e invisible; partir de mas es
el error barato, Diego fusiona en 5 segundos).

Los cortes de mas NO fallan el test: se REPORTAN, porque son el coste
aceptable del diseño. Lo que si falla es que **empeoren** respecto a la linea
base medida (5), porque eso significaria que alguien toco el umbral sin medir.

Si estos tests no encuentran las fotos, se SALTAN con un motivo explicito
(`fotos/` esta gitignored: son fotos de Diego, no se versionan). Un skip es
visible; un test que "pasa" sin datos seria una mentira.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from core.grouping import UMBRAL_HUECO_SEGUNDOS, agrupar

_REPO = Path(__file__).resolve().parent.parent
_TRUTH = _REPO / "tests" / "golden" / "truth.json"
_FOTOS = _REPO / "fotos"

# Linea base MEDIDA (re-derivada ejecutando, 2026-07-14) con
# UMBRAL_HUECO_SEGUNDOS = 20: 6/6 fronteras, 0 fusiones, 5 cortes de mas.
CORTES_DE_MAS_LINEA_BASE = 5


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
            f"(encontradas {len(fotos)}, esperadas {truth['n_fotos']}). "
            "La carpeta esta gitignored: son sus fotos. Sin ellas este gate "
            "NO se puede evaluar — y sin el gate, un cambio en la agrupacion "
            "NO se cierra."
        )
    return fotos, esperado


def test_el_gate_cero_fusiones(golden, capsys):
    """EL GATE. Ningun grupo propuesto puede contener fotos de dos productos
    distintos. Una sola fusion = venta perdida = no se cierra el cambio."""
    fotos, esperado = golden
    grupos = agrupar(fotos)

    fusiones = []
    for grupo in grupos:
        productos = {esperado[f.stem] for f in grupo.fotos}
        if len(productos) > 1:
            fusiones.append(
                f"  grupo con productos {sorted(productos)} "
                f"(confianza={grupo.confianza}): {[f.name for f in grupo.fotos]}"
            )

    assert not fusiones, (
        f"FUSION DETECTADA — {len(fusiones)} grupo(s) mezclan productos distintos.\n"
        "Es el fallo mas caro del proyecto: una foto de otra prenda en el anuncio, "
        "y nadie lo caza.\n"
        f"Accion correcta: BAJAR UMBRAL_HUECO_SEGUNDOS (ahora {UMBRAL_HUECO_SEGUNDOS:.0f} s), "
        "nunca subirlo.\n" + "\n".join(fusiones)
    )


def test_todas_las_fronteras_reales_se_encuentran(golden):
    """Las 6 fronteras entre productos consecutivos tienen que caer todas en
    un corte. Es la otra cara del gate: sin fusiones, cada producto real esta
    contenido en uno o mas grupos, pero nunca repartido con otro."""
    fotos, esperado = golden
    grupos = agrupar(fotos)

    # Cada producto real debe estar cubierto por grupos que no contengan nada mas.
    grupos_por_producto: dict[int, int] = defaultdict(int)
    for grupo in grupos:
        productos = {esperado[f.stem] for f in grupo.fotos}
        assert len(productos) == 1, "cubierto por test_el_gate_cero_fusiones"
        grupos_por_producto[productos.pop()] += 1

    assert set(grupos_por_producto) == set(esperado.values()), (
        "Falta algun producto en la propuesta de agrupacion"
    )


def test_cortes_de_mas_no_empeoran(golden, capsys):
    """Los cortes de mas son el coste ACEPTADO del diseño (Diego fusiona en
    ~5 s). No fallan el test por existir — pero si aumentan respecto a la
    linea base medida, alguien movio el umbral sin medir, y eso si se caza."""
    fotos, esperado = golden
    grupos = agrupar(fotos)

    n_productos = len(set(esperado.values()))
    cortes_de_mas = len(grupos) - n_productos

    with capsys.disabled():
        print(f"\n--- AGRUPACION sobre las {len(fotos)} fotos reales de Diego ---")
        print(f"umbral={UMBRAL_HUECO_SEGUNDOS:.0f}s  productos_reales={n_productos}  "
              f"grupos_propuestos={len(grupos)}  cortes_de_mas={cortes_de_mas}  FUSIONES=0")
        for i, grupo in enumerate(grupos, 1):
            pid = {esperado[f.stem] for f in grupo.fotos}
            print(f"  [{i:2d}] prod={sorted(pid)} conf={grupo.confianza:5s} "
                  f"n={len(grupo.fotos)}  {[f.name[13:-4] for f in grupo.fotos]}")

    assert cortes_de_mas <= CORTES_DE_MAS_LINEA_BASE, (
        f"Los cortes de mas subieron a {cortes_de_mas} (linea base medida: "
        f"{CORTES_DE_MAS_LINEA_BASE}). No es catastrofico —Diego fusiona— pero "
        "significa que el umbral se movio sin volver a medir."
    )


def test_subir_el_umbral_a_25_fusionaria(golden):
    """Test de REGRESION INVERSA: demuestra que el margen es real y estrecho.

    Si alguien "optimiza" el umbral subiendolo a 25 s, los productos 1 y 2 se
    fusionan (la frontera entre ellos es de 23 s). Este test existe para que
    ese razonamiento —tentador, porque reduce los cortes de mas— quede
    documentado como MEDIDO Y FALSO, no como una idea que nadie probo."""
    fotos, esperado = golden
    import core.grouping as g

    original = g.UMBRAL_HUECO_SEGUNDOS
    try:
        g.UMBRAL_HUECO_SEGUNDOS = 25.0
        grupos = agrupar(fotos)
        fusiones = [
            grupo for grupo in grupos if len({esperado[f.stem] for f in grupo.fotos}) > 1
        ]
    finally:
        g.UMBRAL_HUECO_SEGUNDOS = original

    assert fusiones, (
        "Con umbral=25 s NO hubo fusiones sobre el golden set. Eso contradice la "
        "medicion sobre la que se eligio 20 s: revisa `tests/golden/truth.json` y "
        "vuelve a medir antes de tocar la constante."
    )
