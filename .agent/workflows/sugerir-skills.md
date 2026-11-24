---
description: Detección unificada de expertise (4.1.0, FW-072) — sub-dominios del producto → expertos proactivos; señales técnicas → skills. Propone con borrador listo, nunca instala ni bloquea.
---

# Detección de Expertise — expertos proactivos + radar de skills

> Un solo mecanismo para "falta expertise → proponer pieza" (ADR-048). La mitad
> nueva (4.1.0): el agente detecta los sub-dominios que el producto va revelando
> y **propone expertos con el borrador ya redactado** — la carga está invertida:
> el builder revisa y corrige, no redacta. Lo invocan `/diseñar` (paso 3.5),
> `/enriquecer-ticket` (paso 0.7) y el gate suave de `/cierre-modulo`.

Usar al diseñar, al enriquecer un ticket, al inicio de sesión ante señales
claras, o cuando el agente se sienta limitado por falta de criterio.

## Parte A — Sub-dominios del producto → expertos de dominio (PRIMERO)

> **Camino único** (4.1.0 endurecido, FW-131): esta Parte A es la ÚNICA vía de
> nacimiento de expertos de dominio en todo el framework — incluida la
> invocación desde `/nuevo-proyecto` paso 7. No existe un segundo camino con
> formato propio; un experto nacido en cualquier momento usa
> `.agent/skills/domain/_TEMPLATE.md`.

0. LECTURA MÍNIMA — ancla según el momento de invocación
   - `_project/09_domain_profile.md` (dominio, sub-dominio, expertos_activos,
     barrido previo si existe)
   - Listado de `.agent/skills/domain/` (expertos que ya existen)
   - **Si `_project/01_product_requirements.md` existe** (invocación desde
     `/diseñar`, `/enriquecer-ticket`, gate suave de `/cierre-modulo`): esa es
     el ancla, junto con `_project/04_active_scope.md` (qué se toca ahora).
   - **Si NO existe todavía** (invocación desde `/nuevo-proyecto` paso 7 —
     el PRD nace después): el ancla son las respuestas de la entrevista de
     inicialización — descripción del problema, dominio + sub-dominio,
     usuarios objetivo, quick win y criticidad. Declarar explícitamente cuál
     de los dos anclajes se está usando antes de barrer.

1. BARRIDO OBLIGATORIO CON VEREDICTO ENUMERADO (no detección oportunista)
   Preguntarse: **¿qué ramas de conocimiento involucra este producto/scope que
   NO cubren ni las skills core (superficie técnica: arquitectura, seguridad,
   calidad, frontend, deployment) ni los expertos activos?**

   El barrido en sí es el artefacto, no solo su resultado positivo. Todo
   sub-dominio identificado — incluido el que se descarta — se registra como
   una fila en la tabla "Barrido de sub-dominios" de `09_domain_profile.md`
   con veredicto `propuesto` o `descartado + razón`, y la cita concreta del
   ancla (PRD/scope o respuestas de init) que lo dispara. Un barrido que no
   detecta nada real se registra igual, con la fila `sin sub-dominios fuera
   de las skills core — [razón]`: el silencio deja de ser una salida barata
   porque su ausencia es visible en el artefacto.

   Calibración del juicio (known-good / known-bad):
   - ✅ Software para un estudio de arquitectura → proponer ingeniero civil
     (estructural), ingeniero hidrosanitario, ingeniero geológico, arquitecto
     de interiores, paisajista — según qué toque el scope ACTUAL.
   - ✅ Bot de finanzas personales → experto en clasificación financiera,
     experto en seguridad de bots de mensajería.
   - ❌ CRUD genérico de tareas sin dominio especial → registrar la fila "sin
     sub-dominios" (las skills core bastan; proponer un experto aquí es ruido).
   - ❌ NO proponer un experto cuyo sub-dominio no aparece en el ancla vigente
     (anti-fabricación: la detección se justifica citando la sección concreta
     que la dispara — PRD/scope, o la respuesta de init correspondiente).

   Si el barrido corre en el contexto de un módulo concreto (module_id
   conocido — invocación desde `scope-siguiente`, `enriquecer-ticket` o el
   EXPERT GATE de `/cierre-modulo`), sellarlo con `mutate_state` action
   `record_checkpoint` con `{"module": "[module]", "kind": "domain_sweep",
   "scope_id": "[module]", "evidence": "[resumen del barrido: N sub-dominios
   evaluados]", "reference": "09_domain_profile.md fila(s) [module]"}` (FW-134:
   el gate mecánico de `/cierre-modulo` paso 3.9 exige este recibo en tier_2,
   no solo la fila en la tabla).

2. PROPONER CON BORRADOR LISTO (carga invertida)
   Para cada sub-dominio con veredicto `propuesto`:
   - Redactar el borrador COMPLETO usando `.agent/skills/domain/_TEMPLATE.md`
     (conocimiento base, anti-patrones, invariantes candidatos, sub-dominio
     que cubre, origen — proactivo o manual).
   - Presentar al builder con la plantilla obligatoria de checkpoint (3.19.0,
     `decision_translation_core`): qué aporta el experto en lenguaje llano,
     analogía, recomendación, y opciones (agregar / agregar ajustado / no
     agregar — con su trade-off).
   - Formato de la propuesta: "Te propongo agregar [experto X, Y, Z] porque
     aportan [A, B, C]" — nunca "¿quieres crear un experto?" sin borrador.
   - Si el builder confirma → escribir `.agent/skills/domain/[nombre].md` y
     registrar en `09_domain_profile.md → expertos_activos`, consistente con
     la fila `propuesto` del barrido. El enriquecimiento posterior con
     fuentes reales va por `/añadir-experto` (modo B).
   - Si el builder rechaza uno que el barrido marcó `propuesto` → actualizar
     esa fila a `descartado: [razón dada por el builder]` para que el gate
     suave de `/cierre-modulo` no vuelva a proponerlo.

3. USAR AL EXPERTO (que viva, no que exista)
   - Al decidir dentro de su sub-dominio, citar al experto:
     `register_decision` con `"expertise": "domain/[archivo]"`.
   - Al toparse conocimiento nuevo del sub-dominio, proponer enriquecerlo
     (🌱 → 📚) — no dejarlo congelado en base IA.

## Parte B — Señales técnicas → skills (radar clásico)

4. DETECTAR SEÑALES TÉCNICAS
   UI/UX, JS/CSS, seguridad, testing, performance, sobreingeniería (YAGNI),
   deployment/operación, temas `pendiente` en `_project/21_knowledge_map.md`.

5. REVISAR SI YA EXISTE
   - Skill core de `_project/19_core_skills.md` cubre la señal → activarla, no proponer.
   - Sobreingeniería/minimalismo → activar `.agent/skills/minimal_solution_core.md`
     antes de proponer Ponytail externo (solo si el usuario pidió sus comandos).
   - Skill importada `aprobado` → usarla. Recomendación `rechazada` → no insistir.
   - Falta de claridad de producto → `/diseñar` antes que skill externa.

6. PROPONER SIN BLOQUEAR (≤4 líneas)
   ```markdown
   Veo señal de [tipo]. Podría ayudar instalar una skill de [tipo] local.
   Beneficio: [1 línea].
   Si la apruebas, uso `/instalar-skills` con fuente exacta y registro en `14_skill_provenance.md`.
   ```
   Antes de fuentes no oficiales, sugerir el plugin oficial claude-code-setup
   ("recommend automations for this project"); si el usuario ya recibió sus
   recomendaciones, registrarlas en `14_skill_provenance.md` como `aprobado`.

7. REGISTRAR
   Propuestas de skill → `_project/17_skill_recommendations.md` (fecha, señal,
   recomendación, estado). Propuestas de experto → `09_domain_profile.md`.
   Si el usuario aprueba una skill → `.agent/workflows/instalar-skills.md`.

## Reglas

- No instalar ni escribir expertos automáticamente: siempre checkpoint primero.
- No proponer como muletilla: cada propuesta cita la señal del PRD/scope que la dispara.
- No repetir propuestas rechazadas (skill o experto).
- Bloquear solo si: el usuario pidió una skill no instalada, la tarea es de alto
  riesgo sin criterio local, o una dependencia es necesaria para compilar.
- Al cerrar no se emite un cierre nuevo: vuelve al `Siguiente:` vigente de la sesión y repítelo tal cual.

La interfaz, los logs y los textos para el usuario van en español.
