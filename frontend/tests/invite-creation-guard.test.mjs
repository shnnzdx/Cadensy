import assert from "node:assert/strict";
import test from "node:test";

import { createSessionRuntime } from "../../shared/session-runtime/index.js";
import {
  INVITE_UNAVAILABLE_MESSAGE,
  createInviteAttemptGate,
  verifiedOrganizerMembership,
} from "../../trip/src/final/inviteCreation.js";

function memoryStorage() {
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
  };
}

function organizer(id, membershipId = `membership-${id}`) {
  return { id, membership_id: membershipId, my_role: "organizer" };
}

function deferredQueue() {
  const pending = [];
  return {
    pending,
    push() {
      return new Promise(resolve => pending.push(resolve));
    },
  };
}

test("verified organizer membership ignores cache shape and non-organizer rows", () => {
  assert.equal(verifiedOrganizerMembership(null, "trip-route"), null);
  assert.equal(verifiedOrganizerMembership([{ id: "trip-route", my_role: "organizer" }], "trip-route"), null);
  assert.equal(
    verifiedOrganizerMembership([{ id: "trip-route", membership_id: "m", my_role: "participant" }], "trip-route"),
    null,
  );
  assert.deepEqual(
    verifiedOrganizerMembership([
      { id: "other", membership_id: "m-other", my_role: "organizer" },
      organizer("trip-route", "m-route"),
    ], "trip-route"),
    organizer("trip-route", "m-route"),
  );
});

test("guest sessions never post an invite or adopt another trip", async () => {
  for (const routeTripId of ["trip-guest", "trip-other"]) {
    const posts = [];
    const lists = [];
    const adopts = [];
    const gate = createInviteAttemptGate();
    await assert.rejects(
      () => gate.createVerifiedInvite({
        routeTripId,
        readSession: () => ({ kind: "guest", activeTripId: "trip-guest", membershipId: "m-guest" }),
        listAccountTrips: async () => {
          lists.push(routeTripId);
          return [organizer(routeTripId)];
        },
        adoptAccountTrip: (input) => {
          adopts.push(input);
          return { facts: { kind: "account", activeTripId: input.tripId } };
        },
        postInvite: (input) => {
          posts.push(input);
          return { token: "nope" };
        },
      }),
      (error) => error.message === INVITE_UNAVAILABLE_MESSAGE,
    );
    assert.equal(lists.length, 0);
    assert.equal(adopts.length, 0);
    assert.equal(posts.length, 0);
  }
});

test("account refusal paths post zero invites and do not adopt a raw route", async () => {
  const cases = [
    { name: "missing route", routeTripId: "", rows: [organizer("trip-route")], activeTripId: "trip-active", expectList: false },
    { name: "absent target", routeTripId: "trip-route", rows: [organizer("trip-active")], activeTripId: "trip-active", expectList: true },
    { name: "participant", routeTripId: "trip-route", rows: [{ id: "trip-route", membership_id: "m", my_role: "participant" }], activeTripId: "trip-active", expectList: true },
    { name: "blank membership", routeTripId: "trip-route", rows: [{ id: "trip-route", membership_id: "", my_role: "organizer" }], activeTripId: "trip-route", expectList: true },
  ];

  for (const item of cases) {
    const posts = [];
    const adopts = [];
    let lists = 0;
    const gate = createInviteAttemptGate();
    await assert.rejects(() => gate.createVerifiedInvite({
      routeTripId: item.routeTripId,
      readSession: () => ({ kind: "account", activeTripId: item.activeTripId, membershipId: "m-active" }),
      listAccountTrips: async () => {
        lists += 1;
        return item.rows;
      },
      adoptAccountTrip: (input) => {
        adopts.push(input);
        return { facts: { kind: "account", activeTripId: input.tripId, membershipId: input.membershipId } };
      },
      postInvite: (input) => {
        posts.push(input);
        return { token: "nope" };
      },
    }));
    assert.equal(posts.length, 0, item.name);
    assert.equal(adopts.length, 0, item.name);
    assert.equal(lists, item.expectList ? 1 : 0, item.name);
  }
});

test("adopt failure or unequal post-adopt facts post zero invites", async () => {
  const failing = createInviteAttemptGate();
  const failingPosts = [];
  await assert.rejects(() => failing.createVerifiedInvite({
    routeTripId: "trip-route",
    readSession: () => ({ kind: "account", activeTripId: "trip-active", membershipId: "m-active" }),
    listAccountTrips: async () => [organizer("trip-route")],
    adoptAccountTrip: () => {
      throw new Error("account token missing");
    },
    postInvite: (input) => {
      failingPosts.push(input);
      return { token: "nope" };
    },
  }));
  assert.equal(failingPosts.length, 0);

  const unequal = createInviteAttemptGate();
  const unequalPosts = [];
  await assert.rejects(() => unequal.createVerifiedInvite({
    routeTripId: "trip-route",
    readSession: () => ({ kind: "account", activeTripId: "trip-active", membershipId: "m-active" }),
    listAccountTrips: async () => [organizer("trip-route")],
    adoptAccountTrip: () => ({ facts: { kind: "account", activeTripId: "trip-other", membershipId: "m-other" } }),
    postInvite: (input) => {
      unequalPosts.push(input);
      return { token: "nope" };
    },
  }));
  assert.equal(unequalPosts.length, 0);
});

test("a late verification for the previous route posts zero times", async () => {
  const gate = createInviteAttemptGate();
  const lists = deferredQueue();
  const posts = [];
  const adopts = [];
  let session = { kind: "account", activeTripId: "trip-old", membershipId: "m-old" };
  const run = routeTripId => gate.createVerifiedInvite({
    routeTripId,
    readSession: () => session,
    listAccountTrips: () => lists.push(),
    adoptAccountTrip: (input) => {
      adopts.push(input);
      session = { kind: "account", activeTripId: input.tripId, membershipId: input.membershipId };
      return { facts: { ...session } };
    },
    postInvite: (input) => {
      posts.push(input);
      return { token: "ok", invite_id: "i" };
    },
  });

  const first = run("trip-a");
  const second = run("trip-b");
  lists.pending[0]([organizer("trip-a", "m-a")]);
  lists.pending[1]([organizer("trip-b", "m-b")]);
  await assert.rejects(first);
  await second;

  assert.equal(posts.length, 1);
  assert.equal(posts[0].tripId, "trip-b");
  assert.equal(posts[0].facts.activeTripId, "trip-b");
  assert.equal(adopts.length, 1);
  assert.equal(adopts[0].tripId, "trip-b");
  assert.equal(posts.some(call => call.tripId === "trip-a"), false);
});

test("logout or another session change expires the in-flight invite", async () => {
  const gate = createInviteAttemptGate();
  const lists = deferredQueue();
  const posts = [];
  const pending = gate.createVerifiedInvite({
    routeTripId: "trip-route",
    readSession: () => ({ kind: "account", activeTripId: "trip-route", membershipId: "m" }),
    listAccountTrips: () => lists.push(),
    adoptAccountTrip: () => {
      throw new Error("should not adopt");
    },
    postInvite: (input) => {
      posts.push(input);
      return { token: "nope" };
    },
  });
  gate.invalidate();
  lists.pending[0]([organizer("trip-route")]);
  await assert.rejects(pending);
  assert.equal(posts.length, 0);
});

test("two overlapping starts of the same load post at most once", async () => {
  const gate = createInviteAttemptGate();
  const lists = deferredQueue();
  const posts = [];
  const run = () => gate.createVerifiedInvite({
    routeTripId: "trip-route",
    readSession: () => ({ kind: "account", activeTripId: "trip-route", membershipId: "m-route" }),
    listAccountTrips: () => lists.push(),
    adoptAccountTrip: () => {
      throw new Error("already active");
    },
    postInvite: ({ tripId, facts }) => {
      posts.push({ tripId, headerTripId: facts.activeTripId });
      return { token: "ok" };
    },
  });

  const first = run();
  const second = run();
  lists.pending[0]([organizer("trip-route")]);
  lists.pending[1]([organizer("trip-route")]);
  await assert.rejects(first);
  await second;
  assert.equal(posts.length, 1);
  assert.equal(posts[0].tripId, "trip-route");
  assert.equal(posts[0].headerTripId, "trip-route");
});

test("a current organizer verification posts exactly one matching invite", async () => {
  const runtime = createSessionRuntime({ storage: memoryStorage() });
  let facts = runtime.adoptAccountAuth({
    token: "account-token",
    activeTripId: "trip-active",
    membershipId: "membership-active",
  }).facts;
  const posts = [];
  const gate = createInviteAttemptGate();
  await gate.createVerifiedInvite({
    routeTripId: "trip-route",
    readSession: () => facts,
    listAccountTrips: async () => [organizer("trip-route", "membership-route")],
    adoptAccountTrip: (input) => {
      const adopted = runtime.adoptTechnicalTripContext({
        membershipId: input.membershipId,
        activeTripId: input.tripId,
      });
      facts = adopted.facts;
      return adopted;
    },
    postInvite: ({ tripId, facts: postFacts }) => {
      const identity = runtime.requestIdentityFor("trip", postFacts);
      posts.push({
        path: `/api/trips/${tripId}/invite`,
        headerTripId: identity.headers["X-Trip-Id"],
      });
      return { token: "invite-token" };
    },
  });

  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, "/api/trips/trip-route/invite");
  assert.equal(posts[0].headerTripId, "trip-route");
});

test("a list failure posts zero invites", async () => {
  const posts = [];
  const gate = createInviteAttemptGate();
  await assert.rejects(
    () => gate.createVerifiedInvite({
      routeTripId: "trip-route",
      readSession: () => ({ kind: "account", activeTripId: "trip-route", membershipId: "m" }),
      listAccountTrips: async () => {
        throw new Error("network down");
      },
      adoptAccountTrip: () => {
        throw new Error("should not adopt");
      },
      postInvite: (input) => {
        posts.push(input);
        return { token: "nope" };
      },
    }),
    (error) => error.message === "network down",
  );
  assert.equal(posts.length, 0);
});
