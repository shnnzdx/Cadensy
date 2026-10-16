"use client";

import Link from "next/link";
import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { createSessionRuntime, SESSION_RUNTIME_CODES } from "../../../shared/session-runtime/index.js";
import { isSafeInternalReturnPath } from "../../../shared/trip-navigation-policy/invite-auth-return.js";
import BrandLogo from "../BrandLogo";

const API_BASE_URL =
  import.meta.env.NEXT_PUBLIC_API_BASE_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";
const sessionRuntime = createSessionRuntime();

function hasPersistenceWarning(warnings: string[]): boolean {
  return warnings.includes(SESSION_RUNTIME_CODES.warnings.PERSISTENCE_UNAVAILABLE) ||
    warnings.includes(SESSION_RUNTIME_CODES.warnings.PERSISTENCE_WRITE_FAILED);
}

async function validateRestoredAccountSession(facts: ReturnType<typeof sessionRuntime.restoreTechnicalSession>["facts"]): Promise<boolean> {
  const identity = sessionRuntime.requestIdentityFor("account", facts);
  if (!identity.ok) return false;

  const response = await fetch(`${API_BASE_URL}/api/account`, {
    headers: { Accept: "application/json", ...identity.headers },
  });
  if (response.ok) return true;
  if (response.status === 401) {
    sessionRuntime.invalidateTechnicalSession(
      facts,
      SESSION_RUNTIME_CODES.invalidation.ACCOUNT_CREDENTIALS_INVALID,
    );
  }
  return false;
}

export default function LoginPage() {
  const pupilsRef = useRef<HTMLElement[]>([]);
  const [showPassword, setShowPassword] = useState(false);
  const [accountCreated, setAccountCreated] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nextPathRef = useRef("/trip");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedNext = params.get("next");
    const resolvedNext = isSafeInternalReturnPath(requestedNext) ? requestedNext : "/trip";
    setAccountCreated(params.get("created") === "1");
    nextPathRef.current = resolvedNext;

    let cancelled = false;
    const restored = sessionRuntime.restoreTechnicalSession();
    if (restored.facts.kind === "account") {
      validateRestoredAccountSession(restored.facts)
        .then((valid) => {
          if (!cancelled && valid) window.location.replace(resolvedNext);
        })
        .catch(() => {
          if (!cancelled) {
            sessionRuntime.invalidateTechnicalSession(
              restored.facts,
              SESSION_RUNTIME_CODES.invalidation.ACCOUNT_CREDENTIALS_INVALID,
            );
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let frame = 0;
    let pointerX = window.innerWidth * 0.72;
    let pointerY = window.innerHeight * 0.35;
    const onPointerMove = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        pupilsRef.current.forEach((pupil) => {
          const eye = pupil.parentElement?.getBoundingClientRect();
          if (!eye) return;
          const dx = pointerX - (eye.left + eye.width / 2);
          const dy = pointerY - (eye.top + eye.height / 2);
          const distance = Math.max(1, Math.hypot(dx, dy));
          const travel = Math.min(6, distance / 45);
          pupil.style.transform = `translate(${(dx / distance) * travel}px, ${(dy / distance) * travel}px)`;
        });
      });
    };
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      cancelAnimationFrame(frame);
    };
  }, []);

  const setPupil = (index: number) => (node: HTMLElement | null) => { if (node) pupilsRef.current[index] = node; };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        setError(response.status === 401 ? "Invalid email or password." : "Could not log in. Try again.");
        return;
      }
      const result = await response.json();
      const membership = result.default_membership || result.memberships?.[0];
      if (!result.token) {
        setError("The backend returned an invalid session. Try again.");
        return;
      }
      const adoption = sessionRuntime.adoptAccountAuth({
        token: result.token,
        ...(membership ? {
          activeTripId: membership.trip_id,
          membershipId: membership.membership_id,
        } : {}),
      });
      if (hasPersistenceWarning(adoption.warnings)) {
        setError("Could not reach the backend. Make sure the API is running.");
        return;
      }
      window.location.href = nextPathRef.current;
    } catch {
      setError("Could not reach the backend. Make sure the API is running.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <Link className="login-brand" href="/" aria-label="CADENSY home">
        <BrandLogo />
      </Link>
      <section className="login-experience">
        <div className="travel-scene" aria-label="Three CADENSY travelers whose eyes follow the cursor">
          <div className="route route-one" /><div className="route route-two" />
          <span className="map-label label-one">NRT</span><span className="map-label label-two">CDG</span><span className="map-label label-three">ORD</span>
          <div className="scene-copy">
            <p className="scene-kicker">Your group is waiting</p>
            <h1>Every great trip<br />starts together.</h1>
            <p>Three travelers. Three perspectives. One shared direction.</p>
          </div>
          <div className="traveler-trio" aria-hidden="true">
            {["woman-one", "man", "woman-two"].map((kind, personIndex) => (
              <div className={`q-person ${kind}`} key={kind}>
                <div className="q-hair" />
                <div className="q-head">
                  <div className="q-hair-front" />
                  <div className="q-eye left"><i ref={setPupil(personIndex * 2)} /></div>
                  <div className="q-eye right"><i ref={setPupil(personIndex * 2 + 1)} /></div>
                  <div className="q-nose" /><div className="q-smile" />
                </div>
                <div className="q-body"><span /></div>
              </div>
            ))}
          </div>
          <span className="scene-hint">They&apos;re listening</span>
        </div>
        <div className="login-side">
          <div className="login-card">
            {accountCreated && <div className="account-created" role="status"><span>✓</span><p><strong>Account created.</strong> Log in to continue to CADENSY.</p></div>}
            <div className="login-heading">
              <p className="login-kicker">Welcome back</p><h2>Welcome back.</h2>
              <p>Sign in to bring your group&apos;s ideas back into sync.</p>
            </div>
            <form className="login-form" onSubmit={submit}>
              <label htmlFor="email">Email address</label>
              <input id="email" name="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} type="email" placeholder="you@example.com" />
              <div className="password-row"><label htmlFor="password">Password</label><Link href="#">Forgot password?</Link></div>
              <div className="password-field">
                <input id="password" name="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} type={showPassword ? "text" : "password"} placeholder="Enter your password" />
                <button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button>
              </div>
              {error && <p className="login-error" role="alert">{error}</p>}
              <button className="login-submit" type="submit" disabled={submitting || !email.trim() || !password}>{submitting ? "Logging in..." : "Log in"} <span>→</span></button>
            </form>
            <div className="login-divider"><span>or</span></div>
            <button className="google-button" type="button"><span>G</span> Continue with Google</button>
            <p className="signup-copy">New to CADENSY? <Link href="/signup" onClick={(event) => {
              event.preventDefault();
              window.location.href = `/signup?next=${encodeURIComponent(nextPathRef.current)}`;
            }}>Create an account</Link></p>
          </div>
          <p className="login-legal">By continuing, you agree to our <Link href="/privacy">Privacy Policy</Link>.</p>
        </div>
      </section>
    </main>
  );
}
