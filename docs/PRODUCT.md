# Cadensy Product Logic

Audience:

- product teammates
- designers
- future maintainers

This file explains product behavior, not low-level implementation.

Related docs:

- `PROPOSAL.md`
- `../trip/FRONTEND.md`
- `../trip/BACKEND.md`
- `../交接.md`

## 1. What The Product Does

Cadensy helps a group maintain one living travel plan without needing a meeting for every
small change.

The product is not just an itinerary generator. It uses itinerary generation as one part of a
larger goal:

- fair group decision-making
- private constraint protection
- lightweight change handling

Core claims:

1. Most itinerary changes should not interrupt everyone.
2. When group input is needed, use fast structured choices before falling back to chat.
3. Private constraints may affect outcomes without being publicly exposed.

## 2. Roles

Roles belong to a trip, not to the account identity alone.

| Capability | Organizer | Participant | Guest |
| --- | :--: | :--: | :--: |
| View plan | Yes | Yes | Yes |
| Submit own preferences | Yes | Yes | Yes |
| Propose changes | Yes | Yes | Yes |
| Vote and confirm | Yes | Yes | Yes |
| Comment publicly | Yes | Yes | Yes |
| View members | Yes | No | No |
| Create invite links | Yes | No | No |
| Handle deadlock exit | Yes | No | No |
| Use dashboard across trips | Yes | Yes | No |

Non-negotiable rules:

- organizer preference weight is not higher than anyone else's
- organizers cannot read private preference wording
- nobody can decide for another person

Guests are not reduced-rights trip members. The difference is mainly account persistence and
cross-trip ownership.

## 3. Full Journey

### Create

An organizer creates a trip with:

- trip name
- destination
- date window
- expected group size
- currency
- preference deadline

### Join

Members join through an invite flow and only become members after an explicit join step.

### Preferences

Each member can provide:

- preferred dates
- acceptable date range
- ideal budget
- maximum budget
- essential needs
- pace and interests

Each meaningful preference can have different visibility.

### Initial Plan

AI or rules generate an initial plan, but the result must still pass deterministic validation.
If generation cannot satisfy the hard requirements, the system should block honestly instead of
pretending the plan is valid.

### Ongoing Use

After a plan exists, all meaningful edits route through the backend decision system.

## 4. How Changes Enter The Plan

Each itinerary slot has a settledness level:

- `loose`
- `touched`
- `settled`
- `booked`

The routing order is:

1. hard blocker check
2. reopened settled decision
3. contested or touched slot
4. direct notice

### Notice

The change applies immediately and the group gets an anonymous update notice.

Meaning:

- no one is forced into extra work
- members can still object later
- silence functions as acceptance because objection is cheap

### Round

The slot becomes a decision card with multiple options. Members respond in parallel. Silence
does not count as explicit agreement, but it also does not block settlement forever.

### Reopened Round

Settled decisions are harder to overturn. A reopened round requires a written reason and a
stronger support threshold.

### Confirm

Only affected members must confirm. The proposal applies only after all required participants
accept.

If Confirm deadlocks, the organizer can close the escalated proposal with one
of three resolution actions:

- **keep** — reject the pending proposal and preserve the live Current Plan
  item.  The proposed patch is not applied, and this does not restore an old
  snapshot over a later valid change.
- **split** — split the block so the groups can follow separate plans and
  regroup afterwards.
- **clear** — remove the disputed activity from the shared itinerary.

Each action closes the deadlock and leaves an auditable Plan Change. `keep` is
not a vote, does not give the organizer any additional member decision power,
and does not bypass the existing required, settled, or booked protections: it
only rejects the pending proposal. The organizer still cannot simply impose
one side's proposed change.

## 5. Preferences And Constraints

The product distinguishes ideals from boundaries.

Examples:

| Topic | Ideal | Boundary |
| --- | --- | --- |
| Dates | preferred trip days | widest acceptable range |
| Budget | ideal spend | maximum spend |

Supported hard-constraint categories currently include:

- `time_window`
- `budget_ceiling`
- `date_range`
- `walk_limit`
- `dietary`
- `avoid_tag`

If a user need does not fit the enforceable categories, the system should not pretend it can
guarantee that rule.

## 6. Privacy

The product promise is structural privacy, not best-effort wording.

The system should be able to say:

- a private constraint is affected
- one member is affected
- a time or budget rule is involved

It should not say:

- who the private member is
- their raw wording
- hidden member-only reasoning

Important nuance:

- the system should not claim impossible anonymity
- in a small group, people may still infer who a constraint belongs to
- the promise is that the system itself does not expose private details

## 7. AI Responsibilities

AI appears in these product roles:

- translate natural-language preferences into structured constraints
- explain trade-offs and confidence
- mediate anonymous conflict conversations
- generate or repair itinerary candidates
- suggest options for contested slots
- support private dry-run chat

AI does not own:

- final path classification
- vote counting
- confirm acceptance rules
- private data disclosure policy
- canonical place names: itinerary generation selects an opaque candidate ID, while the
  Place Library supplies the English and local names shown in the Current Plan

For a normal full travel day, itinerary quality should follow this priority order:

1. hard constraints
2. fixed or locked events
3. trip dates and user availability
4. opening hours
5. geographic feasibility
6. reasonable timing and transitions
7. meal timing
8. soft preferences and interests
9. daily variety

Lunch and dinner should normally have natural opportunities in the 11:30–14:00 and
17:30–20:00 windows. Concrete restaurants are preferred: selection first searches the nearby
route cluster, then expands a bounded coordinate radius, then relaxes lunch/dinner-specific food
categories, and only then uses an honest flexible break. Dinner does not treat a café as a
restaurant. Meal stability must not be achieved by inventing a venue or overriding a
higher-priority rule.

Meals are schedule sections, not sightseeing quota. Candidate ranking combines clear provider
names, travel relevance, category quality, interests, and coordinate proximity while preserving
category and neighborhood diversity. Coordinate distance may guide clustering, but must not be
presented as a real walking route or travel time.

When provider opening hours are safely parseable, a place known to be closed is not scheduled.
Missing or unparseable hours remain unknown rather than being presented as all-day availability.
An optional post-dinner activity is allowed only for a nearby, high-value candidate explicitly
suited to evenings.

If preferences or enforceable constraints change after a plan exists, the shared plan stays
unchanged and is marked as needing refresh. Later replans and change evaluation use the latest
inputs; saving a preference never silently rewrites existing itinerary decisions.

## 8. Trust And Provenance

Data quality needs visible provenance.

My Trips city covers are Trip-level presentation data. A provider-backed cover must retain visible
photographer and source attribution, use a card-sized hotlinked rendition, and be cached on the
Trip. Missing or unavailable provider data uses the neutral Travel cover and never substitutes a
Place or activity photo from another city.

The product should distinguish between:

- verified information
- AI-estimated information
- manually curated information
- not-yet-verified information

The model should not self-certify trust.

## 9. Important Edge Cases

These product decisions matter:

- one slot should not have multiple unresolved decision objects at the same time
- hard constraints should not silently disappear because of convenience
- strict preference changes should not automatically overwrite previously settled group choices
- booked items are more expensive to change and should route more conservatively
- if AI cannot produce a valid plan, blocked is a valid result

## 10. Scope

Current practical MVP shape:

- small groups
- one-city planning emphasis
- 2-5 day trips
- curated place library
- no live booking infrastructure required

Out of scope for this product layer:

- full payment splitting
- multi-city optimization
- live travel commerce
- crisis replanning as a separate ops system

## 11. Success Criteria

Functional success looks like this:

- members can join and submit visible and private needs
- the system can generate a viable initial plan or explain why not
- most changes resolve without heavy coordination
- contested changes route into clear structured decisions
- hard constraints stay protected
- change history remains auditable

Practical product metrics can include:

- preference completion rate
- time to first viable plan
- share of changes resolved through Notice
- round/confirm frequency
- hard-constraint satisfaction rate
- amount of accepted plan retained after revisions
