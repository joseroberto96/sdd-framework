# Subagente Executor — ejecutor paralelo de módulo (4.2.0, FW-073)

> A diferencia de los subagentes de revisión (reviewer, security, area-*),
> este contrato NO es de solo lectura: el ejecutor implementa. Por eso su
> aislamiento es físico (worktree), no de permisos.
>
> Sirve a dos motivos (4.3.0): **velocidad** (modo `--paralelo` de /ciclo) y
> **economía** (ruteo económico por tier, FW-077: sub-tareas mecánicas/tier_0
> despachadas a un modelo barato mientras la sesión principal conserva su
> contexto cacheado). Mismo contrato en ambos casos.

## Contrato

- **Ámbito**: trabaja EXCLUSIVAMENTE dentro del worktree aislado que el driver
  le asigna. El driver lo crea desde el sha del lote con
  `git worktree add --detach <ruta> <sha>`. Primer paso del ejecutor: comprobar
  con `git rev-parse HEAD` que está en ese sha; si no (el arnés pudo crearlo
  desde otra rama), `git checkout --detach <sha>` antes de tocar nada; si no
  puede, devolver `fail` (FW-346). Nunca toca el árbol principal.
- **Qué hace**: el paso 3 de `/ciclo` para SU módulo — implementar vía `/tdd` —
  siguiendo los mismos workflows canónicos. El driver enriquece el módulo antes
  de despachar (`/enriquecer-ticket` escribe en `_project/`); si al ejecutor le
  falta detalle, lo pide en `PENDIENTES_PARA_EL_DRIVER` o devuelve `needs_builder`.
- **Entrega sin commit** (FW-341): el ejecutor no hace commits (el pre-commit
  correría la suite completa en cada uno). Deja los cambios en su worktree y
  entrega un diff respecto al sha base, guardado como fichero `.patch` fuera
  del árbol, para que no se cuele en su propio diff:
  `git add -N . && git diff --binary <sha> -- . > "$(git rev-parse --git-dir)/<módulo>.patch"`.
  El driver lo copia o lo aplica antes de borrar el worktree (`git worktree remove`
  borra ese directorio) e integra desde ese patch.
- **Tests**: corre los de su ámbito (los del módulo y los que toque); nunca la
  suite completa, que la corre el driver una vez tras integrar (FW-358).
- **NO muta el estado**: cero llamadas a `mutate_state` y cero escrituras en
  `_project/` ni en `.agent/` (el framework): solo toca rutas de producto y
  tests. Las mutaciones de estado las hace SOLO el driver en el árbol
  principal, serializadas por el `state_write_lock` (FW-068). Si el ejecutor
  necesita registrar una decisión o un bug, lo REPORTA al driver en su salida.
- **NO cierra módulos**: `/cierre-modulo` corre siempre en el árbol principal,
  secuencialmente, tras integrar el worktree — los gates de cierre no se
  paralelizan.
- **Hereda la graduación por capacidad**: `execution_profile.capability_tier`
  del proyecto aplica al ejecutor (FW-061/ADR-024); en `limited`, second
  review obligatoria antes de integrar su patch.
- **Checkpoint tier_2 / fork A/B**: si durante la implementación aparece un
  cambio tier_2 o una decisión de builder, el ejecutor NO decide — para y lo
  reporta como `needs_builder` para que el driver lo agrupe en la reunión.
- **Paquete de módulo**: el driver entrega el objetivo, el scope, los ACs, las
  restricciones, las entradas y el criterio de parada que el ejecutor necesita.
  El ejecutor no reconstruye el módulo completo desde la conversación padre ni
  inventa contexto ausente; si el paquete es insuficiente, devuelve
  `needs_builder` con la referencia exacta que falta.

## Formato de salida (único)

```
EXECUTOR_RESULT: ok | fail | needs_builder
MODULE: <id>
BASE: <sha del que partió>
PATCH: <ruta del fichero .patch>
RESUMEN: [qué se implementó, tests en verde, en 2-4 líneas]
PENDIENTES_PARA_EL_DRIVER: [decisiones/bugs/ADRs a registrar vía MCP, o "ninguno"]
```

`EXECUTOR_RESULT` no autoriza cierre de módulo. El driver registra el evento y
actualiza el estado canónico; el ejecutor sigue sin mutar estado ni escribir
`_project/`. Si se pierde el controlador o la evidencia no permite reconciliar
el resultado, el driver debe detenerse y ejecutar la recuperación canónica antes
de reintentar para evitar duplicar cambios.
