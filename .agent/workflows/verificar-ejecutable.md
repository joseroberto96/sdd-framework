---
description: Verificación ejecutable del módulo — feedback loop real exigido por /cierre-modulo paso 3.8. Detecta tipo de módulo y ejecuta verificación con MCP si está disponible, o fallback manual reproducible
---

# Verificar Ejecutable — Feedback Loop del Cierre

**No es comando directo.** Invocado por `/cierre-modulo` paso 3.8 (EJECUTABLE GATE). Su objetivo es demostrar que el módulo funciona realmente, no solo que tests verdes pasaron.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, una verificación `ok_automatizada`, `ok_manual`
o `no_aplica` válida devuelve `INTERNAL_RESULT: CONTINUE` sin respuesta final ni
`Siguiente:`; `fallida`/`diferida` devuelve `INTERNAL_RESULT: BLOCK`; si
el smoke necesita una elección o input indispensable del builder, devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo los dos últimos liberan el turno.

## Principio

<!-- CONTEXT_SOURCE: runtime_execution -->
La verificación ejecutable demuestra comportamiento real después de tests; no
se sustituye por una narración ni por un receipt fabricado. Registrar comando,
resultado observado, código de salida cuando exista y evidencia suficiente.
Si el código cambia, el receipt queda stale y debe repetirse.
<!-- END_CONTEXT_SOURCE: runtime_execution -->

El workshop SDD insiste en que "lo más importante" para obtener calidad real es que el agente tenga forma de verificar su trabajo (Boris Cherny, ADR-015). Tests pasando no equivalen a funcionalidad demostrada. Este workflow exige una evidencia ejecutable adicional al EVIDENCE GATE de ACs.

## Pasos

0. CONTEXTO MÍNIMO
   Leer:
   - `_project/03_project_state.json → module_specs[módulo]` (saber qué specs aplican)
   - `_project/03_project_state.json → active_mcps` (saber si Playwright/Sentry/otros están activos)
   - `_project/08_module_acceptance.md` (ACs del módulo a verificar)
   - `_project/10_arch_constraints.md` (tipo de aplicación, stack)
   - `_project/02_technical_plan/api_contract.yaml` si `module_specs.api_contract ∈ {required, done}` — fuente de endpoints y schemas
   - `_project/15_environment_profile.md` si existe (comandos para arrancar entorno)

1. DETECTAR TIPO DE VERIFICACIÓN
   Clasificar el módulo según su naturaleza primaria (un módulo puede caer en múltiples):

   | Tipo | Señales | Verificación esperada |
   |---|---|---|
   | `http` | `module_specs.api_contract ∈ {required, done}`, endpoints en `api_contract.yaml` | curl/httpie a endpoints reales con payload realista; verificar status code, response schema y al menos un error documentado |
   | `ui_web` | Stack frontend declarado en `10_arch_constraints.md` y módulo añade interfaz | Playwright (si MCP activo) o screenshot manual con flujo descrito en `_scratch/` |
   | `cli` | Módulo expone comando ejecutable (script, binario, comando de framework) | Ejecutar con args mínimos + verificar exit code 0 y stdout coherente |
   | `job_background` | Worker, cron, queue consumer | Trigger manual + verificar artefacto producido (registro DB, archivo, log) |
   | `migration_db` | Cambio de schema, migración en directorio dedicado | Ejecutar `up` en sandbox + `down` + `up` (idempotencia); verificar estructura resultante |
   | `internal_lib` | Sin interfaz HTTP/UI/CLI; función pública consumida por otros módulos | Smoke test que invoca la función con caso real del dominio + verifica salida |
   | `doc_config` | Solo Markdown/YAML/JSON sin lógica | `module_specs` debe estar marcado `n/a` en `data_model` y `api_contract`; NO requiere verificación ejecutable |

   Si el módulo cae en `doc_config` puro → SALIDA inmediata con `verificacion: "no_aplica"` y razón. `/cierre-modulo` lo aceptará.

2. INTENTAR VERIFICACIÓN AUTOMATIZADA
   Para cada tipo detectado, intentar primero la ruta automatizada:

   **Runtime obsoleto produce falso negativo — confirmar antes de ejecutar
   cualquiera de las rutas de abajo.** Un servidor de desarrollo con proceso
   vivo desde antes del cambio, o un estático servido desde caché, produce un
   resultado que no corresponde al código actual — ni éxito ni fallo bajo esas
   condiciones cuentan como evidencia. Reiniciar el proceso (o
   `preview_stop`/`preview_start` si aplica) tras tocar backend con estado de
   proceso; forzar recarga sin caché (`cache: 'reload'` + recarga dura) tras
   tocar estáticos servidos. Repetir la verificación después de eso.

   **Distinguir fallos por permisos (sandbox) de fallos del producto** — si se ejecuta un comando y falla con error de permisos (PermissionError, "access denied", etc.), eso no es evidencia de que el producto falle: es una limitación del sandbox. Reenrocar el comando en el entorno autorizado (si está disponible) antes de contar el resultado como fallo del producto. Si se reejecutó y pasa, registrar "— verificación ejecutable: (reejecutada en entorno autorizado)".

   **http:**
   - Leer endpoints del módulo en `api_contract.yaml` (vía skill `spec_contracts`).
   - Para cada endpoint nuevo o modificado: ejecutar `curl -X [MÉTODO] [URL] -d '[payload realista derivado del request schema]'`.
   - Capturar status code, headers relevantes, body.
   - Comparar con response schema declarado. Si diverge → reportar como hallazgo.
   - Provocar al menos un error documentado (ej: 404, 422, 401) para confirmar que el manejo existe.

   **ui_web:**
   - Si `playwright` ∈ `active_mcps`: lanzar navegación al flujo principal del módulo, capturar screenshot inicial, ejecutar interacción crítica, screenshot final.
   - Si NO está activo → saltar al paso 3 (fallback manual).

   **cli:**
   - Ejecutar el comando con argumentos del happy path.
   - Capturar exit code, stdout, stderr.
   - Ejecutar con al menos un input inválido para confirmar mensaje de error útil.

   **job_background:**
   - Disparar el trigger (función, evento, cron manual).
   - Verificar que el artefacto esperado se produce (consulta DB, archivo, log line).

   **migration_db:**
   - En sandbox: ejecutar migración `up`. Verificar tablas/campos esperados.
   - Ejecutar `down`. Verificar reversión limpia.
   - Ejecutar `up` de nuevo (idempotencia). Confirmar sin errores.

   **internal_lib:**
   - Invocar la función pública con un caso concreto del dominio.
   - Verificar el output contra valor esperado.

   Si la ruta automatizada produce evidencia → adjuntar al paso 4 y salir con éxito.

   Si la ruta automatizada falla por **dependencia ausente** (MCP no activo, herramienta no instalada, entorno no arrancable): pasar al paso 3.

   Si la ruta automatizada falla por **defecto del código** (el módulo no funciona): reportar el fallo concreto al usuario y devolver control a `/cierre-modulo` sin marcar evidencia. El módulo NO debe cerrarse.

3. FALLBACK MANUAL REPRODUCIBLE (ADR-015)
   Cuando la verificación automatizada no es posible, producir un script reproducible que el usuario puede ejecutar manualmente:

   Crear `_scratch/verify_<módulo>.md` con:
   ```markdown
   # Verificación manual — [módulo]

   ## Razón del fallback
   [Por qué no se pudo automatizar: MCP X no activo, herramienta Y no instalada, etc.]

   ## Prerrequisitos
   - [comando para arrancar entorno]
   - [datos de prueba necesarios]

   ## Pasos
   1. [paso concreto con comando o navegación exacta]
   2. [paso concreto]
   ...

   ## Resultado esperado
   - [observación 1 verificable]
   - [observación 2 verificable]

   ## Resultado observado (rellenar tras ejecución)
   - [pendiente]
   ```

   Pedir al usuario que ejecute los pasos y reporte el resultado observado. Solo cuando el usuario confirme que el resultado coincide con lo esperado → considerar verificación cumplida.

4. ADJUNTAR EVIDENCIA SUPLEMENTARIA
   La evidencia ejecutable producida aquí es **suplementaria**, no sustituye al EVIDENCE GATE del paso 1.c de `/cierre-modulo`. Esa evidencia primaria (test path, comando + output observado, file:line) debe existir antes de llegar al paso 3.8. Si un AC marcado `[x]` no tiene `— evidencia:` válida cuando entra al paso 3.8, el cierre ya está bloqueado por el paso 1.c y este workflow no se ejecuta.

   La función de este paso es **enriquecer**, no rescatar:

   a) **Para ACs ya con evidencia válida** que esta verificación complementa (ej: el AC dice "evidencia: tests/auth_test.py::test_login" y aquí se ejecutó un curl real al endpoint): añadir línea adicional en la entrada del AC con prefijo `— verificación ejecutable:` para no sobrescribir la evidencia primaria.
     Ejemplo:
     ```
     - [x] AC: el endpoint POST /login devuelve 200 con credenciales válidas — evidencia: tests/auth_test.py::test_login_ok
       — verificación ejecutable: curl -X POST [URL] -d '...' → 200 + body coherente con response schema (fecha [ISO8601])
     ```

   b) **Para evidencia ejecutable que no encaja en ningún AC existente** (ej: smoke test del módulo entero, screenshot UI, output de migración up/down): registrar como entrada nueva en `_project/05_test_history.md` con el módulo, fecha, tipo de verificación, comando/pasos, resultado observado, MCP usado o "manual". NO inventar ACs nuevos retroactivamente.

   c) **Si la verificación detectó divergencia entre código y spec** (respuesta no coincide con schema declarado, entidad usada sin estar en data_model.md, etc.): NO escribir evidencia. Reportar al usuario el hallazgo concreto y devolver `verificacion: "fallida"` en el paso 5 — el módulo no debe cerrarse.

   Si no había AC que cubriera la verificación realizada Y tampoco hay entrada en `05_test_history.md` que merezca crearse, dejar nota en `_project/13_session_journal.md` solo si `framework_mode = initialized`.

4.bis SELLAR EL RECIBO (FW-144 — obligatorio)
   Una verificación narrada y una ejecutada producen el mismo texto. Antes de
   devolver el resultado, sellar lo que realmente ocurrió:

   Llamar `mutate_state` con action `record_execution_receipt` y payload:
   ```json
   {
     "module": "[module_id]",
     "kind": "ok_automatizada | ok_manual | no_aplica",
     "command": "[el comando exacto que se ejecutó, o los pasos del fallback manual]",
     "exit_code": 0,
     "tests_run": 12,
     "evidence": "[salida real observada — status code + body, stdout, screenshot path, confirmación del usuario]"
   }
   ```
   `tests_run` (opcional) es cuántos tests corrieron: 0 no cierra el módulo, y
   una herramienta ausente en la evidencia (`command not found`) tampoco (FW-299).

   Reglas que el motor aplica y no se pueden rodear:
   - `ok_automatizada` y `ok_manual` exigen `command` no vacío y `evidence` de
     ≥20 caracteres.
   - `exit_code` **solo** aplica a `ok_automatizada`, donde es obligatorio y un
     valor distinto de 0 bloquea el cierre. En `ok_manual` se **omite** el campo:
     un fallback confirmado por el builder no tiene código de salida, y rellenar
     un `0` de mentira para pasar el gate es justo lo que este receipt existe
     para impedir. Ahí la evidencia es la confirmación del builder.
   - `no_aplica` solo se acepta si ninguna de las specs del módulo
     (`data_model`, `api_contract`, `integration_real`) declara superficie: cada
     una debe estar en `n/a` o ausente (doc_config puro). Una sola en
     `required`/`done` significa que hay algo que ejecutar, y bloquea.
   - El receipt fija el `diff_hash` actual: si el código cambia después, queda
     `stale` y hay que repetir el paso 3.8 sobre el código nuevo.
   - `fallida` y `diferida` NO producen receipt: bloquean antes.

   Verificar con `python3 .agent/scripts/workflow_receipts.py execution <módulo>`.
   Sin `RECEIPT GATE: OK`, el cierre no puede continuar.

5. SALIDA
   Devolver a `/cierre-modulo` paso 3.8 uno de estos resultados:

   Con `INVOCADO_POR_CICLO: true`, mapear además el resultado al recibo anidado:
   éxito/no_aplica → `INTERNAL_RESULT: CONTINUE`; fallida/diferida →
   `INTERNAL_RESULT: BLOCK`; elección o input indispensable del builder →
   `INTERNAL_RESULT: DECISION_REQUIRED`. No emitir respuesta final.

   Resultados que **permiten avanzar** al paso 4:
   - `verificacion: "ok_automatizada"` — la verificación pasó vía MCP/herramientas Y dejó evidencia suplementaria en al menos un AC (línea `— verificación ejecutable:`) o una entrada en `_project/05_test_history.md`. Si no quedó rastro escrito, este resultado no es válido: devolver `"fallida"` con razón "verificación ejecutada sin registrar evidencia suplementaria".
   - `verificacion: "ok_manual"` — fallback manual reproducible confirmado por el usuario Y registrado: línea `— verificación ejecutable:` en AC con referencia a `_scratch/verify_<módulo>.md`, o entrada en `_project/05_test_history.md`. Mismas reglas que el caso automatizado.
   - `verificacion: "no_aplica"` — módulo tipo `doc_config` puro (ambas specs `n/a`); no requiere demostración ejecutable ni registro en `05_test_history.md`.

   Resultados que **bloquean** el cierre:
   - `verificacion: "fallida"` — el módulo no funciona como debería. Devolver el reporte concreto al usuario y mantener `module_statuses[module] = "in_progress"`. No registrar `closure_rejection` (no es fallo de proceso sino de implementación).
   - `verificacion: "diferida"` — el usuario quiso posponer la verificación. Si el gate puede saltarse, deja de ser gate. Registrar la deuda en `_project/13_session_journal.md` (cuando `framework_mode = initialized`) Y en `_project/04_active_scope.md` sección "Deuda Técnica" como tarea pendiente, devolver control a `/cierre-modulo` con resultado bloqueante. El módulo NO cierra hasta resolverla.

## Reglas

- **No bloquear por falta de MCP.** El fallback manual reproducible siempre es válido (ADR-015).
- **Bloquear si el módulo no funciona.** Tests verdes sin verificación ejecutable real no equivalen a entrega.
- **No modificar ni completar `— evidencia:` primaria.** Esa evidencia la produce el flujo de implementación/tests y la valida el EVIDENCE GATE del paso 1.c de `/cierre-modulo`. Aquí solo se **añade** una línea `— verificación ejecutable:` a ACs que ya pasaron ese gate, o se registra la verificación en `_project/05_test_history.md` cuando no encaja en un AC. Si un AC carece de evidencia primaria, no se rescata desde este workflow — el cierre ya quedó bloqueado en el paso 1.c.
- **No reescribir ACs ni inventar ACs nuevos retroactivamente.** Si la verificación cubre algo que ningún AC declara, va a `_project/05_test_history.md`.
- **No tocar `module_specs` ni `module_statuses`.** Esos cambios pertenecen a `/cierre-modulo` paso 6.
- Devolver a `/cierre-modulo` el resultado. Solo en standalone cerrar con:
  Siguiente: /ciclo Continuar el gate de cierre del módulo y llevarlo a cierre.
  Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
