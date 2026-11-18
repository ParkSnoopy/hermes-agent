import { slashCmdEn } from '../../../copy/slashCmd.js'
import type { SlashCommand } from '../types.js'

type Ctx = Parameters<SlashCommand['run']>[1]

interface UpdateAllRow {
  detail?: string
  name: string
  status: 'current' | 'failed' | 'needs_review' | 'updated'
}

export interface PluginsUpdateAllResponse {
  current?: number
  results?: UpdateAllRow[]
  skipped?: { name: string; reason: string }[]
}

// One minute per plugin is the single-update budget; a whole sweep gets ten (as Desktop).
export const UPDATE_ALL_TIMEOUT_MS = 600_000

const STATUS_MARK = {
  current: () => slashCmdEn.slashCmd.ops.pluginsUpdate.current,
  failed: () => slashCmdEn.slashCmd.ops.pluginsUpdate.failed,
  needs_review: () => slashCmdEn.slashCmd.ops.pluginsUpdate.needsReview,
  updated: () => slashCmdEn.slashCmd.ops.pluginsUpdate.updated
} satisfies Record<UpdateAllRow['status'], () => string>

export function pluginsUpdateAllLines(outcome: PluginsUpdateAllResponse): string[] {
  const results = outcome.results ?? []

  const lines = [
    slashCmdEn.slashCmd.ops.pluginsUpdate.title,
    ...results.map(row => `  ${STATUS_MARK[row.status]?.() ?? row.status}  ${row.name}${row.detail ? `  (${row.detail})` : ''}`),
    ...(outcome.skipped ?? []).map(skip => `  - ${slashCmdEn.slashCmd.ops.pluginsUpdate.skipped(skip.name, skip.reason)}`)
  ]

  if (!results.length) {
    lines.push(`  ${slashCmdEn.slashCmd.ops.pluginsUpdate.none}`)
  }

  if (results.some(row => row.status === 'needs_review')) {
    lines.push(`  ${slashCmdEn.slashCmd.ops.pluginsUpdate.review}`)
  }

  return lines
}

export function runPluginsUpdateAll(ctx: Ctx): void {
  ctx.transcript.sys(slashCmdEn.slashCmd.ops.pluginsUpdate.running)
  ctx.gateway.gw
    .request<PluginsUpdateAllResponse>('plugins.manage', { action: 'update_all' }, UPDATE_ALL_TIMEOUT_MS)
    .then(outcome => {
      if (ctx.stale()) {
        return
      }

      ctx.transcript.page(pluginsUpdateAllLines(outcome ?? {}).join('\n'), slashCmdEn.slashCmd.ops.slashWorker.pluginsTitle)
    })
    .catch(ctx.guardedErr)
}
