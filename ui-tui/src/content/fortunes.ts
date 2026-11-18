import { contentEn } from '../copy/content.js'


/** Everyday fortunes in display order. */
export const fortunes = (): string[] => Object.values(contentEn.content.fortunes)

/** Rare fortunes (every 20th roll). */
export const legendaryFortunes = (): string[] => Object.values(contentEn.content.legendaryFortunes)

const hash = (s: string) => [...s].reduce((h, c) => Math.imul(h ^ c.charCodeAt(0), 16777619), 2166136261) >>> 0

const fromScore = (n: number) => {
  const rare = n % 20 === 0
  const bag = rare ? legendaryFortunes() : fortunes()

  return `${rare ? '🌟' : '🔮'} ${bag[n % bag.length]}`
}

export const randomFortune = () => fromScore(Math.floor(Math.random() * 0x7fffffff))
export const dailyFortune = (seed: null | string) => fromScore(hash(`${seed || 'anon'}|${new Date().toDateString()}`))
