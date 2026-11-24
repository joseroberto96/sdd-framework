---
description: Skill core. Se activa al definir scope, priorizar backlog, partir módulos, entrevistar requisitos o decidir MVP siguiendo principios ágiles.
core: true
---

# Agile Product — Skill Core

**Activación:** scope, backlog, MVP, historias de usuario, criterios de aceptación, entrevista de requisitos, priorización, cambios de fase o dudas de producto.

## Principios

- Personas y colaboración sobre proceso rígido.
- Software funcionando sobre documentación pesada.
- Colaboración con el usuario sobre contratos cerrados.
- Responder al cambio sobre seguir un plan fijo.

Estos principios no eliminan la disciplina del framework: el estado vive en `_project/`, los ACs se declaran y los módulos se cierran con gates.

## Reglas de Producto

- Partir trabajo en rebanadas verticales pequeñas que entreguen valor observable.
- Preferir MVM: módulo viable mínimo antes de extras.
- No bloquear por documentación perfecta; sí bloquear por ambigüedad que pueda cambiar comportamiento o datos.
- Cada tarea debe tener un resultado verificable por usuario o por test.
- Si una petición mezcla varias intenciones, proponer separar por valor y riesgo.
- Cambios de scope importantes pasan por `/entrevistar` o actualización explícita de ACs.
- Si la idea todavía no tiene usuario, quick win, MVP y no-MVP claros, pasar por `/descubrir-producto` y `/prd-mvp` antes de construir.
- Antes de documentos grandes o planes de fase, hacer el eco de verificación para confirmar entendimiento con el usuario.

## Checklist

- [ ] ¿El usuario final y su problema están claros?
- [ ] ¿La tarea entrega valor visible o desbloquea valor?
- [ ] ¿Hay ACs observables?
- [ ] ¿La rebanada es pequeña y cerrable?
- [ ] ¿Qué queda fuera del MVM?
- [ ] ¿Qué feedback se necesita después de entregar?
- [ ] ¿El PRD vivo en `_project/01_product_requirements.md` refleja el MVP actual?
- [ ] ¿El nivel del constructor en `_project/20_builder_profile.md` cambia cuánto debo explicar?

## Señales de Alerta

- Scope crece sin cerrar módulos.
- Se añaden extras antes del flujo principal.
- ACs describen implementación, no comportamiento.
- Se está optimizando algo que aún no fue validado.

## Salida Recomendada

Cuando se active, responder con una propuesta breve:

```markdown
Rebanada recomendada:
- Valor:
- Incluye:
- Fuera por ahora:
- Verificación:
```
