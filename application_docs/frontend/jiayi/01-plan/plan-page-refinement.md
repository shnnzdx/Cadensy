# Plan Page Refinement

## Scope

This work only changes the Trip Plan page presentation.

The main files involved are:

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/final.css`

## Shared Itinerary Card

The `Your shared itinerary` area was kept as the main Trip overview for the Plan page.

Changes:

- Removed the old action button cluster from inside the summary card.
- Removed the extra `Shared with N collaborators` stat from the stats row.
- Kept the core summary content:
  - Current Plan
  - Your shared itinerary
  - description
  - days
  - stops
  - activities
  - meals
- Removed decorative line-art / texture from the summary card background through CSS.
- Kept the surface clean, warm, and low-shadow.

## Collaborators

The previous `Trip collaborators` treatment felt too heavy because the content was sparse.

It was changed from a large standalone card into a lightweight collaborator strip.

Current behavior:

- Shows `Collaborators`
- Shows current member initials/avatar
- Shows `Manage members ->`
- Uses transparent/light utility styling rather than a big blue-gray card
- Does not inflate visually when there is only one member

## Day Header

The Day module header was simplified and made more structured.

Changes:

- Kept Day number and date on the left.
- Kept the Day title in the main area.
- Kept compact stats:
  - activities
  - meals
  - stops
- Kept the existing collapse / expand interaction.
- Removed the extra day summary paragraph from the header to reduce clutter.

## Route Line

The route summary row was changed from a dense string of activity names into a lighter route control row.

Current display:

- `Today's route · N planned places`
- `Show on map ->`

This keeps the action clear without turning the row into a long text-heavy database summary.

## Activity Rows

The activity row styling was refined to avoid a page that feels entirely white.

Changes:

- Ordinary activity rows now use a very pale secondary surface.
- Meal rows keep a very subtle warm sand tint.
- Transport / next-stop rows remain the lightest layer.
- Day headers are slightly more distinct than ordinary rows.
- No new pattern, gradient, decoration, or heavy shadow was added.

The goal is a soft itinerary rhythm:

- page background
- summary/day surfaces
- activity row tint
- meal row tint
- transport rows as quiet dividers

## Business Logic

No itinerary generation, item selection, drawer behavior, collapse behavior, map behavior, or data structure was intentionally changed for visual styling.
