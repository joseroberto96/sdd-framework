---
description: Gestión de skills — sin argumentos detecta y sugiere, con "instalar [fuente]" instala localmente con procedencia registrada
---

# Skills — Sugerir e Instalar

Comando unificado de gestión de skills. El modo se detecta por los argumentos.

## Modos

### Modo SUGERIR (sin argumentos o `/skills`)

Ejecutar `.agent/workflows/sugerir-skills.md` completo:

1. Leer `_project/19_core_skills.md`, `14_skill_provenance.md`, `17_skill_recommendations.md`, scope activo y builder profile.
2. Detectar señales en la tarea actual: UI/UX, JS/CSS, seguridad, testing, performance, dominio ambiguo.
3. Si una skill core o local ya cubre la señal: activarla directamente, sin proponer instalación.
4. Si falta una skill útil más allá de las core, proponer en ≤4 líneas:
   ```
   Veo señal de [tipo]. Podría ayudar instalar una skill de [tipo] local al proyecto.
   Beneficio: [1 línea].
   Si la apruebas, ejecuta: /skills instalar [fuente exacta]
   ```
5. Registrar propuesta en `_project/17_skill_recommendations.md` con estado `propuesta`.
6. Continuar con la tarea — no bloquear salvo que la skill sea estrictamente necesaria.

### Modo INSTALAR (`/skills instalar [fuente]`)

Requiere fuente con versión exacta: `repo@tag`, `repo@commit-sha`, `paquete@versión` o archivo local.
No aceptar `latest`, ramas móviles sin SHA, ni gists sin checksum.

Ejecutar `.agent/workflows/instalar-skills.md` completo:

1. Confirmar qué problema resuelve la skill y si ya existe una local suficiente.
2. Presentar plan de instalación antes de ejecutar (nombre, fuente, versión, archivos esperados, riesgos).
3. Esperar aprobación explícita si requiere red, instalador o escritura fuera de `.agent/skills/`.
4. Instalar en `.agent/skills/imported/[nombre]/`.
5. Validar que el archivo principal existe y no contradice `CLAUDE.md` ni las reglas del framework.
6. Registrar en `_project/14_skill_provenance.md` con fecha, versión/commit, fuente, comando y estado.
7. Si vino de una recomendación en `17_skill_recommendations.md`, actualizar estado a `instalada`.

## Reglas

- No instalar en modo SUGERIR.
- No instalar skills globales por defecto; requiere aprobación explícita.
- Si una skill core cubre la necesidad, activarla primero.
- No repetir recomendaciones rechazadas o pospuestas.
- Si la skill afecta criterios visuales, actualizar `_project/12_design_profile.md`.
- Al cerrar no se emite un cierre nuevo: vuelve al `Siguiente:` vigente de la sesión y repítelo tal cual.

La interfaz, los logs y los textos para el usuario van en español.
