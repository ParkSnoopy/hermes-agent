import { contentEn } from '../copy/content.js'
import type { PanelSection } from '../types.js'

export const setupRequiredTitle = (): string => contentEn.content.setup.title

export const buildSetupRequiredSections = (): PanelSection[] => {
  const s = contentEn.content.setup

  return [
    {
      text: s.intro
    },
    {
      rows: [
        ['/setup', s.setupRow],
        ['/model', s.modelRow],
        ['Ctrl+C', s.exitRow]
      ],
      title: s.actions
    },
    {
      text: s.footer
    }
  ]
}
