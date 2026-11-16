import { describe, expect, it } from 'vitest'

import { chatBitsEn } from '../copy/chatBits.js'

describe('chat presentation copy', () => {

  it('keeps English labels and interpolated counts', () => {
    expect(chatBitsEn.chatBits.thinking.toolCalls).toBe('Tool calls')
    expect(chatBitsEn.chatBits.queued.header(3)).toBe('queued (3)')
    expect(chatBitsEn.chatBits.messageLine.chars('1,234')).toBe('1,234 chars')
  })
})
