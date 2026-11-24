---
description: Cosecha la fricción del framework registrada durante el proyecto — consolida el field report con señales duras del estado y clasifica candidatos a retroportar
---

# Retrospectiva del Framework — Cosecha de Fricción

Usar al cerrar un proyecto o un hito grande. Recopila lo que el framework dejó
como fricción durante el uso real y produce candidatos accionables a retroportar
al repo del framework. NO modifica el repo del framework (son repos separados);
produce el informe que el usuario lleva al mantenimiento.

## Reglas de seguridad

- Solo lectura sobre el proyecto, salvo marcar entradas cosechadas en
  `_feedback/field_report.md` y anexar el resumen a `_feedback/retrospectives.md`.
- No red, no push, no instalar. No tocar `_project/` ni el código del producto.

## Pasos

0. Si no existe `_feedback/field_report.md` y no hay señales duras (paso 2),
   reportar "sin fricción registrada en este proyecto" y terminar.

1. LEER ANOTACIONES (capa cualitativa)
   Leer `_feedback/field_report.md`: entradas FW-NNN no marcadas como cosechadas.
   Las entradas nuevas de un adoptante (`initialized`) usan `<PREFIJO>-FW-NNN`
   (el `fw_prefix` declarado en `_project/15_environment_profile.md`) para no chocar
   con la numeración de la plantilla; las antiguas sin prefijo no se renumeran.

   Leer también `_feedback/friction_buffer.md` si existe: clases de fricción
   acumuladas por la cosecha de sesión (`.agent/prompts/cosecha_sesion.md`) que
   aún no eran accionables una a una. **El conteo de instancias es la señal**:
   una clase que alcanzó el umbral (3 instancias o 2 proyectos/sesiones) es
   candidata a `FW-NNN` en esta cosecha, aunque ninguna instancia suelta lo
   justificara. Una clase con instancias en varios proyectos pertenece a la
   PLANTILLA, no al field report del adoptante — el cruce entre proyectos es
   justo lo que ningún agente ve desde dentro de una sesión.

2. MINAR SEÑALES DURAS (red de seguridad — funciona aunque nadie anotara)
   - `_project/03_project_state.json → closure_rejections`: rechazos de gates =
     fricción de cierre.
   - `_project/state_log.jsonl`: frecuencia de acciones — muchos
     `register_clarification` = ambigüedad estructural; ida y vuelta de
     `update_module_status` = retrabajo.
   - `_project/state_log.jsonl → ciclo_paused` (4.2.1, FW-075): las pausas del
     driver autónomo son la señal de fricción del modo autónomo. Pausas
     repetidas con la misma `reason` = fricción sistémica (gate que grita
     lobo) aunque nadie la haya anotado; cruzar contra los FW-NNN del field
     report — pausa recurrente SIN entrada FW = fricción perdida, generarla
     en la cosecha. También medir aquí la métrica del salto v4:
     interrupciones al builder por módulo, partidas en decisión de producto
     (legítima) vs pregunta técnica (fallo de autonomía).
   - `_project/06_bug_fixes.md` (`_bugs`): bugs que apunten a herramientas del
     framework, no al producto.
   - `_project/13_session_journal.md` y `_project/22_lessons.md`: pistas sueltas.

3. CONSOLIDAR
   Deduplicar y agrupar fricciones equivalentes. Una fricción anotada que además
   aparece como señal dura sube de prioridad.

4. CLASIFICAR por `.agent/docs/framework_evolution_policy.md`
   Para cada fricción: naturaleza (fallo objetivo | fricción subjetiva), tier por
   blast radius + reversibilidad (tier_0/1/2), y decisión de retroporte
   (inmediato | ≥2-proyectos | backlog).

5. PASE DE RESTA (4.12.0 — regla de resta de la política de evolución)
   Una cosecha que añade debe evaluar qué resta. Inventariar la superficie del
   framework usada realmente en este proyecto desde la cosecha anterior:
   - Fuentes de uso: `_project/state_log.jsonl` (workflows que mutaron estado),
     receipts de workflow, `_project/13_session_journal.md` (workflows citados).
   - Cruzar contra el catálogo (`.agent/workflows/manifest.json` + skills core):
     todo workflow/skill **sin uso registrado** es candidato.
   - Para cada candidato, proponer una decisión y pedirla al usuario:
     `mantener` (con razón explícita: p. ej. es de fase que este proyecto no
     alcanzó), `fusionar` (con cuál) o `borrar`.
   - La resta la decide el humano con datos delante — nunca borrar de forma
     automática, y nunca ejecutar el borrado aquí: la decisión viaja en el
     informe al repo del framework (este workflow sigue siendo read-only sobre
     el framework).

6. EMITIR
   - Marcar las entradas procesadas como `cosechada-[fecha ISO]` en
     `_feedback/field_report.md`.
   - Anexar el resumen a `_feedback/retrospectives.md` (crear si no existe).
   - Mostrar en el chat: tabla de fricciones clasificadas + lista de "candidatos
     a retroportar al framework" (los de retroporte inmediato primero) + tabla
     del pase de resta (candidato → decisión → razón).

Cerrar con: `Siguiente: ninguno — llevar los candidatos al repo del framework en el próximo mantenimiento.`

UI, logs y strings visibles al usuario deben estar en ESPAÑOL.
