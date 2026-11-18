import { chromeEn } from '../copy/chrome.js'
import { pick } from '../lib/text.js'

export const placeholders = (): string[] => Object.values(chromeEn.composer.placeholders)

// Picked once per launch by index.
const slot = Math.floor(Math.random() * placeholders().length)

export const placeholder = (): string => placeholders()[slot] ?? pick(placeholders())
