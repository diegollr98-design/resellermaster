"""Tests de `core/categorias.py` -- las HOJAS candidatas de categoria.

La propiedad que MAS importa (y la que estos tests blindan) NO es "acierta la
hoja": es que **nunca elige por Diego**. `candidatas()` devuelve un ranking
para que el humano cierre; jamas una hoja unica que alguien pueda meter en
`catalog_id`/`categoria` sin mirar (`decision-making.md` §18, `truth-loop.md`:
una hoja mal = anuncio oculto = venta perdida silenciosa).
"""

from __future__ import annotations

import pytest

from core import categorias


# --- Contrato: PROPONE, no elige ------------------------------------------
def test_devuelve_ranking_nunca_una_hoja_suelta():
    cands = categorias.candidatas("moda", "Sudadera con capucha", "wallapop", k=3)
    assert isinstance(cands, list)
    # Cada elemento es una Candidata (hoja + puntuacion), NUNCA la hoja pelada.
    for c in cands:
        assert isinstance(c, categorias.Candidata)
        assert isinstance(c.hoja, categorias.Hoja)
        assert c.puntuacion > 0


def test_respeta_k():
    for k in (1, 2, 5):
        cands = categorias.candidatas("moda", "camiseta pantalon sudadera falda", "wallapop", k=k)
        assert len(cands) <= k


def test_ordenadas_de_mejor_a_peor():
    cands = categorias.candidatas("moda", "sudadera con capucha deportiva", "wallapop", k=3)
    puntuaciones = [c.puntuacion for c in cands]
    assert puntuaciones == sorted(puntuaciones, reverse=True)


# --- La ropa de Diego: la hoja correcta aparece en el top-k ---------------
@pytest.mark.parametrize(
    ("texto", "esperado_en_nombre"),
    [
        ("Sudadera Reebok gris con capucha", "sudadera"),
        ("Camiseta Umbro manga corta", "camiseta"),
        ("Falda vaquera midi", "falda"),
    ],
)
def test_ropa_real_encuentra_la_hoja(texto, esperado_en_nombre):
    for plataforma in ("wallapop", "vinted"):
        cands = categorias.candidatas("moda", texto, plataforma, k=3)
        nombres = " ".join(c.hoja.nombre.lower() for c in cands)
        assert esperado_en_nombre in nombres, (plataforma, texto, nombres)


# --- Restriccion de subarbol ----------------------------------------------
def test_moda_solo_devuelve_hojas_de_moda_wallapop():
    cands = categorias.candidatas("moda", "camiseta", "wallapop", k=5)
    # La raiz de todas debe ser "Moda y accesorios" (id 12465).
    assert cands  # hay al menos una
    assert all(c.hoja.raiz_id == 12465 for c in cands)


def test_categoria_desconocida_busca_en_todo_el_arbol_sin_lanzar():
    # `None` y una categoria fuera del enum NO lanzan: buscan sin restriccion.
    a = categorias.candidatas(None, "camiseta", "wallapop", k=3)
    b = categorias.candidatas("inexistente", "camiseta", "wallapop", k=3)
    assert isinstance(a, list) and isinstance(b, list)


# --- Casos borde ----------------------------------------------------------
def test_texto_vacio_o_solo_stopwords_no_da_candidatas():
    assert categorias.candidatas("moda", "", "wallapop") == []
    assert categorias.candidatas("moda", "de la el con y para", "wallapop") == []


def test_sin_match_devuelve_vacio_no_ruido():
    # Un texto que no casa con ninguna hoja de moda -> lista vacia, nunca una
    # candidata inventada. (Diego navega a mano: nunca peor que hoy.)
    cands = categorias.candidatas("moda", "xyzzy qwerty zzzznope", "wallapop")
    assert cands == []


# --- Snapshots versionados existen y se leen ------------------------------
def test_los_dos_snapshots_existen_y_tienen_hojas():
    for plataforma, minimo in (("wallapop", 800), ("vinted", 2000)):
        hojas = categorias._cargar_hojas(plataforma)
        assert len(hojas) >= minimo
        assert categorias.fecha_snapshot(plataforma)  # fecha no vacia


def test_plataforma_desconocida_lanza():
    with pytest.raises(FileNotFoundError):
        categorias._cargar_hojas("mercadona")


def test_ruta_completa_incluye_raiz_y_hoja():
    cands = categorias.candidatas("moda", "sudadera", "wallapop", k=1)
    assert cands
    rc = cands[0].hoja.ruta_completa
    assert " > " in rc
    assert rc.endswith(cands[0].hoja.nombre)


# --- Matching robusto a plurales/acentos ----------------------------------
def test_singular_casa_con_plural_de_la_hoja():
    # "sudadera" (consulta) debe casar con "Sudaderas ..." (hoja).
    cands = categorias.candidatas("moda", "sudadera", "wallapop", k=3)
    assert any("sudadera" in c.hoja.nombre.lower() for c in cands)


# --- Género (`[listing-audit] SERIO, 2026-07-17`): la forma natural más común
# ("hombre" singular) DEBE detectarse; antes del fix salía None y ganaba Mujer.
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tokens", "esperado"),
    [
        (["hombre"], "hombre"),          # singular: EL caso que estaba roto
        (["hombres"], "hombre"),         # plural
        (["masculino"], "hombre"),
        (["chico"], "hombre"),
        (["mujer"], "mujer"),
        (["femenino"], "mujer"),
        (["camiseta", "roja"], None),    # sin género
        (["hombre", "mujer"], None),     # ambiguo -> no se sesga
    ],
)
def test_genero_en_detecta_la_forma_natural(tokens, esperado):
    assert categorias._genero_en(tokens) == esperado


def test_genero_hombre_sube_hombre_y_no_todo_mujer():
    # Una prenda de HOMBRE (singular) debe sacar una hoja de Hombre en el top,
    # no las tres de Mujer. Este test FALLABA antes del fix de `_GENERO_STEMS`.
    for plataforma in ("wallapop", "vinted"):
        cands = categorias.candidatas("moda", "Sudadera Reebok gris hombre XXL", plataforma, k=3)
        assert cands
        rutas = " || ".join(c.hoja.ruta_completa.lower() for c in cands)
        assert "hombre" in rutas, (plataforma, rutas)
        # La #1 no puede ser de mujer cuando el texto dice "hombre".
        assert "mujer" not in cands[0].hoja.ruta_completa.lower(), (plataforma, cands[0].hoja.ruta_completa)


def test_genero_mujer_sube_mujer():
    for plataforma in ("wallapop", "vinted"):
        cands = categorias.candidatas("moda", "Falda vaquera midi mujer", plataforma, k=3)
        assert cands
        assert "mujer" in cands[0].hoja.ruta_completa.lower(), (plataforma, cands[0].hoja.ruta_completa)


# --- Ropa infantil: puerta por señal (Diego vende niño, pero un query de
# adulto NO debe sacar hojas de niño -- `[listing-audit]`). --------------------
def test_es_infantil_detecta_las_senales_derivadas():
    # Lee el texto crudo; las señales de talla ('meses'/'años') cuentan aquí
    # aunque sean stopwords del ranking. Derivadas con _stem/_tokens, no a ojo.
    assert categorias._es_infantil("Camiseta de niño")
    assert categorias._es_infantil("Body bebé algodón")
    assert categorias._es_infantil("Pijama 12 meses")
    assert categorias._es_infantil("Camiseta 6 años")
    assert not categorias._es_infantil("Camiseta Reebok hombre")


def test_query_adulto_no_saca_hojas_infantiles():
    # Una camiseta de hombre NO puede sacar candidatas de la sección de niños.
    for plataforma, raiz_infantil in (("wallapop", 12461), ("vinted", 1193)):
        cands = categorias.candidatas("moda", "Camiseta Umbro hombre M", plataforma, k=5)
        assert all(c.hoja.raiz_id != raiz_infantil for c in cands), plataforma


def test_query_infantil_vinted_saca_hojas_infantiles():
    # Con señal infantil, las hojas de niño SÍ entran (Diego vende infantil).
    # Vinted organiza la ropa de niño POR PRENDA (como adulto), así que el
    # ranking por prenda + el `_factor_infantil` la suben bien.
    # NOTA HONESTA: Wallapop organiza la ropa de niño POR EDAD ("Ropa infantil
    # > 6-7 años (116 cm)"), no por prenda -> el ranking por prenda no encaja
    # ahí y las candidatas son flojas (Diego navega los 14 tramos de edad, que
    # es trivial). Es límite del árbol de Wallapop, no del ranking; kids es
    # minoría del catálogo. Por eso este test sólo afirma lo verificable: Vinted.
    cands = categorias.candidatas("moda", "Sudadera de niña 8 años", "vinted", k=5)
    assert any(c.hoja.raiz_id == 1193 for c in cands), [c.hoja.ruta_completa for c in cands]
    assert "niñ" in cands[0].hoja.ruta_completa.lower()
