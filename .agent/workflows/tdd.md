# TDD

Ciclo rojo-verde-refactorizar para implementación tier_1 o tier_2 con lógica que probar y para bugs de regresión.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, rojo-verde-refactor y verificación verdes
devuelven `INTERNAL_RESULT: CONTINUE` al driver, sin respuesta final ni
`Siguiente:`. Un fallo/gate duro devuelve `INTERNAL_RESULT: BLOCK`; un
destructivo, scope nuevo o decisión de builder devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo estos dos resultados liberan el
turno.

## Pasos

0. Clasificar tier de rigor con `GUARDRAILS_MASTER.md` → "Graduación de Rigor por Tier" y verificar/crear la entrada correspondiente en `03_project_state.json → work_tiers` si el cambio entra al proyecto.
   - Consultar primero el contexto de implementación. Si devuelve `NEEDS_INPUT`
     por preparación, producir el requisito mediante su workflow autorizado y
     reconsultar; no escribir código durante esa fase.
   - Antes de CODE en `tier_2`, ejecutar
     `python3 .agent/scripts/workflow_receipts.py approval <módulo>`. Un FAIL
     devuelve `DECISION_REQUIRED`; no basta con recordar una aprobación del chat.
     `tier_1` no requiere un receipt formal de aprobación, aunque conserva la
     clasificación, el inventario y los gates aplicables.
1. Elegir una rebanada vertical pequeña desde `04_active_scope.md` y `08_module_acceptance.md`.
2. Ejecutar `.agent/workflows/preflight-review.md` sobre la rebanada y el plan TDD antes de CODE.
   - Si `PLAN_REVIEW: fail`, corregir el plan o ejecutar el workflow recomendado antes de escribir tests/codigo.
   - Si hay dispatcher sin aislamiento read-only, usar revisor distinto y
     declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION`; solo sin dispatcher declarar
     `SECUENCIAL_NO_AISLADO`.
3. Ejecutar `.agent/workflows/cambio-seguro.md` hasta completar el inventario `PRESERVAR` y clasificar el cambio.
   - El `PLAN_REVIEW` del paso 2 es consumible por cambio-seguro (receipt): con el mismo plan y scope, cambio-seguro NO repite el preflight — un solo review por flujo.
   - Si el cambio es `DESTRUCTIVO`, detenerse hasta tener aprobación explícita del usuario.
   - Si no hay aprobación, conservar comportamiento público y adaptar la implementación.
   - Toda pausa que pida decisión al builder (esta y la del gate de diff del paso 5) se presenta con la plantilla de `decision_translation_core.md` — mecánico, no opcional (GW FW-043): qué significa, qué recomiendo, alternativas; nunca un "¿qué prefieres?" a secas.

   <!-- CONTEXT_SOURCE: approval_receipt -->
   La aprobación formal es obligatoria antes de CODE solo para `tier_2` y se
   valida por fingerprint de scope, ACs, riesgos y ADRs. `tier_1` no se bloquea
   por ausencia de APPROVE_RECEIPT; ningún tier puede saltarse sus validaciones
   restantes ni convertir una consulta de contexto en autorización.
   <!-- END_CONTEXT_SOURCE: approval_receipt -->
4. Escribir un test que falle por la razón esperada.
   - Para bugs de regresión, el test debe cubrir el comportamiento que se perdió.
   - Para refactors, añadir o identificar tests que protejan al menos un elemento crítico de `PRESERVAR`.
5. Implementar el cambio mínimo para ponerlo verde.
   - Aplicar el CONTROL DE ALCANCE DEL DIFF (`cambio-seguro.md` paso 4.5): si
     supera la señal cuantitativa, revalidar scope y trocear autónomamente cuando
     convenga. No pedir aprobación solo por líneas/archivos; pausar únicamente
     ante scope nuevo, cambio destructivo, producto observable o imposibilidad de
     revisión fiable.
6. Refactorizar manteniendo el test verde y sin quitar elementos del inventario `PRESERVAR`.
   - El gate de tamaño aplica también aquí: el refactor cuenta dentro del mismo diff.
7. Ejecutar la verificación anti-regresión de `.agent/workflows/cambio-seguro.md`.
7.5. EVIDENCIA REAL, NO NARRATIVA (FW-030)
   - "Verde" exige el **exit code real** de cada check crítico, no un resumen. Reportar el comando y su `rc` (p. ej. `python3 -m unittest ... → rc=0`). Un check que no terminó en `rc=0` no es verde, aunque el output parezca pasar.
   - Dependencias de plataforma (watchers de filesystem, FSEvents/inotify, GUI, red): si un test depende de ellas, usar un fallback testeable (p. ej. `PollingObserver` en vez del observer nativo, o inyección por factory) antes de aceptar la evidencia. Un fallo tipo `Cannot start fsevents stream` invalida el verde aunque otros tests pasen.
   - **Distinguir fallos por permisos (sandbox) de fallos del producto** — si un check falla con error de permisos (PermissionError, "access denied", etc.), eso no es evidencia del producto: es limitación del sandbox. Reejecuta en el entorno autorizado si está disponible. Si pasa, registra `— verificación: (reejecutada en entorno autorizado) rc=0`; si falla de otra forma, es hallazgo del producto.
8. Registrar avance en ACs (con evidencia concreta: `— evidencia: [test/comando/output con rc]`), contratos o mapa de dependencias si aplica.

9. SALIDA
   Con `INVOCADO_POR_CICLO: true`, devolver `INTERNAL_RESULT: CONTINUE` y dejar
   que el driver invoque cierre; no emitir respuesta final. En standalone,
   cerrar con:
   Siguiente: /ciclo Llevar el módulo a cierre con todos los gates (si /tdd corrió dentro de /ciclo, el driver continúa solo).
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
