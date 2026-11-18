import { describe, expect, it, vi } from 'vitest'

import {
  richEightBitColorNumber,
  shouldUseRichEightBitDowngradeForLegacyAppleTerminal,
  terminalColorLevel
} from './colorize.js'

describe('terminal color depth', () => {
  it('trusts tmux client capabilities rather than the inherited color hint', () => {
    const env = { COLORTERM: 'truecolor', TMUX: 'active', TMUX_PANE: '%4' }
    const features = vi.fn(() => '256,RGB,title')

    expect(terminalColorLevel(2, env, features)).toBe(3)
    expect(features).toHaveBeenCalledWith('%4')

    for (const unconfirmed of ['256,title', '256,notRGB', '']) {
      expect(terminalColorLevel(3, env, () => unconfirmed)).toBe(2)
    }
  })

  it('honors explicit color choices without querying tmux', () => {
    const env = { TMUX: 'active', TMUX_PANE: '%4' }
    const features = vi.fn(() => '256,RGB')

    expect(terminalColorLevel(3, { ...env, HERMES_TUI_TRUECOLOR: '1' }, features)).toBe(3)
    expect(terminalColorLevel(3, { ...env, HERMES_TUI_TRUECOLOR: '0' }, features)).toBe(2)
    expect(terminalColorLevel(3, { ...env, HERMES_TUI_TRUECOLOR: '1', NO_COLOR: '1' }, features)).toBe(0)
    expect(terminalColorLevel(0, { ...env, FORCE_COLOR: '0' }, features)).toBe(0)
    expect(terminalColorLevel(2, { ...env, FORCE_COLOR: '2' }, features)).toBe(2)
    expect(features).not.toHaveBeenCalled()
  })
})

describe('shouldUseRichEightBitDowngradeForLegacyAppleTerminal', () => {
  it('uses Rich-compatible 256-color downgrade on legacy Apple Terminal', () => {
    expect(
      shouldUseRichEightBitDowngradeForLegacyAppleTerminal({ TERM_PROGRAM: 'Apple_Terminal' } as NodeJS.ProcessEnv, 2)
    ).toBe(true)
  })

  it('normalizes Apple Terminal names before matching', () => {
    expect(
      shouldUseRichEightBitDowngradeForLegacyAppleTerminal({ TERM_PROGRAM: ' Apple_Terminal ' } as NodeJS.ProcessEnv, 2)
    ).toBe(true)
  })

  it('does not rewrite when Apple Terminal advertises truecolor', () => {
    expect(
      shouldUseRichEightBitDowngradeForLegacyAppleTerminal(
        { COLORTERM: 'truecolor', TERM_PROGRAM: 'Apple_Terminal' } as NodeJS.ProcessEnv,
        3
      )
    ).toBe(false)
  })

  it('does not override explicit color environment choices', () => {
    expect(
      shouldUseRichEightBitDowngradeForLegacyAppleTerminal(
        { FORCE_COLOR: '2', TERM_PROGRAM: 'Apple_Terminal' } as NodeJS.ProcessEnv,
        2
      )
    ).toBe(false)
    expect(
      shouldUseRichEightBitDowngradeForLegacyAppleTerminal(
        { HERMES_TUI_TRUECOLOR: '1', TERM_PROGRAM: 'Apple_Terminal' } as NodeJS.ProcessEnv,
        3
      )
    ).toBe(false)
  })
})

describe('richEightBitColorNumber', () => {
  it('matches Rich downgrade output for default Hermes skin colors', () => {
    expect(richEightBitColorNumber(0xff, 0xd7, 0x00)).toBe(220)
    expect(richEightBitColorNumber(0xff, 0xbf, 0x00)).toBe(214)
    expect(richEightBitColorNumber(0xcd, 0x7f, 0x32)).toBe(173)
    expect(richEightBitColorNumber(0xb8, 0x86, 0x0b)).toBe(136)
    expect(richEightBitColorNumber(0xff, 0xf8, 0xdc)).toBe(230)
  })
})
