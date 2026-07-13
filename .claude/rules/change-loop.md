# Loop de Cambios — RESELLERMASTER

**Protocolo obligatorio.** Cada vez que Diego proponga un **cambio, añadido o feature nueva**, ANTES de diseñar el plan o de tocar nada, el orquestador **activa este loop**: lo anuncia en una línea y **pregunta si lo corre**.

> **Regla de disparo:** ante una propuesta de cambio → di *"esto activa el Loop de Cambios"* + recomienda correrlo o saltarlo según §B de `truth-loop.md`, y espera el OK. Para retoques triviales de **una sola superficie no sensible**, basta con anunciar que se salta el ritual pesado y proceder. **Nunca al revés:** nunca ejecutar el ritual pesado sin avisar, ni saltarlo en algo que toque atributos, precio, agrupación, coste o persistencia.

Heredado del red-team de SEKURA v0.15: las optimizaciones de *planificación* no bastaban; el modo de fallo real de este equipo es **agentes que reportan sin verificar**.

---

## §A — Planificación del plan
Debatir la descomposición **completa** antes de tocar nada. Máximo **2 rondas** (Anti-Loop, `decision-making.md` §6). El agente que debate **lee el código real**, no la doc.

**Panel ciego condicional:** si el faseado no es obvio, lanzar un 2º Opus que redescomponga **desde cero, sin ver la primera propuesta**. La divergencia entre ambos marca exactamente dónde el plan no era obvio — ahí es donde hay que mirar. Si convergen, ejecuta.

## §B — Calibrado de ceremonia
Por **superficie**, no por número de líneas. La lista de superficies sensibles vive en `truth-loop.md` §B y es parte del **conjunto inmutable**.

## §C — Los mecanismos DUROS de verificación (el núcleo)

1. **Verificación por EJECUCIÓN, no por informe.** El orquestador corre él mismo `pytest` / `/eval` / `grep` / la app, y **pega la SALIDA REAL dentro del prompt del siguiente agente**. Ningún *"el subagente reportó que está OK"* cierra nada. Un informe no es evidencia; una salida sí.

2. **Doble pase en veredictos críticos.** Todo veredicto que desbloquee una superficie sensible (`listing-audit` diciendo LIMPIA, `/eval` diciendo que no hay regresión) recibe **un segundo pase independiente**, o el orquestador lo **re-deriva él mismo** de la salida real.

3. **Etapas, no solo funciones gemelas** (`decision-making.md` §11). El flujo es `ingerir → agrupar → extraer → tasar → exportar`. Un bug en "extraer" hay que buscarlo en las **cinco etapas**, no solo en las funciones que se parecen. El predicado vive en **un** sitio y lo llaman **todas**.

4. **Verde local ≠ ficha correcta.** `pytest` verde, `ruff` limpio y la app arrancando **no dicen nada** sobre si la talla que extrajo es la real. La única señal que cuenta para una superficie sensible es **`/eval` contra el golden set** + el ojo de Diego. Un fallback honesto (campo `null`, valor por defecto) **jamás** puede tragarse un error de extracción en silencio: loguear ruidoso y marcar el campo.

5. **Coste antes de ejecutar.** Cualquier cambio que altere cuántas llamadas al LLM se hacen por producto → **recalcular el coste/producto y decirlo**, aunque nadie lo pregunte. Un cambio de prompt que duplica el coste es un cambio de arquitectura disfrazado.

## §D — Reconciliación de estado antes de cada fase
**Nunca derivar el "estado real" del seed anterior.** Antes de refinar o ejecutar la fase N, comprobar el estado **contra el repo** (`git log`, el código, `/eval`). Los seeds y los docs se desactualizan; el código y los datos no.

## §E — Seeds just-in-time
Seed **completo** de la fase inmediata + **esqueleto de una línea** por fase posterior. Escribir seeds detallados de fases lejanas es escribir ficción: el estado habrá cambiado. Plantilla en `docs/seeds/README.md`.

## §F — Retro + meta-mejora
Ver `truth-loop.md` §F. Resumen: **el retro registra hechos en el ledger; solo `/optimize` escribe reglas; las reglas se etiquetan con el `[id]` del incidente y se retiran por convergencia; el conjunto inmutable no lo toca ningún auto-proceso.**

---

## Changelog
- **v1.0** (2026-07-13) — creado con la infra, adaptado de `SEKURA/.claude/rules/change-loop.md` v1.1.
