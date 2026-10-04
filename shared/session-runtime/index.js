/**
 * Shared browser-capable technical session runtime.
 *
 * This module owns:
 * - private persistence key policy
 * - technical session restore/adopt/clear/invalidate
 * - active technical trip context
 * - bounded Guest bearer credential persistence
 * - token-scoped invite adoption cache mechanics
 * - request identity derivation
 * - logout lifecycle sequencing
 *
 * It intentionally does not own:
 * - endpoint URL catalogs
 * - HTTP transport
 * - React state
 * - browser navigation
 * - Trip domain hydration
 * - Candidate 1 navigation policy
 */

const AUTH_TOKEN_KEY = "tripsync:authToken";
const GUEST_TOKEN_KEY = "tripsync:guestToken";
const MEMBERSHIP_ID_KEY = "tripsync:membershipId";
const TRIP_ID_KEY = "tripsync:tripId";
const INVITE_KEY_PREFIX = "tripsync:invite:";

const MISSING_ACCOUNT_AUTH = "missing-account-auth";
const MISSING_ACTIVE_TRIP_CONTEXT = "missing-active-trip-context";
const MISSING_MEMBERSHIP_IDENTITY = "missing-membership-identity";

const PERSISTENCE_UNAVAILABLE = "persistence-unavailable";
const PERSISTENCE_READ_FAILED = "persistence-read-failed";
const PERSISTENCE_WRITE_FAILED = "persistence-write-failed";
const PERSISTENCE_CLEAR_FAILED = "persistence-clear-failed";
const PERSISTENCE_MALFORMED_DATA = "persistence-malformed-data";

const ACCOUNT_CREDENTIALS_INVALID = "account-credentials-invalid";
const MEMBERSHIP_CREDENTIALS_INVALID = "membership-credentials-invalid";

const INVALID_ADOPTION_INPUT = "invalid-adoption-input";
const INVALID_REQUEST_SCOPE = "invalid-request-scope";
const INVALID_INVITE_TOKEN = "invalid-invite-token";

/**
 * Stable public semantic codes.
 */
export const SESSION_RUNTIME_CODES = Object.freeze({
  missingContext: Object.freeze({
    MISSING_ACCOUNT_AUTH,
    MISSING_ACTIVE_TRIP_CONTEXT,
    MISSING_MEMBERSHIP_IDENTITY,
  }),
  warnings: Object.freeze({
    PERSISTENCE_UNAVAILABLE,
    PERSISTENCE_READ_FAILED,
    PERSISTENCE_WRITE_FAILED,
    PERSISTENCE_CLEAR_FAILED,
    PERSISTENCE_MALFORMED_DATA,
  }),
  invalidation: Object.freeze({
    ACCOUNT_CREDENTIALS_INVALID,
    MEMBERSHIP_CREDENTIALS_INVALID,
  }),
  errors: Object.freeze({
    INVALID_ADOPTION_INPUT,
    INVALID_REQUEST_SCOPE,
    INVALID_INVITE_TOKEN,
  }),
});

class SessionRuntimeContractError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   */
  constructor(code, message) {
    super(message);
    this.name = "SessionRuntimeContractError";
    this.code = code;
  }
}

/**
 * @typedef {{
 *   getItem(key: string): string | null,
 *   setItem(key: string, value: string): void,
 *   removeItem(key: string): void,
 * }} SessionRuntimeStorage
 */

/**
 * @typedef {{ kind: "none" }} NoTechnicalSessionFacts
 * @typedef {{ kind: "guest", guestAuth: true, activeTripId: string, membershipId: string }} GuestTechnicalSessionFacts
 * @typedef {{ kind: "account", accountAuth: true, activeTripId: string | null, membershipId: string | null }} AccountTechnicalSessionFacts
 * @typedef {NoTechnicalSessionFacts | GuestTechnicalSessionFacts | AccountTechnicalSessionFacts} TechnicalSessionFacts
 *
 * @typedef {{
 *   facts: TechnicalSessionFacts,
 *   restorationHint: { tripId: string } | null,
 *   warnings: string[],
 * }} RestoreTechnicalSessionResult
 *
 * @typedef {{
 *   token: string,
 *   activeTripId?: string | null,
 *   membershipId?: string | null,
 * }} AccountAuthAdoptionInput
 *
 * @typedef {{
 *   token: string,
 *   activeTripId: string,
 *   membershipId: string,
 *   inviteToken?: string | null,
 * }} GuestAuthAdoptionInput
 *
 * @typedef {{
 *   activeTripId: string,
 *   membershipId: string,
 *   inviteToken?: string | null,
 * }} TechnicalTripContextAdoptionInput
 *
 * @typedef {"account" | "trip"} RequestScope
 * @typedef {"missing-account-auth" | "missing-active-trip-context" | "missing-membership-identity"} MissingContextCode
 * @typedef {"account-credentials-invalid" | "membership-credentials-invalid"} InvalidationCause
 * @typedef {{
 *   activeTripId: string,
 *   membershipId: string,
 * }} InviteAdoptionRecord
 * @typedef {"persistence-unavailable" | "persistence-read-failed" | "persistence-write-failed" | "persistence-clear-failed" | "persistence-malformed-data"} PersistenceWarningCode
 * @typedef {() => Promise<void>} RevokeCapability
 */

/**
 * @param {{
 *   storage?: SessionRuntimeStorage | null,
 * }} [options]
 */
export function createSessionRuntime(options = {}) {
  let privateAccountToken = null;
  let privateGuestToken = null;

  return {
    restoreTechnicalSession,
    adoptAccountAuth,
    adoptGuestAuth,
    adoptTechnicalTripContext,
    requestIdentityFor,
    readInviteAdoption,
    invalidateTechnicalSession,
    logoutTechnicalSession,
  };

  /**
   * @returns {RestoreTechnicalSessionResult}
   */
  function restoreTechnicalSession() {
    const warnings = [];
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) {
      privateAccountToken = null;
      privateGuestToken = null;
      return {
        facts: { kind: "none" },
        restorationHint: null,
        warnings,
      };
    }

    const authToken = readString(storage, AUTH_TOKEN_KEY, warnings);
    const guestToken = readString(storage, GUEST_TOKEN_KEY, warnings);
    const membershipId = readString(storage, MEMBERSHIP_ID_KEY, warnings);
    const tripId = readString(storage, TRIP_ID_KEY, warnings);

    privateAccountToken = authToken;
    privateGuestToken = guestToken;
    const facts = buildFactsFromMaterial({
      authToken,
      guestToken,
      membershipId,
      tripId,
    });
    // Legacy Membership IDs are object identifiers, not Guest credentials.
    // Clear them rather than offering any restore or credential-exchange path.
    if (facts.kind === "none" && membershipId && tripId) {
      clearTripContextMaterial(warnings);
    }

    return {
      facts,
      restorationHint: facts.kind === "none" ? null : tripId ? { tripId } : null,
      warnings,
    };
  }

  /**
   * @param {AccountAuthAdoptionInput} input
   * @returns {{ facts: TechnicalSessionFacts, warnings: string[] }}
   */
  function adoptAccountAuth(input) {
    const token = requireNonEmptyString(
      input?.token,
      INVALID_ADOPTION_INPUT,
      "Account auth adoption requires a non-empty token.",
    );
    const activeTripId = optionalNonEmptyString(
      input?.activeTripId,
      INVALID_ADOPTION_INPUT,
      "activeTripId must be a non-empty string when provided.",
    );
    const membershipId = optionalNonEmptyString(
      input?.membershipId,
      INVALID_ADOPTION_INPUT,
      "membershipId must be a non-empty string when provided.",
    );

    privateAccountToken = token;
    privateGuestToken = null;
    const facts = {
      kind: "account",
      accountAuth: true,
      activeTripId: activeTripId || null,
      membershipId: membershipId || null,
    };
    const warnings = [];
    persistAccountSession(token, activeTripId || null, membershipId || null, warnings);
    return { facts, warnings };
  }

  /**
   * @param {GuestAuthAdoptionInput} input
   * @returns {{ facts: TechnicalSessionFacts, warnings: string[] }}
   */
  function adoptGuestAuth(input) {
    const token = requireNonEmptyString(
      input?.token,
      INVALID_ADOPTION_INPUT,
      "Guest auth adoption requires a non-empty token.",
    );
    const activeTripId = requireNonEmptyString(
      input?.activeTripId,
      INVALID_ADOPTION_INPUT,
      "Guest auth adoption requires a non-empty activeTripId.",
    );
    const membershipId = requireNonEmptyString(
      input?.membershipId,
      INVALID_ADOPTION_INPUT,
      "Guest auth adoption requires a non-empty membershipId.",
    );
    const inviteToken = optionalNonEmptyString(
      input?.inviteToken,
      INVALID_INVITE_TOKEN,
      "inviteToken must be a non-empty string when provided.",
    );

    privateAccountToken = null;
    privateGuestToken = token;
    const facts = {
      kind: "guest",
      guestAuth: true,
      activeTripId,
      membershipId,
    };
    const warnings = [];
    persistGuestSession(token, activeTripId, membershipId, warnings);
    if (inviteToken) {
      writeInviteCache(inviteToken, { activeTripId, membershipId }, warnings);
    }
    return { facts, warnings };
  }

  /**
   * @param {TechnicalTripContextAdoptionInput} input
   * @returns {{ facts: TechnicalSessionFacts, warnings: string[] }}
   */
  function adoptTechnicalTripContext(input) {
    const activeTripId = requireNonEmptyString(
      input?.activeTripId,
      INVALID_ADOPTION_INPUT,
      "Technical trip adoption requires a non-empty activeTripId.",
    );
    const membershipId = requireNonEmptyString(
      input?.membershipId,
      INVALID_ADOPTION_INPUT,
      "Technical trip adoption requires a non-empty membershipId.",
    );
    const inviteToken = optionalNonEmptyString(
      input?.inviteToken,
      INVALID_INVITE_TOKEN,
      "inviteToken must be a non-empty string when provided.",
    );
    const warnings = [];
    if (!privateAccountToken) {
      throw new SessionRuntimeContractError(
        INVALID_ADOPTION_INPUT,
        "Technical trip adoption requires an active account token.",
      );
    }
    persistTripContext(activeTripId, membershipId, warnings);
    if (inviteToken) {
      writeInviteCache(inviteToken, { activeTripId, membershipId }, warnings);
    }

    const facts = {
      kind: "account",
      accountAuth: true,
      activeTripId,
      membershipId,
    };

    return { facts, warnings };
  }

  /**
   * @param {RequestScope} scope
   * @param {TechnicalSessionFacts} facts
   * @returns {{ ok: true, headers: Record<string, string> } | { ok: false, code: MissingContextCode }}
   */
  function requestIdentityFor(scope, facts) {
    if (!["account", "trip"].includes(scope)) {
      throw new SessionRuntimeContractError(
        INVALID_REQUEST_SCOPE,
        `Unsupported request scope: ${scope}`,
      );
    }

    if (!facts || typeof facts !== "object" || typeof facts.kind !== "string") {
      throw new SessionRuntimeContractError(
        INVALID_ADOPTION_INPUT,
        "requestIdentityFor requires technical session facts.",
      );
    }

    if (scope === "account") {
      if (facts.kind !== "account" || !privateAccountToken) {
        return { ok: false, code: MISSING_ACCOUNT_AUTH };
      }
      return {
        ok: true,
        headers: {
          Authorization: `Bearer ${privateAccountToken}`,
        },
      };
    }

    if (scope === "trip") {
      if (facts.kind === "none" || !facts.activeTripId) {
        return { ok: false, code: MISSING_ACTIVE_TRIP_CONTEXT };
      }

      if (facts.kind === "guest") {
        if (!privateGuestToken) {
          return { ok: false, code: MISSING_MEMBERSHIP_IDENTITY };
        }
        return {
          ok: true,
          headers: {
            Authorization: `Bearer ${privateGuestToken}`,
            "X-Trip-Id": facts.activeTripId,
          },
        };
      }

      if (!privateAccountToken) {
        return { ok: false, code: MISSING_ACCOUNT_AUTH };
      }

      const headers = {
        Authorization: `Bearer ${privateAccountToken}`,
        "X-Trip-Id": facts.activeTripId,
      };
      return { ok: true, headers };
    }
  }

  /**
   * @param {string} token
   * @returns {{ record: InviteAdoptionRecord | null, warnings: string[] }}
   */
  function readInviteAdoption(token) {
    const normalizedToken = requireNonEmptyString(
      token,
      INVALID_INVITE_TOKEN,
      "Invite adoption reads require a non-empty token.",
    );
    const warnings = [];
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) {
      return { record: null, warnings };
    }

    let raw;
    try {
      raw = storage.getItem(inviteKey(normalizedToken));
    } catch {
      pushWarning(warnings, PERSISTENCE_READ_FAILED);
      return { record: null, warnings };
    }

    if (!raw) {
      return { record: null, warnings };
    }

    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        pushWarning(warnings, PERSISTENCE_MALFORMED_DATA);
        return { record: null, warnings };
      }

      const activeTripId = asStoredString(parsed.activeTripId ?? parsed.tripId);
      const membershipId = asStoredString(parsed.membershipId);
      if (!activeTripId || !membershipId) {
        pushWarning(warnings, PERSISTENCE_MALFORMED_DATA);
        return { record: null, warnings };
      }

      return {
        record: {
          activeTripId,
          membershipId,
        },
        warnings,
      };
    } catch {
      pushWarning(warnings, PERSISTENCE_MALFORMED_DATA);
      return { record: null, warnings };
    }
  }

  /**
   * @param {TechnicalSessionFacts} currentFacts
   * @param {InvalidationCause} cause
   * @returns {{ facts: TechnicalSessionFacts, warnings: string[] }}
   */
  function invalidateTechnicalSession(currentFacts, cause) {
    if (![ACCOUNT_CREDENTIALS_INVALID, MEMBERSHIP_CREDENTIALS_INVALID].includes(cause)) {
      throw new SessionRuntimeContractError(
        INVALID_ADOPTION_INPUT,
        `Unsupported invalidation cause: ${cause}`,
      );
    }

    const warnings = [];
    if (cause === ACCOUNT_CREDENTIALS_INVALID) {
      privateAccountToken = null;
      privateGuestToken = null;
      clearAccountSessionMaterial(warnings);
      return {
        facts: { kind: "none" },
        warnings,
      };
    }

    clearGuestSessionMaterial(warnings);

    if (currentFacts?.kind === "account" && privateAccountToken) {
      return {
        facts: {
          kind: "account",
          accountAuth: true,
          activeTripId: null,
          membershipId: null,
        },
        warnings,
      };
    }

    return {
      facts: { kind: "none" },
      warnings,
    };
  }

  /**
   * @param {TechnicalSessionFacts} currentFacts
   * @param {{ revoke?: RevokeCapability }} [options]
   * @returns {Promise<{ facts: TechnicalSessionFacts, revokeAttempted: boolean, revokeFailed: boolean, warnings: string[] }>}
   */
  async function logoutTechnicalSession(currentFacts, options = {}) {
    const hadAccountAuth = currentFacts?.kind === "account" && privateAccountToken;
    const hadGuestAuth = currentFacts?.kind === "guest" && privateGuestToken;
    let revokeAttempted = false;
    let revokeFailed = false;

    if ((hadAccountAuth || hadGuestAuth) && typeof options.revoke === "function") {
      revokeAttempted = true;
      try {
        await options.revoke();
      } catch {
        revokeFailed = true;
      }
    }

    privateAccountToken = null;
    privateGuestToken = null;
    const warnings = [];
    clearAccountSessionMaterial(warnings);

    return {
      facts: { kind: "none" },
      revokeAttempted,
      revokeFailed,
      warnings,
    };
  }

  /**
   * @param {string} token
   * @param {string | null} activeTripId
   * @param {string | null} membershipId
   * @param {string[]} warnings
   */
  function persistAccountSession(token, activeTripId, membershipId, warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;

    writeItem(storage, AUTH_TOKEN_KEY, token, warnings);
    removeItem(storage, GUEST_TOKEN_KEY, warnings);
    if (membershipId) {
      writeItem(storage, MEMBERSHIP_ID_KEY, membershipId, warnings);
    } else {
      removeItem(storage, MEMBERSHIP_ID_KEY, warnings);
    }

    if (activeTripId) {
      writeItem(storage, TRIP_ID_KEY, activeTripId, warnings);
    } else {
      removeItem(storage, TRIP_ID_KEY, warnings);
    }
  }

  /**
   * @param {string} token
   * @param {string} activeTripId
   * @param {string} membershipId
   * @param {string[]} warnings
   */
  function persistGuestSession(token, activeTripId, membershipId, warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    removeItem(storage, AUTH_TOKEN_KEY, warnings);
    writeItem(storage, GUEST_TOKEN_KEY, token, warnings);
    persistTripContext(activeTripId, membershipId, warnings);
  }

  /**
   * @param {string} activeTripId
   * @param {string} membershipId
   * @param {string[]} warnings
   */
  function persistTripContext(activeTripId, membershipId, warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    writeItem(storage, MEMBERSHIP_ID_KEY, membershipId, warnings);
    writeItem(storage, TRIP_ID_KEY, activeTripId, warnings);
  }

  /**
   * @param {string} token
   * @param {InviteAdoptionRecord} record
   * @param {string[]} warnings
   */
  function writeInviteCache(token, record, warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    writeItem(storage, inviteKey(token), JSON.stringify({
      membershipId: record.membershipId,
      tripId: record.activeTripId,
    }), warnings);
  }

  /**
   * @param {string[]} warnings
   */
  function clearAccountSessionMaterial(warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    removeItem(storage, AUTH_TOKEN_KEY, warnings);
    removeItem(storage, GUEST_TOKEN_KEY, warnings);
    clearTripContextMaterial(warnings);
  }

  /**
   * @param {string[]} warnings
   */
  function clearGuestSessionMaterial(warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    privateGuestToken = null;
    removeItem(storage, GUEST_TOKEN_KEY, warnings);
    clearTripContextMaterial(warnings);
  }

  /**
   * @param {string[]} warnings
   */
  function clearTripContextMaterial(warnings) {
    const storage = resolveStorageCapability(options.storage, warnings);
    if (!storage) return;
    removeItem(storage, MEMBERSHIP_ID_KEY, warnings);
    removeItem(storage, TRIP_ID_KEY, warnings);
  }
}

/**
 * @param {{
 *   authToken: string | null,
 *   guestToken: string | null,
 *   membershipId: string | null,
 *   tripId: string | null,
 * }} material
 * @returns {TechnicalSessionFacts}
 */
function buildFactsFromMaterial(material) {
  if (material.authToken) {
    return {
      kind: "account",
      accountAuth: true,
      activeTripId: material.tripId || null,
      membershipId: material.membershipId || null,
    };
  }

  if (material.guestToken && material.membershipId && material.tripId) {
    return {
      kind: "guest",
      guestAuth: true,
      activeTripId: material.tripId,
      membershipId: material.membershipId,
    };
  }

  return { kind: "none" };
}

/**
 * @param {SessionRuntimeStorage | null | undefined} injectedStorage
 * @param {string[]} warnings
 * @returns {SessionRuntimeStorage | null}
 */
function resolveStorageCapability(injectedStorage, warnings) {
  if (injectedStorage) return injectedStorage;
  if (typeof window === "undefined" || !window || !window.localStorage) {
    pushWarning(warnings, PERSISTENCE_UNAVAILABLE);
    return null;
  }
  return window.localStorage;
}

/**
 * @param {SessionRuntimeStorage} storage
 * @param {string} key
 * @param {string[]} warnings
 * @returns {string | null}
 */
function readString(storage, key, warnings) {
  try {
    const raw = storage.getItem(key);
    if (raw === null) return null;
    const normalized = asStoredString(raw);
    if (normalized === null) {
      pushWarning(warnings, PERSISTENCE_MALFORMED_DATA);
    }
    return normalized;
  } catch {
    pushWarning(warnings, PERSISTENCE_READ_FAILED);
    return null;
  }
}

/**
 * @param {SessionRuntimeStorage} storage
 * @param {string} key
 * @param {string} value
 * @param {string[]} warnings
 */
function writeItem(storage, key, value, warnings) {
  try {
    storage.setItem(key, value);
  } catch {
    pushWarning(warnings, PERSISTENCE_WRITE_FAILED);
  }
}

/**
 * @param {SessionRuntimeStorage} storage
 * @param {string} key
 * @param {string[]} warnings
 */
function removeItem(storage, key, warnings) {
  try {
    storage.removeItem(key);
  } catch {
    pushWarning(warnings, PERSISTENCE_CLEAR_FAILED);
  }
}

/**
 * @param {string | undefined | null} token
 */
function inviteKey(token) {
  return `${INVITE_KEY_PREFIX}${token}`;
}

/**
 * @param {unknown} value
 * @returns {string | null}
 */
function asStoredString(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * @param {unknown} value
 * @param {string} code
 * @param {string} message
 * @returns {string}
 */
function requireNonEmptyString(value, code, message) {
  if (typeof value !== "string" || value.length === 0) {
    throw new SessionRuntimeContractError(code, message);
  }
  return value;
}

/**
 * @param {unknown} value
 * @param {string} code
 * @param {string} message
 * @returns {string | null}
 */
function optionalNonEmptyString(value, code, message) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.length === 0) {
    throw new SessionRuntimeContractError(code, message);
  }
  return value;
}

/**
 * @param {string[]} warnings
 * @param {string} warning
 */
function pushWarning(warnings, warning) {
  if (!warnings.includes(warning)) {
    warnings.push(warning);
  }
}
