# Accessibility and localisation (BUILD_PLAN P7.4)

TabFree aims at **WCAG 2.2 level AA** for the portal and the `<tf-*>` components. This page records what
is done and how it is checked; it is not yet an audited conformance claim.

## Components

| Need | What the components do |
|---|---|
| Text alternatives (1.1.1) | Each chart's SVG has an accessible name from its `title`; a visually hidden table of the data (first 200 rows) follows each chart |
| Keyboard (2.1.1, 2.4.7) | Selectable marks are buttons in the tab order, activated with Enter or Space, with a visible focus outline; non-interactive charts are images |
| Structure (1.3.1, 4.1.2) | Interactive charts are groups of buttons, never buttons inside an image; tables use header cells |
| Language (3.1.1) | `<tf-dashboard locale="…">` sets the document language |
| Contrast (1.4.3, 1.4.11) | Not yet verified: axe's contrast rule needs a real browser, which the unit tests do not use |

Checks: `packages/runtime/test/accessibility.test.ts` runs **axe-core** against rendered components and
tests keyboard activation and the data tables, in every `npm test`.

## Localisation

Numbers and dates follow the dashboard's locale (`locale` attribute, otherwise the browser's): grouping and
decimal separators, currency and percentages through `Intl.NumberFormat`, and dates as ISO (the default,
matching Tableau's exports) or in the locale's `short`, `medium` or `long` style (`dates` attribute), in
UTC so everyone sees the same date. The portal's own text is English for now.

## Not done yet

- A manual audit with screen readers (NVDA, VoiceOver) and of the portal pages, then a conformance report
  (VPAT).
- Translated portal text.
- Reduced-motion and high-contrast themes.
