---
description: Skill core. Se activa al cerrar sesiones, publicar en GitHub, preparar fase 0/MVP, releases o compartir actualizaciones.
core: true
---

# Delivery y Release — Skill Core

**Activación:** fase 0, MVP, GitHub, commits, releases, tags, distribución, `git pull`, cierre de sesión o handoff.

## Principios

- Publicar temprano el esqueleto si ayuda a compartir y probar.
- No publicar secretos, datos privados ni artefactos locales.
- Cada publicación debe ser recuperable por otras personas con instrucciones simples.
- Estado del framework antes que memoria conversacional.
- Releases/tags para hitos estables; commits claros para avance incremental.

## Checklist Fase 0

- [ ] Framework inicializado.
- [ ] README mínimo.
- [ ] `llms.txt` presente para orientar agentes.
- [ ] `_project/01_product_requirements.md` define MVP/no-MVP.
- [ ] `.gitignore` y `.gitattributes`.
- [ ] Sin secretos.
- [ ] `16_release_profile.md` actualizado.
- [ ] Instrucciones `git clone` y `git pull --ff-only`.

## Checklist Antes de Push

- [ ] `git status` revisado.
- [ ] Gate de secretos ejecutado.
- [ ] Journal sin pendientes.
- [ ] Estado y scope actualizados.
- [ ] Checks focalizados ejecutados o justificados.
- [ ] Push aprobado explícitamente.

## Distribución

Instrucciones base para equipo:

```bash
git clone [URL]
cd [repo]
git pull --ff-only
```

Si hay setup adicional, debe estar en README.
