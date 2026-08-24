"""core/finanzas.py — FASE 5 FINANZAS: agregados + export a Excel.

Superficie sensible de PERSISTENCIA (`truth-loop.md` §B): los importes son
dinero. Este módulo NO decide nada de negocio (eso ya lo cerró `core/store.py`
con sus snapshots congelados) — sólo LEE `store.cargar_ventas()` y (a) agrega
en euros para el dashboard, (b) vuelca esas mismas filas a un `.xlsx`. Cero
lógica de Streamlit aquí (`.claude/rules/file-organization.md`).

## Céntimos enteros dentro, euros fuera
Todo importe que entra a este módulo (`coste_cents`, `precio_final_cents`,
`beneficio_bruto_cents`) es un ENTERO de céntimos — nunca un float. La única
conversión a euros ocurre al pintar/exportar (`cents_a_euros`), y siempre
como texto formateado, no como un float que vuelva a redondearse aguas abajo.

## El .xlsx es un INFORME, no la verdad
SQLite (`core/store.py`) es la única fuente de verdad. `exportar_excel`
escribe una FOTO en el momento de pulsar el botón — nombrada con la fecha
(`finanzas_YYYY-MM-DD.xlsx`) para que Diego sepa cuándo se generó. Editar ese
fichero a mano no cambia nada en la base de datos: el próximo export lo
sobreescribe. Esto se dice explícitamente en la UI (`ui/finanzas.py`), no
sólo aquí.

## 'devuelta' no suma beneficio
`store.cargar_ventas()` YA calcula `beneficio_bruto_cents=None` para
cualquier venta que no esté en estado `'vendida'` (ver su docstring). Los
agregados de aquí simplemente suman lo que no es `None` — así que una
devolución nunca infla el beneficio total sin que este módulo tenga que
conocer el concepto de "estado" explícitamente (single source of truth: el
store ya decidió qué cuenta).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook

# --------------------------------------------------------------------------
# Céntimos -> euros, siempre como texto (nunca un float que se re-redondee).
# --------------------------------------------------------------------------


def cents_a_euros(cents: int | None) -> str:
    """`None` -> `""` (importe no informado/no aplicable, nunca "0.00" que
    parecería un dato real). Un entero de céntimos -> texto con 2 decimales,
    p.ej. `1999` -> `"19.99"`."""
    if cents is None:
        return ""
    return f"{cents / 100:.2f}"


def euros_texto_a_cents(texto: str) -> int:
    """El sentido inverso, para la UI: lo que Diego teclea en un `text_input`
    de euros -> céntimos enteros (redondeo estándar, nunca trunca). Acepta
    coma o punto decimal (`"19,99"` o `"19.99"`).

    `ValueError` con un mensaje claro si el texto no es un número — para que
    quien llama (la UI) lo capture y lo muestre con `st.error`, nunca un
    traceback (`decision-making.md` §13).
    """
    limpio = texto.strip().replace(",", ".")
    if not limpio:
        raise ValueError("el precio de venta está vacío")
    valor = float(limpio)  # ValueError propio de Python si no es un número
    return round(valor * 100)


# --------------------------------------------------------------------------
# Agregados — puros, sin Streamlit, fáciles de testear con listas a mano.
# --------------------------------------------------------------------------


def total_vendido_cents(filas: list[dict[str, Any]]) -> int:
    """Suma `precio_final_cents` de las ventas en estado `'vendida'` — NUNCA
    las `'devuelta'` (un artículo devuelto no se quedó vendido) ni los
    productos sin venta (`venta is None`)."""
    total = 0
    for fila in filas:
        venta = fila.get("venta")
        if not venta or venta.get("estado") != "vendida":
            continue
        precio = venta.get("precio_final_cents")
        if precio is not None:
            total += precio
    return total


def beneficio_total_cents(filas: list[dict[str, Any]]) -> int:
    """Suma `beneficio_bruto_cents` de las filas que lo tengan. El store ya
    deja ese campo en `None` para todo lo que no sea una venta `'vendida'`
    (`truth-loop.md` §B, `core/store.py::cargar_ventas`) — sumar sólo lo que
    no es `None` basta para que una `'devuelta'` no cuente."""
    return sum(
        fila["beneficio_bruto_cents"]
        for fila in filas
        if fila.get("beneficio_bruto_cents") is not None
    )


# --------------------------------------------------------------------------
# Export a Excel — un INFORME, no la verdad (ver docstring del módulo).
# --------------------------------------------------------------------------

_COLUMNAS: tuple[str, ...] = (
    "Referencia",
    "Título",
    "Lote",
    "Coste (€)",
    "Subido",
    "Vendido",
    "Precio venta (€)",
    "Plataforma venta",
    "Fecha venta",
    "Beneficio bruto (€)",
)


def _texto_subido(fila: dict[str, Any]) -> str:
    publicaciones = fila.get("publicaciones") or []
    if not publicaciones:
        return ""
    return "; ".join(
        f"{p.get('plataforma')} ({p.get('subido_en') or '?'})" for p in publicaciones
    )


# Excel/LibreOffice interpretan como FORMULA cualquier celda de texto que
# empiece por = + - @ (o por un control de tabulacion/retorno). El titulo de
# una ficha es texto libre, asi que un titulo que empiece por "=" se ejecuta al
# abrir el .xlsx en vez de leerse. Es la clase "CSV/Excel injection", y aqui no
# es teorica: `Titulo` sale de la sintesis y de lo que Diego teclea.
#
# Riesgo real bajo -- es un fichero que genera Diego con sus propios datos y
# que abre el -- pero el repo YA tiene un sanitizador con dientes para el texto
# que va a las plataformas (`schema.validar_texto`); que la hoja de calculo no
# tuviera ninguno era una incoherencia, no una decision. Se antepone una
# comilla simple, que es la forma estandar: Excel la consume al mostrar, asi
# que el valor se LEE igual y deja de ejecutarse.
_ARRANQUES_PELIGROSOS = ("=", "+", "-", "@", chr(9), chr(13))


def _neutralizar_formula(valor: Any) -> Any:
    """Impide que una celda de TEXTO se interprete como formula al abrirla.

    Solo toca cadenas: los numeros y las fechas se escriben tal cual, porque no
    hay forma de que un float arranque una formula.
    """
    if isinstance(valor, str) and valor.startswith(_ARRANQUES_PELIGROSOS):
        return "'" + valor
    return valor

def _fila_a_columnas(fila: dict[str, Any]) -> list[Any]:
    venta = fila.get("venta")
    if venta is None:
        vendido = "no"
        precio_venta = ""
        plataforma_venta = ""
        fecha_venta = ""
    elif venta.get("estado") == "devuelta":
        vendido = "devuelta"
        precio_venta = cents_a_euros(venta.get("precio_final_cents"))
        plataforma_venta = venta.get("plataforma_venta") or ""
        fecha_venta = venta.get("fecha_venta") or ""
    else:
        vendido = "sí"
        precio_venta = cents_a_euros(venta.get("precio_final_cents"))
        plataforma_venta = venta.get("plataforma_venta") or ""
        fecha_venta = venta.get("fecha_venta") or ""

    return [
        fila.get("referencia"),
        fila.get("titulo") or "",
        fila.get("lote_id") or "",
        cents_a_euros(fila.get("coste_cents", 0)),
        _texto_subido(fila),
        vendido,
        precio_venta,
        plataforma_venta,
        fecha_venta,
        cents_a_euros(fila.get("beneficio_bruto_cents")),
    ]


def exportar_excel(filas: list[dict[str, Any]], destino: str | Path) -> Path:
    """Escribe `filas` (la forma exacta de `store.cargar_ventas()`) como un
    `.xlsx` en `destino`. Crea el directorio padre si hace falta. Devuelve la
    ruta escrita (== `destino`, como `Path`).

    Una fila por producto, en el mismo orden que llega (el ledger ya viene
    ordenado por más reciente primero, `store.cargar_ventas`). No se
    reordena ni se filtra aquí — lo que se le pase, se exporta tal cual (el
    filtrado de la UI, si lo hay, ya pasó ANTES de llamar a esto).
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Finanzas"
    hoja.append(list(_COLUMNAS))
    for fila in filas:
        hoja.append([_neutralizar_formula(v) for v in _fila_a_columnas(fila)])
    libro.save(destino)
    return destino


def nombre_export_por_defecto(ahora: datetime | None = None) -> str:
    """`finanzas_YYYY-MM-DD.xlsx` — la fecha en el NOMBRE es lo que le dice a
    Diego cuándo se generó ese informe (el .xlsx no es la verdad; ver
    docstring del módulo). `ahora` es inyectable para tests deterministas."""
    momento = ahora or datetime.now(timezone.utc)
    return f"finanzas_{momento.date().isoformat()}.xlsx"
