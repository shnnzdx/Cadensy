import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createSessionRuntime, SESSION_RUNTIME_CODES } from "../../shared/session-runtime/index.js";

function createMemoryStorage() {
  const store = new Map();
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
    dump() {
      return Object.fromEntries(store.entries());
    },
  };
}

test("login source adopts account auth with optional default_membership compatibility facts", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  assert.match(loginPage, /import \{ createSessionRuntime, SESSION_RUNTIME_CODES \} from "\.\.\/\.\.\/\.\.\/shared\/session-runtime\/index\.js";/);
  assert.match(loginPage, /const membership = result\.default_membership \|\| result\.memberships\?\.\[0\];/);
  assert.match(loginPage, /const adoption = sessionRuntime\.adoptAccountAuth\(\{\s*token: result\.token,\s*\.\.\.\(membership \? \{\s*activeTripId: membership\.trip_id,\s*membershipId: membership\.membership_id,\s*\} : \{\}\),\s*\}\);/s);
  assert.doesNotMatch(loginPage, /window\.localStorage\.setItem\("tripsync:(authToken|membershipId|tripId)"/);
});

test("login source preserves token-only accounts and host redirect behavior", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  assert.match(loginPage, /if \(!result\.token\) \{/);
  assert.doesNotMatch(loginPage, /This account is not connected to a trip yet/);
  assert.match(loginPage, /const nextPathRef = useRef\("\/trip"\);/);
  assert.match(loginPage, /const resolvedNext = isSafeInternalReturnPath\(requestedNext\) \? requestedNext : "\/trip";/);
  assert.match(loginPage, /import \{ isSafeInternalReturnPath \} from "\.\.\/\.\.\/\.\.\/shared\/trip-navigation-policy\/invite-auth-return\.js";/);
  assert.match(loginPage, /window\.location\.href = nextPathRef\.current;/);
});

test("login source verifies restored account session before redirecting from login", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  assert.match(loginPage, /async function validateRestoredAccountSession/);
  assert.match(loginPage, /sessionRuntime\.requestIdentityFor\("account", facts\)/);
  assert.match(loginPage, /fetch\(`\$\{API_BASE_URL\}\/api\/account`, \{/);
  assert.match(loginPage, /if \(response\.status === 401\) \{/);
  assert.match(loginPage, /sessionRuntime\.invalidateTechnicalSession\(\s*facts,\s*SESSION_RUNTIME_CODES\.invalidation\.ACCOUNT_CREDENTIALS_INVALID,\s*\);/s);
  assert.match(loginPage, /if \(!cancelled && valid\) window\.location\.replace\(resolvedNext\);/);
  assert.doesNotMatch(loginPage, /if \(restored\.facts\.kind === "account"\) \{\s*window\.location\.replace\(resolvedNext\);\s*\}/s);
});

test("phase 3 shared session-runtime still persists the same compatibility values login previously wrote directly", () => {
  const storage = createMemoryStorage();
  const runtime = createSessionRuntime({ storage });

  runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-login",
    membershipId: "m-login",
  });

  assert.deepEqual(storage.dump(), {
    "tripsync:authToken": "token-1",
    "tripsync:membershipId": "m-login",
    "tripsync:tripId": "t-login",
  });
});

test("phase 3 login cutover preserves current generic error UI for persistence warnings instead of treating them as successful navigation", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  assert.match(loginPage, /function hasPersistenceWarning\(warnings: string\[\]\): boolean \{/);
  assert.match(loginPage, /SESSION_RUNTIME_CODES\.warnings\.PERSISTENCE_UNAVAILABLE/);
  assert.match(loginPage, /SESSION_RUNTIME_CODES\.warnings\.PERSISTENCE_WRITE_FAILED/);
  assert.match(loginPage, /if \(hasPersistenceWarning\(adoption\.warnings\)\) \{\s*setError\("Could not reach the backend\. Make sure the API is running\."\);\s*return;\s*\}/s);
});

test("phase 3 session-runtime warning semantics make the login persistence-warning branch explicit", () => {
  const runtime = createSessionRuntime({
    storage: {
      getItem() {
        return null;
      },
      setItem() {
        throw new Error("write failed");
      },
      removeItem() {},
    },
  });

  const adoption = runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  });

  assert.deepEqual(adoption.facts, {
    kind: "account",
    accountAuth: true,
    activeTripId: "t-1",
    membershipId: "m-1",
  });
  assert.deepEqual(adoption.warnings, [
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_WRITE_FAILED,
  ]);
});
