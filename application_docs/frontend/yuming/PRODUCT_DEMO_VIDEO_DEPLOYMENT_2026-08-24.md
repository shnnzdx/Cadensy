# Product Demo Video Deployment - 2026-08-24

## Scope

This update documents the product-page demo video work that was completed and deployed to production.

Primary goal:

- Replace static PRODUCT VIEW placeholders with short product micro-demo videos.
- Keep the frontend implementation simple: native `<video>` elements, no video tooling embedded in the app.
- Deploy the latest frontend to AWS production at `https://app.cadensy.top`.

## Source Changes

Frontend product content:

- `shared/tripsync-product-content.js`
- The How It Works flow now has four user-facing demo steps:
  - Create
  - Share
  - Build
  - Decide
- The previous fourth and fifth flows were merged into one final Decide demo.
- Each step now carries a `videoSrc` value pointing to the public video asset.

How It Works page:

- `frontend/app/how-it-works/page.tsx`
- PRODUCT VIEW cards now render the demo videos directly.
- The old overlay copy was removed because it blocked the screen recording content.
- The page keeps the existing product narrative and uses the videos only as embedded visual proof.

Home demo panel:

- `frontend/app/ui.tsx`
- The homepage demo panel now uses the final decision/adaptation demo video.
- The old overlay text was removed, including the previous path/notice copy.

Video styling:

- `frontend/app/globals.css`
- Added video frame styling for embedded demo assets.
- The current implementation keeps the renderer plain and lightweight.

Video assets:

- `frontend/public/video/demo-create-trip.mp4`
- `frontend/public/video/demo-share-invite.mp4`
- `frontend/public/video/demo-build-plan.mp4`
- `frontend/public/video/demo-decide-flow.mp4`

## Product Contract

The videos are treated as static frontend assets.

Recordly remains an external production tool:

```text
Cadensy local app
browser recording
Recordly export
frontend/public/video/*.mp4
native frontend video embed
```

Do not import Recordly source code into Cadensy.

Do not add Remotion or video-shotcraft runtime dependencies for the current Product View work. video-shotcraft may remain a design reference for pacing, zoom, and framing, but it is not part of the application.

## Validation

Local validation completed before deployment:

- `npm run build` from `frontend`
- `node --test tests/rendered-html.test.mjs tests/trip-preview-integration.test.mjs`
- Broader frontend regression check included session-runtime login cutover coverage during the same workstream.

Production validation after deployment:

- `https://app.cadensy.top/api/health` returned `200`
- `https://app.cadensy.top/login` returned `200`
- `https://app.cadensy.top/how-it-works` returned `200`
- All four video URLs returned `200`:
  - `https://app.cadensy.top/video/demo-create-trip.mp4`
  - `https://app.cadensy.top/video/demo-share-invite.mp4`
  - `https://app.cadensy.top/video/demo-build-plan.mp4`
  - `https://app.cadensy.top/video/demo-decide-flow.mp4`

## Deployment

GitHub commit deployed:

- `d0a934f Add product demo videos`

AWS deployment path:

- GitHub Actions workflow: `phase8-frontend-provision.yml`
- Workflow run: `https://github.com/shnnzdx/cap_stone/actions/runs/32800956720`
- Result: success
- Production URL: `https://app.cadensy.top`

The deployment rebuilt and pushed the frontend image, then updated the ECS frontend service behind the existing ALB/domain routing.

## Notes For Next Work

- Backend was not changed for this video update.
- No frontend Markdown renderer or extra media dependency was added.
- `.env` secrets were used only by existing deployment tooling and must not be copied into docs or committed.
- If the demo videos are replaced later, keep the same public paths when possible to avoid product-page code changes.
