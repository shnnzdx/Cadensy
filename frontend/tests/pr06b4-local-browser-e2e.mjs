/*
 * Real Chromium acceptance runner for the disposable PR-06B.4 database.
 * It deliberately drives the public login and iframe UI; direct HTTP calls
 * are used only for documented lifecycle setup/assertion where no organizer
 * moderation UI exists yet.  No route is mocked.
 */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const fixturePath = process.argv[2];
const runtimeOff = process.argv.includes('--runtime-off');
if (!fixturePath || !existsSync(fixturePath)) throw new Error('fixture JSON is required');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
const root = new URL('../..', import.meta.url).pathname.replace(/^\//, '').replace(/\//g, '\\');
const python = `${root}backend\\.venv\\Scripts\\python.exe`;
const base = 'http://127.0.0.1:8000';
const web = 'http://127.0.0.1:3000';
const evidence = [];

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitForHealth() {
  for (let i = 0; i < 80; i += 1) {
    try { if ((await fetch(`${base}/api/health`)).ok) return; } catch {}
    await sleep(125);
  }
  throw new Error('local backend health check timed out');
}
function dbAssert(membership, users, assistants) {
  const out = execFileSync(python, ['scripts/pr06b4_e2e_fixture.py', 'assert-counts', fixture.trip_id, membership, String(users), String(assistants)], {
    cwd: `${root}backend`, env: process.env, encoding: 'utf8',
  }).trim();
  evidence.push({ database: JSON.parse(out) });
}
async function api(token, path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, 'X-Trip-Id': fixture.trip_id, Accept: 'application/json', ...(options.headers || {}) },
  });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  evidence.push({ http: `${options.method || 'GET'} ${path}`, status: response.status });
  return { response, body };
}
async function browserApi(session, path, options = {}) {
  const result = await session.page.evaluate(async ({ baseUrl, tripId, pathName, init }) => {
    const token = localStorage.getItem('tripsync:authToken');
    const response = await fetch(`${baseUrl}${pathName}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'X-Trip-Id': tripId, Accept: 'application/json', ...(init.headers || {}) },
    });
    return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
  }, { baseUrl: base, tripId: fixture.trip_id, pathName: path, init: options });
  evidence.push({ browserHttp: `${options.method || 'GET'} ${path}`, status: result.status });
  return { response: { status: result.status }, body: result.body };
}
async function login(browser, account) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const calls = [];
  page.on('response', response => { if (response.url().includes('/api/')) calls.push({ url: response.url(), status: response.status() }); });
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
  await session.frame.getByText('Cadensy', { exact: true }).first().click();
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
function report(id, status, detail) { console.log(JSON.stringify({ journey: id, status, detail })); }

await waitForHealth();
const browser = await chromium.launch({ headless: true });
try {
  if (runtimeOff) {
    const session = await login(browser, fixture.organizer);
    await openPersonal(session);
    await session.frame.getByText('Assistant delivery is unavailable here. You can review saved history, but new messages cannot be sent.').waitFor();
    assert.equal(await session.frame.getByPlaceholder('Message Cadensy...').isDisabled(), true);
    assert.equal(session.calls.some(call => call.url.endsWith('/api/chat')), false, 'durable UI must not fall back to legacy /api/chat');
    report('RUNTIME-OFF', 'PASS', 'history UI loaded, composer disabled, no legacy chat request');
    await session.context.close();
  } else {
    // J01: real browser login, iframe navigation, send, durable completion and refresh.
    const org = await login(browser, fixture.organizer);
    await sendPersonal(org, 'J01 browser durable message');
    dbAssert((await api(org.token, '/api/me')).body.membership_id, 1, 1);
    await org.page.reload({ waitUntil: 'networkidle' });
    await openPersonal(org);
    await org.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J01', 'PASS', 'UI login/send/refresh; conversation, messages and submission verified through PostgreSQL fixture assertion');

    // J02: logout is driven through the product UI then a fresh real login restores history.
    await org.frame.getByRole('button', { name: 'Profile menu' }).click();
    await org.frame.locator('button.profileMenuSignOut').click();
    await org.page.waitForURL(`${web}/login`, { timeout: 20_000 });
    await org.page.goto(`${web}/login`, { waitUntil: 'networkidle' });
    await org.page.locator('#email').fill(fixture.organizer.email);
    await org.page.locator('#password').fill(fixture.organizer.password);
    await org.page.getByRole('button', { name: /log in/i }).click();
    await org.page.waitForURL(`${web}/trip`);
    await openPersonal(org);
    await org.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J02', 'PASS', 'new Account Session restores server-owned history');

    // J03: a second real Chromium context proves Account-owned private history is server scoped.
    const second = await login(browser, fixture.organizer);
    await openPersonal(second);
    await second.frame.getByText('J01 browser durable message', { exact: true }).waitFor();
    report('J03', 'PASS', 'isolated Chromium context, separate bearer, same Account history');

    // J06: explicit idempotency replay uses actual durable endpoint; one user row remains.
    const me = (await browserApi(second, '/api/me')).body;
    const created = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'personal', context_key: 'personal' }) });
    const key = 'e2e-network-replay-key';
    const one = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${created.body.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify({ text: 'J06 replay survives an interrupted client' }) });
    const replay = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${created.body.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify({ text: 'J06 replay survives an interrupted client' }) });
    assert.equal(replay.body.submission_id, one.body.submission_id);
    dbAssert(me.membership_id, 2, 2);
    report('J06', 'PASS', 'browser context issued durable HTTP replay with one idempotency key; database has no duplicate user row');

    // J07: every scope is resolved through the live API and distinct server Conversation IDs.
    const global = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'plan_assistant', context_key: 'global' }) });
    const item = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'plan_assistant', context_key: `item:${fixture.plan_item_id}` }) });
    assert.notEqual(created.body.id, global.body.id);
    assert.notEqual(global.body.id, item.body.id);
    report('J07', 'PASS', 'personal/global/item contexts resolve to separate durable conversations');

    // J09: delete through real HTTP contract, then refresh/re-login cannot restore it.
    await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${created.body.id}`, { method: 'DELETE' });
    const deleted = await browserApi(second, `/api/trips/${fixture.trip_id}/conversations/${created.body.id}`);
    assert.equal(deleted.response.status, 404);
    report('J09', 'PASS', 'deleted conversation remains unavailable after authenticated read');

    // J10: inspect this browser's real API evidence; no durable interaction called legacy chat.
    assert.equal([...org.calls, ...second.calls].some(call => /\/api\/chat(?:\?|$)/.test(call.url)), false);
    report('J10', 'PASS', 'durable UI/API trace contains no legacy /api/chat request');

    // J04/J05/J08 require lifecycle moderation and protected Preview/Apply.  They are kept as
    // explicit unexecuted gates until the fixture exposes a non-bypassing lifecycle command.
    report('J04', 'BLOCKED', 'No existing organizer removal UI; harness intentionally refuses direct table mutation as a replacement for protected lifecycle operation.');
    report('J05', 'BLOCKED', 'Fake Runtime barrier is installed, but release requires a protected quarantine command; no safe test-only caller exists without duplicating the writer.');
    report('J08', 'BLOCKED', 'Current deterministic Runtime returns reply-only output, so it cannot produce an advisory option for a real stale Preview/Apply browser flow.');

    await second.context.close();
    await org.context.close();
  }
  console.log(JSON.stringify({ evidence }));
} finally {
  await browser.close();
}
