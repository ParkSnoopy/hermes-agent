import type { AuxTaskCopyMap } from './types_aux_tasks'

export const enAuxTasks: AuxTaskCopyMap = {
  vision: { label: 'Vision', hint: 'Image analysis' },
  compression: { label: 'Compression', hint: 'Context compaction' },
  approval: { label: 'Approval', hint: 'Smart auto-approve' },
  mcp: { label: 'MCP', hint: 'MCP tool routing' },
  title_generation: { label: 'Title gen', hint: 'Session titles' },
  review: { label: 'Review', hint: '/review reviewer subagent' },
  voice_chat: { label: 'Voice chat', hint: 'Spoken voice-mode replies' },

  profile_describer: { label: 'Profile describer', hint: 'Auto profile descriptions' },
  curator: { label: 'Curator', hint: 'Skill-usage review' }
}
