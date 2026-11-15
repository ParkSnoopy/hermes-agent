import type { AuxTaskCopyMap } from './types_aux_tasks'

export const frAuxTasks: AuxTaskCopyMap = {
  vision: {
    label: 'Vision',
    hint: "Analyse d'image"
  },
  compression: {
    label: 'Compression',
    hint: 'Compaction de contexte'
  },
  approval: {
    label: 'Approbation',
    hint: 'Auto-approbation intelligente'
  },
  mcp: {
    label: 'MCP',
    hint: "Routage d'outils MCP"
  },
  title_generation: {
    label: 'Génération de titre',
    hint: 'Titres de session'
  },
  review: {
    label: 'Révision',
    hint: 'Sous-agent de révision /review'
  },
  voice_chat: { label: 'Chat vocal', hint: 'Réponses parlées du mode vocal' },

  profile_describer: {
    label: 'Description de profil',
    hint: 'Descriptions automatiques des profils'
  },
  curator: {
    label: 'Curateur',
    hint: "Revue d'utilisation des skills"
  }
}
