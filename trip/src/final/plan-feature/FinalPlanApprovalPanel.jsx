import { useCallback, useEffect, useState } from 'react'

const REASON_TEXT = {
  plan_not_active: 'The plan is not active.',
  no_plan_items: 'The itinerary has no items yet.',
  open_decision_round: 'A decision round is still open.',
  pending_change_proposal: 'A change proposal is still pending.',
}

const STATE_TEXT = {
  approved_current: 'approved this itinerary',
  approved_earlier: 'approved an earlier version — needs re-approval',
  not_approved: 'has not approved',
}

function reasonText(reasons) {
  return (reasons || []).map(reason => REASON_TEXT[reason] || reason).join(' ')
}

function formatApprovedOn(value) {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

export default function FinalPlanApprovalPanel({ tripId, app }) {
  const [status, setStatus] = useState(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    if (!tripId || !app?.loadFinalPlan) return null
    const next = await app.loadFinalPlan(tripId)
    setStatus(next)
    return next
  }, [app, tripId])

  useEffect(() => {
    let cancelled = false
    reload()
      .catch(error => {
        if (!cancelled) setNotice(error.message || 'Could not load final plan approval.')
      })
    return () => {
      cancelled = true
    }
  }, [reload])

  const approve = async () => {
    if (!status) return
    setBusy(true)
    setNotice('')
    try {
      const result = await app.approveFinalPlan(tripId, status.itinerary_revision)
      const next = await reload()
      const closed = result.final_plan_approved || next?.final_plan_approved
      setNotice(closed
        ? 'You approved this itinerary. The invite link is now closed.'
        : 'You approved this itinerary.')
    } catch (error) {
      if (error.message === 'itinerary_revision_stale') {
        await reload().catch(() => {})
        setNotice('The itinerary changed since you last looked — please review and approve again.')
      } else if (error.message === 'final_plan_not_ready') {
        const reasons = error.body?.reasons || []
        setNotice(error.body?.message || reasonText(reasons) || 'This itinerary is not ready for final approval.')
        await reload().catch(() => {})
      } else if (error.message === 'invite_closed') {
        await reload().catch(() => {})
        setNotice('The invite link is already closed.')
      } else {
        setNotice(error.message || 'Could not approve this itinerary.')
      }
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return <section className="finalPlanPanel"><p>Loading final plan approval…</p></section>
  }

  const closed = status.final_plan_approved
  const ready = status.ready && !closed
  const disabledReason = closed ? '' : reasonText(status.reasons)

  return <section className="finalPlanPanel" aria-label="Final plan approval">
    <div className="finalPlanPanelHeader">
      <span className="eyebrow">Final plan</span>
      <h2>{closed ? `Final plan approved by all participants on ${formatApprovedOn(status.final_plan_approved_at)}` : 'Approve this itinerary'}</h2>
    </div>
    {closed && <p>The invite remains closed. Later edits do not reopen it.</p>}
    {!closed && <p>{ready ? 'Every current participant can approve this itinerary.' : disabledReason}</p>}
    <ul className="finalPlanRoster">
      {(status.participants || []).map((row, index) => (
        <li key={`${row.role}-${index}`}>
          <span>{row.role === 'organizer' ? 'Organizer' : 'Participant'}</span>
          <span>{STATE_TEXT[row.approval_state] || row.approval_state}</span>
        </li>
      ))}
    </ul>
    {!closed && <button type="button" disabled={!ready || busy} onClick={approve}>
      {busy ? 'Approving…' : 'Approve'}
    </button>}
    {notice && <p className="finalPlanNotice">{notice}</p>}
  </section>
}
