---
name: Warm Bento Minimal
colors:
  surface: '#faf9f5'
  surface-dim: '#dbdad6'
  surface-bright: '#faf9f5'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f5f4f0'
  surface-container: '#efeeea'
  surface-container-high: '#e9e8e4'
  surface-container-highest: '#e3e2df'
  on-surface: '#1b1c1a'
  on-surface-variant: '#47464b'
  inverse-surface: '#30312e'
  inverse-on-surface: '#f2f1ed'
  outline: '#77767b'
  outline-variant: '#c8c5cb'
  surface-tint: '#5f5e61'
  primary: '#000000'
  on-primary: '#ffffff'
  primary-container: '#1b1b1e'
  on-primary-container: '#858387'
  inverse-primary: '#c8c5ca'
  secondary: '#615884'
  on-secondary: '#ffffff'
  secondary-container: '#d6cbfe'
  on-secondary-container: '#5c547f'
  tertiary: '#000000'
  on-tertiary: '#ffffff'
  tertiary-container: '#0c2000'
  on-tertiary-container: '#6e8d55'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e4e1e6'
  primary-fixed-dim: '#c8c5ca'
  on-primary-fixed: '#1b1b1e'
  on-primary-fixed-variant: '#47464a'
  secondary-fixed: '#e6deff'
  secondary-fixed-dim: '#cbc0f2'
  on-secondary-fixed: '#1d153d'
  on-secondary-fixed-variant: '#49416b'
  tertiary-fixed: '#cbedad'
  tertiary-fixed-dim: '#b0d193'
  on-tertiary-fixed: '#0c2000'
  on-tertiary-fixed-variant: '#334e1e'
  background: '#faf9f5'
  on-background: '#1b1c1a'
  surface-variant: '#e3e2df'
typography:
  display-hero:
    fontFamily: Plus Jakarta Sans
    fontSize: 44px
    fontWeight: '400'
    lineHeight: 52px
    letterSpacing: -0.03em
  display-hero-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '500'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.01em
  label-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
  stat-display:
    fontFamily: Plus Jakarta Sans
    fontSize: 34px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.03em
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-lg: 1.25rem
  margin: 1.5rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
---

## Brand & Style

This design system embodies a calm, high-touch, human-centric aesthetic designed for intelligent agency management and CRM platforms. By rejecting the stark, clinical dashboard tropes of typical enterprise software, it introduces warmth, editorial elegance, and organic softness inspired by modern wellness environments and tactile bento layouts.

The design movement combines **Contemporary Bento-Grid Modularism** with **Soft Neoclassical Tactility**. The visual tone evokes unhurried confidence, clarity, and bespoke hospitality. Interfaces feature layered cards with ultra-soft, diffused shadow envelopes, crisp typography, playful pastel color blocking, and pillular touchpoints. It communicates supreme capability through calm competence rather than data density overload.

## Colors

The palette relies on an earthy, warm cream canvas contrasted against intense charcoal action anchors and tinted pastel tonal zones:

- **Primary Canvas (`#F7F6F2` / `#F3F1EC`):** A restful, warm alabaster surface that eliminates cold screen fatigue and serves as the negative space framework.
- **Primary Ink & Action (`#18181B`):** Deep charcoal slate reserved for primary calls to action, high-level headers, and high-impact micro-surfaces.
- **Lavender / Lilac Accent (`#D7CCFF` / `#ECE7FF`):** Used for focal highlight cards, active navigation states, primary flow status indicators, and subtle visual tags.
- **Mint / Spring Lime Accent (`#D3F5B4` / `#E5F9D0`):** Used for positive delta metrics, active status pills, AI assist card accents, and completion milestones.
- **Amber Warmth (`#FFE4A0` / `#FFF3D6`):** Dedicated to attention markers, bottlenecks, and pending action items.
- **Card Neutral Surface (`#FFFFFF`):** Pure white modular tiles with feathered borders, floating on the warm foundation.

## Typography

Typography relies uniformly on **Plus Jakarta Sans** to maintain soft, geometric clarity with rounded terminal counters and an approachable rhythm. 

- **Greeting & Hero Displays:** Mix light and medium weights with custom italicized flourishes or high-tracking tags to create an editorial, human touch (`Good morning, Olivia`).
- **Metric Figures:** Prominent and tightly tracked numerical values (`stat-display`) ensure high legibility from a glance.
- **Section Micro-Headings:** Rendered in semi-bold uppercase or medium-contrast muted slate (`#71717A`) with loose tracking (`0.04em`) to establish visual separation without harsh divider rules.

## Layout & Spacing

The layout is constructed around an asymmetric **Bento Grid** architecture:

- **Desktop (1280px+):** Fixed 260px soft-cream navigation sidebar with a flexible 12-column bento modular area. Cards span variable proportions (e.g., 4-col stat clusters, 8-col journey trackers, 4-col agenda feeds).
- **Tablet (768px - 1024px):** 8-column layout with sidebar collapsing to a 72px icon rail. Bento tiles re-stack into pairs or full-width blocks.
- **Mobile (< 768px):** Single-column stacked stack. Bento cards convert to swipeable carousels or single-column tiles with `1rem` outer canvas margins.
- **Grid Gaps:** Tight, deliberate `1rem` to `1.25rem` gutters prevent cards from feeling isolated, maintaining a cohesive tiled desk surface.

## Elevation & Depth

Visual hierarchy uses **tonal elevation and ambient contact shadows** rather than hard structural edges:

- **Canvas Base:** Flat `#F7F6F2` canvas without borders.
- **Card Tier 1 (Default White Tiles):** Crisp `#FFFFFF` surface with an ultra-soft, diffused drop shadow: `0 4px 20px -2px rgba(24, 24, 27, 0.03), 0 2px 6px -1px rgba(24, 24, 27, 0.02)` and an optional delicate border `1px solid rgba(24, 24, 27, 0.04)`.
- **Card Tier 2 (Pastel Accent Blocks):** Flat saturated fills (`#D7CCFF` lavender, `#D3F5B4` lime, `#FFF3D6` amber) with zero elevation, embedding visually as primary focal zones.
- **Floating Overlays & Menus:** Clean `#FFFFFF` elevated with `0 12px 32px -4px rgba(24, 24, 27, 0.08)`, creating distinct focus over the bento layout.

## Shapes

The design system uses an exaggerated, welcoming curvature:

- **Bento Tiles:** Sculpted using `rounded-3xl` (24px to 28px radius), producing an organic, tablet-like appearance.
- **Interactive Badges & Buttons:** Fully rounded pill contours (`border-radius: 9999px`) across primary buttons, secondary filter segments, inline tags, and status badges.
- **Progress Funnel Bars:** Rounded top corners (`rounded-t-2xl`) on rising metrics, preserving vertical alignment while mirroring the smooth surface geometry.
- **Avatars & Floating Thumbnails:** Circular forms (`rounded-full`) or smooth squircle nesting when overlapping.

## Components

### Buttons & Action Triggers
- **Primary Action:** Solid charcoal slate (`#18181B`) with pure white text, full pill shape (`rounded-full`), padded with `0.75rem 1.25rem`. Hover introduces subtle luminance reduction (`#27272A`).
- **Secondary / Ghost Action:** Translucent charcoal tint (`rgba(24, 24, 27, 0.05)`) or soft white with border `1px solid rgba(24, 24, 27, 0.1)`.
- **Special AI Action:** Mint green surface (`#D3F5B4`) with charcoal text and sparkle icon embellishments.

### Status Chips & Pills
- **Active / Completed:** Pastel lime background (`#E5F9D0`) with forest green text (`#27581B`).
- **In Treatment / Progress:** Pastel lavender background (`#ECE7FF`) with violet text (`#4B32A6`).
- **Attention / Pending:** Pastel amber background (`#FFF3D6`) with warm ochre text (`#8A5B00`).
- **Padding:** Compact `0.25rem 0.75rem` with `label-md` uppercase typography.

### Bento Cards & Surfaces
- Modular white and pastel containers with consistent internal padding of `1.5rem`.
- Header bars within cards contain subtle secondary metadata, contextual timeframes, and pillular dropdown triggers (`Week`, `Month`, `Quarter`).

### Form Inputs & Search Fields
- Rounded pill search bars (`rounded-full`) with pure white backgrounds, subtle low-opacity slate border (`rgba(24, 24, 27, 0.08)`), and keyboard shortcut badge indicators (`⌘K`).

### Funnel & Metric Visualizations
- Segmented vertical progression bars with smooth rounded caps (`rounded-t-xl`) that shift from neutral tint (`#F0EFEA`) to status fills (`#D7CCFF`, `#D3F5B4`, `#FFE4A0`) depending on operational stage.
- Paired with stacked micro-avatars at column bases for transparent team allocation.