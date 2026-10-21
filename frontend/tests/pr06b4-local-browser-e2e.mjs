/*
 * Real Chromium acceptance runner for the disposable PR-06B.4 database.
 * It uses public HTTP only for organizer-only lifecycle setup where no UI
 * exists; affected users are observed through the actual browser workspace.
 */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const fixturePath = process.argv[2];
const runtimeOff = process.argv.includes('--runtime-off');
if (!fixturePath || !existsSync(fixturePath)) throw new Error('fixture JSON is required');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
const root = new URL('../..', import.meta.url).pathname.replace(/^\//, '').replace(/\//g, '\\');
const python = `${root}backend\\.venv\\Scripts\\python.exe`;
const base = 'http://127.0.0.1:8000';
const web = process.env.CADENSY_E2E_FRONTEND_URL || 'http://localhost:4499';
const control = process.env.CADENSY_E2E_RUNTIME_CONTROL_DIR;
const evidence = [];
const installedChromium = [
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1223', 'chrome-win64', 'chrome.exe'),
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1208', 'chrome-win64', 'chrome.exe'),
].find(existsSync);
if (!installedChromium) throw new Error('No preinstalled local Playwright Chromium executable was found');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = (id, status, detail) => console.log(JSON.stringify({ journey: id, status, detail }));
const redactEvidenceRoute = route => route.replace(
  /^\/api\/invites\/[^/]+\/account-join$/,
  '/api/invites/[redacted-invite-token]/account-join',
);
function fixtureAssert(command, ...args) {
  const out = execFileSync(python, ['scripts/pr06b4_e2e_fixture.py', command, ...args.map(String)], {
    cwd: `${root}backend`, env: process.env, encoding: 'utf8',
  }).trim();
  const parsed = JSON.parse(out);
  evidence.push({ database: { command, ...parsed } });
  return parsed;
}
async function waitForHealth() {
  for (let i = 0; i < 80; i += 1) {
    try { if ((await fetch(`${base}/api/health`)).ok) return; } catch {}
    await sleep(125);
  }
  throw new Error('local backend health check timed out');
}
async function waitForFrontend() {
  for (let i = 0; i < 120; i += 1) {
    try { if ((await fetch(`${web}/login`)).ok) return; } catch {}
    await sleep(125);
  }
  throw new Error(`local frontend did not become reachable at ${web}`);
}
async function waitForFile(file) {
  for (let i = 0; i < 200; i += 1) {
    if (existsSync(file)) return;
    await sleep(50);
  }
  throw new Error(`timed out waiting for local runtime control file ${file}`);
}
async function api(token, pathName, options = {}) {
  const tripId = options.tripId || fixture.trip_id;
  const response = await fetch(`${base}${pathName}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, 'X-Trip-Id': tripId, Accept: 'application/json', ...(options.headers || {}) },
  });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  evidence.push({ http: `${options.method || 'GET'} ${redactEvidenceRoute(pathName)}`, status: response.status });
  return { response, body };
}
async function browserApi(session, pathName, options = {}) {
  const tripId = options.tripId || fixture.trip_id;
  const result = await session.page.evaluate(async ({ baseUrl, trip, route, init }) => {
    const token = localStorage.getItem('tripsync:authToken');
    const response = await fetch(`${baseUrl}${route}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'X-Trip-Id': trip, Accept: 'application/json', ...(init.headers || {}) },
    });
    return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
  }, { baseUrl: base, trip: tripId, route: pathName, init: options });
  evidence.push({ browserHttp: `${options.method || 'GET'} ${redactEvidenceRoute(pathName)}`, status: result.status });
  return { response: { status: result.status }, body: result.body };
}
async function login(browser, account) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const calls = [];
  page.on('response', response => {
    if (response.url().includes('/api/')) calls.push({ url: response.url(), status: response.status(), method: response.request().method() });
  });
  await page.goto(`${web}/login`, { waitUntil: 'networkidle' });
  await page.locator('#email').fill(account.email);
  await page.locator('#password').fill(account.password);
  await page.getByRole('button', { name: /log in/i }).click();
  await page.waitForURL(`${web}/trip`, { timeout: 20_000 });
  const frame = page.frameLocator('iframe[title="TripSync workspace"]');
  await frame.locator('body').waitFor();
  const token = await page.evaluate(() => localStorage.getItem('tripsync:authToken'));
  assert.ok(token, 'real login must persist an Account token through session runtime');
  return { context, page, frame, token, calls };
}
async function openPersonal(session) {
  try {
    if (await session.frame.getByRole('link', { name: 'Chat', exact: true }).count() === 0) {
      await session.frame.getByText('Review current plan', { exact: false }).first().click();
      await session.frame.getByRole('link', { name: 'Chat', exact: true }).waitFor({ state: 'visible' });
    }
    await session.frame.getByRole('link', { name: 'Chat', exact: true }).click();
  } catch (error) {
    const visibleText = await session.frame.locator('body').innerText().catch(() => '<iframe body unavailable>');
    throw new Error(`Personal chat navigation failed. iframe text: ${visibleText.slice(0, 1_500)}`, { cause: error });
  }
  const input = session.frame.getByPlaceholder('Message Cadensy...');
  await input.waitFor({ state: 'visible', timeout: 20_000 });
  return input;
}
async function sendPersonal(session, text) {
  const input = await openPersonal(session);
  await input.fill(text);
  await input.press('Enter');
  await session.frame.getByText(`E2E assistant completion: ${text}`, { exact: true }).waitFor({ timeout: 25_000 });
}
async function openItemAssistant(session) {
  if (await session.frame.getByTitle('Ask Cadensy').count() === 0) {
    await session.frame.getByText('Review current plan', { exact: false }).first().click();
  }
  // The plan initially renders day cards as accordions. Open the first closed
  // day through its visible header before interacting with an item action.
  const firstClosedDay = session.frame.locator('button.accordionHead[aria-expanded="false"]').first();
  if (await firstClosedDay.count()) await firstClosedDay.click();
  // Keep the fixture's first item identity, but first scroll its activity
  // card into the middle of the visible list. The sticky workspace header can
  // otherwise cover the button immediately after the dashboard transition.
  const ask = session.frame.locator('button[title="Ask Cadensy"]:visible').first();
  await ask.waitFor({ state: 'visible', timeout: 20_000 });
  await ask.evaluate(element => element.closest('.activityBlock')?.scrollIntoView({ block: 'center' }));
  await sleep(100);
  await ask.click();
  const input = session.frame.getByPlaceholder('Ask about this item or request a change...');
  await input.waitFor({ state: 'visible', timeout: 20_000 });
  return input;
}
async function sendItemAssistant(session, text) {
  const input = await openItemAssistant(session);
  await input.fill(text);
  await input.press('Enter');
  await session.frame.getByText(`E2E assistant completion: ${text}`, { exact: true }).waitFor({ timeout: 25_000 });
}

await waitForHealth();
await waitForFrontend();
const browser = await chromium.launch({ headless: true, executablePath: installedChromium });
try {
  if (runtimeOff) {
    const session = await login(browser, fixture.organizer);
    await openPersonal(session);
    await session.frame.getByText('Assistant delivery is unavailable here. You can review saved history, but new messages cannot be sent.').waitFor();
    assert.equal(await session.frame.getByPlaceholder('Message Cadensy...').isDisabled(), true);
    assert.equal(session.calls.some(call => call.url.includes(`/api/trips/${fixture.trip_id}/chat`)), false, 'durable UI must not fall back to legacy chat');
    report('RUNTIME-OFF', 'PASS', 'history UI loaded, composer disabled, no legacy chat request');
    await session.context.close();
  } else {
    // J01-J03 retain the original browser-only personal-history proof.
    const org = await login(browser, fixture.organizer);
    await sendPersonal(org, 'J01 browser durable message');
    const orgMe = (await browserApi(org, '/api/me')).body;
    fixtureAssert('assert-scope', (await browserApi(org, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'personal', context_key: 'personal' }) })).body.id, orgMe.membership_id, 'J01 browser durable message');
    await org.page.reload({ waitUntil: 'networkidle' });
    await openPersonal(org);
    await org.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J01', 'PASS', 'UI login/send/refresh; server-owned messages and owner identity asserted through PostgreSQL');

    await org.frame.getByRole('button', { name: 'Profile menu' }).click();
    await org.frame.locator('button.profileMenuSignOut').click();
    await org.page.waitForURL(/\/login(?:\?|$)/, { timeout: 20_000 });
    await org.page.goto(`${web}/login`, { waitUntil: 'networkidle' });
    await org.page.locator('#email').fill(fixture.organizer.email);
    await org.page.locator('#password').fill(fixture.organizer.password);
    await org.page.getByRole('button', { name: /log in/i }).click();
    await org.page.waitForURL(`${web}/trip`);
    await openPersonal(org);
    await org.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J02', 'PASS', 'new Account Session restores server-owned history');

    const second = await login(browser, fixture.organizer);
    await openPersonal(second);
    await second.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J03', 'PASS', 'isolated Chromium context, separate bearer, same Account history');

    // J06 snapshots the exact idempotency namespace before and after replay.
    const personal = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'personal', context_key: 'personal' }) });
    const replayKey = 'e2e-network-replay-key';
    fixtureAssert('assert-submission', personal.body.id, replayKey, 0);
    const one = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${personal.body.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': replayKey }, body: JSON.stringify({ text: 'J06 replay survives an interrupted client' }) });
    const replay = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${personal.body.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': replayKey }, body: JSON.stringify({ text: 'J06 replay survives an interrupted client' }) });
    assert.equal(one.response.status, 202);
    assert.equal(replay.body.submission_id, one.body.submission_id);
    fixtureAssert('assert-submission', personal.body.id, replayKey, 1);
    report('J06', 'PASS', 'specific Conversation/key snapshots prove replay created one Submission and one User Message');

    // J07 writes distinguishable content in all identities, refreshes, and checks owner-scoped histories.
    const global = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'plan_assistant', context_key: 'global' }) });
    const item = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'plan_assistant', context_key: `item:${fixture.plan_item_id}` }) });
    assert.notEqual(personal.body.id, global.body.id);
    assert.notEqual(global.body.id, item.body.id);
    const scopeWrites = [[personal.body.id, 'J07 personal identity'], [global.body.id, 'J07 global identity'], [item.body.id, 'J07 item identity']];
    for (const [conversationId, text] of scopeWrites) {
      const sent = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${conversationId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `e2e-${conversationId}` }, body: JSON.stringify({ text, selected_plan_item_id: conversationId === item.body.id ? fixture.plan_item_id : null }) });
      assert.equal(sent.response.status, 202);
      fixtureAssert('assert-scope', conversationId, orgMe.membership_id, text);
    }
    await second.page.reload({ waitUntil: 'networkidle' });
    await openPersonal(second);
    await second.frame.getByText('J07 personal identity', { exact: true }).waitFor();
    assert.equal(await second.frame.getByText('J07 global identity', { exact: true }).count(), 0);
    report('J07', 'PASS', 'distinct Personal/Global/Item IDs persist isolated content and owner identity across a browser refresh');

    // J09 verifies the server deletion is still effective after a real reload and login-backed read.
    await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${personal.body.id}`, { method: 'DELETE' });
    await second.page.reload({ waitUntil: 'networkidle' });
    const deleted = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${personal.body.id}`);
    assert.equal(deleted.response.status, 404);
    report('J09', 'PASS', 'deleted conversation remains unavailable after browser reload');

    // J10 calls the actual legacy path as an old client and proves it created no durable Conversation.
    const beforeLegacy = fixtureAssert('count-conversations', fixture.trip_id, orgMe.membership_id);
    const legacy = await browserApi(second, `/api/trips/${fixture.trip_id}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'J10 legacy compatibility request', history: [] }) });
    assert.equal(legacy.response.status, 200);
    const afterLegacy = fixtureAssert('count-conversations', fixture.trip_id, orgMe.membership_id);
    assert.equal(afterLegacy.conversation_count, beforeLegacy.conversation_count);
    assert.equal([...org.calls, ...second.calls].some(call => call.method === 'POST' && call.url.includes(`/api/trips/${fixture.trip_id}/chat`)), true, 'old-client route must be observed exactly');
    report('J10', 'PASS', 'actual legacy POST route remains compatible and created no durable Conversation rows');

    // J04: remove Membership A through the protected writer and rejoin as fresh Membership B.
    const owner = await login(browser, fixture.owner_a);
    await sendPersonal(owner, 'J04 Membership A private history');
    const ownerConversation = (await browserApi(owner, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'personal', context_key: 'personal' }) })).body;
    const remove = await api(second.token, `/api/trips/${fixture.trip_id}/members/${fixture.owner_a.membership_id}`, { method: 'DELETE', headers: { 'Idempotency-Key': 'e2e-remove-owner-a', 'X-Expected-Trip-Facts-Revision': String(fixture.trip_facts_revision) } });
    assert.equal(remove.response.status, 200);
    const factsAfterRemove = Number(remove.response.headers.get('x-trip-facts-revision'));
    assert.equal(factsAfterRemove, fixture.trip_facts_revision + 1);
    const removedRead = await browserApi(owner, `/api/trips/${fixture.trip_id}/conversations/${ownerConversation.id}`);
    assert.ok([403, 404].includes(removedRead.response.status));
    const invite = await api(second.token, `/api/trips/${fixture.trip_id}/invite`, { method: 'POST' });
    assert.equal(invite.response.status, 200);
    const rejoin = await browserApi(owner, `/api/invites/${invite.body.token}/account-join`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    assert.equal(rejoin.response.status, 200);
    assert.notEqual(rejoin.body.membership_id, fixture.owner_a.membership_id);
    await owner.page.reload({ waitUntil: 'networkidle' });
    const rejoinedMe = await browserApi(owner, '/api/me');
    assert.equal(rejoinedMe.body.membership_id, rejoin.body.membership_id);
    const oldReadByB = await browserApi(owner, `/api/trips/${fixture.trip_id}/conversations/${ownerConversation.id}`);
    assert.equal(oldReadByB.response.status, 404);
    const ownerBody = await owner.frame.locator('body').innerText();
    assert.equal(ownerBody.includes('J04 Membership A private history'), false);
    fixtureAssert('assert-lifecycle', fixture.trip_id, fixture.owner_a.membership_id, rejoin.body.membership_id, fixture.owner_a.user_id, ownerConversation.id);
    const ownerFresh = await login(browser, fixture.owner_a);
    const freshOldRead = await browserApi(ownerFresh, `/api/trips/${fixture.trip_id}/conversations/${ownerConversation.id}`);
    assert.equal(freshOldRead.response.status, 404);
    await openPersonal(ownerFresh);
    const freshOwnerBody = await ownerFresh.frame.locator('body').innerText();
    assert.equal(freshOwnerBody.includes('J04 Membership A private history'), false);
    report('J04', 'PASS', 'fresh Chromium context for Membership B cannot discover Membership A private history');

    // J08: the test-only Direct Candidate crosses the real Preview/Coordinator
    // persistence path. Resolve it once while current, then make it historical.
    const member = await login(browser, fixture.member);
    const memberMe = (await browserApi(member, '/api/me')).body;
    await sendItemAssistant(member, '[E2E-DIRECT-CANDIDATE] J08 durable direct candidate');
    const assistantConversations = (await browserApi(member, `/api/trips/${fixture.trip_id}/conversations?scope=plan_assistant`)).body.conversations;
    const histories = await Promise.all(assistantConversations.map(async conversation => ({
      conversation,
      history: await browserApi(member, `/api/trips/${fixture.trip_id}/conversations/${conversation.id}/messages`),
    })));
    const isDirectOption = option => option.target_plan_item_id === fixture.plan_item_id
      && option.safe_patch?.start_hour === 15
      && option.title.startsWith('Suggested change for ');
    const persisted = histories.find(({ history }) => history.body.messages.flatMap(message => message.options || []).some(isDirectOption));
    assert.ok(persisted, 'Direct Candidate option must be returned by the durable history contract');
    const itemConversation = persisted.conversation;
    const persistedAssistant = persisted.history.body.messages.find(message => (message.options || []).some(isDirectOption));
    assert.ok(persistedAssistant, 'Direct Candidate option must retain its assistant parent identity');
    const persistedOption = persistedAssistant.options.find(isDirectOption);
    assert.ok(persistedOption, 'Direct Candidate option must be returned by durable history');
    fixtureAssert('assert-option', itemConversation.id, persistedOption.target_plan_item_id);
    const directSubmissionPath = `/api/trips/${fixture.trip_id}/conversations/${itemConversation.id}/messages`;
    const submissionsBeforeRecovery = member.calls.filter(call => call.method === 'POST' && call.url.endsWith(directSubmissionPath)).length;
    assert.equal(submissionsBeforeRecovery, 1, 'Direct Candidate journey must have one initial submission');

    // Refresh and a real sign-out/login must both restore the same server-owned
    // option before the current-revision Resolve. Neither action may resubmit.
    await member.page.reload({ waitUntil: 'networkidle' });
    await openItemAssistant(member);
    await member.frame.getByRole('button', { name: persistedOption.title, exact: true }).waitFor({ state: 'visible' });
    await member.frame.getByRole('button', { name: 'Profile menu' }).click();
    await member.frame.locator('button.profileMenuSignOut').click();
    await member.page.waitForURL(/\/login(?:\?|$)/, { timeout: 20_000 });
    await member.page.goto(`${web}/login`, { waitUntil: 'networkidle' });
    await member.page.locator('#email').fill(fixture.member.email);
    await member.page.locator('#password').fill(fixture.member.password);
    await member.page.getByRole('button', { name: /log in/i }).click();
    await member.page.waitForURL(`${web}/trip`, { timeout: 20_000 });
    await openItemAssistant(member);
    let optionButton = member.frame.getByRole('button', { name: persistedOption.title, exact: true });
    await optionButton.waitFor({ state: 'visible' });
    const submissionsAfterRelogin = member.calls.filter(call => call.method === 'POST' && call.url.endsWith(directSubmissionPath)).length;
    assert.equal(submissionsAfterRelogin, submissionsBeforeRecovery, 'refresh/re-login must not resubmit the saved User Message');

    const changesBeforeCurrentResolve = member.calls.filter(call => call.method === 'POST' && call.url.includes('/changes')).length;
    const currentResolveResponse = member.page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(
      `/api/trips/${fixture.trip_id}/conversations/${itemConversation.id}/messages/${persistedAssistant.id}/options/${persistedOption.id}/resolve`,
    ));
    await optionButton.click();
    const currentResolution = await currentResolveResponse;
    assert.equal(currentResolution.status(), 200);
    const currentResolutionBody = await currentResolution.json();
    assert.equal(currentResolutionBody.ready, true);
    assert.equal(currentResolutionBody.item_id, fixture.plan_item_id);
    assert.equal(currentResolutionBody.patch.start_hour, 15);
    await member.frame.locator('button.changePrimaryAction').waitFor({ state: 'visible', timeout: 20_000 });
    const changesAfterCurrentResolve = member.calls.filter(call => call.method === 'POST' && call.url.includes('/changes')).length;
    assert.equal(changesAfterCurrentResolve, changesBeforeCurrentResolve, 'current historical Resolve must not directly Apply');
    fixtureAssert('assert-plan-state', fixture.plan_id, fixture.plan_item_id, persistedOption.plan_revision, fixture.plan_item_start_hour);
    report('J08-CURRENT', 'PASS', 'Direct Candidate persisted; refresh/re-login restored it; current Resolve returned ready without Apply or Plan mutation');

    const booked = await api(second.token, `/api/plans/items/${persistedOption.target_plan_item_id}/booking`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'e2e-j08-booking', 'X-Expected-Plan-Revision': String(persistedOption.plan_revision) }, body: JSON.stringify({ booked: true }) });
    assert.equal(booked.response.status, 200);
    const planAfter = fixtureAssert('assert-plan-revision', fixture.plan_id, persistedOption.plan_revision);
    await member.page.reload({ waitUntil: 'networkidle' });
    await openItemAssistant(member);
    optionButton = member.frame.getByRole('button', { name: persistedOption.title, exact: true });
    await optionButton.waitFor({ state: 'visible' });
    const callsBeforeSelection = member.calls.filter(call => call.method === 'POST' && call.url.includes('/changes')).length;
    const resolveResponse = member.page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(
      `/api/trips/${fixture.trip_id}/conversations/${itemConversation.id}/messages/${persistedAssistant.id}/options/${persistedOption.id}/resolve`,
    ));
    await optionButton.click();
    assert.equal((await resolveResponse).status(), 200);
    await member.frame.getByText('Checking that option...').waitFor({ state: 'hidden', timeout: 20_000 });
    await member.frame.getByText('That suggestion is out of date. Please ask Cadensy for a current option.').waitFor({ state: 'visible' });
    const callsAfterSelection = member.calls.filter(call => call.method === 'POST' && call.url.includes('/changes')).length;
    assert.equal(callsAfterSelection, callsBeforeSelection, 'selecting a historical option must not directly Apply');
    const planAfterSelection = fixtureAssert('assert-plan-revision', fixture.plan_id, persistedOption.plan_revision);
    assert.equal(planAfterSelection.current_revision, planAfter.current_revision);
    report('J08-STALE', 'PASS', 'the same Direct Candidate Option remains visible and owner-scoped Resolve rejects it after the Plan revision changes');

    // J05: the same legitimate participant is fenced at completion by a real protected quarantine transaction.
    const quarantineConversation = (await browserApi(member, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'personal', context_key: 'personal' }) })).body;
    const quarantineKey = 'e2e-j05-runtime-barrier';
    const pendingAdmission = browserApi(member, `/api/trips/${fixture.trip_id}/conversations/${quarantineConversation.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': quarantineKey }, body: JSON.stringify({ text: '[E2E-BLOCK] J05 durable admission before quarantine' }) });
    assert.ok(control, 'local runtime control directory must be configured only for this E2E process');
    await waitForFile(path.join(control, 'started'));
    fixtureAssert('assert-submission', quarantineConversation.id, quarantineKey, 1);
    const sentinelInvite = await api(second.token, `/api/trips/${fixture.trip_id}/invite`, { method: 'POST' });
    assert.equal(sentinelInvite.response.status, 200);
    const quarantined = await api(second.token, `/api/trips/${fixture.trip_id}/members/${memberMe.membership_id}/quarantine`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'e2e-quarantine-member', 'X-Expected-Trip-Facts-Revision': String(factsAfterRemove + 1) }, body: JSON.stringify({ reason: 'security_incident' }) });
    assert.equal(quarantined.response.status, 200);
    assert.equal(Number(quarantined.response.headers.get('x-trip-facts-revision')), factsAfterRemove + 2);
    writeFileSync(path.join(control, 'release'), '1', 'ascii');
    const admission = await pendingAdmission;
    assert.equal(admission.response.status, 202);
    fixtureAssert('assert-quarantine', fixture.trip_id, memberMe.membership_id, quarantineConversation.id, sentinelInvite.body.id);
    const denied = await browserApi(member, `/api/trips/${fixture.trip_id}/conversations/${quarantineConversation.id}`);
    assert.ok([403, 404].includes(denied.response.status));
    await member.page.reload({ waitUntil: 'networkidle' });
    const globalAccount = await member.page.evaluate(async baseUrl => (await fetch(`${baseUrl}/api/account`, { headers: { Authorization: `Bearer ${localStorage.getItem('tripsync:authToken')}` } })).status, base);
    assert.equal(globalAccount, 200);
    const unrelatedTrip = await browserApi(member, `/api/trips/${fixture.unrelated_trip_id}`, { tripId: fixture.unrelated_trip_id });
    assert.equal(unrelatedTrip.response.status, 200);
    report('J05', 'PASS', 'submission committed -> runtime barrier -> protected quarantine -> release -> no assistant/option; browser loses Trip X while Account remains valid for another Trip');

    await member.context.close();
    await ownerFresh.context.close();
    await owner.context.close();
    await second.context.close();
    await org.context.close();
  }
  console.log(JSON.stringify({ evidence }));
} finally {
  await browser.close();
}
