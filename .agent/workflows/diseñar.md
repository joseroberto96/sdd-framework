---
description: Pipeline de diseño unificado — detecta qué pasos faltan (descubrimiento → PRD → diseño técnico) y los ejecuta en secuencia
---

# Diseñar — Pipeline unificado

Punto de entrada único para todo el trabajo de diseño. Detecta dónde estás en el pipeline y arranca desde ahí.

## Cuándo usar
- Al iniciar un proyecto o fase nueva sin MVP claro.
- Cuando hay idea pero no PRD ni diseño técnico.
- Cuando hay PRD pero falta diseño técnico y contratos.
- Después de un cambio de dirección de producto.

## Pasos

0. DETECTAR PUNTO DE ENTRADA
   Producto en producción (FW-225): si `lifecycle.stage == production`, avisar
   en lenguaje del builder que el producto está en mantenimiento. Funcionalidad
   nueva entra como módulo escalado (`escalate_change`/`add_module` + `/ciclo`);
   rediseñar el producto implica volver a desarrollo, y eso lo decide solo el
   builder (`set_lifecycle` con su confirmación explícita). No continuar sin su
   respuesta.

   Leer en paralelo:
   - `_project/01_product_requirements.md` — ¿tiene MVP real con usuario, criterios y no-MVP?
   - `_project/03_project_state.json` — fase, step, módulos
   - `_project/04_active_scope.md` — scope activo
   - `_project/20_builder_profile.md` — nivel del constructor (si existe)
   - `_project/21_knowledge_map.md` — gaps de conocimiento (si existe)

   Determinar punto de entrada:
   - `01_product_requirements.md` no existe o solo tiene placeholders → **PASO 1**
   - Existe PRD real pero sin diseño técnico documentado → **PASO 3**
   - Existe PRD y hay diseño técnico incompleto → **PASO 3** (continúa donde quedó)

1. DESCUBRIMIENTO (si aplica)
   Ejecutar `.agent/workflows/descubrir-producto.md` completo:
   - Entrevista breve (problema, usuario, quick win, no-MVP, prueba de éxito).
   - Eco de verificación.
   - Actualizar `_project/01_product_requirements.md`, `20_builder_profile.md`, `21_knowledge_map.md`.
   Al terminar, continuar con PASO 2.

2. PRD (si aplica)
   Ejecutar `.agent/workflows/prd-mvp.md` completo:
   - Eco de verificación del MVP entendido.
   - Actualizar `_project/01_product_requirements.md` con flujos, criterios de éxito y no-MVP.
   - Derivar ACs observables en `_project/08_module_acceptance.md`.
   - Proponer módulos pequeños si el usuario aprueba.
   Al terminar, continuar con PASO 3.

3. DISEÑO TÉCNICO
   Ejecutar `.agent/workflows/tech-design-mvp.md` completo:
   - Gate THINK (Risk Surface Map, ADR pre-comprometido, riesgos de performance y UI).
   - Diseño ligero: stack, contratos, trade-offs, integridad de datos, resiliencia.
   - Gate APPROVE — esperar aprobación explícita del usuario antes de continuar.
   - Actualizar `_project/03_arch_decisions.md`, `03_contracts.md`, `10_arch_constraints.md`.
   **Sin aprobación del usuario en este paso: NO continuar a implementación.**

3.5. DETECCIÓN DE EXPERTOS (4.1.0, FW-072)
   Ejecutar la Parte A de `.agent/workflows/sugerir-skills.md` (detección de
   sub-dominios): con el PRD y el diseño técnico ya visibles, preguntarse qué
   ramas de conocimiento del producto no cubren ni las skills core ni los
   expertos activos de `_project/09_domain_profile.md`. Proponer los expertos
   faltantes con borrador listo (checkpoint 3.19.0) — "te propongo agregar X,
   Y, Z porque aportan A, B, C". No bloquea el pipeline: si el builder difiere
   la decisión, registrarla como pendiente en `09_domain_profile.md`.

4. SALIDA
   ```
   Diseño completado:
   - Descubrimiento: [ejecutado | saltado — PRD existente]
   - PRD: [ejecutado | saltado — PRD existente]
   - Diseño técnico: ejecutado
   - Gate APPROVE: [pendiente de aprobación | aprobado]
   ```

   Siguiente: /ciclo Abrir el primer módulo del plan y llevarlo a cierre.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
