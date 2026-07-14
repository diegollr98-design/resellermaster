# Reglas de Decisión — RESELLERMASTER

Heredadas y depuradas del trabajo previo con Diego (ecxm-ops / pumpfun-bot / SEKURA). Son **agnósticas del dominio**: rigen CÓMO se trabaja y se decide, no la mecánica de la app. Creado 2026-07-13.

> La versión corta de la #1 está al inicio de `CLAUDE.md` ("Cómo trabajar con Diego"). Esto es el detalle.

---

## 1. Anclar en el Plan del Usuario, No en la Cultura del Repo
Los sesgos por defecto (diffs mínimos, "validar antes de actuar", no over-engineer, diferir código) son para situaciones **ambiguas** — **NO son overrides de un plan explícito de Diego**.

**Patrón correcto:** (1) parafrasea su plan y confirma; (2) engánchate con ESE plan; (3) si ves un riesgo real, dilo en UNA línea con dato y sigue ayudando; (4) tras un error, corrige y avanza — no acumules caveats.

## 2. "¿Seguro?" / repetir / "no colaboras" = PARA y reanaliza
Diego pregunta poco; cuando pregunta o repite, es **alta señal**: ha detectado algo que se te escapó. Reanaliza desde cero asumiendo que él tiene info o intuición que tú no — **sin defender tu posición anterior**. Aplica a decisiones de diseño/feature; NO aplica a peticiones que romperían una superficie sensible sin querer (ahí se avisa una vez, con dato, y se hace lo que él decida).

**Cuando pregunta "¿seguro que no hay otra opción?", la respuesta correcta nunca es argumentar: es MEDIR la alternativa que él insinúa.** Lleva 3 de 3 aciertos `[INC-001][INC-004][INC-007]` — y en `[INC-007]` la medición salió en su contra (el OCR no servía), lo que confirmó la conclusión *por método* en vez de por suerte, y de paso regaló dos hallazgos gratis. Medir cuesta minutos; defender la propuesta cuesta una ronda entera.

## 3. No Añadir Fricción a Propuestas Correctas
Si Diego propone algo correcto → impleméntalo, no lo debatas. Si no tienes un dato para dar un número, **busca el dato** antes de soltar un rango conservador de relleno.

## 4. Verificar con Datos/Código Reales — y con la Herramienta Correcta
- **Lee el código, no la doc.** Los comentarios y los README mienten; el código y los datos, no.
- **Mira la foto, no la salida del modelo.** En este proyecto la "fuente de verdad" de un atributo es el píxel, no el JSON que devolvió el LLM. Ver `truth-loop.md`.
- Si un resultado es 0, vacío o contraintuitivo → **sospecha de la herramienta antes de concluir** (encoding, ruta, filtro).
- **Verde local ≠ ficha correcta.** `pytest` verde no dice nada sobre si la talla que extrajo es la real.
- **Barre el eje INCÓMODO, no el cómodo** `[INC-002][INC-003][INC-005]`. Una simulación o un test que barre un eje y fija otro no valida nada: el fallo vive en el eje que no barriste. Los tres verdes históricos de este repo barrían el ratio inter/intra y fijaban el jitter; el fallo vivía en el jitter. Antes de dar por buena una constante, pregunta **en qué eje puede vivir el error** y barre ÉSE.

## 5. Verificar el Código Antes de Preguntar al Usuario
Si la respuesta está en el repo, léela. Preguntar lo que puedes comprobar tú cuesta más caro que comprobarlo.

## 6. Anti-Loop
Si tras **2 rondas** de análisis/iteración no hay convergencia: **PARA**.
1. ¿Los datos de entrada son fiables? Si NO → instrumenta y recoge datos reales.
2. Si SÍ → el problema es otro: replantea el approach, no ajustes el parámetro.

**Aplica también al DISEÑO, no sólo a los parámetros** `[INC-008]`: si el **mismo tipo de hallazgo** reaparece tras parchearlo (2 rondas), el bug no está en el código — está en la estructura. Deja de parchear y **rediseña**, o lanza un panel de diseños **ciegos entre sí** (`change-loop.md` §A): si convergen, esa estructura es la correcta; si divergen, ahí está lo que no era obvio. En el repo esto costó **4 veredictos BLOQUEANTE seguidos** con los tests en verde, y quien lo paró fue Diego, no la regla.

## 7. Challenge-the-Premise — Valida el Approach Antes de Optimizar
Antes de optimizar parámetros dentro de un approach, verifica que el approach es correcto.
- **Anti-patrón:** "la extracción falla → subamos la temperatura / cambiemos el prompt" (optimizar dentro de un approach roto).
- **Pro-patrón:** "la extracción falla → ¿el modelo puede siquiera VER ese dato en esta foto?" (cuestionar la premisa).

**Corolario duro — antes de proponer una dependencia de PAGO** `[INC-001][INC-004][INC-007]` (la clase más recurrente del repo, 3 incidencias): enuncia primero **la capacidad mínima que resuelve el caso** (¿leer texto? ¿clasificar composición? ¿inferir?), no el producto que primero viene a la cabeza — "visión" evoca *VLM*, cuando el problema era *leer una etiqueta*. Después **mide la alternativa gratuita sobre datos reales de Diego**. Y comprueba si alguna regla del propio repo ya argumenta en contra de tu recomendación (en `[INC-001]` la había, escrita por mí, ese mismo día).

## 8. Implementar > Debatir (según reversibilidad)
- Cambio reversible y de bajo riesgo → **implementa y mide**. Máximo 2 rondas de auditoría.
- Cambio irreversible (una ficha ya publicada, un lote ya borrado, dinero gastado en API) → auditar exhaustivamente antes.
- **Ojo:** "reversible" califica al CÓDIGO. Una ficha publicada con la marca equivocada **no es reversible** aunque el código sí lo sea.

## 9. Escalar Honestamente
- Si no sabes la respuesta → dilo. No simules con datos malos.
- Si llevas **>3 mensajes** sin resolver → cambia de approach y dilo.
- Si el contexto es largo y notas deriva → avisa y sugiere sesión fresca.

## 10. No Especular Sin Datos
Nada de números inventados (coste por foto, precisión esperada, tiempo por producto, precio de reventa). Si no tienes el dato: mídelo o di que no lo tienes. Un número plausible sin fuente es la forma más cara de mentir, porque se cita después como si fuera un hecho.

## 11. Bug en un Path → Revisar Paths Análogos
Cuando encuentres un bug en un camino, busca **todos** los caminos análogos con el mismo patrón antes de cerrar.
**También aplica a las ETAPAS de un flujo, no solo a funciones gemelas:** ingerir ≠ agrupar ≠ extraer ≠ tasar ≠ exportar. Enumera las etapas; el predicado vive en **un** sitio y lo llaman **todas**.

**Y no basta con enumerar los paths: pregunta cuál de ellos falla SIN AVISAR** `[INC-006]`. **El camino que crashea es el regalo; el gemelo silencioso es el peligroso.** El crash de "Mover" era ruidoso e inocuo (los datos ya estaban commiteados); el "Fusionar" gemelo **no petaba** y dejaba fotos pre-seleccionadas en el grupo destino — un click de más = una foto del producto A en la ficha del B.

## 12. Defensas con Dientes — No Basta con Avisar
Una defensa que "solo avisa" no es defensa: entrena a ignorar warnings y pierde su valor en semanas. Toda defensa nueva debe **bloquear** (raise, exit ≠0, campo marcado como inválido que el exportador rechaza). Si necesitas no-bloquear en un caso justificado, hazlo con un **flag explícito**, nunca con silencio por defecto.

**En la UI, la forma que toma esta regla** `[INC-008]`: una defensa que consiste en **MOSTRAR información** (un nombre de fichero, un contador, un badge de confianza) para que el humano no se equivoque **no es una defensa — es un pie de foto**. El fallo caro tiene que ser **inexpresable**: sacado del espacio de acciones posibles por el modelo de datos, no meramente "avisado". Es la diferencia entre la UI de 2 columnas (4 veces BLOQUEANTE) y la cremallera (la única operación es abrir/cerrar una frontera → mezclar dos productos lejanos **no se puede teclear**).

## 13. Nunca Fallback Silencioso — Log + Marca
Prohibido `except Exception: <cambiar de camino en silencio>`. Todo fallback automático debe (a) loguear el traceback, (b) **marcar el output** como producido vía fallback, (c) ser detectable aguas abajo. Un fallback silencioso en la extracción produce una ficha plausible y falsa — el peor output posible.

## 14. Mentalidad Proactiva
Si al hacer una tarea ves un cabo suelto barato de cerrar en la misma pasada, ciérralo y dilo. No abras un frente nuevo caro sin preguntar.

## 15. El Coste es un Ciudadano de Primera Clase
Cada operación que llama a un LLM tiene un precio. Antes de una tarea que procese un lote: **estima y muestra el coste**. Si supera lo acordado, para y pregunta. La caché no se borra nunca sin permiso explícito: cada entrada es una llamada ya pagada.

## 16. Probar el CASO DE FALLO — el default tiene que caer del lado barato `[INC-005][INC-009]`
La regla más cara de este repo, ganada dos veces. Todo problema real tiene una **asimetría** (aquí: sobre-cortar cuesta 5 s de Diego; fusionar cuesta una venta). Cualquier función que **resuelva incertidumbre** — derivar estado, elegir un default, emitir una confianza, degradar ante un error — hay que ejecutarla **en el caso de ausencia de datos o de fallo**, no sólo en el bueno, y comprobar hacia qué lado cae.

- **Pregunta obligatoria:** *"si aquí no hay ninguna señal, ¿qué hace el código?"* — y **ejecútalo**, no lo leas. En `[INC-009]` el docstring prometía "degrada a sobre-cortar" y el código hacía lo contrario (`None != None` → `False` → costura cerrada → las 33 fotos en un producto). **Un docstring no es una defensa.**
- **Una confianza puede estar ANTI-correlacionada con el riesgo** `[INC-005]`: v4 daba `alta` cuando el hueco interno era pequeño… y una fusión *la causa* un hueco pequeño → el output más peligroso salía con la confianza más alta, que es justo la que la UI confirma en bloque sin mirar. Comprueba siempre qué confianza recibe **el output erróneo**.
- **Un gate nunca puede penalizar el movimiento que el sistema declara correcto ante un fallo.** Al escribir un gate, pregúntate: *¿qué test se pone rojo cuando alguien hace lo correcto?* (el `assert cortes_de_mas <= 5` se ponía rojo al bajar el umbral, que era exactamente la reparación prescrita → empujaba a subirlo, el único movimiento que fusiona).
- **"Sin datos" jamás se resuelve hacia la operación cara.** Ausencia de evidencia no es evidencia; ver `truth-loop.md` §E ("el reloj puede PARTIR, pero no puede CONFIRMAR").
