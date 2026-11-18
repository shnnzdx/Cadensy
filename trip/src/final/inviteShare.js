/**
 * Clipboard and WeChat-template sharing.
 *
 * The template may contain the organizer-chosen trip name and the invite link.
 * It must not contain member names, preferences, constraints, or budgets.
 */

export function wechatShareTemplate({ tripName, url }) {
  const name = String(tripName || "").trim()
  const link = String(url || "").trim()
  if (!name || !link) {
    throw new TypeError("A trip name and invite link are required.")
  }
  return `一起来规划「${name}」。\n打开链接加入：${link}\n在 Cadensy 里一起把行程定下来。`
}

export async function writeClipboard(text, clipboard = globalThis.navigator?.clipboard) {
  const value = String(text || "")
  if (!value) return { ok: false, reason: "empty" }
  if (!clipboard || typeof clipboard.writeText !== "function") {
    return { ok: false, reason: "unavailable" }
  }
  try {
    await clipboard.writeText(value)
    return { ok: true }
  } catch {
    return { ok: false, reason: "failed" }
  }
}
