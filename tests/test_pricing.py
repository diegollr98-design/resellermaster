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
    LIMITE_COMPARABLES,
    N_MINIMO_COMPARABLES,
    Comparable,
    ConsultaPrecio,
    Tasacion,
    buscar,
    buscar_comparables_por_imagen,
    tasar,
)
from core.schema import Campo, Evidencia, TallaWallapop


# ----------------------------------------------------------------------------
# Doble de `Buscador` -- los tests de `tasar` NUNCA tocan la red.
# ----------------------------------------------------------------------------
class _BuscadorFake:
    def __init__(self, comparables: list[Comparable]):
        self._comparables = comparables
        self.llamado_con: list[str] = []

    def buscar_comparables(self, terminos: str) -> list[Comparable]:
        self.llamado_con.append(terminos)
        return list(self._comparables)


def _comps(precios: list[float]) -> list[Comparable]:
    return [Comparable(url=f"https://es.wallapop.com/item/x{i}", precio=p, titulo=f"item {i}")
            for i, p in enumerate(precios)]

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


# ============================================================================
# v2 -- `tasar()`: mediana de PARECIDOS, sin red, con el gate n>=5 y el
# conjunto inmutable (sin muestra suficiente -> None + motivo).
# ============================================================================


class TestTasar:
    def _producto(self):
        return {"marca": _diego("Reebok"), "tipo": _diego("sudadera"), "talla": _foto("XXL")}

    def test_mediana_rango_y_urls_con_comparables_suficientes(self):
        buscador = _BuscadorFake(_comps([7, 10, 22, 20, 9, 17.5, 4, 30]))
        t = tasar(self._producto(), buscador)
        assert isinstance(t, Tasacion)
        assert t.n == 8
        assert t.mediana == 13.75  # mediana de los 8
        assert t.minimo == 4.0 and t.maximo == 30.0
        assert t.tipo_match == "aproximado"  # SIEMPRE parecidos, nunca "el mismo"
        assert t.url_busqueda.startswith("https://")
        assert all(c.url.startswith("https://") for c in t.comparables)
        # Buscó por TEXTO (marca+tipo+talla), nunca por EAN.
        assert buscador.llamado_con == ["Reebok sudadera XXL"]

    def test_conjunto_inmutable_pocos_comparables_no_da_numero(self):
        buscador = _BuscadorFake(_comps([10, 12, 15]))  # 3 < 5
        t = tasar(self._producto(), buscador)
        assert t.mediana is None and t.minimo is None and t.maximo is None
        assert str(N_MINIMO_COMPARABLES) in t.motivo or "comparable" in t.motivo
        assert t.n == 3  # los enseña igual, pero sin mediana

    def test_cero_comparables_no_da_numero(self):
        t = tasar(self._producto(), _BuscadorFake([]))
        assert t.mediana is None
        assert t.n == 0

    def test_sin_identificativo_no_busca_ni_tasa(self):
        # Solo talla (modificador): no identifica el producto -> ni se llama al
        # buscador. Mismo criterio que `buscar()` v1.
        buscador = _BuscadorFake(_comps([10, 12, 15, 18, 20]))
        t = tasar({"talla": _foto("M")}, buscador)
        assert t.mediana is None
        assert t.terminos == ""
        assert buscador.llamado_con == []  # nunca tocó el buscador

    def test_nunca_usa_datos_inferidos_para_buscar(self):
        # marca inferida NO entra en la búsqueda (mismo filtro de procedencia
        # que v1): con solo una marca inferida no hay identificativo válido.
        buscador = _BuscadorFake(_comps([10, 12, 15, 18, 20, 25]))
        t = tasar({"marca": _inferido("Nike"), "talla": _foto("M")}, buscador)
        assert t.mediana is None
        assert buscador.llamado_con == []

    def test_ean_no_se_usa_para_la_busqueda_de_precio(self):
        # Aunque haya EAN, la tasación busca por TEXTO (nadie pone el EAN en
        # los anuncios -- decisión de Diego, seed fase-4).
        buscador = _BuscadorFake(_comps([10, 12, 15, 18, 20, 25]))
        prod = {"marca": _diego("Reebok"), "tipo": _diego("sudadera"),
                CAMPO_EAN: _foto("8445061029720")}
        tasar(prod, buscador)
        assert "8445061029720" not in buscador.llamado_con[0]
        assert buscador.llamado_con == ["Reebok sudadera"]

    def test_respeta_el_limite_de_comparables(self):
        muchos = _comps([float(i) for i in range(1, 40)])  # 39
        t = tasar(self._producto(), _BuscadorFake(muchos))
        assert t.n == LIMITE_COMPARABLES  # se queda con los primeros 15

    def test_motivo_dice_precio_pedido_no_de_venta(self):
        t = tasar(self._producto(), _BuscadorFake(_comps([10, 12, 15, 18, 20, 25])))
        assert "PIDEN" in t.motivo or "vend" in t.motivo.lower()

    def test_tasacion_es_frozen(self):
        t = tasar(self._producto(), _BuscadorFake(_comps([10, 12, 15, 18, 20])))
        with pytest.raises(Exception):
            t.mediana = 999  # type: ignore[misc]


class TestAtributosDesdeCampos:
    def _campos(self, **extra):
        base = {
            "marca": {"valor": "Reebok", "fuente": "diego", "confianza": "alta", "evidencia": None},
            "titulo": {"valor": "Sudadera Reebok gris talla XXL", "fuente": "inferido",
                       "confianza": "baja", "evidencia": None},
        }
        base.update(extra)
        return base

    def test_deriva_tipo_del_titulo_confirmado(self):
        from core.pricing import atributos_desde_campos
        attrs = atributos_desde_campos(self._campos())
        assert attrs["tipo"].valor == "sudadera"
        assert attrs["tipo"].fuente == "diego"
        assert attrs["marca"].valor == "Reebok"

    def test_no_usa_campos_inferidos(self):
        from core.pricing import atributos_desde_campos
        campos = self._campos(
            marca={"valor": "Nike", "fuente": "inferido", "confianza": "baja", "evidencia": None}
        )
        attrs = atributos_desde_campos(campos)
        assert "marca" not in attrs  # marca inferida NO entra

    def test_titulo_sin_tipo_reconocible_no_inventa_tipo(self):
        from core.pricing import atributos_desde_campos
        campos = self._campos(
            titulo={"valor": "Cosa rara sin nombre de prenda", "fuente": "inferido",
                    "confianza": "baja", "evidencia": None}
        )
        attrs = atributos_desde_campos(campos)
        assert "tipo" not in attrs

    def test_reconstruye_evidencia_de_campo_foto(self):
        from core.pricing import atributos_desde_campos
        campos = self._campos(
            talla={"valor": "XXL", "fuente": "foto", "confianza": "baja",
                   "evidencia": {"fichero": "IMG.jpg", "bbox": [1, 2, 3, 4]}}
        )
        attrs = atributos_desde_campos(campos)
        assert attrs["talla"].valor == "XXL"
        assert attrs["talla"].evidencia.fichero == "IMG.jpg"


class TestTasarHallazgosAudit:
    """`[listing-audit] 2026-07-17`: los 3 hallazgos del audit del precio."""

    def test_serio_tipo_generico_solo_no_tasa(self):
        # Sin marca ni modelo, solo un tipo genérico ("sudadera") -> la cohorte
        # sería el catálogo entero: NO se da número (truth-loop §D.2).
        from core.pricing import tasar
        buscador = _BuscadorFake(_comps([5, 8, 10, 12, 15, 20, 25]))
        t = tasar({"tipo": _diego("sudadera")}, buscador)
        assert t.mediana is None
        assert "cohorte" in t.motivo.lower() or "catálogo" in t.motivo.lower()
        assert buscador.llamado_con == []  # ni se molesta en buscar

    def test_con_marca_si_tasa(self):
        # Con marca (identificativo fuerte) sí se tasa.
        from core.pricing import tasar
        t = tasar({"marca": _diego("Reebok"), "tipo": _diego("sudadera")},
                  _BuscadorFake(_comps([5, 8, 10, 12, 15])))
        assert t.mediana is not None

    def test_serio_parsear_items_forma_inesperada_devuelve_vacio(self):
        from core.pricing import BuscadorWallapop
        b = BuscadorWallapop()
        for items in ("una cadena", None, [None, "x", 3], 42):
            datos = {"data": {"section": {"payload": {"items": items}}}}
            assert b._parsear(datos) == [], items  # nunca lanza, siempre []
        # estructura totalmente ausente
        assert b._parsear({}) == []
        assert b._parsear({"data": None}) == []

    def test_menor_bool_no_es_un_precio(self):
        from core.pricing import BuscadorWallapop
        assert BuscadorWallapop._precio(True) is None
        assert BuscadorWallapop._precio(False) is None
        assert BuscadorWallapop._precio({"amount": True}) is None
        assert BuscadorWallapop._precio(12) == 12.0


class TestVariantesDeBusqueda:
    """Idea de Diego (2026-07-18): varias combinaciones de palabras clave para
    triangular el precio -- cada una con identificativo FUERTE, de específica a
    amplia, sin duplicados."""

    def test_ropa_da_varias_combinaciones_ordenadas(self):
        from core.pricing import variantes_de_busqueda
        v = variantes_de_busqueda(
            {"marca": _diego("Reebok"), "tipo": _diego("sudadera"), "talla": _foto("XXL")}
        )
        assert v == ["Reebok sudadera XXL", "Reebok sudadera", "Reebok XXL", "Reebok"]

    def test_todos_los_atributos_dan_hasta_siete(self):
        from core.pricing import variantes_de_busqueda
        v = variantes_de_busqueda(
            {"marca": _diego("Nike"), "modelo": _foto("AB1"), "tipo": _diego("zapatillas"),
             "talla": _foto("42")}
        )
        assert len(v) == 7
        assert v[0] == "Nike AB1 zapatillas 42"  # la más específica primero
        assert v[-1] == "Nike"                    # la más amplia al final

    def test_cada_variante_tiene_identificativo_fuerte(self):
        from core.pricing import variantes_de_busqueda
        v = variantes_de_busqueda(
            {"marca": _diego("Reebok"), "tipo": _diego("sudadera"), "talla": _foto("XXL")}
        )
        assert all(("reebok" in t.lower()) for t in v)  # todas llevan la marca

    def test_solo_tipo_generico_no_da_variantes(self):
        from core.pricing import variantes_de_busqueda
        assert variantes_de_busqueda({"tipo": _diego("sudadera"), "talla": _foto("M")}) == []

    def test_marca_inferida_no_da_variantes(self):
        from core.pricing import variantes_de_busqueda
        assert variantes_de_busqueda({"marca": _inferido("Nike"), "tipo": _diego("sudadera")}) == []

    def test_tasar_variantes_una_tasacion_por_variante(self):
        from core.pricing import tasar_variantes
        prod = {"marca": _diego("Reebok"), "tipo": _diego("sudadera"), "talla": _foto("XXL")}
        buscador = _BuscadorFake(_comps([5, 8, 10, 12, 15]))
        tas = tasar_variantes(prod, buscador)
        assert len(tas) == 4  # una por variante
        assert all(t.mediana == 10.0 for t in tas)  # el fake devuelve lo mismo
        # cada variante buscó su propio término
        assert buscador.llamado_con == ["Reebok sudadera XXL", "Reebok sudadera", "Reebok XXL", "Reebok"]

    def test_tasar_variantes_vacio_si_no_hay_fuerte(self):
        from core.pricing import tasar_variantes
        assert tasar_variantes({"tipo": _diego("sudadera")}, _BuscadorFake(_comps([1, 2, 3, 4, 5]))) == []


class TestTerminosEditables:
    """Vía editable de la UI (idea de Diego 2026-07-18): sugerencias generosas
    (incluida marca inferida) que Diego edita, y una mediana por línea."""

    def test_sugiere_marca_inferida_para_el_box_real(self):
        from core.pricing import sugerir_terminos
        # Ficha REAL del masajeador: marca Lufthous es 'inferido' -> la búsqueda
        # estricta la excluía; como SUGERENCIA editable sí entra.
        campos = {
            "marca": {"valor": "Lufthous", "fuente": "inferido"},
            "modelo": {"valor": "LLLT-200", "fuente": "foto"},
            "titulo": {"valor": "Lufthous LLLT-200 blanco y gris", "fuente": "inferido"},
        }
        assert sugerir_terminos(campos) == ["Lufthous LLLT-200", "LLLT-200", "Lufthous"]

    def test_sin_marca_ni_modelo_cae_al_titulo(self):
        from core.pricing import sugerir_terminos
        campos = {"titulo": {"valor": "Sudadera gris chula", "fuente": "inferido"}}
        assert sugerir_terminos(campos) == ["Sudadera gris chula"]

    def test_ficha_vacia_no_sugiere_nada(self):
        from core.pricing import sugerir_terminos
        assert sugerir_terminos({}) == []

    def test_tasar_terminos_una_por_linea_no_vacia(self):
        from core.pricing import tasar_terminos
        buscador = _BuscadorFake(_comps([10, 12, 15, 18, 20]))
        tas = tasar_terminos(["Lufthous LLLT-200", "  ", "masajeador laser rodilla", ""], buscador)
        assert len(tas) == 2  # las vacías/espacios se saltan
        assert buscador.llamado_con == ["Lufthous LLLT-200", "masajeador laser rodilla"]
        assert all(t.mediana == 15.0 for t in tas)

    def test_tasar_terminos_respeta_lo_que_diego_escribe(self):
        # Un término que Diego añade a mano (no derivado del pipeline) se busca
        # TAL CUAL -- él controla las palabras y ve los comparables.
        from core.pricing import tasar_terminos
        buscador = _BuscadorFake(_comps([5, 8, 10, 12, 15, 20]))
        tasar_terminos(["masajeador laser rodilla Lufthous"], buscador)
        assert buscador.llamado_con == ["masajeador laser rodilla Lufthous"]
