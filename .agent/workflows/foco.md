---
description: Cambia el foco activo a un área del producto (frontend, backend, data, seguridad, testing) — sube la verbosidad y rutea al skill core de esa superficie
---

# /foco <área>

Eres el agente que enruta el foco en este proyecto.

`/foco` cambia el **foco activo** del agente: la superficie del producto en la que
el builder quiere profundizar durante la sesión. Con foco activo, el agente
eleva la verbosidad (pregunta más, muestra más, co-diseña) y **rutea al skill
core** que YA existe para esa área. No es un selector de persona, ni de agente,
ni de subagente: es una lente de interacción. Sin foco, el agente opera en modo
general (decide lo técnico solo, pregunta lo esencial).

> **Cuándo usarlo:** el builder dice "ahora quiero profundizar en la UI",
> "vamos al backend", "pon el foco en testing", "/foco frontend" — explícito o
> tácito. Si no hay foco declarado, el agente trabaja en modo general; no asume
> un foco por defecto.
>
> **Cuándo NO usarlo:** para cambiar de módulo de implementación
> (`/scope-siguiente`); para profundizar en una decisión técnica concreta
> (ya hay routing en `decision_translation_core`); para asignar trabajo a
> otro agente o subagente (los subagentes `reviewer` o `preflight-reviewer`).

## Mapa de áreas (cerrado — reusa skills core ya existentes)

| Área (acepta)              | Skill core principal       | Skills de apoyo            |
|----------------------------|----------------------------|----------------------------|
| `frontend` / `ui` / `ux`   | `frontend_design`          | `cross_platform_web`       |
| `backend`                  | `architecture_core`        | `security_core`            |
| `data`                     | `architecture_core`        | —                          |
| `seguridad` / `security`   | `security_core`            | —                          |
| `testing` / `qa`           | `quality_testing_core`     | —                          |
| `devops` / `deploy` / `infra` | `deployment_core`       | `architecture_core`        |

Área no reconocida → **preguntar al builder** a qué skill quiere mapearla (no
inventar). El framework no tiene un "árbol de profiles" ni áreas derivadas:
reusa el catálogo core (`_project/19_core_skills.md`). Esto es deliberado —
extender el mapa se difiere hasta que un proyecto real lo demande (Fase 2+).

## Pasos

1. **LEER FOCO PREVIO**
   - Llamar a la herramienta MCP `get_work_context` con `{"view":"startup"}`
     y leer `data.summary.active_focus`. Consultar `get_full_state` o el JSON
     canónico solo si la vista reporta una inconsistencia explícita.

2. **VALIDAR ÁREA**
   - Normalizar el argumento a minúsculas: `frontend` | `ui` | `ux` |
     `backend` | `data` | `seguridad` | `security` | `testing` | `qa` | `null`.
   - Si el área no está en la tabla de arriba y no es `null` → **detenerse y
     preguntar al builder** qué área quiere y a qué skill mapearla. No inventar.
   - Si la invocación es `/foco` o `/foco null` → foco = null (limpia el foco).
   - Si la invocación es `/foco off` / `/foco salir` → foco = null (alias).

3. **PARQUEAR ÁREA PREVIA (si cambió)**
   - Si el `active_focus` previo difiere del nuevo y el previo no es null,
     escribir en `_project/13_session_journal.md` una línea con tag
     `[área: <anterior>]` resumiendo el estado en curso de esa área:
     ```
     - [timestamp ISO] [tipo: focus] [área: <anterior>] Se cambió el foco
       activo a <nuevo> (o null). Estado parqueado: <1 línea: módulo activo,
       decisión pendiente, último cambio>.
     ```
   - El reusa el journal existente; no se introduce worktree ni
     paralelización. Una sola sesión, un solo foco, secuencial.

4. **MUTAR FOCO**
   - Llamar a `mutate_state` con `action: "set_focus"` y payload
     `{"area": "<área normalizada>"}` o `{"area": null}` para limpiar.
   - Confirmar que el retorno indica éxito. El render regenera `core.md`
     (proyección de foco, ver WI-5) y `00_session_brief.json`.

5. **RUTEAR AL SKILL CORE**
   - Activar la skill core principal del mapa (paso 2). Si hay skills de
     apoyo, mencionarlas pero no activarlas a ciegas — se activan por señal.
   - A partir de aquí, en esta sesión, las salidas al builder en el área
     de foco:
     - Abren con "qué significa para ti" (aplicar `builder_language_core`).
     - Incluyen recomendación fundada en la skill core de la superficie.
     - Muestran alternativas y trade-offs del dominio.
     - Co-diseñan con el builder, no solo deciden en silencio.
    - La regla "se nota desde fuera" se preserva intacta: el foco **no
      convierte decisiones internas en decisiones de builder**; solo sube la
      verbosidad cuando sí lo son. La subsección "Checkpoint por blast radius"
      de `decision_translation_core` sigue aplicando igual.
    - **Coexistencia con reglas por ruta (Claude Code — Salida 1):**
      En harnesses con reglas por ruta nativas (`.claude/rules/`), la relación es
      ortogonal: `/foco` gobierna la lente conversacional, estratégica y de etapa
      con el builder; las reglas por ruta gobiernan los invariantes mecánicos de
      archivo (CSS, unidades `dvh`, chunking, eventos de puntero). No hay solape
      porque `/foco` activa las obligaciones de etapa y el harness inyecta las
      de fichero al abrir el código.

6. **APLICAR CHECKPOINT POR BLAST RADIUS (tier_2)**
   - Antes de implementar cualquier cambio clasificado `tier_2` (ver
     `03_project_state.json → work_tiers[id]`), producir un resumen de
     checkpoint: qué se va a hacer, qué significa para ti, por qué este
     tier, qué se rompe si se deshace. Aplicar la semántica vigente de
     `decision_translation_core`: con `APPROVE_RECEIPT` dentro de `/ciclo` es
     informativo y continúa; sin recibo vigente devuelve decisión requerida.

## Verificación post-foco (rápida)

Tras mutar, comprobar:
- `python3 .agent/scripts/state_manager.py render` regenera `core.md` con
  la línea "Foco activo: <área>" (o "sin foco").
- El skill core del mapa existe en `.agent/skills/` (no es instalación
  externa; ya está en el template).
- Si el cambio fue `null`, `active_focus` queda en null y `core.md` muestra
  "sin foco".

## Anti-patrones (NO)

- **NO** crear `.agent/areas/` ni un árbol de profiles. El plan es
  explícito: reusar skills core, no inventar superficie.
- **NO** instalar skills externas para cubrir áreas. Si el área no tiene
  skill core, preguntar al builder (paso 2).
- **NO** convertir el foco en un compromiso de scope. Cambiar de foco
  parquea el anterior, no lo borra.
- **NO** paralelizar trabajo por foco. Una sesión, un foco, secuencial.
- **NO** usar foco como sustituto de `/scope-siguiente` (que cambia el
  módulo activo, no la lente de interacción).

## Mensaje al builder

Tras ejecutar, devolver (en español, con `builder_language_core`):

```
Foco activo: <área> — skill ruteada: <skill core principal>.
Si quieres profundizar en otra superficie: /foco <área>.
Si quieres volver al modo general: /foco.
```

El cierre repite tal cual el siguiente paso vigente de la sesión; solo si aún no hay, usar esta plantilla (FW-318). Emitir en texto plano, sin bloque de código:

Siguiente: /ciclo <instrucción concreta del trabajo en curso, con la nueva lente>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
