# Delegación Codex — texto 5.2.2 (modo `legacy`)

> Interruptor de reversión de ADR-072. Solo aplica si `_project/15_environment_profile.md`
> declara `codex_delegation: legacy`; `get_work_context` en `startup` lo señala con
> `summary.codex_delegation = "legacy"`. En ese caso estas dos secciones **sustituyen**
> a las de `.agent/adapters/codex.md`. Es copia literal del adaptador 5.2.2; no se
> mantiene: el modo por defecto es el de `codex.md`.

## SUBAGENT_WAIT_LIFECYCLE

La política de unidades pequeñas y la política de espera se aplican juntas;
esta sección es la autoridad única para el ciclo de espera y
`CODEX_SMALL_TASK_DELEGATION` para el tamaño y la coordinación de encargos.

Al esperar subagentes, aplicar SUBAGENT_WAIT_LIFECYCLE: un timeout no
terminal (WAIT_TIMEOUT_NONTERMINAL) solo termina una ventana de polling; no
es PASS, FAIL ni cancelación. Solo PASS, FAIL, NEEDS_ATTENTION o
UNAVAILABLE permiten cerrar la espera.
El timeout no debe llamar `interrupt` ni `close_agent` automáticamente.
La garantía mecánica depende del dispatcher Codex; el framework no intercepta
acciones del host ni aplica enforcement runtime fuera de él.
Cuando no haya enforcement read-only observable, el reviewer debe ser otro
agente con contrato no-write y diff pre/post; si tampoco existe dispatcher,
declarar SECUENCIAL_NO_AISLADO.

## CODEX_SMALL_TASK_DELEGATION

En Codex se trabaja con un presupuesto de contexto conservador. Dividir
implementación y revisión en encargos pequeños y autosuficientes, cada uno con
un objetivo verificable, archivos concretos, entradas mínimas, alcance de
escritura y salida breve. Ejecutar varios encargos independientes en paralelo;
serializar dependencias y escrituras solapadas. No transferir el historial
completo ni asignar una auditoría integral a un solo subagente.

La delegación sin historial no es automática: el valor predeterminado de la
herramienta del host puede ser bifurcar turnos (`fork_turns: "all"`). Se debe
exigir configurar explícitamente `fork_turns: "none"` (o el parámetro equivalente
de sesión limpia según el esquema de la herramienta disponible en el host).
Por ello, el encargo debe ser operativamente autosuficiente:
- Formular el prompt con objetivo técnico concreto, rutas de archivos explícitas
  y criterio de verificación ejecutable (comando de prueba o diff esperado).
- Delimitar estrictamente el alcance de escritura (o prohibir escritura si es
  revisión); si el subagente requiere estado, instruirle a consultar la vista
  acotada con `state_manager.py query --view implementation/closure --module-id <id>`
  o MCP, nunca volcar transcripciones en el prompt.
- Distinguir aislamiento de paralelismo según la herramienta disponible:
  - Si hay dispatcher pero carece de aislamiento de solo lectura en el SO: es
    posible paralelizar encargos independientes declarando
    `INDEPENDENT_SUBAGENT_NO_ISOLATION`; para revisores se exige contrato de
    no-escritura en el prompt y verificación mecánica del diff pre/post.
  - Si el entorno no dispone de dispatcher de subagentes: declarar
    `SECUENCIAL_NO_AISLADO` y ejecutar las tareas de forma secuencial en el
    hilo principal, sin fingir paralelismo.
Si el encargo crece, planificar las siguientes unidades en una frontera de
trabajo, sin recortar el rigor ni abortar la unidad activa por tardanza.

Una demora puede deberse a compactación. El silencio y un timeout no terminal
no autorizan interrupción, solicitud de finalización anticipada ni cierre.
El timeout o la demora no autorizan `interrupt`, finalización anticipada ni
`close_agent`.
Esperar el resultado y mantener al usuario informado. Cancelar solo por
petición explícita, riesgo concreto, error irrecuperable observado o cambio
explícito de alcance. Las revisiones parciales no satisfacen una revisión Tier
2. No inferir telemetría privada ni prometer ausencia de compactación.
