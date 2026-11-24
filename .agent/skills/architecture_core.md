---
description: Skill core. Se activa al diseñar módulos, refactorizar, declarar interfaces, tocar dependencias o tomar decisiones técnicas no triviales.
core: true
---

# Arquitectura Core — Skill Core

**Activación:** nuevos módulos, interfaces públicas, dependencias, refactors, DB, integración entre módulos, cambios de estructura o decisiones técnicas con impacto futuro.

## Principios

- Interfaces pequeñas, módulos profundos.
- Contratos explícitos antes de acoplamiento implícito.
- Dependencias hacia adentro: el dominio no depende de detalles de infraestructura.
- Cambios aditivos por defecto; cambios destructivos requieren aprobación.
- Decisiones no obvias se registran en `_project/03_arch_decisions.md`.
- El diseño técnico de un MVP se registra con `/tech-design-mvp` antes de abrir varios módulos o dependencias.

## Reglas

- Revisar `03_project_state.json`, `04_active_scope.md`, `11_file_dependencies.md` y, según aplique, `03_contracts.md` (no-HTTP), `data_model.md` (entidades) y `api_contract.yaml` (HTTP).
- Revisar `_project/01_product_requirements.md` antes de diseñar arquitectura para no optimizar fuera del MVP.
- Revisar `_project/21_knowledge_map.md` para detectar temas sensibles o desconocidos antes de decidir.
- No crear dependencias directas entre módulos sin contrato declarado.
- No introducir abstracciones para una sola ocurrencia salvo que protejan un contrato importante.
- Preferir composición/inyección a instanciación directa.
- No hardcodear configuración, paths ni IDs de entorno.
- Si una decisión afecta varios módulos, documentarla como ADR.

## Checklist

- [ ] ¿La interfaz pública está declarada?
- [ ] ¿El módulo puede probarse o verificarse de forma aislada?
- [ ] ¿Las dependencias están registradas?
- [ ] ¿El cambio preserva contratos existentes?
- [ ] ¿La complejidad agregada paga su costo?
- [ ] ¿Hace falta ADR?
- [ ] ¿Se consideraron alternativas/trade-offs suficientes para el MVP sin sobrediseñar?
- [ ] ¿El diseño sirve los atributos de calidad declarados en 10_arch_constraints.md, o documenta explícitamente por qué los difiere?
- [ ] ¿Las entidades que usa el módulo están declaradas en `_project/02_technical_plan/data_model.md`?
- [ ] ¿Los errores siguen la estrategia declarada (FATAL / RECUPERABLE / ESPERADO / SILENCIOSO-OK)?
- [ ] ¿Las operaciones críticas producen algún registro observable según lo declarado?

## Anti-Patrones

- Refactor por gusto sin presión real.
- Módulos superficiales que solo pasan llamadas.
- Lógica de dominio escondida en UI, scripts o rutas.
- Importar internals de otro módulo porque "es más rápido".
- Romper una API pública para limpiar nombres sin migración.
