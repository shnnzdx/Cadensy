# 3D Collaborative Idea Sphere

## Homepage Hero 3D idea sphere design record

**Status:** current implemented direction  
**Scope:** homepage Hero only  
**Current implementation:** `frontend/app/IdeaSphereCanvas.tsx`, `frontend/app/ui.tsx`, `frontend/app/globals.css`  
**Design language:** pale blue, soft spatial data, collaborative intelligence  
**Reference:** Auros-style volumetric particle presence, adapted into TripSync's calm travel-product identity. Do not copy Auros colors, typography, shader, or source behavior.

---

## 1. Product Meaning

The sphere represents many independent traveler inputs before they become one shared plan.

Each particle is one independent idea, constraint, preference, or concern:

- budget ceiling
- hotel preference
- activity interest
- accessibility need
- travel timing
- food preference
- privacy note
- transportation preference

The whole sphere is not the final plan. It is the group's uncoordinated idea space: many voices coexisting before TripSync resolves them into one plan.

The visual should communicate:

- individual ideas remain distinct
- the group has one shared problem space
- the system feels alive, but calm
- the composition feels premium and intelligent
- this is a travel collaboration product, not a generic WebGL demo

Avoid visual readings such as:

- Earth or planet
- AI brain
- blockchain network
- crypto token
- radar
- portal
- Matrix rain
- cyberpunk data globe

---

## 2. Design Read

Reading this as a premium collaborative travel homepage Hero for modern consumers, using a soft spatial-data language rather than a hard tech or cyberpunk language.

Working dials:

```text
DESIGN_VARIANCE: 7
MOTION_INTENSITY: 7
VISUAL_DENSITY: 4
```

Interpretation:

- The silhouette can be organic and slightly imperfect.
- The sphere must still be clearly readable as a volumetric sphere.
- Motion should be visible but ambient.
- Particle density should be rich but not packed solid.
- The center must keep air and depth.

---

## 3. Current Implemented Architecture

The sphere is implemented as native Three.js inside a client component.

```text
BrandConstellation
  -> .constellation.idea-sphere-stage
    -> IdeaSphereCanvas
      -> .idea-sphere-canvas
        -> transparent WebGL canvas
        -> HTML tooltip overlay
```

Current files:

```text
frontend/app/ui.tsx
frontend/app/IdeaSphereCanvas.tsx
frontend/app/globals.css
frontend/package.json
frontend/package-lock.json
```

Current dependency:

```text
three
@types/three
```

Do not introduce React Three Fiber for the current version. The direct Three.js implementation is intentional and keeps the rendering path small.

---

## 4. Rendering Model

Use:

```text
THREE.Scene
THREE.PerspectiveCamera
THREE.WebGLRenderer
THREE.Points
THREE.BufferGeometry
THREE.ShaderMaterial
transparent renderer
```

Do not create one React element per particle.

Continuous animation state should live in refs, shader uniforms, local variables, and requestAnimationFrame. React state should not own pointer position, frame values, or tooltip projection.

Current renderer setup:

```text
alpha: true
antialias: true
powerPreference: high-performance
clearColor: transparent
```

Device pixel ratio is capped:

```text
desktop DPR cap: 1.8
mobile/tablet DPR cap: 1.35
```

The render loop is paused when the canvas is not intersecting the viewport.

---

## 5. Particle Count

Current responsive particle counts:

```text
mobile width < 640:
  visual particles: 720
  interactive particles: 44

tablet width < 980:
  visual particles: 1600
  interactive particles: 86

desktop:
  visual particles: 3800
  interactive particles: 148
```

This is enough for the current visual direction. Do not increase particle count as a first response to visual tuning.

Priority:

```text
stable frame rate
>
more particles
```

---

## 6. Sphere Geometry

The sphere is generated from randomized spherical points with layered radii and local field distortion.

Current radius distribution:

```text
outer layer: 0.82 to 1.07
middle layer: 0.60 to 0.93
inner layer: 0.38 to 0.66
```

Current layer weighting:

```text
outer: 68%
middle: 25%
inner: 7%
```

The base shape is slightly non-perfect:

```text
x scale: about 1.03 plus small random variation
y scale: about 1.09 plus small random variation
z scale: about 0.96 plus small random variation
```

This makes the sphere feel like an information cloud rather than a perfect tutorial sphere.

Preserve:

- sphericalPoint distribution
- layerRadius distribution
- localField distortion
- outerSpill behavior
- densityWave variation
- depth-based visual hierarchy

---

## 7. Depth and Visual Hierarchy

Depth is built through position, point size, opacity, color, and brightness.

Current depth behavior:

- rear particles are smaller, softer, and lower opacity
- mid-depth particles are pale blue
- front particles are brighter and larger
- active/anchor particles receive subtle highlight color

Current color system:

```text
rear:      #b5c7df
soft:      #d0e2fb
mid:       #afcdf7
active:    #82b5f5
highlight: #5f9ef1
aura:      #d8e9fb at very low opacity
```

Avoid:

- neon bloom
- purple glow
- hard cyberpunk contrast
- multi-color category coding
- network lines
- latitude/longitude lines

---

## 8. Shader Design

The current shader is intentionally simple:

- vertex shader handles local drift and point size
- fragment shader draws soft circular particles
- depth affects point size and brightness
- alpha remains per-particle

Important current shader values:

```text
depthScale = clamp(2.32 / -mvPosition.z, 0.42, 2.05)
brightness depth clamp = 0.82 to 1.24
```

Particle shape:

```text
soft circular point
no hard outline
no square HUD glyph
no text glyph
```

Do not rewrite the shader unless a concrete rendering defect requires it.

---

## 9. Motion

The sphere should feel alive, not mechanical.

Current motion layers:

1. slow Y rotation
2. tiny low-frequency rotation-speed variation
3. local per-particle shader drift
4. very subtle breathing
5. pointer parallax through group rotation

Current rotation:

```text
group.rotation.y += 0.00115 + sin(elapsed * 0.18) * 0.00024
```

Current breathing:

```text
scale = 1 + sin(elapsed * 0.42) * 0.004
```

Current pointer parallax:

```text
yaw target:   +/- 0.07 rad
pitch target: +/- 0.04 rad
damping:
  x rotation lerp: 0.045
  z rotation lerp: 0.035
```

Reduced motion:

- disables rotation
- disables drift
- disables breathing
- disables hover label flow through the reduced-motion branch
- preserves a static readable sphere

---

## 10. Interaction

Only a subset of particles is interactive.

Current interactive selection:

- randomly selected from visual particles
- excludes particles too close to the center
- interactive count varies by viewport
- every seventh interactive particle gets an anchor boost

Current hover behavior:

- screen-space nearest particle search
- hover radius starts at 30px
- about 70ms hover intent before label appears
- hovered particle grows by 1.34x
- hovered particle alpha increases up to 1
- hovered particle color lerps toward highlight
- old hover visual is restored immediately when candidate changes

Hover priority is currently simple:

```text
active hover
>
pending hover
>
base particle visual
```

There is no extraction, auto label, or spawning in the current implemented version.

---

## 11. Tooltip Model

Tooltip ownership is ref-driven.

React creates the DOM once:

```text
div.idea-sphere-label
  span
  small Independent input
```

Runtime refs own:

- text
- transform
- opacity
- visibility

Do not reintroduce React state for tooltip position or visibility.

Reason:

The tooltip position is derived from a Three.js projection and can update every animation frame. React should not rerender for this.

Current hidden state:

```text
opacity: 0
visibility: hidden
transform: translate3d(-9999px, -9999px, 0)
textContent: ""
```

Tooltip visibility requires:

- pointer is inside the canvas
- reduced motion is not active
- an interactive particle is detected
- same particle passes hover intent
- projection is finite
- projected x/y are inside canvas bounds
- label placement is finite
- projection belongs to the same active candidate

Never allow a missing transform, `x = 0`, `y = 0`, or fallback text to show a tooltip.

There is no visible fallback label such as "Group idea".

---

## 12. Tooltip Visual Design

Current label styling:

```text
width: 144px
padding: 7px 10px 8px
border-radius: 12px
background: rgba(255,255,255,.58)
border: 1px solid rgba(142,170,201,.24)
box-shadow: 0 10px 24px rgba(47,105,185,.07)
backdrop-filter: blur(8px)
```

Text:

```text
main label: 12px, 600 weight
sub label: 10px, "Independent input"
```

Label placement:

- if particle is right-side, label prefers left
- if particle is low, label prefers above
- x/y are clamped inside canvas bounds

The tooltip is an annotation, not a card UI.

---

## 13. Hero Layout Model

The current Hero is a natural-flow content section, not a viewport-constrained composition.

Do not force headline, sphere, CTA, and fine print to all fit in the first viewport.

Current desktop layout rules:

```css
.product-page .hero {
  min-height: auto;
  display: grid;
  grid-template-columns: 1fr;
  align-items: start;
  gap: 0;
  padding-top: clamp(64px,8vh,84px);
  padding-bottom: clamp(72px,9vh,110px);
}
```

The Hero height is determined by content.

This is intentional:

- headline-to-sphere spacing can be tuned independently
- sphere-to-CTA spacing can be tuned independently
- CTA may sit below the first fold on shorter screens
- the sphere does not need to shrink to fit an artificial viewport box
- the next section begins naturally after the Hero

---

## 14. Page-Level Spacing

Do not use canvas inset, top/bottom offsets, transform, or Three.js camera values to solve page spacing.

Separation of responsibility:

```text
Three.js / shader:
  sphere rendering

.idea-sphere-canvas:
  internal canvas composition only

.idea-sphere-stage:
  sphere visual area and sphere-to-CTA spacing

.hero-copy:
  headline-to-sphere spacing

.hero:
  section padding and natural flow
```

Current desktop spacing:

```css
.product-page .hero-copy {
  margin-bottom: clamp(56px,7vh,72px);
}

.product-page .idea-sphere-stage {
  margin: 0 0 clamp(56px,7vh,70px) 50%;
}

.product-page .hero-actions .fineprint {
  margin: clamp(14px,2vh,20px) 0 0;
}
```

Observed desktop result at 1440 x 940:

```text
Hero natural height: about 1022px
headline-to-sphere: about 66px
sphere-to-CTA: about 66px
fine print to Hero bottom: about 85px
horizontal overflow: 0
```

---

## 15. Stage and Canvas Sizing

Current desktop stage:

```css
.product-page .idea-sphere-stage {
  width: min(1120px,100dvw);
  height: clamp(430px,46vh,500px);
}
```

This size is the accepted current middle ground:

- larger than the earlier 330 to 380px regression
- smaller than the previous 500 to 650px oversized version
- strong enough to dominate the Hero
- allowed to push CTA slightly below the first fold

Current desktop canvas:

```css
.idea-sphere-canvas {
  position: absolute;
  inset: -8% -7%;
  min-height: 100%;
}
```

This canvas inset is now an internal composition choice only. It should not be used to control the page-level distance between headline, sphere, and CTA.

Responsive canvas:

```css
@media (max-width:900px) {
  .idea-sphere-canvas { inset: -5% -8%; }
}

@media (max-width:620px) {
  .idea-sphere-canvas {
    inset: -6% -14%;
    cursor: default;
  }
}
```

---

## 16. Responsive Layout

Tablet:

```css
.product-page .hero {
  padding-top: 88px;
  gap: 0;
}

.product-page .hero-copy {
  margin-bottom: clamp(52px,7vw,68px);
}

.product-page .idea-sphere-stage {
  width: 100%;
  height: clamp(470px,58vh,590px);
  margin: 0 0 clamp(52px,7vw,66px);
  transform: none;
}
```

Mobile:

```css
.product-page .hero {
  padding-block: 68px 88px;
}

.product-page .hero-copy {
  margin-bottom: 44px;
}

.product-page .idea-sphere-stage {
  height: 440px;
  margin-bottom: 44px;
}
```

Mobile should stack naturally:

```text
headline
sphere
CTA
fine print
next section
```

Do not force mobile Hero content into one viewport.

---

## 17. Current Implementation Boundaries

Current version includes:

- volumetric sphere
- depth hierarchy
- slow rotation
- local shader drift
- subtle breathing
- pointer parallax
- interactive subset
- hover particle visual
- ref-driven tooltip
- reduced-motion handling
- responsive particle counts
- natural-flow Hero layout

Current version intentionally does not include:

- particle extraction
- splines
- trails
- dynamic spawning
- auto labels
- selected state
- featured idea cycle
- GSAP or Motion
- React Three Fiber
- React state for frame values

Those ideas were removed from this document because they are not part of the accepted current sphere design.

---

## 18. Rules for Future Edits

Do not change these unless there is a clear reason:

- `pickCount()`
- `layerRadius()`
- `sphericalPoint()`
- localField / outerSpill / densityWave
- shader drift
- depthScale
- particle count
- camera position
- tooltip ref-driven ownership
- 70ms hover intent
- Hero natural-flow model
- stage height `clamp(430px,46vh,500px)`

When tuning composition:

- use `.hero-copy` margin for headline-to-sphere spacing
- use `.idea-sphere-stage` margin for sphere-to-CTA spacing
- use `.hero` padding for section top/bottom breathing
- use `.idea-sphere-canvas` only for internal sphere framing

Do not tune page spacing by:

- changing Three.js camera
- changing particle size
- changing shader scale
- using canvas `top` or `bottom` as a layout hack
- forcing the Hero into `100vh`
- shrinking the sphere to keep CTA above the fold

---

## 19. Acceptance Checklist

The current sphere design is acceptable when:

- [ ] It reads as a true volumetric 3D point cloud, not a flat image.
- [ ] It reads as a sphere without becoming a literal planet.
- [ ] The center keeps visible air.
- [ ] Foreground, middle, and rear particles are distinguishable.
- [ ] Particle sizes, alpha, brightness, and density vary naturally.
- [ ] Motion feels alive but calm.
- [ ] Pointer parallax reveals depth without making the object chase the cursor.
- [ ] Hover highlights one idea without darkening the whole sphere.
- [ ] Tooltip never appears at top-left.
- [ ] Tooltip has one owner: refs, not React state.
- [ ] No fallback tooltip text appears when hover is invalid.
- [ ] Reduced motion leaves a readable static sphere.
- [ ] Desktop, tablet, and mobile have no horizontal overflow.
- [ ] Hero height is natural content height.
- [ ] CTA remains normal-flow content.
- [ ] Sphere remains visually dominant.
- [ ] The design still feels like TripSync travel collaboration, not a generic tech demo.

---

## 20. Final Visual Judgment

Review the sphere in this order:

```text
1. Does it have real volume?
2. Is there air inside the sphere?
3. Is the depth hierarchy clear?
4. Is the silhouette stable?
5. Does local motion feel alive?
6. Are individual ideas still readable as separate particles?
7. Does hover clarify product meaning?
8. Does the Hero flow naturally instead of behaving like a fixed viewport box?
9. Does it belong to this travel planning product?
```

If the answer to item 9 is no, the design is not finished, even if the WebGL effect is technically impressive.
