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

import os
from pathlib import Path

import pytest
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


class _BuscadorFakeUI:
    """Doble de `pricing.Buscador` para la UI -> los tests del Export NUNCA
    tocan la red (el precio AUTO-BUSCA al abrir la pantalla, así que sin este
    doble cada test que renderiza el Export golpearía Wallapop)."""

    def __init__(self, *a, **k) -> None:
        pass

    def buscar_comparables(self, terminos: str):
        from core.pricing import Comparable

        return [
            Comparable(url=f"https://es.wallapop.com/item/x{i}", precio=float(10 + i), titulo=f"t{i}")
            for i in range(6)
        ]


@pytest.fixture(autouse=True)
def _sin_red_en_precio(monkeypatch):
    """El precio auto-busca al abrir «4. Export» -> TODO test que renderice esa
    pantalla usaría la red. Se falsea el buscador para toda la suite de UI."""
    from core import pricing

    monkeypatch.setattr(pricing, "BuscadorWallapop", _BuscadorFakeUI)


@pytest.fixture(autouse=True)
def _sin_apertura_de_carpeta_real(monkeypatch) -> list[str]:
    """"Preparar fotos" abre la carpeta con `os.startfile` (Windows) tras
    copiar -- NINGÚN test debe abrir una ventana de Explorer real (estamos en
    win32, así que `os.startfile` existe de verdad aquí). Se sustituye por un
    registro en memoria, autouse para TODA la suite (incluidos los tests que
    ya pulsaban "Preparar fotos" antes de esta feature); el test dedicado
    comprueba las llamadas registradas."""
    llamadas: list[str] = []
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda ruta: llamadas.append(str(ruta)))
    return llamadas


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
# 4. El botón "Preparar fotos" copia de verdad a disco, renombradas, Y ABRE
#    la carpeta (`os.startfile`, guardado con `hasattr` -- Windows-only).
# ============================================================================
def test_boton_preparar_fotos_crea_carpeta_con_fotos_renombradas(
    tmp_path, _sin_apertura_de_carpeta_real
):
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

    # "Abrir la carpeta" -- guardado con `hasattr(os, "startfile")`: en esta
    # máquina (win32) existe de verdad, así que el fixture autouse lo
    # sustituyó por un registro; comprobamos que SE LLAMÓ con la ruta
    # correcta (no sólo que el código "compila" -- `change-loop.md` §C4: un
    # botón se prueba PULSÁNDOLO, no sólo renderizándolo).
    if hasattr(os, "startfile"):
        assert str(directorio) in _sin_apertura_de_carpeta_real, _sin_apertura_de_carpeta_real


# ============================================================================
# 5. El botón "Buscar comparables" se EJECUTA (el clic, no sólo el render):
#    cazaría un `AttributeError` como el de `pricing._NOTA_PRECIO_PEDIDO`
#    renombrado, que ni ruff ni el arranque veían (`[INC-006]`/`[INC-021]`:
#    un flujo que cuesta un clic arrancar no está probado hasta que alguien
#    paga ese clic). Con buscador falso -> sin red.
# ============================================================================
def test_precio_auto_busca_al_abrir_y_pinta_medianas(tmp_path):
    # El precio AUTO-BUSCA al abrir «4. Export» (menos clics, idea de Diego):
    # la mediana sale SIN pulsar nada. Esto ejecuta `_render_precio` entero,
    # incluida `pricing.NOTA_PRECIO_PEDIDO` -> cazaría el `[INC-028]`.
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception, at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "PIDEN" in captions  # la nota honesta se pintó
    assert at.metric  # ya hay al menos una mediana, sin clic


def test_boton_re_buscar_no_peta(tmp_path):
    # El botón «Buscar de nuevo» re-ejecuta con las palabras editadas.
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    boton = next(b for b in at.button if b.key == f"btn_precio_{pid}_wallapop")
    at = boton.click().run()
    assert not at.exception, at.exception


# ============================================================================
# 6. Referencia -- "Ref. N" GARANTIZADA (asignada al abrir Export) e
#    inyectada en la descripción de AMBAS plataformas (requisito FIJO de
#    Diego, Fase 5 FINANZAS).
# ============================================================================
def test_referencia_se_asigna_y_se_inyecta_en_ambas_descripciones(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception, at.exception

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    producto = next(p for p in estado["productos"] if p["id"] == pid)
    referencia = producto["referencia"]
    assert referencia is not None  # el export la GARANTIZA (asignar_referencia)

    marca = f"Ref. {referencia}"
    codigos = [c.value for c in at.code]
    # `st.tabs` renderiza el contenido de las DOS pestañas en el mismo run
    # (igual que el test #2 con el estado traducido) -> ambas descripciones
    # (Wallapop y Vinted) deben llevar la marca.
    coincidencias = [c for c in codigos if c.rstrip().endswith(marca)]
    assert len(coincidencias) >= 2, codigos


# ============================================================================
# 7. "Subido" -- registra la publicación (precio + tasación) EN DISCO, se
#    relee del disco (no de `session_state`) y es idempotente.
# ============================================================================
def test_boton_subido_registra_publicacion_con_precio_y_tasacion(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception, at.exception

    boton = next(b for b in at.button if b.key == f"export_subido_{pid}_wallapop")
    at = boton.click().run()
    assert not at.exception, at.exception

    # El badge se pinta en el MISMO run -- se relee del disco, no de
    # `session_state` (`decision-making.md` §19).
    assert any("Subido a Wallapop" in s.value for s in at.success)

    store = LoteStore(data_dir=tmp_path)
    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == pid)
    pub = next(p for p in fila["publicaciones"] if p["plataforma"] == "wallapop")
    assert pub["subido_en"]
    assert pub["precio_elegido_cents"] == 1250  # mediana 12.5 € del buscador falso
    assert pub["tasacion"] is not None
    assert pub["tasacion"]["mediana"] == 12.5

    # Idempotente: pulsar otra vez NO duplica la fila ni pierde el snapshot.
    boton_2 = next(b for b in at.button if b.key == f"export_subido_{pid}_wallapop")
    at2 = boton_2.click().run()
    assert not at2.exception, at2.exception
    ventas2 = LoteStore(data_dir=tmp_path).cargar_ventas()
    fila2 = next(f for f in ventas2 if f["producto_id"] == pid)
    pubs_wallapop = [p for p in fila2["publicaciones"] if p["plataforma"] == "wallapop"]
    assert len(pubs_wallapop) == 1


def test_sin_pulsar_subido_no_hay_publicacion_registrada(tmp_path):
    lote_id, pid = _preparar(
        tmp_path, extraccion=_ficha_confirmada_limpia(), confirmar_ficha=True
    )
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception, at.exception
    assert any("Aún no marcado como subido" in c.value for c in at.caption)
    store = LoteStore(data_dir=tmp_path)
    assert store.cargar_ventas() == []
