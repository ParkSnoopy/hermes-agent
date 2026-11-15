import { normalize } from '@/lib/text'

const SOURCE_LABELS: Record<string, string> = {
  acp: 'ACP',
  api_server: 'API',
  bluebubbles: 'iMessage',
  cli: 'CLI',
  codex: 'Codex',
  desktop: 'Desktop',
  discord: 'Discord',
  email: 'Email',
  gateway: 'Gateway',
  kanban: 'Kanban',
  local: 'Local',
  matrix: 'Matrix',
  mattermost: 'Mattermost',
  oneshot: 'One-shot',
  photon: 'Photon',
  qqbot: 'QQ',
  signal: 'Signal',
  slack: 'Slack',
  sms: 'SMS',
  telegram: 'Telegram',
  tui: 'TUI',
  webhook: 'Webhook',
  weixin: 'WeChat',
  whatsapp: 'WhatsApp',
  yuanbao: 'Yuanbao'
}

const SOURCE_ALIASES: Record<string, string[]> = {
  bluebubbles: ['apple messages', 'imessage'],
  photon: ['imessage', 'messages'],
  cli: ['terminal'],
  desktop: ['app', 'gui'],
  local: ['machine'],
  qqbot: ['qq'],
  telegram: ['tg'],
  tui: ['terminal'],
  weixin: ['wechat'],
  whatsapp: ['wa']
}

// Sources that are not external messaging origins, including legacy Kanban rows.
// They must not acquire a messaging-origin badge on archived handoffs.
export const LOCAL_SESSION_SOURCE_IDS = [
  'acp',
  'cli',
  'codex',
  'desktop',
  'gateway',
  'kanban',
  'local',
  'oneshot',
  'tui'
]
const LOCAL_SOURCE_IDS = new Set(LOCAL_SESSION_SOURCE_IDS)

// Historical external source IDs used to classify and filter stored sessions.
export const MESSAGING_SESSION_SOURCE_IDS = [
  'telegram',
  'discord',
  'slack',
  'mattermost',
  'matrix',
  'signal',
  'whatsapp',
  'bluebubbles',
  'photon',
  'homeassistant',
  'email',
  'sms',
  'webhook',
  'api_server',
  'weixin',
  'wecom',
  'qqbot',
  'yuanbao',
  'dingtalk',
  'feishu'
]
const MESSAGING_SOURCE_IDS = new Set(MESSAGING_SESSION_SOURCE_IDS)

/** True when a stored source identifies an external messaging platform. */
export function isMessagingSource(source: null | string | undefined): boolean {
  const id = normalizeSessionSource(source)

  return id != null && MESSAGING_SOURCE_IDS.has(id)
}

export function normalizeSessionSource(source: null | string | undefined): string | null {
  return normalize(source) || null
}

/**
 * Resolve the origin messaging platform for a handed-off session. Returns the
 * normalized platform id (e.g. 'telegram') when the session completed a handoff
 * from a real messaging platform, otherwise null. After a handoff the live
 * source is local, so this is what drives the row's origin-platform badge.
 */
export function handoffOriginSource(
  handoffState: null | string | undefined,
  handoffPlatform: null | string | undefined
): string | null {
  if (handoffState !== 'completed') {
    return null
  }

  const id = normalizeSessionSource(handoffPlatform)

  if (!id || LOCAL_SOURCE_IDS.has(id)) {
    return null
  }

  return id
}

export function sessionSourceLabel(source: null | string | undefined): string | null {
  const id = normalizeSessionSource(source)

  if (!id) {
    return null
  }

  return SOURCE_LABELS[id] || id.replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

export function sessionSourceSearchTerms(source: null | string | undefined): string[] {
  const id = normalizeSessionSource(source)
  const label = sessionSourceLabel(id)

  if (!id) {
    return []
  }

  return [id, label ?? '', ...(SOURCE_ALIASES[id] ?? [])].filter(Boolean)
}
