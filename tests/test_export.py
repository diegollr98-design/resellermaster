"""Tests de `core/export.py` -- EL EXPORT (superficie `atributos`/`persistencia`
en el sentido de `truth-loop.md` SS B: lo que aquí se produce es lo que Diego
pega tal cual en Wallapop/Vinted, así que un bug aquí publica una mentira o
una ficha sin confirmar).

La distinción que se prueba en cada test, explícita (`decision-making.md`
SS12): **BLOQUEA** (algo publicaría una mentira / algo que Diego no vio) vs
**AVISA** (algo sólo falta, Diego lo elige en la plataforma en 2 s, nunca se
inventa).

El caso de FALLO es obligatorio, no sólo el bueno (`decision-making.md`
SS16): "ficha sin confirmar" y "texto sucio" tienen que BLOQUEAR de verdad
(se comprueba que salta `ExportBloqueadoError`, no que una función devuelva
`False` en silencio).

Las fichas de prueba se construyen a mano con la MISMA forma que
`ui/ficha.py::_construir_confirmado` deja en `producto["campos"]`
(raíz: `campos`/`coste_usd`/`fallos`/`aviso_coherencia`/`confirmada`), para
no desincronizarse del contrato real sin depender de Streamlit ni del VLM.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.export import (
    CampoExportado,
    ExportBloqueadoError,
    PayloadPlataforma,
    construir_payload,
)

_TITULO_VALIDO = "Sudadera gris talla M, apenas usada"
_DESCRIPCION_VALIDA = (
    "Sudadera en buen estado, apenas usada, sin manchas ni roturas visibles."
)


def _campo(valor: Any) -> dict:
    return {
        "valor": valor,
        "fuente": "diego",
        "confianza": "alta" if valor is not None else "baja",
        "evidencia": None,
        "propuesta": None,
    }


def _campos_confirmados(**overrides: Any) -> dict[str, dict]:
    base: dict[str, Any] = {
        "marca": None,
        "talla": None,
        "modelo": None,
        "ean": None,
        "composicion": None,
        "medidas": None,
        "color": None,
        "estado": None,
        "desperfectos": None,
        "titulo": _TITULO_VALIDO,
        "descripcion": _DESCRIPCION_VALIDA,
        "categoria": "moda",
    }
    base.update(overrides)
    return {nombre: _campo(valor) for nombre, valor in base.items()}


def _producto(
    *,
    confirmada: bool = True,
    fotos: list[str] | None = None,
    aviso_coherencia: str | None = None,
    **overrides: Any,
) -> dict:
    return {
        "id": "p1",
        "lote_id": "l1",
        "campos": {
            "campos": _campos_confirmados(**overrides),
            "coste_usd": 0.0,
            "fallos": [],
            "aviso_coherencia": aviso_coherencia,
            "confirmada": confirmada,
        },
        "confirmado": True,  # agrupación (Fase 1); NO es lo que valida el export
        "fotos": fotos if fotos is not None else ["f1"],
    }


_FOTOS_POR_ID = {"f1": {"ruta": "foto1.jpg"}}


# ============================================================================
# BLOQUEOS -- el caso de fallo, con dientes
# ============================================================================


def test_ficha_sin_confirmar_bloquea():
    """Sin `confirmada=True` no hay export -- es lo que sostiene la premisa
    de `truth-loop.md` SS A.2: un valor 'mejor intento' sólo es legítimo
    porque Diego lo vio. `producto["confirmado"]` (agrupación) no basta."""
    producto = _producto(confirmada=False)
    with pytest.raises(ExportBloqueadoError) as excinfo:
        construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert excinfo.value.violaciones
    assert any("confirmad" in v.lower() for v in excinfo.value.violaciones)


def test_campos_ausente_del_todo_bloquea():
    """`producto["campos"]` ni siquiera es un dict (nunca se extrajo nada
    todavía) -- debe bloquear igual que "sin confirmar", nunca reventar con
    un `AttributeError`/`KeyError` sin clasificar."""
    producto = {"id": "p1", "lote_id": "l1", "campos": {}, "fotos": ["f1"]}
    with pytest.raises(ExportBloqueadoError):
        construir_payload(producto, _FOTOS_POR_ID, "vinted")


def test_marca_ajena_en_descripcion_bloquea():
    """Mencionar una marca distinta a la seleccionada OCULTA el anuncio en
    Vinted (product.md SS7) -- BLOQUEA, no avisa."""
    producto = _producto(
        marca="Adidas",
        descripcion="Muy parecida a las Nike, en buen estado y sin roturas.",
    )
    with pytest.raises(ExportBloqueadoError) as excinfo:
        construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert any("descripci" in v.lower() for v in excinfo.value.violaciones)


def test_email_en_descripcion_bloquea():
    producto = _producto(descripcion="Contacta conmigo en diego@ejemplo.com para más info.")
    with pytest.raises(ExportBloqueadoError) as excinfo:
        construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert excinfo.value.violaciones


def test_enlace_en_descripcion_bloquea():
    producto = _producto(descripcion="Más fotos en https://ejemplo.com/producto, mira el enlace.")
    with pytest.raises(ExportBloqueadoError):
        construir_payload(producto, _FOTOS_POR_ID, "wallapop")


def test_categoria_ausente_bloquea():
    producto = _producto(categoria=None)
    with pytest.raises(ExportBloqueadoError) as excinfo:
        construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert any("categor" in v.lower() for v in excinfo.value.violaciones)


def test_categoria_invalida_bloquea():
    producto = _producto(categoria="ropa-de-diego")  # no está en schema.CATEGORIAS
    with pytest.raises(ExportBloqueadoError):
        construir_payload(producto, _FOTOS_POR_ID, "vinted")


def test_plataforma_desconocida_lanza_value_error():
    producto = _producto()
    with pytest.raises(ValueError):
        construir_payload(producto, _FOTOS_POR_ID, "ebay")


# ============================================================================
# AVISOS -- lo que falta se enseña, nunca se inventa
# ============================================================================


def test_estado_sin_elegir_no_bloquea_y_no_adivina():
    producto = _producto(estado=None)
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    assert isinstance(payload, PayloadPlataforma)
    campo_estado = next(c for c in payload.campos if c.nombre == "estado")
    assert campo_estado.valor is None
    assert campo_estado.traducido is False
    assert any("estado" in a.lower() for a in payload.avisos)


def test_talla_ausente_no_bloquea_solo_avisa():
    producto = _producto(talla=None)
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo_talla = next(c for c in payload.campos if c.nombre == "talla")
    assert campo_talla.valor is None
    assert any("talla" in a.lower() for a in payload.avisos)


def test_avisos_incluyen_precio_y_categoria_hoja():
    """El precio NUNCA sale de aquí (costura 2) y la categoría hoja real de
    cada plataforma tampoco la produce el pipeline -- ambos son avisos, no
    valores inventados."""
    producto = _producto()
    payload_vinted = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert any("price" in a for a in payload_vinted.avisos)
    assert any("catalog_id" in a for a in payload_vinted.avisos)

    payload_wallapop = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    assert any("precio" in a for a in payload_wallapop.avisos)
    assert any("categoria" in a for a in payload_wallapop.avisos)


# ============================================================================
# Traducciones exactas -- la única tabla verificada (estado)
# ============================================================================


def test_estado_bueno_moda_literales_exactos():
    producto = _producto(estado="Bueno", categoria="moda")

    payload_wallapop = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    campo_w = next(c for c in payload_wallapop.campos if c.nombre == "estado")
    assert campo_w.valor == "Buen estado"  # NO "Bueno" (product.md)
    assert campo_w.traducido is True

    payload_vinted = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo_v = next(c for c in payload_vinted.campos if c.nombre == "estado")
    assert campo_v.valor == "Bueno"
    assert campo_v.traducido is True


def test_estado_para_reparar_electronica_vinted():
    """`PARA_REPARAR` sólo tiene un literal propio ('Necesita reparación')
    en Vinted cuando la categoría es electrónica -- fuera de ahí baja al
    literal más bajo de la escala general (sesgo oficial de Vinted). Es
    FIEL (literal propio, no compartido con un nivel funcional): sigue
    `traducido=True`, SIN nota -- si se marcase igual que el caso infiel, el
    aviso se volvería ruido y entrenaría a ignorarlo."""
    producto = _producto(estado="Para reparar", categoria="electronica")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "estado")
    assert campo.valor == "Necesita reparación"
    assert campo.traducido is True
    assert campo.nota is None


# ============================================================================
# `[listing-audit] BLOQUEANTE, 2026-07-17` -- la sudadera ROTA. El literal
# más bajo disponible puede ser CORRECTO y AUN ASÍ comunicar "funciona"
# cuando comparte literal con un nivel funcional -- eso NO puede salir con
# `traducido=True` (la insignia de "no re-decidas") ni en silencio.
# ============================================================================


def test_para_reparar_moda_wallapop_no_es_traducido_y_avisa():
    producto = _producto(
        estado="Para reparar",
        categoria="moda",
        desperfectos="ROTO: agujero de 5 cm en la manga izquierda",
    )
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    campo = next(c for c in payload.campos if c.nombre == "estado")
    # El literal sigue siendo el correcto (el más bajo disponible) -- lo que
    # cambia es que NO se marca como "pégalo sin re-decidir".
    assert campo.valor == "En condiciones aceptables"
    assert campo.traducido is False
    assert campo.nota is not None
    assert "NO FUNCIONAL" in campo.nota
    assert any("estado" in a.lower() and "no funcional" in a.lower() for a in payload.avisos)


def test_para_reparar_moda_vinted_no_es_traducido_y_avisa():
    producto = _producto(estado="Para reparar", categoria="moda")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "estado")
    assert campo.valor == "Satisfactorio"
    assert campo.traducido is False
    assert campo.nota is not None


def test_para_reparar_otros_wallapop_si_es_traducido():
    """`_WALLAPOP_RESTO` SÍ tiene un literal propio ('Lo ha dado todo') --
    fiel, no se marca."""
    producto = _producto(estado="Para reparar", categoria="otros")
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    campo = next(c for c in payload.campos if c.nombre == "estado")
    assert campo.valor == "Lo ha dado todo"
    assert campo.traducido is True
    assert campo.nota is None


def test_precintado_y_muy_bueno_siguen_traducidos():
    """Presentan el producto igual o PEOR que el real (ambigüedad de
    vocabulario de la plataforma, no una mentira) -- `traducido` no baja."""
    producto_muy_bueno = _producto(estado="Muy bueno", categoria="moda")
    payload = construir_payload(producto_muy_bueno, _FOTOS_POR_ID, "wallapop")
    campo = next(c for c in payload.campos if c.nombre == "estado")
    assert campo.valor == "Buen estado"
    assert campo.traducido is True
    assert campo.nota is None
    # PRECINTADO no es alcanzable desde la UI (`ESTADO_UI_A_CANONICO`) --
    # se prueba directamente contra `schema.fidelidad_estado` en
    # `tests/test_schema.py::TestFidelidadEstado`.


# ============================================================================
# `desperfectos` -- el campo que evita la devolución, ya no se pierde.
# ============================================================================


def test_desperfectos_confirmado_sale_en_ambas_plataformas_y_avisa():
    texto = "ROTO: agujero de 5 cm en la manga izquierda"
    producto = _producto(desperfectos=texto)

    for plataforma in ("wallapop", "vinted"):
        payload = construir_payload(producto, _FOTOS_POR_ID, plataforma)
        campo = next(c for c in payload.campos if c.nombre == "desperfectos")
        assert campo.valor == texto
        assert campo.traducido is False
        assert any("desperfecto" in a.lower() for a in payload.avisos)


def test_desperfectos_ausente_no_emite_campo():
    producto = _producto(desperfectos=None)
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    assert not any(c.nombre == "desperfectos" for c in payload.campos)


# ============================================================================
# `aviso_coherencia` (`[INC-011]`, la ficha Frankenstein) -- se propaga,
# arriba del todo, textual. El fixture anterior lo hardcodeaba a `None`: ese
# camino no se ejercía nunca.
# ============================================================================


def test_aviso_coherencia_se_propaga_arriba_del_todo():
    producto = _producto(aviso_coherencia="marca y talla vienen de fotos disjuntas")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert payload.avisos, "el aviso de coherencia debe estar presente"
    assert "fotos disjuntas" in payload.avisos[0]
    assert "FRANKENSTEIN" in payload.avisos[0]


# ============================================================================
# El filtro de marca ajena es best-effort -- se avisa SIEMPRE, nunca en
# silencio (medido: 4/5 marcas reales del golden set no lo disparan).
# ============================================================================


def test_aviso_de_filtro_de_marca_best_effort_siempre_presente():
    producto = _producto(marca="Lufthous")  # marca real del golden set, no en la heurística
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert any("best-effort" in a.lower() for a in payload.avisos)


# ============================================================================
# Composición: no existe en moda de Wallapop
# ============================================================================


def test_moda_wallapop_no_emite_composicion():
    producto = _producto(categoria="moda", composicion="algodón")
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    assert not any(c.nombre == "composicion" for c in payload.campos)


def test_vinted_si_emite_composicion_sin_traducir():
    producto = _producto(categoria="moda", composicion="algodón")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "composicion")
    assert campo.valor == "algodón"
    assert campo.traducido is False
    assert campo.nota is not None


def test_vinted_composicion_ausente_del_todo_degrada_a_null_sin_petar():
    """Diego quitó "composicion" de la ficha (2026-07-17): `core/extract.py`
    ya NO produce esa clave -- la ficha confirmada real ya no trae
    "composicion" en `campos` en absoluto (ni siquiera con `valor=None`).
    El export debe seguir sin reventar y mostrar el campo vacío (Diego lo
    rellena a mano si vende un mueble), nunca un `KeyError`."""
    campos = _campos_confirmados(categoria="hogar")
    del campos["composicion"]
    producto = _producto(categoria="hogar")
    producto["campos"]["campos"] = campos
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "composicion")
    assert campo.valor is None


# ============================================================================
# Marca
# ============================================================================


def test_marca_ausente_vinted_sin_marca():
    producto = _producto(marca=None)
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "marca")
    assert campo.valor == "Sin marca"


def test_marca_presente_vinted_pasa_tal_cual():
    producto = _producto(marca="Lufthous")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    campo = next(c for c in payload.campos if c.nombre == "marca")
    assert campo.valor == "Lufthous"


# ============================================================================
# Talla: Vinted NO implementada a propósito (mapear_talla_a_vinted no se
# llama nunca desde aquí -- si se llamase, lanzaría NotImplementedError).
# ============================================================================


def test_talla_vinted_no_traducida_y_no_revienta():
    producto = _producto(talla="M")
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")  # no debe lanzar NotImplementedError
    campo = next(c for c in payload.campos if c.nombre == "talla")
    assert campo.valor == "M"
    assert campo.traducido is False
    assert campo.nota is not None


def test_talla_wallapop_sale_cruda_y_NO_se_marca_como_traducida():
    # `product.md` implicacion #2: Wallapop usa un string COMBINADO
    # ("XS / 34 / 6") y marca la tabla de mapeo como obligatoria — tabla que
    # no existe. Emitir el valor crudo es correcto; marcarlo `traducido=True`
    # seria decirle a Diego "no re-decidas" sobre algo que nadie ha traducido.
    # Es la misma honestidad que ya se aplica a la talla de Vinted: mismo dato
    # crudo, misma senal. (Este test asertaba `is True` antes de `[INC-017]`.)
    producto = _producto(talla="XS / 34 / 6")
    payload = construir_payload(producto, _FOTOS_POR_ID, "wallapop")
    campo = next(c for c in payload.campos if c.nombre == "talla")
    assert campo.valor == "XS / 34 / 6"
    assert campo.traducido is False
    assert campo.nota is not None


# ============================================================================
# Fotos: orden + límite por plataforma (core/images.py)
# ============================================================================


def _fotos_25() -> tuple[list[str], dict[str, dict]]:
    ids = [f"foto_{i:02d}" for i in range(1, 26)]
    por_id = {fid: {"ruta": f"{fid}.jpg"} for fid in ids}
    return ids, por_id


def test_fotos_wallapop_10_mas_15_excluidas():
    ids, por_id = _fotos_25()
    producto = _producto(fotos=ids)
    payload = construir_payload(producto, por_id, "wallapop")
    assert len(payload.fotos) == 10
    assert len(payload.fotos_excluidas) == 15
    assert any("por encima del límite" in a for a in payload.avisos)


def test_fotos_vinted_20_mas_5_excluidas():
    ids, por_id = _fotos_25()
    producto = _producto(fotos=ids)
    payload = construir_payload(producto, por_id, "vinted")
    assert len(payload.fotos) == 20
    assert len(payload.fotos_excluidas) == 5


def test_foto_referenciada_sin_entrada_en_fotos_por_id_falla_ruidosa():
    """Nunca un fallback silencioso (`decision-making.md` SS13): un id de
    foto del producto que no está en `fotos_por_id` es un bug de quien
    llama, y debe fallar de forma ruidosa, no perder la foto en silencio."""
    producto = _producto(fotos=["f1", "no-existe"])
    with pytest.raises(ValueError):
        construir_payload(producto, _FOTOS_POR_ID, "vinted")


# ============================================================================
# Título/descripción se pasan tal cual (no se recomponen aquí)
# ============================================================================


def test_titulo_y_descripcion_pasan_al_payload():
    producto = _producto()
    payload = construir_payload(producto, _FOTOS_POR_ID, "vinted")
    assert payload.titulo == _TITULO_VALIDO
    assert payload.descripcion == _DESCRIPCION_VALIDA


def test_campo_exportado_es_frozen():
    c = CampoExportado(nombre="x", etiqueta="x", valor="v", traducido=True)
    with pytest.raises(Exception):
        c.valor = "otro"  # type: ignore[misc]
