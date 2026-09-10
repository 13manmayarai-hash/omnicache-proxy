---
name: neumorphism-styleguide
description: >-
  Enforces the official Neumorphism (Soft UI) Product UI Styleguide for OmniCache dashboards, web interfaces, and UI components based on the confirmed WhatsApp reference design. Use whenever designing, building, inspecting, or refactoring UI components, dashboards, CSS, or HTML.
---

# Neumorphism (Soft UI) Product UI Styleguide

This skill governs all frontend interface engineering and design system implementations for OmniCache. It codifies the exact visual language, color tokens, light physics, and component architectures specified in the confirmed **Product UI Styleguide**.

---

## 1. Core Visual Philosophy

Neumorphism (Soft UI) treats the user interface as a continuous physical surface made of soft porcelain or extruded matte polymer. Instead of floating flat cards or translucent frosted glass, elements either **extrude outward** (raised towards the user via dual light-source shadows) or **press inward** (recessed wells into the surface via inset dual shadows).

* **Light Direction:** Top-Left (135° simulated angle)
* **Highlight Color:** Pure White (`#FFFFFF`) on top-left edges
* **Shadow Color:** Soft desaturated blue-slate (`#D1D9E6` or `#CBD5E1`) on bottom-right edges
* **Continuous Canvas:** The background and card surfaces share identical base tones (`#EEF2F6`).

---

## 2. Token Specification

### Color Tokens
| Token | Hex Value | Semantic Role |
| :--- | :--- | :--- |
| `--canvas-bg` | `#EEF2F6` | Master page background canvas |
| `--surface-bg` | `#EEF2F6` | Card and component extrusion surface |
| `--primary-mint` | `#9FE6D4` | Primary action pill button background |
| `--primary-mint-hover` | `#88DEC9` | Primary action hover state |
| `--primary-mint-text` | `#0F4C3A` | High-contrast forest green text on mint |
| `--focus-ring` | `#00CFCC` | Cyan-mint active/focus border halo |
| `--alert-success-bg` | `#C7F2E5` | Pastel mint success status badge |
| `--alert-success-text`| `#065F46` | Success text |
| `--alert-warning-bg` | `#FEF08A` | Soft butter yellow warning badge |
| `--alert-warning-text`| `#854D0E` | Warning text |
| `--alert-error-bg` | `#FECDD3` | Soft pastel rose error badge |
| `--alert-error-text` | `#9F1239` | Error text |
| `--text-main` | `#1E293B` | Primary headings and metrics |
| `--text-muted` | `#475569` | Secondary body copy |
| `--text-dim` | `#64748B` | Labels, uppercase titles, hints |

### Shadow & Elevation Tokens
```css
/* Extrusions (Raised Components) */
--neu-flat-1: 3px 3px 7px #d1d9e6, -3px -3px 7px #ffffff;
--neu-flat-2: 6px 6px 14px #d1d9e6, -6px -6px 14px #ffffff;
--neu-flat-3: 10px 10px 22px #d1d9e6, -10px -10px 22px #ffffff;
--neu-flat-hover: 8px 8px 18px #c5cfdf, -8px -8px 18px #ffffff;

/* Insets (Recessed Wells & Active States) */
--neu-pressed: inset 3px 3px 6px #d1d9e6, inset -3px -3px 6px #ffffff;
--neu-pressed-deep: inset 4px 4px 8px #cbd5e1, inset -4px -4px 8px #ffffff;
```

### Radii & Geometry
* **Pill Elements:** `border-radius: 9999px` (Buttons, tabs, status chips, badges, single-line search/inputs)
* **Containers & Cards:** `border-radius: 20px`
* **Textareas & Code Wells:** `border-radius: 16px`
* **Subtle Highlights:** `border: 1px solid rgba(255, 255, 255, 0.7)`

---

## 3. Standard Component Recipes

### A. Primary Action Pill Button
```css
.btn-primary-mint {
  background: var(--primary-mint);
  color: var(--primary-mint-text);
  border-radius: 9999px;
  padding: 0.55rem 1.15rem;
  font-weight: 600;
  box-shadow: 4px 4px 10px #c5d0e0, -4px -4px 10px #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.5);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.btn-primary-mint:hover {
  background: var(--primary-mint-hover);
  box-shadow: 6px 6px 14px #b8c5d8, -6px -6px 14px #ffffff;
  transform: translateY(-1px);
}
.btn-primary-mint:active {
  box-shadow: inset 3px 3px 6px #78c8b4, inset -3px -3px 6px #bbf4e6;
  transform: translateY(1px);
}
```

### B. Recessed Input Well
```css
.input-neu {
  background: var(--surface-bg);
  border-radius: 9999px;
  box-shadow: inset 3px 3px 6px #d1d9e6, inset -3px -3px 6px #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.6);
  padding: 0.65rem 1.15rem;
  color: #1e293b;
  outline: none;
}
.input-neu:focus {
  box-shadow: inset 3px 3px 6px #d1d9e6, inset -3px -3px 6px #ffffff, 0 0 0 2px #00cfcc;
}
```

### C. Segmented Tab Pills
```css
.tab-container {
  display: flex;
  padding: 4px;
  background: var(--surface-bg);
  border-radius: 9999px;
  box-shadow: inset 2px 2px 5px #d1d9e6, inset -2px -2px 5px #ffffff;
}
.tab-item {
  border-radius: 9999px;
  padding: 0.4rem 1rem;
  font-weight: 600;
  cursor: pointer;
}
.tab-item.active {
  background: var(--surface-bg);
  box-shadow: 3px 3px 7px #d1d9e6, -3px -3px 7px #ffffff;
  color: #0d9488;
}
```

---

## 4. Strict Design System Constraints (Anti-Patterns)

1. **NO Glassmorphism / Frosted Acrylic:** Do NOT use `backdrop-filter: blur()`, semi-transparent saturated backgrounds, or neon glow borders.
2. **NO Dark Mode Inversion:** Maintain the clean soft-porcelain `#EEF2F6` theme.
3. **NO Harsh Black Shadows:** Avoid `rgba(0, 0, 0, 0.5+)`. Shadows must use subtle, diffused slate tints (`#D1D9E6`) paired with bright white light counter-shadows (`#FFFFFF`).
4. **NO Sharp Rectangular Corners:** Interactive elements MUST use full pill geometry (`9999px`), and cards MUST use `20px` radius.
5. **Preserve DOM Element IDs & Contracts:** Any dashboard styling update must preserve telemetry listeners, Chart.js canvases, and WebSocket hook points.
