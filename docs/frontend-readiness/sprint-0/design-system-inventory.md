# Design System Inventory

The application has a real token foundation in `static/tokens.css`: dark and light semantic colors, three main surfaces, typography families and scale, 4/8-based spacing, radii, elevations, focus, motion, z-index, shell dimensions, content width, and breakpoint aliases. `components.css` supplies shared component primitives and `theme.css` applies theme behavior.

The canonical brand basis is electric blue on dark neutral surfaces. Semantic green, amber, and red tokens exist. Legacy aliases remain for compatibility, and specialist chat colors include lime values; those aliases are migration debt rather than a second approved system.

Static risks requiring runtime validation include 10–14px body-scale tokens below the audit's preferred mobile baseline, overlapping z-index ownership (`--z-drawer` and `--z-fab` both 200), fixed shell dimensions, and component/page CSS that can bypass semantic tokens. The breakpoint variables document intent but cannot be used inside standard media queries as custom properties, so actual stylesheet query consistency must be checked separately.

Sprint 0 makes no visual redesign. Sprint 1 should first consolidate navigation/action layering and safe-area spacing, then address token bypasses, typography/contrast, and component-state consistency using supported-run evidence.
