---
name: neumorphism-styleguide
description: >-
  Enforces the official Neumorphism (Soft UI) Dark Monochrome Design System for OmniCache dashboards and web components based on the confirmed WhatsApp reference image (IMG-20260911-WA0000.jpg). Use whenever designing, building, inspecting, or refactoring UI components, dashboards, CSS, or HTML.
---

# Neumorphism (Soft UI) Dark Monochrome Styleguide

This design system governs all frontend interface engineering for OmniCache. It strictly codifies the 6-tier dark monochrome slate/navy palette from the confirmed **Product UI Styleguide** (reference: `IMG-20260911-WA0000.jpg`).

---

## 1. The Strict 6-Color Palette

| Token Variable | Hex Code | Visual Role |
| :--- | :--- | :--- |
| `--c-obsidian` | `#06141B` | Deepest canvas background & recessed inset wells |
| `--c-midnight` | `#11212D` | Extruded card & component surface |
| `--c-navy`     | `#253745` | Elevated surfaces, secondary buttons, subtle active fills |
| `--c-slate`    | `#4A5C6A` | Outer borders, divider lines, architectural gridlines |
| `--c-silver`   | `#9BA8AB` | Secondary body text, metrics hints, timestamps |
| `--c-frost`    | `#CCD0CF` | Primary action pills, high-contrast headings, hero metric values |

---

## 2. Neumorphic Dual-Shadow Physics (Dark Soft UI)

* **Light Direction:** Top-Left simulated illumination (`rgba(74, 92, 106, 0.25)` or `#253745`).
* **Shadow Direction:** Bottom-Right absorption (`#02090D` / `#030A0F`).

### Elevation Tokens
```css
/* Extrusions (Raised Components) */
--neu-flat-1: 3px 3px 8px #030a0f, -3px -3px 8px rgba(74, 92, 106, 0.22);
--neu-flat-2: 6px 6px 16px #03090e, -6px -6px 16px rgba(74, 92, 106, 0.25);
--neu-flat-3: 10px 10px 24px #02070b, -10px -10px 24px rgba(74, 92, 106, 0.28);
--neu-flat-hover: 7px 7px 18px #02070b, -7px -7px 18px rgba(74, 92, 106, 0.32);

/* Insets (Recessed Wells & Textareas) */
--neu-pressed: inset 3px 3px 6px #02080d, inset -3px -3px 6px rgba(74, 92, 106, 0.2);
--neu-pressed-deep: inset 4px 4px 8px #01060a, inset -4px -4px 8px rgba(74, 92, 106, 0.25);
```

---

## 3. Geometry & Typography
* **Pill Geometry (`border-radius: 9999px`):** Buttons, segmented tabs, status chips, badges, single-line inputs.
* **Card Geometry (`border-radius: 20px`):** All containers, metric blocks, chart panels.
* **Typography:** `Plus Jakarta Sans` for UI copy and headings; `JetBrains Mono` for tabular metrics and code blocks.
