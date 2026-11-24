---
description: Crea o enriquece un experto de dominio con fuentes reales aportadas por el desarrollador
---

Eres el agente que construye expertos de dominio.

Recibirás:
- @09_domain_profile.md
- El fichero del experto existente (si se enriquece): @.agent/skills/domain/[nombre_del_experto].md

## Pasos

0. RUTA PROACTIVA (4.1.0, FW-072 — la normal)
   La creación de expertos normalmente NO empieza aquí: empieza en la detección
   proactiva (`.agent/workflows/sugerir-skills.md` Parte A, invocada por
   /diseñar y /enriquecer-ticket), donde el agente llega con el borrador ya
   redactado sobre `.agent/skills/domain/_TEMPLATE.md` y el builder solo
   confirma/corrige. Este workflow es el camino MANUAL: cuando el builder pide
   un experto directamente o quiere enriquecer uno con fuentes reales (🌱 → 📚).

1. DETERMINAR EL MODO
   Preguntar al usuario:
   ```
   ¿Quieres:
   (A) Crear un nuevo experto de dominio
   (B) Enriquecer un experto existente con nuevas fuentes
   ```
   Esperar la respuesta.

2A. SI SE CREA UN EXPERTO NUEVO
    Preguntar:
    ```
    Para crear el experto necesito:
    1. Área de especialización (ej: "seguridad financiera", "UI/UX móvil"):
    2. Fuentes reales a incorporar (docs, APIs, normativas, papers — pega los fragmentos relevantes):
    3. ¿Hay invariantes críticos que el agente NUNCA debe violar en esta área?
    ```
    Crear `.agent/skills/domain/[nombre_del_experto].md` a partir de `.agent/skills/domain/_TEMPLATE.md`.
    Actualizar la tabla de expertos activos de @09_domain_profile.md.

2B. SI SE ENRIQUECE UN EXPERTO EXISTENTE
    Preguntar:
    ```
    Para enriquecer el experto necesito:
    1. ¿Qué experto quieres enriquecer? (nombre del archivo)
    2. Fuentes nuevas a incorporar (pega el contenido):
    3. ¿Algún invariante nuevo detectado en la práctica?
    ```
    Leer el fichero existente e incorporar lo nuevo en "Fuentes del desarrollador".
    Actualizar el estado: 🌱 → 📚 si ya hay al menos una fuente real.

3. CONFIRMAR
   > Experto [nombre] [creado / actualizado] con estado [🌱 / 📚].
   > Para activarlo en el proyecto, asegúrate de que aparece en 09_domain_profile.md → expertos_activos.
   > Siguiente: /ciclo <instrucción concreta del trabajo en curso, consultando al experto>
   > Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
