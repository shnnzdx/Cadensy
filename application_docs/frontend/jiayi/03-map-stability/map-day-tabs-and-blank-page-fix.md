# Map Day Tabs And Stability Notes

## Problem

The Plan map day tabs and adjacent day-accordion behaviors regressed in two different ways.

Symptoms included:

- `All / Day 1 / Day 2 / Day 3 / Day 4` visually rendered
- day buttons sometimes did not receive pointer events
- clicking a day could destabilize the map area
- `/trip` could appear as a blank warm-paper page after interaction regressions
- clicking `Day 1 / Day 2 / ...` could blank the Plan view
- clicking the itinerary `+ / -` control could appear to do nothing

## Files Involved

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/plan-feature/usePlanInteractionRuntime.js`
- `trip/src/final/final.css`
- `frontend/public/trip-app/*`

## What Changed

### Kept Existing Selection Logic

The existing plan runtime state and handlers are still used:

- `railDay`
- `showAllOnMap`
- `showDayOnMap(day.id)`
- `railDays`

No second selected-day state was introduced.

### Removed Map Remount Key

The map was previously keyed by selected day:

```jsx
<TripMap key={view.railDay} ... />
```

That forced the Leaflet map to remount when changing tabs.

The key was removed so the existing map instance can update through props instead of being destroyed and recreated on every tab click.

### Protected Tab Click Targets

CSS was added so the map tab row stays above Leaflet/map layers:

- `position: relative`
- higher `z-index`
- `isolation: isolate`
- explicit `pointer-events: auto`
- pointer cursor on buttons

This keeps the visual tab controls clickable without rewriting the map component.

### Preserved Marker Interaction

CSS was also added to keep Leaflet marker icons interactive:

- marker pointer events remain enabled
- marker cursor remains pointer
- marker z-index stays above map overlays

### Restored Single-Day Map Rendering

The blank-page regression was not a routing bug.

The day-tab click only changed local Plan state, but the single-day branch in `PlanFeature.jsx`
called `totalRouteMiles(...)` without a live definition. That only ran after selecting a single
day, so the page could look stable in `All` mode and then fail after clicking `Day 1`.

The fix restored that helper so single-day map summary rendering no longer throws.

### Reconnected Map-Day Focus With Day Expansion

Selecting a day on the map rail now:

- sets `railDay`
- narrows `railDays`
- opens that day in the left itinerary accordion

This keeps the map focus and the visible itinerary in sync without adding a second source of truth.

### Repaired Accordion State Styling

The `+ / -` control looked broken because a later workspace-scoped CSS override forced the
accordion body and inner content into an always-open visual state:

- closed day rows still rendered as open
- React state could change, but the CSS no longer expressed that difference

The fix restored separate selectors for:

- default closed state
- `.accordionDay.open` expanded state

This was a CSS-state regression, not a React toggle bug.

## What Was Not Changed

No changes were made to:

- map data
- POI data
- TripMap component logic
- map library
- backend route data
- selected item data structure
- route ownership
- session ownership

## Expected Behavior

Clicking map tabs should:

- set the selected tab visually
- update `railDay`
- filter/focus the map to that day through existing data flow
- open the same day in the itinerary list
- keep `All` as the full-trip view
- not blank the page

Clicking the itinerary `+ / -` control should:

- expand a closed day
- collapse an open day
- remain visible in `/trip` after syncing the preview bundle

## Important Runtime Note

If a fix works in `trip/` but still looks unchanged in `http://localhost:3000/trip`, the most
likely cause is an outdated embedded preview bundle.

After Trip UI changes, rebuild the embedded shell preview:

```powershell
cd frontend
npm run build:trip-preview
```
