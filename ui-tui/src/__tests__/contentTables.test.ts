import { describe, expect, it } from 'vitest'

import { dailyFortune, fortunes, legendaryFortunes } from '../content/fortunes.js'
import { buildSetupRequiredSections } from '../content/setup.js'
import { contentEn } from '../copy/content.js'

describe('English content tables', () => {

  it('setup panel keeps the command cells untranslated', () => {
    const sections = buildSetupRequiredSections()
    const rows = sections[1]?.rows ?? []

    expect(rows.map(r => r[0])).toEqual(['/setup', '/model', 'Ctrl+C'])
    expect(rows[0]?.[1]).toBe(contentEn.content.setup.setupRow)
    expect(sections[0]?.text).toBe(contentEn.content.setup.intro)
  })

  it('dailyFortune draws from the catalog bags', () => {
    const text = dailyFortune('seed')
    const bag = [...fortunes(), ...legendaryFortunes()]

    expect(bag.some(f => text.endsWith(f))).toBe(true)
  })
})
