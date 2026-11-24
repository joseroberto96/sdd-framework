---
description: Descubre producto, usuario, quick win y riesgos antes de generar PRD o codificar
---

# Descubrir Producto

Usar al iniciar un proyecto nuevo, al migrar un proyecto confuso o antes de una fase importante.

## Objetivo

Convertir una idea amplia en contexto accionable sin sobredocumentar: usuario, problema, MVP, no-MVP, quick win y riesgos.

## Pasos

1. LEER CONTEXTO
   Leer si existen:
   - `_project/03_project_state.json`
   - `_project/04_active_scope.md`
   - `_project/01_product_requirements.md`
   - `_project/20_builder_profile.md`
   - `_project/21_knowledge_map.md`
   - `README.md`

2. ENTREVISTA BREVE
   Preguntar en un solo bloque:
   ```text
   Para descubrir el producto necesito:
   1. Que problema quieres resolver?
   2. Quien lo usara y en que contexto real?
   3. Que accion debe poder hacer primero para que ya valga la pena?
   4. Que NO quieres incluir en el MVP?
   5. Que seria una prueba simple de que funciona?
   6. Que nivel de ayuda quieres del agente? [vibe-coder | intermedio | developer | equipo]
   ```
   Esperar respuesta.

3. ECO DE VERIFICACIÓN
   Responder con 5-8 bullets:
   - Producto entendido.
   - Usuario/contexto.
   - Quick win.
   - MVP incluye.
   - No-MVP.
   - Riesgos o preguntas abiertas.
   - Siguiente artefacto recomendado.

   Si el usuario corrige algo, incorporar correcciones antes de seguir.

4. ACTUALIZAR ARTEFACTOS
   Actualizar:
   - `_project/01_product_requirements.md`
   - `_project/20_builder_profile.md`
   - `_project/21_knowledge_map.md`
   - `_project/07_pending_clarifications.md` si hay bloqueos

5. SALIDA
   Cerrar con:
   Siguiente: /diseñar Continuar el pipeline de PRD.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
   Si hay ambigüedad bloqueante:
   Siguiente: /diseñar Resolver la decisión de negocio pendiente.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

## Reglas

- No convertir descubrimiento en contrato pesado.
- Mantener el MVP pequeno y verificable.
- Registrar explicitamente lo que queda fuera para evitar scope creep.
- No codificar desde una idea amplia sin quick win y criterio de exito.
- **Modo brief:** si el usuario pega un documento o texto extenso como respuesta a la entrevista, extraer lo que aplique, hacer el eco de verificación y preguntar solo por los huecos. No re-preguntar lo ya contenido.
- **Anti-fabricación:** solo derivar de `_project/01_product_requirements.md` y de respuestas explícitas del usuario en esta sesión. Ante un hueco, registrar `PRD-CLAR-NNN` en `_project/07_pending_clarifications.md` y bloquear el paso afectado. Nunca inferir desde dominios análogos ni desde "buenas prácticas".

La interfaz, los logs y los textos para el usuario van en español.
