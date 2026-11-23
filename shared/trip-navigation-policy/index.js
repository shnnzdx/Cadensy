/**
 * @typedef {"participant" | "organizer"} MembershipRole
 * @typedef {"guest" | "account"} IdentityKind
 * @typedef {"signed-out" | "session-no-trip-access" | "trip-session"} AccessState
 * @typedef {"planning" | "active" | "archived" | "unknown"} TripState
 * @typedef {"plan" | "chat" | "conflict" | "updates" | "preferences" | "members" | "invite"} TripSection
 * @typedef {"profile" | "travel" | "notifications" | "settings"} AccountSection
 *
 * @typedef {{ kind: "home" }} HomeRouteRef
 * @typedef {{ kind: "create-trip" }} CreateTripRouteRef
 * @typedef {{ kind: "account", section: AccountSection }} AccountRouteRef
 * @typedef {{ kind: "trip", tripId: string, section: TripSection }} TripRouteRef
 * @typedef {{ kind: "join", token: string }} JoinRouteRef
 * @typedef {HomeRouteRef | CreateTripRouteRef | AccountRouteRef | TripRouteRef | JoinRouteRef} WorkspaceRouteRef
 *
 * @typedef {{
 *   role: MembershipRole,
 *   identityKind: IdentityKind,
 * }} MembershipFact
 *
 * @typedef {{
 *   membership?: MembershipFact,
 *   state?: TripState,
 * }} RelevantTripFact
 *
 * @typedef {{
 *   token: string,
 *   validity: "valid" | "invalid" | "unknown",
 *   tripId?: string,
 * }} InviteFact
 *
 * @typedef {{
 *   currentRoute?: WorkspaceRouteRef,
 *   returnTarget?: WorkspaceRouteRef,
 *   restoredSelection?: {
 *     tripId: string,
 *     preferredSection?: TripSection,
 *   },
 *   inviteFlow?: {
 *     token: string,
 *     step: "open" | "complete",
 *   },
 * }} DestinationIntent
 *
 * @typedef {{
 *   accessState: AccessState,
 *   relevantTripsById: Record<string, RelevantTripFact>,
 *   invite?: InviteFact,
 *   intent: DestinationIntent,
 * }} DestinationResolutionInput
 *
 * @typedef {{
 *   accessState: AccessState,
 *   relevantTripsById: Record<string, RelevantTripFact>,
 *   currentRoute: WorkspaceRouteRef,
 * }} NavigationDescriptionInput
 *
 * @typedef {{
 *   intent: "invite-flow" | "return-target" | "restored-selection" | "current-route" | "default",
 *   code: string,
 *   detailCode?: string,
 * }} RejectedIntentDiagnostic
 *
 * @typedef {{
 *   acceptedIntent: "invite-flow" | "return-target" | "restored-selection" | "current-route" | "default",
 *   acceptedCode: string,
 *   rejectedIntents: RejectedIntentDiagnostic[],
 *   fallbackApplied: boolean,
 * }} ResolutionDiagnostics
 *
 * @typedef {{
 *   disposition: "allow" | "redirect",
 *   destination: WorkspaceRouteRef,
 *   diagnostics?: ResolutionDiagnostics,
 * }} DestinationResolution
 *
 * @typedef {{
 *   id: string,
 *   destination: WorkspaceRouteRef,
 *   active: boolean,
 * }} NavigationEntry
 *
 * @typedef {{
 *   contextRoute: WorkspaceRouteRef | null,
 *   entries: NavigationEntry[],
 *   diagnostics?: {
 *     hiddenEntryCodes: Array<{ id: string, code: string }>,
 *   },
 * }} NavigationDescription
 */

const ORGANIZER_ONLY_SECTIONS = new Set(["members", "invite"]);
const TRIP_NAV_ORDER = ["plan", "chat", "updates", "preferences"];
const ORGANIZER_TRIP_NAV_ORDER = [...TRIP_NAV_ORDER, "members", "invite"];
const ACCOUNT_NAV_ORDER = [
  ["account-profile", "profile"],
  ["account-travel", "travel"],
  ["account-notifications", "notifications"],
  ["account-settings", "settings"],
];

class PolicyContractError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   */
  constructor(code, message) {
    super(message);
    this.name = "PolicyContractError";
    this.code = code;
  }
}

/**
 * @param {DestinationResolutionInput} input
 * @param {{ explain?: boolean }} [options]
 * @returns {DestinationResolution}
 */
function resolveDestination(input, options = {}) {
  validateBaseInput(input);

  const diagnostics = createResolutionDiagnostics();
  const candidates = [
    buildInviteCandidate(input),
    buildReturnTargetCandidate(input),
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (!candidate.valid) {
      diagnostics.rejectedIntents.push({
        intent: candidate.intent,
        code: candidate.code,
        ...(candidate.detailCode ? { detailCode: candidate.detailCode } : {}),
      });
      continue;
    }
    diagnostics.acceptedIntent = candidate.intent;
    diagnostics.acceptedCode = candidate.code;
    diagnostics.fallbackApplied = false;
    return finalizeResolution(
      candidate.destination,
      input.intent.currentRoute,
      diagnostics,
      options,
    );
  }

  const currentRouteResult = evaluateCurrentRoute(input);
  if (currentRouteResult.valid && shouldPreferCurrentRouteBeforeRestoration(input)) {
    diagnostics.acceptedIntent = "current-route";
    diagnostics.acceptedCode = currentRouteResult.code;
    diagnostics.fallbackApplied = false;
    return finalizeResolution(
      currentRouteResult.destination,
      input.intent.currentRoute,
      diagnostics,
      options,
    );
  }

  const restoredSelectionCandidate = buildRestoredSelectionCandidate(input);
  if (restoredSelectionCandidate) {
    if (restoredSelectionCandidate.valid) {
      diagnostics.acceptedIntent = restoredSelectionCandidate.intent;
      diagnostics.acceptedCode = restoredSelectionCandidate.code;
      diagnostics.fallbackApplied = false;
      return finalizeResolution(
        restoredSelectionCandidate.destination,
        input.intent.currentRoute,
        diagnostics,
        options,
      );
    }
    diagnostics.rejectedIntents.push({
      intent: restoredSelectionCandidate.intent,
      code: restoredSelectionCandidate.code,
      ...(restoredSelectionCandidate.detailCode ? { detailCode: restoredSelectionCandidate.detailCode } : {}),
    });
  }

  if (currentRouteResult.valid) {
    diagnostics.acceptedIntent = "current-route";
    diagnostics.acceptedCode = currentRouteResult.code;
    diagnostics.fallbackApplied = false;
    return finalizeResolution(
      currentRouteResult.destination,
      input.intent.currentRoute,
      diagnostics,
      options,
    );
  }
  if (currentRouteResult.code) {
    diagnostics.rejectedIntents.push({
      intent: "current-route",
      code: currentRouteResult.code,
    });
  }

  const fallback = computeFallback(input, diagnostics);
  diagnostics.acceptedIntent = "default";
  diagnostics.acceptedCode = fallback.code;
  diagnostics.fallbackApplied = true;
  return finalizeResolution(
    fallback.destination,
    input.intent.currentRoute,
    diagnostics,
    options,
  );
}

function shouldPreferCurrentRouteBeforeRestoration(input) {
  const route = input.intent.currentRoute;
  if (!route) return false;
  // A reachable trip URL is the active trip. This runs only after that route
  // has already been validated, so a missing membership or an organizer-only
  // section still falls through to the existing redirect.
  if (route.kind === "trip") return true;
  if (route.kind !== "home" && route.kind !== "create-trip" && route.kind !== "account") {
    return false;
  }
  return hasAccountBackedMembership(reachableMembershipEntries(input.relevantTripsById));
}

/**
 * @param {NavigationDescriptionInput} input
 * @param {{ explain?: boolean }} [options]
 * @returns {NavigationDescription}
 */
function describeNavigation(input, options = {}) {
  validateBaseInput({
    accessState: input.accessState,
    relevantTripsById: input.relevantTripsById,
    intent: { currentRoute: input.currentRoute },
  });

  const membershipEntries = reachableMembershipEntries(input.relevantTripsById);
  const accountBacked = hasAccountBackedMembership(membershipEntries);
  const guestBackedOnly = hasGuestBackedMembership(membershipEntries) && !accountBacked;

  if (input.currentRoute.kind === "account") {
    const entries = ACCOUNT_NAV_ORDER.map(([id, section]) => ({
      id,
      destination: accountRoute(section),
      active: input.currentRoute.kind === "account" && input.currentRoute.section === section,
    }));
    return {
      contextRoute: accountBacked ? homeRoute() : null,
      entries,
      ...(options.explain
        ? {
            diagnostics: {
              hiddenEntryCodes: [],
            },
          }
        : {}),
    };
  }

  const tripId = input.currentRoute.kind === "trip"
    ? input.currentRoute.tripId
    : membershipEntries[0]?.tripId || null;
  const membership = tripId ? input.relevantTripsById[tripId]?.membership || null : null;
  const isOrganizer = membership?.role === "organizer";
  const visibleSections = isOrganizer ? ORGANIZER_TRIP_NAV_ORDER : TRIP_NAV_ORDER;
  const hiddenEntryCodes = isOrganizer
    ? []
    : [...ORGANIZER_ONLY_SECTIONS].map((section) => ({ id: section, code: "role-not-authorized" }));

  const entries = tripId
    ? visibleSections.map((section) => ({
        id: section,
        destination: tripRoute(tripId, section),
        active: input.currentRoute.kind === "trip" && input.currentRoute.section === section,
      }))
    : [];

  return {
    contextRoute: guestBackedOnly ? null : accountBacked ? homeRoute() : null,
    entries,
    ...(options.explain
      ? {
          diagnostics: {
            hiddenEntryCodes,
          },
        }
      : {}),
  };
}

/**
 * @param {DestinationResolutionInput | { accessState: AccessState, relevantTripsById: Record<string, RelevantTripFact>, intent: DestinationIntent }} input
 */
function validateBaseInput(input) {
  const membershipEntries = reachableMembershipEntries(input.relevantTripsById);

  if ((input.accessState === "signed-out" || input.accessState === "session-no-trip-access") && membershipEntries.length > 0) {
    throw new PolicyContractError(
      "invalid-access-state-membership-combination",
      "Signed-out or session-no-trip-access inputs cannot include memberships.",
    );
  }

  if (membershipEntries.some(({ membership }) => membership.identityKind === "guest" && membership.role === "organizer")) {
    throw new PolicyContractError(
      "invalid-membership-role-identity-combination",
      "Guest-backed memberships cannot be organizers.",
    );
  }

  if (input.intent?.inviteFlow && input.invite && input.intent.inviteFlow.token !== input.invite.token) {
    throw new PolicyContractError(
      "invite-token-mismatch",
      "Invite flow token must match the invite fact token.",
    );
  }

  if (input.accessState === "trip-session" && membershipEntries.length === 0) {
    throw new PolicyContractError(
      "trip-session-without-membership",
      "Trip-session inputs must include at least one membership.",
    );
  }
}

/**
 * @param {DestinationResolutionInput} input
 */
function buildInviteCandidate(input) {
  const inviteFlow = input.intent.inviteFlow;
  if (!inviteFlow || !input.invite) return null;

  if (input.invite.validity !== "valid") {
    return {
      intent: "invite-flow",
      valid: false,
      code: "invite-invalid",
    };
  }

  const inviteTripId = input.invite.tripId;
  if (!inviteTripId) {
    return {
      intent: "invite-flow",
      valid: false,
      code: "trip-not-accessible",
    };
  }

  const hasMembership = Boolean(input.relevantTripsById[inviteTripId]?.membership);
  if (inviteFlow.step === "complete") {
    if (!hasMembership) {
      return {
        intent: "invite-flow",
        valid: false,
        code: "trip-not-accessible",
      };
    }
    return {
      intent: "invite-flow",
      valid: true,
      code: "invite-completion-first-membership",
      destination: tripRoute(inviteTripId, "preferences"),
    };
  }

  if (hasMembership) {
    return {
      intent: "invite-flow",
      valid: true,
      code: "invite-existing-membership",
      destination: tripRoute(inviteTripId, "plan"),
    };
  }

  return {
    intent: "invite-flow",
    valid: true,
    code: "invite-open-allowed",
    destination: joinRoute(input.invite.token),
  };
}

/**
 * @param {DestinationResolutionInput} input
 */
function buildReturnTargetCandidate(input) {
  const route = input.intent.returnTarget;
  if (!route) return null;
  const validation = validateRouteReachability(route, input);
  if (!validation.valid) {
    return {
      intent: "return-target",
      valid: false,
      code: "return-target-rejected",
      detailCode: validation.code,
    };
  }
  return {
    intent: "return-target",
    valid: true,
    code: "return-target-accepted",
    destination: route,
  };
}

/**
 * @param {DestinationResolutionInput} input
 */
function buildRestoredSelectionCandidate(input) {
  const restored = input.intent.restoredSelection;
  if (!restored) return null;
  const tripInfo = input.relevantTripsById[restored.tripId];
  if (!tripInfo || !tripInfo.membership) {
    return {
      intent: "restored-selection",
      valid: false,
      code: "stale-restored-selection",
    };
  }
  const route = tripRoute(restored.tripId, restored.preferredSection || "plan");
  const validation = validateRouteReachability(route, input);
  if (!validation.valid) {
    return {
      intent: "restored-selection",
      valid: false,
      code: validation.code,
    };
  }
  return {
    intent: "restored-selection",
    valid: true,
    code: "restored-selection-accepted",
    destination: route,
  };
}

/**
 * @param {DestinationResolutionInput} input
 */
function evaluateCurrentRoute(input) {
  const route = input.intent.currentRoute;
  if (!route) {
    return { valid: false, code: null };
  }

  if (route.kind === "join") {
    if (input.invite && input.invite.validity === "invalid") {
      return {
        valid: true,
        code: "invite-invalid",
        destination: route,
      };
    }
    if (input.intent.inviteFlow && input.invite?.validity === "valid" && !input.relevantTripsById[input.invite.tripId || ""]?.membership) {
      return {
        valid: true,
        code: "invite-open-allowed",
        destination: route,
      };
    }
    return {
      valid: true,
      code: "current-route-allowed",
      destination: route,
    };
  }

  const validation = validateRouteReachability(route, input);
  if (validation.valid) {
    return {
      valid: true,
      code: "current-route-allowed",
      destination: route,
    };
  }

  return {
    valid: false,
    code: validation.code,
  };
}

/**
 * @param {DestinationResolutionInput} input
 * @param {ResolutionDiagnostics} diagnostics
 */
function computeFallback(input, diagnostics) {
  const currentRoute = input.intent.currentRoute;
  if (currentRoute?.kind === "trip") {
    const tripInfo = input.relevantTripsById[currentRoute.tripId];
    if (tripInfo?.membership) {
      return {
        code: "role-not-authorized",
        destination: tripRoute(currentRoute.tripId, "plan"),
      };
    }
  }

  const memberships = reachableMembershipEntries(input.relevantTripsById);
  if (hasAccountBackedMembership(memberships)) {
    return {
      code: "default-home",
      destination: homeRoute(),
    };
  }

  const guestTrip = firstGuestTripId(input.relevantTripsById);
  if (guestTrip) {
    return {
      code: "default-guest-trip-plan",
      destination: tripRoute(guestTrip, "plan"),
    };
  }

  return {
    code: "default-home",
    destination: homeRoute(),
  };
}

/**
 * @param {WorkspaceRouteRef} route
 * @param {DestinationResolutionInput} input
 */
function validateRouteReachability(route, input) {
  if (route.kind === "home" || route.kind === "create-trip") {
    return input.accessState === "session-no-trip-access" ||
      hasAccountBackedMembership(reachableMembershipEntries(input.relevantTripsById))
      ? { valid: true, code: "current-route-allowed" }
      : { valid: false, code: "trip-not-accessible" };
  }

  if (route.kind === "account") {
    return input.accessState === "session-no-trip-access" ||
      hasAccountBackedMembership(reachableMembershipEntries(input.relevantTripsById))
      ? { valid: true, code: "current-route-allowed" }
      : { valid: false, code: "trip-not-accessible" };
  }

  if (route.kind === "join") {
    return { valid: true, code: "current-route-allowed" };
  }

  const tripInfo = input.relevantTripsById[route.tripId];
  if (!tripInfo || !tripInfo.membership) {
    return { valid: false, code: "trip-not-accessible" };
  }
  if (ORGANIZER_ONLY_SECTIONS.has(route.section) && tripInfo.membership.role !== "organizer") {
    return { valid: false, code: "role-not-authorized" };
  }
  return { valid: true, code: "current-route-allowed" };
}

/**
 * @param {WorkspaceRouteRef} destination
 * @param {WorkspaceRouteRef | undefined} currentRoute
 * @param {ResolutionDiagnostics} diagnostics
 * @param {{ explain?: boolean }} options
 * @returns {DestinationResolution}
 */
function finalizeResolution(destination, currentRoute, diagnostics, options) {
  const disposition = routesEqual(destination, currentRoute) ? "allow" : "redirect";
  return {
    disposition,
    destination,
    ...(options.explain ? { diagnostics } : {}),
  };
}

function createResolutionDiagnostics() {
  return {
    acceptedIntent: "default",
    acceptedCode: "default-home",
    rejectedIntents: [],
    fallbackApplied: false,
  };
}

/**
 * @param {Record<string, RelevantTripFact>} relevantTripsById
 */
function reachableMembershipEntries(relevantTripsById) {
  return Object.entries(relevantTripsById)
    .filter(([, info]) => Boolean(info?.membership))
    .map(([tripId, info]) => ({ tripId, membership: info.membership, state: info.state || "unknown" }))
    .sort((a, b) => a.tripId.localeCompare(b.tripId));
}

/**
 * @param {Array<{ tripId: string, membership: MembershipFact }>} memberships
 */
function hasAccountBackedMembership(memberships) {
  return memberships.some(({ membership }) => membership.identityKind === "account");
}

/**
 * @param {Array<{ tripId: string, membership: MembershipFact }>} memberships
 */
function hasGuestBackedMembership(memberships) {
  return memberships.some(({ membership }) => membership.identityKind === "guest");
}

/**
 * @param {Record<string, RelevantTripFact>} relevantTripsById
 */
function firstGuestTripId(relevantTripsById) {
  return reachableMembershipEntries(relevantTripsById)
    .find(({ membership }) => membership.identityKind === "guest")
    ?.tripId || null;
}

/**
 * @param {WorkspaceRouteRef | undefined} a
 * @param {WorkspaceRouteRef | undefined} b
 */
function routesEqual(a, b) {
  return JSON.stringify(a || null) === JSON.stringify(b || null);
}

function homeRoute() {
  return { kind: "home" };
}

/**
 * @param {AccountSection} section
 * @returns {AccountRouteRef}
 */
function accountRoute(section) {
  return { kind: "account", section };
}

/**
 * @param {string} token
 * @returns {JoinRouteRef}
 */
function joinRoute(token) {
  return { kind: "join", token };
}

/**
 * @param {string} tripId
 * @param {TripSection} section
 * @returns {TripRouteRef}
 */
function tripRoute(tripId, section) {
  return { kind: "trip", tripId, section };
}

export const tripNavigationPolicy = {
  resolveDestination,
  describeNavigation,
};
