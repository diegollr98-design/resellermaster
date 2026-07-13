# Seeds — RESELLERMASTER

Un **seed** es un prompt autocontenido para arrancar una sesión fresca en una fase concreta, sin arrastrar el contexto (ni los sesgos) de la anterior.

## Prompt de arranque de sesión fresca

```
Eres el orquestador ("papá oso") de RESELLERMASTER. Antes de actuar lee CLAUDE.md y .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization, sessions-log),
y docs/seeds/README.md. El estado y el siguiente paso están en CLAUDE.md, línea 3.

Vamos a ejecutar la FASE <N>: lee docs/seeds/fase-<N>.md y ejecútala con la metodología
(gasta poco contexto: delega la implementación a subagentes Sonnet `engineer`; usa `bug-hunter`
para bugs, `listing-audit` para todo lo que el pipeline AFIRME del producto, `flow-qa` para la
velocidad del flujo; valida tú leyendo la salida REAL, no el informe del subagente; corre /eval
antes de cerrar cualquier cambio en una superficie sensible; gate con Diego al final; commit por fase).

Si el seed de una fase futura no existe, genéralo con la plantilla de abajo antes de ejecutarla.
```

## Plantilla de seed

```
# Fase N — <título>
## Objetivo (1-2 líneas)
## Precondiciones (qué debe estar hecho; qué necesita Diego: API keys, fotos de ejemplo, ajustes)
## Alcance / tareas (qué construir; archivos concretos en core/ y ui/)
## Cómo (approach; qué delegar a Sonnet; contratos/tipos exactos)
## Verdad (qué campos afirma esta fase; qué pasa por listing-audit; qué mide /eval)
## Coste (cuántas llamadas al LLM por producto añade esta fase; € estimados)
## Verificación (comandos concretos; /eval; qué prueba Diego en el gate, con fotos suyas reales)
## Commit + gate (mensaje de commit; qué confirma Diego; generar el siguiente seed)
```

## Reglas
- **Seeds just-in-time** (`change-loop.md` §E): seed **completo** de la fase inmediata, **una línea** de esqueleto por fase posterior. Escribir el seed detallado de la fase 6 hoy es escribir ficción — el estado habrá cambiado.
- **Nunca derivar el estado real del seed anterior** (`change-loop.md` §D). Antes de ejecutar una fase, comprueba el estado **contra el repo**: `git log`, el código, el último `/eval`. Los seeds mienten; el código no.

## Estado
_Sin seeds todavía. Se generan en la sesión de planificación, una vez Diego apruebe el plan._
