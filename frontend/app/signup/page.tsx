"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
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

export default function SignupPage() {
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nextPathRef = useRef("/trip");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next");
    const resolvedNext = isSafeInternalReturnPath(next) ? next : "/trip";
    nextPathRef.current = resolvedNext;
    const restored = sessionRuntime.restoreTechnicalSession();
    if (restored.facts.kind === "account") {
      window.location.replace(resolvedNext);
    }
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirmPassword")) { setError("The passwords do not match. Please try again."); return; }
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          name: String(form.get("name") || ""),
          email: String(form.get("email") || ""),
          password: String(form.get("password") || ""),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(response.status === 409
          ? "An account with this email already exists. Try logging in instead."
          : (typeof result.detail === "string" ? result.detail : "Could not create your account. Try again."));
        return;
      }
      const adoption = sessionRuntime.adoptAccountAuth({ token: result.token });
      if (hasPersistenceWarning(adoption.warnings)) {
        setError("Your account was created, but this browser could not save the session. Please log in.");
        return;
      }
      window.location.href = nextPathRef.current;
    } catch {
      setError("Could not reach the backend. Make sure the API is running.");
    } finally {
      setSubmitting(false);
    }
  };

  return <main className="signup-page">
    <Link className="signup-brand" href="/"><BrandLogo /></Link>
    <section className="signup-layout">
      <div className="signup-story">
        <p className="signup-kicker">PLAN TOGETHER</p>
        <h1>Join CADENSY.<br />Keep every plan.</h1>
        <p>Save decisions, revisit accepted plans, and start the next trip together.</p>
        <ul><li><i>✓</i><span><strong>Keep every version</strong>Return to accepted plans and past decisions.</span></li><li><i>✓</i><span><strong>Stay in control</strong>Your visibility choices remain attached to each preference.</span></li><li><i>✓</i><span><strong>Start the next trip</strong>Move from guest traveler to organizer whenever you are ready.</span></li></ul>
      </div>
      <div className="signup-card-wrap">
        <div className="signup-card">
          <p className="signup-kicker">CREATE YOUR ACCOUNT</p>
          <h2>Join CADENSY.</h2>
          <p className="signup-intro">Save your trips and start planning.</p>
          <form className="signup-form" onSubmit={submit}>
            <label htmlFor="name">Your name</label><input id="name" name="name" autoComplete="name" placeholder="Jiayi Chen" required />
            <label htmlFor="signup-email">Email address</label><input id="signup-email" name="email" type="email" autoComplete="email" placeholder="you@example.com" required />
            <label htmlFor="signup-password">Password</label><div className="signup-password"><input id="signup-password" name="password" type={showPassword ? "text" : "password"} minLength={8} autoComplete="new-password" placeholder="At least 8 characters" required /><button type="button" onClick={() => setShowPassword(v => !v)}>{showPassword ? "Hide" : "Show"}</button></div>
            <label htmlFor="confirm-password">Confirm password</label><input id="confirm-password" name="confirmPassword" type={showPassword ? "text" : "password"} minLength={8} autoComplete="new-password" required />
            {error && <p className="signup-error" role="alert">{error}</p>}
            <label className="signup-consent"><input type="checkbox" required /><span>I agree to the <Link href="/privacy">Privacy Policy</Link> and understand how CADENSY uses planning data.</span></label>
            <button className="signup-submit" type="submit" disabled={submitting}>{submitting ? "Creating account..." : "Create account"} <span>→</span></button>
          </form>
          <p className="signup-login">Already have an account? <Link href="/login" onClick={(event) => {
            event.preventDefault();
            window.location.href = `/login?next=${encodeURIComponent(nextPathRef.current)}`;
          }}>Log in</Link></p>
        </div>
      </div>
    </section>
  </main>;
}
