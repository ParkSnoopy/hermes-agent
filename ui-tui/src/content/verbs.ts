import { contentEn } from '../copy/content.js'


/** Tool name → progress verb. */
export const toolVerbs = (): Record<string, string> => contentEn.content.verbs

export const toolVerb = (name: string): string | undefined => toolVerbs()[name]

export const VERBS = [
  'pondering',
  'contemplating',
  'musing',
  'cogitating',
  'ruminating',
  'deliberating',
  'mulling',
  'reflecting',
  'processing',
  'reasoning',
  'analyzing',
  'computing',
  'synthesizing',
  'formulating',
  'brainstorming'
]
