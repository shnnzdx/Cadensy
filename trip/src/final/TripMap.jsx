import { useEffect, useMemo, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const flattenDays = days => {
  let globalIndex = 0
  return days.flatMap((day, dayIdx) => day.items.map((item, index) => ({
    ...item,
    dayLabel: day.label,
    dayDate: day.date,
    dayIndex: dayIdx,
    dayTag: (day.label.match(/\d+/) || [dayIdx + 1])[0],
    stopNumber: days.length > 1 ? ++globalIndex : index + 1,
  })))
}

// markerMode 'day':右侧总览按天标注(D1/D2/D3),同一天同色
// markerMode 'stop':单天视图按顺序标注(1/2/3)
const markerIcon = (item, selected, markerMode) => L.divIcon({
  className: `leafletTripMarker day${item.dayIndex % 4}${selected ? ' selected' : ''}${markerMode === 'day' ? ' byDay' : ''}`,
  html: `<span><b>${markerMode === 'day' ? 'D' + item.dayTag : item.stopNumber}</b></span>`,
  iconSize: [34, 34],
  iconAnchor: [17, 30],
})

const curvedRoute = points => {
  if (points.length < 2) return points
  return points.slice(0, -1).flatMap((start, index) => {
    const end = points[index + 1]
    const direction = index % 2 === 0 ? 1 : -1
    const dx = end[1] - start[1]
    const dy = end[0] - start[0]
    const control = [
      (start[0] + end[0]) / 2 - dx * 0.18 * direction,
      (start[1] + end[1]) / 2 + dy * 0.18 * direction,
    ]
    return Array.from({ length: 18 }, (_, step) => {
      const t = step / 18
      const oneMinus = 1 - t
      return [
        oneMinus * oneMinus * start[0] + 2 * oneMinus * t * control[0] + t * t * end[0],
        oneMinus * oneMinus * start[1] + 2 * oneMinus * t * control[1] + t * t * end[1],
      ]
    })
  }).concat([points[points.length - 1]])
}

export default function TripMap({ days, selectedItemId, onSelectItem, compact = false, variant = 'sketch', markerMode = 'stop' }) {
  const mapRef = useRef(null)
  const layerRef = useRef(null)
  const containerRef = useRef(null)
  const invalidateTimerRef = useRef(null)
  const baseItems = useMemo(() => flattenDays(days), [days])

  const mappedItems = useMemo(() => baseItems
    .filter(item => Array.isArray(item.coords) && item.coords.length === 2),
  [baseItems])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current, {
      zoomControl: !compact,
      attributionControl: true,
      scrollWheelZoom: false,
      dragging: !compact,
      doubleClickZoom: !compact,
      boxZoom: !compact,
      keyboard: !compact,
      tap: !compact,
    })
    map.attributionControl.setPrefix(false)
    if (variant === 'real') {
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(map)
    } else {
      map.attributionControl.addAttribution('Geocoding &copy; OpenStreetMap contributors')
    }
    layerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    return () => {
      if (invalidateTimerRef.current !== null) window.clearTimeout(invalidateTimerRef.current)
      invalidateTimerRef.current = null
      layerRef.current?.clearLayers()
      map.remove()
      mapRef.current = null
      layerRef.current = null
    }
  }, [compact, variant])

  useEffect(() => {
    const map = mapRef.current
    const layer = layerRef.current
    if (!map || !layer) return

    if (invalidateTimerRef.current !== null) window.clearTimeout(invalidateTimerRef.current)
    invalidateTimerRef.current = window.setTimeout(() => {
      if (mapRef.current !== map) return
      invalidateTimerRef.current = null
      map.invalidateSize()
    }, 80)
    const invalidateTimer = invalidateTimerRef.current
    layer.clearLayers()

    if (!mappedItems.length) {
      map.setView([41.8781, -87.6298], 12)
      return () => {
        window.clearTimeout(invalidateTimer)
        if (invalidateTimerRef.current === invalidateTimer) invalidateTimerRef.current = null
      }
    }

    const points = mappedItems.map(item => item.coords)
    mappedItems.forEach(item => {
      L.marker(item.coords, { icon: markerIcon(item, item.id === selectedItemId, markerMode) })
        .addTo(layer)
        // compact = 每天那张宽图,有空间常驻显示地名;窄的右栏只在悬停时显示,否则会溢出
        .bindTooltip(markerMode === 'day' ? `${item.dayLabel} · ${item.title}` : `${item.stopNumber}. ${item.title}`, {
          className: 'handDrawnMapLabel',
          direction: 'top',
          offset: [0, -22],
          permanent: compact,
        })
        .on('click', () => onSelectItem?.(item.id))
    })

    if (points.length > 1) L.polyline(curvedRoute(points), {
      className: 'handDrawnRouteLine',
      color: '#2f2a22',
      weight: compact ? 2 : 3,
      opacity: variant === 'real' ? 0.78 : 0.62,
      dashArray: variant === 'real' ? '6 8' : '8 10',
      lineCap: 'round',
      lineJoin: 'round',
    }).addTo(layer)

    const bounds = L.latLngBounds(points)
    map.fitBounds(bounds, { padding: compact ? [20, 20] : [34, 34], maxZoom: compact ? 14 : 13 })
    return () => {
      window.clearTimeout(invalidateTimer)
      if (invalidateTimerRef.current === invalidateTimer) invalidateTimerRef.current = null
    }
  }, [compact, mappedItems, markerMode, onSelectItem, selectedItemId, variant])

  return <div className={cx('tripLeafletMap', variant === 'real' ? 'realMap' : 'handDrawn', compact && 'compact')} ref={containerRef}/>
}

const cx = (...classes) => classes.filter(Boolean).join(' ')
