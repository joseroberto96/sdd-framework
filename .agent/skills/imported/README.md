# Skills Importadas

Este directorio contiene skills instaladas localmente para este proyecto.

Reglas:
- Cada skill importada vive en `.agent/skills/imported/[nombre]/`.
- La procedencia se registra en `_project/14_skill_provenance.md`.
- No copiar instaladores, caches o dependencias completas salvo que sean parte necesaria de la skill.
- Si hace falta exponer una skill como archivo directo, crear un wrapper en `.agent/skills/[nombre].md` que apunte al contenido importado.
