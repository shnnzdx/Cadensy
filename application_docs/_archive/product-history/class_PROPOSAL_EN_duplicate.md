# Group Travel Decision Engine — Project Proposal

Updated: August 13, 2026

Positioning:

This is not an AI that merely generates itineraries. It is a group decision engine that helps
several people negotiate, protects hard constraints, explains trade-offs, and pushes the group
toward an actual decision.

This document is the source of truth for product rationale and business case. Technical and
implementation context lives in:

- `../trip/BACKEND.md`
- `../交接.md`

## 1. Business Problem

Group travel planning usually turns into one organizer:

- collecting scattered chat opinions
- remembering everyone's dates, budget, and needs
- resolving conflicts manually
- rebuilding the whole plan whenever conditions change

This creates several structural failures:

- decision fatigue kills trips
- hard constraints get lost in chat
- louder members dominate quieter ones
- private financial or personal limits are often never said out loud
- ordinary polls cannot distinguish a preference from a boundary

The real competitor is not another AI startup. It is:

- group chat
- spreadsheets
- poll tools
- travel sites
- one unusually motivated organizer

## 2. Differentiation

Cadensy is different in four important ways:

1. AI is a facilitator, not just a planner.
2. Hard constraints and soft preferences are treated separately.
3. Private input can still shape group-safe output.
4. Compromises are explainable.

The real product value is not “better travel recommendations.” It is “better group decisions.”

## 3. Core Mechanism

Cadensy maintains one living Current Plan.

There is no final Lock and no separate Final publish step.

Each proposed change routes through one of four paths:

- `notice`
- `round`
- `reopen_round`
- `confirm`

The product should always take the cheapest valid path first.

### Routing Order

1. Hard boundary hit means Confirm.
2. Already-settled slot means Reopened Round.
3. Previously touched or contested slot means Round.
4. Otherwise use Notice.

### Product Claim

- Most changes should apply immediately with an anonymous notice.
- Some changes should require a round.
- Very few should require a real conversation.

## 4. Roles And Fairness

Roles belong to the trip, not just the user account.

The same person can be an organizer in one trip and a participant in another.

Three non-negotiable rules:

1. Organizer preferences do not carry more weight.
2. Organizers cannot read private raw preference wording.
3. Nobody can decide for another member.

If Confirm deadlocks, the organizer still cannot simply choose a side. They can:

- keep the current plan, which rejects the pending proposal without applying it
- split up
- clear the slot

`keep` is a neutral rejection, not an extra organizer vote or an adoption of
the blocked proposal.

## 5. AI Responsibilities

AI currently serves these product roles:

- translate natural-language preferences into structured constraints
- generate the initial itinerary
- explain why a plan works
- propose multiple options for a contested slot
- support private read-only dry runs and explanations in chat

AI does not own:

- final path classification
- settlement logic
- member acceptance
- private data disclosure

## 6. Fairness Logic

Recommendation order should prioritize:

1. minimizing hard-constraint violations
2. raising the lowest individual satisfaction
3. raising average satisfaction
4. reducing spread between members
5. reducing concentrated sacrifice on one person

Do not claim a plan is objectively fair. Describe it as the option that best accommodates the
confirmed inputs and available data.

## 7. Data Confidence

The MVP does not use live booking inventory, so travel facts need provenance labels:

- `verified`
- `ai_estimate`
- `mock`
- `not_verified`

Labels come from code or data provenance, not model self-reporting.

## 8. Scope

The current MVP is best suited to:

- small groups
- one-city planning
- 2-5 day trips
- curated place libraries

In scope:

- trip creation
- invitations
- preference submission and AI structuring
- privacy visibility
- conflict analysis
- initial plan generation
- four-path change routing
- anonymous negotiation
- decision logging
- explanations and confidence labels

Out of scope:

- complex multi-city optimization
- live booking
- payments
- crisis replanning
- complex expense splitting

## 9. Success Criteria

At minimum, the product should prove that:

- multiple members can join one trip
- each person can submit public and private needs
- the system can identify at least one real conflict
- the system can generate a viable plan or honestly report `blocked`
- proposed changes route correctly
- hard constraints are not silently violated
- the decision log can replay how the plan was formed

Useful metrics:

- preference completion rate
- time to first plan
- Notice / Round / Confirm distribution
- conversation count
- hard-constraint satisfaction rate

## 10. Risks

Main risks include:

- scope creep
- AI misunderstanding inputs
- perceived unfairness
- low participation
- anonymity inference in small groups

Mitigations include:

- narrowing the scenario
- requiring structured confirmation
- showing trade-off explanations
- never treating silence as agreement
- being honest about anonymity limits

## 11. Business Case

Group travel is low-frequency, so a traditional annual subscription is probably weak.

More plausible directions:

- affiliate or booking revenue after a plan settles
- one-time Trip Pass
- premium live data and dynamic replanning
- B2B group planning for retreats or student travel

For the capstone, the more important validation questions are:

1. Will users submit their real needs?
2. Does the AI-assisted workflow shorten time to a workable group plan?
3. Do users understand and trust the trade-off explanations?
