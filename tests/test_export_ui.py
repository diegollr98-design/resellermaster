"""Tests de `ui/export.py` — EL EXPORT (superficies `atributos`+`persistencia`;
pantalla que Diego TOCA CON LAS MANOS -> `AppTest` obligatorio, `[INC-006]`,
`change-loop.md` §C4).

Qué se prueba, y por qué así:
- La defensa con dientes de `truth-loop.md` §A.2: sin ficha CONFIRMADA no
  hay payload, ni botón que lo salte — sólo el warning con el motivo real.
- El estado ya viene TRADUCIDO al literal exacto de cada plataforma desde
  el MISMO estado confirmado ("Bueno" -> "Buen estado" en Wallapop moda,
  "Bueno" en Vinted) — `core/schema.py::mapear_estado_*`.
- Una descripción que menciona una marca ajena a la seleccionada bloquea el
  export entero (`core/export.py::ExportBloqueadoError`) — no se pinta nada
  de esa plataforma, sólo el error con el motivo.
- El botón "Preparar fotos" copia de verdad a disco vía
  `core.images.exportar_producto`, en `data/exports/<lote_id>/<plataforma>/`.

La ficha confirmada se construye a mano con el mismo formato que
`ui/ficha.py::_construir_confirmado` persiste (valor/fuente/confianza por
campo + `confirmada=True`), guardada directo vía `store.confirmar_ficha` —
no hace falta pasar por el diálogo de extracción/VLM para probar el export.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.store import Foto, LoteStore

# ============================================================================
# Fixtures de ficha confirmada.
# ============================================================================


def _campo(valor, fuente: str = "diego", confianza: str = "alta") -> dict:
    return {"valor": valor, "fuente": fuente, "confianza": confianza}


def _ficha_confirmada_limpia() -> dict:
    return {
        "campos": {
            "categoria": _campo("moda"),
            "marca": _campo("Nike"),
            "talla": _campo("M"),
            "estado": _campo("Bueno"),
            "titulo": _campo("Camiseta Nike deportiva talla M"),
            "descripcion": _campo(
                "Camiseta Nike en buen estado, apenas usada, talla M, "
                "comoda y resistente para entrenar."
            ),
        },
        "confirmada": True,
    }


def _ficha_marca_ajena() -> dict:
    ficha = _ficha_confirmada_limpia()
    ficha["campos"] = dict(ficha["campos"])
    ficha["campos"]["descripcion"] = _campo(
        "Camiseta Nike, un poco parecida a Adidas en el corte y muy comoda."
    )
    return ficha


def _extraccion_sin_confirmar() -> dict:
    """Simula el estado tras `guardar_extraccion` (Fase 3 de `ui/ficha.py`):
    hay `campos`, pero NUNCA se llamó a `confirmar_ficha` -> sin
    `confirmada=True`."""
    return {"campos": {"marca": _campo("Nike", fuente="inferido", confianza="baja")}}


# ============================================================================
# Setup: lote + 1 foto real + 1 producto con la AGRUPACIÓN confirmada.
# ============================================================================


def _crear_img(ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), (120, 60, 60)).save(ruta, format="JPEG")


def _preparar(
    tmp_path: Path,
    *,
    extraccion: dict | None,
    confirmar_ficha: bool,
) -> tuple[str, str]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote export", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / "IMG_1.jpg"
    _crear_img(ruta)
    (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash="hash_1")])
    (producto_id,) = store.guardar_agrupacion(lote_id, [[foto_id]])
    store.confirmar_producto(producto_id)  # Fase 1: agrupación confirmada
    if extraccion is not None:
        if confirmar_ficha:
            store.confirmar_ficha(producto_id, extraccion)
        else:
            store.guardar_extraccion(producto_id, extraccion)
    return lote_id, producto_id


def _script(data_dir: str, lote_id: str) -> None:
    from pathlib import Path as _Path

    from core.store import LoteStore as _LoteStore
    from ui import export as _export

    _export.render(_LoteStore(data_dir=_Path(data_dir)), lote_id)


# ============================================================================
# 1. Sin ficha confirmada -> ni payload ni botón de saltárselo.
# ============================================================================
def test_ficha_sin_confirmar_no_pinta_payload_ni_hay_boton_de_exportar(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_extraccion_sin_confirmar(), confirmar_ficha=False
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "Confirma la ficha" in textos_warning
    assert "SIN CONFIRMAR" in textos_warning

    # Nada de payload: ni título/descripción/campos (todos van por st.code),
    # ni el botón de preparar fotos (la ÚNICA acción de esta pantalla).
    assert not list(at.code)
    assert not any((b.key or "").startswith("export_fotos_") for b in at.button)


# ============================================================================
# 2. Ficha confirmada -> título, descripción y estado YA TRADUCIDO.
# ============================================================================
def test_ficha_confirmada_pinta_titulo_descripcion_y_estado_traducido(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    # `at.warning` sí puede tener contenido (campos obligatorios que el
    # pipeline nunca produce -- precio, ubicación...): eso es un AVISO, no un
    # bloqueo. Lo que importa aquí es que el payload SÍ se pintó.

    codigos = [c.value for c in at.code]
    assert any("Camiseta Nike deportiva" in c for c in codigos), codigos
    assert any("apenas usada" in c for c in codigos), codigos
    # El MISMO estado confirmado ("Bueno") traducido a los DOS literales
    # exactos, uno por plataforma, ambos presentes porque `st.tabs` renderiza
    # el contenido de las dos pestañas en el mismo script run.
    assert "Buen estado" in codigos  # Wallapop, categoría moda
    assert "Bueno" in codigos  # Vinted, escala general


# ============================================================================
# 3. Marca ajena en la descripción -> bloqueo total, nada se pinta.
# ============================================================================
def test_descripcion_con_marca_ajena_bloquea_el_export(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_marca_ajena(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    assert at.error
    textos_error = " ".join(e.value for e in at.error).lower()
    assert "adidas" in textos_error

    # Ningún literal de estado ni el título deberían llegar a pintarse: el
    # bloqueo corta ANTES de construir ningún campo.
    codigos = [c.value for c in at.code]
    assert "Buen estado" not in codigos
    assert "Bueno" not in codigos
    assert not any("Camiseta Nike" in c for c in codigos)


# ============================================================================
# 4. El botón "Preparar fotos" copia de verdad a disco, renombradas.
# ============================================================================
def test_boton_preparar_fotos_crea_carpeta_con_fotos_renombradas(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton = next(b for b in at.button if b.key == f"export_fotos_{pid}_wallapop")
    at = boton.click().run()
    assert not at.exception

    directorio = tmp_path / "exports" / lote_id / "wallapop" / pid[:8]
    assert directorio.is_dir()
    archivos = sorted(directorio.glob("*.jpg"))
    assert len(archivos) == 1
    assert archivos[0].name.startswith(pid[:8])

    # La ruta preparada se enseña con su propio st.code (copiable) tras el
    # rerun que dispara el propio botón.
    codigos = [c.value for c in at.code]
    assert any(str(directorio) == c for c in codigos), codigos
    assert at.success
