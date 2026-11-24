---
name: "frontend-design"
description: "Se activa al diseñar o modificar UI web para mantener cohesión visual y ergonomía"
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Diseño Frontend

**Activación:** módulos que tocan UI, componentes, layout, CSS, copy visible o experiencia de usuario.

## Fuentes

Antes de diseñar, leer si existe:
- `_project/12_design_profile.md`
- `_project/09_domain_profile.md`
- `_project/10_arch_constraints.md`

## Reglas de etapa y diseño

- Construir la pantalla o flujo real, no una landing explicativa, salvo que el scope sea marketing.
- Mantener consistencia con componentes, tokens, espaciado y navegación existentes.
- Usar lenguaje del dominio en labels, estados y acciones.
- Priorizar flujos frecuentes: menos fricción, estados claros, errores recuperables.
- Verificar responsive móvil/escritorio si la UI es usuario-final.
- No introducir una paleta nueva si el proyecto ya tiene sistema visual.

<!-- CONTEXT_SOURCE: file_obligations -->
## Invariantes de archivo: UI y componentes

- **Descomposición por responsabilidad:** Separar vista/markup, lógica de estado, servicios/fetch y componentes reutilizables en unidades separadas. Un componente = un archivo.
- **Monolitos de UI prohibidos:** Si un archivo de UI concentra render + estado + fetch + validación, partirlo antes de continuar.
- **Umbral de §Chunking:** Objetivo < 300 líneas; dividir obligatoriamente si supera 500 líneas (aplica a JS, TS, JSX, TSX, HTML, CSS, SCSS, Vue y Svelte).
- **Estados de interfaz obligatorios:** Contemplar explícitamente en el componente: loading, empty, error y success cuando apliquen.
- **Controles ergonómicos:** Toggles para valores binarios, tabs para vistas alternativas, menús para opciones, sliders/inputs para números.
- **Ajuste de texto:** Asegurar que los contenedores no corten ni desborden texto en móvil ni escritorio.
- **Acciones destructivas:** Requerir siempre confirmación previa o proveer reversibilidad en los handlers de acción.
<!-- END_CONTEXT_SOURCE: file_obligations -->

## Checklist

- [ ] Estados: loading, empty, error, success si aplican.
- [ ] Controles adecuados: toggles para binario, tabs para vistas, menús para opciones, sliders/inputs para números.
- [ ] Texto cabe en sus contenedores en móvil y escritorio.
- [ ] Acciones destructivas tienen confirmación o reversibilidad.
- [ ] La UI respeta compatibilidad cross-platform si aplica.
- [ ] La UI está descompuesta por responsabilidad (vista / estado / servicios / componentes); ningún archivo de UI es un bloque monolítico ni supera el umbral de §Chunking sin justificación.

## Si falta perfil visual

Crear o proponer `_project/12_design_profile.md` con:
- Audiencia
- Tono visual
- Componentes base
- Paleta/tokens existentes
- Reglas de layout
- Anti-patrones visuales del proyecto
