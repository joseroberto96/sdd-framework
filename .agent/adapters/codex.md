# Adaptador — Codex

> **Soporte:** verified (estructural_support) — la superficie estructural se valida con
> framework_doctor; CLI y Desktop/app-server se certifican por separado.
> **Smoke runtime:** runtime_smoke de codex_runtime_smoke.py informa cada superficie como
> pass, fail o unavailable; solo un pass observado por el host permite afirmar
> compatibilidad runtime.
> **Degradación:** ver .agent/adapters/_capability_matrix.md. Si no hay
> dispatcher read-only, declarar INDEPENDENT_SUBAGENT_NO_ISOLATION o
> SECUENCIAL_NO_AISLADO; si no hay slash commands, usar los workflows por
> archivo.
> **Subagentes read-only:** no — Codex no ofrece hoy un contrato read-only
> verificable por el host.

## Unidad de trabajo y carga progresiva

La conversación es una caché temporal, no la fuente de verdad ni la unidad de
trabajo. El estado canónico vive en _project/03_project_state.json, su
bitácora y las proyecciones; una compactación exige /reconciliar antes de
seguir. El adaptador no intenta medir la ventana privada del host ni convertir
un porcentaje supuesto en permiso de ejecución.

La fase `preparation` pertenece al workflow, no a las vistas públicas: allí se
clasifican tier/specs/aplicabilidad, se prepara PRESERVAR y se obtienen receipts
cuando aplican. Tras cada preparativo se vuelve a consultar `implementation`;
la entrada a CODE exige contexto completo y vigente. En cierre se pueden
producir ejecución y revisión antes de la reconsulta final, sin abrir el estado
completo como atajo.

El camino normal obtiene solo la vista de la etapa actual mediante
get_work_context:

    python3 .agent/scripts/state_manager.py query --view startup
    python3 .agent/scripts/state_manager.py query --view implementation --module-id <módulo>
    python3 .agent/scripts/state_manager.py query --view closure --module-id <módulo>
    python3 .agent/scripts/state_manager.py query --view recovery

La misma operación está disponible por MCP con {view, module_id?, cursor?}.
Las vistas son paginadas y se ligan a un fingerprint de estado, aplicabilidad y
documentos realmente leídos:

- startup: identidad, fase, objetivo activo, tier, continuidad y bloqueos
  globales.
- implementation: precondiciones, especificaciones, restricciones,
  dependencias y contratos del objetivo.
- closure: criterios de aceptación, rechazos, riesgos, recibos y evidencia
  aplicables al cierre.
- recovery: continuidad, journal, bitácora, diferencias y evidencia de
  recuperación.

La primera respuesta apunta a un máximo de 12 KiB de JSON de datos y las
respuestas de detalle a 24 KiB. El límite se aplica al transporte controlado,
no a la calidad ni a la ventana del modelo: un elemento grande se fragmenta o
se entrega con una referencia exacta; nunca se descarta silenciosamente. El
next_cursor se invalida si cambia la instantánea. complete significa que
terminó la paginación y required_context_complete que se entregaron todas las
secciones y obligaciones activas de la vista. La respuesta también expone
`required_obligations` y añade el ID de la obligación a cada ref faltante; un
`NEEDS_INPUT` por evidencia ausente no se puede convertir en `PASS` cargando
el estado completo. Cuando hay varios motivos del validator, la ref conserva
`validation_reasons` acotados además de su `reason` estable. Ninguna de estas
señales autoriza por sí sola una mutación o un cierre.

get_full_state (volcado completo con `{"force": true}` por encima de 256 KiB) y
get_session_brief se conservan para diagnóstico y compatibilidad. No se usan como precarga normal. Las fuentes futuras —
reconciliación, cierre, revisión y recuperación— se activan solo cuando el
workflow llega a esa frontera. Si una fuente es ambigua, falta o no tiene un
selector normativo inequívoco, cambia durante la lectura, o el journal de
recovery está ausente, el resultado es NEEDS_INPUT o un error clasificado,
nunca una selección aproximada.

## Rigor y seguridad

La carga progresiva reduce información repetida, no obligaciones. Antes de una
mutación siguen vigentes estado canónico, scope, PRESERVAR, tier, specs,
contratos, seguridad, secretos, comandos destructivos, aprobación y
dependencias. /ciclo vuelve a validar los predicados reales; una vista PASS no
sustituye cambio-seguro, TDD, revisión ni complete_module.

Las respuestas de los validadores deben conservar código de salida, estado,
conteo e identificadores de fallo y una referencia a la evidencia completa.
UNAVAILABLE, timeout, salida malformada o comprobación no ejecutada nunca se
interpreta como PASS. El estado de recuperación no relanza, cancela ni declara
terminada una ejecución por silencio.

Para comparar recorridos, `.agent/scripts/context_measurement.py` separa bytes
controlados de datos y bytes del wire y cuenta páginas, rutas leídas y
duplicados. Esa medición sirve para optimizar la carga; no prueba por sí sola
una reducción de tokens ni ausencia de compactación en el host.

## Integración Codex

Para mutaciones, Codex descubre el MCP nativo state_manager desde
.codex/config.toml; .mcp.json es un espejo opcional. La configuración y
los hooks solo cuentan después de revisarlos y confiar el proyecto desde
/hooks. Si el MCP no está disponible, el fallback explícito es:

    python3 .agent/scripts/state_manager.py mutate --action <acción> --payload '<json>'

ensure_codex_integration.py --from-profile --native-only crea o fusiona el
servidor MCP nativo y los hooks gestionados preservando hooks personalizados.
Se conservan el lector app-server, la preservación de superficies, el guard
destructivo, el runner de hooks y la sincronización de hooks. No se conserva
un planificador de contexto ni un dispatcher de slices.

También escribe `.codex/rules/framework-reads.rules`, que permite sin revisor las
lecturas del framework (`state_manager.py query|lookup|describe` y
`framework_doctor.py`); no rebaja `mutate`, `git push` ni nada destructivo. Codex
solo carga reglas de proyecto con la capa `.codex/` de confianza.

Las reglas del framework no son preferencias del builder: no las guardes en la
memoria del host (`~/.codex/memories`). Se inyectaría en todos los proyectos; el
doctor avisa si ya están allí (FW-294).

La certificación estricta se ejecuta con modelo y esfuerzo explícitos:

    export CODEX_SMOKE_MODEL=MODELO_PROBADO
    export CODEX_SMOKE_REASONING_EFFORT=ESFUERZO_PROBADO
    python3 .agent/scripts/codex_runtime_smoke.py --surface cli --required-surfaces cli --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --json
    python3 .agent/scripts/codex_runtime_smoke.py --surface desktop --required-surfaces desktop --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --json

Una prueba de terminal no certifica la UI Desktop. Si una superficie no está
disponible, se conserva como unavailable y la certificación queda pendiente.

## TURN_CLOSE_HOOK

El `Stop` llama a `.agent/scripts/turn_close_hook.py` (5.3.0, ADR-074), que pasa
el último mensaje por el validador único del cierre (`turn_close.py`, modo
`live`). Si el cierre tiene errores bloquea **una vez** con la corrección de
cada defecto y el agente responde solo con las líneas de cierre corregidas, sin
repetir la respuesta anterior; nunca bloquea dos veces seguidas. Los avisos solo
se registran: `INSTRUCCION_LARGA` y `PROHIBICIONES` avisan sin bloquear desde la
5.3.2 (FW-402). Si el hook falla o agota su tiempo, deja pasar y lo registra. No
evalúa en modo `template` ni en subagentes. Telemetría: solo códigos y
contadores, fuera del repo, en `~/.cache/framework-v4/<proyecto>-<huella>/turn_close.jsonl`.
Reversión: `turn_close_hook: off` en `_project/15_environment_profile.md` lo deja
en solo registro.

Prosa llana (FW-362): separa con un espacio las palabras de las cifras y de las siglas («antes de CODE», «sin el 8210», «por 8 bugs»); el validador lo avisa con `PALABRAS_PEGADAS`, sin bloquear.

En Codex lo invoca `codex_hook_runner.py --hook-id stop`. La continuación con
`decision: "block"` está probada en Codex 0.162.1; con una versión anterior el
doctor avisa. El detector de bucles y el aviso de sesión larga no cubren Codex
en 5.3.0: su transcripción tiene otro formato y el medidor los ve a posteriori.

## SESSION_HYGIENE

Aplica la regla 7 de `.agent/rules/runtime_core.md`. Modelo y esfuerzo se eligen
al empezar: cambiar de modelo invalida la caché de prompt, y en Codex cambiar el
esfuerzo a mitad de sesión también (medido en 5.3.0 L1, FW-320).

Política de sesión: si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar`.

## SUBAGENT_WAIT_LIFECYCLE

La política de delegación y la política de espera se aplican juntas; esta
sección es la autoridad única para el ciclo de espera y
`CODEX_SMALL_TASK_DELEGATION` para cuándo y cómo se delega (ADR-072, que
modifica ADR-057).

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

**Tope de espera.** Se espera con ventanas de polling cortas y se mide el
progreso observable del subagente (salida nueva, archivos tocados, cambio de
estado). Pasados **20 min sin progreso observable**, el agente puede decidir
—de forma explícita y dejándolo dicho al builder— interrumpir al subagente con
`interrupt` y seguir el trabajo en el hilo principal. Esa decisión nunca es
automática ni la toma el timeout por sí solo. El resultado de ese subagente se
registra como `NEEDS_ATTENTION`: jamás como PASS ni como FAIL, y una revisión
interrumpida no satisface una revisión Tier 2 (hay que repetirla o degradar
según el workflow). Con progreso observable no hay tope: se sigue esperando.
Cerrar al subagente con `close_agent` solo se hace al terminar su encargo, tras
recoger su resultado.

## CODEX_SMALL_TASK_DELEGATION

**En Codex no se delega por defecto.** El trabajo se hace en una sola
conversación: coordinar subagentes cuesta arranque, espera y encargos de
corrección, y en la auditoría del 2026-09-30 eso multiplicó por varias veces el
tiempo de un plan. Se delega solo si se cumple alguna de estas condiciones:

1. el builder lo pide;
2. un workflow exige revisión independiente (revisor y security en el cierre
   tier_2, preflight);
3. hay al menos dos encargos grandes y realmente independientes (sin archivos
   ni estado compartidos) y el agente anuncia al builder, antes de lanzarlos,
   cuántos subagentes y cuánto tiempo estima.

Tope: 4 subagentes simultáneos por plan. Si no hay dispatcher, declarar
`SECUENCIAL_NO_AISLADO` y hacerlo en el hilo principal, sin fingir paralelismo.

**Subagente nuevo por encargo (FW-243).** No se reutiliza una instancia para
otra tarea con `followup_task`: al terminar el encargo se recoge el resultado,
se cierra el subagente y el siguiente arranca limpio. Única excepción: la
corrección del mismo encargo que ese subagente acaba de entregar.

Si se delega, valen las reglas de FW-195: encargos pequeños y autosuficientes,
cada uno con un objetivo verificable, archivos concretos, entradas mínimas,
alcance de escritura y salida breve; las dependencias y las escrituras
solapadas se serializan. No transferir el historial completo ni asignar una
auditoría integral a un solo subagente.

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
- Si hay dispatcher pero carece de aislamiento de solo lectura en el SO,
  declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION`; para revisores se exige contrato
  de no-escritura en el prompt y verificación mecánica del diff pre/post.
Si el encargo crece, planificar las siguientes unidades en una frontera de
trabajo, sin recortar el rigor.

Una demora puede deberse a compactación: el silencio y un timeout no terminal
no son un veredicto ni autorizan cerrar al subagente. Solo el tope de espera
de arriba, con progreso nulo y decisión explícita, permite interrumpirlo.
Cancelar por otras causas solo por petición explícita, riesgo concreto, error
irrecuperable observado o cambio explícito de alcance. Las revisiones parciales
no satisfacen una revisión Tier 2.
No inferir telemetría privada ni prometer ausencia de compactación.

**Interruptor de reversión.** Si `_project/15_environment_profile.md` declara
`codex_delegation: legacy` (`get_work_context` en `startup` lo señala con
`summary.codex_delegation`), estas dos secciones se sustituyen por el texto de
5.2.2 de `.agent/docs/specs/codex_delegation_legacy.md`.

Codex puede no tener slash commands. En ese caso, interpretar cada /comando
como .agent/workflows/<comando>.md o el skill portable homónimo. Los
workflows internos no se ofrecen como menú del builder y no se saltan sus
gates cuando se invocan como fallback.

Usar rg/shell para localizar archivos, mantener el estado fuera de la
conversación y cerrar la sesión con /gycd. Antes de compactar, persistir
pendientes en _project/13_session_journal.md; después, ejecutar
/reconciliar y dejar que el archivo canónico gane ante cualquier recuerdo
inconsistente.

## Verificación antes de entregar

- Confirmar el diff, la evidencia anti-regresión y la integridad de la
  bitácora si se modificó código existente.
- Ejecutar el doctor, las suites focalizadas y el smoke real de las superficies
  declaradas; separar evidencia estructural de runtime.
- No afirmar que se controla la ventana de contexto ni que se evita toda
  compactación: el contrato garantiza selección, paginación, recuperación y
  rigor, no telemetría privada del host.
