# Shared Trip Workspace Visual System

## Goal

Make Plan, Chat, Updates, Preferences, Members, and Invite feel like one Cadensy Trip workspace.

The pages should keep different functions, but use the same visual foundation.

## Shared Tokens

The shared visual system is defined in `trip/src/final/final.css` under `.tripPage`.

The token logic includes:

- page background
- primary surface
- secondary surface
- accent / helper surface
- border color
- soft border color
- primary ink color
- muted text color
- shared radius
- restrained shadow

The purpose is to avoid each page inventing its own color, border, and card language.

## Page Background

All workspace pages now use the same clean warm paper background.

The background intentionally avoids:

- grid patterns
- large map textures
- dark decorative line art
- gradients
- repeated wallpaper-like decoration

## Surface Hierarchy

The shared hierarchy is:

1. Page background
2. Primary content surfaces
3. Secondary/context surfaces
4. Accent/helper surfaces
5. Lightweight row tints

Examples:

- Plan summary and day containers use primary warm surfaces.
- Map/sidebar and conversation sidebar use secondary surfaces.
- Day at a glance and helper modules use quiet accent surfaces.
- Meal rows use a very pale warm tint.

## Typography

The typography direction is unified:

- serif for page-level/editorial headings and important travel titles
- sans-serif for controls, metadata, forms, tabs, buttons, helper text, and system information

This keeps Plan's editorial feel from becoming isolated while keeping functional pages readable.

## Borders, Radius, And Shadow

Cards and content containers were normalized toward:

- subtle border
- consistent radius
- low or no shadow
- no heavy SaaS-style elevation

The UI relies on surface tint, whitespace, and hierarchy rather than strong shadows.

## Page-Specific Adjustments

Plan:

- retained itinerary-first travel workspace structure
- refined row tinting and sidebar surfaces

Chat:

- conversation list and chat panel use the same workspace surface system
- composer and header borders align with the shared border system

Updates:

- update rows and filters use the same calm border and surface treatment

Preferences:

- form cards, inputs, selects, textareas, and option tiles use shared surfaces and borders

Members:

- member stats, member list, rows, and organizer helper panels align with shared card treatment

Invite:

- invite manager, share hero, and link panel use the same surface hierarchy

## Important Constraint

This pass is visual-only.

It does not add content to make sparse pages feel fuller. Instead, it uses shared spacing, surfaces, borders, and typography to make the existing content feel more coherent.
