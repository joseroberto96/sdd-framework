---
paths:
  - "**/*.{html,jsx,tsx,vue,svelte,css,scss}"
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->
<!-- REGLA POR RUTA (Claude Code) — fuente canónica: .agent/skills/frontend_design.md -->
<!-- Obligaciones técnicas de archivo. Las obligaciones de etapa residen en la fuente canónica. -->

# Reglas de archivo: frontend-design

## Invariantes de archivo: UI y componentes

- **Descomposición por responsabilidad:** Separar vista/markup, lógica de estado, servicios/fetch y componentes reutilizables en unidades separadas. Un componente = un archivo.
- **Monolitos de UI prohibidos:** Si un archivo de UI concentra render + estado + fetch + validación, partirlo antes de continuar.
- **Umbral de §Chunking:** Objetivo < 300 líneas; dividir obligatoriamente si supera 500 líneas (aplica a JS, TS, JSX, TSX, HTML, CSS, SCSS, Vue y Svelte).
- **Estados de interfaz obligatorios:** Contemplar explícitamente en el componente: loading, empty, error y success cuando apliquen.
- **Controles ergonómicos:** Toggles para valores binarios, tabs para vistas alternativas, menús para opciones, sliders/inputs para números.
- **Ajuste de texto:** Asegurar que los contenedores no corten ni desborden texto en móvil ni escritorio.
- **Acciones destructivas:** Requerir siempre confirmación previa o proveer reversibilidad en los handlers de acción.
