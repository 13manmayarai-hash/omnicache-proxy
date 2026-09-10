# OmniCache Dashboard UI Rules & Constraints

## Strict Neumorphism (Soft UI) Standard
All user interface changes, styling enhancements, and component additions in this directory MUST strictly adhere to the confirmed **Neumorphism (Soft UI) Product UI Styleguide** (derived from the official reference styleguide image).

### Mandatory Guidelines:
1. **Never Deviate to Glassmorphism or Flat/Dark Mode:** Do not introduce frosted glass (`backdrop-filter`), pitch-black dark mode, neon glow rings, or flat unshaded rectangles.
2. **Surface & Canvas:** Canvas and surface cards must use `#EEF2F6`.
3. **Dual-Shadow Extrusion:** Raised elements must cast a light highlight on the top-left (`-Xpx -Xpx ... #FFFFFF`) and soft slate shadow on bottom-right (`Xpx Xpx ... #D1D9E6`).
4. **Recessed Wells:** Input fields, code boxes, and search inputs must use inset shadows (`inset 3px 3px 6px #D1D9E6, inset -3px -3px 6px #FFFFFF`).
5. **Primary Action Color:** Mint green (`#9FE6D4`) with deep green text (`#0F4C3A`) and focus halo (`#00CFCC`).
6. **Geometry:** Buttons, tabs, and status badges must be full pills (`border-radius: 9999px`). Container cards must use `20px` border-radius.
7. **Telemetry Integrity:** Never modify or delete DOM element IDs used by Chart.js, the WebSocket telemetry stream, or the playground sandbox.
