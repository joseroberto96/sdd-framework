# Subagentes

> Definiciones portables para entornos que soporten subagentes. Aislamiento de
> permisos y autoría independiente son capacidades distintas y se declaran.

## Política

- Los subagentes de REVISIÓN del framework son de solo lectura. La única
  excepción declarada es `Executor` (4.2.0): implementa, pero SOLO dentro de
  su worktree aislado y sin mutar `_project/` ni el estado (ver su contrato).
- No pueden escribir archivos, ejecutar fixers, instalar dependencias ni modificar estado.
- El agente principal es el único que puede actualizar `_project/`.
- Si existe dispatcher pero no permisos read-only mecánicos, usar otro agente
  bajo contrato no-write, verificar diff pre/post y declarar
  `INDEPENDENT_SUBAGENT_NO_ISOLATION`. Si no existe dispatcher, usar
  `SECUENCIAL_NO_AISLADO`.

## Subagentes disponibles

- `Preflight Reviewer` (`preflight-reviewer.md`): revisa planes antes de CODE, Risk Surface y gates/workflows faltantes.
- `Reviewer`: valida ACs, contratos y dependencias declaradas.
- `Security`: valida constraints, secrets, inputs, auth, logs y exposición de datos.
- `Area Frontend` (`area-frontend.md`, 3.16.0): revisa el módulo bajo foco `frontend` con la lente de `frontend_design` + `cross_platform_web` (5 estados de UI, a11y, no innerHTML sin sanitizar). **Condicional** al foco activo del builder.
- `Area Backend` (`area-backend.md`, 3.16.0): revisa el módulo bajo foco `backend` con la lente de `architecture_core` + `security_core` (validación de inputs, ORM/prepared, authz). **Condicional** al foco activo del builder.
- `Executor` (`executor.md`, 4.2.0): ejecutor paralelo de módulo para `/ciclo fase --paralelo` — implementa en worktree aislado, no muta estado, no cierra; reporta `EXECUTOR_RESULT`. **Opt-in** con checkpoint de coste.
- `Area Data` (`area-data.md`, 3.16.0): revisa el módulo bajo foco `data` con la lente de `architecture_core` + `security_core` (migraciones idempotentes, PII). **Condicional** al foco activo del builder.
