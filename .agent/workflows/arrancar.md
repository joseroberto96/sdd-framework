---
description: Ritual de inicio de sesión — consulta la vista de arranque y presenta resumen accionable
---

Eres el agente de arranque de sesión de este proyecto.

## Lectura progresiva de contexto

**Modelo y esfuerzo:** se eligen al empezar y no se cambian durante la sesión (cambiar de modelo invalida la caché; cambiar el esfuerzo la conserva o no según el host: regla 7 de `.agent/rules/runtime_core.md`).

**Política de sesión (H4):** si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar`. Reanudar en frío cuesta 4–11× más tokens de caché (120K–379K) que arrancar (33K). El estado vive en ficheros; la sesión nueva no pierde nada que el framework no pueda reconstruir.

**Producto en producción (FW-225):** si la vista de arranque o `core.md` muestra
`Ciclo de vida: production`, presentar solo la **tarjeta**: qué es el producto,
stack, cambio activo (o "ninguno"), bloqueadores y la línea `Siguiente:` con
`/ciclo` (ajuste) o `/nuevo-bug`. Omitir fase, módulo activo, lecturas
extendidas y peek de specs. El trabajo sigue `.agent/workflows/mantenimiento.md`.

**Consumo del Hook de Continuidad (Claude Code, Lote 3):**
- Si el hook de continuidad ya entregó el sobre estructurado de `startup`:
  - Verificar vigencia: `session_id` coincide con la sesión actual, `vigencia.source_fingerprint` coincide con el estado actual en disco, y no han mediado mutaciones posteriores.
  - Interpretar los 5 campos: `completitud` (si `gate_status == "PASS"`, continuar; si `"BLOCK"`, detenerse; si `"NEEDS_INPUT"`, resolver referencias), `cursor` (si `completitud.complete == false`, paginar con `cursor`), `referencias` (abrir fuentes indicadas), `vigencia`, y `via_de_recuperacion`.
  - **Reutilización legítima de la primera página:** si el sobre es válido y reciente, **omitir la consulta repetida de `get_work_context`**, ahorrando un ciclo de herramientas y contexto.
  - Si el hook no estuvo activo, expiró, falló (`status != "OK"`) o devolvió `TIMEOUT`/`ERROR`, ejecutar la consulta explícita normal:

**Caso normal — usar la vista de arranque:**
- Llamar a la herramienta MCP `get_work_context` con `{"view":"startup"}`.
- Si la respuesta trae `next_cursor`, continuar solo con ese cursor; no solicitar
  `get_full_state` para ampliar una vista normal.
- La respuesta entrega identidad, continuidad, objetivo activo y conteos de
  bloqueos. `gate_status` no sustituye los gates de implementación o cierre.

**Fallback CLI — solo si el MCP no está activo:**
- Ejecutar `python3 .agent/scripts/state_manager.py query --view startup`.
- Si tampoco es viable, leer `_project/core.md` como proyección corta; abrir el
  estado completo únicamente para diagnosticar una inconsistencia concreta.

**Freshness check (A2) — antes de usar `core.md`, verificar que no esté stale:**
- Si `_project/core.md` existe y su mtime es anterior a `_project/03_project_state.json`, la proyección está desactualizada (sucedió una mutación sin render, o alguien editó el state a mano). Regenerar antes de leer:
  ```bash
  python3 .agent/scripts/state_manager.py render
  ```
- Si la diferencia es de pocos segundos (race tolerable durante una mutación activa), aceptar y continuar.
- Si la diferencia es > 60s, ejecutar el render y notificar al usuario en el resumen inicial: "regeneré core.md porque estaba X minutos stale respecto al state".
- Si el render falla, NO continuar con `core.md` stale: usar la consulta CLI de
  `startup` y avisar al usuario. No hacer un volcado completo como fallback
  silencioso.

**Modo template — si la vista devuelve framework_mode "template" (o core.md contiene "[NOMBRE_PROYECTO]"):**
- Leer solo lo necesario para confirmar `framework_mode: template`.
- No reescribir `_project/core.md`; la plantilla debe permanecer limpia.
- Presentar setup inicial y recomendar `/nuevo-proyecto` o `/migrar-proyecto`.

**Post-interruption audit — si hubo corte de conexión, contexto compactado dudoso, recap inconsistente, cambio de intención, petición contradictoria con el scope activo o el usuario pide "revisa qué se hizo":**
- Delegar en `.agent/workflows/reconciliar.md` y seguir su salida.
- Mientras dure la reconciliación: **no escribir**, **no red**, no pedir permisos de red, no instalar skills/MCPs, no hacer push.
- Requisitos mínimos heredados de `/reconciliar`: reconstruir verdad desde disco, ejecutar doctor/tests, revisar diff de ACs e inventariar artefactos esperados contra ACs activos.
- Solo después de este audit puede continuar implementación o publicación.

**Lectura extendida — solo al activar una etapa:**
- `_project/01_product_requirements.md` — si el módulo activo necesita contexto de producto
- `_project/03_invariants.md` — si la siguiente acción implica cambiar código existente
- `_project/19_core_skills.md` — si hay señales claras de UI/UX, seguridad, testing o dominio
- `_project/20_builder_profile.md` — si el nivel/preferencias del builder son relevantes
- Lecciones: si existe `_project/22_lessons_digest.md` (proyección generada por `lessons_reflect.py`), leer SOLO el digest — es la vista corta con decaimiento y corroboración; no cargar el archivo completo. Solo si el digest no existe, hacer peek de cabecera de `_project/22_lessons.md`.

**Peek de specs formales (cabecera) si la siguiente acción toca código:**
- `_project/02_technical_plan/data_model.md` — solo si `module_specs[módulo activo].data_model ∈ {required, done}`
- `_project/02_technical_plan/api_contract.yaml` — solo si `module_specs[módulo activo].api_contract ∈ {required, done}`
Lectura selectiva por módulo activo, vía skill `spec_contracts`. Si las specs aplican pero el módulo no las clasificó, advertir y derivar a `/scope-siguiente` o `/enriquecer-ticket`. No precargar cierre, reconciliación ni documentos de módulos futuros.

## Presentar resumen en ESPAÑOL

Aplicar `.agent/skills/builder_language_core.md`: explicar primero qué significa
para el builder y evitar abrir con jerga interna.

Con los datos de la vista de arranque (o core.md si se usó fallback), presentar:

1. **Fase actual** — nombre de la fase en curso
2. **Último evento** — qué se completó o decidió en la última sesión
3. **Módulo activo** — ID, estado, tier de rigor y descripción breve
3.5. **Recuento** — `Módulos: N completados · M usados por ti · P pendientes` (`completed_count`, `builder_used_count`, `pending_count`). «Completado» es código cerrado por el agente, no uso real (FW-275). Si `no_pending_work` es `true`, no presentarlo como «todo hecho»: avisar «no hay trabajo pendiente declarado; ¿la fase tiene criterio de salida cumplido?» (PA FW-136).
4. **Siguiente acción** — la tarea concreta del campo "Siguiente acción"
5. **Bloque de cierre** — las dos líneas de `decision_translation_core` → "Bloque de cierre de turno" (4.20.1, FW-141/142), en este orden. El comando es el que traiga `session.next_command`; `/ciclo` es solo el ejemplo. Emitir en texto plano, sin bloque de código:
   Siguiente: /ciclo <instrucción concreta, suficiente y necesaria>
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
   - Entregar **tal cual** el siguiente paso vigente (`session.next_command` + `session.next_action`): `/arrancar` muestra el estado, no cambia el comando ni hace de intermediario (FW-318). La instrucción solo se reescribe si es vaga (viñeta de autosuficiencia), si pasa de 40 palabras o si lleva prohibiciones («sin X ni Y»): entonces se redacta como una sola acción de hasta 40 palabras con el mismo comando y esa redacción se entrega en el cierre. `/arrancar` no muta el estado: el puntero guardado lo reescribe el siguiente `/gycd` (FW-402).
   - `/arrancar` nunca se devuelve a sí mismo: si `session.next_command` es `/arrancar` (puntero histórico o proyecto recién inicializado), elegir por el estado (en modo template manda el setup inicial de abajo): módulo activo o pendiente → `/ciclo` con instrucción concreta; producto o fase sin definir → `/diseñar`; sin trabajo declarado → la forma de bloqueo del contrato (una sola línea con «ninguno» y el motivo).
   - La línea `Siguiente` es **un solo string copiable**: comando + instrucción fusionados (`{session.next_command} {session.next_action}`), no dos campos separados que el builder tenga que combinar mentalmente (FW-141).
   - La instrucción debe ser **autosuficiente**: si el "Siguiente acción" de la vista (o core.md) es vago ("continuar con el módulo"), redactarla concreta aquí — cualquier modelo, sin el contexto de esta conversación, debe saber qué hacer.
   - Si `/comando` es maquinaria interna en vez de uno de los 9 verbos `builder_facing`, es un bug del workflow que lo dejó ahí — corregirlo en la fuente, nunca traducirlo aquí en silencio.
   - La línea `Modelo · Esfuerzo` se emite **aquí**, al devolver el turno, porque es el único momento en que el builder puede ajustar su entorno antes de que arranque el trabajo (FW-142). Omitirla solo si no hay tier clasificado todavía.
6. **Bloqueadores** — si existen; si no, omitir esta sección
7. **Skill sugerida** — solo si hay señal clara y concreta; máximo 2 líneas
8. **Contexto usado** — `startup` si bastó la vista de arranque (MCP o CLI); `projection` si se usó `core.md` como fallback; `diagnostic` si se requirió `get_full_state` (con `{"force": true}` si el estado supera 256 KiB) por inconsistencia concreta

Si la vista de arranque o core.md está en modo template o el proyecto acaba de inicializarse:
- Indicar que el proyecto está en setup inicial
- Recomendar como siguiente acción `/nuevo-proyecto` si el framework no está inicializado; `/diseñar` si ya está inicializado pero el producto no está claro

Format: bullet lists, conciso, accionable. Sin preámbulo. Sin explicaciones innecesarias.
UI, logs y strings visibles al usuario deben estar en ESPAÑOL.
