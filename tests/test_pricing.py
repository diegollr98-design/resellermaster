"""Tests de core/pricing.py -- costura 2.

Cubre, en orden:
1. `precio` es SIEMPRE `None` -- incluso con datos perfectos y con EAN.
   Y `ConsultaPrecio` es estructuralmente incapaz de llevar un numero.
2. Un atributo con fuente "inferido" (o "comparable") NUNCA entra en
   los terminos de busqueda.
3. El EAN, cuando existe y es observado, se marca como match EXACTO y
   es el termino principal.
4. Sin datos suficientes -> motivo claro, consulta vacia (nunca una
   busqueda vacia que devuelva el catalogo entero).
5. El caso de fallo (decision-making.md SS 16): producto SIN NINGUN
   atributo legible -- se ejecuta, no se lee.
6. Formato real de las URLs (dominios verificados, URL-encoding).
"""

from __future__ import annotations

import pytest

from core.pricing import (
    CAMPO_EAN,
    Comparable,
    ConsultaPrecio,
    buscar,
    buscar_comparables_por_imagen,
)
from core.schema import Campo, Evidencia, TallaWallapop

# ============================================================================
# Helpers
# ============================================================================


def _foto(valor, fichero: str = "IMG_0001.jpg") -> Campo:
    return Campo(valor=valor, fuente="foto", confianza="alta", evidencia=Evidencia(fichero))


def _diego(valor) -> Campo:
    return Campo(valor=valor, fuente="diego", confianza="alta")


def _inferido(valor) -> Campo:
    return Campo(valor=valor, fuente="inferido", confianza="baja")


def _comparable(valor) -> Campo:
    return Campo(valor=valor, fuente="comparable", confianza="media")


# ============================================================================
# 1. El precio NUNCA sale de aqui
# ============================================================================


class TestPrecioSiempreNone:
    def test_con_datos_perfectos_precio_es_none(self):
        producto = {
            "marca": _foto("Reebok"),
            "tipo": _foto("sudadera"),
            "talla": _foto("M"),
        }
        resultado = buscar(producto)
        assert resultado.precio is None

    def test_con_ean_precio_es_none(self):
        producto = {CAMPO_EAN: _foto("8445061029720")}
        resultado = buscar(producto)
        assert resultado.precio is None

    def test_sin_ningun_dato_precio_es_none(self):
        resultado = buscar({})
        assert resultado.precio is None

    def test_construir_consulta_con_precio_no_none_lanza(self):
        # Estructuralmente imposible: ni siquiera a mano se puede colar
        # un numero en este dataclass.
        with pytest.raises(ValueError):
            ConsultaPrecio(
                urls_busqueda={},
                terminos="reebok",
                precio=19.99,  # type: ignore[arg-type]
                motivo="intento de colar un precio",
            )

    def test_construir_consulta_con_precio_cero_tambien_lanza(self):
        # 0 no es "falsy None": tiene que fallar igual.
        with pytest.raises(ValueError):
            ConsultaPrecio(
                urls_busqueda={},
                terminos="reebok",
                precio=0,  # type: ignore[arg-type]
                motivo="intento de colar un precio",
            )


# ============================================================================
# 2. "inferido" (y "comparable") NUNCA entran en la busqueda
# ============================================================================


class TestFuenteInferidaExcluida:
    def test_marca_inferida_no_entra_en_terminos(self):
        producto = {"marca": _inferido("Nike")}
        resultado = buscar(producto)
        assert "Nike" not in resultado.terminos
        assert resultado.urls_busqueda == {}
        assert resultado.tipo_match is None

    def test_mezcla_de_fuentes_solo_usa_foto_y_diego(self):
        producto = {
            "marca": _foto("Reebok"),
            "modelo": _inferido("Classic"),  # no debe colarse
            "tipo": _diego("sudadera"),
            "talla": _inferido("M"),  # no debe colarse
        }
        resultado = buscar(producto)
        assert "Reebok" in resultado.terminos
        assert "sudadera" in resultado.terminos
        assert "Classic" not in resultado.terminos
        assert "M" not in resultado.terminos.split()  # talla inferida fuera

    def test_ean_inferido_no_se_usa_como_match_exacto(self):
        # Un EAN "leido" con fuente inferido no es un EAN LEIDO de verdad.
        producto = {CAMPO_EAN: _inferido("8445061029720"), "marca": _foto("Reebok")}
        resultado = buscar(producto)
        assert resultado.tipo_match == "aproximado"
        assert "8445061029720" not in resultado.terminos
        assert "Reebok" in resultado.terminos

    def test_fuente_comparable_tampoco_entra(self):
        producto = {"marca": _comparable("Reebok")}
        resultado = buscar(producto)
        assert resultado.urls_busqueda == {}


# ============================================================================
# 3. El atajo del EAN -- match EXACTO
# ============================================================================


class TestAtajoEAN:
    def test_ean_por_foto_es_match_exacto(self):
        producto = {CAMPO_EAN: _foto("8445061029720")}
        resultado = buscar(producto)
        assert resultado.tipo_match == "exacto"
        assert resultado.terminos == "8445061029720"

    def test_ean_por_diego_tambien_es_match_exacto(self):
        producto = {CAMPO_EAN: _diego("8445061029720")}
        resultado = buscar(producto)
        assert resultado.tipo_match == "exacto"

    def test_ean_presente_ignora_marca_en_los_terminos(self):
        # El EAN es el termino PRINCIPAL: no se diluye con otros campos.
        producto = {
            CAMPO_EAN: _foto("8445061029720"),
            "marca": _foto("lufthous"),
            "tipo": _foto("masajeador"),
        }
        resultado = buscar(producto)
        assert resultado.terminos == "8445061029720"

    def test_sin_ean_el_match_es_aproximado(self):
        producto = {"marca": _foto("Reebok")}
        resultado = buscar(producto)
        assert resultado.tipo_match == "aproximado"


# ============================================================================
# Talla/color son MODIFICADORES, no IDENTIFICATIVOS -- no sostienen una
# consulta ellos solos (agujero reportado por el orquestador: "M" sola
# construia una URL real que devuelve el catalogo entero).
# ============================================================================


class TestTallaSolaNoIdentificaElProducto:
    def test_marca_inferida_mas_talla_por_foto_no_construye_consulta(self):
        """Reproduccion EXACTA del agujero: la marca inferida se
        descarta correctamente, pero antes del fix quedaba
        terminos='M' y SI se construia la URL -- comparables de
        cualquier producto con talla M, no de esta prenda."""

        producto = {
            "marca": Campo("Nike", "inferido", "alta"),
            "talla": Campo("M", "foto", "alta", Evidencia("x.jpg")),
        }
        resultado = buscar(producto)

        assert resultado.terminos == ""
        assert resultado.urls_busqueda == {}
        assert resultado.tipo_match is None
        assert resultado.precio is None
        assert "no identifican" in resultado.motivo

    def test_solo_talla_legible_sin_ningun_identificativo(self):
        producto = {"talla": _foto("M")}
        resultado = buscar(producto)
        assert resultado.urls_busqueda == {}
        assert resultado.terminos == ""
        assert resultado.tipo_match is None
        assert "no identifican" in resultado.motivo

    def test_talla_mas_marca_legibles_si_construye_consulta_y_talla_entra(self):
        """Caso mixto: con un identificativo real presente, la talla
        SI puede acompanar la consulta."""

        producto = {
            "marca": _foto("Reebok"),
            "talla": _foto("M"),
        }
        resultado = buscar(producto)

        assert resultado.urls_busqueda != {}
        assert resultado.tipo_match == "aproximado"
        assert "Reebok" in resultado.terminos
        assert "M" in resultado.terminos.split()


# ============================================================================
# 4. Sin datos suficientes -> motivo claro, no una consulta basura
# ============================================================================


class TestSinDatosSuficientes:
    def test_dict_vacio_no_hay_con_que_buscar(self):
        resultado = buscar({})
        assert resultado.urls_busqueda == {}
        assert resultado.terminos == ""
        assert resultado.tipo_match is None
        assert "no hay con que buscar" in resultado.motivo

    def test_solo_campos_con_valor_none_no_hay_con_que_buscar(self):
        producto = {
            "marca": Campo(valor=None, fuente="foto", confianza="baja", evidencia=Evidencia("x.jpg")),
            "tipo": Campo(valor=None, fuente="diego", confianza="baja"),
        }
        resultado = buscar(producto)
        assert resultado.urls_busqueda == {}
        assert "no hay con que buscar" in resultado.motivo

    def test_solo_inferidos_no_hay_con_que_buscar(self):
        producto = {"marca": _inferido("Nike"), "tipo": _inferido("sudadera")}
        resultado = buscar(producto)
        assert resultado.urls_busqueda == {}
        assert "no hay con que buscar" in resultado.motivo

    def test_con_datos_el_motivo_dice_que_v1_no_tasa(self):
        resultado = buscar({"marca": _foto("Reebok")})
        assert "no tasa" in resultado.motivo


# ============================================================================
# 5. EL CASO DE FALLO -- decision-making.md SS 16, se ejecuta, no se lee
# ============================================================================


class TestCasoDeFallo:
    def test_producto_sin_ningun_atributo_legible_degrada_a_abstencion(self):
        """Un producto totalmente ilegible NUNCA produce una busqueda
        vacia que devolveria el catalogo entero -- degrada a "no hay
        con que buscar", el lado barato de la asimetria."""

        producto_ilegible = {
            "marca": _inferido("podria ser Nike"),
            "modelo": Campo(valor=None, fuente="foto", confianza="baja", evidencia=Evidencia("x.jpg")),
        }
        resultado = buscar(producto_ilegible)

        assert resultado.precio is None
        assert resultado.urls_busqueda == {}, "una busqueda vacia devolveria el catalogo entero"
        assert resultado.terminos == ""
        assert resultado.tipo_match is None
        assert resultado.motivo != ""

    def test_producto_completamente_vacio(self):
        resultado = buscar({})
        assert resultado.urls_busqueda == {}
        assert resultado.terminos == ""


# ============================================================================
# 6. Formato de las URLs -- verificado contra las webs reales, no inventado
# ============================================================================


class TestFormatoURLs:
    def test_url_wallapop_usa_el_dominio_y_parametro_verificados(self):
        resultado = buscar({"marca": _foto("Reebok")})
        assert resultado.urls_busqueda["wallapop"].startswith(
            "https://es.wallapop.com/search?keywords="
        )

    def test_url_vinted_usa_el_dominio_y_parametro_verificados(self):
        resultado = buscar({"marca": _foto("Reebok")})
        assert resultado.urls_busqueda["vinted"].startswith(
            "https://www.vinted.es/catalog?search_text="
        )

    def test_termino_con_espacios_se_codifica(self):
        producto = {"marca": _foto("Reebok"), "tipo": _foto("sudadera gris")}
        resultado = buscar(producto)
        assert " " not in resultado.urls_busqueda["wallapop"]
        assert " " not in resultado.urls_busqueda["vinted"]
        assert "+" in resultado.urls_busqueda["wallapop"] or "%20" in resultado.urls_busqueda["wallapop"]

    def test_talla_wallapop_dataclass_se_extrae_como_string(self):
        producto = {
            "marca": _foto("Zara"),
            "talla": _foto(TallaWallapop(valor="XS / 34 / 6")),
        }
        resultado = buscar(producto)
        assert "XS" in resultado.terminos


# ============================================================================
# v2 -- sigue sin implementar, a proposito
# ============================================================================


class TestV2NoImplementado:
    def test_buscar_comparables_por_imagen_lanza_not_implemented(self):
        with pytest.raises(NotImplementedError):
            buscar_comparables_por_imagen({}, ())

    def test_comparable_dataclass_existe_y_es_tipada(self):
        c = Comparable(url="https://es.wallapop.com/item/x", precio=12.5, similitud_visual=0.9)
        assert c.precio == 12.5
