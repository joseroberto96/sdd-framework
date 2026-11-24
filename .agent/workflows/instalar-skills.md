---
description: Instala o importa skills técnicas en el proyecto, con procedencia, versión fija y sin instalación global accidental
---

# Instalar Skills del Proyecto

Usar cuando el usuario quiera añadir habilidades técnicas, UI/UX, dominio o tooling al proyecto.

## Objetivo

Instalar skills de forma local al proyecto, reproducible y verificable:
- Nada global salvo aprobación explícita.
- Nada con `latest` o versión implícita.
- Toda procedencia registrada en `_project/14_skill_provenance.md`.
- La skill queda usable desde `.agent/skills/` o `.agent/skills/imported/`.

## Pasos

0. CONTEXTO
   Leer:
   - `_project/03_project_state.json`
   - `_project/14_skill_provenance.md`
   - `_project/17_skill_recommendations.md` si existe
   - `_project/15_environment_profile.md`
   - `.agent/skills/` listado superficial

1. DEFINIR NECESIDAD
   Confirmar qué problema debe resolver la skill:
   - UI/UX
   - framework/backend/frontend específico
   - testing
   - seguridad
   - dominio
   - otra

   Si ya existe una skill local suficiente, usarla antes de instalar otra.

2. CLASIFICAR FUENTE
   Fuentes en orden de prioridad:

   **1ª — Plugin oficial (fuente primaria):**
   - `claude.com/plugins/claude-code-setup` — recomendaciones pre-validadas por Anthropic.
     No requiere instalación de archivos; registrar en `14_skill_provenance.md` con estado `aprobado`.
     Usar cuando el plugin haya sugerido explícitamente la skill o MCP.

   **2ª — Fuentes externas verificables (fallback):**
   - Repositorio Git con tag, release o commit SHA específico.
   - Paquete npm/pip/etc con versión exacta.
   - Archivo local provisto por el usuario.
   - Skill creada manualmente en `.agent/skills/`.

   Fuentes no permitidas sin marcar `no_verificado`:
   - `latest`
   - rama móvil sin commit SHA
   - gist o zip sin checksum/commit
   - instrucciones que requieren ejecutar scripts remotos sin revisión

3. PLAN DE INSTALACIÓN
   Presentar antes de ejecutar:
   ```markdown
   PLAN DE SKILL
   - Nombre:
   - Fuente:
   - Versión/commit:
   - Instalación local en:
   - Archivos esperados:
   - Comando de descarga/instalación:
   - Riesgos:
   ```

   Esperar aprobación explícita si requiere red, ejecución de instalador o escritura fuera de `.agent/skills/`.

4. INSTALACIÓN LOCAL
   Regla base:
   - Instalar/copiar dentro de `.agent/skills/imported/[nombre]/`.
   - Si el formato del entorno requiere un archivo directo en `.agent/skills/[nombre].md`, crear un wrapper corto que apunte a `imported/[nombre]/SKILL.md`.
   - No instalar en `$HOME`, `$CODEX_HOME`, configuración global de Claude/Codex ni rutas del sistema salvo aprobación explícita.

   Para Git:
   - Descargar a un directorio temporal.
   - Hacer checkout de tag o commit aprobado.
   - Copiar solo archivos necesarios de la skill.
   - No ejecutar scripts del repo salvo aprobación separada.

   Para paquetes:
   - Usar versión exacta.
   - Preferir descarga/inspección antes de ejecutar generadores.
   - Registrar comando exacto.

5. VALIDACIÓN
   Verificar:
   - Existe `SKILL.md` o archivo principal equivalente.
   - La skill tiene nombre, propósito, cuándo usarla y límites.
   - No contiene instrucciones que contradigan `CLAUDE.md` o `.agent/rules/architecture_constraints.md`.
   - No introduce comandos destructivos, globales o de red sin aprobación.

   Si falla, marcar como `no_verificado` o `rechazado`.

6. REGISTRO DE PROCEDENCIA
   Actualizar `_project/14_skill_provenance.md`:
   - Fecha
   - Skill / paquete
   - Versión/tag/commit
   - Fuente
   - Comando usado
   - Archivos instalados
   - Estado: `aprobado`, `no_verificado` o `rechazado`
   - Scope: `project-local` o `global-aprobado`
   Si la instalación venía de una recomendación de `_project/17_skill_recommendations.md`, marcar esa fila como `instalada`.

7. ACTIVACIÓN
   Explicar cómo se activa:
   - Por mención explícita del nombre.
   - Por coincidencia con la descripción.
   - Como referencia secundaria si quedó `no_verificado`.

## Reglas

- No instalar skills globales por defecto.
- No ejecutar instaladores remotos sin versión exacta y aprobación.
- No duplicar skills existentes: enriquecer o registrar variante con motivo.
- Si la skill es para UX/UI, actualizar `_project/12_design_profile.md` si cambia criterios visuales del proyecto.
- Si la skill trae reglas que chocan con el framework, prevalecen `CLAUDE.md` y `.agent/rules/architecture_constraints.md`.
- Al cerrar no se emite un cierre nuevo: vuelve al `Siguiente:` vigente de la sesión y repítelo tal cual.

La interfaz, los logs y los textos para el usuario van en español.
