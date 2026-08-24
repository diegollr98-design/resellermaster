"""Configuración común de la suite.

## Suelo al `default_timeout` de `AppTest`

`streamlit.testing.v1.AppTest` trae un `default_timeout` de **3 segundos**, y
ese número no mide nada del código bajo prueba: mide cuánto tarda el runtime de
Streamlit en arrancar y renderizar en la máquina que corre el test. Bajo
contención de CPU —un runner de CI compartido, o la suite entera de golpe— el
script no termina a tiempo y el test falla con la pantalla **vacía**:
`len(at.error) == 0`, `at.get("imgs") == []`.

Ese modo de fallo es especialmente traicionero aquí porque **miente sobre la
causa**: un `assert len(at.error) >= 1` que se cae parece decir *"la UI ya no
pinta el aviso que bloquea confirmar"* —un bug real y grave en la superficie
que Diego toca con las manos— cuando lo que ha pasado es que nadie llegó a
pintar nada. Un fallo que misatribuye su causa es la clase `[INC-030]`, ahora
en la suite en vez de en la UI.

Ya se diagnosticó una vez en este repo (el flaky de `test_curar`, resuelto
subiendo su `timeout` a 30 s), pero el arreglo se quedó **en ese único test**;
los ~90 `AppTest` de `test_ficha.py` siguieron con el default de 3 s y se
cayeron los seis a la vez en el primer CI. Por eso el suelo se pone en UN sitio
y lo heredan todos los tests de UI: el predicado vive en un sitio y lo llaman
todas las etapas (`decision-making.md` §11).

**No afloja ninguna aserción.** Un test que pasa, pasa igual de rápido: el
timeout es un tope, no una espera. Lo único que cambia es que el reloj de
arranque de Streamlit deje de decidir el resultado.
"""

from __future__ import annotations

from typing import Any

from streamlit.testing.v1 import AppTest

# 30 s es el valor que ya se validó contra el flaky de `test_curar` (ver su
# comentario en la linea 312). No se elige a ojo: es el que se midió que
# aguanta el warmup de Streamlit bajo carga.
TIMEOUT_MINIMO_APPTEST = 30.0

_init_original = AppTest.__init__


def _init_con_suelo(self: AppTest, *args: Any, **kwargs: Any) -> None:
    """Aplica `TIMEOUT_MINIMO_APPTEST` como suelo, nunca como techo.

    Se respeta cualquier timeout MAYOR que pida el test explícitamente; sólo se
    sube el que se quedaría corto.
    """
    pedido = float(kwargs.get("default_timeout", 3))
    kwargs["default_timeout"] = max(pedido, TIMEOUT_MINIMO_APPTEST)
    _init_original(self, *args, **kwargs)


AppTest.__init__ = _init_con_suelo  # type: ignore[method-assign]
