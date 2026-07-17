"""Tests de core/schema.py — costura 3.

Cubre, en orden:
1. El invariante de procedencia (`Campo`/`Evidencia`) — truth-loop.md SS A.
2. Los mapeos de estado — los tres conjuntos de literales exactos.
3. El sanitizador de texto — cada codigo de violacion, uno por uno.
"""

from __future__ import annotations

import pytest

from core.schema import (
    VINTED_CAMPOS,
    WALLAPOP_CAMPOS,
    WALLAPOP_ATRIBUTOS_POR_CATEGORIA,
    WALLAPOP_ESTADOS_MODA,
    WALLAPOP_ESTADOS_RESTO,
    VINTED_ESTADOS,
    Campo,
    Evidencia,
    EstadoCanonico,
    TallaWallapop,
    Violacion,
    es_exportable,
    fidelidad_estado,
    mapear_estado_vinted,
    mapear_estado_wallapop,
    mapear_talla_a_vinted,
    validar_texto,
)

# ============================================================================
# 1. Invariante de procedencia
# ============================================================================


class TestEvidencia:
    def test_evidencia_valida_solo_fichero(self):
        ev = Evidencia(fichero="IMG_0421.jpg")
        assert ev.fichero == "IMG_0421.jpg"
        assert ev.bbox is None

    def test_evidencia_valida_con_bbox(self):
        ev = Evidencia(fichero="IMG_0421.jpg", bbox=(10, 20, 100, 50))
        assert ev.bbox == (10, 20, 100, 50)

    def test_evidencia_fichero_vacio_lanza(self):
        with pytest.raises(ValueError):
            Evidencia(fichero="")

    def test_evidencia_fichero_solo_espacios_lanza(self):
        with pytest.raises(ValueError):
            Evidencia(fichero="   ")

    def test_evidencia_bbox_negativo_lanza(self):
        with pytest.raises(ValueError):
            Evidencia(fichero="IMG_0421.jpg", bbox=(-1, 0, 10, 10))

    def test_evidencia_bbox_longitud_incorrecta_lanza(self):
        with pytest.raises(ValueError):
            Evidencia(fichero="IMG_0421.jpg", bbox=(1, 2, 3))  # type: ignore[arg-type]


class TestCampo:
    def test_fuente_foto_sin_evidencia_lanza(self):
        """EL invariante central del proyecto: fuente=foto sin evidencia
        es un bug, no un dato. Debe reventar, no avisar."""
        with pytest.raises(ValueError):
            Campo(valor="Nike", fuente="foto", confianza="alta", evidencia=None)

    def test_fuente_foto_con_evidencia_ok(self):
        ev = Evidencia(fichero="IMG_0421.jpg", bbox=(0, 0, 50, 50))
        campo = Campo(valor="Nike", fuente="foto", confianza="alta", evidencia=ev)
        assert campo.valor == "Nike"
        assert campo.evidencia is ev

    def test_fuente_diego_sin_evidencia_ok(self):
        campo = Campo(valor="Nike", fuente="diego", confianza="alta")
        assert campo.evidencia is None

    def test_fuente_inferido_sin_evidencia_ok(self):
        campo = Campo(valor="parece de algodon", fuente="inferido", confianza="baja")
        assert campo.evidencia is None

    def test_fuente_comparable_sin_evidencia_ok(self):
        campo = Campo(valor=19.99, fuente="comparable", confianza="media")
        assert campo.evidencia is None

    def test_valor_none_es_correcto_no_error(self):
        """Un campo vacio es un resultado CORRECTO, no un fallo."""
        campo = Campo(valor=None, fuente="foto", confianza="baja",
                       evidencia=Evidencia(fichero="IMG_0421.jpg"))
        assert campo.valor is None

        campo_diego = Campo(valor=None, fuente="diego", confianza="baja")
        assert campo_diego.valor is None

    def test_fuente_invalida_lanza(self):
        with pytest.raises(ValueError):
            Campo(valor="Nike", fuente="adivinado", confianza="alta")  # type: ignore[arg-type]

    def test_confianza_invalida_lanza(self):
        with pytest.raises(ValueError):
            Campo(valor="Nike", fuente="diego", confianza="segura")  # type: ignore[arg-type]


# ============================================================================
# 2. Mapeos de estado
# ============================================================================


class TestLiteralesExactos:
    def test_vinted_seis_literales_exactos(self):
        assert VINTED_ESTADOS == (
            "Nuevo",
            "Como nuevo",
            "Muy bueno",
            "Bueno",
            "Satisfactorio",
            "Necesita reparación",
        )

    def test_wallapop_moda_cinco_literales_exactos(self):
        assert WALLAPOP_ESTADOS_MODA == (
            "Nuevo",
            "Sin estrenar",
            "Como nuevo",
            "Buen estado",
            "En condiciones aceptables",
        )

    def test_wallapop_resto_ocho_literales_exactos(self):
        assert WALLAPOP_ESTADOS_RESTO == WALLAPOP_ESTADOS_MODA + (
            "Sin abrir",
            "En su caja",
            "Lo ha dado todo",
        )

    def test_no_existe_bueno_a_secas_en_wallapop(self):
        # Error comun documentado: es "Buen estado", NO "Bueno".
        assert "Bueno" not in WALLAPOP_ESTADOS_MODA
        assert "Buen estado" in WALLAPOP_ESTADOS_MODA

    def test_no_existe_aceptable_a_secas_en_wallapop(self):
        assert "Aceptable" not in WALLAPOP_ESTADOS_MODA
        assert "En condiciones aceptables" in WALLAPOP_ESTADOS_MODA

    def test_nuevo_con_etiquetas_no_existe_en_vinted(self):
        # "Nuevo con etiquetas"/"Nuevo sin etiquetas" NO son literales de Vinted.
        assert all("etiqueta" not in e.lower() for e in VINTED_ESTADOS)


class TestMapeoWallapop:
    @pytest.mark.parametrize("estado", list(EstadoCanonico))
    def test_moda_devuelve_literal_valido(self, estado):
        resultado = mapear_estado_wallapop(estado, "moda")
        assert resultado in WALLAPOP_ESTADOS_MODA

    @pytest.mark.parametrize("estado", list(EstadoCanonico))
    def test_resto_devuelve_literal_valido(self, estado):
        resultado = mapear_estado_wallapop(estado, "hogar")
        assert resultado in WALLAPOP_ESTADOS_RESTO

    def test_precintado_en_resto_es_sin_abrir(self):
        assert mapear_estado_wallapop(EstadoCanonico.PRECINTADO, "electronica") == "Sin abrir"

    def test_para_reparar_en_resto_es_lo_ha_dado_todo(self):
        assert mapear_estado_wallapop(EstadoCanonico.PARA_REPARAR, "hogar") == "Lo ha dado todo"

    def test_para_reparar_en_moda_baja_al_mas_bajo_disponible(self):
        # Moda no tiene "Lo ha dado todo": debe caer al literal mas bajo,
        # no inventarse uno ni subir a uno mejor.
        resultado = mapear_estado_wallapop(EstadoCanonico.PARA_REPARAR, "moda")
        assert resultado == "En condiciones aceptables"
        assert resultado == WALLAPOP_ESTADOS_MODA[-1]

    def test_muy_bueno_sin_equivalente_baja_a_buen_estado(self):
        # No existe "Muy bueno" en Wallapop: el sesgo debe bajar (Buen
        # estado), nunca subir (Como nuevo).
        resultado = mapear_estado_wallapop(EstadoCanonico.MUY_BUENO, "moda")
        assert resultado == "Buen estado"
        indice_como_nuevo = WALLAPOP_ESTADOS_MODA.index("Como nuevo")
        indice_resultado = WALLAPOP_ESTADOS_MODA.index(resultado)
        assert indice_resultado > indice_como_nuevo  # "mas bajo" = mas atras en la lista


class TestMapeoVinted:
    @pytest.mark.parametrize("estado", list(EstadoCanonico))
    @pytest.mark.parametrize("categoria", ["moda", "electronica", "hogar", "libros", "otros"])
    def test_siempre_devuelve_literal_valido(self, estado, categoria):
        resultado = mapear_estado_vinted(estado, categoria)
        assert resultado in VINTED_ESTADOS

    def test_necesita_reparacion_solo_en_electronica(self):
        assert (
            mapear_estado_vinted(EstadoCanonico.PARA_REPARAR, "electronica")
            == "Necesita reparación"
        )

    def test_para_reparar_fuera_de_electronica_baja_a_satisfactorio(self):
        # Fuera de electronica no existe "Necesita reparación": debe caer
        # al literal mas bajo de la escala general, nunca inventar uno.
        resultado = mapear_estado_vinted(EstadoCanonico.PARA_REPARAR, "moda")
        assert resultado == "Satisfactorio"
        assert resultado == VINTED_ESTADOS[-2]  # el ultimo antes de "Necesita reparación"

    def test_precintado_es_techo_nuevo_no_algo_mejor(self):
        assert mapear_estado_vinted(EstadoCanonico.PRECINTADO, "moda") == "Nuevo"


class TestFidelidadEstado:
    """`schema.fidelidad_estado` -- `[listing-audit] BLOQUEANTE, 2026-07-17`:
    el literal más bajo disponible puede ser CORRECTO (sesgo oficial de
    Vinted) y AUN ASÍ comunicar un nivel mejor que el real cuando comparte
    literal con un estado FUNCIONAL. El caso de fallo (`decision-making.md`
    §16) es el que importa: una sudadera ROTA presentada como "usada pero
    aceptable", en silencio.
    """

    # -- el caso de fallo: PARA_REPARAR compartiendo literal con un nivel
    #    funcional -- es INFIEL, debe devolver una nota, no `None`.
    def test_para_reparar_moda_wallapop_es_infiel(self):
        nota = fidelidad_estado(EstadoCanonico.PARA_REPARAR, "moda", "wallapop")
        assert nota is not None
        assert "NO FUNCIONAL" in nota

    def test_para_reparar_moda_vinted_es_infiel(self):
        nota = fidelidad_estado(EstadoCanonico.PARA_REPARAR, "moda", "vinted")
        assert nota is not None

    @pytest.mark.parametrize("categoria", ["hogar", "libros", "otros"])
    def test_para_reparar_fuera_de_moda_vinted_tambien_infiel(self, categoria):
        # Sólo electronica tiene literal PROPIO ("Necesita reparación") en
        # Vinted; el resto cae a la escala general compartida con ACEPTABLE.
        nota = fidelidad_estado(EstadoCanonico.PARA_REPARAR, categoria, "vinted")
        assert nota is not None

    # -- el caso seguro: cuando la plataforma SÍ tiene un literal propio para
    #    "no funciona", no se marca -- si no, el aviso se vuelve ruido y
    #    entrena a ignorarlo.
    def test_para_reparar_electronica_vinted_es_fiel(self):
        assert fidelidad_estado(EstadoCanonico.PARA_REPARAR, "electronica", "vinted") is None

    def test_para_reparar_otros_wallapop_es_fiel(self):
        # `_WALLAPOP_RESTO` tiene "Lo ha dado todo", literal propio.
        assert fidelidad_estado(EstadoCanonico.PARA_REPARAR, "otros", "wallapop") is None

    def test_para_reparar_hogar_wallapop_es_fiel(self):
        assert fidelidad_estado(EstadoCanonico.PARA_REPARAR, "hogar", "wallapop") is None

    # -- ambigüedades de VOCABULARIO (no de veracidad): dos niveles
    #    funcionales comparten literal -- eso es seguro, nunca se marca.
    @pytest.mark.parametrize("categoria", ["moda", "electronica", "hogar", "libros", "otros"])
    @pytest.mark.parametrize("plataforma", ["wallapop", "vinted"])
    def test_precintado_nunca_es_infiel(self, categoria, plataforma):
        assert fidelidad_estado(EstadoCanonico.PRECINTADO, categoria, plataforma) is None

    @pytest.mark.parametrize("categoria", ["moda", "electronica", "hogar", "libros", "otros"])
    @pytest.mark.parametrize("plataforma", ["wallapop", "vinted"])
    def test_muy_bueno_nunca_es_infiel(self, categoria, plataforma):
        assert fidelidad_estado(EstadoCanonico.MUY_BUENO, categoria, plataforma) is None

    @pytest.mark.parametrize("categoria", ["moda", "electronica", "hogar", "libros", "otros"])
    @pytest.mark.parametrize("plataforma", ["wallapop", "vinted"])
    def test_bueno_nunca_es_infiel(self, categoria, plataforma):
        # BUENO comparte "Buen estado" con MUY_BUENO en Wallapop, pero ambos
        # son FUNCIONALES: es una ambigüedad de vocabulario, no una mentira.
        assert fidelidad_estado(EstadoCanonico.BUENO, categoria, plataforma) is None

    @pytest.mark.parametrize("categoria", ["moda", "electronica", "hogar", "libros", "otros"])
    @pytest.mark.parametrize("plataforma", ["wallapop", "vinted"])
    def test_solo_para_reparar_puede_ser_infiel(self, categoria, plataforma):
        for estado in EstadoCanonico:
            if estado == EstadoCanonico.PARA_REPARAR:
                continue
            assert fidelidad_estado(estado, categoria, plataforma) is None

    def test_plataforma_desconocida_lanza(self):
        with pytest.raises(ValueError):
            fidelidad_estado(EstadoCanonico.PARA_REPARAR, "moda", "ebay")  # type: ignore[arg-type]


class TestTallaNoImplementada:
    def test_mapear_talla_lanza_not_implemented(self):
        with pytest.raises(NotImplementedError):
            mapear_talla_a_vinted(TallaWallapop(valor="XS / 34 / 6"), catalog_id=123)


class TestEsquemaDeclarativo:
    def test_vinted_brand_es_obligatoria(self):
        brand = next(c for c in VINTED_CAMPOS if c.nombre == "brand")
        assert brand.obligatorio is True

    def test_vinted_title_limite_5_100(self):
        title = next(c for c in VINTED_CAMPOS if c.nombre == "title")
        assert title.limite.minimo == 5
        assert title.limite.maximo == 100

    def test_vinted_description_limite_5_2000(self):
        description = next(c for c in VINTED_CAMPOS if c.nombre == "description")
        assert description.limite.minimo == 5
        assert description.limite.maximo == 2000

    def test_vinted_colores_max_2_materiales_max_3(self):
        colores = next(c for c in VINTED_CAMPOS if c.nombre == "colores")
        materiales = next(c for c in VINTED_CAMPOS if c.nombre == "materiales")
        assert colores.maximo_items == 2
        assert materiales.maximo_items == 3

    def test_vinted_fotos_max_20(self):
        fotos = next(c for c in VINTED_CAMPOS if c.nombre == "fotos")
        assert fotos.maximo_items == 20

    def test_wallapop_fotos_max_10(self):
        fotos = next(c for c in WALLAPOP_CAMPOS if c.nombre == "fotos")
        assert fotos.maximo_items == 10

    def test_wallapop_title_limite_duro_es_el_de_vinted_100(self):
        title = next(c for c in WALLAPOP_CAMPOS if c.nombre == "title")
        assert title.limite.maximo == 100

    def test_wallapop_moda_no_tiene_material(self):
        assert "material" not in WALLAPOP_ATRIBUTOS_POR_CATEGORIA["moda"]
        assert set(WALLAPOP_ATRIBUTOS_POR_CATEGORIA["moda"]) == {
            "brand", "size", "color", "condition",
        }

    def test_wallapop_electronica_atributos(self):
        assert set(WALLAPOP_ATRIBUTOS_POR_CATEGORIA["electronica"]) == {
            "brand", "model", "storage_capacity", "color", "condition",
        }

    def test_wallapop_hogar_atributos(self):
        assert set(WALLAPOP_ATRIBUTOS_POR_CATEGORIA["hogar"]) == {
            "height_cm", "width_cm", "length_cm", "material", "color", "is_bulky",
        }

    def test_wallapop_libros_atributos(self):
        assert set(WALLAPOP_ATRIBUTOS_POR_CATEGORIA["libros"]) == {
            "isbn", "author", "publisher", "language", "book_format",
        }


# ============================================================================
# 3. Sanitizador de texto — un test por codigo de violacion
# ============================================================================


def _codigos(violaciones: list[Violacion]) -> set[str]:
    return {v.codigo for v in violaciones}


class TestValidarTexto:
    def test_texto_limpio_no_tiene_violaciones(self):
        texto = (
            "Camiseta en buen estado, apenas usada. Talla M, corte "
            "clasico, tejido comodo para el dia a dia."
        )
        violaciones = validar_texto(texto, "vinted", marca_seleccionada="Sin marca")
        assert violaciones == []
        assert es_exportable(texto, "vinted", marca_seleccionada="Sin marca")

    def test_contains_email(self):
        texto = "Contactame en diego@example.com si tienes dudas, gracias por leer todo esto."
        violaciones = validar_texto(texto, "vinted", None)
        assert "CONTAINS_EMAIL" in _codigos(violaciones)
        assert not es_exportable(texto, "vinted", None)

    def test_contains_link(self):
        texto = "Mira mas fotos en https://ejemplo.com/producto para que veas el estado real."
        violaciones = validar_texto(texto, "vinted", None)
        assert "CONTAINS_LINK" in _codigos(violaciones)

    def test_contains_link_www_sin_esquema(self):
        texto = "Visita www.ejemplo.com para ver mas fotos del producto en detalle, gracias."
        violaciones = validar_texto(texto, "vinted", None)
        assert "CONTAINS_LINK" in _codigos(violaciones)

    def test_excessive_uppercase(self):
        texto = "ESTA CAMISETA ESTA COMO NUEVA Y SE VENDE MUY BARATA HOY MISMO"
        violaciones = validar_texto(texto, "vinted", None)
        assert "EXCESSIVE_UPPERCASE" in _codigos(violaciones)

    def test_mayusculas_normales_no_disparan(self):
        texto = "Camiseta Nike talla M en buen estado, poco uso, envio rapido y seguro."
        violaciones = validar_texto(texto, "vinted", "Nike")
        assert "EXCESSIVE_UPPERCASE" not in _codigos(violaciones)

    def test_excessive_symbols_repeticion(self):
        texto = "Precio increible!!!! Aprovecha esta oferta unica de temporada, corre ya."
        violaciones = validar_texto(texto, "vinted", None)
        assert "EXCESSIVE_SYMBOLS" in _codigos(violaciones)

    def test_unallowed_symbols_emoji(self):
        texto = "Camiseta preciosa 🔥🔥 apenas usada, talla M, muy comoda para el verano ya"
        violaciones = validar_texto(texto, "vinted", None)
        assert "UNALLOWED_SYMBOLS" in _codigos(violaciones)

    def test_long_words(self):
        texto = (
            "Compralaahoraaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa antes de que se agote "
            "el stock disponible en tienda."
        )
        violaciones = validar_texto(texto, "vinted", None)
        assert "LONG_WORDS" in _codigos(violaciones)

    def test_too_short_description(self):
        violaciones = validar_texto("Hola", "vinted", None, campo="description")
        assert "TOO_SHORT" in _codigos(violaciones)

    def test_too_long_description_vinted(self):
        texto = "a" * 2001
        violaciones = validar_texto(texto, "vinted", None, campo="description")
        assert "TOO_LONG" in _codigos(violaciones)

    def test_too_long_title_wallapop_limite_compartido_100(self):
        texto = "a" * 101
        violaciones = validar_texto(texto, "wallapop", None, campo="title")
        assert "TOO_LONG" in _codigos(violaciones)

    def test_mentions_other_brand(self):
        texto = "Esta camiseta es identica a las de Nike pero de otra marca generica buena."
        violaciones = validar_texto(texto, "vinted", marca_seleccionada="Adidas")
        assert "MENTIONS_OTHER_BRAND" in _codigos(violaciones)

    def test_mentions_selected_brand_no_dispara(self):
        texto = "Camiseta Nike original, talla M, en muy buen estado y con poco uso real."
        violaciones = validar_texto(texto, "vinted", marca_seleccionada="Nike")
        assert "MENTIONS_OTHER_BRAND" not in _codigos(violaciones)

    def test_sin_marca_seleccionada_no_rompe(self):
        texto = "Camiseta comoda y bonita, talla M, en buen estado general de uso diario."
        violaciones = validar_texto(texto, "vinted", marca_seleccionada=None)
        assert "MENTIONS_OTHER_BRAND" not in _codigos(violaciones)

    def test_plataforma_desconocida_lanza(self):
        with pytest.raises(ValueError):
            validar_texto("texto valido de sobra para pasar el minimo", "ebay", None)  # type: ignore[arg-type]

    def test_es_exportable_bloquea_no_solo_avisa(self):
        texto_sucio = "CONTACTAME EN diego@example.com AHORA MISMO!!!! 🔥🔥🔥🔥"
        assert es_exportable(texto_sucio, "vinted", None) is False
        assert len(validar_texto(texto_sucio, "vinted", None)) >= 3
