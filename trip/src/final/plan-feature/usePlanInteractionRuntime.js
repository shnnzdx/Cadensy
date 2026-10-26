import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useTripApp } from '../TripAppState.jsx'

const groupCommentsByItem = rows => (rows || []).reduce((grouped, comment) => {
  const itemId = comment.plan_item_id || comment.planItemId
  if (!itemId) return grouped
  grouped[itemId] = [...(grouped[itemId] || []), comment]
  return grouped
}, {})

const groupChangesByItem = rows => (rows || []).reduce((grouped, change) => {
  if (['ai_generate', 'rule_generate', 'initial_plan'].includes(change.origin)) return grouped
  const itemId = change.plan_item_id || change.planItemId
  if (!itemId) return grouped
  grouped[itemId] = [...(grouped[itemId] || []), change]
  return grouped
}, {})

export function usePlanInteractionRuntime({ currentTrip }) {
  const app = useTripApp()
  const location = useLocation()
  const [openDays, setOpenDays] = useState(['day2'])
  const [comments, setComments] = useState({})
  const [changeHistory, setChangeHistory] = useState({})
  const [historyOpen, setHistoryOpen] = useState(null)
  const [commenting, setCommenting] = useState(null)
  const [commentDraft, setCommentDraft] = useState('')
  const [commentError, setCommentError] = useState('')
  const [menuOpen, setMenuOpen] = useState(null)
  const [drawerItem, setDrawerItem] = useState(null)
  const [drawerMode, setDrawerMode] = useState('ask')
  const [selectedTripItemId, setSelectedTripItemId] = useState(null)
  const [highlightedItemId, setHighlightedItemId] = useState(null)
  const [railDay, setRailDay] = useState('all')
  const [addStopContext, setAddStopContext] = useState(null)

  useEffect(() => {
    if (!menuOpen) return
    const handle = event => {
      if (!event.target.closest('.moreWrap')) setMenuOpen(null)
    }
    document.addEventListener('pointerdown', handle)
    return () => document.removeEventListener('pointerdown', handle)
  }, [menuOpen])

  const patched = item => app.appliedPatches[item.id] ? { ...item, ...app.appliedPatches[item.id], status: 'Updated' } : item
  const days = useMemo(() => (app.days || []).map(day => ({ ...day, items: day.items.map(patched) })), [app.appliedPatches, app.days])
  const itemDayById = useMemo(() => Object.fromEntries(days.flatMap(day => day.items.map(item => [item.id, day.id]))), [days])
  const railDays = useMemo(() => railDay === 'all' ? days : days.filter(day => day.id === railDay), [days, railDay])

  const focusPlanItem = useCallback((itemId, target = 'item') => {
    if (!itemId) return
    setSelectedTripItemId(itemId)
    setHighlightedItemId(itemId)
    const dayId = itemDayById[itemId]
    if (dayId) setOpenDays(current => current.includes(dayId) ? current : [...current, dayId])
    window.setTimeout(() => {
      const roundCard = target === 'round' ? document.querySelector(`[data-round-item-id="${itemId}"]`) : null
      const itemCard = document.getElementById(`trip-item-${itemId}`)
      ;(roundCard || itemCard)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }, 120)
    window.setTimeout(() => setHighlightedItemId(current => current === itemId ? null : current), 1800)
  }, [itemDayById])

  const handleSelectTripItem = useCallback(itemId => focusPlanItem(itemId), [focusPlanItem])

  useEffect(() => {
    const focusItemId = new URLSearchParams(location.search).get('focus')
    if (focusItemId && days.length) focusPlanItem(focusItemId)
  }, [days.length, focusPlanItem, location.search])

  useEffect(() => {
    if (!app.loadComments || !days.length) return undefined
    let cancelled = false
    const load = async () => {
      try {
        const rows = await app.loadComments()
        if (!cancelled) {
          setComments(groupCommentsByItem(rows))
          setCommentError('')
        }
      } catch {
        if (!cancelled) setCommentError('Could not load group notes.')
      }
    }
    load()
    const timer = window.setInterval(load, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [app.loadComments, days.length])

  useEffect(() => {
    if (!app.loadChangeLog || !app.planId || !days.length) {
      setChangeHistory({})
      return undefined
    }
    let cancelled = false
    const load = async () => {
      try {
        const rows = await app.loadChangeLog()
        if (!cancelled) setChangeHistory(groupChangesByItem(rows))
      } catch {
        if (!cancelled) setChangeHistory({})
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [app.loadChangeLog, app.planId, days])

  const toggleDay = useCallback(id => {
    setOpenDays(current => current.includes(id) ? current.filter(x => x !== id) : [...current, id])
  }, [])

  const submitComment = useCallback(async id => {
    if (!commentDraft.trim()) return
    setCommentError('')
    try {
      const saved = await app.addComment(id, commentDraft.trim())
      setComments(current => ({
        ...current,
        [id]: [...(current[id] || []).filter(comment => comment.id !== saved.id), saved],
      }))
      setCommentDraft('')
      setCommenting(null)
      app.notify('Group note posted')
    } catch (err) {
      setCommentError(err.status === 422 ? 'Write a note before posting.' : 'Could not post this note.')
    }
  }, [app, commentDraft])

  const openDrawer = useCallback((item, mode, day) => {
    setDrawerItem(day ? { ...item, dayLabel: `${day.label} · ${day.date}` } : item)
    setDrawerMode(mode)
    setMenuOpen(null)
  }, [])

  const openGlobalAssistant = useCallback(() => {
    setDrawerItem({
      id: null,
      title: currentTrip.name || 'Current plan',
      place: currentTrip.destination || 'Trip itinerary',
      time: currentTrip.dates || 'Current plan',
    })
    setDrawerMode('global')
    setMenuOpen(null)
  }, [currentTrip])

  const closeDrawer = useCallback(() => setDrawerItem(null), [])

  const toggleBooked = useCallback(async item => {
    const nextBooked = item.settledness !== 'booked'
    setMenuOpen(null)
    try {
      await app.setItemBooked(item.id, nextBooked)
      app.notify(nextBooked ? 'Marked as booked' : 'Booked status removed')
    } catch (err) {
      app.notify(err.status === 404 ? 'This plan item no longer exists.' : 'Could not update booking status.')
    }
  }, [app])

  const toggleCommentComposer = useCallback(itemId => {
    setCommenting(current => current === itemId ? null : itemId)
    setMenuOpen(null)
  }, [])

  const toggleHistory = useCallback(itemId => {
    if (!(changeHistory[itemId] || []).length) return
    setHistoryOpen(current => current === itemId ? null : itemId)
    setMenuOpen(null)
  }, [changeHistory])

  const cancelCommentComposer = useCallback(() => {
    setCommenting(null)
    setCommentDraft('')
    setCommentError('')
  }, [])

  const toggleMenu = useCallback(itemId => {
    setMenuOpen(current => current === itemId ? null : itemId)
  }, [])

  const showDayOnMap = useCallback(dayId => {
    setRailDay(dayId)
    setOpenDays([dayId])
  }, [])
  const showAllOnMap = useCallback(() => setRailDay('all'), [])
  const openAddStop = useCallback((day, afterItem, beforeItem) => {
    setAddStopContext({ day, afterItem, beforeItem })
  }, [])
  const closeAddStop = useCallback(() => setAddStopContext(null), [])
  const selectPlanItem = useCallback(itemId => {
    setSelectedTripItemId(itemId)
    const dayId = itemDayById[itemId]
    if (!dayId) return
    setRailDay(dayId)
    setOpenDays(current => current.includes(dayId) ? current : [...current, dayId])
  }, [itemDayById])
  const updateCommentDraft = useCallback(value => setCommentDraft(value), [])

  return {
    view: {
      app,
      currentTrip,
      days,
      railDays,
      openDays,
      comments,
      changeHistory,
      historyOpen,
      commenting,
      commentDraft,
      commentError,
      menuOpen,
      drawerItem,
      drawerMode,
      selectedTripItemId,
      highlightedItemId,
      railDay,
      addStopContext,
    },
    actions: {
      focusPlanItem,
      handleSelectTripItem,
      toggleDay,
      selectPlanItem,
      showDayOnMap,
      showAllOnMap,
      openAddStop,
      closeAddStop,
      toggleCommentComposer,
      toggleHistory,
      cancelCommentComposer,
      updateCommentDraft,
      submitComment,
      toggleMenu,
      openDrawer,
      openGlobalAssistant,
      closeDrawer,
      toggleBooked,
    },
  }
}
